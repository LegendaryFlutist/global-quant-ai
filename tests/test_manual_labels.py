"""Contracts for delayed manual-entry labels and the closed 4h ATR risk floor."""
import unittest

import numpy as np
import pandas as pd

from pattern_features import MINUTES, prepare_timeframe


STEP = 900_000
HOUR = 3_600_000


def market_candles(size=1024):
    index = np.arange(size)
    close = 100 + .025*index + 2*np.sin(index/23)
    opening = np.r_[close[0], close[:-1]] + .03*np.cos(index/11)
    return pd.DataFrame(dict(
        open_time=index*STEP, close_time=(index+1)*STEP-1,
        open=opening, high=np.maximum(opening, close)+.2,
        low=np.minimum(opening, close)-.2, close=close,
        volume=100.+index % 29))


class ManualLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = market_candles()
        cls.marks = cls.bars.copy()
        cls.marks[['open', 'high', 'low', 'close']] *= 1.0007

    def prepare(self, timeframe='15m', bars=None, marks=None, **kwargs):
        return prepare_timeframe(
            self.bars if bars is None else bars,
            self.marks if marks is None else marks, timeframe,
            execution_delay_ms=STEP, label_holding_ms=12*HOUR, **kwargs)

    def test_all_timeframes_label_actual_delayed_15m_open_to_twelve_hour_close(self):
        opening = self.bars.set_index('open_time').open
        closing = self.bars.set_index('close_time').close
        fee, slip = .0007, .0003
        for timeframe in MINUTES:
            with self.subTest(timeframe=timeframe):
                frame = self.prepare(timeframe, fee=fee, slip=slip)
                expected_entry = frame.close_time+1+STEP
                expected_end = expected_entry+12*HOUR-1
                available_entry = expected_entry.isin(opening.index)
                available_exit = expected_end.isin(closing.index)
                np.testing.assert_array_equal(
                    frame.label_entry_time.notna(), available_entry)
                np.testing.assert_array_equal(
                    frame.label_end_time.notna(), available_entry & available_exit)
                valid = available_entry & available_exit
                np.testing.assert_array_equal(frame.loc[valid, 'label_entry_time'], expected_entry[valid])
                np.testing.assert_array_equal(frame.loc[valid, 'label_end_time'], expected_end[valid])
                future = expected_end.map(closing)/expected_entry.map(opening)-1
                hurdle = 2*(fee+slip)+.25*frame.atr_pct
                expected_label = np.where(future > hurdle, 1, np.where(future < -hurdle, -1, 0))
                np.testing.assert_array_equal(frame.label, expected_label)
                self.assertTrue(frame.loc[~valid, 'label_end_time'].isna().all())

    def test_four_hour_label_is_not_filled_at_next_four_hour_open(self):
        frame = self.prepare('4h', fee=0., slip=0.)
        row = frame.iloc[14]
        expected_entry = 15*4*HOUR+STEP
        expected_end = expected_entry+12*HOUR-1
        self.assertEqual(row.label_entry_time, expected_entry)
        self.assertEqual(row.label_end_time, expected_end)
        actual_open = self.bars.loc[self.bars.open_time == expected_entry, 'open'].iloc[0]
        next_four_hour_open = self.bars.loc[self.bars.open_time == 15*4*HOUR, 'open'].iloc[0]
        self.assertNotEqual(actual_open, next_four_hour_open)

    def test_non_grid_delay_rounds_up_to_first_real_fifteen_minute_open(self):
        frame = prepare_timeframe(self.bars, self.marks, '1h',
                                  execution_delay_ms=5*60_000,
                                  label_holding_ms=12*HOUR)
        self.assertEqual(frame.label_entry_time.iloc[0], HOUR+STEP)
        self.assertEqual(frame.label_end_time.iloc[0], HOUR+STEP+12*HOUR-1)

    def test_label_end_time_rejects_every_training_example_reaching_cut(self):
        frame = self.prepare('15m')
        cut = frame.close_time.iloc[700]
        train = frame[(frame.close_time < cut) & (frame.label_end_time < cut)]
        self.assertTrue((train.label_end_time < cut).all())
        # t+2 starts the delayed trade; its 48th 15m close is t+49.
        self.assertEqual(train.index[-1], 650)
        self.assertEqual(frame.label_end_time.iloc[651], cut)
        self.assertTrue(frame.label_end_time.iloc[-49:].isna().all())

    def test_risk_atr_floor_matches_only_completed_four_hour_ohlc(self):
        # Compute the 4h reference independently from raw blocks of 16 candles.
        raw = self.bars
        blocks = len(raw)//16
        highs = raw.high.to_numpy().reshape(blocks, 16).max(axis=1)
        lows = raw.low.to_numpy().reshape(blocks, 16).min(axis=1)
        closes = raw.close.to_numpy().reshape(blocks, 16)[:, -1]
        previous = np.r_[np.nan, closes[:-1]]
        true_range = np.nanmax(np.column_stack((highs-lows, abs(highs-previous), abs(lows-previous))), axis=1)
        expected_atr = pd.Series(true_range).ewm(alpha=1/14, adjust=False, min_periods=14).mean().to_numpy()
        for timeframe in MINUTES:
            with self.subTest(timeframe=timeframe):
                frame = self.prepare(timeframe, risk_atr_floor_minutes=240)
                last_closed = ((frame.close_time.to_numpy()+1)//(4*HOUR)-1).astype(int)
                expected = np.full(len(frame), np.nan)
                available = last_closed >= 0
                expected[available] = expected_atr[last_closed[available]]
                np.testing.assert_allclose(frame.risk_atr_floor, expected, equal_nan=True)
                combined = np.maximum(frame.signal_atr, expected)
                # A missing floor keeps risk ATR unavailable during warm-up.
                np.testing.assert_allclose(frame.atr, combined, equal_nan=True)
        lower = self.prepare('15m', risk_atr_floor_minutes=240)
        self.assertTrue(lower.atr.iloc[:223].isna().all())
        self.assertTrue(np.isfinite(lower.atr.iloc[223:]).all())

    def test_unclosed_four_hour_spike_cannot_change_floor_early(self):
        altered = self.bars.copy()
        altered.loc[230, 'high'] += 1000
        changed = self.prepare('15m', bars=altered, marks=self.marks,
                               risk_atr_floor_minutes=240)
        original = self.prepare('15m', risk_atr_floor_minutes=240)
        pd.testing.assert_series_equal(original.risk_atr_floor.iloc[:239],
                                       changed.risk_atr_floor.iloc[:239])
        self.assertGreater(changed.risk_atr_floor.iloc[239], original.risk_atr_floor.iloc[239])
        self.assertEqual(changed.risk_atr_floor.iloc[230], original.risk_atr_floor.iloc[223])

    def test_non_label_features_and_risk_floor_are_prefix_causal(self):
        for timeframe in MINUTES:
            full = self.prepare(timeframe, risk_atr_floor_minutes=240)
            feature_columns = [name for name in full if not name.startswith('label')]
            for stop in (311, 499, 1023):
                with self.subTest(timeframe=timeframe, stop=stop):
                    prefix = self.prepare(timeframe, bars=self.bars.iloc[:stop],
                                          marks=self.marks.iloc[:stop],
                                          risk_atr_floor_minutes=240)
                    pd.testing.assert_frame_equal(full.loc[:len(prefix)-1, feature_columns],
                                                   prefix[feature_columns])


if __name__ == '__main__':
    unittest.main()
