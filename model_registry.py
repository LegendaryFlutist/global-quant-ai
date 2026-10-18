"""兼容旧模块 model_registry；业务实现位于 quant_lab.models.model_registry。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.models.model_registry')
