"""Check inherited NVIDIA packages, GPU computation and report write access."""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
from pathlib import Path
import platform
import tempfile
import warnings


def check_runtime():
    """核验DGX容器的Python、GPU、CUDA与科学计算库可用性。

    Returns:
        实际环境摘要；缺失或不兼容条件会中止，不修改依赖。
    """
    import joblib
    import matplotlib
    import numpy as np
    import pandas as pd
    import scipy
    import sklearn
    import torch
    from sklearn.neural_network import MLPClassifier

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable: use the GPU-enabled project Compose service")
    torch.backends.cuda.matmul.fp32_precision = "ieee"
    generator = torch.Generator().manual_seed(42)
    left = torch.randn(256, 256, generator=generator)
    right = torch.randn(256, 256, generator=generator)
    actual = (left.cuda() @ right.cuda()).cpu()
    expected = left @ right
    torch.cuda.synchronize()
    if not torch.allclose(actual, expected, rtol=1e-4, atol=1e-4):
        raise RuntimeError("CUDA matrix result differs from the CPU reference")
    if "sample_weight" not in inspect.signature(MLPClassifier.fit).parameters:
        raise RuntimeError("MLPClassifier.fit must support sample_weight")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        MLPClassifier(hidden_layer_sizes=(4,), max_iter=2, random_state=42).fit(
            np.array([[0., 0.], [1., 1.], [0., 1.], [1., 0.]]),
            np.array([0, 1, 0, 1]), sample_weight=np.ones(4),
        )
    reports = Path("runs")
    reports.mkdir(exist_ok=True)
    with tempfile.TemporaryFile(dir=reports) as handle:
        handle.write(b"runtime write check")
    return {
        "status": "PASS", "architecture": platform.machine(),
        "python": platform.python_version(), "uid": os.getuid(),
        "torch": torch.__version__, "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "gpu_capability": list(torch.cuda.get_device_capability(0)),
        "cuda_matrix_test": "PASS",
        "cuda_matrix_max_abs_error": float((actual - expected).abs().max()),
        "tensorflow_installed": importlib.util.find_spec("tensorflow") is not None,
        "numpy": np.__version__, "pandas": pd.__version__,
        "scipy": scipy.__version__, "scikit_learn": sklearn.__version__,
        "matplotlib": matplotlib.__version__, "joblib": joblib.__version__,
        "weighted_mlp_fit": "PASS", "reports_writable": True,
        "mail_mode": os.environ.get("QUANT_MAIL_MODE"),
        "traditional_models_device": "CPU",
    }


def main():
    """运行环境自检并将实际结果打印/写入用户指定位置。

    Returns:
        无返回值；不启动训练或读取邮件配置。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save runtime check JSON")
    args = parser.parse_args()
    result = check_runtime()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")


if __name__ == "__main__":
    main()
