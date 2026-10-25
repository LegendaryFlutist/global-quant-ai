"""Read-only analytical queries across validated Parquet market-data files."""

from pathlib import Path
from typing import Sequence

import duckdb

_TIME_COLUMNS = {"open_time", "close_time", "funding_time", "mark_time", "observed_at_ms"}


def _validated_files(paths: Sequence[str | Path]) -> list[Path]:
    """Resolve a non-empty list of existing Parquet file paths.

    Args:
        paths: Explicit files already selected by trusted storage metadata.

    Returns:
        Canonical existing paths; glob patterns and directories are rejected.
    """
    if not paths:
        raise ValueError("至少需要一个Parquet文件")
    resolved = [Path(path).resolve(strict=True) for path in paths]
    if any(not path.is_file() or path.suffix != ".parquet" for path in resolved):
        raise ValueError("DuckDB只接受现有Parquet文件")
    return resolved


def _scan_source(file_count: int) -> str:
    """Build a parameterized DuckDB Parquet table-function expression.

    Args:
        file_count: Number of canonical file parameters in the scan.

    Returns:
        A SQL fragment with one placeholder for each file path.
    """
    if file_count < 1:
        raise ValueError("Parquet扫描文件数必须大于零")
    placeholders = ", ".join("?" for _ in range(file_count))
    return f"read_parquet([{placeholders}], union_by_name = true)"


def _where_clause(end_column: str | None, end_ms: int | None) -> tuple[str, list[int]]:
    """Build a safe optional UTC-millisecond upper-bound filter.

    Args:
        end_column: Whitelisted timestamp column or None when no filter is used.
        end_ms: Inclusive Unix-millisecond ceiling supplied by the caller.

    Returns:
        SQL WHERE clause and its positional millisecond parameter.
    """
    if end_ms is None:
        return "", []
    if end_column not in _TIME_COLUMNS:
        raise ValueError("结束时间过滤列不受支持")
    if not isinstance(end_ms, int) or end_ms < 0:
        raise ValueError("结束时间必须为非负UTC毫秒时间戳")
    return f' WHERE "{end_column}" <= ?', [end_ms]


def query_parquet_rows(
    paths: Sequence[str | Path],
    *,
    order_column: str,
    end_column: str | None = None,
    end_ms: int | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Read ordered rows from explicitly selected Parquet segments.

    Args:
        paths: Hash-validated files selected by the Parquet storage adapter.
        order_column: Whitelisted UTC-millisecond column used for ordering.
        end_column: Optional whitelisted UTC-millisecond column for an inclusive ceiling.
        end_ms: Optional inclusive UTC-millisecond query ceiling.
        limit: Optional maximum row count; when set, the newest rows are returned.

    Returns:
        Arrow-backed Python records in ascending order with Decimal values preserved.

    Raises:
        ValueError: Paths, columns, limits, or query bounds are invalid.
    """
    if order_column not in _TIME_COLUMNS:
        raise ValueError("K线排序列不受支持")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ValueError("查询行数上限必须为正整数")
    files = _validated_files(paths)
    where, time_params = _where_clause(end_column, end_ms)
    direction = "DESC" if limit is not None else "ASC"
    limit_clause = " LIMIT ?" if limit is not None else ""
    parameters: list[object] = [str(path) for path in files] + time_params
    if limit is not None:
        parameters.append(limit)
    sql = (
        f'SELECT * FROM {_scan_source(len(files))}{where} '
        f'ORDER BY "{order_column}" {direction}{limit_clause}'
    )
    connection = duckdb.connect(database=":memory:", config={"threads": "2"})
    try:
        rows = connection.execute(sql, parameters).to_arrow_table().to_pylist()
    finally:
        connection.close()
    if limit is not None:
        rows.reverse()
    return rows


def parquet_row_stats(
    paths: Sequence[str | Path],
    *,
    first_column: str,
    last_column: str,
    end_column: str | None = None,
    end_ms: int | None = None,
) -> tuple[int, int | None, int | None]:
    """Count matching rows and return time bounds without materializing the dataset.

    Args:
        paths: Hash-validated files selected by the Parquet storage adapter.
        first_column: Whitelisted timestamp column used for the earliest boundary.
        last_column: Whitelisted timestamp column used for the latest boundary.
        end_column: Optional timestamp column compared with the inclusive ceiling.
        end_ms: Optional inclusive UTC-millisecond query ceiling.

    Returns:
        Row count, minimum first-column time, and maximum last-column time.
    """
    if first_column not in _TIME_COLUMNS or last_column not in _TIME_COLUMNS:
        raise ValueError("Parquet统计时间列不受支持")
    files = _validated_files(paths)
    where, time_params = _where_clause(end_column, end_ms)
    sql = (
        f'SELECT COUNT(*), MIN("{first_column}"), MAX("{last_column}") '
        f'FROM {_scan_source(len(files))}{where}'
    )
    parameters: list[object] = [str(path) for path in files] + time_params
    connection = duckdb.connect(database=":memory:", config={"threads": "2"})
    try:
        count, first, last = connection.execute(sql, parameters).fetchone()
    finally:
        connection.close()
    return int(count), first, last
