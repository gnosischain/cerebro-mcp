"""Mini-app platform infrastructure: registry, view store, dataset loader, hydration.

This module is shared by every concrete mini app (Token Explorer, Metric Lab,
future apps). It owns:

* the **app registry** — maps ``app_id`` to ``MiniAppDefinition``
* the **view store** — keeps live ``ViewRecord`` objects keyed by ``view_id``
* the **structured loader** — runs ClickHouse queries and returns raw row
  data, bypassing the markdown formatters in ``tool_output.py``
* the **bounded dataset sampler** — selects between
  ``exact_bounded`` / ``random_sample`` / ``preview_only`` modes using
  deterministic hash bucketing (never ``ORDER BY rand()``)
* the **page-token hydration helper** — serves dataset pages out of the
  in-memory cache without re-querying ClickHouse
* the **app-only hydration tools** ``get_mini_app_rows`` and
  ``get_mini_app_state``, plus the ``install_app_only_filter`` helper that
  removes them from the model-facing tool list while leaving them callable
  by the frontend (which goes through a different code path)
"""

from __future__ import annotations

import hashlib
import importlib.resources
import logging
import math
import json
import inspect
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.clients.clickhouse import (
    ClickHouseManager,
    ExecutedQuery,
    QueryBudget,
)
from cerebro_mcp.runtime.mini_app_cache import (
    CachedDataset,
    CachedFailure,
    FailureCache,
    get_cache,
    make_cache_key,
)
from cerebro_mcp.models.mini_app import (
    DatasetDescriptor,
    DatasetMode,
    DatasetSchemaColumn,
    DatasetStats,
    MiniAppPayload,
    SummaryCard,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App registry
# ---------------------------------------------------------------------------


@dataclass
class MiniAppDefinition:
    app_id: str
    title: str
    resource_uri: str


_apps: dict[str, MiniAppDefinition] = {}
_apps_lock = threading.Lock()


def register_app(app_id: str, title: str, resource_uri: str) -> MiniAppDefinition:
    """Register an app. Idempotent — second call with same app_id is a no-op."""
    with _apps_lock:
        existing = _apps.get(app_id)
        if existing:
            return existing
        defn = MiniAppDefinition(app_id=app_id, title=title, resource_uri=resource_uri)
        _apps[app_id] = defn
        return defn


def get_app(app_id: str) -> MiniAppDefinition | None:
    with _apps_lock:
        return _apps.get(app_id)


def list_apps() -> list[MiniAppDefinition]:
    with _apps_lock:
        return list(_apps.values())


# ---------------------------------------------------------------------------
# View store
# ---------------------------------------------------------------------------


_VIEW_TTL = timedelta(minutes=15)
_VIEW_MAX = 50
_VIEW_PAGE_SIZE = 500
_VIEW_MAX_PAGE_SIZE = 10_000
# Keep a single hydration response bounded even when a caller asks for a very
# large page of wide forensic rows.  The row-count limit remains authoritative;
# this byte budget merely selects a smaller safe page when needed.
_VIEW_PAGE_BYTES = 2_000_000


@dataclass
class ViewRecord:
    view_id: str
    app_id: str
    title: str
    created_at: datetime
    expires_at: datetime
    view_state: dict[str, Any] = field(default_factory=dict)
    datasets: dict[str, CachedDataset] = field(default_factory=dict)
    # Monotonic per-dataset-key revision, bumped on every attach/replace.
    # Frontends key hydration / draft reseeding on this — NOT on SQL text,
    # which stays identical across param changes and forced reruns.
    dataset_revisions: dict[str, int] = field(default_factory=dict)
    # Independent newest-wins channels. A focus request must not supersede a
    # receipt load, and pagination must not supersede Money Trail state.
    request_revisions: dict[str, int] = field(default_factory=dict)


_views: dict[str, ViewRecord] = {}
_views_lock = threading.Lock()


def _prune_views_locked() -> None:
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _views.items() if now > v.expires_at]
    for k in expired:
        del _views[k]
    while len(_views) > _VIEW_MAX:
        oldest = min(_views, key=lambda k: _views[k].expires_at)
        del _views[oldest]


def _touch_view_locked(record: ViewRecord) -> None:
    record.expires_at = datetime.now(timezone.utc) + _VIEW_TTL


def create_view(app_id: str, title: str) -> str:
    view_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    record = ViewRecord(
        view_id=view_id,
        app_id=app_id,
        title=title,
        created_at=now,
        expires_at=now + _VIEW_TTL,
    )
    with _views_lock:
        _views[view_id] = record
        _prune_views_locked()
    return view_id


def get_view(view_id: str) -> ViewRecord | None:
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            return None
        if datetime.now(timezone.utc) > record.expires_at:
            del _views[view_id]
            return None
        _touch_view_locked(record)
        return record


def snapshot_view(view_id: str) -> ViewRecord | None:
    """Return a coherent, detached snapshot of one live view.

    CachedDataset objects are immutable for a dataset revision, so the dataset
    mapping is copied shallowly while mutable state/revision maps are detached.
    """
    with _views_lock:
        record = _views.get(view_id)
        if record is None or datetime.now(timezone.utc) > record.expires_at:
            if record is not None:
                del _views[view_id]
            return None
        _touch_view_locked(record)
        return ViewRecord(
            view_id=record.view_id,
            app_id=record.app_id,
            title=record.title,
            created_at=record.created_at,
            expires_at=record.expires_at,
            view_state=deepcopy(record.view_state),
            datasets=dict(record.datasets),
            dataset_revisions=dict(record.dataset_revisions),
            request_revisions=dict(record.request_revisions),
        )


def commit_view_update(
    view_id: str,
    *,
    request_channel: str = "",
    request_id: int = 0,
    guard_channels: tuple[str, ...] | list[str] = (),
    datasets: dict[str, CachedDataset] | None = None,
    remove_datasets: tuple[str, ...] | list[str] = (),
    state_patch: dict[str, Any] | None = None,
) -> bool:
    """Atomically commit datasets and state if a request is not stale.

    Heavy queries run before this call. Only the final newest-wins comparison,
    revision bumps, dataset replacement, and state merge occur under the view
    lock. Legacy request id ``0`` remains accepted without advancing a channel.
    """
    normalized_channel = str(request_channel or "").strip()
    normalized_request_id = max(0, int(request_id or 0))
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            raise KeyError(f"Unknown view_id: {view_id}")
        if normalized_channel and normalized_request_id:
            current = int(record.request_revisions.get(normalized_channel, 0))
            if normalized_request_id < current:
                return False
            for channel in guard_channels:
                guarded = str(channel or "").strip()
                if guarded and normalized_request_id < int(
                    record.request_revisions.get(guarded, 0)
                ):
                    return False
            record.request_revisions[normalized_channel] = normalized_request_id
        elif normalized_channel:
            # A legacy request that never reserved an effective revision must
            # not overwrite evidence after any revisioned request has begun.
            if int(record.request_revisions.get(normalized_channel, 0)) > 0:
                return False
        for key in remove_datasets:
            record.datasets.pop(key, None)
            record.dataset_revisions.pop(key, None)
        for key, dataset in (datasets or {}).items():
            record.datasets[key] = dataset
            record.dataset_revisions[key] = (
                record.dataset_revisions.get(key, 0) + 1
            )
        if state_patch:
            record.view_state = _deep_merge(record.view_state, state_patch)
        _touch_view_locked(record)
        return True


