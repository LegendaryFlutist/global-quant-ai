"""兼容旧模块 paper_mail；业务实现位于 quant_lab.notifications.paper_mail。"""
from quant_lab.compat import alias_legacy
alias_legacy(__name__, 'quant_lab.notifications.paper_mail')
