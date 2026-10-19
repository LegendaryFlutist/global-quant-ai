"""Regression tests for atomic, immutable local Parquet persistence."""

import json
from decimal import Decimal

import pyarrow as pa
import pytest

from app.storage.parquet_files import (
    sha256_file,
    write_content_addressed_parquet,
    write_json_atomic,
    write_parquet_immutable,
)


def test_parquet_writer_is_immutable_and_idempotent(tmp_path):
    """Write one typed price file twice and reject conflicting replacement bytes.

    Args:
        tmp_path: Pytest isolated temporary storage root.

    Returns:
        None; asserts stable SHA256 and no overwrite of an existing snapshot.
    """
    target = tmp_path / "snapshot.parquet"
    first_table = pa.table({"close": [Decimal("123.450000000000")]})
    second_table = pa.table({"close": [Decimal("999.000000000000")]})

    first_path, first_sha = write_parquet_immutable(first_table, target)
    repeated_path, repeated_sha = write_parquet_immutable(first_table, target)

    assert first_path == repeated_path == target
    assert first_sha == repeated_sha == sha256_file(target)
    with pytest.raises(ValueError, match="文件名冲突"):
        write_parquet_immutable(second_table, target)
    assert sha256_file(target) == first_sha


def test_content_addressed_segments_and_atomic_manifest(tmp_path):
    """Publish a hash-named segment and replace a complete JSON manifest.

    Args:
        tmp_path: Pytest isolated temporary storage root.

    Returns:
        None; asserts the stable filename suffix matches full file SHA256.
    """
    table = pa.table({"open_time": [1, 2], "close": [Decimal("1.1"), Decimal("1.2")]})
    segment, digest = write_content_addressed_parquet(
        table,
        tmp_path / "BTCUSDT" / "15m",
        prefix="1-2",
    )
    manifest_path = write_json_atomic(
        segment.parent / "manifest.json",
        {"file": segment.name, "sha256": digest},
    )

    assert segment.name == f"1-2-{digest[:16]}.parquet"
    assert sha256_file(segment) == digest
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
        "file": segment.name,
        "sha256": digest,
    }
    assert not list(segment.parent.glob("*.tmp"))