def begin_view_request(
    view_id: str,
    *,
    request_channel: str,
    request_id: int,
) -> bool:
    """Reserve a loader revision before doing expensive work.

    Advancing a channel only when its result commits leaves a race where an
    older request can publish after a newer request has started. Loaders call
    this immediately after validating arguments, run their queries outside the
    lock, and finally call :func:`commit_view_update` for the same channel.
    Request id ``0`` retains the legacy unversioned behavior.
    """
    normalized_channel = str(request_channel or "").strip()
    normalized_request_id = max(0, int(request_id or 0))
    if not normalized_channel:
        raise ValueError("request_channel is required")
    if normalized_request_id == 0:
        return True
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            raise KeyError(f"Unknown view_id: {view_id}")
        current = int(record.request_revisions.get(normalized_channel, 0))
        if normalized_request_id < current:
            return False
        record.request_revisions[normalized_channel] = normalized_request_id
        _touch_view_locked(record)
        return True


def reserve_view_request(
    view_id: str,
    *,
    request_channel: str,
    request_id: int = 0,
) -> int | None:
    """Reserve and return an effective positive request revision.

    Public/legacy tool callers may omit ``request_id``. Allocating that call a
    server-side revision is essential: treating zero as permanently
    unversioned lets a slow legacy query overwrite a newer browser request.
    ``None`` means the supplied nonzero id was already stale.
    """
    normalized_channel = str(request_channel or "").strip()
    if not normalized_channel:
        raise ValueError("request_channel is required")
    requested = max(0, int(request_id or 0))
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            raise KeyError(f"Unknown view_id: {view_id}")
        current = int(record.request_revisions.get(normalized_channel, 0))
        if requested and requested < current:
            return None
        effective = requested or (current + 1)
        record.request_revisions[normalized_channel] = effective
        _touch_view_locked(record)
        return effective


def get_view_title(view_id: str) -> str:
    record = get_view(view_id)
    return record.title if record else ""


def patch_view_state(view_id: str, patch: dict[str, Any]) -> ViewRecord | None:
    """Deep-merge ``patch`` into the view's ``view_state`` and return the record."""
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            return None
        record.view_state = _deep_merge(record.view_state, patch)
        _touch_view_locked(record)
        return record


def set_view_state(view_id: str, state: dict[str, Any]) -> ViewRecord | None:
    """EXACT view-state replacement (INITIAL_LOAD semantics).

    ``patch_view_state`` deep-merges, so stale keys (a cleared
    aggregate_config, a removed dataset's provenance) would survive a
    reload. INITIAL_LOAD-emitting paths must use this instead.
    """
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            return None
        record.view_state = dict(state)
        _touch_view_locked(record)
        return record


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def attach_dataset(view_id: str, key: str, dataset: CachedDataset) -> None:
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            raise KeyError(f"Unknown view_id: {view_id}")
        record.datasets[key] = dataset
        record.dataset_revisions[key] = record.dataset_revisions.get(key, 0) + 1
        _touch_view_locked(record)


def replace_view_datasets(
    view_id: str, datasets: dict[str, CachedDataset]
) -> None:
    """Atomically replace all datasets attached to a view."""
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            raise KeyError(f"Unknown view_id: {view_id}")
        record.datasets = dict(datasets)
        record.dataset_revisions = {
            key: record.dataset_revisions.get(key, 0) + 1 for key in datasets
        }
        _touch_view_locked(record)


def remove_view_datasets(view_id: str, keys: list[str] | tuple[str, ...]) -> None:
    """Detach the named datasets from a view.

    Revisions for removed keys are dropped so a later re-attach restarts the
    revision counter for that key at 1 — frontends key hydration on the
    (key, revision) pair, so a fresh attach always re-hydrates.
    Unknown keys are ignored (idempotent eviction).
    """
    with _views_lock:
        record = _views.get(view_id)
        if record is None:
            raise KeyError(f"Unknown view_id: {view_id}")
        for key in keys:
            record.datasets.pop(key, None)
            record.dataset_revisions.pop(key, None)
        _touch_view_locked(record)


def reset_views_for_tests() -> None:
    with _views_lock:
        _views.clear()
    with _apps_lock:
        # apps are persistent across runs, but tests may want a clean slate
        pass


# ---------------------------------------------------------------------------
# Structured loader (bypasses markdown formatters)
# ---------------------------------------------------------------------------


@dataclass
class StructuredResult:
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    row_count: int
    sql: str
    database: str
    elapsed_seconds: float
    warnings: list[str]


def _column_types_from_executed(executed: ExecutedQuery) -> list[str]:
    """Best-effort column type inference from the first non-null row.

    The ``ExecutedQuery`` shape doesn't carry per-column types from the
    arrow / native fetcher, so we infer them lightly here. Used only as a
    presentation hint by Metric Lab.
    """
    types: list[str] = []
    for col_idx, _name in enumerate(executed.columns):
        inferred = "Unknown"
        for row in executed.rows:
            if col_idx >= len(row):
                continue
            value = row[col_idx]
            if value is None:
                continue
            inferred = type(value).__name__
            break
        types.append(inferred)
    return types


def run_structured_query(
    ch: ClickHouseManager,
    sql: str,
    database: str = "dbt",
    parameters: dict[str, Any] | None = None,
    requested_max_rows: int = 5000,
    query_budget: QueryBudget | None = None,
) -> StructuredResult:
    """Run ``sql`` and return raw rows / columns without markdown formatting."""
    run_kwargs: dict[str, Any] = {
        "requested_max_rows": requested_max_rows,
        "audience": "internal",
        "fetch_mode": "auto",
        "parameters": parameters,
    }
    if query_budget is not None:
        try:
            signature = inspect.signature(ch.run_query)
            supports_budget = "query_budget" in signature.parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            supports_budget = False
        if supports_budget:
            run_kwargs["query_budget"] = query_budget
    executed = ch.run_query(sql, database, **run_kwargs)
    return StructuredResult(
        columns=list(executed.columns),
        column_types=_column_types_from_executed(executed),
        rows=[list(row) for row in executed.rows],
        row_count=executed.row_count,
        sql=executed.sql,
        database=executed.database,
        elapsed_seconds=executed.elapsed_seconds,
        warnings=list(executed.warnings),
    )


