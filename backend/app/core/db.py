from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import DATABASE_URL

class Base(DeclarativeBase):
    pass

engine = create_engine(DATABASE_URL, pool_pre_ping=True, **({'connect_args': {'check_same_thread': False}} if DATABASE_URL.startswith('sqlite') else {}))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def get_db():
    """为一次依赖调用提供SQLAlchemy会话，并在结束时关闭。

    Returns:
        生成器，yield数据库会话；不自动代替业务提交事务。
    """
    with SessionLocal() as db:
        yield db
