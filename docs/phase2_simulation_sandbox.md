# Phase 2 — DuckDB + Parquet Simulation Sandbox

**Status:** shipped (Sprint 2 of the next-gen architecture plan).
**Goal:** give simulator-style agents (`mmm_simulator`, `forecasting_analyst`)
a real way to run counterfactual SQL — UPDATE, INSERT, DELETE — without
ever touching production ClickHouse.

This document describes everything that landed in Phase 2: the parquet
export pipeline from CH, the in-memory DuckDB sandbox, the MCP tools,
the lifecycle hooks, the persona changes, and how to use it end-to-end.

---

## Why this exists

ClickHouse runs in `readonly=1`. That's the right default for analyst
safety, but it leaves simulator agents with nothing but prose. Before
Phase 2, a question like "what if Gnosis Pay cashback was +30% for the
last 90 days, what would cumulative volume look like?" would be answered
by the agent estimating the delta in its head and writing a paragraph.
There's no audit trail, no joinable detail, no way for a reviewer to
re-run the math.

After Phase 2, the agent forks the relevant CH slice into a DuckDB
sandbox, mutates the snapshot, re-aggregates with the same CH-style SQL
it would have written anyway, and reports a numerical delta backed by an
on-disk parquet file the operator can replay.

Production CH never sees a write.

---

## What was implemented

### 1. ClickHouse → Parquet exporter

**File:** `src/cerebro_mcp/clickhouse_client.py` (additions).

A new method `ClickHouseManager.export_to_parquet(sql, output_path,
max_bytes, database)` streams a SELECT through the existing Arrow path
(`query_arrow_stream`) and writes zstd-compressed parquet batch by batch.

Two safety guards before the stream starts:

- `safety.validate_query` runs (same allowlist that protects
  `execute_query`).
- `DESCRIBE (sql)` introspects the inner-query column types so we can
  build a sanitizing outer SELECT.

Two safety guards during the stream:

- `output_path.stat().st_size` is checked after every batch — if the
  running size exceeds `max_bytes`, we abort and unlink the partial file.
- Any exception cleans up the in-flight parquet writer + workspace.

### 2. Parquet type sanitizer

**Why:** ClickHouse → Arrow is not 1:1. Some CH types either crash
pyarrow or read back as opaque BLOBs in DuckDB. The sanitizer wraps the
user's SELECT in an outer SELECT that casts problematic columns to
parquet-safe equivalents.

| ClickHouse type | Cast applied | Reason |
|---|---|---|
| `Enum8 / Enum16` | `CAST(col AS String)` | clickhouse-connect surfaces as int by default; loses labels |
| `UUID` | `toString(col)` | Arrow's `fixed_size_binary[16]` reads as BLOB in DuckDB |
| `IPv4 / IPv6` | `toString(col)` | Same BLOB problem |
| `DateTime64(N)` for N > 6 | `toDateTime64(col, 6)` | Arrow ns precision is unreliable in some pyarrow builds |
| `Decimal(P, S)` for P > 38 | `CAST(col AS Float64)` | Arrow's `decimal128` caps at precision 38 |
| `Array(Tuple(...))` / nested ≥ 2 | `toString(col)` | Last-resort flatten — DuckDB chokes on deeply nested |
| `Nullable(T)` / `LowCardinality(T)` | wrappers stripped, then re-evaluate `T` | Casts still produce nullable output |
| Everything else | bare column name | Pass-through |

All column references are backtick-quoted so reserved words / unusual
chars don't break the wrapping SELECT.

Implemented as `_sanitize_column_for_parquet(name, ch_type) -> str` in
the same file, plus `_decimal_precision` / `_datetime64_precision`
helpers. All pure functions, fully unit-tested.

### 3. SandboxManager

**File:** `src/cerebro_mcp/sandbox_manager.py` (new, 350 lines).

Process-wide registry of active DuckDB sandboxes. Public API:

- **`create(sandbox_id, source_query, ch_manager, table_name="data", database="dbt")`** —
  exports CH data to `<root>/<sandbox_id>/snapshot.parquet`, opens an
  in-memory DuckDB connection, mounts the parquet via
  `CREATE TABLE <table_name> AS SELECT * FROM read_parquet(...)`. Returns
  `{sandbox_id, table, row_count, bytes, parquet_path}`.
- **`query(sandbox_id, sql)`** — runs ANY SQL inside the sandbox's DuckDB
  (read or write). Returns `{columns, rows, row_count, rows_affected}`.
- **`destroy(sandbox_id)`** — closes the connection, unlinks the parquet,
  removes the workspace dir. Idempotent (returns `False` if not found).
- **`list_sandboxes()`** — diagnostic; returns id, table, row_count,
  bytes, created_at, last_used_at, idle_seconds.
