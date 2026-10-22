"""Server-owned queue routing; Redis job IDs remain global across queue upgrades."""
from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.core.config import REDIS_URL

LEGACY_QUEUE = "market-data"
DATASET_QUEUE = "research-data"


def queue(kind: str | None = None) -> Queue:
    """Return the server-selected persistent queue without executing a task.

    Only INGEST_DATASET uses the isolated research-data worker. Existing callers
    without a kind retain market-data; request payloads cannot choose queue names.
    """
    name = DATASET_QUEUE if kind == "INGEST_DATASET" else LEGACY_QUEUE
    return Queue(name, connection=Redis.from_url(REDIS_URL), default_timeout=900)


def find_job(task_id: str) -> Job | None:
    """Look up a durable task ID across queues; return None only if its job is absent.

    A pre-upgrade dataset job may still belong to market-data. Never requeue a
    present job merely because its origin differs from the current routing rule.
    Redis errors propagate so callers preserve the last known database state.
    """
    try:
        return Job.fetch(task_id, connection=Redis.from_url(REDIS_URL))
    except NoSuchJobError:
        return None
