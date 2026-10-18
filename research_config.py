"""Shared, reviewable configuration for the manual swing research launcher."""
import json
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / 'config' / 'swing.json'


def load_config(path=None):
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