# ---------------------------------------------------------------------------
# Bounded dataset sampler
# ---------------------------------------------------------------------------


SAMPLE_TARGET = 10_000
PREVIEW_ROW_CAP = 1_000
HARD_TOTAL_LIMIT = 1_000_000_000


def _friendly_query_error(exc: Exception) -> str:
    """Rewrite gnarly ClickHouse failures into actionable messages.

    MEMORY_LIMIT_EXCEEDED (code 241) is the big one: most dbt models are
    views, so loading one executes its full aggregation — when that trips
    the per-query cap, tell the user how to narrow instead of dumping the
    raw allocator trace.
    """
    from cerebro_mcp.config import settings as _settings

    text = str(exc)
    if "MEMORY_LIMIT_EXCEEDED" in text or "Code: 241" in text or "code: 241" in text:
        cap = _settings.CLICKHOUSE_MAX_QUERY_MEMORY_GB
        cap_txt = f"{cap:g} GiB" if cap > 0 else "the server's"
        return (
            "This model is too heavy to load whole — it is a view that "
            "aggregates a large table and exceeded the "
            f"{cap_txt} per-query memory cap. Load it with a shorter time "
            "window or a lower row limit. "
            f"(ClickHouse: {text[:200]})"
        )
    return text


class MiniAppQueryError(RuntimeError):
    """Raised when the user-supplied query cannot be executed at all.

    This signals a broken query that the caller should surface as an
    explicit error (``isError=True`` ``CallToolResult``), not a silent
    empty ``preview_only`` dataset. Preview-mode fallback is reserved for
    the case where the query *runs* but we cannot safely sample it — e.g.,
    a hash function unsupported on a particular column type.
    """


def _wrap_count(sql: str) -> str:
    return f"SELECT count() AS c FROM (\n{sql}\n) AS _ml_count"


def _wrap_limit(sql: str, limit: int) -> str:
    return f"SELECT * FROM (\n{sql}\n) AS _ml_inner LIMIT {int(limit)}"


def _wrap_hash_sample(sql: str, cutoff: int, target: int) -> str:
    cutoff = max(1, min(100, int(cutoff)))
    return (
        "SELECT * FROM (\n"
        "  SELECT * FROM (\n"
        f"    {sql}\n"
        "  ) AS _ml\n"
        f"  WHERE cityHash64(toString(tuple(*))) % 100 < {cutoff}\n"
        f"  LIMIT {int(target)}\n"
        ") AS _sample"
    )


def _pick_cutoff(total: int, target: int, attempt: int) -> int:
    """Choose a hash-bucket cutoff (1..100) sized to land near ``target``.

    The expected post-filter row count is ``total * cutoff / 100``, so to
    hit ``target`` rows we want roughly ``cutoff = target * 100 / total``.
    A safety multiplier widens the window on retry.
    """
    if total <= 0:
        return 100
    multiplier = 1.5 + 1.5 * attempt
    raw = math.ceil(target * 100 * multiplier / total)
    return max(1, min(100, raw))


def _stats(
    *,
    rows: list[list[Any]],
    mode: DatasetMode,
    source_rows: int,
    elapsed: float,
    warnings: list[str],
) -> DatasetStats:
    return DatasetStats(
        row_count=len(rows),
        rows_returned=len(rows),
        mode=mode,
        sample_source_rows=source_rows,
        elapsed_seconds=elapsed,
        warnings=warnings,
    )


