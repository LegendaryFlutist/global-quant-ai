"""Shared, reviewable configuration for the manual swing research launcher."""
import json
from pathlib import Path

from quant_lab.core.paths import PROJECT_ROOT

DEFAULT_CONFIG = PROJECT_ROOT / 'config' / 'swing.json'


def load_config(path=None):
    """加载默认研究参数，并只允许已声明的键覆盖默认值。

    Args:
        path: 输入/输出文件路径；具体读写方向见函数用途。

    Returns:
        合并后的参数字典；未知键或非对象配置抛出ValueError。
    """
    defaults=json.loads(DEFAULT_CONFIG.read_text(encoding='utf-8'))
    if path is None or Path(path).resolve()==DEFAULT_CONFIG:
        return defaults
    overrides=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(overrides,dict):
        raise ValueError('研究配置必须是JSON对象')
    unknown=set(overrides)-set(defaults)
    if unknown:
        raise ValueError('未知研究配置项：'+', '.join(sorted(unknown)))
    return {**defaults,**overrides}
