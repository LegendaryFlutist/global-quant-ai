import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from batch_report import export_batch_report


class BatchReportTests(unittest.TestCase):
    def fixture(self, source, symbol='BTCUSDT', all_options=False):
        source.mkdir(parents=True)
        start = 1_787_616_000_000
        periods = ('15m', '30m', '1h', '2h', '4h') if all_options else ('15m',)
        exits = ('fixed_rr', 'atr_trailing', 'three_bar_breakout', 'three_bar_trailing',
                 'time_exit', 'fixed_three_bar_target', 'percentage_swing') if all_options else ('fixed_rr',)
        ranking, trades, events, curves = [], [], [], {}
        for model in ('pattern_only', 'mlp'):
            for timeframe in periods:
                for exit_mode in exits:
                    config = '|'.join((timeframe, model, exit_mode))
                    direction = 1 if model == 'pattern_only' else -1
                    trade = dict(config=config, model=model, timeframe=timeframe, exit_mode=exit_mode,
                        fold=1, fold_start_equity=1., direction=direction, entry_time=start+900000,
                        exit_time=start+900000+24*3600000, entry_price=100.,
                        exit_price=100.+direction*2, net_pnl=.009, gross_pnl=.01,
                        entry_fee=.0005, exit_fee=.0005, total_fee=.001, funding_paid=0.,
                        quantity=.005, allocated_margin=.1, entry_notional=.5,
                        account_equity_before=1., account_equity_after=1.009,
                        exit_reason='TAKE_PROFIT', exit_time_precision='bar_open',
                        partial_exit_count=0, account_return=.009, position_id=1)
                    trades.append(trade)
                    events.append(dict(config=config, fold=1, event='ENTRY', time=trade['entry_time']))
                    row = dict(config=config, model=model, timeframe=timeframe, exit_mode=exit_mode,
                        net_return=.009, max_drawdown=.01, entries=1, complete_folds=3,
                        eligible=False, rejection_reasons=['too few trades'])
                    ranking.append(row)
                    curves[config] = {str(start): 1., str(trade['exit_time']): 1.009}
        selected = next(row for row in ranking if row['model'] == 'mlp')
        report = dict(source=dict(symbol=symbol, start=start, end=start+30*86400000, synthetic=False),
            ranking=ranking, selected=selected, status='REJECTED',
            period_pairs={period: dict(entry_minutes=15, higher_minutes=60) for period in (*periods, '8h')},
            exit_modes={mode: mode for mode in exits},
            holdout_used=True, holdout=dict(net_return=-.01, entries=1),
            holdout_double_cost=dict(net_return=-.02),
            risk=dict(leverage=5., margin_fraction=.1),
            actual_email_sent=False, real_orders_enabled=False)
        (source/'report.json').write_text(json.dumps(report))
        (source/'validation_curves.json').write_text(json.dumps(curves))
        pd.DataFrame(trades).to_csv(source/'trades.csv', index=False)
        pd.DataFrame(events).to_csv(source/'events.csv', index=False)
        for name in ('selected_holdout_equity.csv', 'selected_holdout_trades.csv', 'selected_holdout_events.csv'):
            (source/name).write_text('time,value\n1,1\n')
        (source/'models.joblib').write_bytes(b'PRIVATE MODEL ARTIFACT')
        (source/'outbox').mkdir()
        (source/'outbox'/'message.json').write_text('private mail preview')
        return report

    def snapshot(self, root):
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob('*') if path.is_file()}

    def test_scopes_preserve_all_timeframes_exits_without_modifying_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)/'batch'
            source = batch/'raw'/'btc'
            self.fixture(source, all_options=True)
            before = self.snapshot(source)
            with patch('notifications.send_email') as send:
                result = export_batch_report(batch, [dict(symbol='BTCUSDT', status='REJECTED', run_dir='raw/btc')])
                send.assert_not_called()
            self.assertEqual(before, self.snapshot(source))
            self.assertEqual(result['configurations'], 70)
            self.assertTrue(result['independent_accounts'])
            self.assertNotIn('overall_net_return', result)
            self.assertEqual(set(result['models']), {'pattern_only', 'mlp'})
            for model in result['models']:
                scope = batch/'models'/model/'symbols'/'BTCUSDT'
                report = json.loads((scope/'report.json').read_text())
                self.assertEqual(len(report['ranking']), 35)
                self.assertEqual(set(report['period_pairs']), {'15m', '30m', '1h', '2h', '4h'})
                self.assertEqual(len(report['exit_modes']), 7)
                self.assertTrue(all(row['model'] == model for row in report['ranking']))
                self.assertEqual(len(pd.read_csv(scope/'trades.csv')), 35)
                self.assertEqual(len(pd.read_csv(scope/'events.csv')), 35)
                self.assertEqual(len(json.loads((scope/'validation_curves.json').read_text())), 35)
                self.assertTrue((scope/'operations.html').exists())
                self.assertFalse((scope/'models.joblib').exists())
                self.assertFalse((scope/'outbox').exists())
                page = (batch/'models'/model/'index.html').read_text()
                self.assertIn('symbols/BTCUSDT/operations.html', page)
                self.assertNotIn('fetch(', page)
                self.assertIn('single-curve', page)
            all_accounts = pd.read_csv(batch/'accounts_summary.csv')
            self.assertEqual(len(all_accounts), 70)
            self.assertTrue((all_accounts.net_return-.009).abs().max() < 1e-12)

    def test_holdout_is_only_copied_to_the_original_selected_model(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)/'batch'
            source = Path(directory)/'source'
            self.fixture(source)
            export_batch_report(batch, [dict(symbol='BTCUSDT', status='REJECTED', run_dir=str(source))])
            scopes = batch/'models'
            rule = json.loads((scopes/'pattern_only'/'symbols'/'BTCUSDT'/'report.json').read_text())
            mlp = json.loads((scopes/'mlp'/'symbols'/'BTCUSDT'/'report.json').read_text())
            self.assertIsNone(rule['selected'])
            self.assertNotIn('holdout', rule)
            self.assertNotIn('holdout_double_cost', rule)
            self.assertFalse(rule['holdout_used'])
            self.assertEqual(rule['status'], 'NOT_SELECTED')
            self.assertEqual(mlp['holdout']['net_return'], -.01)
            self.assertEqual(mlp['status'], 'REJECTED')
            self.assertFalse((scopes/'pattern_only'/'symbols'/'BTCUSDT'/'selected_holdout_trades.csv').exists())
            self.assertTrue((scopes/'mlp'/'symbols'/'BTCUSDT'/'selected_holdout_trades.csv').exists())

    def test_economics_separates_price_margin_and_account_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)/'batch'
            source = batch/'btc'
            self.fixture(source)
            export_batch_report(batch, [dict(symbol='BTCUSDT', status='REJECTED', report='btc/operations.html')])
            root = batch/'models'
            long = pd.read_csv(root/'pattern_only'/'symbols'/'BTCUSDT'/'trade_economics.csv').iloc[0]
            short = pd.read_csv(root/'mlp'/'symbols'/'BTCUSDT'/'trade_economics.csv').iloc[0]
            self.assertAlmostEqual(long.underlying_price_return, .02)
            self.assertAlmostEqual(short.underlying_price_return, -.02)
            for trade in (long, short):
                self.assertAlmostEqual(trade.direction_adjusted_price_return, .02)
                self.assertAlmostEqual(trade.realized_price_return, .02)
                self.assertAlmostEqual(trade.margin_return, .09)
                self.assertAlmostEqual(trade.account_return, .009)
                self.assertAlmostEqual(trade.initial_account_return_contribution, .009)
                self.assertEqual(trade.margin_return_denominator_allocated_margin, .1)
                self.assertEqual(trade.account_return_denominator_equity_before, 1.)
                self.assertIn('slippage', trade.price_basis)

    def test_waiting_and_failed_records_still_have_a_report_and_repeat_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)/'batch'
            records = [dict(symbol='BTCUSDT', status='WAITING'),
                       dict(symbol='ETHUSDT', status='FAILED', error='<problem>')]
            result = export_batch_report(batch, records)
            self.assertEqual(result['configurations'], 0)
            self.assertIn('&lt;problem&gt;', (batch/'index.html').read_text())
            self.fixture(batch/'btc')
            records[0].update(status='REJECTED', run_dir='btc')
            first = export_batch_report(batch, records)
            with patch('batch_report.export_operations_report', side_effect=AssertionError('should be cached')):
                second = export_batch_report(batch, records)
            self.assertEqual(first, second)

    def test_new_swing_policy_and_raw_prices_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)/'batch'
            source = batch/'btc'
            report = self.fixture(source)
            report.update(holding_policy=dict(min_holding_hours=24, max_holding_hours=504),
                swing_policy=dict(lookback_hours=168, min_range=.02, max_range=.5,
                                  min_profit_move_pct=.02, price_target_pct=.05,
                                  price_stop_pct=.03),
                validation_windows=[[1_787_616_000_000, 1_787_616_000_000+12*86400000]])
            (source/'report.json').write_text(json.dumps(report))
            trades = pd.read_csv(source/'trades.csv')
            trades['entry_raw_price'] = 99.98
            trades['exit_raw_price'] = trades.exit_price+.02
            trades['signal_price'] = 99.9
            trades['directional_price_return'] = .02
            trades.to_csv(source/'trades.csv', index=False)
            export_batch_report(batch, [dict(symbol='BTCUSDT', status='REJECTED', run_dir='btc')])
            page = (batch/'index.html').read_text()
            self.assertIn('24小时～21天', page)
            self.assertIn('最近7日', page)
            self.assertIn('2%～50%', page)
            self.assertIn('不是未来价格涨幅或账户收益保证', page)
            self.assertIn('12.0～12.0天', page)
            scoped = batch/'models'/'pattern_only'/'symbols'/'BTCUSDT'
            economics = pd.read_csv(scoped/'trade_economics.csv').iloc[0]
            self.assertAlmostEqual(economics.entry_raw_price, 99.98)
            self.assertAlmostEqual(economics.raw_underlying_price_return, 102.02/99.98-1)
            self.assertAlmostEqual(economics.directional_price_return, .02)


if __name__ == '__main__':
    unittest.main()
