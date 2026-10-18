"""Research only. Public data, chronological evaluation, no account/order API."""
import sys

# VS Code's Run Python File sends no subcommand. Route that invocation to the
# direct launcher before scientific imports and local SMTP settings are read.
if __name__=='__main__' and len(sys.argv)==1:
    from run import main as launch
    raise SystemExit(launch())

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.neural_network import MLPClassifier
from features import FEATURES, make_features, parse_klines
from technical import dow123
from strategy import STRATEGY, signal_features, follow_hourly

STEP = 900_000

def public_get(endpoint, **params):
    # Fixed public host and read-only allowlist; no API keys accepted.
    if endpoint not in ('klines', 'fundingRate'):
        raise ValueError('Only public historical data endpoints allowed')
    url = 'https://fapi.binance.com/fapi/v1/' + endpoint + '?' + urlencode(params)
    with urlopen(url, timeout=30) as response:
        result = json.load(response)
    if not isinstance(result, list):
        raise ValueError(f'Unexpected market data response: {result}')
    return result


def download(symbol, days, out):
    if symbol.upper() in ('KORU', 'QID'):
        raise ValueError('US ETFs require independently sourced daily CSV; use analyze, not Binance download')
    out.mkdir(parents=True, exist_ok=False)
    end = int(time.time() * 1000) // STEP * STEP - 1
    start = (end + 1) - days * 86400000
    cursor, rows = start, []
    while cursor <= end:
        batch = public_get('klines', symbol=symbol, interval='15m', startTime=cursor, endTime=end, limit=1000)
        if not batch:
            raise ValueError('Incomplete Kline history')
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + STEP
        if next_cursor <= cursor:
            raise ValueError('Pagination did not advance')
        cursor = next_cursor
        time.sleep(.25)
    bars = parse_klines(rows)
    if len(bars) != days * 96 or int(bars.iloc[0].open_time) != start:
        raise ValueError('History does not cover requested range')
    bars.to_csv(out / 'bars.csv', index=False)
    cursor, rates = start, []
    while cursor <= end:
        batch = public_get('fundingRate', symbol=symbol, startTime=cursor, endTime=end, limit=1000)
        if not batch:
            break
        rates.extend(batch)
        nxt = int(batch[-1]['fundingTime']) + 1
        if nxt <= cursor:
            raise ValueError('Funding pagination did not advance')
        cursor = nxt
        time.sleep(.25)
    if not rates:
        raise ValueError('No funding history returned; refusing zero-cost substitution')
    pd.DataFrame(rates).to_csv(out / 'funding.csv', index=False)
    (out / 'source.json').write_text(json.dumps(dict(symbol=symbol, start=start, end=end, synthetic=False), indent=2))
    print(f'Downloaded {len(bars)} closed bars and {len(rates)} funding events')


