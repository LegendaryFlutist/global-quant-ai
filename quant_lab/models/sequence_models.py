"""Causal PyTorch classifiers for PatchTST, TFT-style attention and TCN research."""

from pathlib import Path
import json

import numpy as np


class TorchSequenceClassifier:
    """Train a compact sequence classifier using only the supplied training fold."""

    algorithm = "sequence_classifier"

    def __init__(self, sequence_length=48, hidden_size=32, epochs=8, batch_size=128,
                 seed=42, device="auto"):
        """Record a fixed architecture budget; model weights are created during fit.

        Args:
            sequence_length: Number of contiguous closed-candle rows per sample.
            hidden_size: Hidden feature width; must be divisible by four attention heads.
            epochs: Fixed training passes; validation or holdout returns never drive stopping.
            batch_size: Maximum number of training or inference windows per tensor batch.
            seed: Fixed random seed used to initialize CPU/GPU training.
            device: `auto`, `cpu` or a caller-selected valid PyTorch device string.

        Returns:
            No value; stores parameters and delays importing PyTorch until training.
        """
        if sequence_length < 8 or hidden_size < 8 or hidden_size % 4 or epochs < 1 or batch_size < 1:
            raise ValueError("Invalid time-series model dimensions or training budget")
        self.sequence_length = sequence_length
        self.hidden_size = hidden_size
        self.epochs = epochs
        self.batch_size = batch_size
        self.seed = seed
        self.requested_device = device
        self.classes_ = np.array([-1, 0, 1])
        self.context_ = None

    def _network(self, features):
        """Build the subclass architecture for the supplied feature count.

        Args:
            features: Number of numeric features per closed candle.

        Returns:
            An untrained `torch.nn.Module` with three direction logits.
        """
        raise NotImplementedError

    def fit(self, X, y):
        """Fit scaler and sequence network on a chronological training fold only.

        Args:
            X: Finite DataFrame of ordered per-candle features; index is bar position.
            y: Aligned -1/0/1 training labels whose label end times precede validation.

        Returns:
            This fitted estimator with train-only scale and a training audit.
        """
        import torch

        torch.manual_seed(self.seed)
        torch.set_num_threads(1)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        self.device_ = "cuda" if self.requested_device == "auto" and torch.cuda.is_available() else (
            "cpu" if self.requested_device == "auto" else self.requested_device
        )
        self.feature_names_ = list(X.columns)
        values = np.asarray(X, dtype=np.float32)
        labels = np.asarray(y, dtype=np.int64)
        indices = np.asarray(X.index, dtype=np.int64)
        if len(values) != len(labels) or not np.isfinite(values).all() or not np.isin(labels, [-1, 0, 1]).all():
            raise ValueError("Training features and direction labels must be finite and aligned")
        self.mean_ = values.mean(axis=0)
        self.scale_ = values.std(axis=0)
        self.scale_[self.scale_ < 1e-6] = 1.0
        scaled = np.clip((values - self.mean_) / self.scale_, -12, 12)
        windows, targets, ends = [], [], []
        for end in range(self.sequence_length - 1, len(values)):
            left = end - self.sequence_length + 1
            if np.all(np.diff(indices[left:end + 1]) == 1):
                windows.append(scaled[left:end + 1])
                targets.append(int(labels[end] + 1))
                ends.append(int(indices[end]))
        if len(windows) < 16:
            raise ValueError(f"Not enough contiguous training sequences for {self.algorithm}")
        target_values = np.asarray(targets, dtype=np.int64)
        counts = np.bincount(target_values, minlength=3)
        class_weights = len(target_values) / (3 * np.maximum(counts, 1))
        x = torch.tensor(np.stack(windows), dtype=torch.float32, device=self.device_)
        target = torch.tensor(target_values, dtype=torch.long, device=self.device_)
        self.network_ = self._network(values.shape[1]).to(self.device_)
        loss_fn = torch.nn.CrossEntropyLoss(
            weight=torch.tensor(class_weights, dtype=torch.float32, device=self.device_),
        )
        optimizer = torch.optim.AdamW(self.network_.parameters(), lr=0.001, weight_decay=0.01)
        self.loss_history_ = []
        for _epoch in range(self.epochs):
            self.network_.train()
            total_loss = 0.0
            for start in range(0, len(x), self.batch_size):
                xb, yb = x[start:start + self.batch_size], target[start:start + self.batch_size]
                optimizer.zero_grad(set_to_none=True)
                loss = loss_fn(self.network_(xb), yb)
                if not torch.isfinite(loss):
                    raise ValueError(f"Nonfinite loss while training {self.algorithm}")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.network_.parameters(), 1.0)
                optimizer.step()
                total_loss += float(loss.detach()) * len(xb)
            self.loss_history_.append(total_loss / len(x))
        self.training_audit_ = {
            "algorithm": self.algorithm,
            "device": self.device_,
            "torch": torch.__version__,
            "sequence_length": self.sequence_length,
            "hidden_size": self.hidden_size,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "seed": self.seed,
            "sequences": len(windows),
            "class_counts": counts.tolist(),
            "first_window_end": ends[0],
            "last_window_end": ends[-1],
            "loss_history": self.loss_history_,
            "parameters": sum(parameter.numel() for parameter in self.network_.parameters()),
            "normalization": "training-only",
            "future_inputs": False,
            "gpu_name": torch.cuda.get_device_name(0) if self.device_.startswith("cuda") else None,
        }
        self.set_context(X.tail(self.sequence_length - 1))
        return self

    def set_context(self, X):
        """Save only past unlabeled features needed to form the next causal window.

        Args:
            X: Historical feature rows strictly before the inference fold.

        Returns:
            No value; retains at most sequence_length-1 rows in model feature order.
        """
        if not hasattr(self, "feature_names_"):
            raise ValueError("Fit the model before setting inference context")
        self.context_ = X[self.feature_names_].tail(self.sequence_length - 1).copy()

    def predict_proba(self, X):
        """Return short/flat/long softmax scores using only each row's preceding window.

        Args:
            X: Ordered finite feature rows to score; each output corresponds to one bar.

        Returns:
            Float matrix in `classes_` order; rows without contiguous context are neutral.
        """
        import pandas as pd
        import torch

        X = X[self.feature_names_]
        context = self.context_ if self.context_ is not None else X.iloc[:0]
        if len(X):
            context = context[context.index < X.index[0]]
        combined = pd.concat([context, X])
        values = np.asarray(combined, dtype=np.float32)
        scaled = np.clip((values - self.mean_) / self.scale_, -12, 12)
        indices = np.asarray(combined.index, dtype=np.int64)
        probabilities = np.tile([0.0, 1.0, 0.0], (len(X), 1))
        windows, positions = [], []
        for offset in range(len(X)):
            end = len(context) + offset
            left = end - self.sequence_length + 1
            if left >= 0 and np.all(np.diff(indices[left:end + 1]) == 1):
                window = scaled[left:end + 1]
                if np.isfinite(window).all():
                    windows.append(window)
                    positions.append(offset)
        if windows:
            self.network_.eval()
            with torch.inference_mode():
                for left in range(0, len(windows), self.batch_size):
                    batch = torch.tensor(np.stack(windows[left:left + self.batch_size]),
                                         dtype=torch.float32, device=self.device_)
                    scores = torch.softmax(self.network_(batch), dim=1).cpu().numpy()
                    probabilities[np.asarray(positions[left:left + self.batch_size])] = scores
        return probabilities

    def export(self, path):
        """Write CPU weights and a JSON training audit without exposing live services.

        Args:
            path: Destination `.pt` checkpoint path within the current research report.

        Returns:
            No value; writes architecture, fitted scaler, classes and training metadata.
        """
        import torch

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "architecture": self.algorithm,
            "state_dict": {key: value.detach().cpu() for key, value in self.network_.state_dict().items()},
            "feature_names": self.feature_names_,
            "mean": self.mean_.tolist(),
            "scale": self.scale_.tolist(),
            "classes": self.classes_.tolist(),
            "training": self.training_audit_,
        }, path)
        path.with_suffix(".json").write_text(json.dumps(self.training_audit_, ensure_ascii=False, indent=2))

    def __getstate__(self):
        """Serialize weights on CPU rather than storing an active CUDA module."""
        state = self.__dict__.copy()
        network = state.pop("network_", None)
        if network is not None:
            state["_weights"] = {key: value.detach().cpu() for key, value in network.state_dict().items()}
        return state

    def __setstate__(self, state):
        """Restore a trusted local CPU model checkpoint without fitting it again."""
        weights = state.pop("_weights", None)
        self.__dict__.update(state)
        if weights is not None:
            self.device_ = "cpu"
            self.network_ = self._network(len(self.feature_names_))
            self.network_.load_state_dict(weights, strict=True)
            self.network_.eval()