- **`sweep_expired()`** — drops sandboxes idle longer than `ttl_seconds`.
- **`shutdown()`** — atexit-safe; tears down every sandbox, logs and
  continues on per-sandbox failures.

Internals:

- `threading.RLock` — concurrency-safe; multiple agents can hit different
  sandboxes simultaneously, same sandbox serializes.
- LRU eviction triggers when `len(_sandboxes) >= max_concurrent`.
  Evicted in `_evict_oldest_locked` by `last_used_at`.
- `_validate_sandbox_id` regex (`[a-zA-Z0-9_-]{1,64}`) prevents
  path-traversal in workspace dirs.
- `_validate_table_name` regex (plain SQL identifier) prevents injection
  in the `CREATE TABLE` statement.
- Failed exports clean up their workspace dir before propagating; failed
  mounts unlink the parquet too. No leaked state.

Singleton accessor: `default_sandbox_manager()`. Tests construct fresh
instances with overridden roots / fake CH clients.

### 4. MCP tools

**File:** `src/cerebro_mcp/tools/sandbox.py` (new).

Four tools, all delegating to `default_sandbox_manager()`:

- **`create_simulation_sandbox(sandbox_id, source_query, table_name="data", database="dbt")`** —
  fork CH data into a sandbox.
- **`query_sandbox(sandbox_id, sql, max_rows=200)`** — run any SQL
  (read or write) against the sandbox.
- **`destroy_sandbox(sandbox_id)`** — tear down. Idempotent.
- **`list_sandboxes()`** — diagnostic.

Wired into the server in `register_sandbox_tools(mcp, ch)` and called
from `server.py` next to `register_custom_query_tools(mcp, ch)`.

### 5. Configuration

**File:** `src/cerebro_mcp/config.py` (additions).

```python
SANDBOX_ROOT: str = ".cerebro/sandboxes"
SANDBOX_MAX_CONCURRENT: int = 4         # LRU-evicted past this
SANDBOX_TTL_SECONDS: int = 1800         # 30 min idle → swept
SANDBOX_MAX_BYTES_PER_EXPORT: int = 2 * 1024 * 1024 * 1024   # 2 GB
```

All env-overridable. Defaults are conservative — bump
`SANDBOX_MAX_BYTES_PER_EXPORT` if you have agents materializing large
historical windows.

### 6. Lifecycle hooks

**File:** `src/cerebro_mcp/bootstrap.py` (additions).

Two functions:

- **`install_sandbox_atexit()`** — idempotent. Registers an `atexit`
  hook that calls `SandboxManager.shutdown()` so process exit reclaims
  every parquet + DuckDB connection.
- **`install_sandbox_sweeper(loop)`** — schedules a periodic
  `sweep_expired()` task on the given event loop. Cadence is
  `SANDBOX_TTL_SECONDS // 6` (clamped to `[60s, 600s]`) so sandboxes
  don't outlive their TTL by more than ~17%.

`install_sandbox_atexit` is called from `server.py:main()` after manifest
loading. `install_sandbox_sweeper` is called lazily on the first
`create_simulation_sandbox` call, so it grabs the actual asyncio loop
FastMCP runs on.

### 7. Persona updates

**Files:**
- `src/cerebro_mcp/prompts/agents/mmm_simulator.md`
- `src/cerebro_mcp/prompts/agents/forecasting_analyst.md`

Both gained a "Sandbox workflow" section documenting the
fork → mutate → re-aggregate → destroy pattern with concrete examples.
Guidance: use the sandbox for any counterfactual that touches >10 rows
or >2 dimensions. Pure-formula deltas (single multiplier on a single
aggregate) can stay in prose.

### 8. Dependency

**File:** `pyproject.toml`.

```toml
"duckdb>=1.0",
```

Pre-built wheels for darwin/linux/windows. No native build step.

### 9. Tests

**File:** `tests/test_phase2_sandbox.py` (new, 23 tests).

Coverage:

- **Sanitizer unit tests** (10): plain types, Enum, UUID, IPv4, DateTime64
  precision, Decimal precision, nested arrays, Nullable / LowCardinality
  unwrapping, helper precision parsers.
- **Lifecycle tests** (10): create/query/destroy roundtrip, mutation
  visible inside sandbox, sandbox-to-sandbox isolation, LRU eviction at
  capacity, LRU picks least-recently-used (not just oldest), TTL sweep,
  shutdown clears all, query unknown sandbox raises, export failure
  cleans workspace, idempotent destroy.
- **Validation tests** (3): invalid sandbox_id rejected, invalid table
  name rejected, duplicate sandbox_id rejected.

Tests use a `FakeCH` stub whose `export_to_parquet` writes from an
in-memory pyarrow table — so the suite runs in 3s with no network or
ClickHouse dependency.

---

## End-to-end usage

### Example 1: MMM cashback counterfactual

