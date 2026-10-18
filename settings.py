"""兼容旧模块 settings；业务实现位于 quant_lab.core.settings。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.core.settings')
