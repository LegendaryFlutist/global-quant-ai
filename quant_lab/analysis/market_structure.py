"""Confirmed-pivot structure, explicit Elliott candidates and causal context."""
import numpy as np
import pandas as pd
from quant_lab.analysis.technical import dow123

ANALYSIS_FEATURES=('analysis_trend','analysis_dow_event','analysis_dow_state','analysis_support_gap','analysis_resistance_gap',
    'analysis_wave_bias','analysis_impulse','analysis_abc','analysis_cycle_bars','analysis_cycle_sin','analysis_cycle_cos','analysis_range_position')


def add_confirmed_turn(turns,point,min_fraction=.005):
    """合并同向更极端的已确认转折，过滤小于幅度门槛的反向转折。

    Args:
        turns: 截至当前时刻已经确认的交替转折列表。
        point: 已确认转折字典，含类型、价格和确认时间。
        min_fraction: 忽略过小反向转折的最低相对价格幅度。

    Returns:
        无返回值；原地更新turns列表。
    """
    if not turns:turns.append(point);return
    last=turns[-1]
    if point['kind']==last['kind']:
        if (point['price']>last['price'])==(point['kind']=='high'):turns[-1]=point
    elif abs(point['price']/last['price']-1)>=min_fraction:turns.append(point)


def impulse(points):
    """检查六个交替摆动点是否满足简化推动浪约束。

    Args:
        points: 按顺序提供的已确认转折点列表。

    Returns:
        1表示上行、-1表示下行；不满足规则返回None。
    """
    if len(points)!=6:return None
    direction=1 if [p['kind'] for p in points]==['low','high','low','high','low','high'] else -1 if [p['kind'] for p in points]==['high','low','high','low','high','low'] else 0
    if not direction:return None
    z=np.array([p['price'] for p in points])*direction
    lengths=np.array([z[1]-z[0],z[3]-z[2],z[5]-z[4]])
    if z[0]<z[2]<z[1] and z[3]>z[1] and z[1]<z[4]<z[3] and z[5]>z[3] and lengths[1]>=min(lengths[0],lengths[2]):
        return direction
    return None


def wave_candidate(turns):
    """从已确认转折提出五浪、五浪加ABC或局部123候选。

    Args:
        turns: 截至当前时刻已经确认的交替转折列表。

    Returns:
        含状态、方向、标签、点位和失效价格的字典；歧义时不强行标浪。
    """
    if len(turns)>=9:
        p=turns[-9:];direction=impulse(p[:6])
        if direction:
            z=np.array([x['price'] for x in p])*direction
            if z[6]<z[5] and z[6]<z[7]<z[5] and z[8]<z[6] and z[8]>z[0]:
                return dict(status='FIVE_PLUS_ABC_CANDIDATE',direction=direction,labels=['0','1','2','3','4','5','A','B','C'],points=p,
                    invalidation=p[0]['price'],interpretation='已确认转折满足五浪与简化ABC条件；只是一种候选计数，不排除更大级别修正。')
    if len(turns)>=6:
        p=turns[-6:];direction=impulse(p)
        if direction:return dict(status='FIVE_WAVE_CANDIDATE',direction=direction,labels=['0','1','2','3','4','5'],points=p,
            invalidation=p[4]['price'],interpretation='五段已确认转折符合简化推动浪三条约束；五浪结束后仍可能延长或进入修正。')
    if len(turns)>=4:
        p=turns[-4:];direction=1 if [x['kind'] for x in p]==['low','high','low','high'] else -1
        z=np.array([x['price'] for x in p])*direction
        if z[0]<z[2]<z[1] and z[3]>z[1]:return dict(status='PARTIAL_123_CANDIDATE',direction=direction,labels=['0','1','2','3?'],points=p,
            invalidation=p[2]['price'],interpretation='可能为1-2-3段，也可能是更大结构的一部分；4/5浪与第三浪非最短尚未确认。')
    return dict(status='AMBIGUOUS',direction=0,labels=[],points=turns[-6:],invalidation=None,
        interpretation='当前确认转折无法给出唯一计数；保留区间震荡/修正结构解释，不强行标浪。')


