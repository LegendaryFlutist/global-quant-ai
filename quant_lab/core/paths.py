"""统一项目资源路径，避免分包后把配置或.env误读到包目录。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