```
# 1) Fork the last 90 days of Gnosis Pay KPIs into a sandbox.
create_simulation_sandbox(
    sandbox_id="gpay_q2_baseline",
    source_query='''
        SELECT day,
               sum(payment_volume_usd) AS volume,
               sum(cashback_usd)        AS cashback,
               count(distinct user_id)  AS active_users
        FROM dbt.fct_execution_gpay_kpi_daily
        WHERE day >= today() - 90
        GROUP BY day
    ''',
    table_name="baseline",
)

# 2) Apply the +30% cashback shock.
query_sandbox(
    sandbox_id="gpay_q2_baseline",
    sql="UPDATE baseline SET cashback = cashback * 1.3",
)

# 3) Compare.
query_sandbox(
    sandbox_id="gpay_q2_baseline",
    sql='''
        SELECT
            sum(cashback)                                  AS new_cashback_total,
            (sum(cashback) - sum(cashback) / 1.3)          AS cashback_delta_usd,
            sum(cashback) / nullif(sum(volume), 0) * 100   AS new_cashback_pct
        FROM baseline
    ''',
)

# 4) Tear down.
destroy_sandbox(sandbox_id="gpay_q2_baseline")
```

### Example 2: forecasting with synthetic future rows

```
create_simulation_sandbox(
    sandbox_id="validators_zerogrowth_q3",
    source_query='''
        SELECT day, count(*) AS active_validators
        FROM dbt.api_consensus_validators_active_daily
        WHERE day >= today() - 365
        GROUP BY day
    ''',
    table_name="series",
)

query_sandbox(
    sandbox_id="validators_zerogrowth_q3",
    sql='''
        INSERT INTO series
        SELECT day, last_value(active_validators)
        FROM (SELECT * FROM series ORDER BY day DESC LIMIT 1) AS last_row
        CROSS JOIN UNNEST(generate_series(today() + 1, today() + 90, INTERVAL 1 DAY)) AS t(day)
    ''',
)

# now run the same forecasting SQL you'd run on CH against `series`
query_sandbox(sandbox_id="validators_zerogrowth_q3", sql="SELECT ...")

destroy_sandbox(sandbox_id="validators_zerogrowth_q3")
```

---

## Live-run findings (2026-04-27)

A 74-step session ran all 5 prompts from the smoke prompt set against the
live cerebro-mcp deployment. Notable outcomes:

- **Sandbox tools were invoked twice** (`create_simulation_sandbox` ×2,
  `query_sandbox` ×7, `destroy_sandbox` ×2, `list_sandboxes` ×2). The
  agent correctly chose the sandbox path for the multi-scenario research
  report (Q2 2025, three independent shocks over 18,587 wallet-months)
  and for the explicit forcing prompt.
- **The agent correctly skipped the sandbox** for pure-formula deltas
  (single multiplier on a single aggregate), computing them in raw CH
  SQL instead. This matches the persona-prompt guidance — Phase 2 was
  *not* over-used.
- **The lifecycle was clean**: every `destroy_sandbox` was followed by a
  successful `list_sandboxes` returning empty. Zero leaked sandboxes
  after the run.
