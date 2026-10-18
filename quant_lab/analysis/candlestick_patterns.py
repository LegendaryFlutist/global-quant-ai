"""Twelve causal candlestick reversal families for already closed OHLC bars.

These are explicit, fixed research definitions, not TA-Lib-compatible outputs.
Each ``pattern_*`` column is +1 (bullish), -1 (bearish), or 0. A pattern must
follow an opposite five-bar local trend, measured *before* its first candle.
The result is an event at this candle's close, never a held trading position.

Reference definitions:
https://ta-lib.org/functions/
https://ta-lib.org/functions/cdl3inside.html
https://ta-lib.org/functions/cdl3outside.html
https://ta-lib.org/functions/cdlabandonedbaby.html
https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bullish-reversal-patterns

Crypto-specific convention: piercing/cloud and morning/evening star allow a
near-continuous open (10% of the preceding average range). This is intentionally
broader than strict session-gap patterns. Abandoned baby retains strict gaps.
Pattern counts overlap and are not independent probabilities or profit claims.
"""

import numpy as np
import pandas as pd


PATTERN_LABELS = {
    'hammer_hanging_man': '锤头 / 上吊线',
    'inverted_hammer_shooting_star': '倒锤头 / 流星线',
    'engulfing': '看涨 / 看跌吞没',
    'harami': '看涨 / 看跌孕线',
    'piercing_dark_cloud': '刺透 / 乌云盖顶（连续市场版）',
    'morning_evening_star': '早晨 / 黄昏之星（连续市场版）',
    'confirmed_doji': '十字星突破确认',
    'tweezer': '平头底 / 平头顶',
    'three_soldiers_crows': '红三兵 / 黑三鸦',
    'three_inside': '三内升 / 三内降',
    'three_outside': '三外升 / 三外降',
    'abandoned_baby': '看涨 / 看跌弃婴（严格缺口）',
}
PATTERN_NAMES = tuple(PATTERN_LABELS)
PATTERN_COLUMNS = tuple('pattern_' + name for name in PATTERN_NAMES)
PATTERN_SOURCES = (
    {'title': 'TA-Lib 官方形态目录', 'url': 'https://ta-lib.org/functions/'},
    {'title': 'StockCharts 官方反转形态与前序趋势说明',
     'url': 'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bullish-reversal-patterns'},
    {'title': 'TA-Lib 三内升降', 'url': 'https://ta-lib.org/functions/cdl3inside.html'},
    {'title': 'TA-Lib 三外升降', 'url': 'https://ta-lib.org/functions/cdl3outside.html'},
    {'title': 'TA-Lib 弃婴', 'url': 'https://ta-lib.org/functions/cdlabandonedbaby.html'},
)
SOURCES = PATTERN_SOURCES
PATTERN_DEFINITIONS = {
    'hammer_hanging_man': '实体为振幅5%-35%，下影至少为实体2倍，上影不超过振幅10%；前序下跌为锤头，前序上涨为上吊线。',
    'inverted_hammer_shooting_star': '实体为振幅5%-35%，上影至少为实体2倍，下影不超过振幅10%；前序下跌为倒锤头，前序上涨为流星线。',
    'engulfing': '相反颜色的第二根实体覆盖第一根非十字实体，至少一端严格超过。',
    'harami': '第一根为长实体，第二根相反颜色实体严格位于其内，长度不超过第一根50%。',
    'piercing_dark_cloud': '两根相反颜色长实体，第二根越过第一根实体中点但未完全吞没；开盘允许前序平均振幅10%的连续市场容差。',
    'morning_evening_star': '长实体、长度不超过首根40%的小实体、反向实体；末根至少为首根50%并越过首根中点，小实体允许10%平均振幅容差。',
    'confirmed_doji': '前根实体不超过振幅10%，本根以相应颜色收盘越过十字星高点或低点。',
    'tweezer': '相反颜色两根的低点或高点差不超过前序平均振幅10%，末根收盘越过前根实体中点。',
    'three_soldiers_crows': '连续三根同方向长实体，逐根收盘创新高或新低，后两根开于前根实体内，趋势方向末端影线不超过振幅25%。',
    'three_inside': '前两根构成孕线，第三根以反转方向颜色收盘超过首根开盘。',
    'three_outside': '前两根构成吞没，第三根以反转方向颜色收盘超过第二根收盘。',
    'abandoned_baby': '长实体、十字星、反向实体；十字星与左右两根全振幅严格隔空，末根越过首根中点并至少为首根实体50%；24小时市场罕见。',
}


