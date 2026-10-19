import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from lab import demo
from pattern_research import finalize_pattern_mail,run_pattern_research
from quant_lab.models.model_registry import MODEL_NAMES


class PatternResearchTests(unittest.TestCase):
    def test_audit_export_duplicate_config_and_resume_without_retest(self):
        """验证审计导出可恢复且不重复训练/测试。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root:
            out=Path(root)
            report=dict(source={'symbol':'BTCUSDT'},dataset_sha256='abc',status='NO_CANDIDATE',
                        period_pairs={'15m':{}},ranking=[{'timeframe':'15m','config':'15m|pattern_only|fixed_rr'}])
            (out/'report.json').write_text(json.dumps(report))
            pd.DataFrame([dict(config='15m|pattern_only|fixed_rr',net_pnl=.01,target_price=None)]).to_csv(out/'trades.csv',index=False)
            with patch('pattern_research.fit_model') as fit,patch('notifications.send_email') as send:
                first=finalize_pattern_mail(out)
                second=finalize_pattern_mail(out)
                fit.assert_not_called();send.assert_not_called()
            self.assertEqual(first['simulated_mail_count'],2)
            self.assertEqual(second['simulated_mail_count'],2)
            records=list((out/'simulated_outbox').glob('*.json'))
            self.assertEqual(len(records),2)
            self.assertTrue(all(json.loads(p.read_text())['status']=='SIMULATED_NOT_SENT' for p in records))

    def test_only_one_selected_configuration_can_access_holdout(self):
        """验证只允许原选定的一个配置访问冻结留出集。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);data=root/'data';demo(data,5000)
            bars=pd.read_csv(data/'bars.csv');bars.to_csv(data/'mark_bars.csv',index=False)
            digest=hashlib.sha256((data/'bars.csv').read_bytes()+(data/'funding.csv').read_bytes()).hexdigest()
            cut=int(bars.open_time.iloc[4000])
            baseline=root/'baseline.json';baseline.write_text(json.dumps(dict(dataset_sha256=digest,holdout_period_ms=[cut,int(bars.close_time.iloc[-1])])) )
            calls=[]
            def fake_engine(frame,signals=None,**kwargs):
                """返回确定性模拟账本供研究流程测试，避免重新训练或测试留出集。

                Args:
                    frame: 已对齐特征列与可知时间的当前数据表。
                    signals: 与信号K线对齐的-1/0/1方向及可选形态表。
                    **kwargs: 透传的命名参数；不能绕过当前调用的校验。

                Returns:
                    测试夹具、构造结果或断言辅助返回值；不影响真实数据。
                """
                calls.append((int(frame.open_time.iloc[0]),int(frame.close_time.iloc[-1])))
                size=len(frame)
                metric=dict(net_return=.01,max_drawdown=.01,max_drawdown_stress=.02,entries=25,liquidations=0)
                eq=pd.DataFrame({'close_time':frame.close_time.to_numpy(),'equity':np.linspace(1.,1.01,size)})
                return dict(metrics=metric,equity=eq,risk_points=[1.,.99,1.01],trades=pd.DataFrame([dict(net_pnl=.01,entry_time=int(frame.open_time.iloc[0]),exit_time=int(frame.close_time.iloc[-1]))]))
            with patch('pattern_research.fit_model',return_value=(None,[])),patch('pattern_execution.simulate_patterns',side_effect=fake_engine),patch('pattern_research.export_pattern_report'),patch('operation_report.export_operations_report',return_value={}),patch('operation_report.print_operations_summary'):
                result=run_pattern_research(data,root/'out',baseline,['15m'],['fixed_rr'])
            self.assertTrue(result['holdout_used'])
            self.assertEqual(len(result['ranking']),1)
            self.assertEqual(sum(first>=cut for first,last in calls),2)
            self.assertTrue(all(last<cut for first,last in calls if first<cut))
            self.assertTrue((root/'out/selected_holdout_equity.csv').exists())
            # Source is synthetic: even a positive mocked test never permits promotion.
            self.assertEqual(result['status'],'REJECTED')

    def test_new_backtest_registry_excludes_all_machine_learning_models(self):
        """确保新回测白名单只暴露确定性的多周期结构规则。

        Returns:
            无返回值；当ML或旧简易模型重新进入新任务注册表时断言失败。
        """
        self.assertEqual(MODEL_NAMES, ('analysis_dow123',))

    def test_partial_exit_mail_is_recorded_once_without_smtp(self):
        """验证减仓模拟通知仅记录一次且不调用SMTP。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root:
            out=Path(root);key='15m|pattern_only|fixed_rr'
            report=dict(source={'symbol':'BTCUSDT'},dataset_sha256='abc',status='NO_CANDIDATE',
                        period_pairs={'15m':{}},ranking=[{'timeframe':'15m','config':key}])
            (out/'report.json').write_text(json.dumps(report))
            pd.DataFrame([dict(config=key,net_pnl=.01)]).to_csv(out/'trades.csv',index=False)
            pd.DataFrame([dict(config=key,fold=1,position_id=1,event='PARTIAL_EXIT',time=123,
                               holding_hours=72,quantity=.25,remaining_quantity=.75)]).to_csv(out/'events.csv',index=False)
            with patch('notifications.send_email') as send:
                first=finalize_pattern_mail(out);second=finalize_pattern_mail(out)
                send.assert_not_called()
            self.assertEqual(first['simulated_mail_count'],3)
            self.assertEqual(second['simulated_mail_count'],3)
            records=[json.loads(p.read_text()) for p in (out/'simulated_outbox').glob('*.json')]
            self.assertEqual(len(records),3)
            partial=next(r for r in records if r['decision'].get('event')=='PARTIAL_EXIT')
            self.assertEqual(partial['decision']['remaining_quantity'],.75)

if __name__=='__main__':unittest.main()