- **One real bug surfaced** (see "Known issue: CH Date → DuckDB
  USMALLINT" below).
- **One CH-side SQL error** unrelated to the sandbox: an aggregate
  function nested inside another aggregate. Agent self-corrected on the
  next attempt.
- **Final artifact**: a `generate_research_report` with 9 charts, a
  3-scenario comparison table, narrative analysis, and a "what these
  scenarios miss" section calling out the model's own limitations
  (volume↔cashback elasticity, cohort heterogeneity). This kind of
  self-aware writeup is exactly what the sandbox makes possible — the
  numbers are auditable, so the agent is free to be honest about what
  isn't modeled.

### Known issue: CH `Date` → DuckDB `USMALLINT` (fixed in 2026-04-27 patch)

When CH `Date` was exported through Arrow into parquet, DuckDB read it
back as `USMALLINT` (its physical storage of `DATE` as days-since-epoch).
SQL like `strftime(month, '%Y-%m')` failed with:

```
Binder Error: No function matches the given name and argument types
'strftime(USMALLINT, STRING_LITERAL)'
```

Fixed by extending `_sanitize_column_for_parquet` to wrap CH `Date` /
`Date32` columns in `toDate32(col)` before export. `Date32` exports as
Arrow `date32[day]`, which DuckDB infers as a real `DATE`. Sanitized
column expressions can be inspected with the `_sanitize_column_for_parquet`
unit tests in `tests/test_phase2_sandbox.py`.

The agent in the live run worked around it manually with
`DATE '1970-01-01' + INTERVAL (month) DAY` — that pattern still works
post-fix, but is no longer necessary.

## Operational characteristics

| Property | Value | Notes |
|---|---|---|
| Sandbox state | local disk + in-memory DuckDB | Per-process; not shared between server instances |
| Production CH writes | **zero** | `readonly=1` enforced at CH client level |
| Per-sandbox disk cost | ≤ `SANDBOX_MAX_BYTES_PER_EXPORT` (default 2 GB) | zstd-compressed parquet |
| Per-sandbox memory | DuckDB `:memory:` connection (variable) | Bounded by DuckDB's own memory_limit (default ~80% RAM) |
| Concurrency | `RLock` per manager | Different sandboxes parallel; same sandbox serialized |
| Eviction | LRU at `SANDBOX_MAX_CONCURRENT` | Default 4 |
| Idle TTL | 30 min default | Swept by background task at TTL/6 cadence |
| Crash safety | atexit teardown | Process kill leaves parquet on disk; manual cleanup of `.cerebro/sandboxes/` if needed |

---

## What was deliberately NOT done

- **No persistent DuckDB on disk.** Sandboxes are `:memory:` only — when
  the process dies, the sandbox is gone (the parquet remains as a
  recovery artifact but isn't auto-remounted). Persistent DuckDB is a
  Phase 3 candidate if "resume mid-simulation" becomes a requirement.
- **No SQL safety check inside the sandbox.** The CH-side export is
  validated by `safety.validate_query`, but `query_sandbox` accepts any
  DuckDB SQL — that's the whole point. Sandboxes are isolated from CH;
  there's nothing to protect there.
- **No multi-table sandboxes.** Each `create_simulation_sandbox` call
  produces one parquet → one DuckDB table. Multi-table joins inside a
  sandbox would require a `query_sandbox` that issues
  `CREATE TABLE ... AS SELECT * FROM read_parquet(other_sandbox)` —
  doable but not yet exposed. Easy add when a use case emerges.
- **No automatic schema migration.** If the source CH schema changes
  between sandbox creations, the sandbox carries the old schema until
  destroyed. By design — the whole point is to freeze a slice in time.
- **No GC of orphan workspaces.** If a process crashes mid-export, a
  partial parquet may stay in `.cerebro/sandboxes/<id>/`. Production
  cleanup happens via TTL+atexit; manual cleanup of the root dir is
  fine when needed.

---

## Files touched

| File | Change |
|---|---|
| `pyproject.toml` | +`duckdb>=1.0` |
| `src/cerebro_mcp/clickhouse_client.py` | +`export_to_parquet`, +`_sanitize_column_for_parquet`, +helpers |
| `src/cerebro_mcp/sandbox_manager.py` | **new** — `SandboxManager`, `Sandbox`, validators, singleton |
| `src/cerebro_mcp/tools/sandbox.py` | **new** — 4 MCP tools, lazy sweeper installer |
| `src/cerebro_mcp/config.py` | +`SANDBOX_ROOT`, +`SANDBOX_MAX_CONCURRENT`, +`SANDBOX_TTL_SECONDS`, +`SANDBOX_MAX_BYTES_PER_EXPORT` |
| `src/cerebro_mcp/bootstrap.py` | +`install_sandbox_atexit`, +`install_sandbox_sweeper` |
| `src/cerebro_mcp/server.py` | +`register_sandbox_tools` import + call, +atexit install in `main()` |
| `src/cerebro_mcp/prompts/agents/mmm_simulator.md` | +"Sandbox workflow" section |
| `src/cerebro_mcp/prompts/agents/forecasting_analyst.md` | +"Sandbox for scenario forecasts" section |
| `tests/test_phase2_sandbox.py` | **new** — 23 tests |
| `docs/phase2_simulation_sandbox.md` | **new** — this document |

---

## Reproducing the tests

```bash
python -m pytest tests/test_phase2_sandbox.py -v
python -m pytest tests/ -q   # full suite — should be 626 + 4 skipped
```

End-to-end against live CH:

1. Set your `.env` so cerebro-mcp can reach ClickHouse.
2. Start the server: `cerebro-mcp` (stdio) or `cerebro-mcp --sse`.
3. From any MCP client:
   ```
   create_simulation_sandbox(
       sandbox_id="smoke",
       source_query="SELECT 1 AS x",
   )
   query_sandbox(sandbox_id="smoke", sql="SELECT * FROM data")
   query_sandbox(sandbox_id="smoke", sql="UPDATE data SET x = 99")
   query_sandbox(sandbox_id="smoke", sql="SELECT * FROM data")  # → 99
   destroy_sandbox(sandbox_id="smoke")
   ```

---

## Next phases

- **Phase 3** — resumable gated workflows (SQLite event log,
  parallel-fanout dispatcher, crash recovery). Separate from sandboxes.
- **Phase 4** — non-blocking event loop (ProcessPoolExecutor for CPU
  work). Touches the BM25 index build from Phase 1; sandboxes themselves
  are not on a hot CPU path.
