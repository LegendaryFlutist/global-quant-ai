"""Twelve reversal families + cost-aware ML + predefined exits research.

One common validation calendar for all entry timeframes. Only one globally
selected configuration may access the reserved holdout. Reusing inspected
validation data and comparing many configurations still creates selection bias.
"""
import hashlib
import html
import json
import os
from pathlib import Path
import tempfile
import warnings
import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier,ExtraTreesClassifier,HistGradientBoostingClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.utils.class_weight import compute_sample_weight
from candlestick_patterns import PATTERN_LABELS,PATTERN_DEFINITIONS,PATTERN_SOURCES,PATTERN_COLUMNS
from pattern_features import prepare_timeframe,entry_signals,FEATURE_COLUMNS,MINUTES,PATTERN_STRATEGY
from trend_report import load_bars
from paper_mail import record_simulated
from model_registry import MODEL_NAMES
from market_structure import analysis_signals

EXIT_MODES=('swing_percent','fixed_rr','atr_trailing','three_bar_breakout','three_bar_trailing','time_exit','fixed_three_bar_target')
EXIT_LABELS={'fixed_rr':'固定2R止盈','atr_trailing':'ATR跟踪止盈','three_bar_breakout':'突破此前三根高点止盈',
             'three_bar_trailing':'跌破此前三根低点跟踪止盈','time_exit':'持仓时间上限退出','fixed_three_bar_target':'入场前固定三根高点目标',
             'swing_percent':'价格波段百分比目标'}


def screening_reasons(row):
    reasons=[]
    if row['complete_folds']!=3:reasons.append('完整训练折不足3')
    if row['net_return']<=0:reasons.append('累计净收益非正')
    if row['validation_score']<=0:reasons.append('折收益中位数非正')
    if sum(fold['net_return']>0 for fold in row['folds'])<2:reasons.append('盈利折不足2')
    if row['entries']<30:reasons.append('交易少于30笔')
    if row['max_drawdown']>.1:reasons.append('压力回撤超过10%')
    if row['liquidations']:reasons.append('出现近似强平')
    return reasons


def estimator(name):
    if name in ('pattern_only','analysis_dow123'):return None
    if name=='torch_gru':
        from deep_model import TorchGRUClassifier
        return TorchGRUClassifier()
    if name=='logistic':return make_pipeline(StandardScaler(),LogisticRegression(C=.1,class_weight='balanced',max_iter=1000))
    if name=='random_forest':return RandomForestClassifier(n_estimators=120,max_depth=5,min_samples_leaf=15,class_weight='balanced_subsample',n_jobs=1,random_state=42)
    if name=='extra_trees':return ExtraTreesClassifier(n_estimators=120,max_depth=5,min_samples_leaf=15,class_weight='balanced',n_jobs=1,random_state=42)
    if name=='hist_gradient':return HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=7,min_samples_leaf=15,l2_regularization=5,class_weight='balanced',early_stopping=False,random_state=42)
    if name=='mlp':return make_pipeline(StandardScaler(),MLPClassifier(hidden_layer_sizes=(16,),alpha=1,max_iter=600,shuffle=False,early_stopping=False,random_state=42))
    raise ValueError('Unknown model')


def fit_model(name,train):
    model=estimator(name)
    messages=[]
    if model is not None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            kwargs={'mlpclassifier__sample_weight':compute_sample_weight('balanced',train.label)} if name=='mlp' else {}
            model.fit(train[list(FEATURE_COLUMNS)],train.label,**kwargs)
        messages=[str(w.message) for w in caught]
    return model,messages


def model_signals(name,frame,model,confidence,swing_kwargs):
    if name in ('analysis_dow123','torch_gru'):
        return analysis_signals(frame,model,confidence,**swing_kwargs)
    return entry_signals(frame,model,confidence,**swing_kwargs)