def dataset(bars, funding):
    bars = bars.copy().reset_index(drop=True)
    required = ['open_time','close_time','open','high','low','close','volume']
    if not np.isfinite(bars[required].to_numpy(dtype=float)).all():
        raise ValueError('Nonfinite input')
    if not (bars.open_time.diff().dropna() == STEP).all():
        raise ValueError('Require chronological contiguous 15-minute bars')
    if not (bars.close_time == bars.open_time + STEP - 1).all():
        raise ValueError('Invalid candle close times')
    if bars.close_time.max() >= int(time.time()*1000):
        raise ValueError('Unclosed candle')
    if (bars[['open','high','low','close']] <= 0).any().any() or (bars.volume < 0).any():
        raise ValueError('Invalid prices or volume')
    if ((bars.high < bars[['open','close','low']].max(axis=1)) | (bars.low > bars[['open','close','high']].min(axis=1))).any():
        raise ValueError('Invalid OHLC ranges')
    if funding.empty or funding.fundingTime.duplicated().any():
        raise ValueError('Empty or duplicate funding events')
    if not np.isfinite(funding[['fundingTime','fundingRate','markPrice']].to_numpy(dtype=float)).all() or (funding.markPrice <= 0).any():
        raise ValueError('Invalid funding data')
    # Detect gross missing funding histories. Not a guarantee of completeness.
    ft = np.sort(funding.fundingTime.to_numpy(dtype='int64'))
    covered = np.r_[int(bars.open_time.iloc[0]), ft[(ft>=bars.open_time.iloc[0]) & (ft<=bars.close_time.iloc[-1])], int(bars.close_time.iloc[-1])]
    if np.diff(covered).max() > 9 * 3600000:
        raise ValueError('Funding history gap >9h; verify market schedule/source')
    d = signal_features(bars)
    # Signal at close[t]; execute at open[t+1]; mark/exit at open[t+2].
    d['entry_time'] = bars.open_time.shift(-1)
    d['exit_time'] = bars.open_time.shift(-2)
    d['forward'] = bars.open.shift(-2) / bars.open.shift(-1) - 1
    d['target'] = (d.forward > 0).astype(int)
    d['funding'] = 0.0
    # Events in (entry, exit]; boundary events belong to pre-rebalance position.
    for row in funding.itertuples():
        i = int(np.searchsorted(bars.open_time.to_numpy(), int(row.fundingTime), side='left')) - 2
        if 0 <= i < len(d)-2 and d.entry_time.iloc[i] < row.fundingTime <= d.exit_time.iloc[i]:
            d.loc[i,'funding'] += float(row.fundingRate) * float(row.markPrice) / float(bars.open.iloc[i+1])
    return d.dropna(subset=FEATURES+['forward','entry_time','exit_time']).reset_index(drop=True)


def models():
    return {
        'logistic': make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500)),
        'random_forest': RandomForestClassifier(n_estimators=100,max_depth=5,min_samples_leaf=40,n_jobs=1,random_state=42),
        'extra_trees': ExtraTreesClassifier(n_estimators=100,max_depth=5,min_samples_leaf=40,n_jobs=1,random_state=42),
        'hist_gradient': HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,min_samples_leaf=40,l2_regularization=10,early_stopping=False,random_state=42),
        'mlp': make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(16,),alpha=10,max_iter=150,shuffle=False,early_stopping=False,random_state=42)),
    }


def positions(name, model, data, threshold):
    if name == 'dow123':
        raw = data.dow123_signal.to_numpy()
    elif name == 'trend':
        raw = np.sign(data.ema_gap.to_numpy())
    elif name == 'mean_reversion':
        raw = np.where(data.ret3 > .005, -1, np.where(data.ret3 < -.005, 1, 0))
    else:
        p = model.predict_proba(data[FEATURES])[:,list(model.classes_).index(1)]
        raw = np.where(p >= threshold,1,np.where(p <= 1-threshold,-1,0))
    return follow_hourly(raw,data)


def simulate(d, pos, fee=.0005, slip=.0002, exposure=.2):
    """Fixed units between changes; target exposure rebalanced only on signal changes.
    Equity starts at 1. Costs include initial entry, flips and final liquidation.
    No intrabar stops, liquidation or order-book simulation. No leveraged exposure.
    """
    if not (0 < exposure <= 1) or fee < 0 or slip < 0:
        raise ValueError('Invalid costs or exposure')
    pos = np.asarray(pos, dtype=float)
    if len(pos) != len(d) or not np.isin(pos,[-1,0,1]).all() or len(d)==0:
        raise ValueError('Invalid positions')
    equity, notional, last, sides, entries = 1., 0., 0., 0, 0
    peak, dd = 1., 0.
    path, returns = [], []
    for row, signal in zip(d.itertuples(), pos):
        previous = equity
        if signal != last:
            if last:
                equity -= abs(notional)*(fee+slip)
                sides += 1
            notional = signal * exposure * max(equity,0)
            if signal:
                equity -= abs(notional)*(fee+slip)
                sides += 1
                entries += 1
        equity += notional * (row.forward - row.funding)
        notional *= 1 + row.forward
        last = signal
        if equity <= 0:
            raise ValueError('Simulation equity exhausted')
        returns.append(equity/previous-1)
        peak = max(peak,equity)
        dd = max(dd,1-equity/peak)
        path.append(equity)
    if last:
        previous = equity
        equity -= abs(notional)*(fee+slip)
        sides += 1
        returns[-1] = (1+returns[-1]) * equity/previous-1
        path[-1] = equity
        dd = max(dd,1-equity/peak)
    sd = np.std(returns,ddof=1) if len(returns)>1 else 0
    return dict(net_return=float(equity-1),max_drawdown=float(dd),entries=entries,order_sides=sides,
                sharpe_estimate=float(np.mean(returns)/sd*np.sqrt(365*96)) if sd>1e-15 else 0.,bars=len(d)),path


