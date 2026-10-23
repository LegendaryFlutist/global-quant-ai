"""Small CPU contract tests for the new causal PyTorch research algorithms."""

import unittest

import numpy as np
import pandas as pd

from quant_lab.models.sequence_models import (
    PatchTSTClassifier,
    TemporalConvNetClassifier,
    TemporalFusionAttentionClassifier,
)


class SequenceModelTests(unittest.TestCase):
    """Verify each registered network trains and predicts on causal sequences."""

    def test_registered_models_fit_and_preserve_prefix_predictions(self):
        """Fit all three architectures and confirm future rows cannot alter past scores.

        Returns:
            No value; each model must return finite normalized short/flat/long scores.
        """
        rng = np.random.default_rng(73)
        values = rng.normal(size=(80, 4)).astype(np.float32)
        features = pd.DataFrame(values, columns=["rsi", "atr", "trend", "volume"])
        labels = pd.Series(np.tile([-1, 0, 1, 1, 0, -1, 0, 1], 10), index=features.index)
        train_x, valid_x = features.iloc[:64], features.iloc[64:]
        train_y = labels.iloc[:64]
        constructors = (
            PatchTSTClassifier,
            TemporalFusionAttentionClassifier,
            TemporalConvNetClassifier,
        )
        for constructor in constructors:
            with self.subTest(model=constructor.__name__):
                model = constructor(sequence_length=8, hidden_size=8, epochs=1,
                                    batch_size=32, seed=42, device="cpu").fit(train_x, train_y)
                model.set_context(train_x)
                full = model.predict_proba(valid_x)
                prefix = model.predict_proba(valid_x.iloc[:5])
                self.assertEqual(full.shape, (16, 3))
                self.assertTrue(np.isfinite(full).all())
                np.testing.assert_allclose(full.sum(axis=1), np.ones(len(full)), atol=1e-6)
                np.testing.assert_allclose(prefix, full[:5], atol=1e-6)
                self.assertEqual(model.training_audit_["normalization"], "training-only")
                self.assertFalse(model.training_audit_["future_inputs"])


if __name__ == "__main__":
    unittest.main()
