"""Account-owned research jobs stay private even in the shared tasks endpoint."""

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hasher
from app.domains.models import Task, User


def test_tasks_hide_other_owner_and_ownerless_private_jobs(logged_in):
    """Two authenticated fixture users see only their own model/simulation jobs plus public data."""
    password = "Fixture-task-owner-password-123"
    with SessionLocal() as db:
        first = db.scalar(select(User).where(User.username == "test_admin"))
        second = User(username="other_task_owner", password_hash=hasher.hash(password), role="admin")
        db.add(second)
        db.flush()
        for kind in ("RUN_SIMULATION_BACKTEST", "RUN_FULL_RESEARCH"):
            for label, owner in (("first", first.id), ("second", second.id), ("missing", None)):
                db.add(Task(id=f"{kind}-{label}", kind=kind, status="SUCCEEDED",
                            parameters={"owner_user_id": owner} if owner else {},
                            result={"private_report": label}))
        db.add(Task(id="public-dataset", kind="INGEST_DATASET", status="SUCCEEDED",
                    parameters={"asset_id": "coinbase_spot:BTC-USD"}, result={"research_only": True}))
        db.commit()
    first_ids = {item["id"] for item in logged_in.get("/api/tasks").json()}
    assert first_ids == {"RUN_SIMULATION_BACKTEST-first", "RUN_FULL_RESEARCH-first", "public-dataset"}
    assert logged_in.post("/api/auth/logout").status_code == 200
    csrf = logged_in.get("/api/auth/status").json()["csrf_token"]
    login = logged_in.post("/api/auth/login", json={"username": "other_task_owner", "password": password}, headers={"X-CSRF-Token": csrf})
    assert login.status_code == 200
    second_ids = {item["id"] for item in logged_in.get("/api/tasks").json()}
    assert second_ids == {"RUN_SIMULATION_BACKTEST-second", "RUN_FULL_RESEARCH-second", "public-dataset"}
