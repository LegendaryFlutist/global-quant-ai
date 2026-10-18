"""兼容旧模块 pattern_execution；业务实现位于 quant_lab.backtesting.pattern_execution。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.backtesting.pattern_execution')
