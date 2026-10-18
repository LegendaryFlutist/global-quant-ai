from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession
from app.core.config import ALLOWED_ORIGINS, REDIS_URL
from app.core.db import get_db
from app.api import auth, assets, market, jobs, settings, system

app=FastAPI(title='Global Quant AI',version='2.0.0-phase1',docs_url='/api/docs',openapi_url='/api/openapi.json')

@app.middleware('http')
async def boundaries(request:Request,call_next):
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({'detail':'请求来源不被允许'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    return response

@app.get('/api/health')
def health(db:DBSession=Depends(get_db)):
    db.execute(text('SELECT 1'))
    try: redis_ok=Redis.from_url(REDIS_URL,socket_connect_timeout=2).ping()
    except Exception: redis_ok=False
    return {'status':'ok' if redis_ok else 'degraded','database':'connected','redis':bool(redis_ok),'phase':1,'live_enabled':False}


for module in (auth,assets,market,jobs,settings,system):
    app.include_router(module.router)
