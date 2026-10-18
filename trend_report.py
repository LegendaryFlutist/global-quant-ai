"""Offline multi-timeframe reports for crypto and independently sourced ETFs."""
import html
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
from technical import dow123

WATCHLIST = Path(__file__).with_name('watchlist.json')
RULES = {'15m':'15min','30m':'30min','1h':'1h','2h':'2h','4h':'4h','8h':'8h','16h':'16h','1d':'1D','1w':'W-MON','1mo':'MS'}


def load_bars(path):
    b = pd.read_csv(path)
    required = ['open_time','close_time','open','high','low','close','volume']
    if not set(required).issubset(b.columns) or len(b) < 2:
        raise ValueError('CSV needs >=2 rows and ' + ','.join(required))
    b = b[required].apply(pd.to_numeric, errors='raise')
    if not np.isfinite(b.to_numpy()).all():
        raise ValueError('Nonfinite data')
    if not (b.open_time.diff().dropna() > 0).all() or (b.close_time < b.open_time).any():
        raise ValueError('Require chronological unique bars and valid close times')
    if (b.open_time.iloc[1:].to_numpy() <= b.close_time.iloc[:-1].to_numpy()).any():
        raise ValueError('Overlapping bars')
    if b.close_time.max() >= int(time.time()*1000):
        raise ValueError('Unclosed source candle')
    if (b[['open','high','low','close']] <= 0).any().any() or (b.volume < 0).any():
        raise ValueError('Invalid price or volume')
    if ((b.high < b[['open','close','low']].max(axis=1)) |
        (b.low > b[['open','close','high']].min(axis=1))).any():
        raise ValueError('Invalid OHLC')
    return b


def aggregate(b, timeframe, market):
    """Only fully covered, closed calendar buckets; never fill exchange holidays.

    ETF input uses UTC midnight session dates and close_time = next midnight-1.
    Weekly/monthly buckets end at the next Monday/month start. Current partial
    buckets and a leading bucket with uncertain coverage are excluded.
    """
    if timeframe not in RULES:
        raise ValueError('Unsupported timeframe: '+timeframe)
    durations = b.close_time-b.open_time+1
    if market == 'us_etf':
        if not (durations == 86400000).all() or not (b.open_time % 86400000 == 0).all():
            raise ValueError('ETF input requires daily UTC session-date bars')
    elif not (durations == 900000).all() or not (b.open_time.diff().dropna() == 900000).all():
        raise ValueError('Crypto input requires contiguous 15m bars')
    d = b.set_index(pd.to_datetime(b.open_time,unit='ms',utc=True))
    rule = RULES[timeframe]
    rows = []
    for start, group in d.resample(rule, closed='left', label='left'):
        if group.empty:
            continue
        end = start + pd.tseries.frequencies.to_offset(rule)
        start_ms, end_ms = start.value//1000000, end.value//1000000
        # Data coverage rather than wall-clock alone determines completeness.
        if start_ms < b.open_time.iloc[0] or end_ms > b.close_time.iloc[-1]+1:
            continue
        if market != 'us_etf' and len(group) != (end_ms-start_ms)//900000:
            continue
        rows.append(dict(open_time=start_ms,close_time=end_ms-1,
                         open=group.open.iloc[0],high=group.high.max(),low=group.low.min(),
                         close=group.close.iloc[-1],volume=group.volume.sum()))
    return pd.DataFrame(rows, columns=['open_time','close_time','open','high','low','close','volume'])


