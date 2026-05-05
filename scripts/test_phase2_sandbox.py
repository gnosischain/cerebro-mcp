#!/usr/bin/env python3
"""Phase 2 smoke test — exercise the DuckDB + Parquet sandbox end-to-end.

Two modes:

  Default (offline)
    Uses an in-memory pyarrow table as the "ClickHouse" source. Runs
    everywhere — no CH connection required. Verifies SandboxManager
    lifecycle, mutation visibility, isolation, LRU eviction, TTL sweep,
    error handling, and the parquet type-sanitizer logic.

  --live
    Connects to ClickHouse via your normal cerebro-mcp config (`.env`)
    and runs an actual CH → parquet → DuckDB roundtrip on a small,
    deterministic query (`SELECT ... LIMIT 100`). Useful as a final
    "is the wiring correct?" check after deployment.

Usage:
    python scripts/test_phase2_sandbox.py
    python scripts/test_phase2_sandbox.py --live
    python scripts/test_phase2_sandbox.py --live --table api_consensus_validators_active_daily

Each section prints PASS / FAIL and a short summary. Exit code is non-zero
if any section fails.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

# Make `cerebro_mcp` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from cerebro_mcp.clickhouse_client import (  # noqa: E402
    _datetime64_precision,
    _decimal_precision,
    _sanitize_column_for_parquet,
)
from cerebro_mcp.sandbox_manager import SandboxManager  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny test runner — local so we don't pull pytest into a smoke script.
# ---------------------------------------------------------------------------


class Reporter:
    def __init__(self) -> None:
        self.passes = 0
        self.fails = 0
        self.section_name = ""

    def section(self, name: str) -> None:
        self.section_name = name
        print(f"\n=== {name} ===")

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        tag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{tag}] {label}{suffix}")
        if ok:
            self.passes += 1
        else:
            self.fails += 1

    def safe_check(self, label: str, fn) -> None:
        """Wrap a callable in try/except so one failing assertion doesn't kill
        the rest of the suite."""
        try:
            fn()
            self.check(label, True)
        except Exception as e:
            tb = traceback.format_exc(limit=2).strip().splitlines()[-1]
            self.check(label, False, f"{type(e).__name__}: {e}  | {tb}")

    def summary(self) -> int:
        total = self.passes + self.fails
        print(f"\n--- {self.passes}/{total} checks passed "
              f"({self.fails} failures) ---")
        return 0 if self.fails == 0 else 1


# ---------------------------------------------------------------------------
# Fake CH for offline mode
# ---------------------------------------------------------------------------


class FakeCH:
    """Minimal stand-in for ClickHouseManager.export_to_parquet. Writes a
    fixed pyarrow table to the requested parquet path."""

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
            {"sql": sql, "output_path": str(output_path),
             "max_bytes": max_bytes, "database": database}
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(self.table, str(output_path), compression="zstd")
        return output_path.stat().st_size


def _baseline_table() -> pa.Table:
    return pa.table(
        {
            "day": pa.array(["2026-04-01", "2026-04-02", "2026-04-03",
                             "2026-04-04", "2026-04-05"]),
            "volume": pa.array([100.0, 200.0, 50.0, 175.0, 90.0]),
            "reward": pa.array([10.0, 20.0, 5.0, 17.5, 9.0]),
            "users": pa.array([12, 22, 7, 18, 11]),
        }
    )


# ---------------------------------------------------------------------------
# Section 1 — sanitizer (pure helpers, no I/O)
# ---------------------------------------------------------------------------


def section_sanitizer(r: Reporter) -> None:
    r.section("1. Parquet type sanitizer (pure helpers)")

    cases = [
        ("plain Float64",     ("x", "Float64"),                     "`x`"),
        ("plain String",      ("name", "String"),                   "`name`"),
        ("Enum8 → String",    ("status", "Enum8('a'=1,'b'=2)"),     "CAST(`status` AS String)"),
        ("UUID → toString",   ("id", "UUID"),                        "toString(`id`)"),
        ("IPv4 → toString",   ("addr", "IPv4"),                      "toString(`addr`)"),
        ("DateTime64(9) → 6", ("ts", "DateTime64(9, 'UTC')"),        "toDateTime64(`ts`, 6)"),
        ("DateTime64(3) ok",  ("ts", "DateTime64(3, 'UTC')"),        "`ts`"),
        ("Decimal(40,2) → Float64", ("amt", "Decimal(40, 2)"),       "Float64"),
        ("Decimal(18,6) ok",  ("amt", "Decimal(18, 6)"),             "`amt`"),
        ("Nullable(UUID)",    ("id", "Nullable(UUID)"),              "toString(`id`)"),
        ("LowCardinality(String)", ("s", "LowCardinality(String)"),  "`s`"),
    ]
    for label, args, expected_substring in cases:
        out = _sanitize_column_for_parquet(*args)
        r.check(label, expected_substring in out, f"got {out!r}")

    r.safe_check(
        "decimal_precision parses Decimal(40,2)",
        lambda: assert_eq(_decimal_precision("Decimal(40, 2)"), 40),
    )
    r.safe_check(
        "datetime64_precision parses DateTime64(9)",
        lambda: assert_eq(_datetime64_precision("DateTime64(9, 'UTC')"), 9),
    )


# ---------------------------------------------------------------------------
# Section 2 — full lifecycle
# ---------------------------------------------------------------------------


def section_lifecycle(r: Reporter, root: Path) -> None:
    r.section("2. Sandbox lifecycle (create → query → mutate → destroy)")

    mgr = SandboxManager(
        root=root / "lifecycle",
        max_concurrent=4,
        ttl_seconds=600,
        max_bytes_per_export=10 * 1024 * 1024,
    )
    ch = FakeCH(_baseline_table())

    info = mgr.create("baseline", "SELECT day, volume, reward FROM ...", ch)
    r.check("create returns sandbox_id", info["sandbox_id"] == "baseline")
    r.check("create reports row_count=5", info["row_count"] == 5,
            f"got {info['row_count']}")
    r.check("parquet file exists on disk",
            Path(info["parquet_path"]).exists())
    r.check("CH export called once", len(ch.calls) == 1)

    # Read original
    res = mgr.query("baseline", "SELECT sum(reward) FROM data")
    r.check("baseline reward sum = 61.5", res["rows"] == [[61.5]],
            f"got {res['rows']}")

    # Mutate
    mgr.query("baseline", "UPDATE data SET reward = reward * 1.3")
    res = mgr.query("baseline", "SELECT sum(reward) FROM data")
    r.check("after +30% mutation, sum ≈ 79.95",
            abs(res["rows"][0][0] - 79.95) < 1e-6,
            f"got {res['rows'][0][0]}")

    # INSERT
    mgr.query(
        "baseline",
        "INSERT INTO data VALUES ('2026-04-06', 999.0, 99.0, 7)",
    )
    res = mgr.query("baseline", "SELECT count(*) FROM data")
    r.check("INSERT visible (row count = 6)", res["rows"] == [[6]])

    # DELETE
    mgr.query("baseline", "DELETE FROM data WHERE day = '2026-04-06'")
    res = mgr.query("baseline", "SELECT count(*) FROM data")
    r.check("DELETE visible (row count = 5)", res["rows"] == [[5]])

    # Destroy + idempotent
    r.check("destroy returns True for existing", mgr.destroy("baseline") is True)
    r.check("destroy returns False for missing", mgr.destroy("baseline") is False)
    r.check("workspace dir cleaned", not (mgr._root / "baseline").exists())


# ---------------------------------------------------------------------------
# Section 3 — isolation between sandboxes
# ---------------------------------------------------------------------------


def section_isolation(r: Reporter, root: Path) -> None:
    r.section("3. Sandbox isolation (mutations don't leak across sandboxes)")

    mgr = SandboxManager(root=root / "isolation", max_concurrent=4,
                         ttl_seconds=600, max_bytes_per_export=10*1024*1024)
    ch = FakeCH(_baseline_table())

    mgr.create("alpha", "SELECT * FROM ...", ch)
    mgr.create("beta", "SELECT * FROM ...", ch)

    mgr.query("alpha", "UPDATE data SET reward = 0")
    a_sum = mgr.query("alpha", "SELECT sum(reward) FROM data")["rows"][0][0]
    b_sum = mgr.query("beta",  "SELECT sum(reward) FROM data")["rows"][0][0]
    r.check("alpha mutated to sum=0", a_sum == 0, f"alpha sum={a_sum}")
    r.check("beta untouched (sum=61.5)", b_sum == 61.5, f"beta sum={b_sum}")

    mgr.shutdown()


# ---------------------------------------------------------------------------
# Section 4 — error handling / validation
# ---------------------------------------------------------------------------


def section_errors(r: Reporter, root: Path) -> None:
    r.section("4. Error handling and validation")

    mgr = SandboxManager(root=root / "errors", max_concurrent=4,
                         ttl_seconds=600, max_bytes_per_export=10*1024*1024)
    ch = FakeCH(_baseline_table())

    r.safe_check(
        "invalid sandbox_id (path traversal) rejected",
        lambda: expect_raises(ValueError, lambda: mgr.create("../etc", "SELECT 1", ch)),
    )
    r.safe_check(
        "invalid sandbox_id (spaces) rejected",
        lambda: expect_raises(ValueError, lambda: mgr.create("has spaces", "SELECT 1", ch)),
    )
    r.safe_check(
        "invalid table_name rejected",
        lambda: expect_raises(ValueError,
            lambda: mgr.create("ok_id", "SELECT 1", ch, table_name="bad name")),
    )

    mgr.create("dup", "SELECT 1", ch)
    r.safe_check(
        "duplicate sandbox_id rejected",
        lambda: expect_raises(ValueError, lambda: mgr.create("dup", "SELECT 1", ch)),
    )

    r.safe_check(
        "query unknown sandbox raises KeyError",
        lambda: expect_raises(KeyError, lambda: mgr.query("missing", "SELECT 1")),
    )

    # Failed export must clean up workspace.
    class FlakyCH:
        def export_to_parquet(self, *_a, **_k):
            raise RuntimeError("CH down")
    r.safe_check(
        "failed CH export propagates",
        lambda: expect_raises(RuntimeError, lambda: mgr.create("flaky", "SELECT 1", FlakyCH())),
    )
    r.check("flaky workspace cleaned", not (mgr._root / "flaky").exists())

    mgr.shutdown()


# ---------------------------------------------------------------------------
# Section 5 — LRU eviction
# ---------------------------------------------------------------------------


def section_lru(r: Reporter, root: Path) -> None:
    r.section("5. LRU eviction at capacity")

    mgr = SandboxManager(root=root / "lru", max_concurrent=2,
                         ttl_seconds=600, max_bytes_per_export=10*1024*1024)
    ch = FakeCH(_baseline_table())

    mgr.create("a", "SELECT 1", ch)
    time.sleep(0.01)
    mgr.create("b", "SELECT 1", ch)
    ids_after_2 = {s["sandbox_id"] for s in mgr.list_sandboxes()}
    r.check("two sandboxes live before eviction",
            ids_after_2 == {"a", "b"}, f"got {ids_after_2}")

    # Bump `a`'s last_used_at so `b` becomes the LRU candidate.
    time.sleep(0.01)
    mgr.query("a", "SELECT 1")
    time.sleep(0.01)
    mgr.create("c", "SELECT 1", ch)
    ids_after_evict = {s["sandbox_id"] for s in mgr.list_sandboxes()}
    r.check("LRU evicted `b` (least recently used)",
            ids_after_evict == {"a", "c"}, f"got {ids_after_evict}")
    r.check("evicted workspace cleaned",
            not (mgr._root / "b").exists())

    mgr.shutdown()


# ---------------------------------------------------------------------------
# Section 6 — TTL sweep
# ---------------------------------------------------------------------------


def section_ttl(r: Reporter, root: Path) -> None:
    r.section("6. TTL sweep removes idle sandboxes")

    mgr = SandboxManager(root=root / "ttl", max_concurrent=10,
                         ttl_seconds=0, max_bytes_per_export=10*1024*1024)
    ch = FakeCH(_baseline_table())
    mgr.create("a", "SELECT 1", ch)
    mgr.create("b", "SELECT 1", ch)
    time.sleep(0.05)   # ensure last_used_at is strictly older than now
    evicted = mgr.sweep_expired()
    r.check("sweep_expired returned 2", evicted == 2, f"got {evicted}")
    r.check("no sandboxes left", mgr.list_sandboxes() == [])

    mgr.shutdown()


# ---------------------------------------------------------------------------
# Section 7 — performance smoke (create + query latency)
# ---------------------------------------------------------------------------


def section_perf(r: Reporter, root: Path) -> None:
    r.section("7. Performance smoke (latency on a 10k-row in-memory dataset)")

    big = pa.table(
        {
            "day":    pa.array([f"2026-{(i % 12) + 1:02d}-01" for i in range(10000)]),
            "volume": pa.array([float(i) for i in range(10000)]),
            "reward": pa.array([float(i) * 0.1 for i in range(10000)]),
        }
    )
    mgr = SandboxManager(root=root / "perf", max_concurrent=2,
                         ttl_seconds=600, max_bytes_per_export=200*1024*1024)
    ch = FakeCH(big)

    t = time.perf_counter()
    info = mgr.create("perf", "SELECT 1", ch)
    create_ms = (time.perf_counter() - t) * 1000
    r.check(f"create 10k rows under 1s", create_ms < 1000,
            f"create_ms={create_ms:.1f}, bytes={info['bytes']}")

    t = time.perf_counter()
    res = mgr.query("perf", "SELECT count(*), sum(volume), sum(reward) FROM data")
    query_ms = (time.perf_counter() - t) * 1000
    r.check("aggregate over 10k rows under 50ms", query_ms < 50,
            f"query_ms={query_ms:.2f}, result={res['rows'][0]}")

    t = time.perf_counter()
    mgr.query("perf", "UPDATE data SET reward = reward * 1.1")
    update_ms = (time.perf_counter() - t) * 1000
    r.check("UPDATE 10k rows under 100ms", update_ms < 100,
            f"update_ms={update_ms:.2f}")

    mgr.shutdown()


# ---------------------------------------------------------------------------
# Section 8 — live ClickHouse roundtrip (opt-in)
# ---------------------------------------------------------------------------


def section_live(r: Reporter, root: Path, ch_table: str) -> None:
    r.section(f"8. LIVE ClickHouse roundtrip (table={ch_table})")

    try:
        from cerebro_mcp.clickhouse_client import ClickHouseManager
    except Exception as e:
        r.check("import ClickHouseManager", False, f"{e}")
        return

    ch = ClickHouseManager()
    try:
        ch.ping()
    except Exception as e:
        r.check("CH ping", False, f"{e}")
        return
    r.check("CH ping", True)

    mgr = SandboxManager(root=root / "live", max_concurrent=2,
                         ttl_seconds=600,
                         max_bytes_per_export=200 * 1024 * 1024)

    sql = f"SELECT * FROM dbt.{ch_table} LIMIT 100"
    try:
        info = mgr.create("live_smoke", sql, ch, table_name="snapshot")
        r.check("create from live CH", True,
                f"rows={info['row_count']}, bytes={info['bytes']}")
    except Exception as e:
        r.check("create from live CH", False, f"{type(e).__name__}: {e}")
        mgr.shutdown()
        return

    res = mgr.query("live_smoke", "SELECT count(*) FROM snapshot")
    r.check("count(*) matches reported row_count",
            res["rows"][0][0] == info["row_count"],
            f"duckdb count={res['rows'][0][0]}, reported={info['row_count']}")

    cols = mgr.query("live_smoke", "PRAGMA table_info('snapshot')")
    r.check("DuckDB sees columns", len(cols["rows"]) > 0,
            f"{len(cols['rows'])} columns mounted")

    mgr.destroy("live_smoke")
    r.check("destroy after live roundtrip", True)
    mgr.shutdown()


# ---------------------------------------------------------------------------
# tiny assertion helpers
# ---------------------------------------------------------------------------


def assert_eq(actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def expect_raises(exc_type, fn) -> None:
    try:
        fn()
    except exc_type:
        return
    except Exception as e:
        raise AssertionError(
            f"expected {exc_type.__name__}, got {type(e).__name__}: {e}"
        )
    raise AssertionError(f"expected {exc_type.__name__}, no exception raised")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="also run a live ClickHouse roundtrip")
    ap.add_argument("--table", default="api_consensus_validators_active_daily",
                    help="dbt model to use for the live roundtrip "
                         "(default: api_consensus_validators_active_daily)")
    ap.add_argument("--root", type=Path, default=None,
                    help="sandbox workspace root (default: a temp dir)")
    args = ap.parse_args()

    import tempfile
    root = args.root or Path(tempfile.mkdtemp(prefix="cerebro_phase2_smoke_"))
    print(f"Phase 2 sandbox smoke test")
    print(f"workspace root: {root}")
    print("=" * 60)

    r = Reporter()
    section_sanitizer(r)
    section_lifecycle(r, root)
    section_isolation(r, root)
    section_errors(r, root)
    section_lru(r, root)
    section_ttl(r, root)
    section_perf(r, root)
    if args.live:
        section_live(r, root, args.table)
    else:
        print("\n=== 8. LIVE ClickHouse roundtrip — SKIPPED (pass --live to enable) ===")

    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
