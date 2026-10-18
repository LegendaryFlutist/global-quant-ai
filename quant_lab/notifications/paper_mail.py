"""Local simulated delivery audit trail; never calls SMTP."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
from email.message import EmailMessage


def record_simulated(root, key, subject, body, decision, recipient='research@example.invalid'):
    """以稳定消息键写入本地JSON和EML模拟邮件记录。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。
        key: 稳定通知/账户配置标识，或当前记录的可选字段名。
        subject: 通知主题，不应包含凭据。
        body: 通知正文或已构建HTML正文，不应包含凭据。
        decision: 已作出的模拟决策及来源审计字典。
        recipient: 模拟通知收件人；默认占位地址不会用于真实投递。

    Returns:
        SIMULATED_NOT_SENT或ALREADY_SIMULATED；绝不调用SMTP。
    """
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256((recipient+'\0'+key).encode()).hexdigest()
    target=root/(digest+'.json')
    with (root/'simulation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.exists(): return 'ALREADY_SIMULATED'
        record=dict(id=digest,key=key,status='SIMULATED_NOT_SENT',recipient=recipient,
            recorded_at=datetime.now(timezone.utc).isoformat(),subject=subject,body=body,decision=decision,
            smtp_called=False,real_orders_enabled=False)
        message=EmailMessage()
        message['To']=recipient
        message['Subject']='[模拟发送] '+subject
        message.set_content('历史回放/模拟记录，未实际发送。\n\n'+body)
        (root/(digest+'.eml')).write_text(message.as_string(),encoding='utf-8')
        temp=target.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2));temp.replace(target)
    return record['status']