def run(data_dir, out, fee, slip, exposure):
    if out.exists():
        raise ValueError('Output exists. Preserve reports; use a new directory (retesting contaminates holdout).')
    source = json.loads((data_dir/'source.json').read_text())
    bars = pd.read_csv(data_dir/'bars.csv')
    funding = pd.read_csv(data_dir/'funding.csv')
    d = dataset(bars,funding)
    if len(d)<2400:
        raise ValueError('Need at least 2400 usable bars')
    split = int(len(d)*.8)
    # Only data before this cut is used for selection. Purge two forward bars.
    boundaries = np.linspace(int(split*.5),split,4,dtype=int)
    names = list(models()) + ['trend','mean_reversion','dow123']
    configs = []
    validation_curves = {}
    for name in names:
        thresholds = [.55,.60] if name in models() else [.6]
        fold_predictions = []
        for left,right in zip(boundaries[:-1],boundaries[1:]):
            train,valid = d.iloc[:left-2],d.iloc[left:right-2]
            model = models().get(name)
            if model is not None:
                model.fit(train[FEATURES],train.target)
            fold_predictions.append((valid,model))
        for threshold in thresholds:
            metrics, curve, capital = [], {}, 1.
            curve[int(fold_predictions[0][0].entry_time.iloc[0])] = 1.
            for v,m in fold_predictions:
                metric,path = simulate(v,positions(name,m,v,threshold),fee,slip,exposure)
                metrics.append(metric)
                curve.update({int(t):float(capital*e) for t,e in zip(v.exit_time,path)})
                capital *= path[-1]
            validation_curves[(name,threshold)] = curve
            eligible = (all(x['max_drawdown'] <= .10 for x in metrics) and
                        sum(x['net_return']>0 for x in metrics)>=2 and
                        sum(x['entries'] for x in metrics)>=30)
            score = float(np.median([x['net_return'] for x in metrics]))
            configs.append(dict(name=name,threshold=threshold,eligible=eligible,validation_score=score,folds=metrics))
        print('Validated',name,flush=True)
    configs.sort(key=lambda x:x['validation_score'],reverse=True)
    eligible = [x for x in configs if x['eligible'] and x['validation_score']>0]
    selected = eligible[0] if eligible else None
    out.mkdir(parents=True)
    report = dict(source=source,dataset_sha256=hashlib.sha256((data_dir/'bars.csv').read_bytes()+(data_dir/'funding.csv').read_bytes()).hexdigest(),
                  selection='Median net return over 3 purged expanding validation folds; final 20% held out',
                  fee_side=fee,slippage_side=slip,exposure=exposure,ranking=configs,selected=selected,
                  strategy=STRATEGY,lower_timeframe='15m',higher_timeframe='1h',timeframe_ratio=4,
                  status='NO_CANDIDATE',real_orders_enabled=False,
                  limitations=['No liquidation, latency, spread dynamics, partial fills or intrabar stops',
                               'Funding timestamp boundary uses pre-rebalance holdings',
                               'Repeated manual selection on this dataset invalidates holdout independence',
                               'ML probabilities are not calibrated; thresholds are research settings'])
    # Baselines on the same untouched holdout, no model-selection influence.
    holdout=d.iloc[split:]
    report['holdout_period_ms']=[int(holdout.entry_time.iloc[0]),int(holdout.exit_time.iloc[-1])]
    report['baseline_flat']=simulate(holdout,np.zeros(len(holdout)),fee,slip,exposure)[0]
    report['baseline_long_perpetual']=simulate(holdout,np.ones(len(holdout)),fee,slip,exposure)[0]
    if selected:
        name=selected['name']
        model=models().get(name)
        train=d.iloc[:split-2]
        if model is not None:
            model.fit(train[FEATURES],train.target)
        pos=positions(name,model,holdout,selected['threshold'])
        metrics,path=simulate(holdout,pos,fee,slip,exposure)
        stress,_=simulate(holdout,pos,fee*2,slip*2,exposure)
        report['holdout']=metrics
        report['holdout_double_cost']=stress
        passed=metrics['net_return']>0 and metrics['max_drawdown']<=.10 and metrics['entries']>=20 and stress['net_return']>0
        report['status']='FORWARD_PAPER_REVIEW' if passed and not source.get('synthetic',True) else 'REJECTED'
        pd.DataFrame(dict(entry_time=holdout.entry_time,signal=pos,equity=path)).to_csv(out/'holdout_equity.csv',index=False)
        # Frozen model is a research artifact, never an execution instruction.
        joblib.dump(dict(name=name,model=model,threshold=selected['threshold'],features=FEATURES,source=source,training_end_ms=int(train.exit_time.iloc[-1]),research_status=report['status'],strategy=STRATEGY),out/'research_model.joblib')
    # One curve per model, threshold frozen by validation score, never holdout.
    comparison, summaries = {}, []
    for name in names:
        candidates = [x for x in configs if x['name']==name]
        best = max(candidates,key=lambda x:(x['eligible'] and x['validation_score']>0,x['validation_score']))
        curve = validation_curves[(name,best['threshold'])]
        equity = np.array(list(curve.values()))
        comparison[name] = curve
        summaries.append(dict(name=name,threshold=best['threshold'],validation_score=best['validation_score'],
            stitched_net_return=float(equity[-1]-1),
            stitched_max_drawdown=float(np.max(1-equity/np.maximum.accumulate(equity))),
            entries=sum(x['entries'] for x in best['folds']),eligible=best['eligible']))
    for name,direction in [('baseline_flat',0),('baseline_long_perpetual',1)]:
        capital,curve=1.,{}
        for left,right in zip(boundaries[:-1],boundaries[1:]):
            v=d.iloc[left:right-2]
            if not curve: curve[int(v.entry_time.iloc[0])]=1.
            _,path=simulate(v,np.full(len(v),direction),fee,slip,exposure)
            curve.update({int(t):float(capital*e) for t,e in zip(v.exit_time,path)})
            capital*=path[-1]
        comparison[name]=curve
    report['comparison']=summaries
    report['comparison_scope']='Out-of-fold validation only; per-model threshold chosen on validation; holdout never used to rank curves'
    from comparison import export_comparison
    export_comparison(out,comparison,summaries,source)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    from notifications import preview_report
    preview_report(out/'report.json')
    pd.DataFrame([{k:v for k,v in x.items() if k!='folds'} for x in configs]).to_csv(out/'ranking.csv',index=False)
    print(json.dumps({k:report[k] for k in ['status','selected','real_orders_enabled']},ensure_ascii=False,indent=2))


