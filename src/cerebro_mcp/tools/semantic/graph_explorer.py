"""Graph Explorer mini app.

A cross-sector graph explorer whose profile catalog is driven entirely by
`cerebro.graph` metadata authored on dbt-cerebro semantic models. No
per-domain wiring lives here — the UI assembles the visible subgraph from
whatever graph-enabled profiles the semantic registry exposes.
"""

from __future__ import annotations

import importlib.resources
import logging
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.config import settings
from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.semantic.graph_profiles import (
    GraphProfile,
    build_evidence_sql,
    build_neighbors_sql,
    build_sample_sql,
    discover_profiles,
    profile_by_id,
    profiles_for_address_roles,
    suggested_next_hops,
)
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.models.mini_app import DatasetStats, MiniAppPayload
from cerebro_mcp.tools.visualization import mini_apps, web_apps
from cerebro_mcp.tools.visualization.mini_apps import MiniAppQueryError

logger = logging.getLogger(__name__)

GRAPH_EXPLORER_APP_ID = "graph_explorer"
GRAPH_EXPLORER_URI = "ui://cerebro/graph_explorer"
DEFAULT_TITLE = "Graph Explorer"
MAX_HOPS = 50
DEFAULT_WINDOW_DAYS = 365
DEFAULT_MAX_NEIGHBORS = 250
# BFS expansion ceilings (overridable via Settings / env). Promoted from a
# function-local literal so the cap is centralized and tunable. The cap is
# checked *after* each frontier round so at least one hop always expands, with
# a per-hop budget so a dense first frontier can't consume the whole cap.
BFS_NODE_CAP = settings.GRAPH_EXPLORER_BFS_NODE_CAP
BFS_PER_HOP_BUDGET = settings.GRAPH_EXPLORER_BFS_PER_HOP_BUDGET

_BUNDLED_HTML: str | None = None


def get_graph_explorer_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_HTML
    if _BUNDLED_HTML is None:
        try:
            _BUNDLED_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/graph_explorer.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>graph_explorer.html not built</div>"
                "</body></html>"
            )
    return _BUNDLED_HTML


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------


def _empty_dataset(label: str, columns: list[str], sql: str = "") -> CachedDataset:
    return CachedDataset(
        columns=list(columns),
        column_types=["String"] * len(columns),
        rows=[],
        stats=DatasetStats(
            row_count=0,
            rows_returned=0,
            mode="exact_bounded",
            sample_source_rows=0,
            elapsed_seconds=0.0,
            warnings=[],
        ),
        sql=sql or f"-- assembled in-process for {label}",
        database="dbt",
        parameters={},
    )


def _dataset_from_rows(
    columns: list[str], rows: list[list[Any]], label: str, sql: str = ""
) -> CachedDataset:
    return CachedDataset(
        columns=list(columns),
        column_types=["String"] * len(columns),
        rows=rows,
        stats=DatasetStats(
            row_count=len(rows),
            rows_returned=len(rows),
            mode="exact_bounded",
            sample_source_rows=len(rows),
            elapsed_seconds=0.0,
            warnings=[],
        ),
        sql=sql or f"-- assembled in-process for {label}",
        database="dbt",
        parameters={},
    )


NODES_COLUMNS = ["id", "kind", "label", "profiles"]
EDGES_COLUMNS = [
    "id",
    "source",
    "target",
    "profile",
    "weight",
    "edge_count",
    "directed",
]
EDGE_EVIDENCE_COLUMNS = ["edge_id", "column", "value"]
NODE_EVIDENCE_COLUMNS = ["node_id", "column", "value"]
METRICS_COLUMNS = ["metric", "value"]

DATASET_TITLES = {
    "nodes": "Nodes",
    "edges": "Edges",
    "node_evidence": "Node Evidence",
    "edge_evidence": "Edge Evidence",
    "graph_metrics": "Graph Metrics",
}


def _short(addr: str) -> str:
    if not addr:
        return ""
    return addr if len(addr) <= 14 else f"{addr[:6]}…{addr[-4:]}"


