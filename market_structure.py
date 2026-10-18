"""兼容旧模块 market_structure；业务实现位于 quant_lab.analysis.market_structure。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.analysis.market_structure')
