import unittest

import numpy as np
import pandas as pd

from candlestick_patterns import PATTERN_COLUMNS, PATTERN_NAMES, reversal_patterns


def prefix():
    """构造形态前置趋势的合成K线前缀。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    close = 120. - np.arange(20)
    return pd.DataFrame({'open': close + .4, 'high': close + .7,
                         'low': close - .3, 'close': close})


def fixture(rows):
    """构造当前报告/行情测试所需的独立临时输入和冻结边界。

    Args:
        rows: 原始K线/记录列表；字段契约由当前领域函数校验。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    return pd.concat([prefix(), pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'])],
                     ignore_index=True)


def mirror(bars):
    """对价格做镜像以验证多空规则对称性。

    Args:
        bars: 按时间升序排列的OHLCV表，时间戳单位为毫秒。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    out = bars.copy()
    out['open'] = 250 - bars.open
    out['close'] = 250 - bars.close
    out['high'] = 250 - bars.low
    out['low'] = 250 - bars.high
    return out


class PatternTests(unittest.TestCase):
    def test_all_twelve_bullish_and_bearish_families(self):
        """验证12类反转形态的多空镜像样本。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        cases = {
            'hammer_hanging_man': [(100, 100.55, 98.5, 100.5)],
            'inverted_hammer_shooting_star': [(100, 102, 99.95, 100.5)],
            'engulfing': [(101, 101.2, 98.8, 99), (98.9, 101.4, 98.7, 101.2)],
            'harami': [(101, 101.2, 98.8, 99), (99.4, 100., 99.2, 99.8)],
            'piercing_dark_cloud': [(101, 101.2, 98.8, 99), (99, 100.6, 98.8, 100.4)],
            'morning_evening_star': [(102, 102.2, 98.8, 99), (98.9, 99.1, 98.7, 99),
                                    (99, 101.5, 98.9, 101.3)],
            'confirmed_doji': [(99, 100, 98, 99.05), (99.1, 100.6, 99, 100.4)],
            'tweezer': [(101, 101.2, 98.8, 99), (99, 101, 98.85, 100.8)],
            'three_soldiers_crows': [(99, 100.6, 98.8, 100.5),
                                     (100.2, 101.8, 100, 101.7),
                                     (101.4, 103, 101.2, 102.9)],
            'three_inside': [(101, 101.2, 98.8, 99), (99.4, 100, 99.2, 99.8),
                             (99.8, 101.5, 99.7, 101.3)],
            'three_outside': [(101, 101.2, 98.8, 99), (98.9, 101.4, 98.7, 101.2),
                              (101.2, 102.3, 101.1, 102.1)],
            'abandoned_baby': [(102, 102.2, 98.8, 99), (98.3, 98.5, 98.1, 98.31),
                               (99, 101.5, 98.9, 101.3)],
        }
        self.assertEqual(tuple(cases), PATTERN_NAMES)
        for name, rows in cases.items():
            with self.subTest(name=name):
                bars = fixture(rows)
                self.assertEqual(reversal_patterns(bars)['pattern_' + name].iloc[-1], 1)
                # Reflection exchanges lower-tail and upper-tail families.
                inverse = {'hammer_hanging_man': 'inverted_hammer_shooting_star',
                           'inverted_hammer_shooting_star': 'hammer_hanging_man'}.get(name, name)
                self.assertEqual(reversal_patterns(mirror(bars))['pattern_' + inverse].iloc[-1], -1)
        # Identical shapes have different meaning in an opposite preceding trend.
        lower_tail_up = pd.concat([mirror(prefix()),
                                   pd.DataFrame([(150, 150.55, 148.5, 150.5)], columns=prefix().columns)],
                                  ignore_index=True)
        self.assertEqual(reversal_patterns(lower_tail_up).pattern_hammer_hanging_man.iloc[-1], -1)

    def test_causal_prefix_and_future_perturbation(self):
        """验证追加或扰动未来K线不改变过去形态。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        rng = np.random.default_rng(123)
        close = np.exp(np.cumsum(rng.normal(0, .01, 180))) * 100
        opens = np.r_[close[0], close[:-1]]
        bars = pd.DataFrame({'open': opens, 'high': np.maximum(opens, close) + .3,
                             'low': np.minimum(opens, close) - .3, 'close': close})
        full = reversal_patterns(bars)
        for stop in (1, 9, 30, 73, 120, 179):
            pd.testing.assert_frame_equal(full.iloc[:stop], reversal_patterns(bars.iloc[:stop]))
        changed = bars.copy()
        changed.iloc[90:] *= 1.5
        pd.testing.assert_frame_equal(full.iloc[:90], reversal_patterns(changed).iloc[:90])

    def test_pattern_requires_a_preceding_trend(self):
        """验证反转形态需要此前已知的前置趋势。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        bars = fixture([(101, 101.2, 98.8, 99), (98.9, 101.4, 98.7, 101.2)])
        bars.loc[:19, 'close'] = 101
        bars.loc[:19, 'open'] = 101.4
        bars.loc[:19, 'high'] = 101.7
        bars.loc[:19, 'low'] = 100.7
        self.assertEqual(reversal_patterns(bars).pattern_engulfing.iloc[-1], 0)

    def test_counts_and_abstention(self):
        """验证形态投票计数及不满足条件时观望。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        result = reversal_patterns(fixture([(101, 101.2, 98.8, 99), (98.9, 101.4, 98.7, 101.2)]))
        events = result[list(PATTERN_COLUMNS)]
        pd.testing.assert_series_equal(result.bullish_count, (events == 1).sum(axis=1).astype('int8'),
                                       check_names=False)
        pd.testing.assert_series_equal(result.bearish_count, (events == -1).sum(axis=1).astype('int8'),
                                       check_names=False)
        np.testing.assert_array_equal(result.candidate_direction,
                                      np.sign(result.bullish_count - result.bearish_count))
        self.assertEqual(result.candidate_direction.iloc[0], 0)
        self.assertEqual(result.candidate_direction.iloc[-1], 1)

    def test_abandoned_baby_needs_strict_gaps(self):
        """验证弃婴形态要求严格价格跳空。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        bars = fixture([(102, 102.2, 98.8, 99), (98.3, 98.5, 98.1, 98.31),
                        (99, 101.5, 98.9, 101.3)])
        bars.loc[21, 'high'] = 98.8
        self.assertEqual(reversal_patterns(bars).pattern_abandoned_baby.iloc[-1], 0)

    def test_invalid_and_empty_input(self):
        """验证非法及空行情输入得到明确处理。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        for field, value in [('close', np.nan), ('high', 1), ('low', -1)]:
            with self.subTest(field=field):
                bars = prefix()
                bars.loc[5, field] = value
                with self.assertRaises(ValueError):
                    reversal_patterns(bars)
        with self.assertRaises(ValueError):
            reversal_patterns(prefix(), trend_window=2)
        with self.assertRaises(ValueError):
            reversal_patterns(prefix().drop(columns='open'))
        self.assertEqual(reversal_patterns(prefix().iloc[:0]).shape, (0, 15))
        flat = pd.DataFrame({k: [100.] * 30 for k in ['open', 'high', 'low', 'close']})
        self.assertEqual(reversal_patterns(flat).to_numpy().sum(), 0)


if __name__ == '__main__':
    unittest.main()
