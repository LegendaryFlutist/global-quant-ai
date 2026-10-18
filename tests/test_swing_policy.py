"""Causal historical opportunity filters and underlying-price swing policies."""
import unittest

import numpy as np
import pandas as pd

from candlestick_patterns import PATTERN_COLUMNS
from pattern_features import FEATURE_COLUMNS, entry_signals, prepare_timeframe
from pattern_execution import simulate_patterns


HOUR = 3_600_000


def candles(rows=None, count=8, interval=900_000, atr=10.):
    if rows is None:
        rows = [(100., 101., 99., 100.)] * count
    frame = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'])
    frame['open_time'] = np.arange(len(frame))*interval
    frame['close_time'] = frame.open_time+interval-1
    frame['volume'] = 100.+np.arange(len(frame)) % 19
    frame['atr'] = atr
    for name in ('open', 'high', 'low', 'close'):
        frame['mark_'+name] = frame[name]
    return frame


class SwingFeatureTests(unittest.TestCase):
    def test_historical_range_needs_seven_complete_days_and_excludes_partial_four_hours(self):
        frame = candles(count=1000)
        frame.loc[680, 'high'] = frame.loc[680, 'mark_high'] = 150.
        result = prepare_timeframe(frame, frame, '15m', swing_lookback_hours=168)
        self.assertTrue(result.swing_range_pct.iloc[:671].isna().all())
        self.assertAlmostEqual(result.swing_range_pct.iloc[671], (101-99)/99)
        self.assertEqual(result.swing_range_pct.iloc[671], result.swing_range_pct.iloc[686])
        self.assertAlmostEqual(result.swing_range_pct.iloc[687], (150-99)/99)
        known = result.swing_close_time.notna()
        self.assertTrue((result.loc[known, 'swing_close_time'] <= result.loc[known, 'close_time']).all())

    def test_swing_features_and_entry_signals_are_prefix_invariant(self):
        frame = candles(count=4096)
        i = np.arange(len(frame))
        frame['close'] = 100+.003*i+np.sin(i/17)
        frame['open'] = np.r_[frame.close.iloc[0], frame.close.iloc[:-1]]
        frame['high'] = frame[['open', 'close']].max(axis=1)+.3
        frame['low'] = frame[['open', 'close']].min(axis=1)-.3
        for name in ('open', 'high', 'low', 'close'):
            frame['mark_'+name] = frame[name]
        for timeframe in ('15m', '1h', '4h'):
            full = prepare_timeframe(frame, frame, timeframe, swing_lookback_hours=168)
            columns = [name for name in full if not name.startswith('label')]
            for stop in (685, 2001, 4003):
                with self.subTest(timeframe=timeframe, stop=stop):
                    prefix = prepare_timeframe(frame.iloc[:stop], frame.iloc[:stop], timeframe,
                                               swing_lookback_hours=168)
                    pd.testing.assert_frame_equal(full.iloc[:len(prefix)][columns], prefix[columns])
                    actual = entry_signals(prefix, swing_min_range=.02, swing_max_range=.5)
                    expected = entry_signals(full.iloc[:len(prefix)], swing_min_range=.02,
                                             swing_max_range=.5)
                    pd.testing.assert_frame_equal(actual, expected)

    def test_entry_filter_uses_observed_range_with_inclusive_bounds_and_rejects_warmup(self):
        frame = pd.DataFrame(0., index=range(5), columns=FEATURE_COLUMNS)
        frame['candidate_direction'] = frame['higher_trend'] = 1
        frame['bullish_count'] = 1
        frame[PATTERN_COLUMNS[0]] = 1
        frame['swing_range_pct'] = [np.nan, .019, .02, .5, .5001]
        self.assertEqual(entry_signals(frame).direction.tolist(), [1]*5)
        actual = entry_signals(frame, swing_min_range=.02, swing_max_range=.5)
        self.assertEqual(actual.direction.tolist(), [0, 0, 1, 1, 0])
        self.assertNotIn('swing_range_pct', FEATURE_COLUMNS)

    def test_invalid_swing_parameters_fail_explicitly(self):
        frame = candles(count=700)
        for lookback in (0, 3, 5, float('nan')):
            with self.assertRaises(ValueError):
                prepare_timeframe(frame, frame, '15m', swing_lookback_hours=lookback)
        prepared=prepare_timeframe(frame,frame,'15m')
        with self.assertRaises(ValueError):
            entry_signals(prepared,swing_min_range=.02)

    def test_delayed_swing_labels_use_actual_entry_end_and_two_percent_cost_floor(self):
        for direction in (1,-1):
            frame=candles(count=1000)
            frame['open']=frame['close']=100+direction*.005*np.arange(len(frame))
            frame['high']=frame.close+.3
            frame['low']=frame.close-.3
            kwargs=dict(execution_delay_ms=900_000,label_holding_ms=48*HOUR)
            original=prepare_timeframe(frame,frame,'15m',**kwargs)
            zero=prepare_timeframe(frame,frame,'15m',label_min_move_pct=0.,**kwargs)
            filtered=prepare_timeframe(frame,frame,'15m',label_min_move_pct=.02,**kwargs)
            pd.testing.assert_series_equal(original.label,zero.label)
            row=filtered.iloc[300]
            self.assertEqual(row.label_entry_time,302*900_000)
            self.assertEqual(row.label_end_time,494*900_000-1)
            self.assertEqual(original.label.iloc[300],direction)
            self.assertEqual(row.label,0)
            frame['open']=frame['close']=100+direction*.02*np.arange(len(frame))
            frame['high']=frame.close+.3
            frame['low']=frame.close-.3
            strong=prepare_timeframe(frame,frame,'15m',label_min_move_pct=.02,**kwargs)
            self.assertEqual(strong.label.iloc[300],direction)


