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
        cls.x=pd.DataFrame(np.random.default_rng(3).normal(size=(140,5)),columns=list('abcde'))
        cls.y=np.resize([-1,0,1],140)
        cls.model=TorchGRUClassifier(sequence_length=4,hidden_size=8,epochs=2).fit(cls.x.iloc[:90],cls.y[:90])

    def test_actual_training_and_probability_contract(self):
        m=self.model
        self.assertEqual(m.training_audit_['num_layers'],2)
        self.assertEqual(m.training_audit_['sequences'],87)
        self.assertEqual(len(m.loss_history_),2)
        self.assertTrue(np.isfinite(m.loss_history_).all())
        m.set_context(self.x.iloc[86:90]);p=m.predict_proba(self.x.iloc[90:110])
        np.testing.assert_allclose(p.sum(axis=1),1,atol=1e-6)
        self.assertEqual(m.classes_.tolist(),[-1,0,1])

    def test_prediction_prefix_and_scaler_ignore_future(self):
        m=self.model;m.set_context(self.x.iloc[86:90]);mean=m.mean_.copy();scale=m.scale_.copy()
        full=m.predict_proba(self.x.iloc[90:130]);prefix=m.predict_proba(self.x.iloc[90:110])
        np.testing.assert_allclose(full[:20],prefix,atol=1e-6)
        changed=self.x.iloc[90:130].copy();changed.iloc[20:]*=1000
        np.testing.assert_allclose(m.predict_proba(changed)[:20],prefix,atol=1e-6)
        np.testing.assert_array_equal(mean,m.mean_);np.testing.assert_array_equal(scale,m.scale_)

    def test_missing_candle_context_is_neutral_instead_of_compressed(self):
        m=self.model;m.set_context(self.x.iloc[86:90])
        query=self.x.iloc[95:100]
        p=m.predict_proba(query)
        np.testing.assert_array_equal(p[:3],np.tile([0,1,0],(3,1)))

    def test_checkpoint_and_cpu_reload_preserve_predictions(self):
        m=self.model;m.set_context(self.x.iloc[86:90]);expected=m.predict_proba(self.x.iloc[90:100])
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);m.export(p/'gru.pt');joblib.dump(m,p/'gru.joblib')
            restored=joblib.load(p/'gru.joblib')
            self.assertEqual(restored.device_,'cpu')
            np.testing.assert_allclose(restored.predict_proba(self.x.iloc[90:100]),expected,atol=1e-5)
            self.assertGreater((p/'gru.pt').stat().st_size,1000)

if __name__=='__main__':unittest.main()
