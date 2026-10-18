"""Replay frozen validation thresholds across isolated-futures risk scenarios."""
import hashlib
import html
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo
from datetime import datetime
import numpy as np
import pandas as pd
from features import FEATURES
from futures_backtest import simulate_isolated
from paper_mail import record_simulated
from strategy import STRATEGY
from trend_report import load_bars


def prepare(data_dir):
    from lab import dataset
    bars=load_bars(data_dir/'bars.csv')
    marks=load_bars(data_dir/'mark_bars.csv')
    if not np.array_equal(bars.open_time,marks.open_time) or not np.array_equal(bars.close_time,marks.close_time):
        raise ValueError('Mark/trade candle coverage mismatch')
    funding=pd.read_csv(data_dir/'funding.csv')
    d=dataset(bars,funding)
    trades=bars.set_index('open_time');risk=marks.set_index('open_time')
    d['entry_price']=d.entry_time.map(trades.open)
    d['exit_price']=d.exit_time.map(trades.open)
    for name,col in [('mark_open','open'),('mark_high','high'),('mark_low','low')]:
        d[name]=d.entry_time.map(risk[col])
    d['exit_mark_open']=d.exit_time.map(risk.open)
    return d


def replay(data_dir, baseline_report, out, leverages=(3,5,10), margin_fraction=.1,
           fee=.0005,slip=.0002,maintenance_rate=.01,liquidation_fee=.01):
    from lab import models, positions
    if out.exists(): raise ValueError('Output exists; use a new directory')
    leverages=list(leverages)
    if not leverages or len(set(leverages))!=len(leverages): raise ValueError('Invalid duplicate/empty leverage scenarios')
    source=json.loads((data_dir/'source.json').read_text())
    baseline=json.loads(baseline_report.read_text())
    digest=hashlib.sha256((data_dir/'bars.csv').read_bytes()+(data_dir/'funding.csv').read_bytes()).hexdigest()
    if baseline.get('dataset_sha256')!=digest or baseline.get('strategy')!=STRATEGY:
        raise ValueError('Prior report must use the same data and 1h/15m strategy')
    d=prepare(data_dir)
    if len(d)<2400: raise ValueError('Insufficient history')
    # Validate all risk inputs before model fitting or creating output artifacts.
    for leverage in leverages:
        simulate_isolated(d.iloc[:1],[0],leverage,margin_fraction,fee,slip,maintenance_rate,liquidation_fee)
    split=int(len(d)*.8)
    boundaries=np.linspace(int(split*.5),split,4,dtype=int)
    frozen={row['name']:row['threshold'] for row in baseline['comparison']}
    names=list(models())+['trend','mean_reversion','dow123']
    out.mkdir(parents=True)
    predictions={};decision_rows=[];mail_count=0
    for name in names:
        threshold=frozen[name];predictions[name]=[]
        for fold,(left,right) in enumerate(zip(boundaries[:-1],boundaries[1:]),1):
            train,valid=d.iloc[:left-2],d.iloc[left:right-2]
            model=models().get(name)
            if model is not None: model.fit(train[FEATURES],train.target)
            signal=positions(name,model,valid,threshold)
            predictions[name].append((fold,valid,signal))
            previous=None
            for row,direction in zip(valid.itertuples(),signal):
                direction=int(direction)
                action=('OBSERVE' if direction==0 else 'OPEN_LONG' if direction==1 else 'OPEN_SHORT') if previous in (None,0) else (
                    'HOLD' if direction==previous else 'CLOSE_TO_FLAT' if direction==0 else 'REVERSE_LONG' if direction==1 else 'REVERSE_SHORT')
                decision=dict(mode='HISTORICAL_OOF_REPLAY',symbol=source['symbol'],model=name,threshold=threshold,
                    fold=fold,decision_time=int(row.close_time),expected_entry_time=int(row.entry_time),
                    reference_close=float(row.close),higher_trend=int(row.higher_trend),signal=direction,action=action,
                    leverage_scenarios=leverages,margin_fraction=margin_fraction,status='SIMULATED_NOT_SENT')
                decision_rows.append(decision)
                if previous is None or direction!=previous:
                    stamp=datetime.fromtimestamp(row.close_time/1000,ZoneInfo('Asia/Shanghai')).isoformat()
                    subject=f"{source['symbol']} {name} {action}"
                    body=(f'历史验证期决策回放，未实际发邮件、未下单。\n时间（上海）：{stamp}\n'
                          +json.dumps(decision,ensure_ascii=False,indent=2)+'\n阈值来自已查看过的验证结果，并非独立前向成绩。')
                    key=f"{digest}:{name}:{threshold}:{fold}:{int(row.close_time)}:{direction}"
                    if record_simulated(out/'simulated_outbox',key,subject,body,decision)=='SIMULATED_NOT_SENT': mail_count+=1
                previous=direction
            # Validation boundaries close all positions, independently of signals.
            boundary=dict(mode='HISTORICAL_OOF_REPLAY',symbol=source['symbol'],model=name,fold=fold,
                          decision_time=int(valid.exit_time.iloc[-1]),action='FOLD_END_CLOSE',signal=0,
                          status='SIMULATED_NOT_SENT',leverage_scenarios=leverages,margin_fraction=margin_fraction)
            if previous:
                key=f"{digest}:{name}:{threshold}:{fold}:boundary"
                record_simulated(out/'simulated_outbox',key,f"{source['symbol']} {name} FOLD_END_CLOSE",
                                 '回测折边界强制平仓，属于研究边界处理，不是模型预测信号。',boundary)
                mail_count+=1
        print('Replayed',source['symbol'],name,flush=True)
    pd.DataFrame(decision_rows).to_csv(out/'decisions.csv',index=False)
    summaries=[];curves={};trades=[]
    for leverage in leverages:
        for name in names:
            capital,curve=1.,{}
            all_stress=[1.];fold_metrics=[]
            for fold,valid,signal in predictions[name]:
                metric,path,events,stress=simulate_isolated(valid,signal,leverage,margin_fraction,fee,slip,maintenance_rate,liquidation_fee)
                if not curve: curve[int(valid.entry_time.iloc[0])]=1.
                curve.update({int(t):float(capital*e) for t,e in zip(valid.exit_time,path)})
                all_stress.extend(capital*x for x in stress)
                for event in events:
                    trades.append(dict(model=name,leverage=leverage,fold=fold,fold_start_equity=capital,**event))
                fold_metrics.append(metric);capital*=path[-1]
            array=np.asarray(all_stress)
            summary=dict(model=name,leverage=leverage,margin_fraction=margin_fraction,
                initial_nominal_exposure=leverage*margin_fraction,threshold=frozen[name],
                net_return=float(capital-1),max_drawdown_stress=float(np.max(1-array/np.maximum.accumulate(array))),
                liquidations=sum(m['liquidations'] for m in fold_metrics),entries=sum(m['entries'] for m in fold_metrics),
                fold_returns=[m['net_return'] for m in fold_metrics])
            summary['eligible']=(summary['net_return']>0 and sum(x>0 for x in summary['fold_returns'])>=2
                and summary['entries']>=30 and summary['max_drawdown_stress']<=.1 and summary['liquidations']==0)
            summaries.append(summary);curves[f'{name}@{leverage:g}x']=curve
    pd.DataFrame(trades).to_csv(out/'trades.csv',index=False)
    pd.DataFrame(summaries).to_csv(out/'leverage_comparison.csv',index=False)
    equity=pd.DataFrame(curves);equity.index.name='exit_time';equity.to_csv(out/'validation_equity.csv')
    policy=dict(mode='ISOLATED',margin_fraction=margin_fraction,leverages=leverages,fee_side=fee,
        slippage_side=slip,maintenance_rate=maintenance_rate,maintenance_amount=0,liquidation_fee=liquidation_fee,
        margin_auto_add=False,notional_at_entry='equity * margin_fraction * leverage',
        maintenance_rate_source='User-configurable research assumption, NOT verified exchange tiers')
    report=dict(source=source,strategy=STRATEGY,dataset_sha256=digest,
        mark_bars_sha256=hashlib.sha256((data_dir/'mark_bars.csv').read_bytes()).hexdigest(),risk=policy,
        status='RESEARCH_REPLAY_ONLY',real_orders_enabled=False,actual_email_sent=False,
        simulated_mail_count=mail_count+1,decision_count=len(decision_rows),comparison=summaries,
        holdout_used=False,baseline_report=str(baseline_report),
        eligible_replay_candidates=[{'model':s['model'],'leverage':s['leverage']} for s in summaries if s['eligible']],
        limitations=['Previously inspected validation data; no new independent evidence',
        'Fixed maintenance rate and zero tier deduction; not exact Binance liquidation replication',
        '15m mark extrema have unknown high/low order; stress drawdown assumes favorable then adverse',
        'Funding charged before risk check, not exact intrabar event order',
        'No partial liquidation, ADL, auto-added margin or liquidation liquidity model',
        'Liquidated direction stays blocked until desired signal changes; no automatic reentry',
        'Cash-equity returns, not margin ROI; no live strategy selected from leverage replay'])
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    export(out,equity,summaries,source,policy)
    # Record current research decision without pretending stale historical data is live.
    decision=dict(action='OBSERVE_RESEARCH_ONLY',mode='HISTORICAL_REPLAY_SUMMARY',symbol=source['symbol'],
                  last_input_close_time=int(d.close_time.iloc[-1]),live_model_enabled=False)
    record_simulated(out/'simulated_outbox','summary:'+digest,str(source['symbol'])+' 杠杆回放完成',
                     '回放已完成，详见 report.json；未启动实盘信号，未实际发信。',decision)
    print(json.dumps({'symbol':source['symbol'],'simulated_mail_count':mail_count+1,'actual_email_sent':False,'out':str(out)},ensure_ascii=False))
    return report


