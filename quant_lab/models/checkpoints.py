"""恢复本机可信研究权重，保留原训练标准化尺度，不执行重新拟合。"""

import hashlib
import numpy as np
from quant_lab.models.deep_model import TorchGRUClassifier


def load_gru_checkpoint(path):
    """以受限Torch反序列化恢复可信本机pt，返回模型和文件SHA256；拒绝未知结构。"""
    import torch

    torch.set_num_threads(1)
    with torch.serialization.safe_globals([torch.torch_version.TorchVersion]):
        payload = torch.load(path, map_location="cpu", weights_only=True)
    if payload["architecture"] != "two_layer_gru" or payload["classes"] != [-1, 0, 1]:
        raise ValueError("Unsupported model contract")
    audit = payload["training"]
    model = TorchGRUClassifier(
        sequence_length=audit["sequence_length"],
        hidden_size=audit["hidden_size"],
        num_layers=audit["num_layers"],
        device="cpu",
    )
    model.device_ = "cpu"
    model.feature_names_ = payload["feature_names"]
    model.mean_ = np.asarray(payload["mean"], dtype=np.float32)
    model.scale_ = np.asarray(payload["scale"], dtype=np.float32)
    if (
        len(model.mean_) != len(model.feature_names_)
        or model.scale_.shape != model.mean_.shape
        or not np.isfinite(model.mean_).all()
        or not np.isfinite(model.scale_).all()
        or (model.scale_ <= 0).any()
    ):
        raise ValueError("Invalid saved normalization")
    model.training_audit_ = audit
    model.network_ = model._network(len(model.feature_names_))
    model.network_.load_state_dict(payload["state_dict"], strict=True)
    model.network_.eval()
    return model, hashlib.sha256(path.read_bytes()).hexdigest()
