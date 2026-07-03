"""Typed date-conversion tests for the two query-result boundaries.

Site 1: `query_metrics` result build — `_format_time_dimension_cells` converts
resolved TIME dimensions (day/week/month, incl. time-spine upcasts) to ISO but
leaves numeric metric columns untouched.

Site 2: `execute_query` arrow path — `_rows_from_arrow` reads the arrow schema
and converts date32/date64 + all timestamp units/timezones to ISO, while a
plain int metric near epoch-day magnitude (17000-25000) passes through.
"""

import datetime
from types import SimpleNamespace

import pyarrow as pa

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.tools.semantic.semantic import (
    _format_time_dimension_cells,
    _time_dimension_names_from_plan,
)


# ── Site 1: query_metrics ─────────────────────────────────────────────


def _plan_with_time_and_numeric():
    return {
        "branches": [
            {
                "root_model": "m",
                "metrics": ["active_addresses"],
                "dimension_bindings": {
                    "week": {
                        "local": True,
                        "dimension": {"name": "week", "type": "time"},
                    },
                    "sector": {
                        "local": True,
                        "dimension": {"name": "sector", "type": "categorical"},
                    },
                },
            }
        ],
        "resolved_dimensions": ["week", "sector"],
    }


def test_query_metrics_converts_time_dim_leaves_numeric_metric():
    plan = _plan_with_time_and_numeric()
    # columns: week (epoch-day int), sector (str), active_addresses (numeric
    # metric that happens to be near the epoch-day magnitude 17000-25000)
    result = SimpleNamespace(
        columns=["week", "sector", "active_addresses"],
        rows=[
            [20626, "defi", 20001],
            [20633, "nfts", 18500],
        ],
    )

    _format_time_dimension_cells(plan, result)

    # week: epoch-day 20626 -> ISO date
    assert result.rows[0][0] == "2026-06-22"
    assert result.rows[1][0] == "2026-06-29"
    # sector untouched
    assert result.rows[0][1] == "defi"
    # numeric metric NEAR epoch-day magnitude is NOT reinterpreted
    assert result.rows[0][2] == 20001
    assert result.rows[1][2] == 18500


def test_time_dimension_names_include_synthesised_upcasts():
    # A time-spine upcast binding carries dimension.type == "time".
    plan = {
        "branches": [
            {
                "dimension_bindings": {
                    "month": {
                        "local": True,
                        "dimension": {
                            "name": "month",
                            "type": "time",
                            "_upcast_template": "toStartOfMonth({col})",
                        },
                    },
                    "token": {
                        "local": True,
                        "dimension": {"name": "token", "type": "categorical"},
                    },
                }
            }
        ]
    }
    names = _time_dimension_names_from_plan(plan)
    assert names == {"month"}


def test_query_metrics_noop_when_no_time_dims():
    plan = {"branches": [{"dimension_bindings": {}}]}
    result = SimpleNamespace(columns=["cnt"], rows=[[20001]])
    _format_time_dimension_cells(plan, result)
    assert result.rows == [[20001]]  # untouched


# ── Site 2: execute_query arrow path ──────────────────────────────────


def test_rows_from_arrow_converts_dates_and_timestamps_leaves_numeric():
    manager = ClickHouseManager()
    table = pa.table(
        {
            "d32": pa.array([datetime.date(2026, 6, 22)], type=pa.date32()),
            "d64": pa.array([datetime.date(2026, 6, 22)], type=pa.date64()),
            "ts": pa.array([datetime.datetime(2026, 6, 22, 10, 30)], type=pa.timestamp("s")),
            "ts_tz": pa.array(
                [datetime.datetime(2026, 6, 22, 10, 30, tzinfo=datetime.timezone.utc)],
                type=pa.timestamp("us", tz="UTC"),
            ),
            # A real numeric metric near the epoch-day magnitude — must NOT be
            # treated as a date because its arrow type is int, not temporal.
            "active_addresses": pa.array([20626], type=pa.int64()),
        }
    )

    columns, rows = manager._rows_from_arrow(table)

    assert columns == ["d32", "d64", "ts", "ts_tz", "active_addresses"]
    row = rows[0]
    assert row[0] == "2026-06-22"  # date32
    assert row[1] == "2026-06-22"  # date64
    assert row[2].startswith("2026-06-22T10:30:00")  # timestamp[s]
    assert row[3].startswith("2026-06-22T10:30:00")  # timestamp[us, tz]
    assert "+00:00" in row[3] or row[3].endswith("+00:00")  # tz preserved
    assert row[4] == 20626  # numeric metric untouched


def test_rows_from_arrow_handles_nulls_in_temporal_column():
    manager = ClickHouseManager()
    table = pa.table(
        {"d": pa.array([datetime.date(2026, 1, 1), None], type=pa.date32())}
    )
    _columns, rows = manager._rows_from_arrow(table)
    assert rows[0][0] == "2026-01-01"
    assert rows[1][0] is None