def demo(out,n=5000):
    out.mkdir(parents=True,exist_ok=False)
    rng=np.random.default_rng(42)
    op=100*np.exp(np.cumsum(rng.normal(0,.002,n)))
    close=op*np.exp(rng.normal(0,.0005,n))
    t=1609459200000+np.arange(n)*STEP
    pd.DataFrame(dict(open_time=t,close_time=t+STEP-1,open=op,close=close,high=np.maximum(op,close)*1.001,low=np.minimum(op,close)*.999,volume=rng.lognormal(5,.4,n))).to_csv(out/'bars.csv',index=False)
    ix=np.arange(0,n,32)
    pd.DataFrame(dict(fundingTime=t[ix],fundingRate=np.full(len(ix),.0001),markPrice=op[ix])).to_csv(out/'funding.csv',index=False)
    (out/'source.json').write_text(json.dumps(dict(symbol='SYNTHETIC',synthetic=True)))


def main():
    from settings import load_local_env
    load_local_env()
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('download');a.add_argument('--symbol',default='BTCUSDT');a.add_argument('--days',type=int,default=365);a.add_argument('--out',type=Path,required=True)
    a=sub.add_parser('demo');a.add_argument('--out',type=Path,required=True)
    sub.add_parser('watchlist')
    a=sub.add_parser('report',help='Regenerate operation counts, trade details and selectable account equity report without backtesting')
    a.add_argument('--run',type=Path,required=True)
    a=sub.add_parser('report-batch',help='Regenerate the overall/model/instrument report tree without backtesting')
    a.add_argument('--run',type=Path,required=True)
    a=sub.add_parser('patterns',help='12 reversal families, 15m-4h entries, multiple exits and leveraged validation')
    a.add_argument('--data',type=Path,required=True);a.add_argument('--baseline-report',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
    a.add_argument('--timeframes',nargs='+',default=['15m','30m','1h','2h','4h'])
    a.add_argument('--models',nargs='+',help='Optional subset, including torch_gru and analysis_dow123')
    a.add_argument('--exit-modes',nargs='+',default=['swing_percent','fixed_rr','atr_trailing','three_bar_breakout','three_bar_trailing','time_exit','fixed_three_bar_target'])
    a.add_argument('--leverage',type=float,default=5.);a.add_argument('--margin-fraction',type=float,default=.1)
    a.add_argument('--confidence',type=float,default=.45);a.add_argument('--fee',type=float,default=.0005);a.add_argument('--slippage',type=float,default=.0002)
    a.add_argument('--maintenance-rate',type=float,default=.01);a.add_argument('--liquidation-fee',type=float,default=.01)
    a.add_argument('--execution-delay-minutes',type=float,default=15.)
    a.add_argument('--min-holding-hours',type=float,default=24.);a.add_argument('--max-holding-hours',type=float,default=504.)
    a.add_argument('--scale-out-after-hours',type=float,default=168.);a.add_argument('--scale-out-interval-hours',type=float,default=48.)
    a.add_argument('--scale-out-fraction',type=float,default=.25);a.add_argument('--scale-out-deviation-atr',type=float,default=3.)
    a.add_argument('--label-holding-hours',type=float,default=48.)
    a.add_argument('--risk-atr-floor-hours',type=float,default=24.)
    a.add_argument('--swing-lookback-hours',type=float,default=168.)
    a.add_argument('--swing-min-range',type=float,default=.02);a.add_argument('--swing-max-range',type=float,default=.5)
    a.add_argument('--min-profit-move-pct',type=float,default=.02)
    a.add_argument('--price-target-pct',type=float,default=.05);a.add_argument('--price-stop-pct',type=float,default=.03)
    a=sub.add_parser('leveraged',help='Isolated futures validation replay with simulated mail audit')
    a.add_argument('--data',type=Path,required=True);a.add_argument('--baseline-report',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
    a.add_argument('--leverages',nargs='+',type=float,default=[3,5,10]);a.add_argument('--margin-fraction',type=float,default=.1)
    a.add_argument('--fee',type=float,default=.0005);a.add_argument('--slippage',type=float,default=.0002)
    a.add_argument('--maintenance-rate',type=float,default=.01);a.add_argument('--liquidation-fee',type=float,default=.01)
    a=sub.add_parser('analyze');a.add_argument('--symbol',required=True);a.add_argument('--bars',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--timeframes',nargs='+');a.add_argument('--pivot-width',type=int,default=2)
    a=sub.add_parser('analyze-multi',help='Eight-period candles, confirmed trend/levels, wave candidates and Dow123')
    a.add_argument('--symbol',required=True);a.add_argument('--data',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
    a.add_argument('--refresh',action='store_true',help='Regenerate derived analysis files in an existing analysis directory')
    a=sub.add_parser('research');a.add_argument('--data',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--fee',type=float,default=.0005);a.add_argument('--slippage',type=float,default=.0002);a.add_argument('--exposure',type=float,default=.2)
    for name in ('research','analyze'):
        sub.choices[name].add_argument('--email',action='store_true')
        sub.choices[name].add_argument('--email-state',type=Path,default=Path('runs/email_state'))
    a=sub.add_parser('signal',help='Forward observation from your trusted frozen model; never places orders')
    a.add_argument('--model',type=Path,required=True);a.add_argument('--data',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
    a.add_argument('--email',action='store_true');a.add_argument('--email-state',type=Path,default=Path('runs/email_state'))
    a=sub.add_parser('notify-report');a.add_argument('--report',type=Path,required=True);a.add_argument('--email-state',type=Path,default=Path('runs/email_state'))
    sub.add_parser('mail-test',help='Send a configuration test to the configured recipient')
    args=p.parse_args()
    if args.command=='download':
        if args.days<30: p.error('At least 30 days required')
        download(args.symbol,args.days,args.out)
    elif args.command=='demo': demo(args.out)
    elif args.command=='report':
        from operation_report import export_operations_report,print_operations_summary
        summary=export_operations_report(args.run)
        print_operations_summary(args.run,summary)
    elif args.command=='report-batch':
        from batch_report import export_batch_report
        records=json.loads((args.run/'run_status.json').read_text())
        export_batch_report(args.run,records)
        print('总体报告：'+str((args.run/'index.html').resolve()))
    elif args.command=='patterns':
        from pattern_research import run_pattern_research
        run_pattern_research(args.data,args.out,args.baseline_report,args.timeframes,args.exit_modes,
            args.leverage,args.margin_fraction,args.confidence,args.fee,args.slippage,args.maintenance_rate,args.liquidation_fee,
            args.execution_delay_minutes,args.min_holding_hours,args.max_holding_hours,args.scale_out_after_hours,
            args.scale_out_interval_hours,args.scale_out_fraction,args.scale_out_deviation_atr,args.label_holding_hours,
            swing_lookback_hours=args.swing_lookback_hours,swing_min_range=args.swing_min_range,swing_max_range=args.swing_max_range,
            min_profit_move_pct=args.min_profit_move_pct,price_target_pct=args.price_target_pct,price_stop_pct=args.price_stop_pct,
            risk_atr_floor_hours=args.risk_atr_floor_hours,models=args.models)
    elif args.command=='leveraged':
        from leverage_research import replay
        replay(args.data,args.baseline_report,args.out,args.leverages,args.margin_fraction,
               args.fee,args.slippage,args.maintenance_rate,args.liquidation_fee)
    elif args.command=='watchlist':
        from trend_report import WATCHLIST
        print(WATCHLIST.read_text())
    elif args.command=='analyze':
        from trend_report import analyze
        analyze(args.symbol.upper(),args.bars,args.out,args.timeframes,args.pivot_width)
    elif args.command=='analyze-multi':
        from structure_report import analyze_market
        analyze_market(args.symbol.upper(),args.data,args.out,refresh=args.refresh)
        print(str(args.out/'index.html'))
    elif args.command=='signal':
        from signals import signal_once
        signal_once(args.model,args.data,args.out,args.email,args.email_state)
    elif args.command=='notify-report':
        from notifications import notify_report
        print(notify_report(args.report,args.email_state))
    elif args.command=='mail-test':
        from notifications import send_email
        send_email('[量化研究] 邮件配置测试','邮件通知配置测试。程序仅发送研究信息和模型观察信号，所有交易由你手动执行。')
        print('SMTP_ACCEPTED')
    else: run(args.data,args.out,args.fee,args.slippage,args.exposure)
    if args.command in ('research','analyze') and args.email:
        from notifications import notify_report
        print(notify_report(args.out/'report.json',args.email_state))

if __name__=='__main__':
    from notifications import NotificationError
    try:
        main()
    except NotificationError as error:
        raise SystemExit(str(error)) from None
