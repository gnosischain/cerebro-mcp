"""Phase 2 tests: SandboxManager + parquet sanitization.

Self-contained — no live ClickHouse needed. We stub `ClickHouseManager`
with a minimal fake whose `export_to_parquet` writes a known parquet from
in-memory pyarrow tables. That exercises every code path of
`SandboxManager` (create, query, mutate, destroy, eviction, TTL sweep,
shutdown) without depending on network or CH availability.
"""

from __future__ import annotations

import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from cerebro_mcp.clients.clickhouse import (
    _datetime64_precision,
    _decimal_precision,
    _sanitize_column_for_parquet,
)
from cerebro_mcp.runtime.sandbox_manager import SandboxManager


# ---------------------------------------------------------------------------
# Fake CH manager — exposes only export_to_parquet, which writes a fixed
# parquet from a pyarrow table built per-test.
# ---------------------------------------------------------------------------


class FakeCH:
    def __init__(self, table: pa.Table) -> None:
        self.table = table
        self.calls: list[dict] = []

    def export_to_parquet(
        self,
        sql: str,
        output_path: Path,
        max_bytes: int,
        database: str = "dbt",
    ) -> int:
        self.calls.append(
            {"sql": sql, "output_path": output_path, "max_bytes": max_bytes,
             "database": database}
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(self.table, str(output_path), compression="zstd")
        return output_path.stat().st_size


def _sample_table() -> pa.Table:
    return pa.table(
        {
            "day": pa.array(["2026-04-01", "2026-04-02", "2026-04-03"]),
            "volume": pa.array([100.0, 200.0, 50.0]),
            "reward": pa.array([10.0, 20.0, 5.0]),
        }
    )


@pytest.fixture
def manager(tmp_path: Path) -> SandboxManager:
    return SandboxManager(
        root=tmp_path / "sandboxes",
        max_concurrent=2,
        ttl_seconds=10,
        max_bytes_per_export=10 * 1024 * 1024,
    )


# ---------------------------------------------------------------------------
# Type-sanitizer unit tests (pure helpers, no I/O)
# ---------------------------------------------------------------------------


class TestParquetSanitizer:
    def test_plain_types_pass_through(self):
        assert _sanitize_column_for_parquet("x", "Float64") == "`x`"
        assert _sanitize_column_for_parquet("x", "String") == "`x`"
        assert _sanitize_column_for_parquet("x", "DateTime") == "`x`"

    def test_enum_cast_to_string(self):
        assert "CAST(`status` AS String)" in _sanitize_column_for_parquet(
            "status", "Enum8('a' = 1, 'b' = 2)"
        )

    def test_uuid_to_string(self):
        assert "toString(`id`)" in _sanitize_column_for_parquet("id", "UUID")

    def test_ipv4_to_string(self):
        assert "toString(`addr`)" in _sanitize_column_for_parquet("addr", "IPv4")

    def test_high_precision_datetime_downcast(self):
        out = _sanitize_column_for_parquet("ts", "DateTime64(9, 'UTC')")
        assert "toDateTime64(`ts`, 6)" in out

    def test_low_precision_datetime_passes(self):
        out = _sanitize_column_for_parquet("ts", "DateTime64(3, 'UTC')")
        assert out == "`ts`"

    def test_oversize_decimal_to_float(self):
        out = _sanitize_column_for_parquet("amt", "Decimal(40, 2)")
        assert "Float64" in out

    def test_normal_decimal_passes(self):
        out = _sanitize_column_for_parquet("amt", "Decimal(18, 6)")
        assert out == "`amt`"

    def test_date_upcast_to_date32(self):
        # CH Date arrives in DuckDB as USMALLINT unless we upcast — verified
        # in the 2026-04-27 live run; agent had to use INTERVAL workarounds.
        out = _sanitize_column_for_parquet("month", "Date")
        assert "toDate32(`month`)" in out

    def test_nullable_date_upcast(self):
        out = _sanitize_column_for_parquet("month", "Nullable(Date)")
        assert "toDate32(`month`)" in out

    def test_date32_passes_through(self):
        # Already wide enough; no cast needed.
        out = _sanitize_column_for_parquet("month", "Date32")
        assert out == "`month`"

    def test_datetime_passes_through(self):
        # DateTime (no precision) is fine for parquet — Arrow handles it.
        out = _sanitize_column_for_parquet("ts", "DateTime")
        assert out == "`ts`"

    def test_nullable_unwrapping(self):
        out = _sanitize_column_for_parquet("id", "Nullable(UUID)")
        assert "toString(`id`)" in out

    def test_lowcardinality_unwrapping(self):
        out = _sanitize_column_for_parquet("s", "LowCardinality(String)")
        assert out == "`s`"

    def test_decimal_precision_helper(self):
        assert _decimal_precision("Decimal(40, 2)") == 40
        assert _decimal_precision("Decimal128(38, 4)") == 38
        assert _decimal_precision("Float64") == 0

    def test_datetime64_precision_helper(self):
        assert _datetime64_precision("DateTime64(9, 'UTC')") == 9
        assert _datetime64_precision("DateTime") == 0


# ---------------------------------------------------------------------------
# SandboxManager unit tests
# ---------------------------------------------------------------------------


class TestSandboxLifecycle:
    def test_create_query_destroy_roundtrip(self, manager):
        ch = FakeCH(_sample_table())
        info = manager.create("sb1", "SELECT 1", ch)
        assert info["sandbox_id"] == "sb1"
        assert info["row_count"] == 3
        assert info["bytes"] > 0

        # Read what was mounted.
        result = manager.query("sb1", "SELECT count(*) FROM data")
        assert result["rows"] == [[3]]

        # Mutate and re-aggregate.
        manager.query("sb1", "UPDATE data SET reward = reward * 2")
        result = manager.query("sb1", "SELECT sum(reward) FROM data")
        assert result["rows"] == [[70.0]]   # original 35 doubled

        assert manager.destroy("sb1") is True
        assert manager.destroy("sb1") is False  # idempotent

    def test_invalid_sandbox_id_rejected(self, manager):
        ch = FakeCH(_sample_table())
        with pytest.raises(ValueError, match="Invalid sandbox_id"):
            manager.create("../../etc/passwd", "SELECT 1", ch)
        with pytest.raises(ValueError, match="Invalid sandbox_id"):
            manager.create("has spaces", "SELECT 1", ch)

    def test_invalid_table_name_rejected(self, manager):
        ch = FakeCH(_sample_table())
        with pytest.raises(ValueError, match="Invalid table name"):
            manager.create("sb_x", "SELECT 1", ch, table_name="drop table")

    def test_duplicate_sandbox_id_rejected(self, manager):
        ch = FakeCH(_sample_table())
        manager.create("dup", "SELECT 1", ch)
        with pytest.raises(ValueError, match="already exists"):
            manager.create("dup", "SELECT 1", ch)

    def test_sandbox_isolation(self, manager):
        ch1 = FakeCH(_sample_table())
        ch2 = FakeCH(_sample_table())
        manager.create("a", "SELECT 1", ch1)
        manager.create("b", "SELECT 1", ch2)
        manager.query("a", "UPDATE data SET reward = 999")
        b_reward = manager.query("b", "SELECT sum(reward) FROM data")["rows"][0][0]
        assert b_reward == 35.0   # untouched

    def test_lru_eviction_at_capacity(self, manager):
        ch = FakeCH(_sample_table())
        manager.create("a", "SELECT 1", ch)
        time.sleep(0.01)
        manager.create("b", "SELECT 1", ch)
        assert {s["sandbox_id"] for s in manager.list_sandboxes()} == {"a", "b"}

        # max_concurrent=2 → adding a third evicts the oldest by last_used_at.
        manager.create("c", "SELECT 1", ch)
        ids = {s["sandbox_id"] for s in manager.list_sandboxes()}
        assert ids == {"b", "c"}
        # And the workspace for `a` is gone.
        assert not (manager._root / "a").exists()

    def test_lru_picks_least_recently_used(self, manager):
        ch = FakeCH(_sample_table())
        manager.create("a", "SELECT 1", ch)
        time.sleep(0.01)
        manager.create("b", "SELECT 1", ch)
        time.sleep(0.01)
        manager.query("a", "SELECT 1")  # bump a's last_used_at past b
        manager.create("c", "SELECT 1", ch)
        ids = {s["sandbox_id"] for s in manager.list_sandboxes()}
        assert ids == {"a", "c"}     # b was the oldest now

    def test_ttl_sweep(self, tmp_path):
        # Short TTL just for this test.
        mgr = SandboxManager(
            root=tmp_path / "sweep",
            max_concurrent=10,
            ttl_seconds=0,            # everything is "expired" immediately
        )
        ch = FakeCH(_sample_table())
        mgr.create("a", "SELECT 1", ch)
        mgr.create("b", "SELECT 1", ch)
        # Make sure last_used_at is strictly older than now.
        time.sleep(0.01)
        evicted = mgr.sweep_expired()
        assert evicted == 2
        assert mgr.list_sandboxes() == []

    def test_shutdown_clears_all(self, manager):
        ch = FakeCH(_sample_table())
        manager.create("a", "SELECT 1", ch)
        manager.create("b", "SELECT 1", ch)
        manager.shutdown()
        assert manager.list_sandboxes() == []

    def test_query_unknown_sandbox_raises(self, manager):
        with pytest.raises(KeyError, match="not found"):
            manager.query("missing", "SELECT 1")

    def test_export_failure_cleans_workspace(self, manager, tmp_path):
        class FlakyCH:
            def export_to_parquet(self, *_a, **_k):
                raise RuntimeError("CH down")
        with pytest.raises(RuntimeError, match="CH down"):
            manager.create("flaky", "SELECT 1", FlakyCH())
        # No leaked workspace dir.
        assert not (manager._root / "flaky").exists()
        # And no leaked sandbox state.
        assert manager.list_sandboxes() == []
