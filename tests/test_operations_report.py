import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from operation_report import export_operations_report


class OperationsReportTests(unittest.TestCase):
    FIRST = '15m|pattern_only|fixed_rr'
    SECOND = '15m|random_forest|time_exit'

    def fixture(self, root, selected=False):
        """构造当前报告/行情测试所需的独立临时输入和冻结边界。

        Args:
            root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。
            selected: 当前测试/接口参数，含义见该函数的具体场景。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。

        Notes:
            原有细则保留如下：
            Two independent accounts, with the first account stitched across folds.
        """
        start = 1_787_616_000_000  # 2026-08-25 00:00 UTC / 08:00 Shanghai.

        def trade(config, fold, capital, direction, net, fee, funding, day=0):
            """构造一条有明确资金、成本和时间口径的模拟完整交易。

            Args:
                config: 当前测试/接口参数，含义见该函数的具体场景。
                fold: 当前测试/接口参数，含义见该函数的具体场景。
                capital: 当前测试/接口参数，含义见该函数的具体场景。
                direction: 方向：1做多、-1做空、0不建立新仓位。
                fee: 单边成交手续费比例，例如0.0005表示0.05%。
                funding: 资金费历史，口径由数据构造函数校验。
                day: 当前测试/接口参数，含义见该函数的具体场景。

            Returns:
                测试夹具、构造结果或断言辅助返回值；不影响真实数据。
            """
            entry = start + day * 86_400_000 + 900_000
            gross = net + fee + funding
            return dict(
                config=config, timeframe='15m', model=config.split('|')[1],
                exit_mode=config.split('|')[2], fold=fold,
                fold_start_equity=capital, stitched_net_pnl=capital * net,
                pattern='engulfing', direction=direction,
                signal_time=entry-1, entry_time=entry,
                exit_time=entry+1_800_000-1, entry_bar=1, exit_bar=2,
                holding_bars=2, entry_price=100.,
                exit_price=100.+direction*gross/.005,
                exit_reason='FINAL_EXIT', exit_time_precision='bar_close',
                quantity=.005, allocated_margin=.1, entry_notional=.5,
                initial_stop=95. if direction == 1 else 105.,
                final_stop=95. if direction == 1 else 105., target_price=None,
                gross_pnl=gross, entry_fee=fee/2, exit_fee=fee/2,
                total_fee=fee, funding_paid=funding, net_pnl=net,
                account_return=net, margin_return=net/.1,
                account_equity_before=1., account_equity_after=1.+net,
            )

        trades = [
            trade(self.FIRST, 1, 1., 1, .1, .01, .005),
            trade(self.FIRST, 2, 1.1, -1, -.02, .004, .002, day=1),
            trade(self.SECOND, 1, 1., -1, .03, .004, .001),
        ]
        end1, end2 = trades[0]['exit_time'], trades[1]['exit_time']
        curves = {
            self.FIRST: {str(start): 1., str(end1): 1.1, str(end2): 1.078},
            self.SECOND: {str(start): 1., str(end1): 1.03, str(end2): 1.03},
        }

        def row(config, net, entries, folds):
            """构造当前报告所需的候选配置排名行。

            Args:
                config: 当前测试/接口参数，含义见该函数的具体场景。
                entries: 当前测试/接口参数，含义见该函数的具体场景。
                folds: 当前测试/接口参数，含义见该函数的具体场景。

            Returns:
                测试夹具、构造结果或断言辅助返回值；不影响真实数据。
            """
            return dict(
                config=config, timeframe='15m', model=config.split('|')[1],
                exit_mode=config.split('|')[2], net_return=net,
                max_drawdown=.02, close_drawdown=.02, entries=entries,
                liquidations=0, validation_score=.01,
                complete_folds=len(folds), eligible=False,
                rejection_reasons=['交易少于30笔'],
                folds=[dict(fold=i+1, complete=True, net_return=ret,
                            entries=1, trades=1, liquidations=0,
                            max_drawdown=.02)
                       for i, ret in enumerate(folds)],
            )

        ranking = [row(self.FIRST, .078, 2, [.1, -.02]),
                   row(self.SECOND, .03, 1, [.03])]
        report = dict(
            source={'symbol': 'BTCUSDT', 'synthetic': False,
                    'start': start, 'end': end2},
            status='REJECTED' if selected else 'NO_CANDIDATE',
            selected=ranking[1] if selected else None,
            ranking=ranking, period_pairs={'15m': {'entry_minutes': 15,
                                                   'higher_minutes': 60}},
            validation_windows=[[start, end1+1], [end1+1, end2+1]],
            risk=dict(leverage=5., margin_fraction=.1, fee=.0005,
                      slip=.0002, maintenance_rate=.01, liquidation_fee=.01),
            exit_modes={'fixed_rr': '固定2R止盈', 'time_exit': '时间退出'},
            actual_email_sent=False, real_orders_enabled=False,
            fit_warnings=[], limitations=[],
        )
        (root/'report.json').write_text(json.dumps(report), encoding='utf-8')
        (root/'validation_curves.json').write_text(json.dumps(curves), encoding='utf-8')
        pd.DataFrame(trades).to_csv(root/'trades.csv', index=False)
        pd.DataFrame(ranking).drop(columns=['folds']).to_csv(root/'ranking.csv', index=False)
        return report

    def test_fold_scaled_costs_returns_operation_directions_and_shanghai_times(self):
        """验证验证折缩放后的成本、方向、收益与上海时间正确。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            summary = export_operations_report(root)
            self.assertEqual(summary['symbol'], 'BTCUSDT')
            self.assertEqual(summary['configurations'], 2)
            self.assertEqual(summary['total_round_trips'], 3)
            self.assertEqual(summary['total_operations'], 6)
            first = summary['accounts'][self.FIRST]
            self.assertEqual(first['round_trips'], 2)
            self.assertEqual(first['open_operations'], 2)
            self.assertEqual(first['close_operations'], 2)
            self.assertEqual(first['long_trades'], 1)
            self.assertEqual(first['short_trades'], 1)
            self.assertAlmostEqual(first['net_return'], .1+1.1*(-.02))
            self.assertAlmostEqual(first['fees'], .01+1.1*.004)
            self.assertAlmostEqual(first['funding'], .005+1.1*.002)
            self.assertAlmostEqual(first['win_rate'], .5)
            curves = json.loads((root/'validation_curves.json').read_text())
            self.assertAlmostEqual(first['net_return'], list(curves[self.FIRST].values())[-1]-1)
            operations = pd.read_csv(root/'operations.csv')
            self.assertEqual(len(operations), 6)
            self.assertEqual(set(operations.symbol), {'BTCUSDT'})
            actions = operations[operations.config == self.FIRST]
            self.assertCountEqual(list(zip(actions.operation, actions.side)),
                                  [('OPEN', 'BUY'), ('CLOSE', 'SELL'),
                                   ('OPEN', 'SELL'), ('CLOSE', 'BUY')])
            self.assertCountEqual(actions.action_label,
                                  ['开多', '平多', '开空', '平空'])
            readable = pd.read_csv(root/'trades_readable.csv')
            first_trade = readable[readable.config == self.FIRST].iloc[0]
            self.assertIn('2026-08-25', first_trade.entry_time_shanghai)
            self.assertIn('08:15:00', first_trade.entry_time_shanghai)
            self.assertIn('08:44:59', first_trade.exit_time_shanghai)
            self.assertTrue(operations.time_shanghai.astype(str).str.contains('08:15:00').any())

    def test_independent_accounts_and_default_selection_does_not_promote_models(self):
        """验证独立账户展示和默认选择不等于模型晋升。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            summary = export_operations_report(root)
            self.assertIs(summary['independent_accounts'], True)
            self.assertNotIn('overall_net_return', summary)
            self.assertEqual(summary['default_config'], self.FIRST)
            self.assertEqual(set(summary['accounts']), {self.FIRST, self.SECOND})
            self.fixture(root, selected=True)
            selected = export_operations_report(root)
            self.assertEqual(selected['default_config'], self.SECOND)
            self.assertIs(selected['independent_accounts'], True)
            self.assertNotIn('overall_net_return', selected)
            self.assertEqual(json.loads((root/'report.json').read_text())['status'], 'REJECTED')

    def test_offline_idempotent_report_preserves_research_and_never_sends_email(self):
        """验证离线重复报告不修改研究原件且不发送邮件。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            inputs = ('report.json', 'validation_curves.json', 'trades.csv', 'ranking.csv')
            before = {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                      for name in inputs}
            with patch('notifications.send_email') as send, \
                    patch('smtplib.SMTP') as smtp, \
                    patch('smtplib.SMTP_SSL') as smtp_ssl:
                first = export_operations_report(root)
                second = export_operations_report(root)
                send.assert_not_called()
                smtp.assert_not_called()
                smtp_ssl.assert_not_called()
            self.assertEqual(first, second)
            after = {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                     for name in inputs}
            self.assertEqual(before, after)
            persisted = json.loads((root/'operation_summary.json').read_text())
            self.assertEqual(persisted['default_config'], self.FIRST)
            page = (root/'operations.html').read_text(encoding='utf-8')
            self.assertIn('<select', page.lower())
            self.assertIn(self.FIRST, page)
            self.assertIn(self.SECOND, page)
            self.assertNotRegex(page, r'(?is)<script\b[^>]*\bsrc\s*=\s*[\"\']https?://')
            self.assertNotRegex(page, r'(?i)\bfetch\s*\(')
            self.assertNotRegex(page, r'(?i)cdn\.(?:jsdelivr|plot\.ly)|cdnjs\.cloudflare')
            for filename in ('overall_equity.png', 'overall_equity.svg'):
                self.assertGreater((root/filename).stat().st_size, 100)

    def test_elapsed_time_uses_timestamps_and_keeps_accounts_independent(self):
        """验证持仓时长用时间戳计算且不混合不同账户。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.fixture(root)
            summary=export_operations_report(root)
            first=summary['accounts'][self.FIRST]
            expected=(1_800_000-1)/3_600_000
            self.assertAlmostEqual(first['holding_hours_mean'],expected)
            self.assertAlmostEqual(first['holding_hours_median'],expected)
            self.assertAlmostEqual(first['holding_hours_total'],2*expected)
            self.assertAlmostEqual(first['occupied_hours'],2*expected)
            self.assertAlmostEqual(first['time_in_market'],2*expected/first['backtest_window_hours'])
            self.assertEqual(first['holding_lt_4h_trades'],2)
            self.assertEqual(first['early_exit_reason_counts'],{'FINAL_EXIT':2})
            self.assertEqual(first['early_exit_exception_counts']['fold_or_backtest_end'],2)
            self.assertNotIn('total_holding_hours',summary)
            readable=pd.read_csv(root/'trades_readable.csv')
            self.assertIn('holding_duration',readable.columns)
            self.assertAlmostEqual(readable.iloc[0].holding_hours,expected)
            self.assertEqual(len(pd.read_csv(root/'holding_summary.csv')),2)
            page=(root/'operations.html').read_text(encoding='utf-8')
            for text in ('整体持仓时间','holding-distribution','holding-timeline','持仓时长','holding_summary.csv'):
                self.assertIn(text,page)

    def test_duration_buckets_tolerate_only_one_millisecond_at_bar_close(self):
        """验证持仓分桶仅容许收盘边界的一毫秒误差。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.fixture(root)
            ledger=pd.read_csv(root/'trades.csv')
            first=ledger[ledger.config==self.FIRST].index
            ledger.loc[first[0],'exit_time']=ledger.loc[first[0],'entry_time']+4*3_600_000-1
            ledger.loc[first[1],'exit_time']=ledger.loc[first[1],'entry_time']+169*3_600_000
            ledger.to_csv(root/'trades.csv',index=False)
            summary=export_operations_report(root)['accounts'][self.FIRST]
            self.assertEqual(summary['holding_lt_4h_trades'],0)
            self.assertEqual(summary['holding_within_4h_7d_trades'],1)
            self.assertEqual(summary['holding_gt_7d_trades'],1)
            self.assertAlmostEqual(summary['holding_hours_min'],4-1/3_600_000)
            self.assertAlmostEqual(summary['holding_hours_max'],169)
            self.assertLessEqual(summary['time_in_market'],1)

    def test_partial_exit_events_are_not_counted_as_new_positions_and_reconcile_pnl(self):
        """验证减仓不当作新开仓且完整损益守恒。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.fixture(root)
            trades=pd.read_csv(root/'trades.csv')
            # Position ids restart in each fold; account + fold must disambiguate.
            trades['position_id']=1
            trades['partial_exit_count']=0
            index=trades[trades.config==self.FIRST].index[0]
            trades.loc[index,'partial_exit_count']=1
            trade=trades.loc[index]
            trades['final_exit_fee']=trades.exit_fee
            trades.loc[index,'total_fee']=float(trade.total_fee)+.001
            trades.loc[index,'exit_fee']=float(trade.exit_fee)+.001
            trades.to_csv(root/'trades.csv',index=False)
            event=dict(config=self.FIRST,fold=1,fold_start_equity=1.,position_id=1,
                time=int(trade.entry_time)+900_000,event='PARTIAL_EXIT',direction=1,
                quantity=.001,remaining_quantity=.004,price=105.,fee=.001,
                net_pnl=.02,reason='TIME_DEVIATION_FAVORABLE',timestamp_precision='bar_open')
            pd.DataFrame([event]).to_csv(root/'events.csv',index=False)
            summary=export_operations_report(root)
            first=summary['accounts'][self.FIRST]
            self.assertEqual(first['round_trips'],2)
            self.assertEqual(first['open_operations'],2)
            self.assertEqual(first['close_operations'],2)
            self.assertEqual(first['partial_exit_operations'],1)
            self.assertEqual(first['operations'],5)
            self.assertEqual(summary['total_operations'],7)
            self.assertEqual(summary['total_partial_exit_operations'],1)
            operations=pd.read_csv(root/'operations.csv')
            account=operations[operations.config==self.FIRST]
            self.assertAlmostEqual(account.pnl_initial_equity_fraction.sum(),first['net_return'])
            self.assertAlmostEqual(account.fee_initial_equity_fraction.sum(),first['fees'])
            closing=account[(account.operation=='CLOSE')&(account.fold==1)].iloc[0]
            self.assertAlmostEqual(closing.quantity_initial_equity_unit,.004)
            self.assertAlmostEqual(closing.pnl_initial_equity_fraction,.08)
            partials=pd.read_csv(root/'partial_exits.csv')
            self.assertEqual(len(partials),1)
            self.assertEqual(partials.iloc[0].trade_number,1)
            self.assertIn('holding_duration',partials.columns)

    def test_no_trade_account_still_has_zero_holding_and_download_headers(self):
        """验证无交易账户持仓为零且仍可导出字段表头。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            report=self.fixture(root)
            empty=pd.read_csv(root/'trades.csv').iloc[:0]
            empty.to_csv(root/'trades.csv',index=False)
            curves=json.loads((root/'validation_curves.json').read_text())
            for row in report['ranking']:
                row['net_return']=0
                curves[row['config']]={time:1. for time in curves[row['config']]}
            (root/'report.json').write_text(json.dumps(report),encoding='utf-8')
            (root/'validation_curves.json').write_text(json.dumps(curves),encoding='utf-8')
            summary=export_operations_report(root)
            self.assertEqual(summary['total_operations'],0)
            first=summary['accounts'][self.FIRST]
            self.assertEqual(first['holding_hours_mean'],0.)
            self.assertEqual(first['time_in_market'],0.)
            self.assertEqual(len(pd.read_csv(root/'partial_exits.csv')),0)

    def test_duration_distribution_uses_the_current_24h_to_21d_policy(self):
        """验证持仓分布使用当前24小时至21天目标。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);report=self.fixture(root)
            report['holding_policy']=dict(min_holding_hours=24,max_holding_hours=504)
            (root/'report.json').write_text(json.dumps(report))
            ledger=pd.read_csv(root/'trades.csv')
            index=ledger[ledger.config==self.FIRST].index
            ledger.loc[index[0],'exit_time']=ledger.loc[index[0],'entry_time']+24*3_600_000-1
            ledger.loc[index[1],'exit_time']=ledger.loc[index[1],'entry_time']+480*3_600_000
            ledger.to_csv(root/'trades.csv',index=False)
            s=export_operations_report(root)['accounts'][self.FIRST]
            self.assertEqual(s['holding_within_target_trades'],2)
            self.assertEqual(s['holding_below_target_trades'],0)
            self.assertEqual(s['holding_above_target_trades'],0)
            self.assertEqual(s['holding_target_max_hours'],504)


if __name__ == '__main__':
    unittest.main()
