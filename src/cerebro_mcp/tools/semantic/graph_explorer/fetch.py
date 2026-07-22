"""Graph Explorer data access: role resolution, edge fetching, evidence.

One row-parsing path for every profile query (`_rows_to_graph`) — seeded and
sample fetches share it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from cerebro_mcp.clients.clickhouse import (
    INTERACTIVE_QUERY_BUDGET,
    ClickHouseManager,
)
from cerebro_mcp.semantic.graph_profiles import (
    GraphProfile,
    build_evidence_sql,
    build_neighbors_sql,
    build_sample_sql,
    build_timeline_sql,
    profile_by_id,
)
from cerebro_mcp.tools.visualization import mini_apps

from .state import short_id

logger = logging.getLogger(__name__)


ADDRESS_ROLES_RELATION = "int_execution_address_roles_current"
ADDRESS_ROLE_COLUMNS = (
    "is_safe",
    "is_gpay_wallet",
    "is_ga_user",
    "controls_gpay_wallet",
    "is_circles_avatar",
    "circles_avatar_type",
    "is_circles_wrapper",
    "is_safe_owner",
    "is_lp_provider",
    "pool_protocol",
    "is_pool",
    "is_lending_user",
    "is_validator_depositor",
    "has_dune_label",
    "dune_project",
)

# Role rows describe the current classification relation and can be reused for
# a short interactive session.  Verified absence gets a deliberately shorter
# TTL: a newly classified address should become discoverable promptly.  The
# two outcomes are stored separately so an empty result can never be confused
# with a lookup exception.
_ADDRESS_ROLE_CACHE_TTL_SECONDS = 600.0
_ADDRESS_ROLE_ABSENCE_CACHE_TTL_SECONDS = 60.0
_ADDRESS_ROLE_CACHE_MAX_ENTRIES = 2_048
_ADDRESS_ROLE_ABSENCE_CACHE_MAX_ENTRIES = 2_048

_AddressRoleCacheKey = tuple[object, str, str, str]
_CachedRoleItems = tuple[tuple[str, Any], ...]

_address_role_cache: OrderedDict[
    _AddressRoleCacheKey, tuple[float, _CachedRoleItems]
] = OrderedDict()
_address_role_absence_cache: OrderedDict[
    _AddressRoleCacheKey, float
] = OrderedDict()
_address_role_cache_lock = threading.RLock()


def _address_role_cache_key(
    ch: ClickHouseManager, address: str
) -> _AddressRoleCacheKey:
    """Scope cached evidence to the concrete source manager and relation.

    Keeping the manager object in the bounded cache avoids cross-source reuse
    (including tests or multi-source deployments) and prevents an ``id()``
    reuse collision while an entry is live.
    """

    return (ch, "dbt", ADDRESS_ROLES_RELATION, address.lower())


def _trim_address_role_cache_locked() -> None:
    while len(_address_role_cache) > _ADDRESS_ROLE_CACHE_MAX_ENTRIES:
        _address_role_cache.popitem(last=False)
    while (
        len(_address_role_absence_cache)
        > _ADDRESS_ROLE_ABSENCE_CACHE_MAX_ENTRIES
    ):
        _address_role_absence_cache.popitem(last=False)


def reset_address_role_cache_for_tests() -> None:
    """Clear positive and verified-absence role caches."""

    with _address_role_cache_lock:
        _address_role_cache.clear()
        _address_role_absence_cache.clear()


@dataclass(frozen=True)
class EvidenceQueryStatus:
    """Outcome of the source query behind one focus evidence dataset.

    Evidence rows deliberately stay a plain list for the existing call sites,
    while focus loading consumes this companion status to distinguish an exact
    empty answer from an exception that happened to produce no display rows.
    ``complete`` is false when a result exactly meets the query cap because a
    larger backing set may exist.
    """

    succeeded: bool
    source_rows_returned: int = 0
    complete: bool = False
    error: str | None = None


_ROLES_SQL = """
    SELECT
        is_safe, is_gpay_wallet, is_ga_user, controls_gpay_wallet,
        is_circles_avatar, circles_avatar_type, is_circles_wrapper,
        is_safe_owner, is_lp_provider, pool_protocol, is_pool,
        is_lending_user, is_validator_depositor,
        has_dune_label, dune_project
    FROM int_execution_address_roles_current
    WHERE address = {addr:String}
    LIMIT 1
