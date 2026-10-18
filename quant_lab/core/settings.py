"""Load local mail settings without executing shell code or printing secrets."""
import os
from pathlib import Path
import shlex
from quant_lab.core.paths import PROJECT_ROOT

KEYS = {'QUANT_SMTP_HOST','QUANT_SMTP_PORT','QUANT_SMTP_SECURITY',
        'QUANT_SMTP_USER','QUANT_SMTP_PASSWORD','QUANT_MAIL_FROM','QUANT_MAIL_TO','QUANT_MAIL_MODE'}


def load_local_env(path=PROJECT_ROOT / '.env'):
    """解析本机邮件配置并补充尚未设置的环境变量。

    Args:
        path: 输入/输出文件路径；具体读写方向见函数用途。

    Returns:
        无返回值；文件缺失不处理，非法内容抛出脱敏后的ValueError。
    """
    if not path.exists():
        return
    values={}
    for number,line in enumerate(path.read_text().splitlines(),1):
        text=line.strip()
        if not text or text.startswith('#'):
            continue
        key,sep,value=text.removeprefix('export ').partition('=')
        key=key.strip()
        if not sep or key not in KEYS:
            raise ValueError(f'Invalid local mail configuration at line {number}')
        try:
            parts=shlex.split(value,comments=True)
        except ValueError:
            raise ValueError(f'Invalid quoting in mail configuration at line {number}') from None
        if len(parts)>1:
            raise ValueError(f'Quote configuration values at line {number}')
        values[key]=parts[0] if parts else ''
    for key,value in values.items():
        os.environ.setdefault(key,value)
