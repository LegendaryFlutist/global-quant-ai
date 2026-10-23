"""Authenticated native-data version endpoints; collection runs only in RQ workers."""

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import Audit, Task, User
from app.domains.datasets import DatasetVersion
from app.services.jobs import enqueue
from app.services.research_data import dataset_catalog, validate_request, version_summary
from app.storage.research_datasets import read_version

router = APIRouter(prefix="/api/datasets")


class IngestRequest(BaseModel):
    """Only server-owned sources and strict date labels are accepted; never arbitrary URLs."""
    model_config = {"extra": "forbid"}
    asset_id: str = Field(min_length=1, max_length=100)
    provider_id: Literal["yahoo_chart", "coinbase_exchange", "legacy_binance_snapshot"]
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.get("/catalog")
def catalog(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Return source-native capabilities and actual latest versions to an authenticated user."""
    return dataset_catalog(db)


@router.post("/ingest", status_code=202)
def ingest(payload: IngestRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Queue one explicit public-data window with CSRF and concurrent active-task deduplication."""
    try:
        validate_request(payload.asset_id, payload.provider_id, payload.start_date, payload.end_date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    params = payload.model_dump()
    request_key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"dataset-request:{request_key}"})
    for task in db.scalars(select(Task).where(Task.kind == "INGEST_DATASET", Task.status.in_(["QUEUED", "RUNNING", "RETRYING"]))):
        if task.parameters == params:
            return {"id": task.id, "status": task.status, "deduplicated": True}
    checkpoint = None
    for previous in db.scalars(select(Task).where(Task.kind == "INGEST_DATASET", Task.status == "FAILED").order_by(Task.updated_at.desc())):
        progress = (previous.result or {}).get("progress", {})
        if previous.parameters == params and payload.start_date < progress.get("next_date", "") <= payload.end_date:
            checkpoint = {key: value for key, value in previous.result.items() if key != "error"}
            break
    task = enqueue(db, "INGEST_DATASET", params, initial_result=checkpoint)
    db.add(Audit(actor=user.username, action="DATASET_INGEST_QUEUED", detail={"task_id": task.id, "asset_id": payload.asset_id}))
    db.commit()
    return {"id": task.id, "status": task.status}


@router.get("")
def versions(asset_id: str = Query(max_length=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """List up to 100 source-separated frozen versions; no private account data is returned."""
    rows = db.scalars(select(DatasetVersion).where(DatasetVersion.asset_id == asset_id)
                      .order_by(DatasetVersion.observed_at.desc(), DatasetVersion.id.desc()).limit(100))
    return {"versions": [version_summary(row) for row in rows]}


@router.get("/{version_id}/bars")
def bars(version_id: str, as_of_ms: int | None = Query(default=None, ge=0), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Verify the fixed manifest and return native Decimal bars known by an optional UTC-ms cutoff."""
    version = db.get(DatasetVersion, version_id)
    if version is None:
        raise HTTPException(404, "数据版本不存在")
    try:
        rows = read_version(version, as_of_ms)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    fields = ("open", "high", "low", "close", "volume")
    return {"version": version_summary(version), "candles": [
        {**row, **{field: str(row[field]) if row[field] is not None else None for field in fields}} for row in rows
    ], "research_only": True}
