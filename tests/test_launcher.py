import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
import run as launcher


class LauncherTests(unittest.TestCase):
    def fixture(self,root,symbol):
        """构造当前报告/行情测试所需的独立临时输入和冻结边界。

        Args:
            root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。
            symbol: 精确产品代码；股票、ETF与相关永续不能混用。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
        data=root/'data'/(symbol.lower()+'_90d_connector');data.mkdir(parents=True)
        for name in ('bars.csv','funding.csv','mark_bars.csv'):(data/name).write_text('fixture\n')
        (data/'source.json').write_text(json.dumps(dict(symbol=symbol,synthetic=False,start=1609459200000,end=1609545599999)))
        digest=hashlib.sha256((data/'bars.csv').read_bytes()+(data/'funding.csv').read_bytes()).hexdigest()
        baseline=root/'runs'/(symbol.lower()+'_1h_15m_20261017');baseline.mkdir(parents=True)
        (baseline/'report.json').write_text(json.dumps(dict(dataset_sha256=digest,holdout_period_ms=[1,2])))

    def test_defaults_from_other_cwd_and_unique_output(self):
        """验证从不同目录启动仍定位项目且每次输出目录唯一。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'QUANT_MAIL_MODE':'real'}):
            root=Path(directory)/'project';other=Path(directory)/'other';other.mkdir()
            for symbol in launcher.DEFAULT_SYMBOLS:self.fixture(root,symbol)
            def research(data,out,baseline,**kwargs):
                """模拟研究调用并断言启动器传入的策略、目录与模拟模式。

                Args:
                    data: 当前函数契约下的时间顺序行情/特征表。
                    out: 本次输出目录；研究执行器通常要求目录尚不存在。
                    baseline: 当前测试/接口参数，含义见该函数的具体场景。
                    **kwargs: 透传的命名参数；不能绕过当前调用的校验。

                Returns:
                    测试夹具、构造结果或断言辅助返回值；不影响真实数据。
                """
                out.mkdir();(out/'comparison.html').write_text('fixture')
                self.assertTrue(data.is_absolute());self.assertTrue(baseline.is_absolute())
                self.assertEqual(kwargs['leverage'],5.)
                self.assertEqual(kwargs['margin_fraction'],.1)
                self.assertEqual(kwargs['timeframes'],list(launcher.TIMEFRAMES))
                self.assertEqual(kwargs['min_holding_hours'],24.)
                self.assertEqual(kwargs['max_holding_hours'],504.)
                self.assertEqual(kwargs['swing_min_range'],.02)
                self.assertEqual(kwargs['swing_max_range'],.5)
                self.assertEqual(kwargs['price_target_pct'],.05)
                self.assertEqual(kwargs['risk_atr_floor_hours'],24.)
                self.assertEqual(os.environ['QUANT_MAIL_MODE'],'simulate')
                return {'status':'NO_CANDIDATE'}
            original=Path.cwd()
            try:
                os.chdir(other)
                with patch('pattern_research.run_pattern_research',side_effect=research) as run,patch('notifications.send_email') as smtp,patch('structure_report.analyze_market'):
                    self.assertEqual(launcher.main([],root=root),0)
                    self.assertEqual(run.call_count,len(launcher.DEFAULT_SYMBOLS))
                    self.assertEqual(launcher.main(['--symbols','BTCUSDT'],root=root),0)
                    self.assertEqual(run.call_count,len(launcher.DEFAULT_SYMBOLS)+1)
                    smtp.assert_not_called()
            finally:os.chdir(original)
            self.assertEqual(len(list((root/'runs').glob('run_*'))),2)
            for batch in (root/'runs').glob('run_*'):
                snapshot=json.loads((batch/'config.json').read_text())
                self.assertEqual(snapshot['label_holding_hours'],48)
                self.assertTrue((batch/'runtime.json').is_file())

    def test_config_overrides_and_existing_output_are_checked(self):
        """验证研究配置覆盖规则且拒绝覆盖已存在输出。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        from research_config import load_config
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=root/'research.json'
            config.write_text(json.dumps({'price_target_pct':.08,'min_holding_hours':48.}))
            values=load_config(config)
            self.assertEqual(values['price_target_pct'],.08)
            self.assertEqual(values['min_holding_hours'],48.)
            self.assertEqual(values['max_holding_hours'],504.)
            config.write_text(json.dumps({'unknown_option':1}))
            with self.assertRaisesRegex(ValueError,'未知研究配置项'):load_config(config)
            for symbol in launcher.DEFAULT_SYMBOLS:self.fixture(root,symbol)
            out=root/'existing';out.mkdir()
            with patch('pattern_research.run_pattern_research') as research:
                self.assertEqual(launcher.main(['--out',str(out)],root=root),1)
                research.assert_not_called()

    def test_preflight_missing_data_or_changed_baseline(self):
        """验证缺数据或冻结数据指纹变化会阻止运行。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as directory,patch('pattern_research.run_pattern_research') as run:
            root=Path(directory)
            self.assertEqual(launcher.main(['--check'],root=root),1)
            for symbol in launcher.DEFAULT_SYMBOLS:self.fixture(root,symbol)
            self.assertEqual(launcher.main(['--check'],root=root),0)
            (root/'data/btcusdt_90d_connector/bars.csv').write_text('changed\n')
            self.assertEqual(launcher.main(['--check'],root=root),1)
            run.assert_not_called()

    def test_lab_without_arguments_dispatches_before_scientific_imports(self):
        """验证无参数旧入口在科学计算库加载前正确转发。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        fake=types.ModuleType('run');fake.main=Mock(return_value=0)
        lab=launcher.ROOT/'lab.py'
        with patch.dict(sys.modules,{'run':fake}),patch.object(sys,'argv',[str(lab)]):
            with self.assertRaises(SystemExit) as result:runpy.run_path(str(lab),run_name='__main__')
        self.assertEqual(result.exception.code,0)
        fake.main.assert_called_once_with()

if __name__=='__main__':unittest.main()
