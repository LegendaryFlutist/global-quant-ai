#!/usr/bin/env python3
"""Collect nonsecret delivery evidence from the running platform."""
import json
import subprocess
from pathlib import Path

CODE = r'''
import json,sys,platform
from pathlib import Path
import fastapi,sqlalchemy,rq,pyarrow
from sqlalchemy import select,func,text
from app.core.db import SessionLocal
from app.domains.models import Asset,MarketData,Provider,Task,User
from app.services.market_data import read_dataset
with SessionLocal() as db:
    datasets=list(db.scalars(select(MarketData)))
    checked=[]
    for item in datasets:
        rows=read_dataset(item)
        assert len(rows)==item.row_count
        checked.append({'asset_id':item.asset_id,'interval':item.interval,'rows':len(rows),'sha256':item.sha256,'last_close_ms':item.last_close_ms})
    record={'python':sys.version.split()[0],'machine':platform.machine(),'fastapi':fastapi.__version__,'sqlalchemy':sqlalchemy.__version__,'rq':rq.__version__,'pyarrow':pyarrow.__version__,'database':db.bind.url.database,'migration':db.scalar(text('SELECT version_num FROM alembic_version')),'assets':db.scalar(select(func.count()).select_from(Asset)),'users':db.scalar(select(func.count()).select_from(User)),'datasets':checked,'providers':[{'id':p.id,'status':p.status,'detail':p.detail} for p in db.scalars(select(Provider))],'tasks':[{'id':t.id,'kind':t.kind,'status':t.status,'attempts':t.attempts,'result':t.result} for t in db.scalars(select(Task))],'source_image_contains_local_env':Path('/app/.env').exists(),'source_image_contains_runtime':Path('/app/.runtime').exists(),'live_execution':False}
    print(json.dumps(record,ensure_ascii=False))
'''

def main():
    result=subprocess.run(['docker','exec','-i','quant-lab-api-1','python','-'],input=CODE,text=True,capture_output=True,check=True)
    runtime=json.loads(result.stdout)
    assert not runtime['source_image_contains_local_env'] and not runtime['source_image_contains_runtime']
    assert runtime['assets']>=80 and len(runtime['datasets'])==4 and runtime['migration']=='0001'
    output=Path('runs/platform_phase1');output.mkdir(parents=True,exist_ok=True)
    (output/'runtime-check.json').write_text(json.dumps({'status':'PASS',**runtime},ensure_ascii=False,indent=2))
    print(json.dumps({'status':'PASS','assets':runtime['assets'],'datasets':len(runtime['datasets']),'candles':sum(d['rows'] for d in runtime['datasets']),'python':runtime['python'],'migration':runtime['migration']},ensure_ascii=False))

if __name__=='__main__':main()