def load_bounded_dataset(
    ch: ClickHouseManager,
    sql: str,
    database: str = "dbt",
    parameters: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> CachedDataset:
    """Return a ``CachedDataset`` sized via the dataset-mode rules.

    Algorithm:
      1. Cache hit on any (sql, db, params, mode) → return immediately
         (skipped with ``force_refresh=True`` — an explicit user rerun must
         actually hit ClickHouse; the fresh result still refills the cache).
      2. Cheap ``count()`` over the wrapped subquery.
      3. ``count <= SAMPLE_TARGET`` → ``exact_bounded``.
      4. Otherwise try deterministic hash-bucket sampling, with one retry
         using a wider cutoff if the first attempt under-samples.
      5. If sampling raises or under-samples on both attempts, fall back to
         ``preview_only`` (≤200 rows, analytics disabled).
    """
    cache = get_cache()

    # 1. cache check across all modes
    if not force_refresh:
        for mode in ("exact_bounded", "random_sample", "preview_only"):
            hit = cache.get(make_cache_key(sql, database, parameters, mode))
            if hit is not None:
                return hit

    # 2. count — a failure here means the user's SQL is broken; propagate.
    try:
        count_result = run_structured_query(
            ch, _wrap_count(sql), database, parameters, requested_max_rows=1
        )
        total = int(count_result.rows[0][0]) if count_result.rows else 0
    except Exception as exc:
        logger.warning("mini_app count failed: %s", exc)
        raise MiniAppQueryError(_friendly_query_error(exc)) from exc

    # 3. exact path — if count succeeded the query is valid, so any failure
    # here is unexpected and should also surface as an error.
    if total <= SAMPLE_TARGET:
        try:
            exact = run_structured_query(
                ch,
                _wrap_limit(sql, SAMPLE_TARGET),
                database,
                parameters,
                requested_max_rows=SAMPLE_TARGET,
            )
        except Exception as exc:
            logger.warning("mini_app exact load failed: %s", exc)
            raise MiniAppQueryError(_friendly_query_error(exc)) from exc
        dataset = _finalize_dataset(
            sql=sql,
            database=database,
            parameters=parameters,
            result=exact,
            mode="exact_bounded",
            source_rows=total,
            warnings=[],
        )
        cache.put(
            make_cache_key(sql, database, parameters, "exact_bounded"), dataset
        )
        return dataset

    # 4. random_sample with retry, but only if total is sane
    if total <= HARD_TOTAL_LIMIT:
        for attempt in (0, 1):
            cutoff = _pick_cutoff(total, SAMPLE_TARGET, attempt)
            sample_sql = _wrap_hash_sample(sql, cutoff, SAMPLE_TARGET)
            try:
                sample = run_structured_query(
                    ch,
                    sample_sql,
                    database,
                    parameters,
                    requested_max_rows=SAMPLE_TARGET,
                )
            except Exception as exc:
                logger.info(
                    "mini_app hash-sample attempt %d failed: %s", attempt, exc
                )
                continue
            if sample.row_count >= SAMPLE_TARGET // 2:
                warning = (
                    f"Showing an approximate random sample of "
                    f"{sample.row_count:,} rows out of {total:,}. "
                    "Client-side aggregations are estimates."
                )
                dataset = _finalize_dataset(
                    sql=sql,
                    database=database,
                    parameters=parameters,
                    result=sample,
                    mode="random_sample",
                    source_rows=total,
                    warnings=[warning],
                )
                cache.put(
                    make_cache_key(sql, database, parameters, "random_sample"),
                    dataset,
                )
                return dataset

    # 5. preview_only fallback (also covers HARD_TOTAL_LIMIT short-circuit)
    return _load_preview_only(ch, sql, database, parameters, source_rows=total)


def load_exact_capped_dataset(
    ch: ClickHouseManager,
    sql: str,
    database: str = "dbt",
    parameters: dict[str, Any] | None = None,
    force_refresh: bool = False,
    cache_ttl_seconds: int = 300,
    row_cap: int = SAMPLE_TARGET,
    exact_source_rows: bool = True,
    query_budget: QueryBudget | None = None,
) -> CachedDataset:
    """Load the newest deterministic rows without random sampling.

    ``sql`` must be an unbounded, deterministically ordered SELECT.  A window
    count is attached to the same capped query, so ClickHouse executes the
    filtered/aggregated statement once while still reporting the exact source
    row count.  This is intended for forensic/analyst tables where a random
    sample would destroy ordering and make pagination misleading.

    ``query_budget`` (optional) is forwarded to that single ClickHouse
    round-trip so interactive apps can cap execution time / memory / result
    rows / threads per query.

    ``exact_source_rows=False`` (opt-in, for HEAVY queries) skips the
    ``count() OVER ()`` window column entirely.  An empty-frame window
    aggregate forces ClickHouse to materialize the FULL inner result set
    before the outer ``LIMIT`` can drop a single row — over an unbounded
    scan that is a guaranteed memory blowup, and it defeats the bounded
    top-N heap sort that makes ``ORDER BY … LIMIT`` memory-safe.  In this
    mode ``source_rows`` equals the returned row count and hitting the cap
    is reported as "at least cap" truncation.

    Existing ``load_bounded_dataset`` behavior remains unchanged for every
    current mini app.
    """
    if not re.search(r"\bORDER\s+BY\b", sql, flags=re.IGNORECASE):
        raise MiniAppQueryError(
            "exact_capped datasets require an explicit deterministic ORDER BY"
        )
    cap = max(1, min(int(row_cap), SAMPLE_TARGET))
    ttl_seconds = max(1, int(cache_ttl_seconds))
    count_key = "exact" if exact_source_rows else "capped_count"
    mode_key = f"exact_capped:{cap}:{ttl_seconds}:{count_key}"
    cache_key = make_cache_key(sql, database, parameters, mode_key)
    cache = get_cache()
    if not force_refresh:
        hit = cache.get(cache_key)
        if hit is not None:
            logger.info(
                "mini_app exact_capped cache_hit database=%s row_cap=%s",
                database,
                cap,
            )
            return hit
    logger.info(
        "mini_app exact_capped cache_miss database=%s row_cap=%s force_refresh=%s",
        database,
        cap,
        force_refresh,
    )

    if exact_source_rows:
        wrapped = (
            "SELECT *,count() OVER () AS __source_rows FROM (\n"
            f"{sql.rstrip()}\n) AS _ml_exact\nLIMIT {cap}"
        )
    else:
        # No window column: the inner ORDER BY … LIMIT stays a bounded top-N
        # heap. The outer LIMIT is a harmless cap for specs that already
        # limit internally.
        wrapped = f"SELECT * FROM (\n{sql.rstrip()}\n) AS _ml_exact\nLIMIT {cap}"
    try:
        rows_result = run_structured_query(
            ch,
            wrapped,
            database,
            parameters,
            requested_max_rows=cap,
            query_budget=query_budget,
        )
    except Exception as exc:
        logger.warning("mini_app exact capped load failed: %s", exc)
        raise MiniAppQueryError(_friendly_query_error(exc)) from exc

    columns = list(rows_result.columns)
    column_types = list(rows_result.column_types)
    rows = [list(row) for row in rows_result.rows]
    source_index = columns.index("__source_rows") if "__source_rows" in columns else -1
    total = int(rows[0][source_index]) if rows and source_index >= 0 else len(rows)
    if source_index >= 0:
        columns.pop(source_index)
        if source_index < len(column_types):
            column_types.pop(source_index)
        rows = [row[:source_index] + row[source_index + 1 :] for row in rows]
    truncated = total > len(rows)
    warnings = []
    if not exact_source_rows and len(rows) >= cap:
        # Source total was deliberately not counted; hitting the cap means
        # "at least cap" rows matched.
        truncated = True
        warnings.append(
            f"Showing the newest {len(rows):,} rows (at least; the full "
            "matching set was not counted for this heavy dataset). "
            "Narrow the filters to inspect the full matching set."
        )
    elif truncated:
        warnings.append(
            f"Showing the newest {len(rows):,} of {total:,} rows. "
            "Narrow the filters to inspect the full matching set."
        )
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    dataset = CachedDataset(
        columns=columns,
        column_types=column_types,
        rows=rows,
        stats=DatasetStats(
            row_count=len(rows),
            rows_returned=len(rows),
            mode="exact_capped",
            source_rows=total,
            row_cap=cap,
            truncated=truncated,
            fetched_at=fetched_at,
            elapsed_seconds=rows_result.elapsed_seconds,
            warnings=warnings,
        ),
        sql=sql,
        database=database,
        parameters=parameters,
    )
    cache.put(cache_key, dataset, ttl=timedelta(seconds=ttl_seconds))
    return dataset


def _load_preview_only(
    ch: ClickHouseManager,
    sql: str,
    database: str,
    parameters: dict[str, Any] | None,
    source_rows: int,
) -> CachedDataset:
    cache = get_cache()
    try:
        preview = run_structured_query(
            ch,
            _wrap_limit(sql, PREVIEW_ROW_CAP),
            database,
            parameters,
            requested_max_rows=PREVIEW_ROW_CAP,
        )
    except Exception as exc:
        # No fallback left — surface the ClickHouse error directly so the
        # launcher can translate it into an isError=True CallToolResult.
        logger.error("mini_app preview load failed: %s", exc)
        raise MiniAppQueryError(_friendly_query_error(exc)) from exc

    warning = (
        "Preview only; full sampling unavailable. "
        "Aggregations and correlations are disabled."
    )
    dataset = _finalize_dataset(
        sql=sql,
        database=database,
        parameters=parameters,
        result=preview,
        mode="preview_only",
        source_rows=source_rows,
        warnings=[warning],
    )
    cache.put(
        make_cache_key(sql, database, parameters, "preview_only"), dataset
    )
    return dataset


def _finalize_dataset(
    *,
    sql: str,
    database: str,
    parameters: dict[str, Any] | None,
    result: StructuredResult,
    mode: DatasetMode,
    source_rows: int,
    warnings: list[str],
) -> CachedDataset:
    return CachedDataset(
        columns=list(result.columns),
        column_types=list(result.column_types),
        rows=list(result.rows),
        stats=_stats(
            rows=result.rows,
            mode=mode,
            source_rows=source_rows,
            elapsed=result.elapsed_seconds,
            warnings=warnings,
        ),
        sql=sql,
        database=database,
        parameters=parameters,
    )


# ---------------------------------------------------------------------------
# Page-token hydration
# ---------------------------------------------------------------------------


def _encode_page_token(offset: int) -> str:
    return f"offset:{offset}"


def _decode_page_token(token: str) -> int:
    if not token:
        return 0
    match = re.fullmatch(r"offset:(0|[1-9][0-9]*)", token)
    if match is None:
        raise ValueError("Invalid dataset page token")
    return int(match.group(1))


def get_view_dataset_page(
    view_id: str,
    dataset_key: str,
    page_token: str = "",
    *,
    page_size: int | None = None,
    dataset_revision: int | None = None,
) -> dict[str, Any]:
    """Return one revision-safe, byte-bounded page of cached dataset rows.

    ``dataset_revision`` is an optimistic read guard.  A hydration loop must
    never concatenate pages from two replacements of the same dataset key.
    Older callers may omit it and retain the legacy behavior.
    """
    requested_size = _VIEW_PAGE_SIZE if page_size is None else int(page_size)
    if requested_size < 1:
        raise ValueError("page_size must be at least 1")
    selected_size = min(requested_size, _VIEW_MAX_PAGE_SIZE)

    # Snapshot the rows and metadata under the view-store lock.  The stored
    # CachedDataset is immutable for a revision, but the mapping can be
    # replaced concurrently by another app action.
    with _views_lock:
        record = _views.get(view_id)
        if record is None or datetime.now(timezone.utc) > record.expires_at:
            if record is not None:
                del _views[view_id]
            raise KeyError(f"Unknown or expired view_id: {view_id}")
        dataset = record.datasets.get(dataset_key)
        if dataset is None:
            raise KeyError(f"Unknown dataset_key: {dataset_key}")
        revision = record.dataset_revisions.get(dataset_key, 0)
        if dataset_revision is not None and int(dataset_revision) != revision:
            raise ValueError(
                f"Dataset revision changed for {dataset_key}: "
                f"requested {dataset_revision}, current {revision}"
            )
        rows = dataset.rows
        columns = list(dataset.columns)
        column_types = list(dataset.column_types)
        stats = dataset.stats.model_dump(exclude_none=True)
        _touch_view_locked(record)

    offset = _decode_page_token(page_token)
    if offset < 0 or offset > len(rows):
        raise ValueError(f"Invalid page offset for {dataset_key}: {offset}")
    candidates = rows[offset : offset + selected_size]
    page_rows: list[list[Any]] = []
    encoded_bytes = 0
    for row in candidates:
        row_bytes = len(
            json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":"))
            .encode("utf-8")
        )
        # Always return at least one row so a wide row cannot deadlock the
        # continuation chain.
        if page_rows and encoded_bytes + row_bytes > _VIEW_PAGE_BYTES:
            break
        page_rows.append(row)
        encoded_bytes += row_bytes
    end = offset + len(page_rows)
    next_token = _encode_page_token(end) if end < len(rows) else ""

    return {
        "view_id": view_id,
        "dataset_key": dataset_key,
        "dataset_revision": revision,
        "page_size": len(page_rows),
        "columns": columns,
        "column_types": column_types,
        "rows": page_rows,
        "next_page_token": next_token,
        "total_rows": len(rows),
        "stats": stats,
    }


