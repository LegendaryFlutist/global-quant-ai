"""Regression checks for safe DuckDB reads over immutable Parquet segments."""

from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.storage.duckdb_query import parquet_row_stats, query_parquet_rows


def _write_segment(path, rows):
    """Write typed test rows to one temporary Parquet segment.

    Args:
        path: Temporary Parquet file destination.
        rows: Candle rows with UTC-millisecond times and decimal-string prices.

    Returns:
        The path of the written test segment.
    """
    schema = pa.schema(
        [
            ("open_time", pa.int64()),
            ("close_time", pa.int64()),
            ("close", pa.decimal128(34, 12)),
        ]
    )
    table = pa.Table.from_pylist(
        [{**row, "close": Decimal(row["close"])} for row in rows],
        schema=schema,
    )
    pq.write_table(table, path, compression="zstd")
    return path


def test_duckdb_queries_multiple_segments_preserve_decimal_and_time_bounds(tmp_path):
    """Filter and limit multi-file history while preserving exact price decimals.

    Args:
        tmp_path: Pytest temporary directory for isolated Parquet test files.

    Returns:
        None; asserts row bounds, sorted selection, and exact Decimal values.
    """
    first_path = _write_segment(
        tmp_path / "first.parquet",
        [
            {"open_time": 1000, "close_time": 1999, "close": "1.123456789012"},
            {"open_time": 2000, "close_time": 2999, "close": "2.123456789012"},
        ],
    )
    second_path = _write_segment(
        tmp_path / "second.parquet",
        [{"open_time": 3000, "close_time": 3999, "close": "3.123456789012"}],
    )

    rows = query_parquet_rows(
        [second_path, first_path],
        order_column="open_time",
        end_column="close_time",
        end_ms=3500,
        limit=1,
    )
    stats = parquet_row_stats(
        [first_path, second_path],
        first_column="open_time",
        last_column="close_time",
        end_column="close_time",
        end_ms=3500,
    )

    assert rows == [
        {"open_time": 2000, "close_time": 2999, "close": Decimal("2.123456789012")}
    ]
    assert stats == (2, 1000, 2999)


def test_duckdb_rejects_empty_or_unsupported_parquet_queries(tmp_path):
    """Reject empty scans, unsupported columns, and non-Parquet file inputs.

    Args:
        tmp_path: Pytest temporary directory for an invalid file path.

    Returns:
        None; asserts invalid caller inputs fail before query execution.
    """
    plain_path = tmp_path / "not-parquet.txt"
    plain_path.write_text("not data", encoding="utf-8")

    with pytest.raises(ValueError, match="至少需要一个Parquet文件"):
        query_parquet_rows([], order_column="open_time")
    with pytest.raises(ValueError, match="排序列"):
        query_parquet_rows([plain_path], order_column="asset_id")
    with pytest.raises(ValueError, match="只接受现有Parquet文件"):
        query_parquet_rows([plain_path], order_column="open_time")