def structure_features(bars,pivot_width=2,return_details=False):
    """逐根推进已确认摆动点，形成道氏、压力支撑、周期与波浪特征。

    Args:
        bars: 按时间升序排列的OHLCV表，时间戳单位为毫秒。
        pivot_width: 确认摆动点所需的左右K线根数；确认必须等右侧样本收盘。
        return_details: 是否同时返回确认转折和技术结构的审计明细。

    Returns:
        特征表；return_details为真时同时返回确认点等明细。
    """
    b=bars.reset_index(drop=True);technical=dow123(b,pivot_width)
    highs=b.high.to_numpy();lows=b.low.to_numpy();closes=b.close.to_numpy()
    turns=[];raw=[];high_points=[];low_points=[];rows=[]
    rolling_low=b.low.rolling(64,min_periods=1).min().to_numpy();rolling_high=b.high.rolling(64,min_periods=1).max().to_numpy()
    for i in range(len(b)):
        j=i-pivot_width
        if j>=pivot_width:
            found=[]
            for kind,values in [('high',highs),('low',lows)]:
                segment=values[j-pivot_width:i+1];v=values[j]
                if v==(segment.max() if kind=='high' else segment.min()) and np.count_nonzero(segment==v)==1:
                    p=dict(index=j,confirmed_index=i,time=int(b.close_time.iloc[j]),confirmed_at=int(b.close_time.iloc[i]),price=float(v),kind=kind)
                    raw.append(p);found.append(p)
                    (high_points if kind=='high' else low_points).append(p)
            # An outside bar can be both extrema. Its intrabar order is unknown.
            if len(found)==1:add_confirmed_turn(turns,found[0])
        trend=0
        if len(high_points)>=2 and len(low_points)>=2:
            dh=high_points[-1]['price']-high_points[-2]['price'];dl=low_points[-1]['price']-low_points[-2]['price']
            trend=1 if dh>0 and dl>0 else -1 if dh<0 and dl<0 else 0
        last=technical.iloc[i];close=closes[i];wave=wave_candidate(turns)
        periods=[turns[k]['index']-turns[k-2]['index'] for k in range(max(2,len(turns)-8),len(turns))]
        cycle=float(np.median(periods)) if len(periods)>=2 else 0.
        phase=2*np.pi*(i-turns[-1]['index'])/cycle if turns and cycle else 0.
        rows.append(dict(analysis_trend=trend,analysis_dow_event=int(last.dow123_event),analysis_dow_state=int(last.dow123_signal),
            analysis_support_gap=float(np.clip((close-last.support)/close,-1,1)) if pd.notna(last.support) else 0.,
            analysis_resistance_gap=float(np.clip((last.resistance-close)/close,-1,1)) if pd.notna(last.resistance) else 0.,
            analysis_wave_bias=wave['direction'],analysis_impulse=int(wave['status']=='FIVE_WAVE_CANDIDATE'),analysis_abc=int(wave['status']=='FIVE_PLUS_ABC_CANDIDATE'),
            analysis_cycle_bars=cycle/100.,analysis_cycle_sin=float(np.sin(phase)) if cycle else 0.,analysis_cycle_cos=float(np.cos(phase)) if cycle else 0.,
            analysis_range_position=float((close-rolling_low[i])/max(rolling_high[i]-rolling_low[i],1e-12))))
    result=pd.DataFrame(rows,index=bars.index,columns=ANALYSIS_FEATURES)
    return (result,technical,raw,turns) if return_details else result


def _rsi_series(closes, period=14):
    """计算仅依赖当前及过去收盘价的Wilder RSI序列。

    Args:
        closes: 按时间升序的有限正数收盘价格序列，单位为报价币。
        period: RSI Wilder平滑周期，单位为K线根数，至少为2。

    Returns:
        与输入等长的浮点RSI数组；预热不足的位置为NaN。

    Raises:
        ValueError: 周期长度或输入价格不符合计算要求。
    """
    values=np.asarray(closes,dtype=float)
    if not isinstance(period,int) or period<2 or not np.isfinite(values).all() or (values<=0).any():
        raise ValueError('RSI周期或收盘价格无效')
    result=np.full(len(values),np.nan)
    if len(values)<=period:return result
    changes=np.diff(values);gains=np.maximum(changes,0.);losses=np.maximum(-changes,0.)
    average_gain=float(gains[:period].mean());average_loss=float(losses[:period].mean())
    result[period]=50. if average_loss==0 and average_gain==0 else 100. if average_loss==0 else 100.-100./(1.+average_gain/average_loss)
    for index in range(period+1,len(values)):
        average_gain=(average_gain*(period-1)+gains[index-1])/period
        average_loss=(average_loss*(period-1)+losses[index-1])/period
        result[index]=50. if average_loss==0 and average_gain==0 else 100. if average_loss==0 else 100.-100./(1.+average_gain/average_loss)
    return result


