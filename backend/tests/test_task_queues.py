"""Exercise queue isolation and restart compatibility against isolated Redis/PostgreSQL."""
from datetime import timedelta

from redis.exceptions import ConnectionError
from rq import SimpleWorker
from rq.job import JobStatus

from app.core.db import SessionLocal
from app.domains.models import Task, utcnow
from app.services import jobs
from app.services.task_queue import DATASET_QUEUE, LEGACY_QUEUE, find_job, queue


def test_dataset_worker_completes_while_legacy_backlog_is_untouched(logged_in, monkeypatch):
    """Run one authenticated dataset request past 50 queued legacy tasks without network I/O."""
    monkeypatch.setattr(jobs, "ingest_dataset", lambda db, task: {"fixture": True, "rows": 1})
    with SessionLocal() as db:
        old_ids = [jobs.enqueue(db, "FETCH_HISTORY", {"fixture": index}).id for index in range(50)]
    payload = {"asset_id": "coinbase_spot:BTC-USD", "provider_id": "coinbase_exchange",
               "start_date": "2024-01-01", "end_date": "2024-01-02"}
    # Clients cannot escape server routing or choose another worker.
    assert logged_in.post("/api/datasets/ingest", json={**payload, "queue": LEGACY_QUEUE}).status_code == 422
    created = logged_in.post("/api/datasets/ingest", json=payload)
    assert created.status_code == 202
    job = find_job(created.json()["id"])
    assert job.origin == DATASET_QUEUE and job.timeout == 900 and not job.retries_left
    selected = queue("INGEST_DATASET")
    SimpleWorker([selected], connection=selected.connection).work(burst=True)
    assert queue().job_ids == old_ids
    with SessionLocal() as db:
        assert db.get(Task, job.id).status == "SUCCEEDED"
        assert db.get(Task, job.id).attempts == 1
        assert all(db.get(Task, task_id).attempts == 0 for task_id in old_ids)
    assert next(row for row in logged_in.get("/api/tasks").json() if row["id"] == job.id)["status"] == "SUCCEEDED"


def test_restart_preserves_existing_origins_and_recovers_missing_jobs():
    """Keep pre-upgrade queue entries and progress; recover only absent IDs to their new route."""
    progress = {"progress": {"next_date": "2024-03-01", "completed_pages": 1}}
    with SessionLocal() as db:
        for task_id, kind, status in [
            ("old-dataset", "INGEST_DATASET", "QUEUED"),
            ("missing-dataset", "INGEST_DATASET", "QUEUE_ERROR"),
            ("missing-archive", "FETCH_BINANCE_ARCHIVES", "QUEUED"),
            ("gpu-owned", "RUN_FULL_RESEARCH", "QUEUED"),
        ]:
            db.add(Task(id=task_id, kind=kind, status=status, parameters={}, result=progress,
                        updated_at=utcnow() - timedelta(hours=1)))
        db.commit()
        queue().enqueue(jobs.execute, "old-dataset", job_id="old-dataset")
        assert jobs.recover_unpublished_tasks(db) == 2
        assert jobs.recover_unpublished_tasks(db) == 0
        assert queue().job_ids == ["old-dataset", "missing-archive"]
        assert queue("INGEST_DATASET").job_ids == ["missing-dataset"]
        assert find_job("old-dataset").origin == LEGACY_QUEUE
        assert find_job("missing-archive").timeout == 21600
        assert find_job("missing-archive").retries_left == 2
        assert find_job("gpu-owned") is None
        jobs.reconcile_task_states(db)
        assert db.get(Task, "old-dataset").status == "QUEUED"
        assert db.get(Task, "missing-dataset").result == progress
        # Terminal jobs on either origin must become visible failures, never duplicate publications.
        for task_id in ("old-dataset", "missing-dataset"):
            find_job(task_id).set_status(JobStatus.FAILED)
        assert jobs.recover_unpublished_tasks(db) == 0
        jobs.reconcile_task_states(db)
        assert all(db.get(Task, task_id).status == "FAILED" for task_id in ("old-dataset", "missing-dataset"))
        assert db.get(Task, "missing-dataset").result["progress"] == progress["progress"]


def test_publish_outage_keeps_checkpoint_for_restart(monkeypatch):
    """Retain saved page progress on Redis failure, then republish the same ID once Redis returns."""
    checkpoint = {"progress": {"next_date": "2024-03-01", "completed_pages": 1}}

    def unavailable(task):
        """Simulate an unavailable Redis publish without network access or database mutation."""
        raise ConnectionError("isolated Redis outage")

    with SessionLocal() as db:
        with monkeypatch.context() as context:
            context.setattr(jobs, "publish_task", unavailable)
            task = jobs.enqueue(db, "INGEST_DATASET", {}, initial_result=checkpoint)
        assert task.status == "QUEUE_ERROR"
        assert task.result["progress"] == checkpoint["progress"]
        assert jobs.recover_unpublished_tasks(db) == 1
        assert find_job(task.id).origin == DATASET_QUEUE
        assert task.status == "QUEUED" and task.attempts == 0
        assert task.result["progress"] == checkpoint["progress"]


def test_reconcile_redis_outage_preserves_last_known_state(monkeypatch):
    """A cross-queue lookup outage must not convert a durable running task into a missing-job failure."""
    with SessionLocal() as db:
        task = jobs.enqueue(db, "INGEST_DATASET", {})
        task.status = "RUNNING"
        task.updated_at = utcnow() - timedelta(hours=1)
        db.commit()

        def unavailable(task_id):
            """Simulate lookup failure for a stable task ID; no external network is contacted."""
            raise ConnectionError("isolated Redis outage")

        monkeypatch.setattr(jobs, "find_job", unavailable)
        jobs.reconcile_task_states(db)
        assert db.get(Task, task.id).status == "RUNNING"
