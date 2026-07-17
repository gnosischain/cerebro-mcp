"""Graph Explorer data access: role resolution, edge fetching, evidence.

One row-parsing path for every profile query (`_rows_to_graph`) — seeded and
sample fetches share it.
"""

from __future__ import annotations

import logging
from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.semantic.graph_profiles import (
    GraphProfile,
    build_evidence_sql,
    build_neighbors_sql,
    build_sample_sql,
    profile_by_id,
)
from cerebro_mcp.tools.visualization import mini_apps

from .state import short_id

logger = logging.getLogger(__name__)


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


def resolve_address_roles(ch: ClickHouseManager, address: str) -> dict[str, Any]:
    if not address:
        return {}
    try:
        result = mini_apps.run_structured_query(
            ch,
            _ROLES_SQL,
            database="dbt",
            parameters={"addr": address.lower()},
            requested_max_rows=1,
        )
    except Exception as exc:
        logger.info("graph_explorer: roles lookup failed for %s: %s", address, exc)
        return {}
    if not result.rows:
        return {}
    row = result.rows[0]
    return {col: value for col, value in zip(result.columns, row)}


def node_evidence_rows(node_id: str, roles: dict[str, Any]) -> list[list[Any]]:
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
        rows.append([node_id, col, str(value)])
    return rows


def edge_evidence_rows(
    ch: ClickHouseManager, edge_id: str, limit: int = 25
) -> list[list[Any]]:
    """Resolve the raw backing rows for a selected edge via the profile's
    evidence model. Edge ids are `{profile}:{src}->{tgt}`."""
    if not edge_id or ":" not in edge_id or "->" not in edge_id:
        return []
    profile_id, _, endpoints = edge_id.partition(":")
    src, _, tgt = endpoints.partition("->")
    profile = profile_by_id(profile_id)
    if profile is None or not src or not tgt:
        return []
    try:
        sql, params = build_evidence_sql(
            profile, source_id=src, target_id=tgt, limit=limit
        )
        result = mini_apps.run_structured_query(
            ch, sql, database="dbt", parameters=params, requested_max_rows=limit
        )
    except Exception as exc:
        logger.info("graph_explorer: edge evidence failed for %s: %s", edge_id, exc)
        return []
    rows: list[list[Any]] = []
    for row in result.rows:
        for col, value in zip(result.columns, row):
            if value in (None, ""):
                continue
            rows.append([edge_id, col, str(value)])
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
        weight = 0.0
        if len(row) >= 3 and row[2] is not None:
            try:
                weight = float(row[2])
            except (TypeError, ValueError):
                weight = 0.0
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
            ch, sql, database="dbt", parameters=params, requested_max_rows=limit
        )
    except Exception as exc:
        logger.info(
            "graph_explorer: %s %s failed: %s", profile.profile, fail_label, exc
        )
        warnings.append(f"{profile.profile}: {exc}")
        return [], [], warnings
    nodes, edges = _rows_to_graph(result.rows, profile)
    return nodes, edges, warnings


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