def _profile_card(profile: GraphProfile) -> dict[str, Any]:
    return {
        "profile": profile.profile,
        "model_name": profile.model_name,
        "module": profile.module,
        "description": profile.description,
        "source_kind": profile.source_kind,
        "target_kind": profile.target_kind,
        "semantic_status": profile.semantic_status,
        "quality_tier": profile.quality_tier,
        "question_synonyms": list(profile.question_synonyms),
        "semantic_source_file": profile.semantic_source_file,
        "time_aware": profile.time_aware,
        "directed": profile.directed,
        "weight_column": profile.weight_column,
    }


def _empty_state(title: str) -> dict[str, Any]:
    profiles = [_profile_card(p) for p in discover_profiles()]
    return {
        "title": title,
        "catalog": profiles,
        "selected_profiles": [],
        "seed_node": {"id": "", "kind": ""},
        "selected_node_id": "",
        "selected_edge_id": "",
        "relation_types": [],
        "layout": "force",
        "transfer_window_days": DEFAULT_WINDOW_DAYS,
        "max_neighbors": DEFAULT_MAX_NEIGHBORS,
        "hops": 0,
        "semantic_status_filter": "all",
        "suggested_next_hops": [],
        "node_roles": {},
        "warnings": [],
    }


def _seed_kind_of(roles: dict[str, Any] | None) -> str:
    if not roles:
        return "address"
    # Prefer the most specific role.
    if roles.get("is_circles_avatar"):
        return "circles_avatar"
    if roles.get("is_gpay_wallet"):
        return "gpay_wallet"
    if roles.get("is_safe"):
        return "safe"
    if roles.get("is_pool"):
        return "pool"
    if roles.get("is_circles_wrapper"):
        return "token"
    return "address"


def _build_payload(record: mini_apps.ViewRecord) -> MiniAppPayload:
    titles = DATASET_TITLES
    descriptors = {
        key: mini_apps.build_dataset_descriptor(
            key=key, dataset=dataset, title=titles.get(key, key)
        )
        for key, dataset in record.datasets.items()
    }
    warnings = list(record.view_state.get("warnings") or [])
    warnings += mini_apps.collect_dataset_warnings(*record.datasets.values())
    seen: list[str] = []
    for warning in warnings:
        if warning and warning not in seen:
            seen.append(warning)
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=record.view_id,
        app_id=GRAPH_EXPLORER_APP_ID,
        title=record.title,
        status="ready",
        summary_cards=[],
        datasets=descriptors,
        view_state=record.view_state,
        provenance={"source": "semantic_registry"},
        warnings=seen,
    )


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------


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


def _resolve_address_roles(ch: ClickHouseManager, address: str) -> dict[str, Any]:
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


def _node_evidence_rows(node_id: str, roles: dict[str, Any]) -> list[list[Any]]:
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


