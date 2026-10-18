"""兼容旧模块 deep_model；业务实现位于 quant_lab.models.deep_model。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.models.deep_model')
