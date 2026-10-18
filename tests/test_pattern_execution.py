import unittest

import numpy as np
import pandas as pd

from pattern_execution import EXIT_MODES, simulate_patterns


def candles(rows=None, count=5, interval=900_000, atr=2.):
    """构造当前技术分析或执行测试所需的合成OHLCV样本。

    Args:
        rows: 原始K线/记录列表；字段契约由当前领域函数校验。
        count: 当前测试/接口参数，含义见该函数的具体场景。
        interval: 精确K线周期名称，月线1M区别于分钟。
        atr: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    rows = rows or [(100., 101., 99., 100.)] * count
    data = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'])
    data['open_time'] = np.arange(len(data)) * interval
    data['close_time'] = data.open_time + interval - 1
    for name in ('open', 'high', 'low', 'close'):
        data['mark_' + name] = data[name]
    data['atr'] = atr
    return data


def simulate(data, signals, **kwargs):
    """为执行测试调用原模拟引擎并统一测试成本/资金费参数。

    Args:
        data: 当前函数契约下的时间顺序行情/特征表。
        signals: 与信号K线对齐的-1/0/1方向及可选形态表。
        **kwargs: 透传的命名参数；不能绕过当前调用的校验。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    return simulate_patterns(data, signals, fee=0., slip=0., **kwargs)


