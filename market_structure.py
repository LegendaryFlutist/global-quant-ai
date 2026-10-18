"""Confirmed-pivot structure, explicit Elliott candidates and causal context."""
import numpy as np
import pandas as pd
from technical import dow123

ANALYSIS_FEATURES=('analysis_trend','analysis_dow_event','analysis_dow_state','analysis_support_gap','analysis_resistance_gap',
    'analysis_wave_bias','analysis_impulse','analysis_abc','analysis_cycle_bars','analysis_cycle_sin','analysis_cycle_cos','analysis_range_position')


def add_confirmed_turn(turns,point,min_fraction=.005):
    if not turns:turns.append(point);return
    last=turns[-1]
    if point['kind']==last['kind']:
        if (point['price']>last['price'])==(point['kind']=='high'):turns[-1]=point
    elif abs(point['price']/last['price']-1)>=min_fraction:turns.append(point)


def impulse(points):
    if len(points)!=6:return None
    direction=1 if [p['kind'] for p in points]==['low','high','low','high','low','high'] else -1 if [p['kind'] for p in points]==['high','low','high','low','high','low'] else 0
    if not direction:return None
    z=np.array([p['price'] for p in points])*direction
    lengths=np.array([z[1]-z[0],z[3]-z[2],z[5]-z[4]])
    if z[0]<z[2]<z[1] and z[3]>z[1] and z[1]<z[4]<z[3] and z[5]>z[3] and lengths[1]>=min(lengths[0],lengths[2]):
        return direction
    return None


def wave_candidate(turns):
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


def analysis_signals(frame,model=None,confidence=.45,swing_min_range=None,swing_max_range=None):
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