"""


def resolve_address_roles_with_status(
    ch: ClickHouseManager, address: str
) -> tuple[dict[str, Any], EvidenceQueryStatus]:
    """Resolve roles and retain whether the query genuinely succeeded."""
    if not address:
        return {}, EvidenceQueryStatus(
            succeeded=False,
            error="address role evidence requires a selected address",
        )

    normalized_address = address.lower()
    cache_key = _address_role_cache_key(ch, normalized_address)
    now = time.monotonic()
    with _address_role_cache_lock:
        cached_roles = _address_role_cache.get(cache_key)
        if cached_roles is not None:
            cached_at, role_items = cached_roles
            if now - cached_at < _ADDRESS_ROLE_CACHE_TTL_SECONDS:
                _address_role_cache.move_to_end(cache_key)
                return dict(role_items), EvidenceQueryStatus(
                    succeeded=True,
                    source_rows_returned=1,
                    complete=True,
                )
            _address_role_cache.pop(cache_key, None)

        cached_absence_at = _address_role_absence_cache.get(cache_key)
        if cached_absence_at is not None:
            if (
                now - cached_absence_at
                < _ADDRESS_ROLE_ABSENCE_CACHE_TTL_SECONDS
            ):
                _address_role_absence_cache.move_to_end(cache_key)
                return {}, EvidenceQueryStatus(
                    succeeded=True,
                    source_rows_returned=0,
                    complete=True,
                )
            _address_role_absence_cache.pop(cache_key, None)

    try:
        result = mini_apps.run_structured_query(
            ch,
            _ROLES_SQL,
            database="dbt",
            parameters={"addr": normalized_address},
            requested_max_rows=1,
            query_budget=INTERACTIVE_QUERY_BUDGET,
        )
    except Exception as exc:
        logger.info("graph_explorer: roles lookup failed for %s: %s", address, exc)
        return {}, EvidenceQueryStatus(succeeded=False, error=str(exc))
    if not result.rows:
        with _address_role_cache_lock:
            _address_role_cache.pop(cache_key, None)
            _address_role_absence_cache[cache_key] = time.monotonic()
            _address_role_absence_cache.move_to_end(cache_key)
            _trim_address_role_cache_locked()
        return {}, EvidenceQueryStatus(
            succeeded=True,
            source_rows_returned=0,
            complete=True,
        )
    row = result.rows[0]
    roles = {col: value for col, value in zip(result.columns, row)}
    with _address_role_cache_lock:
        _address_role_absence_cache.pop(cache_key, None)
        _address_role_cache[cache_key] = (
            time.monotonic(),
            tuple(roles.items()),
        )
        _address_role_cache.move_to_end(cache_key)
        _trim_address_role_cache_locked()
    return (
        roles,
        EvidenceQueryStatus(
            succeeded=True,
            source_rows_returned=1,
            complete=True,
        ),
    )


def resolve_address_roles(ch: ClickHouseManager, address: str) -> dict[str, Any]:
    roles, _status = resolve_address_roles_with_status(ch, address)
    return roles


def node_evidence_rows(
    node_id: str,
    roles: dict[str, Any],
    *,
    request_id: int = 0,
    subject_kind: str = "node",
) -> list[list[Any]]:
    """Flatten resolved role flags into (node_id, column, value) evidence rows.

    Boolean role flags are emitted only when truthy; string-valued attributes
    (controls_gpay_wallet, pool_protocol, dune_project, circles_avatar_type)
    are emitted whenever non-empty so the details panel can surface the
    backing identity context the badges alone don't show.
    """
    if not node_id or not roles:
        return []
    rows: list[list[Any]] = []
    for col, value in roles.items():
        if value in (None, "", 0, "0"):
            continue
        rows.append([node_id, col, str(value), subject_kind, int(request_id or 0)])
    return rows


def flow_evidence_rows_with_status(
    ch: ClickHouseManager,
    edge_id: str,
    view_state: dict[str, Any],
    limit: int = 25,
    request_id: int = 0,
    subject_kind: str = "edge",
) -> tuple[list[list[Any]], EvidenceQueryStatus]:
    """Transaction-level (or per-day for bridge) backing rows for a flow
    edge. Needs the view's flows t0/t1 so evidence matches the traced range."""
    from cerebro_mcp.semantic.flow_queries import (
        build_flow_evidence_sql,
        parse_flow_edge_id,
    )

    parsed = parse_flow_edge_id(edge_id)
    if parsed is None:
        return [], EvidenceQueryStatus(
            succeeded=False,
            error=f"invalid flow evidence edge id: {edge_id}",
        )
    edge_class, src, tgt, token = parsed
    flows = (view_state or {}).get("flows") or {}
    t0, t1 = str(flows.get("t0") or ""), str(flows.get("t1") or "")
    if not (t0 and t1):
        return [], EvidenceQueryStatus(
            succeeded=False,
            error="flow evidence requires an applied t0/t1 window",
        )
    try:
        sql, params = build_flow_evidence_sql(
            edge_class=edge_class,
            source_id=src,
            target_id=tgt,
            token_address=token,
            t0=t0,
            t1_exclusive=t1,
            limit=limit,
        )
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database="dbt",
            parameters=params,
            requested_max_rows=limit,
            query_budget=INTERACTIVE_QUERY_BUDGET,
        )
    except Exception as exc:
        logger.info("graph_explorer: flow evidence failed for %s: %s", edge_id, exc)
        return [], EvidenceQueryStatus(succeeded=False, error=str(exc))
    rows: list[list[Any]] = []
    for row in result.rows:
        for col, value in zip(result.columns, row):
            if value in (None, ""):
                continue
            rows.append(
                [edge_id, col, str(value), subject_kind, int(request_id or 0)]
            )
    source_row_count = len(result.rows)
    return rows, EvidenceQueryStatus(
        succeeded=True,
        source_rows_returned=source_row_count,
        complete=source_row_count < limit,
    )