def chart(b, t, title):
    """Native SVG: latest confirmed anchors, price history and event markers."""
    if b.empty:
        return '<p>无完整周期数据</p>'
    first = max(0,len(b)-180)
    lo, hi = float(b.low.iloc[first:].min()),float(b.high.iloc[first:].max())
    span = max(hi-lo,hi*.001)
    x = lambda i: 55+890*(i-first)/max(1,len(b)-1-first)
    y = lambda v: 300-250*(v-lo)/span
    points = ' '.join(f'{x(i):.1f},{y(b.close.iloc[i]):.1f}' for i in range(first,len(b)))
    parts = [f'<svg viewBox="0 0 1000 350" role="img" aria-label="{html.escape(title)}"><defs><clipPath id="plot"><rect x="55" y="35" width="890" height="275"/></clipPath></defs>',
             f'<text x="55" y="24">{html.escape(title)}</text>',
             f'<text x="5" y="55">{hi:.2f}</text><text x="5" y="300">{lo:.2f}</text>',
             f'<g clip-path="url(#plot)"><polyline points="{points}" fill="none" stroke="#334155" stroke-width="2"/>']
    last = t.iloc[-1]
    for kind,color in [('support','#16a34a'),('resistance','#dc2626')]:
        if pd.isna(last[kind]):
            continue
        a,z = int(last[kind+'_anchor1']),int(last[kind+'_anchor2'])
        col = 'low' if kind == 'support' else 'high'
        av,zv = float(b[col].iloc[a]),float(b[col].iloc[z])
        left = max(a,first)
        lv = av+(zv-av)*(left-a)/(z-a)
        parts.append(f'<line x1="{x(left):.1f}" y1="{y(lv):.1f}" x2="{x(len(b)-1):.1f}" y2="{y(last[kind]):.1f}" stroke="{color}" stroke-dasharray="6 4"/>')
    for i in range(first,len(b)):
        event = t.dow123_event.iloc[i]
        if event:
            parts.append(f'<circle cx="{x(i):.1f}" cy="{y(b.close.iloc[i]):.1f}" r="5" fill="{"#16a34a" if event>0 else "#dc2626"}"/>')
    start_date = pd.to_datetime(b.open_time.iloc[first],unit='ms',utc=True).strftime('%Y-%m-%d')
    end_date = pd.to_datetime(b.open_time.iloc[-1],unit='ms',utc=True).strftime('%Y-%m-%d')
    parts.append(f'</g><text x="55" y="335">{start_date}</text><text x="850" y="335">{end_date}</text></svg>')
    return ''.join(parts)


def analyze(symbol, bars_path, out, timeframes=None, pivot_width=2):
    if not isinstance(pivot_width,int) or pivot_width < 1:
        raise ValueError('pivot_width must be a positive integer')
    instruments = json.loads(WATCHLIST.read_text())['instruments']
    instrument = next((x for x in instruments if x['symbol']==symbol),None)
    if instrument is None:
        raise ValueError('Add instrument to watchlist.json first')
    frames = timeframes or instrument['timeframes']
    if any(x not in RULES for x in frames) or len(set(frames)) != len(frames):
        raise ValueError('Invalid or duplicate timeframes')
    if instrument['market']=='us_etf' and any(x in frames for x in ('15m','1h','4h')):
        raise ValueError('Daily ETF input cannot produce intraday candles')
    b = load_bars(bars_path)
    source_path = bars_path.with_name('source.json')
    source = json.loads(source_path.read_text()) if source_path.exists() else {'synthetic':None}
    if source.get('symbol') not in (None, symbol, 'SYNTHETIC'):
        raise ValueError('source.json symbol does not match requested instrument')
    computed=[]
    for tf in frames:
        candles = aggregate(b,tf,instrument['market'])
        technical = dow123(candles,pivot_width) if len(candles) else pd.DataFrame()
        computed.append((tf,candles,technical))
    out.mkdir(parents=True,exist_ok=False)
    report = dict(instrument=instrument, input=str(bars_path), source=source,
                  input_sha256=hashlib.sha256(bars_path.read_bytes()).hexdigest(),pivot_width=pivot_width,
                  real_orders_enabled=False, timeframes={})
    content = ['<!doctype html><meta charset="utf-8"><title>多周期趋势分析</title>',
               '<style>body{max-width:1100px;margin:30px auto;font-family:sans-serif}svg{width:100%;border:1px solid #ddd;margin:12px 0}text{font-size:13px}</style>',
               '<h1>'+html.escape(symbol+' '+instrument['name'])+'</h1>',
               '<p>'+('合成数据：仅供功能演示' if source.get('synthetic') else '数据来源：'+html.escape(str(source)))+'</p>',
               '<p>绿虚线：最近两确认低点；红虚线：最近两确认高点。圆点：123 反转确认。趋势线可能已破位，详见 CSV。信号针对产品自身价格。</p>']
    for tf,candles,technical in computed:
        if len(candles):
            joined = pd.concat([candles,technical],axis=1)
            joined.to_csv(out/(tf+'.csv'),index=False)
            latest = technical.iloc[-1].replace({np.nan:None}).to_dict()
            report['timeframes'][tf] = dict(bars=len(candles),latest=latest,
                status='OK' if technical[['support','resistance']].iloc[-1].notna().all() else 'INSUFFICIENT_PIVOTS')
        else:
            report['timeframes'][tf] = dict(bars=0,status='INSUFFICIENT_DATA')
        content.append(chart(candles,technical,symbol+' '+tf))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (out/'trends.html').write_text('\n'.join(content),encoding='utf-8')
    print(str(out/'trends.html'))
