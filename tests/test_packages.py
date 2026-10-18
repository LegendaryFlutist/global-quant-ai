"""验证旧导入/已保存模型和新包入口之间的兼容性。"""
import importlib
from pathlib import Path
import pickle
import unittest
from unittest.mock import patch


class PackageTests(unittest.TestCase):
    def test_legacy_and_canonical_modules_are_identical(self):
        """验证旧模块与新领域模块是同一对象，既有patch不会落在副本上。"""
        for old,new in [('technical','quant_lab.analysis.technical'),('pattern_research','quant_lab.research.pattern_research'),('deep_model','quant_lab.models.deep_model'),('run','quant_lab.cli.run')]:
            self.assertIs(importlib.import_module(old),importlib.import_module(new))
        with patch('pattern_research.run_pattern_research',return_value='patched'):
            self.assertEqual(importlib.import_module('quant_lab.research.pattern_research').run_pattern_research(),'patched')

    def test_legacy_pickle_path_restores_same_classifier(self):
        """验证重构前deep_model模块名的可信pickle仍能恢复同一个GRU类型。"""
        from quant_lab.models.deep_model import TorchGRUClassifier
        current=pickle.dumps(TorchGRUClassifier(),protocol=0)
        name=b'cquant_lab.models.deep_model\nTorchGRUClassifier\n'
        self.assertIn(name,current)
        legacy=current.replace(name,b'cdeep_model\nTorchGRUClassifier\n')
        restored=pickle.loads(legacy)
        self.assertIsInstance(restored,TorchGRUClassifier)
        self.assertEqual(restored.sequence_length,24)

    def test_project_resource_paths_stay_at_repository_root(self):
        """验证分包后配置、观察名单及.env仍定位根目录而不是包目录。"""
        from quant_lab.core.paths import PROJECT_ROOT
        from quant_lab.core.research_config import DEFAULT_CONFIG
        from quant_lab.core.settings import load_local_env
        from quant_lab.reports.trend_report import WATCHLIST
        self.assertEqual(PROJECT_ROOT,Path(__file__).resolve().parents[1])
        self.assertEqual(DEFAULT_CONFIG,PROJECT_ROOT/'config/swing.json')
        self.assertEqual(WATCHLIST,PROJECT_ROOT/'watchlist.json')
        self.assertEqual(load_local_env.__defaults__[0],PROJECT_ROOT/'.env')
