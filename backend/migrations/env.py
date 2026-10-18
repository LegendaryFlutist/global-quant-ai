from alembic import context
from app.core.db import Base, engine
from app.domains import models

def run():
    """连接当前数据库并按Alembic上下文执行已声明的迁移。

    Returns:
        无返回值；仅按固定revision执行，不重置已有数据库。
    """
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
run()