def run_pattern_research(data_dir,out,baseline_report,timeframes=tuple(MINUTES),exit_modes=EXIT_MODES,
                         leverage=5.,margin_fraction=.1,confidence=.45,fee=.0005,slip=.0002,
                         maintenance_rate=.01,liquidation_fee=.01,execution_delay_minutes=15.,
                         min_holding_hours=24.,max_holding_hours=504.,scale_out_after_hours=168.,
                         scale_out_interval_hours=48.,scale_out_fraction=.25,
                         scale_out_deviation_atr=3.,label_holding_hours=48.,swing_lookback_hours=168.,
                         swing_min_range=.02,swing_max_range=.5,min_profit_move_pct=.02,
                         price_target_pct=.05,price_stop_pct=.03,risk_atr_floor_hours=24.,models=None):
    from lab import dataset
    from pattern_execution import simulate_patterns
    if out.exists():raise ValueError('Output exists; preserve prior research')
    model_names=tuple(MODEL_NAMES if models is None else models)
    if not model_names or len(set(model_names))!=len(model_names) or any(name not in MODEL_NAMES for name in model_names):raise ValueError('Invalid models')
    if not timeframes or len(set(timeframes))!=len(timeframes) or any(t not in MINUTES for t in timeframes):raise ValueError('Invalid timeframes')
    if not exit_modes or len(set(exit_modes))!=len(exit_modes) or any(x not in EXIT_MODES for x in exit_modes):raise ValueError('Invalid exit modes')
    if not 0<confidence<1:raise ValueError('Confidence must lie between zero and one')
    durations=[execution_delay_minutes,min_holding_hours,max_holding_hours,scale_out_after_hours,scale_out_interval_hours,label_holding_hours]
    if not all(np.isfinite(float(v)) for v in durations) or execution_delay_minutes<0 or min_holding_hours<0 or max_holding_hours<min_holding_hours or max_holding_hours<=0:
        raise ValueError('Invalid execution delay or holding duration')
    if scale_out_after_hours<min_holding_hours or scale_out_interval_hours<=0 or label_holding_hours<=0 or (label_holding_hours*60)%15:
        raise ValueError('Invalid scale-out schedule or label horizon')
    swing_values=[swing_lookback_hours,swing_min_range,swing_max_range,min_profit_move_pct,price_target_pct,price_stop_pct,risk_atr_floor_hours]
    if not all(np.isfinite(float(v)) for v in swing_values) or swing_lookback_hours<4 or swing_lookback_hours%4:
        raise ValueError('Historical swing window must be a positive multiple of four hours')
    if not 0<=swing_min_range<swing_max_range or not 0<=min_profit_move_pct<=price_target_pct or not 0<price_target_pct<=.5 or not 0<price_stop_pct<1:
        raise ValueError('Invalid price swing filter, profit threshold, target or stop')
    if risk_atr_floor_hours<.25 or (risk_atr_floor_hours*60)%15:
        raise ValueError('Risk ATR timeframe must be a positive multiple of 15 minutes')
    swing_kwargs=dict(swing_min_range=swing_min_range,swing_max_range=swing_max_range)
    execution_kwargs=dict(execution_delay_ms=int(execution_delay_minutes*60000),min_holding_ms=int(min_holding_hours*3600000),
        max_holding_ms=int(max_holding_hours*3600000),scale_out_after_ms=int(scale_out_after_hours*3600000),
        scale_out_interval_ms=int(scale_out_interval_hours*3600000),scale_out_fraction=scale_out_fraction,scale_out_deviation_atr=scale_out_deviation_atr)
    source=json.loads((data_dir/'source.json').read_text())
    prior=json.loads(baseline_report.read_text())
    bars=load_bars(data_dir/'bars.csv');marks=load_bars(data_dir/'mark_bars.csv')
    funding=pd.read_csv(data_dir/'funding.csv')
    dataset(bars,funding) # Preserve original price/funding integrity checks.
    digest=hashlib.sha256((data_dir/'bars.csv').read_bytes()+(data_dir/'funding.csv').read_bytes()).hexdigest()
    if prior.get('dataset_sha256')!=digest:raise ValueError('Baseline report must correspond to the same source data')
    cut=int(prior['holdout_period_ms'][0])
    start=int(bars.open_time.iloc[0])
    boundaries=np.linspace(start+(cut-start)//2,cut,4,dtype=np.int64)
    windows=[(int(a),int(z)) for a,z in zip(boundaries[:-1],boundaries[1:])]
    prepared={tf:prepare_timeframe(bars,marks,tf,fee,slip,
        execution_delay_ms=execution_kwargs['execution_delay_ms'],label_holding_ms=int(label_holding_hours*3600000),risk_atr_floor_minutes=int(risk_atr_floor_hours*60),
        swing_lookback_hours=swing_lookback_hours,label_min_move_pct=min_profit_move_pct) for tf in timeframes}
    execution=bars.copy()
    for col in ('open','high','low','close'):execution['mark_'+col]=marks[col].to_numpy()
    risk=dict(leverage=leverage,margin_fraction=margin_fraction,fee=fee,slip=slip,
              maintenance_rate=maintenance_rate,liquidation_fee=liquidation_fee,
              min_profit_move_pct=min_profit_move_pct,price_target_pct=price_target_pct,price_stop_pct=price_stop_pct)
    # Validate risk settings via engine before creating artifacts.
    sample=next(iter(prepared.values())).iloc[:20].copy()
    simulate_patterns(sample,pd.DataFrame({'direction':0,'pattern':''},index=sample.index),funding_events=funding,execution_data=execution,**risk,**execution_kwargs)
    out.mkdir(parents=True)
    results=[];curves={};trade_cache={};event_records=[];decisions=[];fit_warnings=[];pattern_counts=[];deep_training=[]
    for timeframe,frame in prepared.items():
        finite=np.isfinite(frame[list(FEATURE_COLUMNS)].to_numpy(dtype=float)).all(axis=1)
        usable=frame.loc[finite]
        for col in PATTERN_COLUMNS:
            pattern_counts.append(dict(timeframe=timeframe,pattern=col.removeprefix('pattern_'),
                bullish=int((frame.loc[frame.close_time<cut,col]==1).sum()),bearish=int((frame.loc[frame.close_time<cut,col]==-1).sum())))
        predictions={name:[] for name in model_names}
        for fold,(left,right) in enumerate(windows,1):
            train=usable[(usable.close_time<left)&(usable.label_end_time<left)]
            valid=frame[(frame.open_time>=left)&(frame.close_time<right)].copy()
            complete=len(train)>=80 and train.label.nunique()>=2 and len(valid)>=8
            execution_fold=execution[(execution.open_time>=valid.open_time.iloc[0])&(execution.close_time<=valid.close_time.iloc[-1])] if len(valid) else execution.iloc[:0]
            for name in model_names:
                model=None
                if complete:
                    if name=='torch_gru':print(f'Training PyTorch GRU: {source["symbol"]} {timeframe} fold {fold}',flush=True)
                    model,notes=fit_model(name,train)
                    if hasattr(model,'training_audit_'):
                        checkpoint=out/'deep_checkpoints'/timeframe/f'fold_{fold}.pt'
                        model.export(checkpoint)
                        deep_training.append(dict(timeframe=timeframe,fold=fold,checkpoint=str(checkpoint.relative_to(out)),
                            train_last_label_end=int(train.label_end_time.max()),validation_start=left,**model.training_audit_))
                    if hasattr(model,'set_context'):
                        model.set_context(frame.loc[frame.close_time<left,list(FEATURE_COLUMNS)])
                    for note in notes:fit_warnings.append(dict(timeframe=timeframe,model=name,fold=fold,warning=note))
                    allowed=np.isfinite(valid[list(FEATURE_COLUMNS)].to_numpy(dtype=float)).all(axis=1)
                    signals=pd.DataFrame({'direction':0,'pattern':''},index=valid.index)
                    if allowed.any():signals.loc[allowed]=model_signals(name,valid.loc[allowed],model,confidence,swing_kwargs)
                else:signals=pd.DataFrame({'direction':0,'pattern':''},index=valid.index)
                predictions[name].append((fold,valid,signals,complete,execution_fold))
                for row,side,patterns in zip(valid.itertuples(),signals.direction,signals.pattern):
                    decisions.append(dict(record_type='ENTRY_CANDIDATE',timeframe=timeframe,higher_timeframe_minutes=MINUTES[timeframe]*4,
                        model=name,fold=fold,decision_time=int(row.close_time),signal=int(side),pattern=patterns,
                        higher_trend=int(row.higher_trend),swing_range_pct=float(row.swing_range_pct) if pd.notna(row.swing_range_pct) else None,
                        signal_price=float(row.close),earliest_execution_time=int(row.close_time+1+execution_kwargs['execution_delay_ms']),status='SIMULATED_NOT_SENT',fold_complete=complete))
        for name in model_names:
            for exit_mode in exit_modes:
                key=f'{timeframe}|{name}|{exit_mode}'
                capital=1.;curve={windows[0][0]:1.};fold_metrics=[];trades=[];complete_folds=0;stress_points=[1.]
                for fold,valid,signals,complete,execution_fold in predictions[name]:
                    if len(valid):
                        result=simulate_patterns(valid,signals,exit_mode=exit_mode,funding_events=funding,execution_data=execution_fold,
                            max_holding_bars=max(1,int(np.ceil(max_holding_hours*60/MINUTES[timeframe]))),**risk,**execution_kwargs)
                        eq=result['equity']
                        for row in eq.itertuples():curve[int(row.close_time)]=float(capital*row.equity)
                        metric=result['metrics']
                        stress_points.extend(float(capital*x) for x in result['risk_points'])
                        ledger=result['trades'].to_dict('records')
                        for event in result.get('events',pd.DataFrame()).to_dict('records'):
                            event_records.append(dict(config=key,timeframe=timeframe,model=name,exit_mode=exit_mode,fold=fold,fold_start_equity=capital,**event))
                        for trade in ledger:trades.append(dict(config=key,timeframe=timeframe,model=name,exit_mode=exit_mode,fold=fold,fold_start_equity=capital,stitched_net_pnl=float(capital*trade['net_pnl']),**trade))
                        capital*=float(eq.equity.iloc[-1])
                    else:metric=dict(net_return=0.,max_drawdown=0.,entries=0,liquidations=0)
                    fold_metrics.append(dict(fold=fold,complete=bool(complete),**metric))
                    complete_folds+=int(complete)
                arr=np.array(list(curve.values()))
                count=sum(int(m.get('entries',0)) for m in fold_metrics)
                liquidations=sum(int(m.get('liquidations',0)) for m in fold_metrics)
                close_dd=float(np.max(1-arr/np.maximum.accumulate(arr)))
                risk_array=np.array(stress_points)
                dd=max(close_dd,float(np.max(1-risk_array/np.maximum.accumulate(risk_array))))
                score=float(np.median([m['net_return'] for m in fold_metrics]))
                eligible=(complete_folds==3 and capital>1 and score>0 and sum(m['net_return']>0 for m in fold_metrics)>=2
                          and count>=30 and dd<=.1 and liquidations==0)
                hours=[float(t['exit_time']-t['entry_time'])/3600000 for t in trades]
                results.append(dict(config=key,timeframe=timeframe,model=name,exit_mode=exit_mode,
                    net_return=float(capital-1),max_drawdown=dd,close_drawdown=close_dd,entries=count,liquidations=liquidations,
                    validation_score=score,complete_folds=complete_folds,eligible=bool(eligible),folds=fold_metrics,
                    holding_hours_mean=float(np.mean(hours)) if hours else 0.,holding_hours_median=float(np.median(hours)) if hours else 0.,
                    holding_hours_max=float(max(hours)) if hours else 0.,partial_exit_count=sum(int(t.get('partial_exit_count',0)) for t in trades)))
                results[-1]['rejection_reasons']=screening_reasons(results[-1])
                curves[key]=curve;trade_cache[key]=trades
        print('Validated reversal patterns:',source['symbol'],timeframe,flush=True)
    results.sort(key=lambda r:(r['eligible'],r['validation_score']),reverse=True)
    selected=next((r for r in results if r['eligible']),None)
    report=dict(source=source,strategy=PATTERN_STRATEGY,feature_columns=list(FEATURE_COLUMNS),model_names=list(model_names),deep_training=deep_training,dataset_sha256=digest,
        mark_sha256=hashlib.sha256((data_dir/'mark_bars.csv').read_bytes()).hexdigest(),
        validation_windows=windows,holdout_cut_ms=cut,original_holdout_boundary_preserved=True,holdout_used=bool(selected),risk=risk,
        period_pairs={t:dict(entry_minutes=MINUTES[t],higher_minutes=4*MINUTES[t]) for t in timeframes},
        confidence=confidence,pattern_labels=PATTERN_LABELS,pattern_definitions=PATTERN_DEFINITIONS,
        pattern_sources=PATTERN_SOURCES,exit_modes={m:EXIT_LABELS[m] for m in exit_modes},
        feature_change=f'12 reversals + ATR/RSI + confirmed structure/Dow123/wave/cycle + closed higher context; risk ATR floored by closed {risk_atr_floor_hours:g}h ATR',
        analysis_models=dict(analysis_dow123='Confirmed lower-period Dow123 event + same-direction closed 4x trend + nonopposing confirmed pivot structure + historical swing filter; Elliott candidates are context, never forced predictions',
            torch_gru='2-layer 32-unit GRU, 24 consecutive closed-candle inputs, training-only scaler, 20 fixed epochs; probability + higher trend + nonopposing confirmed structure + historical swing filter'),
        holding_policy=dict(execution_delay_minutes=execution_delay_minutes,min_holding_hours=min_holding_hours,max_holding_hours=max_holding_hours,
            scale_out_after_hours=scale_out_after_hours,scale_out_interval_hours=scale_out_interval_hours,scale_out_fraction=scale_out_fraction,
            scale_out_deviation_atr=scale_out_deviation_atr,label_holding_hours=label_holding_hours,risk_atr_floor_hours=risk_atr_floor_hours,
            execution_interval_minutes=15,exceptions=['hard_stop','mark_liquidation','research_cutoff']),
        swing_policy=dict(lookback_hours=swing_lookback_hours,min_range=swing_min_range,max_range=swing_max_range,
            range_definition='(highest high - lowest low) / lowest low across fully closed 4h candles only',
            min_profit_move_pct=min_profit_move_pct,price_target_pct=price_target_pct,price_stop_pct=price_stop_pct,
            exceptions=['hard_stop','liquidation','trend_or_opposite_signal','max_holding_time','research_cutoff','age_deviation_scale_out'],
            profit_definition='directional executed price return, before fees and funding; not account return',
            selected_future_swing_guaranteed=False),
        label=f'3 classes: first 15m open after signal close+human delay to {label_holding_hours:g}h later close; hurdle=max(2*(fee+slip)+0.25*entry-period ATR/close, {min_profit_move_pct:g}+2*(fee+slip)); label_end strictly before validation',
        status='NO_CANDIDATE',ranking=results,selected=selected,fit_warnings=fit_warnings,
        actual_email_sent=False,real_orders_enabled=False,
        limitations=['Validation data has already been inspected in previous experiments; selection bias persists',
          '12 patterns overlap and votes are not independent probabilities',
          '4h entries require 16h context; 90d history may not support three sufficient training folds',
          '1% maintenance rate default is an assumption, not exact Binance liquidation tiers',
          'OHLC cannot resolve intrabar stop/target/mark/funding order; conservative priorities are approximations',
          'Funding within a candle is booked before intrabar exits; a negative funding credit can be overstated if the true exit was earlier',
          'Historical 2%-50% range is an entry qualification, never a guarantee of future favorable price movement',
          'Validation fold cutoffs may close positions before the 21-day upper limit; 90d history limits long-horizon evidence'])
    # Exactly one candidate is tested once; other holdout curves are not inspected.
    if selected:
        tf=selected['timeframe'];frame=prepared[tf]
        finite=np.isfinite(frame[list(FEATURE_COLUMNS)].to_numpy(dtype=float)).all(axis=1)
        train=frame.loc[finite&(frame.close_time<cut)&(frame.label_end_time<cut)]
        holdout=frame[frame.open_time>=cut].copy()
        model,notes=fit_model(selected['model'],train)
        if hasattr(model,'set_context'):model.set_context(frame.loc[frame.close_time<cut,list(FEATURE_COLUMNS)])
        signals=model_signals(selected['model'],holdout,model,confidence,swing_kwargs)
        maximum_bars=max(1,int(np.ceil(max_holding_hours*60/MINUTES[tf])))
        check=simulate_patterns(holdout,signals,exit_mode=selected['exit_mode'],funding_events=funding,execution_data=execution,max_holding_bars=maximum_bars,**risk,**execution_kwargs)
        stress=simulate_patterns(holdout,signals,exit_mode=selected['exit_mode'],funding_events=funding,execution_data=execution,max_holding_bars=maximum_bars,**{**risk,'fee':2*fee,'slip':2*slip},**execution_kwargs)
        report['holdout']=check['metrics'];report['holdout_double_cost']=stress['metrics']
        passed=(check['metrics']['net_return']>0 and stress['metrics']['net_return']>0
            and check['metrics'].get('max_drawdown_stress',1)<=.1 and check['metrics'].get('entries',0)>=20
            and check['metrics'].get('liquidations',0)==0 and source.get('synthetic') is False)
        report['status']='FORWARD_PAPER_REVIEW' if passed else 'REJECTED'
        check['equity'].to_csv(out/'selected_holdout_equity.csv',index=False)
        check['trades'].to_csv(out/'selected_holdout_trades.csv',index=False)
        check.get('events',pd.DataFrame()).to_csv(out/'selected_holdout_events.csv',index=False)
        joblib.dump(dict(model=model,model_name=selected['model'],timeframe=tf,exit_mode=selected['exit_mode'],
            strategy=PATTERN_STRATEGY,features=FEATURE_COLUMNS,confidence=confidence,risk=risk,source=source,
            research_status=report['status'],holding_policy=report['holding_policy'],swing_policy=report['swing_policy'],training_label_end_ms=int(train.label_end_time.max())),out/'pattern_model.joblib')
    pd.DataFrame(decisions).to_csv(out/'decisions.csv',index=False)
    pd.DataFrame(pattern_counts).to_csv(out/'pattern_counts.csv',index=False)
    pd.DataFrame([{k:v for k,v in r.items() if k!='folds'} for r in results]).to_csv(out/'ranking.csv',index=False)
    pd.DataFrame([trade for key in trade_cache for trade in trade_cache[key]]).to_csv(out/'trades.csv',index=False)
    pd.DataFrame(event_records).to_csv(out/'events.csv',index=False)
    with (out/'validation_curves.json').open('w') as stream:json.dump(curves,stream,allow_nan=False)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (out/'deep_training.json').write_text(json.dumps(deep_training,ensure_ascii=False,indent=2,allow_nan=False))
    from notifications import preview_report
    preview_report(out/'report.json')
    export_pattern_report(out,report,curves)
    finalize_pattern_mail(out,report)
    from operation_report import export_operations_report,print_operations_summary
    operation_summary=export_operations_report(out)
    print_operations_summary(out,operation_summary)
    print(json.dumps(dict(symbol=source['symbol'],status=report['status'],configurations=len(results),
                         simulated_mails=report['simulated_mail_count'],out=str(out)),ensure_ascii=False),flush=True)
    return report


def finalize_pattern_mail(out,report=None):
    """Resume audit export from persisted results without retraining/retesting."""
    if report is None:report=json.loads((out/'report.json').read_text())
    ledger=pd.read_csv(out/'trades.csv')
    ledger=ledger.astype(object).where(pd.notna(ledger),None)
    events=pd.DataFrame()
    if (out/'events.csv').is_file() and (out/'events.csv').stat().st_size>1:
        events=pd.read_csv(out/'events.csv',low_memory=False)
        events=events.astype(object).where(pd.notna(events),None)
    # Simulate event mails for one displayed configuration per timeframe only.
    simulated=0
    for timeframe in report['period_pairs']:
        chosen=next(r for r in report['ranking'] if r['timeframe']==timeframe)
        trades=ledger[ledger.config==chosen['config']].to_dict('records')
        for number,trade in enumerate(trades):
            decision=dict(mode='HISTORICAL_VALIDATION_REPLAY',**trade)
            key=f"{report['dataset_sha256']}:{chosen['config']}:{number}"
            record_simulated(out/'simulated_outbox',key,f"{report['source']['symbol']} {chosen['config']} 平仓回放",
                '历史研究回放，未实际发送、未下单。\n'+json.dumps(decision,ensure_ascii=False,indent=2),decision)
            simulated+=1
        if not events.empty:
            partials=events[(events.config==chosen['config'])&(events.event=='PARTIAL_EXIT')]
            for event in partials.to_dict('records'):
                decision=dict(mode='HISTORICAL_VALIDATION_REPLAY',**event)
                key=f"{report['dataset_sha256']}:{chosen['config']}:{event['fold']}:{event['position_id']}:{event['time']}:PARTIAL_EXIT"
                record_simulated(out/'simulated_outbox',key,f"{report['source']['symbol']} {chosen['config']} 分批减仓回放",
                    '历史研究回放，未实际发送、未下单。\n'+json.dumps(decision,ensure_ascii=False,indent=2),decision)
                simulated+=1
    record_simulated(out/'simulated_outbox','summary:'+report['dataset_sha256'],f"{report['source']['symbol']} 12形态研究完成",
        f"状态：{report['status']}。详见本地 comparison.html。",{'mode':'RESEARCH_SUMMARY','status':report['status']})
    report['simulated_mail_count']=simulated+1
    report['simulation_complete']=True
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    from notifications import preview_report
    preview_report(out/'report.json')
    return report


def export_pattern_report(out,report,curves):
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'quant_lab_matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    timeframes=list(report['period_pairs']);rank=report['ranking']
    if report.get('deep_training'):
        training=report['deep_training']
        fig,axes=plt.subplots(len(timeframes),1,figsize=(11,3*len(timeframes)),squeeze=False,layout='constrained')
        for ax,tf in zip(axes.flat,timeframes):
            for row in training:
                if row['timeframe']==tf:ax.plot(np.arange(1,len(row['loss_history'])+1),row['loss_history'],label=f'fold {row["fold"]} | {row["sequences"]} sequences')
            ax.set(title=tf+' | training loss only',xlabel='Epoch',ylabel='Weighted cross entropy');ax.grid(alpha=.2)
            if ax.lines:ax.legend(fontsize=8)
        for ext in ('svg','png'):fig.savefig(out/('deep_training_loss.'+ext),dpi=130)
        plt.close(fig)
        training_rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (r['timeframe'],r['fold'],r['device'],r['gpu_name'],r['sequences'],r['parameters'],f'{r["loss_history"][0]:.4f}',f'{r["loss_history"][-1]:.4f}'))+'</tr>' for r in training)
        training_page='<meta charset="utf-8"><title>PyTorch GRU真实训练记录</title><style>body{max-width:1150px;margin:25px auto;font:16px sans-serif}img{width:100%}td,th{padding:9px;border-bottom:1px solid #ddd}</style><h1>PyTorch双层GRU训练记录</h1><p><a href="comparison.html">返回研究比较</a></p><p>24根连续已收盘K线输入，双层32单元GRU。标准化只用训练集；过去但未参与标签训练的K线可作为验证输入上下文。固定20轮，不用验证或留出收益选训练轮次。训练损失下降不代表样本外盈利。</p><table><tr><th>周期</th><th>折</th><th>训练设备</th><th>GPU</th><th>训练序列</th><th>参数量</th><th>初始损失</th><th>最终损失</th></tr>'+training_rows+'</table><img src="deep_training_loss.svg"><p><a href="deep_training.json">完整训练审计与检查点路径</a></p>'
        (out/'deep_training.html').write_text(training_page)
    for group in ('models','exits'):
        fig,axes=plt.subplots(len(timeframes),1,figsize=(13,4*len(timeframes)),squeeze=False,layout='constrained')
        for ax,tf in zip(axes.flat,timeframes):
            if group=='models':
                names=list(dict.fromkeys(r['model'] for r in rank))
                chosen=[next(r for r in rank if r['timeframe']==tf and r['model']==name) for name in names]
            else:
                baseline_name='pattern_only' if any(r['model']=='pattern_only' for r in rank) else rank[0]['model']
                chosen=[next(r for r in rank if r['timeframe']==tf and r['model']==baseline_name and r['exit_mode']==mode) for mode in report['exit_modes']]
            for row in chosen:
                series=curves[row['config']]
                times=pd.to_datetime([int(t) for t in series],unit='ms',utc=True)
                label=row['model']+' / '+row['exit_mode'] if group=='models' else row['exit_mode']
                ax.plot(times,(np.array(list(series.values()))-1)*100,label=label,linewidth=1)
            ax.axhline(0,color='gray',linewidth=.7);ax.grid(alpha=.2)
            ax.set(title=f"{report['source']['symbol']} | {tf} entry / {report['period_pairs'][tf]['higher_minutes']}m trend | validation",ylabel='Account return (%)')
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3,maxticks=5));ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
            ax.legend(ncol=2,fontsize=8)
        for ext in ('png','svg'):fig.savefig(out/(group+'_comparison.'+ext),dpi=150)
        plt.close(fig)
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
        (r['timeframe'],r['model'],EXIT_LABELS[r['exit_mode']],f"{r['net_return']:.2%}",f"{r['max_drawdown']:.2%}",r['entries'],r['complete_folds'],r['eligible']))+'</tr>' for r in rank[:60])
    names='、'.join(PATTERN_LABELS.values())
    text=f'<!doctype html><meta charset="utf-8"><title>多周期结构与深度模型研究</title><style>body{{max-width:1350px;margin:25px auto;font:16px sans-serif}}img{{width:100%}}td,th{{padding:6px;border-bottom:1px solid #ddd}}</style><h1>{html.escape(report["source"]["symbol"])} 多周期结构 / GRU / 道氏123 / 多止盈比较</h1>'
    if report.get('deep_training'):text+='<p><a href="deep_training.html">查看PyTorch GRU实际GPU训练记录、损失曲线与检查点</a></p>'
    text+=f'<p>状态：{report["status"]}。逐仓{report["risk"]["leverage"]:g}×，保证金{report["risk"]["margin_fraction"]:.0%}。原6模型以同向反转形态与四倍大周期筛选；新增道氏123以确认反转事件入场，GRU以时序方向概率入场，二者均结合大周期、确认结构及历史波动筛选。无新信号不自动平仓。</p><p>{html.escape(names)}</p>'
    policy=report.get('holding_policy')
    if policy:
        text+=f'<p>人工响应延迟{policy["execution_delay_minutes"]:g}分钟，使用原始15分钟行情成交；常规持仓{policy["min_holding_hours"]:g}–{policy["max_holding_hours"]:g}小时，硬止损、强平和研究截止可提前退出。满{policy["scale_out_after_hours"]:g}小时且偏离入场价达到{policy["scale_out_deviation_atr"]:g}倍ATR后，每{policy["scale_out_interval_hours"]:g}小时减初始仓位{policy["scale_out_fraction"]:.0%}。风险ATR取信号周期与已收盘{policy.get("risk_atr_floor_hours",4):g}小时ATR的较大值；训练收益标签统一{policy["label_holding_hours"]:g}小时。</p>'
    swing=report.get('swing_policy')
    if swing:
        text+=f'<p>机会筛选：最近{swing["lookback_hours"]:g}小时已收盘4小时K线的(最高价−最低价)/最低价在{swing["min_range"]:.0%}～{swing["max_range"]:.0%}。普通止盈至少需要标的有利价格变动{swing["min_profit_move_pct"]:.0%}，波段模式目标{swing["price_target_pct"]:.0%}；保护止损取风险ATR止损与{swing["price_stop_pct"]:.0%}价格止损中较近的一档。历史波动筛选不能保证未来收益；趋势反向、持仓上限、风控与截尾可提前退出。</p>'
    text+='<p>所有周期共用验证日历，旧预留测试边界保持不变。4小时配16小时过滤，训练样本不足的折跳过且不能入选。以下曲线仅验证期，不能证明未来收益；模型图每模型按验证选择一种退出，退出图使用相同纯形态入场，隔离退出模式差异。</p>'
    if report.get('fit_warnings'):
        text+='<p>部分MLP折达到600次迭代仍未收敛，训练警告详见report.json；其曲线只能作为未充分收敛的研究输出。</p>'
    top_by_return=sorted(rank,key=lambda r:r['net_return'],reverse=True)[:10]
    top_rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
        (r['config'],f"{r['net_return']:.2%}",r['entries'],r['complete_folds'],'；'.join(r.get('rejection_reasons',[]))))+'</tr>' for r in top_by_return)
    text+='<h2>累计收益最高的10组（仅供观察，不能据此直接选模）</h2><table><tr><th>组合</th><th>验证账户收益</th><th>交易数</th><th>完整折</th><th>筛选说明</th></tr>'+top_rows+'</table>'
    text+='<img src="models_comparison.svg"><img src="exits_comparison.svg"><h2>排名前60（全表见ranking.csv）</h2><table><tr><th>周期</th><th>模型</th><th>退出方式</th><th>账户收益</th><th>压力回撤近似</th><th>笔数</th><th>完整折</th><th>验证合格</th></tr>'+rows+'</table>'
    if 'holdout' in report:text+='<p>仅验证选中候选的预留测试：'+html.escape(json.dumps(report['holdout'],ensure_ascii=False))+'</p>'
    (out/'comparison.html').write_text(text,encoding='utf-8')
