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
    """将会话原始令牌映射为只供服务端查询的SHA256。

    Args:
        value: 待转换或校验的当前值，具体口径见函数用途。

    Returns:
        十六进制哈希字符串；不把原始令牌存入数据库。
    """
    return hashlib.sha256(value.encode()).hexdigest()

def verify_password(encoded: str, password: str) -> bool:
    """用Argon2验证密码，错误哈希和不匹配统一视为失败。

    Args:
        encoded: 已保存的Argon2密码哈希。
        password: 待核验的明文密码，只在内存使用且不写日志。

    Returns:
        匹配返回True，否则False；不记录输入内容。
    """
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False

def current_session(request: Request, db: DBSession = Depends(get_db)) -> Session:
    """校验会话存在、过期时间及写请求的CSRF令牌。

    Args:
        request: FastAPI请求对象；只读取当前路由需要的Cookie/来源等字段。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        有效Session；无效身份报401，写请求CSRF错误报403。
    """
    token = request.cookies.get('gq_session', '')
    session = db.get(Session, digest(token)) if token else None
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(401, '请先登录，会话有效期 12 小时')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), session.csrf_token):
            raise HTTPException(403, 'CSRF 校验失败')
    return session

def current_user(session: Session = Depends(current_session), db: DBSession = Depends(get_db)) -> User:
    """根据已验证会话读取当前用户对象。

    Args:
        session: 已验证且尚未过期的服务端会话。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        用户ORM对象；用户已删除时抛出401。
    """
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, '用户不存在')
    return user
