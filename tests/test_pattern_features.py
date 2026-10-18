"""Causality and execution-time contracts for the new short-timeframe features."""
import unittest

import numpy as np
import pandas as pd

from candlestick_patterns import PATTERN_COLUMNS
from pattern_features import (FEATURE_COLUMNS, MINUTES, aggregate_closed,
                              entry_signals, prepare_timeframe)


def source_candles(size=4096):
    # Start on a 16-hour boundary: all lower and upper research intervals align.
    """生成可复用的连续15分钟合成源行情。

    Args:
        size: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    largest_step = 16 * 60 * 60 * 1000
    start = (1609459200000 // largest_step) * largest_step
    i = np.arange(size)
    close = 100 + .007 * i + 1.8 * np.sin(i / 17) + .5 * np.cos(i / 7)
    opening = np.r_[close[0], close[:-1]] * (1 + .0004 * np.sin(i / 5))
    return pd.DataFrame({
        'open_time': start + i * 900000,
        'close_time': start + (i + 1) * 900000 - 1,
        'open': opening, 'high': np.maximum(opening, close) + .15,
        'low': np.minimum(opening, close) - .15, 'close': close,
        'volume': 100 + (i % 31) * 2.,
    })


class FakeClassifier:
    def __init__(self, classes, probabilities):
        """为测试替身保存指定类别顺序和固定概率。

        Args:
            classes: 当前测试/接口参数，含义见该函数的具体场景。
            probabilities: 当前测试/接口参数，含义见该函数的具体场景。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
        self.classes_ = np.asarray(classes)
        self.probabilities = np.asarray(probabilities, dtype=float)

    def predict_proba(self, features):
        """让测试替身按指定类别顺序返回固定概率，不训练模型。

        Args:
            features: 输入特征维度或测试中的特征表，取决于当前函数契约。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
        if len(features) != len(self.probabilities):
            raise AssertionError('Features/probabilities length mismatch')
        return self.probabilities


class PatternFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """准备可复用的确定性合成行情或小样本训练前置条件。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
        cls.bars = source_candles()
        cls.marks = cls.bars.copy()
        cls.marks[['open', 'high', 'low', 'close']] *= 1.0007

    def test_every_timeframe_aggregates_exact_ohlcv_and_mark_prices(self):
        """验证全部周期精确聚合成交和标记OHLCV。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        for timeframe, minutes in MINUTES.items():
            with self.subTest(timeframe=timeframe):
                frame = prepare_timeframe(self.bars, self.marks, timeframe)
                ratio = minutes // 15
                self.assertEqual(len(frame), len(self.bars) // ratio)
                for j in (0, 5, len(frame) - 1):
                    source = self.bars.iloc[j * ratio:(j + 1) * ratio]
                    actual = frame.iloc[j]
                    self.assertEqual(actual.open_time, source.open_time.iloc[0])
                    self.assertEqual(actual.close_time, source.close_time.iloc[-1])
                    self.assertEqual(actual.open, source.open.iloc[0])
                    self.assertEqual(actual.high, source.high.max())
                    self.assertEqual(actual.low, source.low.min())
                    self.assertEqual(actual.close, source.close.iloc[-1])
                    self.assertEqual(actual.volume, source.volume.sum())
                    for name in ('open', 'high', 'low', 'close'):
                        self.assertAlmostEqual(actual['mark_' + name], actual[name] * 1.0007)

    def test_higher_context_only_appears_at_its_close(self):
        """验证大周期上下文仅在其收盘时出现。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        for timeframe, minutes in MINUTES.items():
            with self.subTest(timeframe=timeframe):
                frame = prepare_timeframe(self.bars, self.marks, timeframe)
                upper = aggregate_closed(self.bars, minutes * 4)
                fast = upper.close.ewm(span=12, adjust=False, min_periods=48).mean()
                slow = upper.close.ewm(span=48, adjust=False, min_periods=48).mean()
                gaps = fast / slow - 1
                self.assertTrue(frame.higher_close_time.iloc[:3].isna().all())
                self.assertEqual(frame.higher_close_time.iloc[3], upper.close_time.iloc[0])
                known = frame.higher_close_time.notna()
                self.assertTrue((frame.loc[known, 'higher_close_time'] <= frame.loc[known, 'close_time']).all())
                # The newest upper context must remain fixed through its four children.
                for j in (0, 47, len(upper) - 2):
                    indices = slice(j * 4 + 3, min(j * 4 + 7, len(frame)))
                    self.assertTrue((frame.higher_close_time.iloc[indices] == upper.close_time.iloc[j]).all())
                    np.testing.assert_allclose(frame.higher_ema_gap.iloc[indices], gaps.iloc[j], equal_nan=True)
                # EMA warm-up remains neutral until upper bar 48 closes.
                self.assertTrue((frame.higher_trend.iloc[:191] == 0).all())
                self.assertEqual(frame.higher_trend.iloc[191], int(np.sign(gaps.iloc[47])))

    def test_features_are_prefix_invariant_even_with_incomplete_upper_bars(self):
        """验证存在不完整大周期边界时特征仍保持前缀不变。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        for timeframe in MINUTES:
            full = prepare_timeframe(self.bars, self.marks, timeframe)
            columns = [c for c in full if c not in ('label', 'label_end_time')]
            for stop in (311, 3191, 3479):
                with self.subTest(timeframe=timeframe, stop=stop):
                    prefix = prepare_timeframe(self.bars.iloc[:stop], self.marks.iloc[:stop], timeframe)
                    pd.testing.assert_frame_equal(full.loc[:len(prefix)-1, columns], prefix[columns])

    def test_feature_contract_excludes_labels_and_future_prices(self):
        """验证特征契约不含标签或未来价格列。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        self.assertEqual(len(FEATURE_COLUMNS), len(set(FEATURE_COLUMNS)))
        self.assertTrue(set(PATTERN_COLUMNS).issubset(FEATURE_COLUMNS))
        self.assertTrue({'bullish_count', 'bearish_count', 'higher_ema_gap'}.issubset(FEATURE_COLUMNS))
        forbidden = {'label', 'label_end_time', 'target', 'forward', 'future', 'next_open', 'higher_close_time'}
        self.assertFalse(forbidden.intersection(FEATURE_COLUMNS))
        self.assertFalse(any('future' in name or 'label' in name for name in FEATURE_COLUMNS))
        frame = prepare_timeframe(self.bars, self.marks, '15m')
        self.assertTrue(np.isfinite(frame[list(FEATURE_COLUMNS)].iloc[250:]).all().all())

    def test_costaware_label_uses_next_open_to_fourth_future_close(self):
        """验证成本标签采用下一开盘到指定未来收盘。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        fee, slip = .0007, .0003
        for timeframe in MINUTES:
            with self.subTest(timeframe=timeframe):
                frame = prepare_timeframe(self.bars, self.marks, timeframe, fee=fee, slip=slip)
                expected_return = (frame.close.shift(-4) / frame.open.shift(-1) - 1)
                hurdle = 2 * (fee + slip) + .25 * frame.atr_pct
                expected = np.where(expected_return > hurdle, 1,
                                    np.where(expected_return < -hurdle, -1, 0))
                np.testing.assert_array_equal(frame.label, expected)
                pd.testing.assert_series_equal(frame.label_end_time, frame.close_time.shift(-4),
                                               check_names=False)
                self.assertTrue(frame.label_end_time.iloc[-4:].isna().all())
                self.assertTrue(frame.label_end_time.iloc[:-4].notna().all())
                # A training cut removes all observations whose label reaches the cut.
                cut = frame.close_time.iloc[100]
                train = frame[(frame.close_time < cut) & (frame.label_end_time < cut)]
                self.assertTrue((train.label_end_time < cut).all())
                self.assertEqual(train.index[-1], 95)

    def test_entry_requires_pattern_and_matching_higher_trend(self):
        # Includes bullish/bearish candidates, opposing higher trend, abstention,
        # and inconsistent external metadata without an actual signed pattern.
        """验证入场同时需要反转形态与同向大周期。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        frame = pd.DataFrame(0., index=range(8), columns=FEATURE_COLUMNS)
        frame['candidate_direction'] = [1, -1, 1, -1, 0, 1, 1, -1]
        frame['higher_trend'] = [1, -1, -1, 1, 1, 1, 1, -1]
        frame['bullish_count'] = [1, 0, 1, 0, 0, 0, 1, 1]
        frame['bearish_count'] = [0, 1, 0, 1, 0, 0, 0, 1]
        frame.loc[[0, 2], PATTERN_COLUMNS[2]] = 1
        frame.loc[[1, 3, 7], PATTERN_COLUMNS[2]] = -1
        frame.loc[7, PATTERN_COLUMNS[3]] = 1
        # Row 5: no count/pattern. Row 6: positive count but no actual pattern.
        expected = [1, -1, 0, 0, 0, 0, 0, 0]
        signals = entry_signals(frame)
        np.testing.assert_array_equal(signals.direction, expected)
        self.assertEqual(signals.pattern.iloc[0], 'engulfing')
        self.assertEqual(signals.pattern.iloc[1], 'engulfing')
        self.assertTrue((signals.pattern.iloc[2:] == '').all())

    def test_model_probability_gate_handles_class_order_and_absent_class(self):
        """验证模型类别顺序及缺类概率不会错位。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        frame = pd.DataFrame(0., index=range(5), columns=FEATURE_COLUMNS)
        frame['candidate_direction'] = [1, -1, 1, -1, 1]
        frame['higher_trend'] = frame.candidate_direction
        frame['bullish_count'] = [1, 0, 1, 0, 1]
        frame['bearish_count'] = [0, 1, 0, 1, 0]
        frame[PATTERN_COLUMNS[2]] = frame.candidate_direction
        # Deliberately shuffled classes: [bullish, neutral, bearish].
        model = FakeClassifier([1, 0, -1], [[.7, .2, .1], [.1, .2, .7],
                                          [.2, .1, .7], [.2, .5, .3], [.5, .1, .4]])
        np.testing.assert_array_equal(entry_signals(frame, model, confidence=.6).direction,
                                      [1, -1, 0, 0, 0])
        absent_bullish = FakeClassifier([-1, 0], [[.2, .8], [.8, .2], [.2, .8], [.8, .2], [.2, .8]])
        np.testing.assert_array_equal(entry_signals(frame, absent_bullish, confidence=.6).direction,
                                      [0, -1, 0, -1, 0])

    def test_source_alignment_and_incomplete_bucket_validation(self):
        """验证成交/标记源对齐并排除不完整聚合桶。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        incomplete = self.bars.iloc[:19]
        self.assertEqual(len(aggregate_closed(incomplete, 60)), 4)
        self.assertEqual(len(aggregate_closed(incomplete, 240)), 1)
        with self.assertRaises(ValueError):
            aggregate_closed(self.bars.drop(index=100), 60)
        shifted = self.marks.copy()
        shifted.loc[100, 'close_time'] -= 1
        with self.assertRaises(ValueError):
            prepare_timeframe(self.bars, shifted, '15m')
        with self.assertRaises(ValueError):
            prepare_timeframe(self.bars, self.marks, '5m')


if __name__ == '__main__':
    unittest.main()