def export(out,equity,summaries,source,policy):
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'quant_lab_matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    leverages=policy['leverages'];names=list(dict.fromkeys(s['model'] for s in summaries))
    times=pd.to_datetime(equity.index,unit='ms',utc=True)
    fig,axes=plt.subplots(len(leverages),1,figsize=(13,4*len(leverages)),squeeze=False,layout='constrained')
    for ax,leverage in zip(axes.flat,leverages):
        for name in names: ax.plot(times,(equity[f'{name}@{leverage:g}x']-1)*100,label=name,linewidth=1)
        ax.axhline(0,color='gray',linewidth=.7);ax.grid(alpha=.2)
        ax.set(title=f"{source['symbol']} | isolated {leverage:g}x | margin {policy['margin_fraction']:.0%} | validation replay",ylabel='Account return (%)')
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3,maxticks=6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'));ax.legend(ncol=4,fontsize=8)
    for ext in ('png','svg'):fig.savefig(out/('leverage_comparison.'+ext),dpi=150)
    plt.close(fig)
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
        (s['model'],s['leverage'],f"{s['net_return']:.2%}",f"{s['max_drawdown_stress']:.2%}",s['entries'],s['liquidations']))+'</tr>' for s in summaries)
    page='<!doctype html><meta charset="utf-8"><title>合约杠杆回放</title><style>body{max-width:1300px;margin:25px auto;font:16px sans-serif}img{width:100%}td,th{padding:6px;border-bottom:1px solid #ddd}</style>'
    page+=f"<h1>{html.escape(source['symbol'])} 逐仓杠杆回放</h1><p>1小时定方向 / 15分钟入场，保证金比例 {policy['margin_fraction']:.0%}。收益按账户权益计算，不是单笔保证金ROI。</p>"
    page+=f'<p>这是已看过的验证集回放，不是新前向成绩。固定维持保证金率假设为{policy["maintenance_rate"]:.2%}，未使用真实阶梯；强平与回撤均为近似压力估计。模拟邮件未通过SMTP发送。</p><img src="leverage_comparison.svg"><table><tr><th>模型</th><th>倍数</th><th>账户收益</th><th>压力回撤</th><th>开仓次数</th><th>估计强平次数</th></tr>'+rows+'</table>'
    (out/'comparison.html').write_text(page,encoding='utf-8')