def flow_evidence_rows(
    ch: ClickHouseManager,
    edge_id: str,
    view_state: dict[str, Any],
    limit: int = 25,
    request_id: int = 0,
    subject_kind: str = "edge",
) -> list[list[Any]]:
    rows, _status = flow_evidence_rows_with_status(
        ch,
        edge_id,
        view_state,
        limit,
        request_id,
        subject_kind,
    )
    return rows


def edge_evidence_rows_with_status(
    ch: ClickHouseManager,
    edge_id: str,
    limit: int = 25,
    view_state: dict[str, Any] | None = None,
    request_id: int = 0,
    subject_kind: str = "edge",
) -> tuple[list[list[Any]], EvidenceQueryStatus]:
    """Resolve the raw backing rows for a selected edge via the profile's
    evidence model. Edge ids are `{profile}:{src}->{tgt}`; flow-mode ids
    (`flow:` / `bridge:`) route to the tx-level flow evidence path."""
    if not edge_id or ":" not in edge_id or "->" not in edge_id:
        return [], EvidenceQueryStatus(
            succeeded=False,
            error=f"invalid graph evidence edge id: {edge_id}",
        )
    if edge_id.startswith(("flow:", "bridge:")):
        return flow_evidence_rows_with_status(
            ch,
            edge_id,
            view_state or {},
            limit,
            request_id=request_id,
            subject_kind=subject_kind,
        )
    profile_id, _, endpoints = edge_id.partition(":")
    src, _, tgt = endpoints.partition("->")
    profile = profile_by_id(profile_id)
    if profile is None or not src or not tgt:
        return [], EvidenceQueryStatus(
            succeeded=False,
            error=f"unknown graph profile or endpoints for edge: {edge_id}",
        )
    # Bind the drill-down to the SAME window the edge's weight was aggregated
    # over, so the evidence describes the edge it hangs off rather than the
    # model's entire history.
    investigate = (view_state or {}).get("investigate") or {}
    window_days = investigate.get("window_days")
    try:
        sql, params = build_evidence_sql(
            profile,
            source_id=src,
            target_id=tgt,
            limit=limit,
            window_days=int(window_days) if window_days else None,
        )
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database="dbt",
            parameters=params,
            requested_max_rows=limit,
            query_budget=INTERACTIVE_QUERY_BUDGET,
        )
    except Exception as exc:
        logger.info("graph_explorer: edge evidence failed for %s: %s", edge_id, exc)
        return [], EvidenceQueryStatus(succeeded=False, error=str(exc))
    rows: list[list[Any]] = []
    for row in result.rows:
        for col, value in zip(result.columns, row):
            if value in (None, ""):
                continue
            rows.append(
                [edge_id, col, str(value), subject_kind, int(request_id or 0)]
            )
    source_row_count = len(result.rows)
    return rows, EvidenceQueryStatus(
        succeeded=True,
        source_rows_returned=source_row_count,
        complete=source_row_count < limit,
    )