class PatchTSTClassifier(TorchSequenceClassifier):
    """Patch-based Transformer classifier for closed-candle multivariate windows."""

    algorithm = "PatchTST"

    def _network(self, features):
        """Build patch embeddings and encoder attention over the known past window."""
        import torch

        patch_length = 4
        stride = 2
        patch_count = (self.sequence_length - patch_length) // stride + 1
        hidden_size = self.hidden_size

        class Network(torch.nn.Module):
            def __init__(net):
                """Allocate patch projection, positional embeddings and Transformer blocks."""
                super().__init__()
                net.project = torch.nn.Linear(features * patch_length, hidden_size)
                net.position = torch.nn.Parameter(torch.zeros(1, patch_count, hidden_size))
                encoder = torch.nn.TransformerEncoderLayer(
                    d_model=hidden_size, nhead=4, dim_feedforward=hidden_size * 2,
                    dropout=0.1, batch_first=True, norm_first=True,
                )
                net.encoder = torch.nn.TransformerEncoder(encoder, num_layers=2)
                net.norm = torch.nn.LayerNorm(hidden_size)
                net.head = torch.nn.Linear(hidden_size, 3)

            def forward(net, x):
                """Map batch×time×feature windows to terminal short/flat/long logits."""
                patches = x.unfold(1, patch_length, stride).permute(0, 1, 3, 2)
                patches = patches.reshape(len(x), patch_count, patch_length * features)
                tokens = net.project(patches) + net.position
                encoded = net.encoder(tokens)
                return net.head(net.norm(encoded[:, -1]))

        return Network()