class SwingExecutionTests(unittest.TestCase):
    def test_percent_targets_measure_long_and_short_actual_fills_after_slippage(self):
        for side, row in ((1, (100, 108, 99, 106)), (-1, (100, 101, 92, 94))):
            with self.subTest(side=side):
                frame = candles([(100, 101, 99, 100), row])
                result = simulate_patterns(frame, [side, 0], exit_mode='swing_percent',
                                           fee=0., slip=.001, price_target_pct=.05)
                trade = result['trades'].iloc[0]
                self.assertEqual(trade.exit_reason, 'TAKE_PROFIT')
                self.assertAlmostEqual(trade.directional_price_return, .05)
                self.assertAlmostEqual(side*(trade.exit_price/trade.entry_price-1), .05)
                self.assertEqual(trade.signal_price, 100)
                self.assertEqual(trade.entry_raw_price, 100)
                self.assertAlmostEqual(trade.exit_price, trade.exit_raw_price*(1-side*.001))

    def test_percent_mode_uses_tighter_price_or_atr_stop_and_risk_can_exit_before_day(self):
        for atr, expected in ((10., 97.), (1., 98.5)):
            with self.subTest(atr=atr):
                frame = candles([(100, 101, 99, 100), (100, 106, 96, 100)] +
                                [(100, 101, 99, 100)]*100, atr=atr)
                trade = simulate_patterns(frame, [1]+[0]*(len(frame)-1),
                    exit_mode='swing_percent', fee=0., slip=0.,
                    min_holding_ms=24*HOUR, max_holding_ms=21*24*HOUR)['trades'].iloc[0]
                self.assertEqual(trade.exit_reason, 'STOP_LOSS')
                self.assertEqual(trade.exit_price, expected)
                self.assertLess(trade.holding_hours, 24)

    def test_percent_target_waits_for_day_and_maximum_is_twenty_one_days(self):
        frame = candles(count=2200)
        frame.loc[2, 'high'] = frame.loc[2, 'mark_high'] = 106
        frame.loc[99, 'high'] = frame.loc[99, 'mark_high'] = 106
        trade = simulate_patterns(frame, [1]+[0]*(len(frame)-1), exit_mode='swing_percent',
            fee=0., slip=0., min_holding_ms=24*HOUR,
            max_holding_ms=21*24*HOUR)['trades'].iloc[0]
        self.assertEqual(trade.exit_bar, 99)
        self.assertGreaterEqual(trade.holding_hours, 24)
        self.assertEqual(trade.exit_reason, 'TAKE_PROFIT')
        frame = candles(count=2200)
        trade = simulate_patterns(frame, [1]+[0]*(len(frame)-1), exit_mode='swing_percent',
            fee=0., slip=0., min_holding_ms=24*HOUR,
            max_holding_ms=21*24*HOUR)['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'MAX_HOLDING_TIME')
        self.assertEqual(trade.holding_hours, 504.)

    def test_fixed_rr_is_raised_to_two_percent_including_slippage(self):
        for side, row in ((1, (100, 104, 99.5, 102)), (-1, (100, 100.5, 96, 98))):
            frame = candles([(100, 100.1, 99.9, 100), row], atr=.5)
            trade = simulate_patterns(frame, [side, 0], exit_mode='fixed_rr', fee=0.,
                slip=.001, min_profit_move_pct=.02)['trades'].iloc[0]
            self.assertEqual(trade.exit_reason, 'TAKE_PROFIT')
            self.assertAlmostEqual(trade.directional_price_return, .02)

    def test_three_bar_profit_level_below_threshold_is_not_filled_at_old_target(self):
        frame = candles([(100, 101, 99, 100)]*3 + [(100, 104, 99, 103)])
        for mode in ('three_bar_breakout', 'fixed_three_bar_target'):
            with self.subTest(mode=mode):
                trade = simulate_patterns(frame, [0, 0, 1, 0], exit_mode=mode,
                    fee=0., slip=0., min_profit_move_pct=.02)['trades'].iloc[0]
                self.assertEqual(trade.exit_reason, 'FINAL_EXIT')
                self.assertEqual(trade.exit_price, 103.)

    def test_atr_trailing_does_not_activate_inside_minimum_profit_move(self):
        frame = candles([(100,101,99,100),(100,103,99,102),
                         (102,102.5,100.5,102),(102,102.5,101.5,102)],atr=1.)
        trade = simulate_patterns(frame,[1,0,0,0],exit_mode='atr_trailing',
            fee=0.,slip=0.,min_profit_move_pct=.02)['trades'].iloc[0]
        # The first completed high would create a +1% trailing stop at 101.
        # That profit level is ignored; the protective loss stop stays active.
        self.assertEqual(trade.exit_reason,'FINAL_EXIT')
        self.assertEqual(trade.final_stop,98.5)
        frame.loc[1,'high']=frame.loc[1,'mark_high']=104
        trade = simulate_patterns(frame,[1,0,0,0],exit_mode='atr_trailing',
            fee=0.,slip=0.,min_profit_move_pct=.02)['trades'].iloc[0]
        self.assertEqual(trade.exit_reason,'ATR_TRAILING_STOP')
        self.assertAlmostEqual(trade.directional_price_return,.02)

    def test_profit_gate_does_not_delay_opposite_signal_or_raise_loss_stop(self):
        frame = candles(count=105)
        signals = [1, -1]+[0]*103
        trade = simulate_patterns(frame, signals, exit_mode='swing_percent', fee=0.,
            slip=0., min_profit_move_pct=.02, min_holding_ms=24*HOUR,
            max_holding_ms=21*24*HOUR)['trades'].iloc[0]
        self.assertEqual(trade.exit_reason, 'OPPOSITE_SIGNAL')
        self.assertEqual(trade.holding_hours, 24)
        self.assertEqual(trade.directional_price_return, 0.)

    def test_partial_exit_price_return_uses_original_notional_and_reconciles_account(self):
        frame = candles([(100, 101, 99, 100)] + [(104, 105, 103, 104)]*180,
                        interval=HOUR, atr=1.)
        # Enter at 100, then keep the ATR reference wide enough that a price
        # target cannot end this duration-based partial-exit experiment.
        frame.loc[1, ['open', 'low', 'mark_open', 'mark_low']] = [100, 99, 100, 99]
        result = simulate_patterns(frame, [1]+[0]*(len(frame)-1), exit_mode='time_exit',
            fee=.001, slip=.0002, max_holding_bars=500,
            min_holding_ms=24*HOUR, max_holding_ms=21*24*HOUR,
            scale_out_after_ms=72*HOUR, scale_out_interval_ms=24*HOUR,
            scale_out_fraction=.25, scale_out_deviation_atr=3.)
        trade = result['trades'].iloc[0]
        partial = result['events'].query("event == 'PARTIAL_EXIT'")
        self.assertEqual(len(partial), 3)
        self.assertEqual(trade.partial_exit_count, 3)
        self.assertAlmostEqual(trade.directional_price_return,
                               trade.gross_pnl/trade.entry_notional)
        weighted_fill = ((partial.quantity*partial.price).sum()+
                         trade.final_exit_quantity*trade.exit_price)/trade.initial_quantity
        self.assertAlmostEqual(trade.directional_price_return,
                               weighted_fill/trade.entry_price-1)
        self.assertAlmostEqual(result['metrics']['net_return'], trade.net_pnl)
        self.assertAlmostEqual(trade.net_pnl, trade.gross_pnl-trade.total_fee-trade.funding_paid)
        self.assertAlmostEqual(trade.margin_return, trade.net_pnl/trade.allocated_margin)

    def test_invalid_price_policy_parameters_are_rejected(self):
        frame = candles()
        for parameter in ({'price_target_pct': .01}, {'price_target_pct': .51},
                          {'price_stop_pct': 0.}, {'price_stop_pct': 1.},
                          {'min_profit_move_pct': -.01}, {'min_profit_move_pct': float('nan')}):
            with self.subTest(parameter=parameter), self.assertRaises(ValueError):
                simulate_patterns(frame, [1]+[0]*(len(frame)-1), **parameter)


if __name__ == '__main__':
    unittest.main()
