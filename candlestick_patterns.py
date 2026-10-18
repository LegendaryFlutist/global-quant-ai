"""兼容旧模块 candlestick_patterns；业务实现位于 quant_lab.analysis.candlestick_patterns。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.analysis.candlestick_patterns')
