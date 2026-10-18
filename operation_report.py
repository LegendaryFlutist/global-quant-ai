"""兼容旧模块 operation_report；业务实现位于 quant_lab.reports.operation_report。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.reports.operation_report')
