"""Local simulated delivery audit trail; never calls SMTP."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
from email.message import EmailMessage


def record_simulated(root, key, subject, body, decision, recipient='research@example.invalid'):
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
