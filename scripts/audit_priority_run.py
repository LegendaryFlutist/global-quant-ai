"""Reconstruct checkpoint inputs, entry constraints and account reconciliation."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import torch
from torch.torch_version import TorchVersion
from deep_model import TorchGRUClassifier
from pattern_features import prepare_timeframe,FEATURE_COLUMNS
from trend_report import load_bars

def audit(root):
    """逐折审查冻结研究的权重、可知时间、信号、成交和账本守恒。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。

    Returns:
        无返回值；任何审计不一致会通过断言失败，不重新训练模型。
    """
    root=Path(root);results=[];torch.set_num_threads(1)
    torch.backends.cuda.matmul.fp32_precision='ieee';torch.backends.cudnn.rnn.fp32_precision='ieee'
    for symbol in ['KORUUSDT','CLUSDT','NVDAUSDT','BTCUSDT']:
        run=root/'symbols'/symbol;r=json.loads((run/'report.json').read_text())
        assert r['source']['symbol']==symbol and r['source']['synthetic'] is False
        data=Path('data')/(symbol.lower()+'_90d_connector');bars=load_bars(data/'bars.csv');marks=load_bars(data/'mark_bars.csv')
        trades=pd.read_csv(run/'trades.csv');operations=pd.read_csv(run/'operations.csv');funding=pd.read_csv(data/'funding.csv')
        p=r['holding_policy'];s=r['swing_policy'];risk=r['risk']
        prepared={tf:prepare_timeframe(bars,marks,tf,fee=risk['fee'],slip=risk['slip'],execution_delay_ms=int(p['execution_delay_minutes']*60000),
            label_holding_ms=int(p['label_holding_hours']*3600000),risk_atr_floor_minutes=int(p['risk_atr_floor_hours']*60),
            swing_lookback_hours=s['lookback_hours'],label_min_move_pct=s['min_profit_move_pct']) for tf in r['period_pairs']}
        close_maps={tf:f.set_index('close_time') for tf,f in prepared.items()}
        opens=bars.set_index('open_time').open
        for t in trades.itertuples():
            f=close_maps[t.timeframe].loc[t.signal_time]
            assert int(t.entry_time)==int(t.signal_time)+1+int(p['execution_delay_minutes']*60000)
            np.testing.assert_allclose(t.entry_price,opens.loc[t.entry_time]*(1+t.direction*risk['slip']),rtol=1e-12)
            assert s['min_range']<=f.swing_range_pct<=s['max_range']
            assert f.higher_trend==t.direction
            if t.model=='analysis_dow123':assert f.analysis_dow_event==t.direction and f.analysis_trend!=-t.direction
            if t.model=='torch_gru':assert f.analysis_trend!=-t.direction
            if t.holding_hours<p['min_holding_hours']-1e-6:assert any(k in t.exit_reason for k in ('STOP','LIQUIDATION','FINAL_EXIT'))
            assert t.holding_hours<=p['max_holding_hours']+1e-6
            if 'PROFIT' in t.exit_reason:assert t.direction*(t.exit_price/t.entry_price-1)>=s['min_profit_move_pct']-1e-8
            np.testing.assert_allclose(t.gross_pnl-t.total_fee-t.funding_paid,t.net_pnl,atol=1e-12)
            np.testing.assert_allclose(t.directional_price_return,t.gross_pnl/t.entry_notional,atol=1e-12)
        for row in r['ranking']:
            tr=trades[trades.config==row['config']];op=operations[operations.config==row['config']]
            np.testing.assert_allclose(tr.stitched_net_pnl.sum(),row['net_return'],atol=1e-10)
            np.testing.assert_allclose(op.pnl_initial_equity_fraction.sum(),row['net_return'],atol=1e-10)
            np.testing.assert_allclose(op.fee_initial_equity_fraction.sum(),(tr.total_fee*tr.fold_start_equity).sum(),atol=1e-10)
        checked=0
        for row in r['deep_training']:
            left,right=r['validation_windows'][row['fold']-1];frame=prepared[row['timeframe']]
            finite=np.isfinite(frame[list(FEATURE_COLUMNS)]).all(axis=1)
            train=frame[finite&(frame.close_time<left)&(frame.label_end_time<left)]
            valid=frame[(frame.open_time>=left)&(frame.close_time<right)&finite]
            assert row['device']=='cuda' and row['gpu_name']=='NVIDIA GB10'
            assert row['train_last_label_end']<row['validation_start']
            assert row['epochs']==20 and len(row['loss_history'])==20 and np.isfinite(row['loss_history']).all()
            with torch.serialization.safe_globals([TorchVersion]):state=torch.load(run/row['checkpoint'],map_location='cpu',weights_only=True)
            assert state['feature_names']==list(FEATURE_COLUMNS)
            values=train[list(FEATURE_COLUMNS)].to_numpy(dtype=np.float32)
            mean=values.mean(axis=0);scale=values.std(axis=0);scale[scale<1e-6]=1.
            np.testing.assert_allclose(state['mean'],mean,rtol=1e-5,atol=1e-6);np.testing.assert_allclose(state['scale'],scale,rtol=1e-5,atol=1e-6)
            m=TorchGRUClassifier();m.feature_names_=state['feature_names'];m.mean_=np.asarray(state['mean'],dtype=np.float32);m.scale_=np.asarray(state['scale'],dtype=np.float32)
            m.device_='cpu';m.network_=m._network(len(m.feature_names_));m.network_.load_state_dict(state['state_dict']);m.set_context(frame.loc[frame.close_time<left,list(FEATURE_COLUMNS)])
            probabilities=m.predict_proba(valid[list(FEATURE_COLUMNS)]);at_time={int(t):i for i,t in enumerate(valid.close_time)}
            group=trades[(trades.model=='torch_gru')&(trades.timeframe==row['timeframe'])&(trades.fold==row['fold'])]
            for t in group.itertuples():
                probability=probabilities[at_time[int(t.signal_time)]];index=int(t.direction)+1
                assert probability[index]>=r['confidence']-1e-5
                assert probability[index]>=np.max(np.delete(probability,index))-1e-5
                checked+=1
        results.append(dict(symbol=symbol,configurations=len(r['ranking']),trades=len(trades),gru_checkpoints=len(r['deep_training']),
            gru_entry_predictions_reconstructed=checked,devices=sorted({x['device'] for x in r['deep_training']}),
            dow123_trades=int((trades.model=='analysis_dow123').sum()),price_mark_bars=len(bars),funding_events=len(funding),
            max_holding_hours=float(trades.holding_hours.max()),research_status=r['status'],holdout_used=r['holdout_used'],
            account_and_fee_reconciliation=True,causal_signal_time_and_filter_checks=True,training_scaler_reconstructed=True))
        print('Audited',symbol,flush=True)
    out=root/'audit';out.mkdir(exist_ok=True)
    (out/'priority_model_audit.json').write_text(json.dumps(dict(status='PASS',results=results),ensure_ascii=False,indent=2))
    print('PASS: checkpoints, probabilities, Dow123 entries, prices, durations and account books')

if __name__=='__main__':audit(sys.argv[1])
