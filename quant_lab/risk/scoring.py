"""可解释入场质量分：模型分类支持与只使用已确认信息的结构约束。"""

import numpy as np
import pandas as pd


def score_entries(
    frame,
    probabilities,
    classes=(-1, 0, 1),
    min_probability=0.45,
    swing_min=0.02,
    swing_max=0.5,
):
    """按相同时间索引计算可解释研究评分，禁止把分类概率标为获利概率。

    Args:
        frame: 已收盘多周期特征表，标签列不参与任何计算。
        probabilities: 仅由模型历史特征产生的N×3分类概率矩阵。
        classes: 模型实际类别顺序，必须包含-1/0/1。
        min_probability: 方向类别最低支持门槛。
        swing_min: 过去完整窗口已观测波幅的下限。
        swing_max: 过去完整窗口已观测波幅的上限。

    Returns:
        分数、各分项、候选方向和拦截原因的逐行DataFrame。
    """
    p = np.asarray(probabilities, dtype=float)
    classes = list(classes)
    if (
        len(set(classes)) != 3
        or set(classes) != {-1, 0, 1}
        or p.shape != (len(frame), 3)
    ):
        raise ValueError("模型概率维度或类别契约错误")
    if (
        not np.isfinite(p).all()
        or (p < 0).any()
        or (p > 1).any()
        or not np.allclose(p.sum(axis=1), 1, atol=1e-5)
    ):
        raise ValueError("模型概率必须为有限、非负且逐行归一")
    if not 0 < min_probability < 1 or not 0 <= swing_min < swing_max:
        raise ValueError("评分门槛无效")
    positive = p[:, classes.index(1)]
    negative = p[:, classes.index(-1)]
    flat = p[:, classes.index(0)]
    side = np.where(positive >= negative, 1, -1)
    support = np.maximum(positive, negative)
    candidate = np.where(
        (support >= min_probability) & (support > flat) & (positive != negative),
        side,
        0,
    )
    rows = []
    for index, row in enumerate(frame.itertuples()):
        direction = int(side[index])
        higher = int(row.higher_trend)
        trend = int(row.analysis_trend)
        model_score = 50.0 * support[index]
        trend_score = 25.0 if higher == direction and trend != -direction else 0.0
        dow_score = (
            10.0
            if row.analysis_dow_event == direction
            or row.analysis_dow_state == direction
            else 0.0
        )
        wave_score = 10.0 if row.analysis_wave_bias == direction else 0.0
        pattern_score = (
            5.0
            if (
                row.bullish_count > row.bearish_count
                if direction == 1
                else row.bearish_count > row.bullish_count
            )
            else 0.0
        )
        reasons = []
        if not candidate[index]:
            reasons.append("模型当前选择观望或方向优势不足")
        if higher != direction or trend == -direction:
            reasons.append("大周期/确认结构不支持该方向")
        measured = float(row.swing_range_pct)
        if not np.isfinite(measured) or not swing_min <= measured <= swing_max:
            reasons.append("历史波幅未满足2%至50%研究范围")
        rows.append(
            dict(
                score=float(
                    model_score + trend_score + dow_score + wave_score + pattern_score
                ),
                direction=int(candidate[index]) if not reasons else 0,
                model_score=float(model_score),
                trend_score=trend_score,
                dow_score=dow_score,
                wave_score=wave_score,
                pattern_score=pattern_score,
                probability_long=float(positive[index]),
                probability_short=float(negative[index]),
                probability_neutral=float(flat[index]),
                blocked_reason="；".join(reasons),
                probabilities_calibrated=False,
            )
        )
    return pd.DataFrame(rows, index=frame.index)


def daily_volatility(bars, window=96):
    """用过去15分钟对数收益估计日尺度波动率，保留预热缺失值。

    Args:
        bars: 连续已收盘15分钟OHLCV，不接受插值小周期。
        window: 历史收益窗口根数，默认96根覆盖一天。

    Returns:
        与源行情索引对齐的日尺度波动率Series；未来K线不改变前缀。
    """
    if window < 2 or int(window) != window:
        raise ValueError("波动率窗口必须是至少2的整数")
    if len(bars) and not ((bars.close_time - bars.open_time + 1) == 900000).all():
        raise ValueError("日波动率只接受15分钟真实K线")
    if len(bars) and (
        not (bars.open_time.diff().dropna() == 900000).all()
        or not (bars.open_time % 900000 == 0).all()
    ):
        raise ValueError("波动率要求连续对齐的15分钟K线")
    if (
        not np.isfinite(bars.close.to_numpy(dtype=float)).all()
        or (bars.close <= 0).any()
    ):
        raise ValueError("价格必须为有限正值")
    return np.log(bars.close).diff().rolling(window, min_periods=window).std(
        ddof=1
    ) * np.sqrt(96.0)
