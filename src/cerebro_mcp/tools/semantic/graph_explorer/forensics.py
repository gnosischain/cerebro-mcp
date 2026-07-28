"""Forensic provenance and source-contract helpers for Graph Explorer.

The graph surfaces answer materially different questions, but they must all
describe *how* they answered them in the same shape.  This module is kept free
of mode-specific policy: callers supply the sources, coverage and residuals;
the helper only normalises the contract and preserves unknown values as
``None`` (never the misleading numeric zero).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
import time
import uuid
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from cerebro_mcp.chains import GNOSIS_CHAIN_ID
from cerebro_mcp.clients.clickhouse import (
    CONTRACT_PROBE_QUERY_BUDGET,
    ClickHouseManager,
)
from cerebro_mcp.tools.visualization import mini_apps


_SOURCE_CONTRACT_SUCCESS_TTL_SECONDS = 600.0
_SOURCE_CONTRACT_FAILURE_TTL_SECONDS = 30.0
_source_contract_cache: dict[
    tuple[int, str, tuple[str, ...], bool, str], tuple[float, dict[str, Any]]
] = {}
_source_contract_lock = threading.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_scope_id(kind: str, request_id: int = 0) -> str:
    """Globally unique scope id with a readable mode/request prefix."""
    return f"{kind}:{max(0, int(request_id or 0))}:{uuid.uuid4().hex}"


def source_record(
    *,
    kind: str,
    name: str,
    role: str,
    status: str = "ok",
    horizon: Any = None,
    horizon_basis: str | None = None,
    fetched_at: str | None = None,
    error: str | None = None,
    freshness_note: str | None = None,
    contract_status: str | None = None,
    model_version: str | None = None,
    manifest_version: str | None = None,
) -> dict[str, Any]:
    out = {
        "kind": kind,
        "name": name,
        "role": role,
        "status": status,
        "contract_status": contract_status or status,
        "horizon": horizon,
        "fetched_at": fetched_at or utc_now_iso(),
    }
    if horizon_basis:
        out["horizon_basis"] = str(horizon_basis)
    if freshness_note:
        out["freshness_note"] = str(freshness_note)
    elif horizon_basis == "system.tables.metadata_modification_time":
        out["freshness_note"] = (
            "table metadata modification time; row-level event freshness is unavailable"
        )
    if error:
        out["error"] = str(error)
    if model_version:
        out["model_version"] = str(model_version)
    if manifest_version:
        out["manifest_version"] = str(manifest_version)
    return out


def canonical_row_hash(rows: Iterable[Any]) -> str:
    """Stable SHA-256 over JSON-normalized evidence rows."""
    payload = json.dumps(
        list(rows),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _runtime_identity() -> tuple[str, str]:
    """Read the same build identities exposed by the app health route."""
    try:
        # Lazy import avoids a package-initialization cycle: __init__ imports
        # the mode modules, which in turn import this contract helper.
        from . import get_graph_explorer_diagnostics

        diagnostics = get_graph_explorer_diagnostics()
        return (
            str(diagnostics.get("app_commit") or "unknown"),
            str(diagnostics.get("dbt_manifest_sha256") or "unknown"),
        )
    except (ImportError, RuntimeError):
        return "unknown", "unknown"


def forensic_scope(
    *,
    scope_id: str,
    status: str,
    request_id: int,
    t0: Any,
    t1: Any,
    window_source: str,
    data_horizon: Any,
    result_observed_through: Any = None,
    sources: Iterable[dict[str, Any]],
    rows_returned: int,
    rows_total: int | None,
    nodes_returned: int | None = None,
    nodes_total: int | None = None,
    edges_returned: int | None = None,
    edges_total: int | None = None,
    known_usd: float | None = None,
    total_usd: float | None = None,
    unknown_usd_rows: int = 0,
    truncated: bool = False,
    truncation_rule: str | None = None,
    coverage_note: str | None = None,
    residuals: Iterable[str] = (),
    warnings: Iterable[str] = (),
    verification_status: str = "unverified",
    verification_method: str | None = None,
    chain_id: int = GNOSIS_CHAIN_ID,
    query_kind: str | None = None,
    evidence_class: str = "derived_dataset",
    subjects: Iterable[str] = (),
    as_of: Any = None,
    token_universe: dict[str, Any] | None = None,
    app_commit: str | None = None,
    dbt_manifest_sha256: str | None = None,
    retrieved_at: str | None = None,
    result_row_hash: str | None = None,
) -> dict[str, Any]:
    """Build the shared, JSON-safe scope contract.

    A total is exact only when the caller can independently establish it.
    ``None`` therefore means unknown, while ``0`` is a meaningful verified
    zero.  Truncation can be explicit (a policy cap fired) or inferred from a
    known row total.
    """
    if status not in {"ready", "partial", "failed"}:
        raise ValueError(f"invalid forensic scope status: {status!r}")
    inferred_truncation = rows_total is not None and rows_returned < rows_total
    runtime_commit, runtime_manifest = _runtime_identity()
    source_rows = list(sources)
    normalized_query_kind = query_kind or str(scope_id).split(":", 1)[0]
    scope = {
        "schema_version": 2,
        "scope_id": scope_id,
        "request_id": max(0, int(request_id or 0)),
        "chain_id": int(chain_id),
        "query_kind": normalized_query_kind,
        "evidence_class": evidence_class,
        "predicate": {
            "subjects": [str(subject) for subject in subjects if str(subject)],
            "t0": t0,
            "t1": t1,
            "as_of": as_of,
        },
        "status": status,
        "window": {"t0": t0, "t1": t1, "source": window_source},
        "data_horizon": data_horizon,
        # This is deliberately distinct from ``data_horizon``.  A scoped
        # result can have no observations near the relation's watermark; the
        # latest returned event is not evidence that the source itself is
        # stale (and must never overwrite a source record's watermark).
        "result_observed_through": result_observed_through,
        "sources": source_rows,
        "coverage": {
            "rows": {"shown": int(rows_returned), "total": rows_total},
            "nodes": {"shown": nodes_returned, "total": nodes_total},
            "edges": {"shown": edges_returned, "total": edges_total},
            "usd": {
                "known": known_usd,
                "total": total_usd,
                "unknown_rows": max(0, int(unknown_usd_rows or 0)),
            },
        },
        "truncation": {
            "truncated": bool(truncated or inferred_truncation),
            "rule": truncation_rule,
        },
        "coverage_note": coverage_note,
        "residuals": list(residuals),
        "warnings": list(warnings),
        "verification": {
            "status": verification_status,
            "method": verification_method,
        },
        "app_commit": app_commit or runtime_commit,
        "dbt_manifest_sha256": dbt_manifest_sha256 or runtime_manifest,
        "retrieved_at": retrieved_at or utc_now_iso(),
        # A scope may cover several datasets. Callers can provide their
        # combined hash here; each descriptor independently publishes its own
        # row hash in state.build_payload.
        "result_row_hash": result_row_hash,
    }
    if token_universe is not None:
        scope["token_universe"] = dict(token_universe)
    return scope


_SQL_IDENTIFIER = re.compile(r"`([^`]+)`|([A-Za-z_][A-Za-z0-9_]*)")
_SQL_KEYWORDS = {
    "and",
    "as",
    "between",
    "case",
    "else",
    "end",
    "false",
    "if",
    "in",
    "is",
    "like",
    "not",
    "null",
    "or",
    "then",
    "true",
    "when",
}


def physical_columns_from_expression(expression: str | None) -> list[str]:
    """Return physical column dependencies from a profile SQL expression.

    Graph endpoints may be authored expressions (for example a validator
    withdrawal address derived with ``concat``/``substring``).  Source
    validation queries ``system.columns`` and therefore needs the underlying
    identifiers, not the complete expression.  The graph metadata gate
    already rejects statement/comment tokens; this helper is intentionally a
    small dependency extractor rather than a general SQL parser.
    """
    authored = str(expression or "").strip()
    if not authored:
        return []
    # String literals can contain address prefixes and arbitrary words that
    # are not columns.  Preserve character offsets so the function-call check
    # below can still inspect what follows each identifier.
    scrubbed = re.sub(r"'(?:''|[^'])*'", lambda m: " " * len(m.group(0)), authored)
    columns: list[str] = []
    for match in _SQL_IDENTIFIER.finditer(scrubbed):
        quoted, bare = match.groups()
        identifier = quoted or bare or ""
        if not identifier:
            continue
        tail = scrubbed[match.end() :].lstrip()
        # An unquoted identifier followed by '(' names a SQL function.  A
        # quoted column may legally be followed by parentheses only in invalid
        # profile metadata, so retain quoted identifiers unconditionally.
        if quoted is None and tail.startswith("("):
            continue
        if quoted is None and identifier.lower() in _SQL_KEYWORDS:
            continue
        if identifier not in columns:
            columns.append(identifier)
    return columns


def validate_source_contract(
    ch: ClickHouseManager,
    relation: str,
    required_columns: Iterable[str],
    *,
    success_ttl_seconds: float = _SOURCE_CONTRACT_SUCCESS_TTL_SECONDS,
    failure_ttl_seconds: float = _SOURCE_CONTRACT_FAILURE_TTL_SECONDS,
    probe_horizon: bool = False,
    horizon_column: str | None = None,
) -> dict[str, Any]:
    """Validate existence and required columns with a short-lived cache.

    The actual data query remains authoritative: a cached success never masks
    a later query exception (callers still catch and publish that failure).
    This probe only turns the common "model retired/table dropped" case into a
    precise failed scope before an empty graph can be mistaken for no activity.
    """
    raw_name = str(relation or "").strip()
    if "." in raw_name:
        raw_database, raw_table = raw_name.split(".", 1)
        database = raw_database.strip().strip("`")
        table = raw_table.strip().strip("`")
    else:
        database = "dbt"
        table = raw_name.strip("`")
    name = f"{database}.{table}"
    # Graph profile authors quote reserved ClickHouse columns such as `from`
    # and `to`. system.columns stores the underlying names without quotes;
    # comparing the authored spelling verbatim falsely reports a live relation
    # as missing. Contract identity is the normalized column identifier.
    required = tuple(
        sorted(
            {
                str(column).strip().strip("`")
                for column in required_columns
                if str(column or "").strip().strip("`")
            }
        )
    )
    normalized_horizon_column = str(horizon_column or "").strip().strip("`")
    cache_key = (
        id(ch),
        name,
        required,
        bool(probe_horizon),
        normalized_horizon_column,
    )
    now = time.monotonic()
    with _source_contract_lock:
        cached = _source_contract_cache.get(cache_key)
        if cached:
            cached_ttl = (
                success_ttl_seconds
                if bool(cached[1].get("ok"))
                else failure_ttl_seconds
            )
            if now - cached[0] < max(0.0, cached_ttl):
                return dict(cached[1])

    sql = """
        SELECT name, type
        FROM system.columns
        WHERE database = {database:String}
          AND table = {table:String}
          AND name IN {required:Array(String)}
        ORDER BY name
    """
    try:
        parameters = {
            "database": database,
            "table": table,
            "required": list(required),
        }
        # ``execute_raw`` is the manager's deliberately narrow metadata path
        # (DESCRIBE/SHOW/system tables). Running this probe through the normal
        # data-query pipeline with ``database='system'`` defeats the database
        # allowlist and makes every real contract fail before its data query.
        # Keep a run_query fallback for the lightweight test doubles.
        execute_raw = getattr(ch, "execute_raw", None)
        if callable(execute_raw):
            raw = _execute_raw_contract_probe(
                execute_raw, sql, database=database, parameters=parameters
            )
            rows = list(raw.get("rows") or [])
        else:
            result = mini_apps.run_structured_query(
                ch,
                sql,
                database=database,
                parameters=parameters,
                requested_max_rows=max(1, len(required)),
            )
            rows = result.rows
        column_types = {
            str(row[0]): str(row[1] or "")
            for row in rows
            if row and row[0]
        }
        present = set(column_types)
        missing = sorted(set(required) - present)
        # A present column can still be unusable. Nothing/Dynamic/Object/JSON
        # cannot safely satisfy the scalar address/date/amount contracts used
        # by Graph Explorer without an explicit cast policy at the call site.
        # Nullable/LowCardinality wrappers around ordinary scalar types remain
        # compatible.
        incompatible = sorted(
            name
            for name, column_type in column_types.items()
            if name in required
            and (
                not column_type
                or "Nothing" in column_type
                or column_type.startswith(("Dynamic", "Object", "JSON"))
            )
        )
        ok = not missing and not incompatible
        horizon: Any = None
        horizon_basis: str | None = None
        horizon_error: str | None = None
        freshness_checked_at = utc_now_iso()
        if ok and probe_horizon:
            try:
                safe_identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
                if not safe_identifier.fullmatch(database) or not safe_identifier.fullmatch(table):
                    raise ValueError(f"unsafe relation identifier for horizon probe: {name}")
                if normalized_horizon_column:
                    if not safe_identifier.fullmatch(normalized_horizon_column):
                        raise ValueError(
                            "unsafe temporal column for horizon probe: "
                            f"{normalized_horizon_column}"
                        )
                    horizon_sql = (
                        f"SELECT toString(max(`{normalized_horizon_column}`)) "
                        f"AS source_horizon FROM `{database}`.`{table}`"
                    )
                    horizon_parameters = None
                    horizon_basis = f"max({normalized_horizon_column})"
                else:
                    # Static/current-state relations have no event-time column.
                    # ClickHouse table metadata is the independently obtainable
                    # source freshness signal; it is not confused with the time
                    # at which Graph Explorer happened to query the relation.
                    horizon_sql = """
                        SELECT max(metadata_modification_time) AS source_horizon
                        FROM system.tables
                        WHERE database = {database:String}
                          AND name = {table:String}
                    """
                    horizon_parameters = {"database": database, "table": table}
                    horizon_basis = "system.tables.metadata_modification_time"

                if callable(execute_raw):
                    horizon_raw = _execute_raw_contract_probe(
                        execute_raw,
                        horizon_sql,
                        database=database,
                        parameters=horizon_parameters,
                    )
                    horizon_rows = list(horizon_raw.get("rows") or [])
                else:
                    horizon_result = mini_apps.run_structured_query(
                        ch,
                        horizon_sql,
                        database=database,
                        parameters=horizon_parameters,
                        requested_max_rows=1,
                    )
                    horizon_rows = horizon_result.rows
                horizon = (
                    horizon_rows[0][0]
                    if horizon_rows and horizon_rows[0]
                    else None
                )
                if horizon is None:
                    if normalized_horizon_column:
                        horizon = (
                            "verified empty/null temporal column at "
                            f"{freshness_checked_at}"
                        )
                        horizon_basis = f"{horizon_basis}; verified empty/null"
                    else:
                        raise ValueError(
                            "static relation metadata did not provide a modification horizon"
                        )
                elif hasattr(horizon, "isoformat"):
                    horizon = horizon.isoformat()
                else:
                    horizon = str(horizon)
            except Exception as exc:
                horizon_error = str(exc)
                ok = False

        checked = {
            "ok": ok,
            "relation": name,
            "missing_columns": missing,
            "incompatible_columns": incompatible,
            "column_types": column_types,
            "horizon": horizon,
            "horizon_basis": horizon_basis,
            "horizon_error": horizon_error,
            "freshness_checked_at": freshness_checked_at,
            "error": (
                None
                if ok
                else (
                    "relation missing, incompatible, or freshness unavailable; "
                    + (
                        f"required column(s) not found: {', '.join(missing)}"
                        if missing
                        else ""
                    )
                    + ("; " if missing and incompatible else "")
                    + (
                        "unsupported required column type(s): "
                        + ", ".join(
                            f"{column}={column_types[column]}"
                            for column in incompatible
                        )
                        if incompatible
                        else ""
                    )
                    + ("; " if (missing or incompatible) and horizon_error else "")
                    + (
                        f"horizon probe failed: {horizon_error}"
                        if horizon_error
                        else ""
                    )
                )
            ),
            "checked_at": utc_now_iso(),
        }
    except Exception as exc:
        checked = {
            "ok": False,
            "relation": name,
            "missing_columns": list(required),
            "incompatible_columns": [],
            "column_types": {},
            "horizon": None,
            "horizon_basis": None,
            "horizon_error": str(exc) if probe_horizon else None,
            "freshness_checked_at": utc_now_iso(),
            "error": str(exc),
            "checked_at": utc_now_iso(),
        }

    with _source_contract_lock:
        _source_contract_cache[cache_key] = (time.monotonic(), dict(checked))
    return checked


def _execute_raw_contract_probe(
    execute_raw,
    sql: str,
    *,
    database: str,
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    """Call production managers with a strict budget without breaking fakes.

    Several small unit-test/source adapters intentionally implement only the
    historical ``execute_raw(sql, database, parameters)`` protocol. Inspecting
    the callable avoids a TypeError-and-retry pattern that could execute a
    real metadata query twice.
    """
    try:
        signature = inspect.signature(execute_raw)
        supports_budget = "query_budget" in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    except (TypeError, ValueError):
        supports_budget = False
    kwargs: dict[str, Any] = {
        "database": database,
        "parameters": parameters,
    }
    if supports_budget:
        kwargs["query_budget"] = CONTRACT_PROBE_QUERY_BUDGET
    return execute_raw(sql, **kwargs)


def reset_source_contract_cache_for_tests() -> None:
    with _source_contract_lock:
        _source_contract_cache.clear()