class TemporalFusionAttentionClassifier(TorchSequenceClassifier):
    """Compact TFT-style variable selection, gated LSTM and temporal attention."""

    algorithm = "TFT-Attention"

    def _network(self, features):
        """Build variable-selection and attention layers over known historical features."""
        import torch

        hidden_size = self.hidden_size

        class Network(torch.nn.Module):
            def __init__(net):
                """Allocate feature gates, recurrent encoder, attention and classifier."""
                super().__init__()
                net.selection = torch.nn.Linear(features, features)
                net.project = torch.nn.Sequential(
                    torch.nn.Linear(features, hidden_size), torch.nn.GELU(), torch.nn.Dropout(0.1),
                )
                net.lstm = torch.nn.LSTM(hidden_size, hidden_size, batch_first=True)
                net.attention = torch.nn.MultiheadAttention(hidden_size, 4, dropout=0.1, batch_first=True)
                net.gate = torch.nn.Sequential(torch.nn.Linear(hidden_size, hidden_size), torch.nn.Sigmoid())
                net.norm = torch.nn.LayerNorm(hidden_size)
                net.head = torch.nn.Sequential(
                    torch.nn.Linear(hidden_size, hidden_size), torch.nn.GELU(), torch.nn.Dropout(0.1),
                    torch.nn.Linear(hidden_size, 3),
                )

            def forward(net, x):
                """Fuse selected feature streams and return logits for each window end."""
                weights = torch.softmax(net.selection(x), dim=-1)
                encoded, _ = net.lstm(net.project(x * weights * features))
                attended, _ = net.attention(encoded, encoded, encoded, need_weights=False)
                fused = net.norm(encoded + net.gate(attended) * attended)
                return net.head(fused[:, -1])

        return Network()


class TemporalConvNetClassifier(TorchSequenceClassifier):
    """Residual dilated temporal convolution network for direction classification."""

    algorithm = "TCN"

    def _network(self, features):
        """Build residual dilated Conv1d blocks over a finite historical window."""
        import torch

        hidden_size = self.hidden_size

        class ResidualBlock(torch.nn.Module):
            def __init__(block, dilation):
                """Allocate same-length residual convolutions at one dilation rate."""
                super().__init__()
                padding = dilation
                block.conv1 = torch.nn.Conv1d(features if dilation == 1 else hidden_size,
                    hidden_size, kernel_size=3, padding=padding, dilation=dilation)
                block.conv2 = torch.nn.Conv1d(hidden_size, hidden_size, kernel_size=3,
                    padding=padding, dilation=dilation)
                block.skip = torch.nn.Conv1d(features if dilation == 1 else hidden_size,
                    hidden_size, kernel_size=1)
                block.norm = torch.nn.GroupNorm(1, hidden_size)
                block.dropout = torch.nn.Dropout(0.1)

            def forward(block, x):
                """Apply two dilated convolutions and a gated-width residual projection."""
                residual = block.skip(x)
                value = torch.nn.functional.gelu(block.conv1(x))
                value = block.dropout(value)
                value = block.conv2(value)
                return torch.nn.functional.gelu(block.norm(value + residual))

        class Network(torch.nn.Module):
            def __init__(net):
                """Create a three-level dilation stack and three-class prediction head."""
                super().__init__()
                net.blocks = torch.nn.ModuleList([ResidualBlock(rate) for rate in (1, 2, 4)])
                net.head = torch.nn.Sequential(
                    torch.nn.LayerNorm(hidden_size), torch.nn.Linear(hidden_size, 16),
                    torch.nn.GELU(), torch.nn.Dropout(0.1), torch.nn.Linear(16, 3),
                )

            def forward(net, x):
                """Read the final known candle representation for direction logits."""
                value = x.transpose(1, 2)
                for block in net.blocks:
                    value = block(value)
                return net.head(value[:, :, -1])

        return Network()
