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
    with TestClient(app) as c:yield c

@pytest.fixture
def logged_in(client):
    csrf=client.get('/api/auth/status').json()['csrf_token']
    response=client.post('/api/auth/login',json={'username':'test_admin','password':'Testing-only-password-123'},headers={'X-CSRF-Token':csrf})
    assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    return client
