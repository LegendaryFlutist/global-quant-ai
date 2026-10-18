"""Shared backtest/forward 1h trend filter for 15m entries."""
import numpy as np
import pandas as pd
from features import make_features
from technical import dow123

STRATEGY = 'trend_1h_entry_15m_v1'
LOWER_MS = 15*60*1000
HIGHER_MS = 4*LOWER_MS


def hourly_context(bars):
    """Align the most recent fully closed hour to every lower-bar close.

    EMA12/48 direction requires 48 complete hourly candles. Partial first/last
    hours never contribute to the EMA; an hour is usable on its closing 15m bar.
    """
    b = bars.reset_index(drop=True)
    if b.empty:
        return pd.DataFrame(columns=['higher_close_time','higher_ema_gap','higher_trend'],index=bars.index)
    if not (b.close_time-b.open_time+1 == LOWER_MS).all() or not (b.open_time % LOWER_MS == 0).all():
        raise ValueError('Strategy requires aligned 15m candles')
    if not (b.open_time.diff().dropna()==LOWER_MS).all():
        raise ValueError('Strategy requires contiguous chronological candles')
    grouped = b.groupby(b.open_time//HIGHER_MS,sort=True).agg(
        open_time=('open_time','first'),close_time=('close_time','last'),
        close=('close','last'),count=('close','size'))
    hours=grouped[(grouped['count']==4) & (grouped.open_time % HIGHER_MS==0)].copy()
    fast=hours.close.ewm(span=12,adjust=False,min_periods=48).mean()
    slow=hours.close.ewm(span=48,adjust=False,min_periods=48).mean()
    hours['higher_ema_gap']=fast/slow-1
    hours['higher_trend']=np.sign(hours.higher_ema_gap).fillna(0).astype(int)
    hours=hours.rename(columns={'close_time':'higher_close_time'})
    available=hours[['higher_close_time','higher_ema_gap','higher_trend']].reset_index(drop=True)
    aligned=pd.merge_asof(b[['close_time']],available,left_on='close_time',right_on='higher_close_time',direction='backward')
    aligned['higher_trend']=aligned.higher_trend.fillna(0).astype(int)
    aligned.index=bars.index
    return aligned.drop(columns='close_time')


def signal_features(bars):
    d=make_features(bars)
    d['dow123_signal']=dow123(bars).dow123_signal
    return pd.concat([d,hourly_context(bars)],axis=1)


def follow_hourly(raw, data):
    if 'higher_trend' not in data:
        raise ValueError('Missing closed 1h trend context')
    raw=np.asarray(raw,dtype=int)
    direction=data.higher_trend.to_numpy(dtype=int)
    return np.where((raw==direction) & (direction!=0),raw,0)
