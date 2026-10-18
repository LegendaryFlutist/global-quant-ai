"""Closed-candle multi-timeframe features and cost-aware research labels."""
import numpy as np
import pandas as pd
from quant_lab.data.features import FEATURES,make_features
from quant_lab.analysis.candlestick_patterns import reversal_patterns,PATTERN_COLUMNS
from quant_lab.analysis.market_structure import structure_features,ANALYSIS_FEATURES

MINUTES={'15m':15,'30m':30,'1h':60,'2h':120,'4h':240}
FEATURE_COLUMNS=tuple(FEATURES)+tuple(PATTERN_COLUMNS)+('bullish_count','bearish_count','atr_pct','rsi14','higher_ema_gap')+ANALYSIS_FEATURES
PATTERN_STRATEGY='multiperiod_structure_gru_dow123_v4'


def aggregate_closed(bars, minutes, source_minutes=15):
    """把连续且对齐的原始K线聚合为完整大周期。

    Args:
        bars: 按时间升序排列的OHLCV表，时间戳单位为毫秒。
        minutes: 目标K线周期分钟数。
        source_minutes: 源K线周期分钟数，聚合必须为整数倍。

    Returns:
        OHLCV表；不完整边界桶被排除，不允许向小周期插值。
    """
    step=int(minutes*60000);base=int(source_minutes*60000)
    if minutes<source_minutes or minutes%source_minutes:
        raise ValueError('Aggregation must be an integer multiple of source timeframe')
    if bars.empty: return bars.copy()
    b=bars.reset_index(drop=True)
    if not (b.open_time.diff().dropna()==base).all() or not (b.close_time-b.open_time+1==base).all() or not (b.open_time%base==0).all():
        raise ValueError('Aggregation needs aligned contiguous source candles')
    g=b.groupby(b.open_time//step,sort=True)
    out=g.agg(open_time=('open_time','first'),close_time=('close_time','last'),open=('open','first'),
              high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'),count=('close','size'))
    out=out[(out['count']==step//base)&(out.open_time%step==0)&(out.close_time==out.open_time+step-1)]
    return out.drop(columns='count').reset_index(drop=True)


def prepare_timeframe(bars,marks,timeframe,fee=.0005,slip=.0002,horizon=4,
                      execution_delay_ms=0,label_holding_ms=None,risk_atr_floor_minutes=None,
                      swing_lookback_hours=None,label_min_move_pct=0.):
    """构造多周期形态/结构特征及严格分离的成本感知未来标签。

    Args:
        bars: 按时间升序排列的OHLCV表，时间戳单位为毫秒。
        marks: 与成交K线逐行同时间对齐的标记价格OHLC表。
        timeframe: 源或目标周期名称，例如15m/4h。
        fee: 单边成交手续费比例，例如0.0005表示0.05%。
        slip: 单边不利滑点比例。
        horizon: 旧标签方案向后观察的K线根数；只能用于训练标签。
        execution_delay_ms: 决策收盘后模拟人工响应延迟，单位毫秒。
        label_holding_ms: 标签在实际可观察成交后持有的毫秒数。
        risk_atr_floor_minutes: 用于已收盘大周期ATR风险下限的周期分钟数。
        swing_lookback_hours: 只看过去完整历史的波幅窗口小时数。
        label_min_move_pct: 训练标签的最低标的价格变动比例。

    Returns:
        含特征、标记价格、标签结束时间和历史波幅的数据表。
    """
    if timeframe not in MINUTES: raise ValueError('Unsupported entry timeframe')
    if not np.isfinite(label_min_move_pct) or not 0<=label_min_move_pct<=.5:
        raise ValueError('Label minimum price move must be between zero and 50%')
    if len(bars)!=len(marks) or not np.array_equal(bars.open_time,marks.open_time) or not np.array_equal(bars.close_time,marks.close_time):
        raise ValueError('Trade and mark source candles must align')
    minute=MINUTES[timeframe]
    lower=aggregate_closed(bars,minute)
    risk=aggregate_closed(marks,minute)
    upper=aggregate_closed(bars,minute*4)
    if not np.array_equal(lower.open_time,risk.open_time): raise ValueError('Aggregated mark candles mismatch')
    d=make_features(lower)
    patterns=reversal_patterns(lower)
    d=pd.concat([d,patterns],axis=1)
    d=pd.concat([d,structure_features(lower)],axis=1)
    previous=lower.close.shift(1)
    true_range=pd.concat([lower.high-lower.low,(lower.high-previous).abs(),(lower.low-previous).abs()],axis=1).max(axis=1)
    d['atr']=true_range.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    d['atr_pct']=d.atr/d.close
    delta=lower.close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    d['rsi14']=gain/(gain+loss)
    d.loc[(gain==0)&(loss==0),'rsi14']=.5
    upper['higher_ema_gap']=(upper.close.ewm(span=12,adjust=False,min_periods=48).mean()/
                              upper.close.ewm(span=48,adjust=False,min_periods=48).mean()-1)
    upper['higher_trend']=np.sign(upper.higher_ema_gap).fillna(0).astype(int)
    known=upper[['close_time','higher_ema_gap','higher_trend']].rename(columns={'close_time':'higher_close_time'})
    context=pd.merge_asof(lower[['close_time']],known,left_on='close_time',right_on='higher_close_time',direction='backward')
    d['higher_close_time']=context.higher_close_time
    d['higher_ema_gap']=context.higher_ema_gap
    d['higher_trend']=context.higher_trend.fillna(0).astype(int)
    for name in ('open','high','low','close'):d['mark_'+name]=risk[name].to_numpy()
    if swing_lookback_hours is not None:
        if (not np.isfinite(swing_lookback_hours) or swing_lookback_hours < 4
                or swing_lookback_hours % 4):
            raise ValueError('Swing lookback must be a positive multiple of four hours')
        # A fixed 4h reference keeps the opportunity filter comparable across
        # entry timeframes. Incomplete candles and future extrema never enter.
        reference=aggregate_closed(bars,240)
        count=int(swing_lookback_hours/4)
        reference['swing_high']=reference.high.rolling(count,min_periods=count).max()
        reference['swing_low']=reference.low.rolling(count,min_periods=count).min()
        reference['swing_range_pct']=(reference.swing_high-reference.swing_low)/reference.swing_low
        reference=reference[['close_time','swing_high','swing_low','swing_range_pct']].rename(columns={'close_time':'swing_close_time'})
        context=pd.merge_asof(d[['close_time']],reference,left_on='close_time',right_on='swing_close_time',direction='backward')
        for name in ('swing_close_time','swing_high','swing_low','swing_range_pct'):
            d[name]=context[name].to_numpy()
    # Labels are separated from features; their end time gates every training cut.
    if label_holding_ms is None:
        d['label_end_time']=d.close_time.shift(-horizon)
        future=d.close.shift(-horizon)/d.open.shift(-1)-1
    else:
        step=15*60000
        if execution_delay_ms<0 or label_holding_ms<=0 or label_holding_ms%step:
            raise ValueError('Delayed labels need nonnegative delay and a positive 15m-multiple holding horizon')
        # Use the actual first available 15m open AFTER the human delay, even
        # when the signal itself is a 4h candle. Never invent an intrabar fill.
        entry_time=((d.close_time+1+execution_delay_ms+step-1)//step)*step
        end_time=entry_time+label_holding_ms-1
        entry_price=entry_time.map(bars.set_index('open_time').open)
        exit_price=end_time.map(bars.set_index('close_time').close)
        d['label_entry_time']=entry_time.where(entry_price.notna())
        d['label_end_time']=end_time.where(entry_price.notna()&exit_price.notna())
        future=exit_price/entry_price-1
    hurdle=pd.concat([2*(fee+slip)+.25*d.atr_pct,
                      pd.Series(label_min_move_pct+2*(fee+slip),index=d.index)],axis=1).max(axis=1)
    # Preserve the original warm-up label behavior: unknown ATR cannot label a
    # move as positive/negative merely because the new scalar floor is known.
    hurdle=hurdle.where(d.atr_pct.notna())
    d['label']=np.where(future>hurdle,1,np.where(future< -hurdle,-1,0)).astype(int)
    if risk_atr_floor_minutes is not None:
        reference=aggregate_closed(bars,risk_atr_floor_minutes)
        prior=reference.close.shift(1)
        tr=pd.concat([reference.high-reference.low,(reference.high-prior).abs(),(reference.low-prior).abs()],axis=1).max(axis=1)
        reference['risk_atr_floor']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
        floor=pd.merge_asof(d[['close_time']],reference[['close_time','risk_atr_floor']],on='close_time',direction='backward').risk_atr_floor
        d['signal_atr']=d.atr
        d['risk_atr_floor']=floor
        d['atr']=pd.concat([d.atr,floor],axis=1).max(axis=1).where(floor.notna())
    return d


def entry_signals(frame,model=None,confidence=.45,swing_min_range=None,swing_max_range=None):
    """要求反转形态、大周期和可选模型方向一致后才保留入场。

    Args:
        frame: 已对齐特征列与可知时间的当前数据表。
        model: 已拟合估计器；None表示只用规则，不在推断阶段拟合。
        confidence: 候选方向概率门槛；未经校准，不能解释为获利概率。
        swing_min_range: 已观测历史价格波幅下限比例。
        swing_max_range: 已观测历史价格波幅上限比例。

    Returns:
        方向与命中形态名称的表；未满足门槛时方向为0。
    """
    candidate=frame.candidate_direction.to_numpy(dtype=int)
    higher=frame.higher_trend.to_numpy(dtype=int)
    permitted=(candidate==higher)&(candidate!=0)
    bullish=frame.bullish_count.to_numpy(dtype=int)
    bearish=frame.bearish_count.to_numpy(dtype=int)
    pattern_values=frame[list(PATTERN_COLUMNS)].to_numpy(dtype=int)
    permitted&=(candidate==np.sign(bullish-bearish))
    permitted&=np.where(candidate>0,(bullish>=1)&(pattern_values==1).any(axis=1),
                        (bearish>=1)&(pattern_values==-1).any(axis=1))
    if swing_min_range is not None or swing_max_range is not None:
        low=0. if swing_min_range is None else float(swing_min_range)
        high=float('inf') if swing_max_range is None else float(swing_max_range)
        if not np.isfinite(low) or low<0 or np.isnan(high) or high<low:
            raise ValueError('Invalid historical swing range bounds')
        if 'swing_range_pct' not in frame:
            raise ValueError('Historical swing filter needs swing_range_pct features')
        measured=frame.swing_range_pct.to_numpy(dtype=float)
        permitted&=np.isfinite(measured)&(measured>=low)&(measured<=high)
    if model is not None:
        probability=model.predict_proba(frame[list(FEATURE_COLUMNS)])
        classes=list(model.classes_)
        def p(value):
            """按模型实际classes_顺序读取指定类别的概率。

            Args:
                value: 待转换或校验的当前值，具体口径见函数用途。

            Returns:
                对应类别概率数组；训练集中缺少该类时返回零数组。
            """
            return probability[:,classes.index(value)] if value in classes else np.zeros(len(frame))
        bullish,bearish,flat=p(1),p(-1),p(0)
        chosen=np.where((bullish>=confidence)&(bullish>bearish)&(bullish>flat),1,
                        np.where((bearish>=confidence)&(bearish>bullish)&(bearish>flat),-1,0))
        permitted&=candidate==chosen
    direction=np.where(permitted,candidate,0)
    names=[]
    for (_,row),side in zip(frame.iterrows(),direction):
        names.append('|'.join(col.removeprefix('pattern_') for col in PATTERN_COLUMNS if side and row[col]==side))
    return pd.DataFrame({'direction':direction,'pattern':names},index=frame.index)
