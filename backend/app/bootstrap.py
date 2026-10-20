"""首次管理员初始化和后台任务恢复；资产登记由catalog服务负责。"""
import os
import secrets
from pathlib import Path
from sqlalchemy import select
from app.core.db import SessionLocal
from app.core.security import hasher
from app.domains.models import User, Audit, MarketData

from app.domains.asset_catalog import PRIORITY
from app.services.catalog import seed

def main():
    """幂等初始化本机管理员，恢复未入队任务并选择真实行情采集路径。

    Returns:
        无返回值；只在无用户时生成本机初始密码，不打印密码。
    """
    with SessionLocal() as db:
        seed(db)
        if db.scalar(select(User.id).limit(1)) is None:
            password=secrets.token_urlsafe(24)
            path=Path(os.environ.get('BOOTSTRAP_FILE','/secrets/bootstrap-admin.txt'))
            path.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:f.write('用户名：admin\n密码：'+password+'\n仅保存在本机；登录后可在设置中修改密码。\n')
            db.add(User(username='admin',password_hash=hasher.hash(password),role='admin'))
            db.add(Audit(actor='system',action='BOOTSTRAP_ADMIN',detail={'credential':'local file only'}));db.commit()
        # Resume queue publication after Redis outage, and enqueue seed imports once.
        from app.services.jobs import enqueue, queue, execute
        from app.domains.models import Task, MarketData
        from rq import Retry
        for task in db.scalars(select(Task).where(Task.status.in_(['QUEUED','QUEUE_ERROR']))):
            if task.kind == 'RUN_FULL_RESEARCH':
                continue  # The dedicated NVIDIA runner claims this durable task.
            if queue().fetch_job(task.id) is None:
                timeout=21600 if task.kind=='FETCH_BINANCE_ARCHIVES' else 900
                queue().enqueue(execute,task.id,job_id=task.id,retry=Retry(max=2,interval=[5,30]),job_timeout=timeout);task.status='QUEUED'
        db.commit()
        from app.services.startup_market_data import queue_missing_binance_archives
        queue_missing_binance_archives(db)
        for symbol,*_ in PRIORITY:
            aid='binance_usdm:'+symbol
            kinds=['IMPORT_LOCAL','FETCH_HISTORY']
            from app.core.config import RESEARCH_ROOT
            if db.scalar(select(MarketData.id).where(MarketData.asset_id==aid)) is None and not any(t.parameters.get('asset_id')==aid for t in db.scalars(select(Task).where(Task.kind.in_(kinds)))):
                if (RESEARCH_ROOT/'data/priority_analysis_latest.json').is_file():
                    enqueue(db,'IMPORT_LOCAL',{'asset_id':aid})
                else:
                    enqueue(db,'FETCH_HISTORY',{'asset_id':aid,'interval':'15m','days':90})
        enqueue(db,'VERIFY_ASSETS',{})

if __name__=='__main__':main()
