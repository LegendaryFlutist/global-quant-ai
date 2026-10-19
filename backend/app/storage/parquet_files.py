"""Atomic immutable Parquet segments and small storage-manifest writes."""

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def sha256_file(path: str | Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    """Hash a stored artifact incrementally without loading it all into memory.

    Args:
        path: Existing local artifact path.
        chunk_bytes: Positive byte count per read; defaults to 1 MiB.

    Returns:
        Lowercase SHA256 hex digest for the exact bytes on disk.

    Raises:
        ValueError: The requested chunk size is not positive.
    """
    if chunk_bytes < 1:
        raise ValueError("哈希读取块大小必须大于零")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_directory(directory: Path) -> None:
    """Flush a published file entry for the given local directory to disk.

    Args:
        directory: Existing local directory containing the published file.

    Returns:
        None; the directory descriptor is always closed before returning.
    """
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_parquet_immutable(
    table: pa.Table,
    target: str | Path,
    *,
    compression: str = "zstd",
) -> tuple[Path, str]:
    """Publish one immutable Parquet file and return its content digest.

    Args:
        table: Typed Arrow table; monetary columns should already use Decimal types.
        target: New `.parquet` path inside the caller's validated data partition.
        compression: PyArrow Parquet compression codec.

    Returns:
        Canonical target path and SHA256 of the published file.

    Raises:
        ValueError: The target is not a Parquet path or an existing name has
            different content.

    Notes:
        Publication uses a same-directory temporary file and an atomic hard link,
        so a crash cannot expose a partial segment or replace an existing segment.
    """
    destination = Path(target)
    if destination.suffix != ".parquet" or destination.name in {"", ".", ".."}:
        raise ValueError("不可变行情文件必须使用.parquet后缀")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        pq.write_table(table, temporary, compression=compression)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = sha256_file(temporary)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if sha256_file(destination) != digest:
                raise ValueError("不可变Parquet文件名冲突") from None
        else:
            _sync_directory(destination.parent)
        return destination, digest
    finally:
        temporary.unlink(missing_ok=True)


def write_content_addressed_parquet(
    table: pa.Table,
    directory: str | Path,
    *,
    prefix: str,
    compression: str = "zstd",
) -> tuple[Path, str]:
    """Write a Parquet segment whose stable name includes its content hash.

    Args:
        table: Typed Arrow table for one immutable page or time partition.
        directory: Validated local directory for one asset/data-stream partition.
        prefix: Safe filename prefix, commonly its first and last UTC timestamps.
        compression: PyArrow Parquet compression codec.

    Returns:
        Published path and full SHA256 digest.

    Raises:
        ValueError: The filename prefix is empty or contains a path separator.
    """
    if not prefix or Path(prefix).name != prefix or prefix in {".", ".."}:
        raise ValueError("Parquet分段名前缀无效")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{prefix}.{uuid.uuid4().hex}.parquet"
    try:
        pq.write_table(table, staging, compression=compression)
        with staging.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = sha256_file(staging)
        target = root / f"{prefix}-{digest[:16]}.parquet"
        try:
            os.link(staging, target)
        except FileExistsError:
            if sha256_file(target) != digest:
                raise ValueError("内容寻址Parquet文件名冲突") from None
        else:
            _sync_directory(root)
        return target, digest
    finally:
        staging.unlink(missing_ok=True)


def write_json_atomic(
    target: str | Path,
    value: Any,
    *,
    compact: bool = False,
) -> Path:
    """Atomically replace a small JSON manifest after flushing its full content.

    Args:
        target: Manifest path inside its validated local data partition.
        value: JSON-serializable manifest values; callers must exclude credentials.
        compact: Use compact separators when matching an existing compact manifest.

    Returns:
        The path of the atomically published manifest.
    """
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    separators = (",", ":") if compact else None
    content = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=separators,
    )
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _sync_directory(destination.parent)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
