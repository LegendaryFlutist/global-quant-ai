"""Phase 1 auth API."""
import secrets
from datetime import timedelta
from fastapi import APIRouter, Depends, Request, Response, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import select, delete
from sqlalchemy.orm import Session as DBSession
from app.core.config import COOKIE_SECURE, SESSION_HOURS, REDIS_URL
from app.core.db import get_db
from app.core.security import current_user, current_session, digest, hasher, verify_password, DUMMY_HASH
from app.domains.models import User, Session, Audit, utcnow


router=APIRouter()

@router.get('/api/auth/status')
def auth_status(request:Request,response:Response):
    token=secrets.token_urlsafe(32)
    response.set_cookie('gq_precsrf',token,httponly=True,secure=COOKIE_SECURE,samesite='strict',max_age=600,path='/api/auth')
    return {'csrf_token':token,'registration_enabled':False,'bootstrap':'本机 .runtime/bootstrap-admin.txt 保存初始登录信息'}

class Login(BaseModel):
    username:str=Field(min_length=1,max_length=80)
    password:str=Field(min_length=1,max_length=256)

@router.post('/api/auth/login')
def login(payload:Login,request:Request,response:Response,db:DBSession=Depends(get_db)):
    if not secrets.compare_digest(request.cookies.get('gq_precsrf','!'),request.headers.get('x-csrf-token','')):
        raise HTTPException(403,'登录 CSRF 校验失败')
    key='login:'+digest((request.client.host if request.client else '')+payload.username)
    try:
        redis=Redis.from_url(REDIS_URL,socket_connect_timeout=2)
        count=redis.incr(key)
        if count==1:redis.expire(key,300)
        if count>10:raise HTTPException(429,'登录尝试过多，请5分钟后再试')
    except HTTPException:raise
    except Exception:raise HTTPException(503,'登录限流服务暂不可用') from None
    user=db.scalar(select(User).where(User.username==payload.username))
    valid=verify_password(user.password_hash if user else DUMMY_HASH,payload.password)
    if not user or not valid:
        db.add(Audit(actor=payload.username,action='LOGIN_FAILED',detail={}));db.commit()
        raise HTTPException(401,'用户名或密码错误')
    token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
    db.execute(delete(Session).where(Session.expires_at<utcnow()))
    db.add(Session(token_hash=digest(token),user_id=user.id,csrf_token=csrf,expires_at=utcnow()+timedelta(hours=SESSION_HOURS)))
    db.add(Audit(actor=user.username,action='LOGIN',detail={}));db.commit();redis.delete(key)
    response.set_cookie('gq_session',token,httponly=True,secure=COOKIE_SECURE,samesite='strict',max_age=SESSION_HOURS*3600,path='/api')
    response.delete_cookie('gq_precsrf',path='/api/auth')
    return {'username':user.username,'role':user.role,'csrf_token':csrf}

@router.get('/api/auth/me')
def me(user:User=Depends(current_user),session:Session=Depends(current_session)):
    return {'username':user.username,'role':user.role,'csrf_token':session.csrf_token}

@router.post('/api/auth/logout')
def logout(response:Response,user:User=Depends(current_user),session:Session=Depends(current_session),db:DBSession=Depends(get_db)):
    db.delete(session);db.add(Audit(actor=user.username,action='LOGOUT',detail={}));db.commit();response.delete_cookie('gq_session',path='/api');return {'ok':True}

class PasswordChange(BaseModel):
    current_password:str=Field(max_length=256)
    new_password:str=Field(min_length=12,max_length=256)

@router.post('/api/auth/password')
def password_change(payload:PasswordChange,response:Response,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    if not verify_password(user.password_hash,payload.current_password):raise HTTPException(400,'原密码错误')
    user.password_hash=hasher.hash(payload.new_password)
    db.execute(delete(Session).where(Session.user_id==user.id));db.add(Audit(actor=user.username,action='PASSWORD_CHANGED',detail={}));db.commit()
    response.delete_cookie('gq_session',path='/api');return {'ok':True,'message':'密码已修改，全部会话已失效，请重新登录'}