def _edge_evidence_rows(
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


# ---------------------------------------------------------------------------
# Edge fetching
# ---------------------------------------------------------------------------


def _pick_direction(profile: GraphProfile, seed_kind: str) -> str:
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


def _fetch_edges(
    ch: ClickHouseManager,
    profile: GraphProfile,
    *,
    seed_ids: list[str],
    direction: str,
    window_days: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    sql, params = build_neighbors_sql(
        profile,
        seed_ids=seed_ids,
        direction=direction,
        window_days=window_days,
        limit=limit,
    )
    warnings: list[str] = []
    try:
        result = mini_apps.run_structured_query(
            ch, sql, database="dbt", parameters=params, requested_max_rows=limit
        )
    except Exception as exc:
        logger.info("graph_explorer: %s query failed: %s", profile.profile, exc)
        warnings.append(f"{profile.profile}: {exc}")
        return [], [], warnings

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for row in result.rows:
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
                    "label": _short(node_id),
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
    return list(nodes.values()), edges, warnings


def _fetch_sample_edges(
    ch: ClickHouseManager,
    profile: GraphProfile,
    *,
    window_days: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Load top-weight edges of a profile without any seed filter."""
    sql, params = build_sample_sql(profile, window_days=window_days, limit=limit)
    warnings: list[str] = []
    try:
        result = mini_apps.run_structured_query(
            ch, sql, database="dbt", parameters=params, requested_max_rows=limit
        )
    except Exception as exc:
        logger.info("graph_explorer: %s sample query failed: %s", profile.profile, exc)
        warnings.append(f"{profile.profile}: {exc}")
        return [], [], warnings

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for row in result.rows:
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
                    "label": _short(node_id),
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
    return list(nodes.values()), edges, warnings


def _merge_graph(
    existing_nodes: list[list[Any]],
    existing_edges: list[list[Any]],
    new_nodes: list[dict[str, Any]],
    new_edges: list[dict[str, Any]],
) -> tuple[list[list[Any]], list[list[Any]]]:
    node_index: dict[str, list[Any]] = {}
    for row in existing_nodes:
        if row and row[0]:
            node_index[str(row[0])] = list(row)
    for node in new_nodes:
        node_id = node["id"]
        if node_id in node_index:
            current_profiles = node_index[node_id][3] or []
            merged = list(current_profiles)
            for profile in node["profiles"]:
                if profile not in merged:
                    merged.append(profile)
            node_index[node_id][3] = merged
        else:
            node_index[node_id] = [node["id"], node["kind"], node["label"], node["profiles"]]
    edge_rows = list(existing_edges)
    seen_edge_ids = {row[0] for row in edge_rows if row}
    for edge in new_edges:
        if edge["id"] in seen_edge_ids:
            continue
        edge_rows.append(
            [
                edge["id"],
                edge["source"],
                edge["target"],
                edge["profile"],
                edge["weight"],
                edge["edge_count"],
                edge["directed"],
            ]
        )
        seen_edge_ids.add(edge["id"])
    return list(node_index.values()), edge_rows


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_graph_explorer_tools(mcp, ch: ClickHouseManager) -> None:
    mini_apps.register_app(
        GRAPH_EXPLORER_APP_ID, title=DEFAULT_TITLE, resource_uri=GRAPH_EXPLORER_URI
    )

    @mcp.resource(GRAPH_EXPLORER_URI, mime_type="text/html;profile=mcp-app")
    def serve_graph_explorer_app() -> str:
        return get_graph_explorer_html()

    @mcp.tool(
        meta={
            "ui": {"resourceUri": GRAPH_EXPLORER_URI},
            "ui/resourceUri": GRAPH_EXPLORER_URI,
        }
    )
    def open_graph_explorer(
        seed_node_id: str = "", seed_model: str = "", title: str = ""
    ) -> CallToolResult:
        """Open the Graph Explorer mini app.

        With no seed, renders the sector-grouped semantic graph catalog and
        lets the user browse profiles. If a seed address is given, also
        loads a 1-hop subgraph and auto-detects applicable profiles.
        """
        effective_title = title or DEFAULT_TITLE
        view_id = mini_apps.create_view(GRAPH_EXPLORER_APP_ID, effective_title)
        mini_apps.patch_view_state(view_id, _empty_state(effective_title))
        mini_apps.replace_view_datasets(
            view_id,
            {
                "nodes": _empty_dataset("nodes", NODES_COLUMNS),
                "edges": _empty_dataset("edges", EDGES_COLUMNS),
                "node_evidence": _empty_dataset("node_evidence", NODE_EVIDENCE_COLUMNS),
                "edge_evidence": _empty_dataset("edge_evidence", EDGE_EVIDENCE_COLUMNS),
                "graph_metrics": _empty_dataset("graph_metrics", METRICS_COLUMNS),
            },
        )
        if seed_node_id:
            return load_graph_explorer_seed(view_id, seed_node_id, seed_model)
        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Graph Explorer ready with {len(payload.view_state.get('catalog') or [])} "
                f"profiles (view_id={view_id[:8]})"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": GRAPH_EXPLORER_URI},
            "ui/resourceUri": GRAPH_EXPLORER_URI,
        }
    )
    def load_graph_explorer_seed(
        view_id: str,
        seed_node_id: str,
        seed_model: str = "",
        relation_types: list[str] | None = None,
        hops: int = 1,
        transfer_window_days: int = DEFAULT_WINDOW_DAYS,
        max_neighbors: int = DEFAULT_MAX_NEIGHBORS,
    ) -> CallToolResult:
        """Load a bounded 1-hop subgraph around seed_node_id.

        When `relation_types` is empty and `seed_model` is unset, the tool
        consults `int_execution_address_roles_current` to auto-detect which
        graph profiles apply to the seed address.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")

        seed_id = (seed_node_id or "").strip()
        all_profiles = discover_profiles()

        # Sample mode: no seed address but a specific profile requested.
        # Load top-weight edges of that profile so the user can click a node to
        # promote it into a real seed.
        is_sample_mode = not seed_id and bool(seed_model)
        if not seed_id and not seed_model and not relation_types:
            return mini_apps.error_call_tool_result(
                "Provide seed_node_id (to seed from an address) or seed_model (to sample a profile)"
            )

        if is_sample_mode:
            roles: dict[str, Any] = {}
            profile = profile_by_id(seed_model)
            if profile is None:
                return mini_apps.error_call_tool_result(f"Unknown profile: {seed_model}")
            profiles = [profile]
        else:
            roles = _resolve_address_roles(ch, seed_id) if seed_id else {}
            if relation_types:
                chosen_ids = set(relation_types)
                profiles = [p for p in all_profiles if p.profile in chosen_ids]
            elif seed_model:
                profile = profile_by_id(seed_model)
                profiles = [profile] if profile else []
            else:
                role_profile_ids = profiles_for_address_roles(roles)
                profiles = [p for p in all_profiles if p.profile in set(role_profile_ids)]
                if not profiles:
                    profiles = [p for p in all_profiles if p.profile == "token_transfers"] or all_profiles

        window_days = max(1, int(transfer_window_days or DEFAULT_WINDOW_DAYS))
        limit = max(1, int(max_neighbors or DEFAULT_MAX_NEIGHBORS))

        # Determine seed_kind early so we can use it for direction picking.
        seed_kind = _seed_kind_of(roles) if roles else ""
        if not seed_kind and seed_id and profiles:
            seed_kind = profiles[0].source_kind or "address"

        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        warnings: list[str] = []
        successful: list[str] = []
        for profile in profiles:
            if is_sample_mode:
                nodes, edges, warn = _fetch_sample_edges(
                    ch, profile, window_days=window_days, limit=limit
                )
            else:
                direction = _pick_direction(profile, seed_kind)
                nodes, edges, warn = _fetch_edges(
                    ch,
                    profile,
                    seed_ids=[seed_id],
                    direction=direction,
                    window_days=window_days,
                    limit=limit,
                )
            warnings.extend(warn)
            if edges:
                successful.append(profile.profile)
            all_nodes.extend(nodes)
            all_edges.extend(edges)

        if seed_id and seed_id not in {n["id"] for n in all_nodes}:
            all_nodes.append(
                {
                    "id": seed_id,
                    "kind": seed_kind or "address",
                    "label": _short(seed_id),
                    "profiles": successful,
                }
            )

        node_rows, edge_rows = _merge_graph([], [], all_nodes, all_edges)
        mini_apps.replace_view_datasets(
            view_id,
            {
                **record.datasets,
                "nodes": _dataset_from_rows(NODES_COLUMNS, node_rows, "nodes"),
                "edges": _dataset_from_rows(EDGES_COLUMNS, edge_rows, "edges"),
            },
        )

        metrics_rows = [
            ["node_count", float(len(node_rows))],
            ["edge_count", float(len(edge_rows))],
            ["profile_count", float(len(profiles))],
            ["window_days", float(window_days)],
        ]
        mini_apps.attach_dataset(
            view_id,
            "graph_metrics",
            _dataset_from_rows(METRICS_COLUMNS, metrics_rows, "graph_metrics"),
        )

        suggestions = suggested_next_hops(seed_kind or "address", all_profiles)
        node_roles_patch: dict[str, Any] = {}
        if seed_id and roles:
            node_roles_patch[seed_id] = roles
        mini_apps.patch_view_state(
            view_id,
            {
                "seed_node": {"id": seed_id, "kind": seed_kind or ""},
                "mode": "sample" if is_sample_mode else "seed",
                "selected_profiles": [p.profile for p in profiles],
                "relation_types": [p.profile for p in profiles],
                "hops": max(1, min(int(hops or 1), MAX_HOPS)),
                "transfer_window_days": window_days,
                "max_neighbors": limit,
                "node_roles": node_roles_patch,
                "suggested_next_hops": suggestions,
                "warnings": warnings,
            },
        )

        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Loaded {len(edge_rows)} edges across {len(profiles)} profiles "
                f"(view_id={view_id[:8]})"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": GRAPH_EXPLORER_URI},
            "ui/resourceUri": GRAPH_EXPLORER_URI,
        }
    )
    def expand_graph_explorer_node(
        view_id: str,
        node_id: str,
        relation_types: list[str] | None = None,
        direction: str = "both",
        hops: int = 1,
    ) -> CallToolResult:
        """Expand `node_id` by one hop across the selected profiles.

        Capped at `MAX_HOPS` total hops per load session; reseed to explore
        further.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")
        state = record.view_state
        current_hops = int(state.get("hops", 1) or 1)
        if current_hops >= MAX_HOPS:
            return mini_apps.error_call_tool_result(
                f"Max {MAX_HOPS} hops reached. Reseed the graph to explore further."
            )
        if not node_id:
            return mini_apps.error_call_tool_result("node_id is required")
        if direction not in ("in", "out", "both"):
            return mini_apps.error_call_tool_result("direction must be 'in', 'out', or 'both'")

        chosen_ids = set(relation_types) if relation_types else set(state.get("selected_profiles") or [])
        if not chosen_ids:
            chosen_ids = {p.profile for p in discover_profiles()}
        profiles = [p for p in discover_profiles() if p.profile in chosen_ids]

        window_days = int(state.get("transfer_window_days") or DEFAULT_WINDOW_DAYS)
        limit = int(state.get("max_neighbors") or DEFAULT_MAX_NEIGHBORS)

        # Resolve the kind of the node being expanded so we can auto-pick
        # direction per profile (avoids type errors on asymmetric profiles
        # when a bare-address node is expanded against profile whose target
        # is non-string).
        expand_roles = _resolve_address_roles(ch, node_id)
        expand_kind = _seed_kind_of(expand_roles) if expand_roles else ""
        if not expand_kind:
            # Fall back to what the node-row carries in the dataset.
            existing_rows = record.datasets.get("nodes")
            if existing_rows and existing_rows.rows:
                for row in existing_rows.rows:
                    if row and str(row[0]) == node_id:
                        expand_kind = str(row[1]) if len(row) > 1 else ""
                        break

        # Multi-hop BFS: expand the frontier `hops` times so callers actually
        # see *paths* grow, not just a single node's 1-hop neighbours. Bounded
        # by a global node cap so a dense seed doesn't blow up the renderer.
        nodes_ds = record.datasets.get("nodes")
        edges_ds = record.datasets.get("edges")
        existing_nodes = list(nodes_ds.rows) if nodes_ds else []
        existing_edges = list(edges_ds.rows) if edges_ds else []
        merged_nodes = list(existing_nodes)
        merged_edges = list(existing_edges)
        already_expanded: set[str] = set()
        frontier: list[tuple[str, str]] = [(node_id, expand_kind or "address")]
        warnings: list[str] = []
        hops_to_run = max(1, int(hops or 1))
        all_catalog = list(discover_profiles())
        # True only when a frontier round genuinely had to truncate against the
        # cap/budget — drives the user-facing warning so it doesn't fire merely
        # because the *initial* seed load was already large.
        truncated_at_hop: int | None = None

        for hop_round in range(hops_to_run):
            if not frontier:
                break
            # Per-round node budget: bounded both by the remaining global cap and
            # by the per-hop budget so one dense frontier can't consume everything.
            remaining_global = BFS_NODE_CAP - len(merged_nodes)
            if remaining_global <= 0:
                truncated_at_hop = hop_round
                break
            round_budget = min(remaining_global, BFS_PER_HOP_BUDGET)
            nodes_at_round_start = len(merged_nodes)
            next_frontier: list[tuple[str, str]] = []
            round_truncated = False
            for cur_id, cur_kind in frontier:
                if cur_id in already_expanded:
                    continue
                already_expanded.add(cur_id)
                # Pick profiles compatible with this node's kind. Fall back to
                # whole chosen_ids set if kind is unknown.
                if cur_kind:
                    step_profiles = [
                        p
                        for p in all_catalog
                        if p.profile in chosen_ids
                        and (p.source_kind == cur_kind or p.target_kind == cur_kind)
                    ]
                else:
                    step_profiles = profiles
                for profile in step_profiles:
                    eff_dir = (
                        direction
                        if direction != "both"
                        else _pick_direction(profile, cur_kind or "")
                    )
                    new_n, new_e, warn = _fetch_edges(
                        ch,
                        profile,
                        seed_ids=[cur_id],
                        direction=eff_dir,
                        window_days=window_days,
                        limit=limit,
                    )
                    warnings.extend(warn)
                    # Learn each new neighbour's kind from its node row so the
                    # next BFS round can pick the right profile for it.
                    for n in new_n:
                        nid = str(n[0]) if isinstance(n, list) else str(n.get("id", ""))
                        nkind = str(n[1]) if isinstance(n, list) else str(n.get("kind", ""))
                        if nid and nid not in already_expanded:
                            next_frontier.append((nid, nkind))
                    merged_nodes, merged_edges = _merge_graph(
                        merged_nodes, merged_edges, new_n, new_e
                    )
                    # Stop once this round has spent its budget — but only after
                    # having added at least some neighbours, so a hop always
                    # makes progress before we declare truncation.
                    if len(merged_nodes) - nodes_at_round_start >= round_budget:
                        round_truncated = True
                        break
                if round_truncated:
                    break
            frontier = next_frontier
            # Genuine truncation: we hit a budget AND there is still frontier
            # left we didn't get to expand.
            if round_truncated and frontier:
                truncated_at_hop = hop_round + 1
                break

        if truncated_at_hop is not None:
            warnings.append(
                f"BFS reached the {BFS_NODE_CAP}-node cap after hop "
                f"{truncated_at_hop}; {len(merged_nodes)} nodes loaded. "
                "Reseed or narrow profiles/window to go deeper."
            )

        # UX fallback: if the caller's chosen profiles couldn't emit any edges
        # from this node (e.g. expanding a token node with only
        # lending_user_to_reserve — source_kind=address), retry with ALL
        # profiles whose source_kind matches the node's kind. Prevents the
        # "click expand, nothing happens" failure mode.
        gained_edges = len(merged_edges) - len(existing_edges)
        widened = False
        if gained_edges == 0 and expand_kind:
            widen_profiles = [
                p
                for p in discover_profiles()
                if p.profile not in chosen_ids
                and p.source_kind == expand_kind
            ]
            for profile in widen_profiles:
                effective_direction = (
                    direction
                    if direction != "both"
                    else _pick_direction(profile, expand_kind)
                )
                nodes, edges, warn = _fetch_edges(
                    ch,
                    profile,
                    seed_ids=[node_id],
                    direction=effective_direction,
                    window_days=window_days,
                    limit=limit,
                )
                if edges:
                    widened = True
                    warnings.extend(warn)
                    merged_nodes, merged_edges = _merge_graph(
                        merged_nodes, merged_edges, nodes, edges
                    )
                    chosen_ids.add(profile.profile)
            if widened:
                warnings.append(
                    "No edges found under selected profiles — widened to "
                    f"{', '.join(sorted(chosen_ids))}."
                )

        mini_apps.replace_view_datasets(
            view_id,
            {
                **record.datasets,
                "nodes": _dataset_from_rows(NODES_COLUMNS, merged_nodes, "nodes"),
                "edges": _dataset_from_rows(EDGES_COLUMNS, merged_edges, "edges"),
            },
        )
        # Refresh graph_metrics and selected_profiles so the UI status bar
        # and chip group stay consistent with the real graph after expand.
        profile_ids_in_graph = sorted(set(str(e[3]) for e in merged_edges if len(e) > 3))
        metrics_rows = [
            ["node_count", float(len(merged_nodes))],
            ["edge_count", float(len(merged_edges))],
            ["profile_count", float(len(profile_ids_in_graph))],
            ["window_days", float(window_days)],
        ]
        mini_apps.attach_dataset(
            view_id,
            "graph_metrics",
            _dataset_from_rows(METRICS_COLUMNS, metrics_rows, "graph_metrics"),
        )
        prev_selected = set(state.get("selected_profiles") or [])
        merged_profiles = sorted(prev_selected | chosen_ids)
        mini_apps.patch_view_state(
            view_id,
            {
                "hops": min(current_hops + max(1, int(hops or 1)), MAX_HOPS),
                "warnings": warnings,
                "selected_profiles": merged_profiles,
                "relation_types": merged_profiles,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return mini_apps.payload_to_call_tool_result(
            _build_payload(updated),
            summary_text=(
                f"Expanded {node_id[:10]}… +{hops} hop; "
                f"{len(merged_edges)} edges total."
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": GRAPH_EXPLORER_URI},
            "ui/resourceUri": GRAPH_EXPLORER_URI,
        }
    )
    def update_graph_explorer_focus(
        view_id: str,
        selected_node_id: str = "",
        selected_edge_id: str = "",
        relation_types: list[str] | None = None,
        layout: str = "",
        transfer_window_days: int = 0,
        max_neighbors: int = 0,
        semantic_status_filter: str = "",
    ) -> CallToolResult:
        """Mutate view state only (selection, filters, layout). No refetch."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")

        patch: dict[str, Any] = {}
        if selected_node_id:
            patch["selected_node_id"] = selected_node_id
        if selected_edge_id:
            patch["selected_edge_id"] = selected_edge_id
        if relation_types is not None:
            patch["relation_types"] = list(relation_types)
        if layout:
            patch["layout"] = layout
        if transfer_window_days:
            patch["transfer_window_days"] = int(transfer_window_days)
        if max_neighbors:
            patch["max_neighbors"] = int(max_neighbors)
        if semantic_status_filter:
            if semantic_status_filter not in ("all", "approved", "candidate"):
                return mini_apps.error_call_tool_result(
                    "semantic_status_filter must be 'all', 'approved', or 'candidate'"
                )
            patch["semantic_status_filter"] = semantic_status_filter

        if selected_node_id:
            # Refresh role info + cross-sector suggestions for the newly
            # selected node so the details panel stays in sync.
            roles = _resolve_address_roles(ch, selected_node_id)
            node_roles = dict(record.view_state.get("node_roles") or {})
            node_roles[selected_node_id] = roles
            patch["node_roles"] = node_roles
            patch["suggested_next_hops"] = suggested_next_hops(
                _seed_kind_of(roles), discover_profiles()
            )
            mini_apps.attach_dataset(
                view_id,
                "node_evidence",
                _dataset_from_rows(
                    NODE_EVIDENCE_COLUMNS,
                    _node_evidence_rows(selected_node_id, roles),
                    "node_evidence",
                ),
            )

        if selected_edge_id:
            mini_apps.attach_dataset(
                view_id,
                "edge_evidence",
                _dataset_from_rows(
                    EDGE_EVIDENCE_COLUMNS,
                    _edge_evidence_rows(ch, selected_edge_id),
                    "edge_evidence",
                ),
            )

        if not patch and not selected_node_id and not selected_edge_id:
            return mini_apps.error_call_tool_result("no fields to update")
        if patch:
            mini_apps.patch_view_state(view_id, patch)

        updated = mini_apps.get_view(view_id)
        assert updated is not None
        # Surface any evidence datasets refreshed above so the details panel
        # can render them without a full INITIAL_LOAD (which would reset the
        # graph layout/zoom).
        dataset_patch: dict[str, Any] = {}
        for key in ("node_evidence", "edge_evidence"):
            dataset = updated.datasets.get(key)
            if dataset is not None:
                dataset_patch[key] = mini_apps.build_dataset_descriptor(
                    key=key, dataset=dataset, title=DATASET_TITLES.get(key, key)
                )
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=updated.view_id,
            app_id=GRAPH_EXPLORER_APP_ID,
            title=updated.title,
            status="ready",
            patch={"view_state": patch, "datasets": dataset_patch}
            if dataset_patch
            else {"view_state": patch},
        )
        return mini_apps.payload_to_call_tool_result(
            payload, summary_text="Graph Explorer focus updated."
        )

    web_apps.register_web_app(
        app_id=GRAPH_EXPLORER_APP_ID,
        open_tool="open_graph_explorer",
        html_loader=get_graph_explorer_html,
        tools={
            "open_graph_explorer": open_graph_explorer,
            "load_graph_explorer_seed": load_graph_explorer_seed,
            "expand_graph_explorer_node": expand_graph_explorer_node,
            "update_graph_explorer_focus": update_graph_explorer_focus,
        },
    )
