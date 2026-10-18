"""Forward inference with trusted local frozen models; notification only."""
import hashlib
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo
from datetime import datetime
import joblib
import numpy as np
from features import FEATURES, make_features
from technical import dow123
from trend_report import load_bars, aggregate
from notifications import deliver_once
from strategy import STRATEGY, signal_features


def infer(bundle, bars, source, now_ms=None):
    from lab import positions, STEP
    now = int(time.time()*1000) if now_ms is None else now_ms
    if source.get('synthetic') is not False or bundle.get('source',{}).get('synthetic') is not False:
        raise ValueError('Forward signals require explicitly non-synthetic data and model')
    if bundle.get('research_status') != 'FORWARD_PAPER_REVIEW':
        raise ValueError('Model has not passed research screening; report notifications remain available')
    if bundle.get('strategy') != STRATEGY:
        raise ValueError('Model strategy version differs; retrain with 1h trend / 15m entries')
    symbol = source.get('symbol')
    if not symbol or symbol != bundle.get('source',{}).get('symbol'):
        raise ValueError('Model/data symbol mismatch')
    if bundle.get('features') != FEATURES:
        raise ValueError('Frozen model feature schema mismatch')
    if not (bars.close_time-bars.open_time+1 == STEP).all() or not (bars.open_time.diff().dropna()==STEP).all():
        raise ValueError('Forward model requires contiguous 15m candles')
    if not (bars.close_time == bars.open_time+STEP-1).all() or not (bars.open_time % STEP == 0).all():
        raise ValueError('Invalid 15m candle timestamps')
    end = int(bars.close_time.iloc[-1])
    if not 0 < now-end <= STEP:
        raise ValueError('Latest closed candle is stale or unclosed; refresh market data first')
    if end <= int(bundle.get('training_end_ms',now)):
        raise ValueError('Forward candle must be later than model training end')
    d = signal_features(bars)
    if d.empty or not np.isfinite(d[FEATURES].iloc[-1].to_numpy(dtype=float)).all():
        raise ValueError('Insufficient history for latest feature calculation')
    threshold = float(bundle['threshold'])
    if not .5 < threshold < 1:
        raise ValueError('Invalid frozen signal threshold')
    signal = int(positions(bundle['name'],bundle['model'],d.iloc[-1:],threshold)[0])
    if signal not in (-1,0,1):
        raise ValueError('Invalid model signal')
    trends = {}
    for timeframe in ('1h','4h','1d'):
        candles = aggregate(bars,timeframe,'binance_usdm')
        if candles.empty:
            trends[timeframe] = {'status':'INSUFFICIENT_DATA'}
            continue
        latest = dow123(candles).iloc[-1]
        trends[timeframe] = dict(
            support=None if np.isnan(latest.support) else float(latest.support),
            resistance=None if np.isnan(latest.resistance) else float(latest.resistance),
            support_state=latest.support_state,resistance_state=latest.resistance_state,
            dow123_signal=int(latest.dow123_signal))
    return dict(symbol=symbol,model=bundle['name'],signal=signal,close_time=end,trends=trends,
                strategy=STRATEGY,higher_trend=int(d.higher_trend.iloc[-1]),
                reference_close=float(bars.close.iloc[-1]),threshold=threshold,
                status='FORWARD_PAPER_REVIEW',real_orders_enabled=False)


def signal_once(model_path, data_dir, out, email=False, state_dir=Path('runs/email_state')):
    # joblib deserialization executes code: only load your own trusted artifact.
    bundle = joblib.load(model_path)
    source = json.loads((data_dir/'source.json').read_text())
    bars = load_bars(data_dir/'bars.csv')
    result = infer(bundle,bars,source)
    result['model_sha256'] = hashlib.sha256(model_path.read_bytes()).hexdigest()
    when = datetime.fromtimestamp(result['close_time']/1000,ZoneInfo('Asia/Shanghai')).isoformat()
    direction = {-1:'偏空',0:'空仓/观望',1:'偏多'}[result['signal']]
    subject = f"[模型观察信号] {result['symbol']} {direction}"
    body = (f"标的：{result['symbol']}\n模型：{result['model']}\n方向：{direction}\n"
            f"K线收盘时间（上海）：{when}\n参考收盘价：{result['reference_close']}\n"
            f"信号ID：{result['model_sha256'][:12]}:{result['close_time']}\n"
            '周期规则：1小时定方向，15分钟信号仅顺势；周期比4:1。\n'
            '趋势背景：\n'+json.dumps(result.get('trends',{}),ensure_ascii=False,indent=2)+'\n'
            '这是前向纸面观察信号，请自行确认后手动操作。参考价不保证成交，不含实时止损；程序没有下单功能。')
    out.mkdir(parents=True,exist_ok=False)
    (out/'signal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (out/'email_preview.txt').write_text(subject+'\n\n'+body)
    if email:
        # A persisted position is not a new signal every 15m: deduplicate by
        # model revision + last direction change observed in chronological runs.
        import fcntl
        root = Path(state_dir)
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        import os
        identity = hashlib.sha256((os.environ.get('QUANT_MAIL_TO','')+os.environ.get('QUANT_MAIL_MODE','real')+result['symbol']+result['model_sha256']).encode()).hexdigest()
        marker = root/(identity+'.position.json')
        with (root/(identity+'.lock')).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            previous = json.loads(marker.read_text()) if marker.exists() else None
            if previous and result['close_time'] <= previous['close_time']:
                status = 'ALREADY_PROCESSED'
            elif previous and previous['signal']==result['signal']:
                status = 'UNCHANGED'
            else:
                key = f"signal:{identity}:{result['close_time']}:{result['signal']}"
                status = deliver_once(key,subject,body,root)
            if not previous or result['close_time'] > previous['close_time']:
                temp = marker.with_suffix('.tmp')
                temp.write_text(json.dumps(result))
                temp.replace(marker)
        print(status)
    else:
        print('PREVIEW_ONLY: '+str(out/'email_preview.txt'))
    return result
