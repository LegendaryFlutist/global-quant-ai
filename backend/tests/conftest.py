import os
import shutil
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import text
from app.core.config import DATABASE_URL, DATA_ROOT, REDIS_URL
from app.core.db import Base, engine, SessionLocal
from app.core.security import hasher
from app.domains.models import User
from app.bootstrap import seed
from app.main import app

@pytest.fixture(autouse=True)
def isolated_database():
    """只对quant_test重建测试表、清空隔离Redis并创建测试用户。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    assert '/quant_test' in DATABASE_URL, 'Tests must never run against the user database'
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Redis.from_url(REDIS_URL).flushdb()
    DATA_ROOT.mkdir(exist_ok=True,parents=True)
    with SessionLocal() as db:
        seed(db)
        db.add(User(username='test_admin',password_hash=hasher.hash('Testing-only-password-123'),role='admin'));db.commit()
    yield

@pytest.fixture
def client():
    """建立FastAPI测试客户端，不启动对外网络服务。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    with TestClient(app) as c:yield c

@pytest.fixture
def logged_in(client):
    """通过真实登录握手建立测试会话并配置CSRF请求头。

    Args:
        client: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    csrf=client.get('/api/auth/status').json()['csrf_token']
    response=client.post('/api/auth/login',json={'username':'test_admin','password':'Testing-only-password-123'},headers={'X-CSRF-Token':csrf})
    assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    return client