def reversal_patterns(bars, trend_window=5):
    """基于已收盘K线识别12类带方向的反转形态。

    Args:
        bars: 按时间升序排列的OHLCV表，时间戳单位为毫秒。
        trend_window: 形态之前用于判断趋势的历史窗口根数。

    Returns:
        形态事件、看多/看空数量和候选方向的DataFrame。

    Notes:
        原有细则保留如下：
        Return twelve signed pattern events, counts, and a majority direction.

        ``candidate_direction`` is sign(bullish_count - bearish_count); ties abstain.
        Inputs must be chronological, closed, finite, positive OHLC candles. Time
        closure and higher-timeframe alignment remain the caller's responsibility.
        Warm-up candles return zero events. No centered windows or future shifts
        are used; appending candles cannot change previously returned events.
    """
    if not isinstance(trend_window, int) or isinstance(trend_window, bool) or trend_window < 3:
        raise ValueError('trend_window must be an integer of at least 3')
    missing = {'open', 'high', 'low', 'close'} - set(bars.columns)
    if missing:
        raise ValueError('missing OHLC columns: ' + ', '.join(sorted(missing)))
    o, h, l, c = (bars[col].astype(float) for col in ('open', 'high', 'low', 'close'))
    raw = bars[['open', 'high', 'low', 'close']].to_numpy(dtype=float)
    if (not np.isfinite(raw).all() or (raw <= 0).any()
            or (h < pd.concat([o, c], axis=1).max(axis=1)).any()
            or (l > pd.concat([o, c], axis=1).min(axis=1)).any()):
        raise ValueError('OHLC bars must be finite, positive, and internally consistent')

    body = (c - o).abs()
    span = h - l
    top, bottom = pd.concat([o, c], axis=1).max(axis=1), pd.concat([o, c], axis=1).min(axis=1)
    upper, lower = h - top, bottom - l
    bull, bear = c > o, c < o
    doji = (span > 0) & (body <= .1 * span)
    # All scale estimates end before the candle being classified.
    mean_range = span.shift(1).rolling(10, min_periods=trend_window).mean()
    mean_body = body.shift(1).rolling(10, min_periods=trend_window).mean()
    tolerance = .1 * mean_range
    substantial = (body >= .8 * mean_body) & (body >= .3 * span) & (body > 0)
    non_doji = (span > 0) & (body > .1 * span)

    def prior_trend(length):
        """读取形态开始之前的历史趋势，避免用形态自身判断前置趋势。

        Args:
            length: 当前形态覆盖的K线根数。

        Returns:
            与当前K线对齐的先前趋势序列。
        """
        prior = c.shift(length)
        start = c.shift(length + trend_window - 1)
        average = prior.rolling(trend_window, min_periods=trend_window).mean()
        return (prior < start) & (prior < average), (prior > start) & (prior > average)

    out = pd.DataFrame(index=bars.index)

    def put(name, bullish, bearish, length):
        """把当前形态的多空布尔条件与前置趋势合并写入结果列。

        Args:
            name: 模型/配置的注册名称。
            bullish: 当前形态的看多条件布尔序列。
            bearish: 当前形态的看空条件布尔序列。
            length: 当前形态覆盖的K线根数。

        Returns:
            无返回值；只更新外层函数的当前形态结果。
        """
        down, up = prior_trend(length)
        yes = (bullish & down).fillna(False)
        no = (bearish & up).fillna(False)
        out['pattern_' + name] = np.select([yes & ~no, no & ~yes], [1, -1], 0).astype('int8')

    lower_tail = ((span > 0) & (body >= .05 * span) & (body <= .35 * span)
                  & (lower >= 2 * body) & (upper <= .1 * span))
    upper_tail = ((span > 0) & (body >= .05 * span) & (body <= .35 * span)
                  & (upper >= 2 * body) & (lower <= .1 * span))
    put('hammer_hanging_man', lower_tail, lower_tail, 1)
    put('inverted_hammer_shooting_star', upper_tail, upper_tail, 1)

    engulf_bull = (bear.shift(1) & bull & non_doji.shift(1)
                   & (o <= c.shift(1)) & (c >= o.shift(1))
                   & ((o < c.shift(1)) | (c > o.shift(1))))
    engulf_bear = (bull.shift(1) & bear & non_doji.shift(1)
                   & (o >= c.shift(1)) & (c <= o.shift(1))
                   & ((o > c.shift(1)) | (c < o.shift(1))))
    put('engulfing', engulf_bull, engulf_bear, 2)

    contained = ((top < top.shift(1)) & (bottom > bottom.shift(1))
                 & (body <= .5 * body.shift(1)) & substantial.shift(1))
    harami_bull = bear.shift(1) & bull & contained
    harami_bear = bull.shift(1) & bear & contained
    put('harami', harami_bull, harami_bear, 2)

    previous_mid = (o.shift(1) + c.shift(1)) / 2
    piercing = (bear.shift(1) & substantial.shift(1) & bull & substantial
                & (o <= c.shift(1) + tolerance) & (c > previous_mid) & (c < o.shift(1)))
    cloud = (bull.shift(1) & substantial.shift(1) & bear & substantial
             & (o >= c.shift(1) - tolerance) & (c < previous_mid) & (c > o.shift(1)))
    put('piercing_dark_cloud', piercing, cloud, 2)

    small_middle = body.shift(1) <= .4 * body.shift(2)
    first_mid = (o.shift(2) + c.shift(2)) / 2
    morning = (bear.shift(2) & substantial.shift(2) & small_middle & bull
               & (body >= .5 * body.shift(2)) & (c > first_mid)
               & (top.shift(1) <= c.shift(2) + tolerance.shift(1)))
    evening = (bull.shift(2) & substantial.shift(2) & small_middle & bear
               & (body >= .5 * body.shift(2)) & (c < first_mid)
               & (bottom.shift(1) >= c.shift(2) - tolerance.shift(1)))
    put('morning_evening_star', morning, evening, 3)

    put('confirmed_doji', doji.shift(1) & bull & (c > h.shift(1)),
        doji.shift(1) & bear & (c < l.shift(1)), 2)
    put('tweezer', bear.shift(1) & bull & ((l - l.shift(1)).abs() <= tolerance)
        & (c > previous_mid),
        bull.shift(1) & bear & ((h - h.shift(1)).abs() <= tolerance)
        & (c < previous_mid), 2)

    opens_inside = ((o >= bottom.shift(1)) & (o <= top.shift(1)))
    strong_bull = bull & substantial & (upper <= .25 * span)
    strong_bear = bear & substantial & (lower <= .25 * span)
    soldiers = (strong_bull & strong_bull.shift(1) & strong_bull.shift(2)
                & opens_inside & opens_inside.shift(1)
                & (c > c.shift(1)) & (c.shift(1) > c.shift(2)))
    crows = (strong_bear & strong_bear.shift(1) & strong_bear.shift(2)
             & opens_inside & opens_inside.shift(1)
             & (c < c.shift(1)) & (c.shift(1) < c.shift(2)))
    put('three_soldiers_crows', soldiers, crows, 3)
    put('three_inside', harami_bull.shift(1) & bull & (c > o.shift(2)),
        harami_bear.shift(1) & bear & (c < o.shift(2)), 3)
    put('three_outside', engulf_bull.shift(1) & bull & (c > c.shift(1)),
        engulf_bear.shift(1) & bear & (c < c.shift(1)), 3)

    put('abandoned_baby', bear.shift(2) & substantial.shift(2) & doji.shift(1)
        & (h.shift(1) < l.shift(2)) & (l > h.shift(1)) & bull
        & (body >= .5 * body.shift(2)) & (c > first_mid),
        bull.shift(2) & substantial.shift(2) & doji.shift(1)
        & (l.shift(1) > h.shift(2)) & (h < l.shift(1)) & bear
        & (body >= .5 * body.shift(2)) & (c < first_mid), 3)

    out['bullish_count'] = (out[list(PATTERN_COLUMNS)] == 1).sum(axis=1).astype('int8')
    out['bearish_count'] = (out[list(PATTERN_COLUMNS)] == -1).sum(axis=1).astype('int8')
    out['candidate_direction'] = np.sign(out.bullish_count - out.bearish_count).astype('int8')
    return out


detect_patterns = reversal_patterns