def edge_evidence_rows(
    ch: ClickHouseManager,
    edge_id: str,
    limit: int = 25,
    view_state: dict[str, Any] | None = None,
    request_id: int = 0,
    subject_kind: str = "edge",
) -> list[list[Any]]:
    rows, _status = edge_evidence_rows_with_status(
        ch,
        edge_id,
        limit,
        view_state,
        request_id,
        subject_kind,
    )
    return rows


def pick_direction(profile: GraphProfile, seed_kind: str) -> str:
    """Choose WHERE-clause direction based on which endpoint the seed matches.

    When the seed kind matches only one endpoint (asymmetric profile), we skip
    the mismatched half — this avoids redundant SQL work and, more importantly,
    prevents comparing a String seed against a non-String column of the other
    endpoint (validator_index, etc.).
    """
    if not seed_kind:
        return "both"
    src_match = profile.source_kind == seed_kind
    tgt_match = profile.target_kind == seed_kind
    if src_match and not tgt_match:
        return "out"
    if tgt_match and not src_match:
        return "in"
    return "both"


def _rows_to_graph(
    result_rows: list[list[Any]], profile: GraphProfile
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Shared (src, tgt, weight, count) row parser -> node/edge dicts."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for row in result_rows:
        if len(row) < 2:
            continue
        src = "" if row[0] is None else str(row[0])
        tgt = "" if row[1] is None else str(row[1])
        weight: float | None = None
        if len(row) >= 3 and row[2] is not None:
            try:
                weight = float(row[2])
            except (TypeError, ValueError):
                weight = None
        edge_count = 0
        if len(row) >= 4 and row[3] is not None:
            try:
                edge_count = int(row[3])
            except (TypeError, ValueError):
                edge_count = 0
        for node_id, kind in (
            (src, profile.source_kind),
            (tgt, profile.target_kind),
        ):
            if not node_id:
                continue
            if node_id not in nodes:
                nodes[node_id] = {
                    "id": node_id,
                    "kind": kind,
                    "label": short_id(node_id),
                    "profiles": [profile.profile],
                }
            elif profile.profile not in nodes[node_id]["profiles"]:
                nodes[node_id]["profiles"].append(profile.profile)
        edges.append(
            {
                "id": f"{profile.profile}:{src}->{tgt}",
                "source": src,
                "target": tgt,
                "profile": profile.profile,
                "weight": weight,
                "edge_count": edge_count,
                "directed": profile.directed,
            }
        )
    return list(nodes.values()), edges


def fetch_profile_edges(
    ch: ClickHouseManager,
    profile: GraphProfile,
    *,
    seed_ids: list[str] | None = None,
    direction: str = "both",
    window_days: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Fetch one profile's edges.

    ``seed_ids=None`` = SAMPLE mode (top-weight edges, no seed filter, via
    ``build_sample_sql``); a seed list routes through ``build_neighbors_sql``.
    A query failure degrades to a warning, never an exception.
    """
    if seed_ids is None:
        sql, params = build_sample_sql(profile, window_days=window_days, limit=limit)
        fail_label = "sample query"
    else:
        sql, params = build_neighbors_sql(
            profile,
            seed_ids=seed_ids,
            direction=direction,
            window_days=window_days,
            limit=limit,
        )
        fail_label = "query"
    warnings: list[str] = []
    try:
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database="dbt",
            parameters=params,
            requested_max_rows=limit,
            query_budget=INTERACTIVE_QUERY_BUDGET,
        )
    except Exception as exc:
        logger.info(
            "graph_explorer: %s %s failed: %s", profile.profile, fail_label, exc
        )
        warnings.append(f"{profile.profile}: {exc}")
        return [], [], warnings
    unknown_weight_rows = sum(
        1 for row in result.rows if len(row) >= 3 and row[2] is None
    )
    if profile.weight_column and unknown_weight_rows:
        warnings.append(
            f"{profile.profile}: weight unknown for {unknown_weight_rows} "
            "edge group(s); the source returned no non-null weighted values"
        )
    nodes, edges = _rows_to_graph(result.rows, profile)
    return nodes, edges, warnings


def fetch_timeline_edges(
    ch: ClickHouseManager,
    profile: GraphProfile,
    *,
    node_ids: list[str],
    grain: str,
    range_start: str,
    range_end_exclusive: str,
    limit: int,
) -> tuple[list[list[Any]], bool, list[str]]:
    """One profile's time-bucketed edge rows for the Timeline mode.

    Returns ``(rows, truncated, warnings)`` where rows are already in
    TIMELINE_EDGES_COLUMNS order. The SQL asks for ``limit + 1`` rows — the
    presence of the extra row is the exact truncation signal (it is dropped).
    Undirected reciprocals are canonicalized in the SQL (least/greatest), so
    the edge id built here matches the investigate ``canonical_edge_id``
    convention and evidence lookups keep working. A query failure degrades to
    a warning, never an exception.
    """
    sql, params = build_timeline_sql(
        profile,
        node_ids=node_ids,
        grain=grain,
        range_start=range_start,
        range_end_exclusive=range_end_exclusive,
        limit=limit,
    )
    warnings: list[str] = []
    try:
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database="dbt",
            parameters=params,
            requested_max_rows=limit + 1,
            query_budget=INTERACTIVE_QUERY_BUDGET,
        )
    except Exception as exc:
        logger.info(
            "graph_explorer: %s timeline query failed: %s", profile.profile, exc
        )
        warnings.append(f"{profile.profile}: {exc}")
        return [], False, warnings

    raw = list(result.rows)
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
    rows: list[list[Any]] = []
    for row in raw:
        if len(row) < 2:
            continue
        src = "" if row[0] is None else str(row[0])
        tgt = "" if row[1] is None else str(row[1])
        if not src or not tgt:
            continue
        bucket_start = "" if len(row) < 3 or row[2] is None else str(row[2])
        bucket_end = "" if len(row) < 4 or row[3] is None else str(row[3])
        try:
            weight = float(row[4]) if len(row) >= 5 and row[4] is not None else 0.0
        except (TypeError, ValueError):
            weight = 0.0
        try:
            edge_count = int(row[5]) if len(row) >= 6 and row[5] is not None else 0
        except (TypeError, ValueError):
            edge_count = 0
        if profile.directed:
            edge_id = f"{profile.profile}:{src}->{tgt}"
        else:
            a, b = sorted((src, tgt))
            edge_id = f"{profile.profile}:{a}|{b}"
        rows.append(
            [
                edge_id,
                src,
                tgt,
                profile.profile,
                weight,
                edge_count,
                profile.directed,
                bucket_start,
                bucket_end,
            ]
        )
    return rows, truncated, warnings


def search_doc_hit(doc: dict[str, Any], score: float | None) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "type": doc.get("type"),
        "title": doc.get("title"),
        "module": doc.get("module", ""),
        "quality_tier": doc.get("quality_tier", ""),
        "payload_ref": doc.get("payload_ref"),
        "score": round(score, 4) if score is not None else None,
    }