def build_dataset_descriptor(
    *,
    key: str,
    dataset: CachedDataset,
    title: str = "",
    preview_limit: int = _VIEW_PAGE_SIZE,
    scope_id: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> DatasetDescriptor:
    """Convert a ``CachedDataset`` into a lightweight ``DatasetDescriptor``."""
    preview = dataset.rows[:preview_limit]
    has_more = len(dataset.rows) > preview_limit
    columns = [
        DatasetSchemaColumn(name=name, type=dataset.column_types[idx]
                            if idx < len(dataset.column_types) else "Unknown")
        for idx, name in enumerate(dataset.columns)
    ]
    return DatasetDescriptor(
        key=key,
        title=title or key,
        sql=dataset.sql,
        database=dataset.database,
        columns=columns,
        stats=dataset.stats,
        preview_rows=preview,
        page_token=_encode_page_token(preview_limit) if has_more else None,
        scope_id=scope_id,
        provenance=dict(provenance or {}),
    )


def collect_dataset_warnings(*datasets: CachedDataset | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ds in datasets:
        if ds is None:
            continue
        for w in ds.stats.warnings:
            if w not in seen:
                seen.add(w)
                out.append(w)
    return out


# ---------------------------------------------------------------------------
# Shared sectioned-app plumbing (CoW Explorer, Governance Explorer, ...)
# ---------------------------------------------------------------------------
#
# The v2 sectioned mini apps share app-agnostic machinery: a static-bundle
# loader, a parallel partial-failure dataset loader, section LRU retention,
# and ViewRecord -> MiniAppPayload assembly. Each app binds these with its own
# app id / database / spec catalog and keeps a thin module-local wrapper —
# mirroring the frontend's shared useGroupLoader + per-app binding pattern.


class StaticBundle:
    """Mtime-aware cached loader for a Vite-built single-file app bundle.

    ``html()`` re-reads the artifact whenever its ``(mtime_ns, size)``
    signature changes — a rebuilt bundle is picked up without a server
    restart — and falls back to a small placeholder page pointing at
    ``build_hint`` when the artifact is missing. ``diagnostics()`` exposes
    the served bundle's sha256/mtime plus the asset directory listing.
    """

    def __init__(self, filename: str, *, assets_dir: str, build_hint: str) -> None:
        self._filename = filename
        self._assets_dir = assets_dir
        self._build_hint = build_hint
        self._lock = threading.Lock()
        self._html: str | None = None
        self._signature: tuple[int, int] | None = None
        self._sha256: str | None = None
        self._mtime: str | None = None

    def _resource(self):
        return importlib.resources.files("cerebro_mcp").joinpath(
            f"static/{self._filename}"
        )

    def html(self) -> str:
        with self._lock:
            try:
                resource = self._resource()
                with importlib.resources.as_file(resource) as path:
                    stat = path.stat()
                    signature = (stat.st_mtime_ns, stat.st_size)
                    if self._html is not None and signature == self._signature:
                        return self._html
                    raw = path.read_bytes()
                    self._html = raw.decode("utf-8")
                    self._signature = signature
                    self._sha256 = hashlib.sha256(raw).hexdigest()
                    self._mtime = datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).isoformat().replace("+00:00", "Z")
            except (FileNotFoundError, ModuleNotFoundError, OSError):
                self._html = (
                    "<!doctype html><html><body><div id='root'>"
                    f"{self._filename} not built — run "
                    f"<code>{self._build_hint}</code></div></body></html>"
                )
                self._signature = None
                self._sha256 = hashlib.sha256(self._html.encode()).hexdigest()
                self._mtime = None
            return self._html

    def diagnostics(self) -> dict[str, Any]:
        self.html()
        assets: list[str] = []
        try:
            root = importlib.resources.files("cerebro_mcp").joinpath(
                f"static/{self._assets_dir}"
            )
            assets = sorted(entry.name for entry in root.iterdir() if entry.is_file())
        except (FileNotFoundError, ModuleNotFoundError, OSError, NotADirectoryError):
            pass
        return {
            "bundle_sha256": self._sha256,
            "bundle_mtime": self._mtime,
            "assets": assets,
        }


class SectionQuerySpec(Protocol):
    """Structural contract the shared loaders need from an app's QuerySpec.

    Apps keep their own frozen dataclasses (with extra app-specific fields
    such as coverage modes or source planes); only these attributes are read
    here.
    """

    key: str
    title: str
    sql: str
    parameters: dict[str, Any]
    cache_ttl_seconds: int
    exact_count: bool


def dataset_titles(specs: Iterable[SectionQuerySpec]) -> dict[str, str]:
    return {spec.key: spec.title for spec in specs}


def payload_from_record(
    record: ViewRecord,
    *,
    app_id: str,
    database: str,
    summary_cards: Callable[[ViewRecord], list[SummaryCard]],
    titles: dict[str, str] | None = None,
) -> MiniAppPayload:
    """Assemble the full INITIAL_LOAD payload for a sectioned app's view."""
    # Retained sections' datasets outlive the specs that created them, so the
    # persisted title map is the fallback for keys the caller didn't pass.
    titles = {
        **(record.view_state.get("dataset_titles") or {}),
        **(titles or {}),
    }
    scope_id = str(record.view_state.get("scope_id") or "")
    coverage = record.view_state.get("coverage") or {}
    descriptors = {
        key: build_dataset_descriptor(
            key=key,
            dataset=dataset,
            title=titles.get(key, key.replace("_", " ").title()),
            scope_id=scope_id,
            provenance={"source": database, "coverage": coverage.get(key, {})},
        )
        for key, dataset in record.datasets.items()
    }
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=record.view_id,
        app_id=app_id,
        title=record.title,
        status="ready",
        summary_cards=summary_cards(record),
        datasets=descriptors,
        view_state=record.view_state,
        provenance={"source": database, "coverage": coverage},
        warnings=list(record.view_state.get("warnings") or []),
    )


