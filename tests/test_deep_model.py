import importlib.util
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
import joblib
from deep_model import TorchGRUClassifier


@unittest.skipUnless(importlib.util.find_spec('torch'),'PyTorch is provided by the NVIDIA container')
class DeepModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """准备可复用的确定性合成行情或小样本训练前置条件。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
        cls.x=pd.DataFrame(np.random.default_rng(3).normal(size=(140,5)),columns=list('abcde'))
        cls.y=np.resize([-1,0,1],140)
        cls.model=TorchGRUClassifier(sequence_length=4,hidden_size=8,epochs=2).fit(cls.x.iloc[:90],cls.y[:90])

    def test_actual_training_and_probability_contract(self):
        """验证真实小样本GRU训练及三类概率输出契约。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        m=self.model
        self.assertEqual(m.training_audit_['num_layers'],2)
        self.assertEqual(m.training_audit_['sequences'],87)
        self.assertEqual(len(m.loss_history_),2)
        self.assertTrue(np.isfinite(m.loss_history_).all())
        m.set_context(self.x.iloc[86:90]);p=m.predict_proba(self.x.iloc[90:110])
        np.testing.assert_allclose(p.sum(axis=1),1,atol=1e-6)
        self.assertEqual(m.classes_.tolist(),[-1,0,1])

    def test_prediction_prefix_and_scaler_ignore_future(self):
        """验证未来样本不影响过去预测和训练尺度。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        m=self.model;m.set_context(self.x.iloc[86:90]);mean=m.mean_.copy();scale=m.scale_.copy()
        full=m.predict_proba(self.x.iloc[90:130]);prefix=m.predict_proba(self.x.iloc[90:110])
        np.testing.assert_allclose(full[:20],prefix,atol=1e-6)
        changed=self.x.iloc[90:130].copy();changed.iloc[20:]*=1000
        np.testing.assert_allclose(m.predict_proba(changed)[:20],prefix,atol=1e-6)
        np.testing.assert_array_equal(mean,m.mean_);np.testing.assert_array_equal(scale,m.scale_)

    def test_missing_candle_context_is_neutral_instead_of_compressed(self):
        """验证缺失K线不会被压缩成连续序列，改为观望。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        m=self.model;m.set_context(self.x.iloc[86:90])
        query=self.x.iloc[95:100]
        p=m.predict_proba(query)
        np.testing.assert_array_equal(p[:3],np.tile([0,1,0],(3,1)))

    def test_checkpoint_and_cpu_reload_preserve_predictions(self):
        """验证模型检查点与CPU重载保持预测一致。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        m=self.model;m.set_context(self.x.iloc[86:90]);expected=m.predict_proba(self.x.iloc[90:100])
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);m.export(p/'gru.pt');joblib.dump(m,p/'gru.joblib')
            restored=joblib.load(p/'gru.joblib')
            self.assertEqual(restored.device_,'cpu')
            np.testing.assert_allclose(restored.predict_proba(self.x.iloc[90:100]),expected,atol=1e-5)
            self.assertGreater((p/'gru.pt').stat().st_size,1000)

if __name__=='__main__':unittest.main()
