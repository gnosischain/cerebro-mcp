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

import logging
import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.clients.clickhouse import ClickHouseManager, ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import (
    CachedDataset,
    get_cache,
    make_cache_key,
)
from cerebro_mcp.models.mini_app import (
    DatasetDescriptor,
    DatasetMode,
    DatasetSchemaColumn,
    DatasetStats,
    MiniAppPayload,
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


@dataclass
class ViewRecord:
    view_id: str
    app_id: str
    title: str
    created_at: datetime
    expires_at: datetime
    view_state: dict[str, Any] = field(default_factory=dict)
    datasets: dict[str, CachedDataset] = field(default_factory=dict)


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
) -> StructuredResult:
    """Run ``sql`` and return raw rows / columns without markdown formatting."""
    executed = ch.run_query(
        sql,
        database,
        requested_max_rows=requested_max_rows,
        audience="internal",
        fetch_mode="auto",
        parameters=parameters,
    )
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
) -> CachedDataset:
    """Return a ``CachedDataset`` sized via the dataset-mode rules.

    Algorithm:
      1. Cache hit on any (sql, db, params, mode) → return immediately.
      2. Cheap ``count()`` over the wrapped subquery.
      3. ``count <= SAMPLE_TARGET`` → ``exact_bounded``.
      4. Otherwise try deterministic hash-bucket sampling, with one retry
         using a wider cutoff if the first attempt under-samples.
      5. If sampling raises or under-samples on both attempts, fall back to
         ``preview_only`` (≤200 rows, analytics disabled).
    """
    cache = get_cache()

    # 1. cache check across all modes
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
        raise MiniAppQueryError(str(exc)) from exc

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
            raise MiniAppQueryError(str(exc)) from exc
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
        raise MiniAppQueryError(str(exc)) from exc

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
    if token.startswith("offset:"):
        try:
            return int(token.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


def get_view_dataset_page(
    view_id: str, dataset_key: str, page_token: str = ""
) -> dict[str, Any]:
    """Return ``{columns, rows, next_page_token, total_rows}`` for one page."""
    record = get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    dataset = record.datasets.get(dataset_key)
    if dataset is None:
        raise KeyError(f"Unknown dataset_key: {dataset_key}")

    offset = _decode_page_token(page_token)
    end = offset + _VIEW_PAGE_SIZE
    page_rows = dataset.rows[offset:end]
    next_token = _encode_page_token(end) if end < len(dataset.rows) else ""

    return {
        "view_id": view_id,
        "dataset_key": dataset_key,
        "columns": dataset.columns,
        "column_types": dataset.column_types,
        "rows": page_rows,
        "next_page_token": next_token,
        "total_rows": len(dataset.rows),
        "stats": dataset.stats.model_dump(exclude_none=True),
    }


def build_dataset_descriptor(
    *,
    key: str,
    dataset: CachedDataset,
    title: str = "",
    preview_limit: int = _VIEW_PAGE_SIZE,
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
# App-only tool registration & visibility filter
# ---------------------------------------------------------------------------


APP_ONLY_META = {"ui": {"visibility": ["app"]}}

# Names of tools that should never appear in the model-facing tool list.
_app_only_tool_names: set[str] = set()
_app_only_lock = threading.Lock()


def mark_app_only(name: str) -> None:
    with _app_only_lock:
        _app_only_tool_names.add(name)


def get_app_only_tool_names() -> set[str]:
    with _app_only_lock:
        return set(_app_only_tool_names)


def install_app_only_filter(mcp) -> None:
    """Wrap ``mcp.list_tools`` so app-only tools never reach the model.

    The MCP protocol exposes every registered tool. We monkey-patch the
    server's ``list_tools`` to filter out anything whose ``meta.ui.visibility``
    contains ``"app"``. The tools are still callable by the frontend (which
    uses the ext-apps SDK ``callTool`` path that bypasses ``list_tools``).

    Idempotent: a marker attribute prevents double-wrapping.
    """
    if getattr(mcp, "_mini_app_filter_installed", False):
        return

    original_list_tools = mcp.list_tools

    async def list_tools_filtered():
        tools = await original_list_tools()
        filtered = []
        for tool in tools:
            meta = getattr(tool, "meta", None) or {}
            ui = meta.get("ui") if isinstance(meta, dict) else None
            visibility = (ui or {}).get("visibility") if isinstance(ui, dict) else None
            if isinstance(visibility, list) and "app" in visibility:
                continue
            filtered.append(tool)
        return filtered

    mcp.list_tools = list_tools_filtered  # type: ignore[assignment]
    mcp._mini_app_filter_installed = True  # type: ignore[attr-defined]


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
        view_id: str, dataset_key: str, page_token: str = ""
    ) -> CallToolResult:
        """[App-only] Fetch the next page of rows for a mini-app dataset.

        Hidden from the model-facing tool list. Frontends call this through
        the ext-apps SDK to hydrate datasets attached to a live view.
        """
        try:
            page = get_view_dataset_page(view_id, dataset_key, page_token)
        except KeyError as exc:
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
