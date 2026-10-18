"""Opt-in encrypted SMTP notifications; never accepts exchange credentials."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr


class NotificationError(RuntimeError):
    pass


def send_email(subject, body):
    if os.environ.get('QUANT_MAIL_MODE','real')!='real':
        raise NotificationError('Real SMTP disabled; QUANT_MAIL_MODE=simulate records via deliver_once only')
    config = {key:os.environ.get('QUANT_'+key,'') for key in
              ('SMTP_HOST','SMTP_USER','SMTP_PASSWORD','MAIL_FROM','MAIL_TO')}
    if not all(config.values()):
        raise NotificationError('Missing QUANT_SMTP_* / QUANT_MAIL_* configuration; no email sent')
    for key in ('MAIL_FROM','MAIL_TO'):
        address = config[key]
        if '\n' in address or '\r' in address or parseaddr(address)[1] != address or address.count('@') != 1:
            raise NotificationError('Use a single valid email address for sender and recipient')
    mode = os.environ.get('QUANT_SMTP_SECURITY','ssl')
    if mode not in ('ssl','starttls'):
        raise NotificationError('SMTP requires ssl or starttls')
    try:
        port = int(os.environ.get('QUANT_SMTP_PORT','465' if mode=='ssl' else '587'))
    except ValueError:
        raise NotificationError('Invalid SMTP port') from None
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = config['MAIL_FROM']
    message['To'] = config['MAIL_TO']
    message['Date'] = formatdate(localtime=False)
    message['Message-ID'] = make_msgid()
    message.set_content(body)
    try:
        client = (smtplib.SMTP_SSL(config['SMTP_HOST'],port,timeout=30,context=ssl.create_default_context())
                  if mode=='ssl' else smtplib.SMTP(config['SMTP_HOST'],port,timeout=30))
        with client as smtp:
            if mode=='starttls':
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(config['SMTP_USER'],config['SMTP_PASSWORD'])
            smtp.send_message(message)
    except (OSError,smtplib.SMTPException):
        # Server diagnostics may contain login or message data; don't print them.
        raise NotificationError('SMTP delivery failed; check network, SMTP service and local authorization code') from None


def deliver_once(key, subject, body, state_dir):
    """Serialize delivery and remember only successfully submitted messages.

    A crash after SMTP accepts but before local commit may cause a duplicate.
    State is per recipient; failures remain retryable.
    """
    mode=os.environ.get('QUANT_MAIL_MODE','real')
    if mode=='simulate':
        from paper_mail import record_simulated
        return record_simulated(Path(state_dir)/'simulated_outbox',key,subject,body,
            {'mode':'SIMULATED_DELIVERY','real_orders_enabled':False},
            os.environ.get('QUANT_MAIL_TO','research@example.invalid'))
    if mode!='real': raise NotificationError('Invalid QUANT_MAIL_MODE')
    root = Path(state_dir)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    digest = hashlib.sha256((os.environ.get('QUANT_MAIL_TO','')+'\0'+key).encode()).hexdigest()
    with (root/'delivery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        marker = root/(digest+'.json')
        if marker.exists():
            return 'ALREADY_SENT'
        send_email(subject,body)
        temporary = root/(digest+'.tmp')
        temporary.write_text(json.dumps({'key':key,'status':'SMTP_ACCEPTED'},ensure_ascii=False))
        temporary.replace(marker)
    return 'SMTP_ACCEPTED'


def report_message(path):
    report = json.loads(Path(path).read_text())
    symbol = report.get('source',{}).get('symbol') or report.get('instrument',{}).get('symbol','UNKNOWN')
    status = report.get('status','ANALYSIS_ONLY')
    subject = f'[量化研究报告] {symbol} {status}'
    body = ('这是研究/分析运行报告，不是即时买卖信号。由你手动决定操作，程序不下单。\n'
            f'报告路径：{Path(path).resolve()}\n'+json.dumps(report,ensure_ascii=False,indent=2))
    return subject,body


def preview_report(path):
    subject,body = report_message(path)
    target=Path(path).with_name('report_email_preview.txt')
    target.write_text(subject+'\n\n'+body,encoding='utf-8')
    return target


def notify_report(path, state_dir):
    subject,body = report_message(path)
    key = 'report:'+hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return deliver_once(key,subject,body,state_dir)