def touch_section_lru(
    view_id: str,
    state: dict[str, Any],
    keep_section: str,
    *,
    section_groups: Mapping[str, Mapping[str, Any]],
    max_retained: int,
    protected_keys: Sequence[str] = (),
) -> None:
    """Mark ``keep_section`` most-recent and evict retained sections above cap.

    Mutates ``state`` in place (section_lru / section_datasets /
    section_fingerprints / loaded_groups) and detaches evicted datasets.
    Keys in ``protected_keys`` survive every eviction.
    """
    lru = [s for s in (state.get("section_lru") or []) if s != keep_section]
    lru.append(keep_section)
    section_datasets = dict(state.get("section_datasets") or {})
    fingerprints = dict(state.get("section_fingerprints") or {})
    loaded = dict(state.get("loaded_groups") or {})
    while len(lru) > max_retained:
        victim = lru.pop(0)
        keys = [
            key for key in (section_datasets.pop(victim, []) or [])
            if key not in protected_keys
        ]
        if keys:
            remove_view_datasets(view_id, keys)
        fingerprints.pop(victim, None)
        for group in section_groups.get(victim, {}):
            loaded[f"{victim}.{group}"] = False
    state["section_lru"] = lru
    state["section_datasets"] = section_datasets
    state["section_fingerprints"] = fingerprints
    state["loaded_groups"] = loaded


