import hashlib
import secrets
from datetime import datetime, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.domains.models import Session, User

hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))

def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False

def current_session(request: Request, db: DBSession = Depends(get_db)) -> Session:
    token = request.cookies.get('gq_session', '')
    session = db.get(Session, digest(token)) if token else None
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(401, '请先登录，会话有效期 12 小时')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), session.csrf_token):
            raise HTTPException(403, 'CSRF 校验失败')
    return session

def current_user(session: Session = Depends(current_session), db: DBSession = Depends(get_db)) -> User:
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, '用户不存在')
    return user