def multitimeframe_structure(bars_by_timeframe,as_of_ms=None,source_hashes=None,pivot_width=2,rsi_period=14):
    """汇总每周期已收盘趋势线、道氏123、波浪候选与RSI背离事件。

    Args:
        bars_by_timeframe: 周期名到真实OHLCV DataFrame的映射；时间戳为UTC毫秒。
        as_of_ms: 可选统一观察截止UTC毫秒；截止时仍未收盘的K线会被丢弃。
        source_hashes: 可选周期到原始数据集SHA256的映射，用于事件溯源。
        pivot_width: 结构摆动点左右确认根数，至少为1。
        rsi_period: 因果Wilder RSI周期，单位为K线根数。

    Returns:
        带schema/算法版本、逐周期结构、趋势破坏时间和排序事件的JSON安全字典。

    Raises:
        ValueError: 时间顺序、OHLCV字段、截止时间或版本参数非法。

    Notes:
        每个pivot的occurred_at是转折K线收盘时刻，confirmed_at是右侧确认K线收盘时刻。
        趋势/123及背离事件只在所依据的K线收盘后发布，不把波浪候选解释成获利概率。
    """
    required={'open_time','close_time','open','high','low','close','volume'}
    if not bars_by_timeframe or not isinstance(pivot_width,int) or pivot_width<1:
        raise ValueError('必须提供周期数据且摆动宽度为正整数')
    if as_of_ms is not None and (not isinstance(as_of_ms,int) or as_of_ms<0):
        raise ValueError('观察截止必须为UTC毫秒整数')
    source_hashes=source_hashes or {};prepared={};observed=[]
    for timeframe,source in bars_by_timeframe.items():
        if not isinstance(timeframe,str) or not timeframe or not isinstance(source,pd.DataFrame) or not required.issubset(source.columns):
            raise ValueError('周期或OHLCV列不完整')
        frame=source.copy().reset_index(drop=True)
        frame['open_time']=pd.to_numeric(frame.open_time,errors='raise').astype('int64')
        frame['close_time']=pd.to_numeric(frame.close_time,errors='raise').astype('int64')
        if len(frame)>1 and not (np.diff(frame.open_time.to_numpy())>0).all():
            raise ValueError('周期K线必须按开盘时间严格升序且不重复')
        if (frame.close_time<frame.open_time).any():raise ValueError('K线收盘时间早于开盘时间')
        for column in ('open','high','low','close','volume'):
            frame[column]=pd.to_numeric(frame[column],errors='raise').astype(float)
        values=frame[['open','high','low','close','volume']].to_numpy()
        if not np.isfinite(values).all() or (frame[['open','high','low','close']]<=0).any().any() or (frame.volume<0).any():
            raise ValueError('OHLCV包含非法数值')
        if ((frame.high<frame[['open','close','low']].max(axis=1))|(frame.low>frame[['open','close','high']].min(axis=1))).any():
            raise ValueError('OHLC区间无效')
        observed.append(int(frame.close_time.max())+1 if len(frame) else 0)
        prepared[timeframe]=frame
    cutoff=as_of_ms if as_of_ms is not None else max(observed)
    events=[];summaries={}
    for timeframe,source in prepared.items():
        bars=source.loc[source.close_time<cutoff].reset_index(drop=True)
        digest=source_hashes.get(timeframe)
        if digest is not None and (not isinstance(digest,str) or len(digest)!=64 or any(ch not in '0123456789abcdef' for ch in digest.lower())):
            raise ValueError('数据集来源哈希必须为SHA256')
        if len(bars)<2*pivot_width+3:
            summaries[timeframe]={'status':'INSUFFICIENT_DATA','bars':len(bars),'source_sha256':digest,'events':[]}
            continue
        features,technical,pivots,turns=structure_features(bars,pivot_width,True)
        rsi=_rsi_series(bars.close.to_numpy(),rsi_period);previous={'high':None,'low':None};divergences=[]
        for point in pivots:
            index=point['index'];prior=previous[point['kind']];value=rsi[index]
            if prior is not None and np.isfinite(value) and np.isfinite(prior['rsi']):
                if point['kind']=='low' and point['price']<prior['price'] and value>prior['rsi']:
                    kind='BULLISH_RSI_DIVERGENCE'
                elif point['kind']=='high' and point['price']>prior['price'] and value<prior['rsi']:
                    kind='BEARISH_RSI_DIVERGENCE'
                else:kind=None
                if kind:
                    event=dict(kind=kind,timeframe=timeframe,occurred_at=int(point['time']),confirmed_at=int(point['confirmed_at']),
                        evidence=dict(previous_pivot_at=int(prior['time']),previous_price=float(prior['price']),price=float(point['price']),
                            previous_rsi=float(prior['rsi']),rsi=float(value),pivot_kind=point['kind']),
                        invalidation_price=float(point['price']),source_sha256=digest)
                    divergences.append(event);events.append(event)
            previous[point['kind']]=dict(time=point['time'],price=point['price'],rsi=value)
        for index in range(1,len(technical)):
            for line in ('support','resistance'):
                if technical[line+'_state'].iloc[index]=='BROKEN' and technical[line+'_state'].iloc[index-1]!='BROKEN':
                    event=dict(kind='TRENDLINE_BREAK',timeframe=timeframe,occurred_at=int(bars.close_time.iloc[index]),
                        confirmed_at=int(bars.close_time.iloc[index]),evidence=dict(line=line,close=float(bars.close.iloc[index]),
                            prior_state=str(technical[line+'_state'].iloc[index-1]),anchors=[int(technical[line+'_anchor1'].iloc[index]),int(technical[line+'_anchor2'].iloc[index])]),
                        invalidation_price=None,source_sha256=digest)
                    events.append(event)
            direction=int(technical.dow123_event.iloc[index])
            if direction:
                event=dict(kind='DOW123_CONFIRMED',timeframe=timeframe,occurred_at=int(bars.close_time.iloc[index]),
                    confirmed_at=int(bars.close_time.iloc[index]),evidence=dict(direction=direction,
                        bullish_step=int(technical.bullish_step.iloc[index]),bearish_step=int(technical.bearish_step.iloc[index])),
                    invalidation_price=None,source_sha256=digest)
                events.append(event)
        latest=technical.iloc[-1];last_bar=bars.iloc[-1];last_point_by_index={p['index']:p for p in pivots}
        lines={}
        for name,column in (('support','low'),('resistance','high')):
            a=latest[name+'_anchor1'];z=latest[name+'_anchor2']
            if pd.isna(a) or pd.isna(z):
                lines[name]=dict(state='UNAVAILABLE',value=None,anchors=[])
                continue
            a=int(a);z=int(z);slope=(float(bars[column].iloc[z])-float(bars[column].iloc[a]))/(z-a)
            anchors=[dict(time=int(bars.close_time.iloc[pos]),price=float(bars[column].iloc[pos]),
                confirmed_at=int(last_point_by_index[pos]['confirmed_at']) if pos in last_point_by_index else int(bars.close_time.iloc[pos])) for pos in (a,z)]
            lines[name]=dict(state=str(latest[name+'_state']),value=float(latest[name]),slope_per_bar=float(slope),anchors=anchors,
                confirmed_at=max(point['confirmed_at'] for point in anchors))
        trend=int(features.analysis_trend.iloc[-1]);dow_direction=int(latest.dow123_signal)
        summaries[timeframe]=dict(status='OK',bars=len(bars),last_closed_at=int(last_bar.close_time),close=float(last_bar.close),
            source_sha256=digest,trend=trend,trendline_break_state='BROKEN' if any(lines[name]['state']=='BROKEN' for name in lines) else 'HOLDING_OR_UNAVAILABLE',
            trend_lines=lines,dow123_state=dow_direction,wave=wave_candidate(turns),latest_divergence=divergences[-1] if divergences else None,
            pivots=[dict(kind=p['kind'],price=float(p['price']),occurred_at=int(p['time']),confirmed_at=int(p['confirmed_at']),index=int(p['index'])) for p in pivots[-20:]])
    events.sort(key=lambda event:(event['confirmed_at'],event['timeframe'],event['kind']))
    return dict(schema_version=1,algorithm='confirmed_structure_divergence_v1',as_of_ms=cutoff,timeframes=summaries,events=events)