def load_specs_safe(
    ch: ClickHouseManager,
    specs: Sequence[SectionQuerySpec],
    range_state: dict[str, Any],
    *,
    force_refresh: bool,
    database: str,
    row_cap: int,
    failure_cache: FailureCache,
    coverage_fn: Callable[
        [CachedDataset, Any, dict[str, Any]], tuple[dict[str, Any], list[str]]
    ],
    failure_coverage_fn: Callable[[Any, dict[str, Any], str, str], dict[str, Any]],
    worker_limit: int,
    thread_name_prefix: str,
    log_success: Callable[[Any, CachedDataset, dict[str, Any], float], None],
    log_failure: Callable[[Any, Exception], None],
    query_budget: QueryBudget | None = None,
) -> tuple[dict[str, CachedDataset], dict[str, Any], list[str]]:
    """Load a section's datasets in parallel with partial-failure isolation.

    One failed dataset never fails the section: its error is remembered in
    the app's negative ``failure_cache`` (so an immediate Retry replays it
    instead of re-running a query that just blew up), reported through
    ``failure_coverage_fn``'s coverage dict, and materialized as a zero-row
    stub dataset so the panel stays VISIBLE instead of silently vanishing.
    """
    datasets: dict[str, CachedDataset] = {}
    coverage: dict[str, Any] = {}
    warnings: list[str] = []

    def load_one(
        spec: SectionQuerySpec,
    ) -> tuple[CachedDataset, dict[str, Any], list[str], float]:
        started = time.monotonic()
        if "ORDER BY" not in spec.sql.upper():
            raise ValueError(f"{spec.key} must define a deterministic ORDER BY")
        if not force_refresh:
            cached_failure = failure_cache.get(spec.sql, spec.parameters)
            if cached_failure is not None:
                raise CachedFailure(
                    f"{cached_failure} (cached failure; retry in up to "
                    f"{failure_cache.ttl_seconds}s, use Refresh to force, or "
                    "narrow the window)"
                )
        dataset = load_exact_capped_dataset(
            ch,
            spec.sql,
            database=database,
            parameters=spec.parameters,
            force_refresh=force_refresh,
            cache_ttl_seconds=spec.cache_ttl_seconds,
            row_cap=row_cap,
            exact_source_rows=spec.exact_count,
            query_budget=query_budget,
        )
        cov, codes = coverage_fn(dataset, spec, range_state)
        return dataset, cov, codes, time.monotonic() - started

    max_workers = min(len(specs), worker_limit)
    results: dict[
        int, tuple[CachedDataset, dict[str, Any], list[str], float] | Exception
    ] = {}
    if max_workers <= 1:
        for index, spec in enumerate(specs):
            try:
                results[index] = load_one(spec)
            except Exception as exc:  # one dataset must not fail the section
                results[index] = exc
    else:
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=thread_name_prefix
        ) as pool:
            futures = {
                pool.submit(load_one, spec): index
                for index, spec in enumerate(specs)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:  # one dataset must not fail the section
                    results[index] = exc

    for index, spec in enumerate(specs):
        result = results[index]
        if not isinstance(result, Exception):
            dataset, cov, codes, elapsed = result
            datasets[spec.key] = dataset
            coverage[spec.key] = cov
            warnings.extend(codes)
            log_success(spec, dataset, range_state, elapsed)
        else:
            log_failure(spec, result)
            if not isinstance(result, CachedFailure):
                # Remember the failure so immediate retries replay it instead
                # of re-running a query that just blew up or timed out.
                failure_cache.put(spec.sql, spec.parameters, str(result))
            message = f"{spec.title} unavailable: {result}"
            warnings.extend(["query_failed", message])
            fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            coverage[spec.key] = failure_coverage_fn(
                spec, range_state, fetched_at, str(result)
            )
            # Stub dataset so the key survives into descriptors/attach: the
            # payload keeps the panel present (zero rows + provenance error)
            # instead of dropping it, which blanked whole sections in v2.
            datasets[spec.key] = CachedDataset(
                columns=[],
                column_types=[],
                rows=[],
                stats=DatasetStats(
                    row_count=0,
                    rows_returned=0,
                    mode="exact_capped",
                    source_rows=0,
                    row_cap=row_cap,
                    truncated=False,
                    fetched_at=fetched_at,
                    elapsed_seconds=0.0,
                    warnings=[message],
                ),
                sql=spec.sql,
                database=database,
                parameters=spec.parameters,
            )
    return datasets, coverage, list(dict.fromkeys(warnings))


# ---------------------------------------------------------------------------
# App-only tool registration & visibility filter
# ---------------------------------------------------------------------------


APP_ONLY_META = {"ui": {"visibility": ["app"]}}

# Names of tools that should never appear in the model-facing tool list.
_app_only_tool_names: set[str] = set()
_app_only_lock = threading.Lock()

# Advanced tools explicitly un-hidden at runtime via `load_tools([...])` so they
# pass the lean-core drop below. NOTE: the `list_tools` wrapper is
# PROCESS-GLOBAL (one monkey-patch per server object, no per-request state), so
# this un-hide set is process-global too — every session sees the same visible
# surface. Making it per-session would require request/session context in the
# wrapper, which FastMCP does not thread through `list_tools`.
_force_visible_tool_names: set[str] = set()
_force_visible_lock = threading.Lock()


def mark_app_only(name: str) -> None:
    with _app_only_lock:
        _app_only_tool_names.add(name)


def get_app_only_tool_names() -> set[str]:
    with _app_only_lock:
        return set(_app_only_tool_names)


def mark_force_visible(*names: str) -> None:
    """Un-hide advanced tools so they survive the lean-core drop (process-global)."""
    with _force_visible_lock:
        _force_visible_tool_names.update(n for n in names if n)


def get_force_visible_tool_names() -> set[str]:
    with _force_visible_lock:
        return set(_force_visible_tool_names)


def clear_force_visible_tool_names() -> None:
    """Reset the un-hide set (used by tests)."""
    with _force_visible_lock:
        _force_visible_tool_names.clear()


def _is_app_only_tool(tool) -> bool:
    """True if a tool is marked app-only via its ``meta.ui.visibility``."""
    meta = getattr(tool, "meta", None) or {}
    ui = meta.get("ui") if isinstance(meta, dict) else None
    visibility = (ui or {}).get("visibility") if isinstance(ui, dict) else None
    return isinstance(visibility, list) and "app" in visibility


def _lean_core_hides(tool) -> bool:
    """True if the lean-core filter should drop this tool.

    Only when ``LEAN_CORE_ENABLED`` is on, the tool classifies ``advanced``,
    and it has NOT been un-hidden via ``load_tools``. Core tools are never
    dropped. The flag is read at call-time (not import-time) so tests /
    hot-reloads see the current value.
    """
    # Imported here (not at module top) so the flag is read live and to avoid
    # any import cycle between config and the mini-app module.
    from cerebro_mcp.config import settings
    from cerebro_mcp.tools.tool_meta import is_core_tool

    if not getattr(settings, "LEAN_CORE_ENABLED", False):
        return False
    name = getattr(tool, "name", "") or ""
    if not name or name in get_force_visible_tool_names():
        return False
    description = getattr(tool, "description", "") or ""
    return not is_core_tool(name, description)


def install_app_only_filter(mcp) -> None:
    """Wrap ``mcp.list_tools`` so hidden tools never reach the model.

    Two drops share this single wrapper:

    1. **App-only** — anything whose ``meta.ui.visibility`` contains ``"app"``
       (mini-app hydration tools). Always applied. The tools stay callable by
       the frontend, which uses the ext-apps SDK ``callTool`` path that bypasses
       ``list_tools``.
    2. **Lean-core** — when ``settings.LEAN_CORE_ENABLED`` is on, tools
       classified ``tier="advanced"`` in ``tools/tool_meta.py`` (and not
       un-hidden via ``load_tools``) are also dropped, leaving the ~17 core
       tools. When the flag is OFF, behaviour is unchanged (all non-app tools
       visible). Advanced tools stay registered and callable either way — this
       only trims the advertised list.

    Idempotent: a marker attribute prevents double-wrapping. NOTE: the wrapper
    is process-global (no per-request/session state), so the lean-core surface
    is the same for every session.
    """
    if getattr(mcp, "_mini_app_filter_installed", False):
        return

    original_list_tools = mcp.list_tools

    async def list_tools_filtered():
        tools = await original_list_tools()
        filtered = []
        for tool in tools:
            if _is_app_only_tool(tool):
                continue
            if _lean_core_hides(tool):
                continue
            filtered.append(tool)
        return filtered

    mcp.list_tools = list_tools_filtered  # type: ignore[assignment]
    mcp._mini_app_filter_installed = True  # type: ignore[attr-defined]


async def _emit_tool_list_changed(mcp) -> bool:
    """Best-effort emit of ``notifications/tools/list_changed``.

    COMPATIBILITY SPIKE — UNPROVEN. This repo has no established list_changed
    emit path. FastMCP exposes no public helper; the notification lives on the
    low-level ``ServerSession`` (``send_tool_list_changed``), reachable only
    within a live request via ``mcp.get_context().session``. Outside a request
    (or if the client ignores the notification) this is a no-op. Returns True
    only if the send call was made without raising; that does NOT prove the
    client refetched. Never raises.
    """
    try:
        ctx = mcp.get_context()
        session = getattr(ctx, "session", None)
        send = getattr(session, "send_tool_list_changed", None)
        if session is None or send is None:
            logger.info("load_tools: no session available; list_changed not emitted")
            return False
        await send()
        return True
    except Exception as exc:  # pragma: no cover - depends on live session
        logger.info("load_tools: list_changed emit failed (best-effort): %s", exc)
        return False


def register_load_tools_tool(mcp) -> None:
    """Register ``load_tools`` — the advanced-tool un-hide COMPATIBILITY SPIKE.

    Un-hiding is deterministic; the client-refresh half is not. Register LAST
    (after the full tool surface exists) so name validation sees every tool.
    Idempotent via a marker attribute.
    """
    if getattr(mcp, "_load_tools_installed", False):
        return

    @mcp.tool()
    async def load_tools(names: list[str]) -> dict[str, Any]:
        """Un-hide advanced tools so they appear in the tool list (lean-core mode).

        When ``LEAN_CORE_ENABLED`` is on, only the ~17 core tools are advertised
        by default; advanced tools stay registered and CALLABLE but are hidden
        from ``list_tools``. This un-hides the named advanced tools so they also
        show up, then best-effort asks the client to refetch the tool list.

        CAVEATS (read before relying on this):
        * The un-hide is PROCESS-GLOBAL, not per-session — the visibility filter
          has no per-request state, so every session sees the same surface.
        * The client-refresh half (``notifications/tools/list_changed``) is an
          UNPROVEN spike: emission is best-effort and only works inside a live
          request; a client may ignore it. If a name doesn't appear afterward,
          the tool is still callable directly by name. Prefer the core set /
          ``find`` follow-ups, which need no refresh.

        Args:
            names: Tool names to un-hide (unknown names are reported, not fatal).

        Returns:
            ``{unhidden, unknown, already_core, list_changed_emitted, note}``.
        """
        registered = getattr(getattr(mcp, "_tool_manager", None), "_tools", {}) or {}
        from cerebro_mcp.tools.tool_meta import is_core_tool

        requested = [n for n in (names or []) if n]
        unknown = [n for n in requested if n not in registered]
        known = [n for n in requested if n in registered]
        already_core = [n for n in known if is_core_tool(n)]
        to_unhide = [n for n in known if n not in already_core]

        if to_unhide:
            mark_force_visible(*to_unhide)

        emitted = await _emit_tool_list_changed(mcp) if to_unhide else False

        return {
            "unhidden": to_unhide,
            "unknown": unknown,
            "already_core": already_core,
            "list_changed_emitted": emitted,
            "note": (
                "Un-hide is process-global. list_changed emission is best-effort "
                "and unproven; if a tool doesn't appear in the list, call it "
                "directly by name — it is still callable."
            ),
        }

    mcp._load_tools_installed = True  # type: ignore[attr-defined]
    return load_tools


# ---------------------------------------------------------------------------
# CallToolResult helpers
# ---------------------------------------------------------------------------


def payload_to_call_tool_result(
    payload: MiniAppPayload, summary_text: str
) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=summary_text)],
        structuredContent=payload.model_dump(exclude_none=True),
    )