class PatternExecutionTests(unittest.TestCase):
    def test_signal_only_enters_at_next_open_and_last_signal_cannot_enter(self):
        """验证收盘信号最早下一开盘成交，最后一根信号不能入场。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 101, 99, 100), (110, 111, 109, 110), (112, 113, 111, 112)])
        result = simulate(data, [1, 0, 1])
        trade = result['trades'].iloc[0]
        self.assertEqual(result['metrics']['entries'], 1)
        self.assertEqual(trade.entry_price, 110)
        self.assertEqual(trade.entry_time, 900_000)
        self.assertEqual(trade.signal_time, 899_999)
        self.assertAlmostEqual(result['metrics']['net_return'], .5 * (112/110-1))
        self.assertTrue(simulate(data, [0, 0, 1])['trades'].empty)

    def test_zero_is_no_new_signal_and_opposite_does_not_reverse_same_bar(self):
        """验证零信号不新开仓，反向退出不在同根立即反手。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles()
        result = simulate(data, [1, 0, -1, 0, 0], exit_mode='time_exit')
        trade = result['trades'].iloc[0]
        self.assertEqual(result['metrics']['entries'], 1)
        self.assertEqual(trade.exit_reason, 'OPPOSITE_SIGNAL')
        self.assertEqual(trade.exit_time, 2_700_000)
        self.assertEqual(trade.holding_bars, 2)
        self.assertEqual(simulate(data, [1, 0, 0, 0, 0])['trades'].iloc[0].exit_reason, 'FINAL_EXIT')

    def test_completed_trend_reversal_waits_for_next_open(self):
        """验证已收盘大周期反转等待下一实际开盘退出。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles()
        data['higher_trend'] = [1, 1, -1, -1, -1]
        trade = simulate(data, [1, 0, 0, 0, 0])['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'HIGHER_TREND_REVERSAL')
        self.assertEqual(trade.exit_time, 2_700_000)

    def test_stop_has_priority_when_same_bar_also_touches_rr_target(self):
        """验证同根同时触及止损止盈时采用保守止损优先。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 101, 99, 100), (100, 107, 96, 103), (103, 104, 102, 103)])
        trade = simulate(data, [1, 0, 0])['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'STOP_LOSS')
        self.assertEqual(trade.exit_price, 97)

    def test_gap_stop_uses_open_and_no_same_bar_reentry(self):
        """验证跳空止损按实际开盘且同根不重新入场。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 101, 99, 100), (100, 101, 99, 100), (95, 96, 94, 95)])
        result = simulate(data, [1, 1, 0])
        self.assertEqual(result['metrics']['entries'], 1)
        trade = result['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'STOP_LOSS_GAP')
        self.assertEqual(trade.exit_price, 95)

    def test_mark_liquidation_precedes_trade_stop_and_caps_loss(self):
        """验证标记强平先于成交价止损且逐仓亏损封顶。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 101, 99, 100), (100, 107, 96, 100), (100, 101, 99, 100)])
        data.loc[1, 'mark_low'] = 80
        result = simulate(data, [1, 0, 0], leverage=10.)
        self.assertEqual(result['metrics']['liquidations'], 1)
        self.assertEqual(result['trades'].iloc[0].exit_reason, 'LIQUIDATION_INTRABAR')
        self.assertAlmostEqual(result['metrics']['net_return'], -.1)
        self.assertTrue((result['equity'].equity >= .9-1e-12).all())

    def test_new_entry_mark_gap_cannot_fill_at_better_liquidation_threshold(self):
        """验证入场标记跳空不获得虚构的有利强平价格。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles(count=2)
        for name, value in [('mark_open', 80), ('mark_high', 81), ('mark_low', 79), ('mark_close', 80)]:
            data.loc[1, name] = value
        trade = simulate(data, [1, 0], leverage=10.)['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'LIQUIDATION_OPEN_GAP')
        self.assertEqual(trade.exit_price, 80)

    def test_rr_long_and_short(self):
        """验证固定盈亏比模式对多空方向均正确。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        for direction, row, target in [(1, (100, 107, 99, 105), 106), (-1, (100, 101, 93, 95), 94)]:
            data = candles([(100, 101, 99, 100), row])
            trade = simulate(data, [direction, 0])['trades'].iloc[0]
            self.assertEqual(trade.exit_reason, 'TAKE_PROFIT')
            self.assertEqual(trade.exit_price, target)
            self.assertAlmostEqual(trade.net_pnl, .03)

    def test_atr_trailing_only_advances_after_close(self):
        """验证ATR跟踪止损只在收盘后推进。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 101, 99, 100), (100, 110, 99, 108), (108, 109, 105, 107)])
        trade = simulate(data, [1, 0, 0], exit_mode='atr_trailing')['trades'].iloc[0]
        self.assertEqual(trade.exit_bar, 2)
        self.assertEqual(trade.exit_reason, 'ATR_TRAILING_STOP')
        self.assertEqual(trade.exit_price, 106)

    def test_dynamic_three_high_target_uses_only_previous_three_candles(self):
        """验证动态三根高点目标不使用当前尚未收盘K线。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 108, 99, 100), (100, 107, 99, 100),
                        (100, 106, 99, 100), (100, 109, 99, 108)])
        trade = simulate(data, [0, 0, 1, 0], exit_mode='three_bar_breakout')['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'TAKE_PROFIT')
        self.assertEqual(trade.exit_price, 108)

    def test_three_high_target_below_entry_is_never_fictitious_profit(self):
        """验证低于入场价的三根高点不被误记为盈利。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(98, 99, 97, 98)] * 3 + [(110, 112, 109, 111), (111, 113, 110, 112)])
        for mode in ('three_bar_breakout', 'fixed_three_bar_target'):
            trade = simulate(data, [0, 0, 1, 0, 0], exit_mode=mode)['trades'].iloc[0]
            self.assertGreaterEqual(trade.exit_price, 110)
            self.assertNotEqual(trade.exit_time, data.close_time.iloc[3])

    def test_three_bar_trailing_gap_does_not_create_favorable_target_fill(self):
        # A gap below a profitable trailing level cannot later be filled at that
        # better level just because the candle's high rebounded through it.
        """验证三根跟踪止盈跳空不伪造有利成交。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(110, 112, 105, 110)] * 3 + [(100, 109, 99, 104)])
        trade = simulate(data, [0, 0, 1, 0], exit_mode='three_bar_trailing')['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'FINAL_EXIT')
        self.assertEqual(trade.exit_price, 104)

    def test_three_bar_trailing_takes_profitable_breakdown(self):
        """验证盈利条件满足时可按此前三根低点破位退出。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 101, 99, 100), (100, 104, 99, 103),
                        (104, 109, 103, 108), (108, 112, 107, 111),
                        (111, 114, 110, 113), (113, 114, 102, 108)])
        trade = simulate(data, [1, 0, 0, 0, 0, 0], exit_mode='three_bar_trailing')['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'THREE_BAR_TRAILING_PROFIT')
        self.assertEqual(trade.exit_bar, 5)
        self.assertEqual(trade.exit_price, 103)

    def test_three_bar_take_profit_must_cover_fee_and_funding(self):
        """验证三根K线获利退出覆盖手续费及资金费。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles([(100, 100.05, 99, 100)] * 3 + [(100, 100.1, 99, 100)])
        result = simulate_patterns(data, [0, 0, 1, 0], exit_mode='three_bar_breakout', fee=.001, slip=0.)
        self.assertEqual(result['trades'].iloc[0].exit_reason, 'FINAL_EXIT')
        # The same apparent target is invalid after a large funding debit.
        data = candles([(100, 101, 99, 100)] * 3 + [(100, 102, 99, 100)])
        funding = pd.DataFrame([dict(funding_time=3*900_000+1, rate=.02, mark_price=100)])
        trade = simulate(data, [0, 0, 1, 0], exit_mode='three_bar_breakout', funding_events=funding)['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'FINAL_EXIT')
        self.assertGreater(trade.funding_paid, 0)

    def test_funding_boundaries_exclude_new_open_include_close_and_incumbent_open(self):
        """验证资金费边界包含原持仓但不错误收费给新入场。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles(count=3)
        funding = pd.DataFrame([dict(funding_time=t, rate=.01, mark_price=100)
                                for t in (900_000, 1_799_999, 1_800_000)])
        result = simulate(data, [1, -1, 0], funding_events=funding)
        trade = result['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'OPPOSITE_SIGNAL')
        self.assertAlmostEqual(trade.funding_paid, .01)
        charged = result['events'].query("event == 'FUNDING'")
        self.assertEqual(charged.time.tolist(), [1_799_999, 1_800_000])
        self.assertAlmostEqual(result['metrics']['net_return'], -.01)

    def test_funding_short_credit_and_explicit_mark_price(self):
        """验证资金费使用显式事件标记价及空头收付符号。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles(count=2)
        funding = pd.DataFrame([dict(fundingTime=900_001, fundingRate=.01, markPrice=110)])
        result = simulate(data, [-1, 0], funding_events=funding)
        self.assertAlmostEqual(result['metrics']['net_return'], .0055)
        event = result['events'].query("event == 'FUNDING'").iloc[0]
        self.assertEqual(event.funding_price_source, 'event_mark')

    def test_time_exit_counts_complete_holding_bars(self):
        """验证时间退出按实际完整持仓根数计算。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles(count=16)
        result = simulate(data, [1]+[0]*15, exit_mode='time_exit', max_holding_bars=12)
        trade = result['trades'].iloc[0]
        self.assertEqual(trade.exit_bar, 13)
        self.assertEqual(trade.holding_bars, 12)
        self.assertEqual(trade.exit_reason, 'TIME_EXIT')

    def test_four_hour_bars_preserve_execution_timing(self):
        """验证4小时源K线的成交时序不漂移。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles(count=4, interval=14_400_000)
        trade = simulate(data, [1, 0, 0, 0], exit_mode='time_exit', max_holding_bars=2)['trades'].iloc[0]
        self.assertEqual(trade.entry_time, 14_400_000)
        self.assertEqual(trade.exit_time, 43_200_000)
        self.assertEqual(trade.holding_bars, 2)

    def test_ledger_reconciles_equity_fees_funding_across_exit_modes(self):
        """验证各退出方式的权益、费用、资金费与账本守恒。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles(count=9)
        funding = pd.DataFrame([dict(funding_time=2*900_000+10, rate=.001, mark_price=100)])
        for mode in EXIT_MODES:
            result = simulate_patterns(data, [1, 0, -1, 0, 1, 0, 0, 0, 0], exit_mode=mode,
                                       funding_events=funding)
            trades = result['trades']
            self.assertAlmostEqual(result['metrics']['net_return'], trades.net_pnl.sum())
            self.assertAlmostEqual(result['metrics']['fees_equity_fraction'], trades.total_fee.sum())
            self.assertAlmostEqual(result['metrics']['funding_equity_fraction'], trades.funding_paid.sum())
            self.assertAlmostEqual(result['equity'].equity.iloc[-1], trades.account_equity_after.iloc[-1])

    def test_invalid_inputs_and_nan_warmup_atr(self):
        """验证非法行情拒绝且ATR预热缺失不伪造入场。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        data = candles(count=3)
        data.loc[0, 'atr'] = np.nan
        self.assertEqual(simulate(data, [1, 0, 0])['metrics']['entries'], 0)
        for kwargs in [dict(leverage=0), dict(atr_stop=0), dict(max_holding_bars=1.5), dict(exit_mode='bad')]:
            with self.assertRaises(ValueError):
                simulate(data, [0, 0, 0], **kwargs)
        data.loc[1, 'open_time'] += 1
        with self.assertRaises(ValueError):
            simulate(data, [0, 0, 0])


class HoldingPolicyTests(unittest.TestCase):
    hour = 3_600_000
    day = 86_400_000

    def test_manual_delay_replays_four_hour_signal_on_real_fifteen_minute_open(self):
        """验证4小时信号按人工延迟在真实15分钟开盘回放。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=3, interval=4*self.hour)
        execution = candles(count=48)
        execution.loc[17, ['open', 'close', 'mark_open', 'mark_close']] = 100.5
        result = simulate(source, [1, 0, 0], execution_data=execution,
                          execution_delay_ms=900_000)
        trade = result['trades'].iloc[0]
        self.assertEqual(trade.signal_time, 4*self.hour-1)
        self.assertEqual(trade.entry_time, 4*self.hour+900_000)
        self.assertEqual(trade.entry_price, 100.5)
        self.assertEqual(result['metrics']['source_interval_ms'], 4*self.hour)
        self.assertEqual(result['metrics']['execution_interval_ms'], 900_000)

    def test_without_fine_prices_delay_waits_for_observed_source_open(self):
        """验证缺细周期价格时只等待真实源周期开盘。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=4, interval=4*self.hour)
        trade = simulate(source, [1, 0, 0, 0], execution_delay_ms=900_000)['trades'].iloc[0]
        self.assertEqual(trade.entry_time, 8*self.hour)

    def test_delayed_source_signal_is_consumed_once_after_fast_exit(self):
        """验证延迟源信号只消费一次，不在快速退出后重复入场。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=3, interval=4*self.hour)
        execution = candles(count=48)
        execution.loc[17, ['high', 'mark_high']] = 107
        result = simulate(source, [1, 0, 0], execution_data=execution,
                          execution_delay_ms=900_000)
        self.assertEqual(result['metrics']['entries'], 1)
        self.assertEqual(result['trades'].iloc[0].exit_reason, 'TAKE_PROFIT')

    def test_opposite_signal_waits_for_four_hours_even_after_signal_becomes_zero(self):
        """验证反向退出意图在最低持仓期间保留，不因零信号丢失。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=28)
        trade = simulate(source, [1, -1]+[0]*26, min_holding_ms=4*self.hour)['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'OPPOSITE_SIGNAL')
        self.assertEqual(trade.exit_time-trade.entry_time, 4*self.hour)
        self.assertEqual(trade.holding_hours, 4.)

    def test_profit_target_is_inactive_before_four_hours(self):
        """验证最低持仓时间前不会自愿止盈。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles([(100, 107, 99, 100)]*24)
        trade = simulate(source, [1]+[0]*23, min_holding_ms=4*self.hour)['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'TAKE_PROFIT')
        self.assertGreaterEqual(trade.holding_ms, 4*self.hour)
        self.assertEqual(trade.exit_time, source.close_time.iloc[17])

    def test_hard_stop_and_liquidation_can_exit_before_minimum(self):
        """验证硬止损和强平始终可提前退出。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        for liquidation in (False, True):
            source = candles(count=24)
            source.loc[1, 'mark_low' if liquidation else 'low'] = 70 if liquidation else 96
            result = simulate(source, [1]+[0]*23, min_holding_ms=4*self.hour)
            trade = result['trades'].iloc[0]
            self.assertLess(trade.holding_hours, 4.)
            self.assertTrue(trade.exit_reason.startswith('LIQUIDATION' if liquidation else 'STOP_LOSS'))
            self.assertEqual(result['metrics']['short_holding_risk_exits'], 1)

    def test_maximum_duration_closes_at_seven_days_on_every_exit_policy(self):
        """验证各退出模式均遵守传入的最长持仓时长。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=7*96+8)
        for mode in ('fixed_rr', 'time_exit'):
            result = simulate(source, [1]+[0]*(len(source)-1), exit_mode=mode,
                              max_holding_bars=10000, min_holding_ms=4*self.hour,
                              max_holding_ms=7*self.day)
            trade = result['trades'].iloc[0]
            self.assertEqual(trade.exit_reason, 'MAX_HOLDING_TIME')
            self.assertEqual(trade.holding_ms, 7*self.day)
            self.assertEqual(trade.holding_hours, 168.)

    def test_policy_time_exit_uses_source_duration_when_execution_is_finer(self):
        """验证细周期执行下时间退出仍按源周期策略时长计算。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=5, interval=self.hour)
        execution = candles(count=20)
        trade = simulate(source, [1]+[0]*4, execution_data=execution,
                         exit_mode='time_exit', max_holding_bars=2)['trades'].iloc[0]
        self.assertEqual(trade.holding_ms, 2*self.hour)
        self.assertEqual(trade.exit_time, 3*self.hour)

    def test_partial_exit_quantities_funding_fees_and_whole_position_reconcile(self):
        """验证分批退出数量、保证金和所有费用与完整仓位守恒。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=7*96+8)
        first = 72*4+1
        source.loc[first:, ['open', 'close', 'mark_open', 'mark_close']] = 106.
        source.loc[first:, ['high', 'mark_high']] = 107.
        source.loc[first:, ['low', 'mark_low']] = 105.
        funding = pd.DataFrame([dict(funding_time=source.open_time.iloc[index],
                                     rate=.001, mark_price=106.)
                                for index in (first, first+96)])
        result = simulate_patterns(source, [1]+[0]*(len(source)-1),
            exit_mode='time_exit', max_holding_bars=10000, slip=0., fee=.0005,
            min_holding_ms=4*self.hour, max_holding_ms=7*self.day,
            scale_out_after_ms=3*self.day, funding_events=funding)
        trades, events = result['trades'], result['events']
        self.assertEqual(len(trades), 1)
        trade = trades.iloc[0]
        partials = events.query("event == 'PARTIAL_EXIT'")
        self.assertEqual(trade.partial_exit_count, 3)
        self.assertEqual(trade.exit_reason, 'TIME_SCALE_OUT_COMPLETE_FAVORABLE')
        self.assertEqual(partials.time.tolist(), [source.open_time.iloc[first+offset] for offset in (0, 96, 192)])
        np.testing.assert_allclose(partials.quantity, .005/4)
        self.assertAlmostEqual(trade.final_exit_quantity, .005/4)
        charged = events.query("event == 'FUNDING'")
        np.testing.assert_allclose(charged.quantity, [.005, .00375])
        self.assertAlmostEqual(trade.gross_pnl, .03)
        self.assertAlmostEqual(trade.net_pnl, trade.gross_pnl-trade.total_fee-trade.funding_paid)
        self.assertAlmostEqual(trade.total_fee, events.fee.sum())
        self.assertAlmostEqual(trade.net_pnl, events.net_pnl.sum())
        self.assertAlmostEqual(trade.funding_paid, events.funding_paid.sum())
        self.assertAlmostEqual(trade.entry_fee, events.entry_fee.sum())
        exits = events.loc[events.net_pnl.notna()]
        np.testing.assert_allclose(exits.net_pnl,
            exits.gross_pnl-exits.fee-exits.entry_fee-exits.funding_paid,
            atol=1e-15)
        self.assertAlmostEqual(result['metrics']['net_return'], trades.net_pnl.sum())
        self.assertAlmostEqual(result['metrics']['fees_equity_fraction'], trades.total_fee.sum())
        self.assertAlmostEqual(result['metrics']['funding_equity_fraction'], trades.funding_paid.sum())
        self.assertAlmostEqual(trade.account_equity_after, result['equity'].equity.iloc[-1])

    def test_adverse_displacement_can_reduce_but_gap_stop_has_priority(self):
        """验证不利偏离可减仓但跳空止损仍优先。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=100*4)
        first = 72*4+1
        for column, value in [('open', 94), ('high', 95), ('low', 93), ('close', 94)]:
            source.loc[first:, [column, 'mark_'+column]] = value
        kwargs = dict(exit_mode='time_exit', max_holding_bars=10000,
                      min_holding_ms=4*self.hour, scale_out_after_ms=3*self.day)
        result = simulate(source, [1]+[0]*(len(source)-1), atr_stop=10., **kwargs)
        partials = result['events'].query("event == 'PARTIAL_EXIT'")
        self.assertEqual(len(partials), 2)
        self.assertTrue((partials.reason == 'TIME_DEVIATION_ADVERSE').all())
        stopped = simulate(source, [1]+[0]*(len(source)-1), **kwargs)
        self.assertEqual(stopped['trades'].iloc[0].exit_reason, 'STOP_LOSS_GAP')
        self.assertEqual(stopped['metrics']['partial_exit_count'], 0)

    def test_delayed_entry_is_cancelled_if_latest_completed_trend_reverses(self):
        """验证延迟成交前已知大周期反转会取消旧信号。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=8)
        source['higher_trend'] = [1]+[-1]*7
        result = simulate(source, [1]+[0]*7, execution_delay_ms=900_000)
        self.assertEqual(result['metrics']['entries'], 0)
        self.assertEqual(result['events'].iloc[0].event, 'ENTRY_CANCELLED_TREND')

    def test_delayed_entry_preserves_atr_from_its_actual_decision(self):
        """验证延迟入场使用原决策时已知ATR。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=8)
        source.loc[1:, 'atr'] = 50.
        trade = simulate(source, [1]+[0]*7, execution_delay_ms=900_000)['trades'].iloc[0]
        self.assertEqual(trade.initial_stop, 97.)
        self.assertEqual(trade.signal_time, 899_999)

    def test_source_three_bar_targets_do_not_use_unclosed_source_candle(self):
        """验证源周期三根目标不读取尚未收盘源K线。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles([(100, high, 99, 100) for high in (108, 107, 106, 200)], interval=4*self.hour)
        execution = candles(count=64)
        execution.loc[49, ['high', 'mark_high']] = 109.
        trade = simulate(source, [0, 0, 1, 0], execution_data=execution,
                         execution_delay_ms=900_000,
                         exit_mode='three_bar_breakout')['trades'].iloc[0]
        self.assertEqual(trade.exit_price, 108.)
        self.assertEqual(trade.entry_time, 12*self.hour+900_000)

    def test_late_new_entries_are_skipped_and_dataset_cutoff_is_separate(self):
        """验证临近样本末尾不开不足最低时长的新仓且区分强制截断。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=24)
        self.assertTrue(simulate(source, [0]*10+[1]+[0]*13,
                                 min_holding_ms=4*self.hour)['trades'].empty)
        result = simulate(source, [1]+[0]*23, min_holding_ms=4*self.hour)
        self.assertEqual(result['trades'].iloc[0].exit_reason, 'FINAL_EXIT')
        self.assertEqual(result['metrics']['final_exit_count'], 1)
        self.assertEqual(result['metrics']['holding_in_target_range'], 1)

    def test_duration_validation_and_missing_fine_candle_coverage(self):
        """验证持仓参数与缺失细K线覆盖检查。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        source = candles(count=4, interval=self.hour)
        for kwargs in (dict(min_holding_ms=-1), dict(execution_delay_ms=.5),
                       dict(min_holding_ms=5*self.hour, max_holding_ms=4*self.hour),
                       dict(scale_out_fraction=1.), dict(scale_out_interval_ms=0)):
            with self.assertRaises(ValueError):
                simulate(source, [1, 0, 0, 0], **kwargs)
        with self.assertRaisesRegex(ValueError, 'complete source candle range'):
            simulate(source, [1, 0, 0, 0], execution_data=candles(count=15))


if __name__ == '__main__':
    unittest.main()
