from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession
from app.core.config import ALLOWED_ORIGINS, REDIS_URL
from app.core.db import get_db
from app.api import auth, assets, market, jobs, settings, system, risk

app=FastAPI(title='Global Quant AI',version='2.0.0-phase1',docs_url='/api/docs',openapi_url='/api/openapi.json')

@app.middleware('http')
async def boundaries(request:Request,call_next):
    """拦截非法写请求Origin并为API响应添加安全与禁止缓存头。

    Args:
        request: FastAPI请求对象；只读取当前路由需要的Cookie/来源等字段。
        call_next: 由ASGI框架注入的下一层请求处理器。

    Returns:
        下游响应或HTTP403；不放宽Cookie和交易边界。
    """
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
    """检查数据库可访问性和Redis连通性。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        健康/降级状态及固定关闭的实盘标志。
    """
    db.execute(text('SELECT 1'))
    try: redis_ok=Redis.from_url(REDIS_URL,socket_connect_timeout=2).ping()
    except Exception: redis_ok=False
    return {'status':'ok' if redis_ok else 'degraded','database':'connected','redis':bool(redis_ok),'phase':1,'live_enabled':False}


for module in (auth,assets,market,jobs,settings,system,risk):
    app.include_router(module.router)