def analysis_signals(frame,model=None,confidence=.45,swing_min_range=None,swing_max_range=None):
    """结合结构事件、大周期方向、历史波幅及可选模型概率过滤入场。

    Args:
        frame: 已对齐特征列与可知时间的当前数据表。
        model: 已拟合估计器；None表示只用规则，不在推断阶段拟合。
        confidence: 候选方向概率门槛；未经校准，不能解释为获利概率。
        swing_min_range: 已观测历史价格波幅下限比例。
        swing_max_range: 已观测历史价格波幅上限比例。

    Returns:
        与输入索引对齐的方向/形态信号表，不是订单。
    """
    higher=frame.higher_trend.to_numpy(dtype=int)
    if model is None:
        side=frame.analysis_dow_event.to_numpy(dtype=int);reason='DOW123_CONFIRMED'
    else:
        p=model.predict_proba(frame[model.feature_names_]);classes=list(model.classes_)
        positive=p[:,classes.index(1)];negative=p[:,classes.index(-1)];flat=p[:,classes.index(0)]
        side=np.where((positive>=confidence)&(positive>negative)&(positive>flat),1,
            np.where((negative>=confidence)&(negative>positive)&(negative>flat),-1,0));reason='GRU_MULTIPERIOD_STRUCTURE'
    allowed=(side!=0)&(side==higher)&(frame.analysis_trend.to_numpy()!=-side)
    if swing_min_range is not None or swing_max_range is not None:
        measured=frame.swing_range_pct.to_numpy()
        allowed&=np.isfinite(measured)&(measured>=(0 if swing_min_range is None else swing_min_range))&(measured<=(np.inf if swing_max_range is None else swing_max_range))
    side=np.where(allowed,side,0)
    return pd.DataFrame(dict(direction=side,pattern=np.where(side!=0,reason,'')),index=frame.index)
