"""兼容旧模块 signals；业务实现位于 quant_lab.notifications.signals。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.notifications.signals')