def error_call_tool_result(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Error: {message}")],
        isError=True,
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_mini_app_infra(mcp, ch: ClickHouseManager) -> None:
    """Install the visibility filter and register the app-only hydration tools."""

    install_app_only_filter(mcp)

    @mcp.tool(meta=APP_ONLY_META)
    def get_mini_app_rows(
        view_id: str,
        dataset_key: str,
        page_token: str = "",
        page_size: int | None = None,
        dataset_revision: int | None = None,
    ) -> CallToolResult:
        """[App-only] Fetch the next page of rows for a mini-app dataset.

        Hidden from the model-facing tool list. Frontends call this through
        the ext-apps SDK to hydrate datasets attached to a live view.
        """
        try:
            page = get_view_dataset_page(
                view_id,
                dataset_key,
                page_token,
                page_size=page_size,
                dataset_revision=dataset_revision,
            )
        except (KeyError, ValueError) as exc:
            return error_call_tool_result(str(exc))
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        f"page rows={len(page['rows'])} "
                        f"next_token={page['next_page_token'] or 'end'}"
                    ),
                )
            ],
            structuredContent=page,
        )

    @mcp.tool(meta=APP_ONLY_META)
    def get_mini_app_state(view_id: str) -> CallToolResult:
        """[App-only] Return the current view state and dataset metadata."""
        record = get_view(view_id)
        if record is None:
            return error_call_tool_result(f"Unknown or expired view_id: {view_id}")
        return CallToolResult(
            content=[TextContent(type="text", text=f"view {view_id} state")],
            structuredContent={
                "view_id": record.view_id,
                "app_id": record.app_id,
                "title": record.title,
                "view_state": record.view_state,
                "datasets": {
                    key: {
                        "columns": ds.columns,
                        "stats": ds.stats.model_dump(exclude_none=True),
                        "total_rows": len(ds.rows),
                    }
                    for key, ds in record.datasets.items()
                },
            },
        )

    mark_app_only("get_mini_app_rows")
    mark_app_only("get_mini_app_state")

    # Also expose these plain callables for standalone web-app delivery.
    from cerebro_mcp.tools.visualization.web_apps import register_mini_app_tools

    register_mini_app_tools(
        {
            "get_mini_app_rows": get_mini_app_rows,
            "get_mini_app_state": get_mini_app_state,
        }
    )


__all__ = [
    "MiniAppDefinition",
    "MiniAppQueryError",
    "ViewRecord",
    "StructuredResult",
    "register_app",
    "get_app",
    "list_apps",
    "create_view",
    "get_view",
    "get_view_title",
    "patch_view_state",
    "attach_dataset",
    "replace_view_datasets",
    "run_structured_query",
    "load_bounded_dataset",
    "load_exact_capped_dataset",
    "get_view_dataset_page",
    "build_dataset_descriptor",
    "collect_dataset_warnings",
    "mark_app_only",
    "get_app_only_tool_names",
    "install_app_only_filter",
    "payload_to_call_tool_result",
    "error_call_tool_result",
    "register_mini_app_infra",
    "reset_views_for_tests",
    "SAMPLE_TARGET",
    "PREVIEW_ROW_CAP",
    "HARD_TOTAL_LIMIT",
]
