"""Causal confirmed-pivot trend lines and sequential Dow 1-2-3 reversals."""
import numpy as np
import pandas as pd


def dow123(bars, pivot_width=2):
    """识别确认转折构成的道氏123事件和趋势线状态。

    Args:
        bars: 按时间升序排列的OHLCV表，时间戳单位为毫秒。
        pivot_width: 确认摆动点所需的左右K线根数；确认必须等右侧样本收盘。

    Returns:
        带信号、事件、支撑压力及确认锚点的DataFrame。

    Notes:
        原有细则保留如下：
        Signal at close; pivots at j become available only at j + width.

        Freeze a rising support/falling resistance at step 1. Then require a
        post-break lower high/higher low (step 2), and a close beyond the frozen
        pre-break low/high (step 3). Invalidate on a new original trend extreme.
        Position persists until the opposite confirmation; this is not a stop model.
    """
    if not isinstance(pivot_width, int) or pivot_width < 1:
        raise ValueError('pivot_width must be a positive integer')
    b = bars.reset_index(drop=True)
    price = {col:b[col].to_numpy(dtype=float) for col in ('high','low','close')}
    highs, lows, pending = [], [], {}
    active = 0
    result = []
    for i in range(len(b)):
        new = {}
        j = i - pivot_width
        if j >= pivot_width:
            for kind, col, store, cmp in [('high','high',highs,np.max), ('low','low',lows,np.min)]:
                window = price[col][j-pivot_width:i+1]
                value = float(price[col][j])
                if value == cmp(window) and np.count_nonzero(window == value) == 1:
                    point = (j, value)
                    store.append(point)
                    new[kind] = point
        close = float(price['close'][i])
        lines = {}
        for kind, pts in [('support',lows),('resistance',highs)]:
            if len(pts) >= 2:
                a, z = pts[-2:]
                slope = (z[1]-a[1])/(z[0]-a[0])
                lines[kind] = (a, z, slope, z[1]+slope*(i-z[0]))
        event = 0
        for direction in list(pending):
            p = pending[direction]
            invalid = (price['high'][i] > p['extreme'] if direction == -1
                       else price['low'][i] < p['extreme'])
            if invalid:
                del pending[direction]
                continue
            point = new.get('high' if direction == -1 else 'low')
            if point and point[0] > p['break_index']:
                if (point[1] < p['extreme'] if direction == -1 else point[1] > p['extreme']):
                    p['step'] = 2
            if p['step'] == 2 and (close < p['trigger'] if direction == -1 else close > p['trigger']):
                event = active = direction
                pending.clear()
                break
        # Candidates only start on an actual crossing of a currently known line.
        for direction, kind, pts in [(-1,'support',highs),(1,'resistance',lows)]:
            if direction in pending or kind not in lines or not pts or i == 0 or event:
                continue
            a, z, slope, value = lines[kind]
            previous_line = value-slope
            crossed = (slope > 0 and close < value and price['close'][i-1] >= previous_line
                       if direction == -1 else
                       slope < 0 and close > value and price['close'][i-1] <= previous_line)
            if crossed and pts[-1][0] > a[0]:
                pending[direction] = dict(step=1, break_index=i, extreme=pts[-1][1],
                                          trigger=z[1], anchors=(a[0],z[0]))
        row = dict(dow123_signal=active, dow123_event=event,
                   bearish_step=pending.get(-1,{}).get('step',0),
                   bullish_step=pending.get(1,{}).get('step',0))
        for kind in ('support','resistance'):
            line = lines.get(kind)
            row[kind] = line[3] if line else np.nan
            row[kind+'_anchor1'] = line[0][0] if line else np.nan
            row[kind+'_anchor2'] = line[1][0] if line else np.nan
            broken = line and (close < line[3] if kind == 'support' else close > line[3])
            row[kind+'_state'] = 'UNAVAILABLE' if line is None else ('BROKEN' if broken else 'HOLDING')
        result.append(row)
    return pd.DataFrame(result, index=bars.index)
