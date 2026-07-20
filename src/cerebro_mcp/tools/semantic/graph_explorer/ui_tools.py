"""Graph Explorer UI tools — the 4 agent-facing tools + 2 app-only tools.

view_state v2: per-mode namespaces (``atlas`` / ``investigate``) sharing one
canvas. Dataset writes are PER KEY (``attach_dataset``) — an investigate load
must never drop the ``atlas_*`` datasets and vice versa. Limits are published
via ``view_state["limits"]``; all limit reads go through ``constants.<NAME>``
attribute access so tests can monkeypatch them.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.models.mini_app import MiniAppPayload
from cerebro_mcp.semantic.graph_profiles import (
    discover_profiles,
    profile_by_id,
    profiles_for_address_roles,
    suggested_next_hops,
)
from cerebro_mcp.tools.visualization import mini_apps

from . import constants
from .fetch import (
    ADDRESS_ROLE_COLUMNS,
    ADDRESS_ROLES_RELATION,
    EvidenceQueryStatus,
    edge_evidence_rows_with_status,
    fetch_profile_edges,
    node_evidence_rows,
    pick_direction,
    resolve_address_roles,
    resolve_address_roles_with_status,
)
from .forensics import (
    forensic_scope,
    new_scope_id,
    physical_columns_from_expression,
    source_record,
    validate_source_contract,
)
from .state import (
    build_payload,
    dataset_from_rows,
    empty_dataset,
    empty_state,
    seed_kind_of,
    short_id,
)
from .traverse import bfs_expand, merge_graph

logger = logging.getLogger(__name__)

_focus_locks_guard = threading.Lock()
_focus_locks: dict[str, threading.Lock] = {}


def _focus_lock(view_id: str) -> threading.Lock:
    """Per-view serialization for focus/evidence mutations.

    ``mini_apps`` protects each individual attach/patch, but a focus response
    spans two datasets plus selection state.  This outer lock makes that
    compound operation indivisible with respect to every Graph Explorer focus
    command without broadening the shared mini-app platform API.
    """
    with _focus_locks_guard:
        return _focus_locks.setdefault(view_id, threading.Lock())


def _serialized_focus_mutation(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        view_id = str(kwargs.get("view_id") or (args[0] if args else ""))
        with _focus_lock(view_id):
            return fn(*args, **kwargs)

    return wrapped

# Keys set_graph_explorer_view may patch (nested per namespace). Selection
# changes go through update_graph_explorer_focus instead — it also refreshes
# evidence datasets.
_VIEW_PATCH_SCHEMA: dict[str, set[str] | type] = {
    "mode": str,
    "layout": str,
    "semantic_status_filter": str,
    "atlas": {"selected_profiles", "sample_size", "window_days"},
    "investigate": {"active_profiles", "window_days", "max_neighbors"},
    # cursor/playing are deliberately ABSENT: playback state is client-local
    # and must never round-trip (a test pins the rejection).
    "timeline": {"profiles", "grain", "range_days", "window_buckets"},
    # seeds/t0/t1/counts/expanded/token_catalog are server-owned.
    "flows": {"direction", "hops", "range_days", "min_usd", "tokens", "include_bridges"},
    # tx_hashes/seed/counterparties/counts/scope are server-owned — the scope
    # contract in particular must never be client-writable, or the app could
    # be made to claim a coverage it does not have.
    "transactions": {"range_days", "max_txs", "tokens", "min_usd"},
}


import re as _re

_EVM_ADDR_RE = _re.compile(r"^0x[0-9a-fA-F]{40}$")


def _normalize_node_id(node_id: str) -> str:
    """Lowercase EVM addresses. On-chain address columns are stored
    lowercase, and the edge SQL compares as strings — a checksummed seed
    like 0x295bA5c… would silently match nothing (roles already lowercase;
    the edge path must too). Non-address ids pass through unchanged."""
    value = (node_id or "").strip()
    return value.lower() if _EVM_ADDR_RE.match(value) else value


def _graph_metrics_rows(
    node_rows: list, edge_rows: list, profile_count: int, window_days: int
) -> list[list[Any]]:
    return [
        ["node_count", float(len(node_rows))],
        ["edge_count", float(len(edge_rows))],
        ["profile_count", float(profile_count)],
        ["window_days", float(window_days)],
    ]


def _profile_contract_columns(profile) -> list[str]:
    authored = [profile.source_column, profile.target_column]
    authored.extend(optional for optional in (
        profile.time_column,
        profile.time_end_column,
        profile.weight_column,
    ) if optional)
    columns: list[str] = []
    for expression in authored:
        for column in physical_columns_from_expression(str(expression)):
            if column not in columns:
                columns.append(column)
    return columns


def _relationship_sources(
    ch: ClickHouseManager,
    profiles: list[Any],
) -> tuple[list[Any], list[dict[str, Any]], list[str]]:
    """Validate selected relationship relations before querying them.

    Returns usable profiles, structured source records, and precise failures.
    A retired/dropped model therefore becomes a failed/partial scope rather
    than an empty graph that looks like verified absence.
    """
    usable: list[Any] = []
    sources: list[dict[str, Any]] = []
    failures: list[str] = []
    for profile in profiles:
        contract = validate_source_contract(
            ch,
            profile.relation_name,
            _profile_contract_columns(profile),
            probe_horizon=True,
            horizon_column=profile.time_column,
        )
        relation = str(profile.relation_name)
        if "." not in relation.replace("`", ""):
            relation = f"dbt.{relation.strip('`')}"
        ok = bool(contract.get("ok"))
        sources.append(
            source_record(
                kind="dbt_aggregate",
                name=relation.replace("`", ""),
                role="primary",
                status="ok" if ok else "error",
                horizon=contract.get("horizon"),
                horizon_basis=contract.get("horizon_basis"),
                fetched_at=(
                    contract.get("freshness_checked_at")
                    or contract.get("checked_at")
                ),
                error=contract.get("error"),
            )
        )
        if ok:
            usable.append(profile)
        else:
            failures.append(
                f"{profile.profile}: {contract.get('error') or 'source contract failed'}"
            )
    return usable, sources, failures


def _record_relationship_query_failure(
    profile: Any,
    query_warnings: list[str],
    sources: list[dict[str, Any]],
    source_failures: list[str],
) -> None:
    """Override a cached contract success when the actual query fails."""
    if not query_warnings:
        return
    weight_unknown = all("weight unknown for" in item for item in query_warnings)
    message = (
        "; ".join(query_warnings)
        if weight_unknown
        else f"{profile.profile}: actual query failed: {'; '.join(query_warnings)}"
    )
    source_failures.append(message)
    relation = str(profile.relation_name).replace("`", "")
    if "." not in relation:
        relation = f"dbt.{relation}"
    for source in sources:
        if str(source.get("name") or "") == relation:
            source["status"] = "partial" if weight_unknown else "error"
            source["error"] = message
            break


def _relationship_scope(
    *,
    kind: str,
    request_id: int,
    window_days: int,
    sources: list[dict[str, Any]],
    source_failures: list[str],
    warnings: list[str],
    node_count: int,
    edge_count: int,
    truncated: bool,
    truncation_rule: str,
    subjects: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    status = (
        "failed"
        if source_failures
        and not any(
            source.get("status") in {"ok", "partial"} for source in sources
        )
        else "partial"
        if source_failures or truncated
        else "ready"
    )
    hard_source_failure = any(
        source.get("status") == "error" for source in sources
    )
    structural_exact = not truncated and not hard_source_failure
    source_horizons = [
        str(source.get("horizon"))
        for source in sources
        if source.get("horizon") is not None
    ]
    return forensic_scope(
        scope_id=new_scope_id(kind, request_id),
        request_id=request_id,
        status=status,
        t0=(now - timedelta(days=max(1, window_days))).isoformat(),
        t1=now.isoformat(),
        window_source=f"{kind}.window_days",
        # Relations can have different clocks; every source retains its own
        # exact horizon while the scope advertises the newest observed one.
        data_horizon=max(source_horizons) if source_horizons else None,
        sources=sources,
        rows_returned=edge_count,
        rows_total=edge_count if structural_exact else None,
        nodes_returned=node_count,
        nodes_total=node_count if structural_exact else None,
        edges_returned=edge_count,
        edges_total=edge_count if structural_exact else None,
        truncated=truncated,
        truncation_rule=truncation_rule,
        residuals=(
            "profile relations have heterogeneous weight units",
            "source horizons are reported per answering relationship",
        ),
        warnings=[*warnings, *source_failures],
        verification_status="verified" if structural_exact else "unverified",
        verification_method=(
            "source contracts plus a result below every query cap; nullable "
            "weights are disclosed separately"
            if structural_exact
            else "source/cap reconciliation incomplete"
        ),
        query_kind=kind,
        evidence_class="relationship_adjacency",
        subjects=subjects,
    )


def _focus_source_context(
    ch: ClickHouseManager,
    selected_node_id: str,
    selected_edge_id: str,
    query_status: EvidenceQueryStatus | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Describe the source actually queried for focus evidence.

    Contract freshness and the data query are separate safety checks. A cached
    contract success therefore cannot hide an actual query exception, and a
    successful empty query is only verified when its source contract and
    horizon also reconcile.
    """
    if not (selected_node_id or selected_edge_id):
        return [], []

    relation = ""
    kind = "dbt_aggregate"
    role = "enrichment" if selected_node_id else "primary"
    required_columns: list[str] = []
    horizon_column: str | None = None
    contract: dict[str, Any] | None = None

    if selected_node_id:
        relation = ADDRESS_ROLES_RELATION
        required_columns = ["address", *ADDRESS_ROLE_COLUMNS]
    elif selected_edge_id.startswith("flow:"):
        # The evidence query unions both chain tails and joins enrichment
        # relations. Their clocks are heterogeneous, so the query outcome is
        # retained while freshness remains explicitly unknown here rather than
        # inventing a single horizon.
        relation = "execution.logs ∪ execution_live.logs"
        kind = "chain"
    elif selected_edge_id.startswith("bridge:"):
        relation = "int_execution_bridges_address_flows_daily"
        required_columns = [
            "direction",
            "user_address",
            "bridge_contract",
            "token_address",
            "date",
            "symbol",
            "amount_raw_sum",
            "transfer_count",
        ]
        horizon_column = "date"
    else:
        profile_id = selected_edge_id.split(":", 1)[0] if selected_edge_id else ""
        profile = profile_by_id(profile_id) if profile_id else None
        if profile is not None:
            relation = str(profile.evidence_model or profile.model_name)
            required_columns = [
                *physical_columns_from_expression(
                    str(profile.evidence_source_column or profile.source_column)
                ),
                *physical_columns_from_expression(
                    str(profile.evidence_target_column or profile.target_column)
                ),
            ]
            if profile.time_column:
                required_columns.append(str(profile.time_column))
                horizon_column = str(profile.time_column)

    if relation and required_columns:
        contract = validate_source_contract(
            ch,
            relation,
            required_columns,
            probe_horizon=True,
            horizon_column=horizon_column,
        )

    normalized_relation = relation.replace("`", "")
    if normalized_relation and "." not in normalized_relation and kind == "dbt_aggregate":
        normalized_relation = f"dbt.{normalized_relation}"
    if not normalized_relation:
        normalized_relation = "graph focus evidence resolver"

    failures: list[str] = []
    if query_status is None or not query_status.succeeded:
        failures.append(
            "focus evidence query failed: "
            + str(
                query_status.error
                if query_status is not None and query_status.error
                else "query outcome unavailable"
            )
        )
    if contract is not None and not contract.get("ok"):
        failures.append(
            "focus evidence source contract failed: "
            + str(contract.get("error") or "source contract unavailable")
        )

    horizon = contract.get("horizon") if contract is not None else None
    fetched_at = (
        contract.get("freshness_checked_at") or contract.get("checked_at")
        if contract is not None
        else None
    )
    source = source_record(
        kind=kind,
        name=normalized_relation,
        role=role,
        status="error" if failures else "ok",
        horizon=horizon,
        horizon_basis=(contract.get("horizon_basis") if contract is not None else None),
        fetched_at=fetched_at,
        error="; ".join(failures) if failures else None,
    )
    warnings = list(failures)
    if not failures and horizon is None:
        warnings.append(
            f"{normalized_relation}: source horizon unknown/unverified for focus evidence"
        )
    return [source], warnings

def register_ui_tools(mcp, ch: ClickHouseManager) -> dict[str, Any]:
    """Register the UI tools; returns {name: fn} for web-app dispatch."""

    @mcp.tool(
        meta={
            "ui": {"resourceUri": constants.GRAPH_EXPLORER_URI},
            "ui/resourceUri": constants.GRAPH_EXPLORER_URI,
        }
    )
    def open_graph_explorer(
        seed_node_id: str = "",
        seed_model: str = "",
        title: str = "",
        mode: str = "",
    ) -> CallToolResult:
        """Open the Graph Explorer mini app.

        Opens in ATLAS mode (browse the semantic graph catalog and sample
        profile subgraphs). If a seed address is given, opens straight into
        INVESTIGATE mode with a 1-hop subgraph and auto-detected profiles,
        unless an explicit legacy ``mode`` route selects another task surface.

        ``mode`` is also consumed by the standalone ``/app/graph_explorer``
        route. Keeping it on the open contract is load-bearing: otherwise the
        route filters ``?mode=atlas`` out while still forwarding ``?seed=``,
        and the backend publishes Investigate before the client boots.
        """
        requested_mode = str(mode or "").strip().lower()
        valid_modes = {
            "atlas",
            "investigate",
            "timeline",
            "flows",
            "transactions",
        }
        if requested_mode and requested_mode not in valid_modes:
            return mini_apps.error_call_tool_result(
                "mode must be one of: atlas, investigate, timeline, flows, transactions"
            )
        effective_title = title or constants.DEFAULT_TITLE
        view_id = mini_apps.create_view(
            constants.GRAPH_EXPLORER_APP_ID, effective_title
        )
        mini_apps.set_view_state(view_id, empty_state(effective_title))
        mini_apps.replace_view_datasets(
            view_id,
            {
                "nodes": empty_dataset("nodes", constants.NODES_COLUMNS),
                "edges": empty_dataset("edges", constants.EDGES_COLUMNS),
                "atlas_nodes": empty_dataset("atlas_nodes", constants.NODES_COLUMNS),
                "atlas_edges": empty_dataset("atlas_edges", constants.EDGES_COLUMNS),
                "atlas_preview_nodes": empty_dataset(
                    "atlas_preview_nodes", constants.NODES_COLUMNS
                ),
                "atlas_preview_edges": empty_dataset(
                    "atlas_preview_edges", constants.EDGES_COLUMNS
                ),
                "node_evidence": empty_dataset(
                    "node_evidence", constants.NODE_EVIDENCE_COLUMNS
                ),
                "edge_evidence": empty_dataset(
                    "edge_evidence", constants.EDGE_EVIDENCE_COLUMNS
                ),
                "graph_metrics": empty_dataset(
                    "graph_metrics", constants.METRICS_COLUMNS
                ),
                "timeline_nodes": empty_dataset(
                    "timeline_nodes", constants.NODES_COLUMNS
                ),
                "timeline_edges": empty_dataset(
                    "timeline_edges", constants.TIMELINE_EDGES_COLUMNS
                ),
                "timeline_narrative": empty_dataset(
                    "timeline_narrative", constants.TIMELINE_NARRATIVE_COLUMNS
                ),
                "flow_nodes": empty_dataset(
                    "flow_nodes", constants.FLOW_NODES_COLUMNS
                ),
                "flow_edges": empty_dataset(
                    "flow_edges", constants.FLOW_EDGES_COLUMNS
                ),
                "tx_nodes": empty_dataset(
                    "tx_nodes", constants.TX_LEG_NODES_COLUMNS
                ),
                "tx_legs": empty_dataset(
                    "tx_legs", constants.TX_LEG_EDGES_COLUMNS
                ),
                "tx_list": empty_dataset("tx_list", constants.TX_LIST_COLUMNS),
                "tx_raw_receipts": empty_dataset(
                    "tx_raw_receipts", constants.TX_RAW_RECEIPTS_COLUMNS
                ),
            },
        )
        if seed_node_id:
            # A seed without an explicit route remains the legacy
            # "enter investigate" intent. An explicit route is authoritative:
            # load the investigate dataset in the background, but publish the
            # requested surface (notably Atlas + its catalog drawer).
            initial_mode = requested_mode or "investigate"
            mini_apps.patch_view_state(
                view_id, {"mode": initial_mode, "mode_revision": 1}
            )
            return load_graph_explorer_seed(view_id, seed_node_id, seed_model)
        if requested_mode:
            mini_apps.patch_view_state(
                view_id, {"mode": requested_mode, "mode_revision": 1}
            )
        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = build_payload(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Graph Explorer ready with {len(payload.view_state.get('catalog') or [])} "
                f"profiles (view_id={view_id[:8]})"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": constants.GRAPH_EXPLORER_URI},
            "ui/resourceUri": constants.GRAPH_EXPLORER_URI,
        }
    )
    def load_graph_explorer_seed(
        view_id: str,
        seed_node_id: str,
        seed_model: str = "",
        relation_types: list[str] | None = None,
        hops: int = 1,
        transfer_window_days: int = 0,
        max_neighbors: int = 0,
        request_id: int = 0,
    ) -> CallToolResult:
        """Load a bounded 1-hop subgraph around seed_node_id (INVESTIGATE mode).

        When `relation_types` is empty and `seed_model` is unset, the tool
        consults `int_execution_address_roles_current` to auto-detect which
        graph profiles apply to the seed address. The legacy no-seed +
        `seed_model` form loads a profile SAMPLE into ATLAS mode instead.
        Window/neighbor limits default to the UI defaults published in
        `view_state["limits"]`.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        seed_id = _normalize_node_id(seed_node_id)
        request_id = max(0, int(request_id or 0))
        all_profiles = discover_profiles()

        # Sample mode: no seed address but a specific profile requested —
        # agent-compat path that now lands in ATLAS mode.
        is_sample_mode = not seed_id and bool(seed_model)
        if not seed_id and not seed_model and not relation_types:
            return mini_apps.error_call_tool_result(
                "Provide seed_node_id (to seed from an address) or seed_model (to sample a profile)"
            )

        window_days = max(
            1, int(transfer_window_days or constants.UI_DEFAULT_WINDOW_DAYS)
        )
        limit = max(1, int(max_neighbors or constants.UI_DEFAULT_MAX_NEIGHBORS))

        if is_sample_mode:
            profile = profile_by_id(seed_model)
            if profile is None:
                return mini_apps.error_call_tool_result(
                    f"Unknown profile: {seed_model}"
                )
            return _load_atlas_sample_impl(
                view_id,
                profiles=[seed_model],
                sample_size=limit,
                window_days=window_days,
                request_id=request_id,
            )

        roles = resolve_address_roles(ch, seed_id) if seed_id else {}
        if relation_types:
            chosen_ids = set(relation_types)
            profiles = [p for p in all_profiles if p.profile in chosen_ids]
        elif seed_model:
            profile = profile_by_id(seed_model)
            profiles = [profile] if profile else []
        else:
            role_profile_ids = profiles_for_address_roles(roles)
            profiles = [
                p for p in all_profiles if p.profile in set(role_profile_ids)
            ]
            if not profiles:
                profiles = [
                    p for p in all_profiles if p.profile == "token_transfers"
                ] or all_profiles

        selected_profiles = list(profiles)
        profiles, sources, source_failures = _relationship_sources(
            ch, selected_profiles
        )

        # Determine seed_kind early so we can use it for direction picking.
        seed_kind = seed_kind_of(roles) if roles else ""
        if not seed_kind and seed_id and profiles:
            seed_kind = profiles[0].source_kind or "address"

        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        warnings: list[str] = []
        successful: list[str] = []
        truncated = False
        for profile in profiles:
            direction = pick_direction(profile, seed_kind)
            nodes, edges, warn = fetch_profile_edges(
                ch,
                profile,
                seed_ids=[seed_id],
                direction=direction,
                window_days=window_days,
                limit=limit,
            )
            _record_relationship_query_failure(
                profile, warn, sources, source_failures
            )
            warnings.extend(warn)
            truncated = truncated or len(edges) >= limit
            if edges:
                successful.append(profile.profile)
            all_nodes.extend(nodes)
            all_edges.extend(edges)

        if seed_id and seed_id not in {n["id"] for n in all_nodes}:
            all_nodes.append(
                {
                    "id": seed_id,
                    "kind": seed_kind or "address",
                    "label": short_id(seed_id),
                    "profiles": successful,
                }
            )

        node_rows, edge_rows = merge_graph([], [], all_nodes, all_edges)
        if not edge_rows and not source_failures:
            warnings.append(
                "No edges found for this seed under "
                f"{', '.join(p.profile for p in profiles) or 'the detected profiles'} "
                f"in the last {window_days} days — try a wider time window or "
                "different profiles."
            )
        warnings.extend(source_failures)
        scope = _relationship_scope(
            kind="investigate",
            request_id=request_id,
            window_days=window_days,
            sources=sources,
            source_failures=source_failures,
            warnings=[warning for warning in warnings if warning not in source_failures],
            node_count=len(node_rows),
            edge_count=len(edge_rows),
            truncated=truncated,
            truncation_rule=f"per-profile neighbour cap {limit}",
            subjects=[seed_id] if seed_id else [],
        )
        # Per-key attach: investigate loads must NOT drop the atlas datasets.
        mini_apps.attach_dataset(
            view_id, "nodes", dataset_from_rows(constants.NODES_COLUMNS, node_rows, "nodes")
        )
        mini_apps.attach_dataset(
            view_id, "edges", dataset_from_rows(constants.EDGES_COLUMNS, edge_rows, "edges")
        )
        mini_apps.attach_dataset(
            view_id,
            "graph_metrics",
            dataset_from_rows(
                constants.METRICS_COLUMNS,
                _graph_metrics_rows(node_rows, edge_rows, len(profiles), window_days),
                "graph_metrics",
            ),
        )

        suggestions = suggested_next_hops(seed_kind or "address", all_profiles)
        node_roles_patch: dict[str, Any] = {}
        if seed_id and roles:
            node_roles_patch[seed_id] = roles
        # Data loader: writes ONLY the investigate namespace + datasets. It must
        # NOT set `mode` or `selection` — those are owned by explicit mode
        # commands (bumping mode_revision). The initial open-into-investigate
        # mode is set by open_graph_explorer; a refetch stays in whatever mode
        # the client is in.
        mini_apps.patch_view_state(
            view_id,
            {
                "investigate": {
                    "seed": {"id": seed_id, "kind": seed_kind or ""},
                    "active_profiles": [p.profile for p in selected_profiles],
                    "window_days": window_days,
                    "max_neighbors": limit,
                    "hops_used": max(1, min(int(hops or 1), constants.MAX_HOPS)),
                    # Nodes whose neighborhoods have already been fetched.
                    # Expanding the seed again advances the FRONTIER (canvas
                    # nodes not yet in this set) instead of re-querying the
                    # seed — which was a structural no-op.
                    "expanded_ids": [seed_id],
                    "scope": scope,
                },
                "dataset_scopes": {
                    "nodes": scope["scope_id"],
                    "edges": scope["scope_id"],
                    "graph_metrics": scope["scope_id"],
                },
                "node_roles": node_roles_patch,
                "suggested_next_hops": suggestions,
                "warnings": warnings,
            },
        )

        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = build_payload(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Loaded {len(edge_rows)} edges across {len(selected_profiles)} profiles "
                f"(view_id={view_id[:8]})"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": constants.GRAPH_EXPLORER_URI},
            "ui/resourceUri": constants.GRAPH_EXPLORER_URI,
        }
    )
    def expand_graph_explorer_node(
        view_id: str,
        node_id: str,
        relation_types: list[str] | None = None,
        direction: str = "both",
        hops: int = 1,
        request_id: int = 0,
    ) -> CallToolResult:
        """Expand `node_id` by `hops` hops across the active profiles.

        Capped at `MAX_HOPS` total hops per load session; reseed to explore
        further.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        state = record.view_state
        request_id = max(0, int(request_id or 0))
        investigate = dict(state.get("investigate") or {})
        current_hops = int(investigate.get("hops_used", 1) or 1)
        if current_hops >= constants.MAX_HOPS:
            return mini_apps.error_call_tool_result(
                f"Max {constants.MAX_HOPS} hops reached. Reseed the graph to explore further."
            )
        node_id = _normalize_node_id(node_id)
        if not node_id:
            return mini_apps.error_call_tool_result("node_id is required")
        if direction not in ("in", "out", "both"):
            return mini_apps.error_call_tool_result(
                "direction must be 'in', 'out', or 'both'"
            )

        chosen_ids = (
            set(relation_types)
            if relation_types
            else set(investigate.get("active_profiles") or [])
        )
        if not chosen_ids:
            chosen_ids = {p.profile for p in discover_profiles()}
        selected_profiles = [
            p for p in discover_profiles() if p.profile in chosen_ids
        ]
        profiles, sources, source_failures = _relationship_sources(
            ch, selected_profiles
        )

        window_days = int(
            investigate.get("window_days") or constants.UI_DEFAULT_WINDOW_DAYS
        )
        limit = int(
            investigate.get("max_neighbors") or constants.UI_DEFAULT_MAX_NEIGHBORS
        )

        # Resolve the kind of the node being expanded so we can auto-pick
        # direction per profile (avoids type errors on asymmetric profiles
        # when a bare-address node is expanded against profile whose target
        # is non-string).
        expand_roles = resolve_address_roles(ch, node_id)
        expand_kind = seed_kind_of(expand_roles) if expand_roles else ""
        if not expand_kind:
            # Fall back to what the node-row carries in the dataset.
            existing_rows = record.datasets.get("nodes")
            if existing_rows and existing_rows.rows:
                for row in existing_rows.rows:
                    if row and str(row[0]) == node_id:
                        expand_kind = str(row[1]) if len(row) > 1 else ""
                        break

        # Multi-hop BFS via THE unified walker (kind-partitioned batching:
        # one query per (kind group, compatible profile) per hop). Bounded by
        # the global node cap + per-hop budget.
        nodes_ds = record.datasets.get("nodes")
        edges_ds = record.datasets.get("edges")
        initial_nodes: dict[str, dict[str, Any]] = {}
        for row in (nodes_ds.rows if nodes_ds else []):
            if row and row[0]:
                initial_nodes[str(row[0])] = {
                    "id": str(row[0]),
                    "kind": str(row[1]) if len(row) > 1 else "",
                    "label": str(row[2]) if len(row) > 2 else "",
                    "profiles": list(row[3] or []) if len(row) > 3 else [],
                }
        initial_edges: dict[str, dict[str, Any]] = {}
        for row in (edges_ds.rows if edges_ds else []):
            if row and row[0]:
                initial_edges[str(row[0])] = {
                    "id": str(row[0]),
                    "source": row[1],
                    "target": row[2],
                    "profile": row[3],
                    "weight": row[4],
                    "edge_count": row[5],
                    "directed": row[6] if len(row) > 6 else True,
                }
        existing_edge_count = len(initial_edges)
        existing_node_count = len(initial_nodes)
        warnings: list[str] = list(source_failures)
        hops_to_run = max(1, int(hops or 1))

        # Frontier construction. Two intents share this tool:
        #  - Expanding a SPECIFIC node (usually selected): frontier is that
        #    node. Its kind is passed as "" (unknown) so the walker queries
        #    ALL chosen profiles — real addresses are multi-role (safe AND
        #    avatar AND transfer counterparty); collapsing them to one kind
        #    dropped most active profiles and made expansion a silent no-op.
        #  - Expanding the SEED again ("+1 hop" / frontier round): the seed's
        #    own neighborhood is already on canvas, so the frontier is every
        #    canvas node NOT yet expanded (tracked in
        #    ``investigate.expanded_ids``). These keep their dataset kinds so
        #    kind-partitioned batching stays efficient.
        expanded_ids = {
            str(x) for x in (investigate.get("expanded_ids") or []) if x
        }
        seed_id = str((investigate.get("seed") or {}).get("id") or "")
        if node_id == seed_id and node_id in expanded_ids:
            frontier = [
                (n["id"], n.get("kind", ""))
                for n in initial_nodes.values()
                if n["id"] not in expanded_ids
            ]
            if not frontier:
                frontier = [(node_id, "")]
        else:
            frontier = [(node_id, "")]

        walk = bfs_expand(
            ch,
            frontier=frontier,
            chosen_profiles=profiles,
            direction=direction,
            auto_direction=True,
            kind_partition=True,
            hops=hops_to_run,
            window_days=window_days,
            per_query_limit=limit,
            node_cap=constants.BFS_NODE_CAP,
            per_hop_budget=constants.BFS_PER_HOP_BUDGET,
            initial_nodes=initial_nodes,
            initial_edges=initial_edges,
        )
        profiles_by_id = {profile.profile: profile for profile in profiles}
        for query_warning in walk.warnings:
            failed_profile = profiles_by_id.get(query_warning.partition(":")[0])
            if failed_profile is not None:
                _record_relationship_query_failure(
                    failed_profile, [query_warning], sources, source_failures
                )
        warnings.extend(walk.warnings)
        if walk.truncated_at_hop is not None:
            # Name the limit that ACTUALLY tripped — the global node cap and
            # the per-hop budget are different knobs with different remedies.
            if len(walk.nodes) >= constants.BFS_NODE_CAP:
                warnings.append(
                    f"Reached the global {constants.BFS_NODE_CAP}-node cap at "
                    f"hop {walk.truncated_at_hop} ({len(walk.nodes)} nodes). "
                    "Narrow profiles/window to go deeper, or raise "
                    "GRAPH_EXPLORER_BFS_NODE_CAP."
                )
            else:
                warnings.append(
                    f"Hop {walk.truncated_at_hop} stopped at the per-hop "
                    f"budget of {constants.BFS_PER_HOP_BUDGET} new nodes "
                    f"({len(walk.nodes)} loaded). Expand again to continue "
                    "the remaining frontier, or raise "
                    "GRAPH_EXPLORER_BFS_PER_HOP_BUDGET."
                )
        walk_nodes = walk.nodes
        walk_edges = walk.edges

        # UX fallback: if the caller's chosen profiles couldn't emit any edges
        # from this node, retry with ALL profiles that touch the node's kind
        # (either endpoint). Prevents "click expand, nothing happens".
        gained_edges = len(walk_edges) - existing_edge_count
        # (Single-node expands only — a zero-gain frontier round means the
        # leaves genuinely have nothing new under these profiles; widening
        # just the seed again cannot help.)
        if (
            gained_edges == 0
            and expand_kind
            and len(frontier) == 1
            and not source_failures
        ):
            widen_profiles = [
                p
                for p in discover_profiles()
                if p.profile not in chosen_ids
                and expand_kind in (p.source_kind, p.target_kind)
            ]
            widen_profiles, widen_sources, widen_failures = _relationship_sources(
                ch, widen_profiles
            )
            sources.extend(widen_sources)
            source_failures.extend(widen_failures)
            warnings.extend(widen_failures)
            if widen_profiles:
                widen_walk = bfs_expand(
                    ch,
                    frontier=[(node_id, expand_kind)],
                    chosen_profiles=widen_profiles,
                    direction=direction,
                    auto_direction=True,
                    kind_partition=True,
                    hops=1,
                    window_days=window_days,
                    per_query_limit=limit,
                    node_cap=constants.BFS_NODE_CAP,
                    per_hop_budget=constants.BFS_PER_HOP_BUDGET,
                    initial_nodes=walk_nodes,
                    initial_edges=walk_edges,
                )
                widened_by_id = {
                    profile.profile: profile for profile in widen_profiles
                }
                for query_warning in widen_walk.warnings:
                    failed_profile = widened_by_id.get(
                        query_warning.partition(":")[0]
                    )
                    if failed_profile is not None:
                        _record_relationship_query_failure(
                            failed_profile,
                            [query_warning],
                            sources,
                            source_failures,
                        )
                if widen_walk.profiles_used:
                    warnings.extend(widen_walk.warnings)
                    walk_nodes = widen_walk.nodes
                    walk_edges = widen_walk.edges
                    chosen_ids |= widen_walk.profiles_used
                    warnings.append(
                        "No edges found under selected profiles — widened to "
                        f"{', '.join(sorted(chosen_ids))}."
                    )

        merged_nodes = [
            [n["id"], n["kind"], n["label"], n["profiles"]]
            for n in walk_nodes.values()
        ]
        merged_edges = [
            [
                e["id"],
                e["source"],
                e["target"],
                e["profile"],
                e["weight"],
                e["edge_count"],
                e["directed"],
            ]
            for e in walk_edges.values()
        ]

        mini_apps.attach_dataset(
            view_id,
            "nodes",
            dataset_from_rows(constants.NODES_COLUMNS, merged_nodes, "nodes"),
        )
        mini_apps.attach_dataset(
            view_id,
            "edges",
            dataset_from_rows(constants.EDGES_COLUMNS, merged_edges, "edges"),
        )
        # Refresh graph_metrics and active_profiles so the UI status bar and
        # chip group stay consistent with the real graph after expand.
        profile_ids_in_graph = sorted(
            set(str(e[3]) for e in merged_edges if len(e) > 3)
        )
        mini_apps.attach_dataset(
            view_id,
            "graph_metrics",
            dataset_from_rows(
                constants.METRICS_COLUMNS,
                _graph_metrics_rows(
                    merged_nodes, merged_edges, len(profile_ids_in_graph), window_days
                ),
                "graph_metrics",
            ),
        )
        # The caller's profile set is authoritative — do NOT union with the
        # previously-persisted actives (that resurrected locally-removed edge
        # types after every expand). chosen_ids already contains the explicit
        # relation_types (or current actives when omitted) plus any widen
        # additions, which stay and stay warned.
        merged_profiles = sorted(chosen_ids)
        gained_nodes = len(merged_nodes) - existing_node_count
        gained_edges = len(merged_edges) - existing_edge_count
        gained = gained_nodes > 0 or gained_edges > 0
        if not gained:
            warnings.append(
                f"Expanding {short_id(node_id)} found no new nodes or edges — "
                "everything reachable under the active profiles in the last "
                f"{window_days} days is already on canvas. Try a longer time "
                "window, more profiles, or a different node."
            )
        scope = _relationship_scope(
            kind="investigate",
            request_id=request_id,
            window_days=window_days,
            sources=sources,
            source_failures=source_failures,
            warnings=[warning for warning in warnings if warning not in source_failures],
            node_count=len(merged_nodes),
            edge_count=len(merged_edges),
            truncated=walk.truncated_at_hop is not None,
            truncation_rule=(
                f"global node cap {constants.BFS_NODE_CAP}; per-hop node budget "
                f"{constants.BFS_PER_HOP_BUDGET}; per-query cap {limit}"
            ),
            subjects=[node_id],
        )
        # Only a round that actually grew the graph consumes a hop. Only ids
        # whose fetches actually RAN count as expanded — a budget-truncated
        # round skips the rest of the frontier, and those leaves must stay
        # expandable so the next Expand continues instead of stranding them.
        new_expanded = sorted(expanded_ids | walk.expanded_frontier)
        mini_apps.patch_view_state(
            view_id,
            {
                "investigate": {
                    "hops_used": (
                        min(current_hops + hops_to_run, constants.MAX_HOPS)
                        if gained
                        else current_hops
                    ),
                    "active_profiles": merged_profiles,
                    "expanded_ids": new_expanded[: constants.BFS_NODE_CAP],
                    "scope": scope,
                },
                "dataset_scopes": {
                    "nodes": scope["scope_id"],
                    "edges": scope["scope_id"],
                    "graph_metrics": scope["scope_id"],
                },
                "warnings": warnings,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return mini_apps.payload_to_call_tool_result(
            build_payload(updated),
            summary_text=(
                f"Expanded {short_id(node_id)}: +{gained_nodes} node(s), "
                f"+{gained_edges} edge(s); {len(merged_nodes)} nodes / "
                f"{len(merged_edges)} edges total."
                if gained
                else (
                    f"Expand of {short_id(node_id)} found nothing new "
                    f"({len(merged_nodes)} nodes / {len(merged_edges)} edges "
                    "unchanged) — widen the window or profiles."
                )
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": constants.GRAPH_EXPLORER_URI},
            "ui/resourceUri": constants.GRAPH_EXPLORER_URI,
        }
    )
    @_serialized_focus_mutation
    def update_graph_explorer_focus(
        view_id: str,
        selected_node_id: str = "",
        selected_edge_id: str = "",
        relation_types: list[str] | None = None,
        layout: str = "",
        transfer_window_days: int = 0,
        max_neighbors: int = 0,
        semantic_status_filter: str = "",
        mode: str = "",
        request_id: int = 0,
    ) -> CallToolResult:
        """Mutate selection, controls, and mode under one focus lock.

        A mode switch may carry one target-task selection.  The mode revision,
        focus request revision, selection, and subject-stamped evidence then
        publish together; omitting a subject keeps the legacy clear-on-switch
        behavior.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        selected_node_id = _normalize_node_id(selected_node_id)
        selected_edge_id = str(selected_edge_id or "").strip()
        request_id = max(0, int(request_id or 0))
        if selected_node_id and selected_edge_id:
            return mini_apps.error_call_tool_result(
                "Select either a node or an edge, not both."
            )
        current_selection = dict(record.view_state.get("selection") or {})
        current_request_id = int(current_selection.get("request_id") or 0)
        if request_id and request_id < current_request_id:
            return mini_apps.error_call_tool_result(
                f"Stale focus request {request_id}; current request is "
                f"{current_request_id}."
            )
        # Legacy clients may omit request_id.  Once a versioned focus exists,
        # allocate the next server revision instead of regressing to zero; new
        # clients always send their own id and can match it exactly.
        effective_request_id = (
            current_request_id + 1
            if request_id == 0
            and current_request_id > 0
            and (selected_node_id or selected_edge_id)
            else request_id
        )
        patch: dict[str, Any] = {}
        if mode:
            if mode not in ("atlas", "investigate", "timeline", "flows", "transactions"):
                return mini_apps.error_call_tool_result(
                    "mode must be one of: atlas, investigate, timeline, flows, transactions"
                )
            # This is THE explicit mode command. Advancing mode_revision is what
            # authorizes the client to adopt the new mode; data loads (which
            # never bump it) can no longer flip the visible tab.
            patch["mode"] = mode
            patch["mode_revision"] = int(record.view_state.get("mode_revision", 0)) + 1
            # Invalidate every response issued before the switch, including a
            # still-running response from the previous mode.
            effective_request_id = max(request_id, current_request_id + 1)
            patch["selection"] = {
                "node_id": "",
                "edge_id": "",
                "request_id": effective_request_id,
            }
        if selected_node_id or selected_edge_id:
            patch["selection"] = {
                "node_id": selected_node_id,
                "edge_id": selected_edge_id,
                "request_id": effective_request_id,
            }
        elif request_id and not mode:
            # A versioned empty focus is an explicit canvas/background clear.
            patch["selection"] = {
                "node_id": "",
                "edge_id": "",
                "request_id": effective_request_id,
            }
        if relation_types is not None:
            patch["investigate"] = {"active_profiles": list(relation_types)}
        if layout:
            patch["layout"] = layout
        if transfer_window_days:
            patch.setdefault("investigate", {})["window_days"] = int(
                transfer_window_days
            )
        if max_neighbors:
            patch.setdefault("investigate", {})["max_neighbors"] = int(max_neighbors)
        if semantic_status_filter:
            if semantic_status_filter not in ("all", "approved", "candidate"):
                return mini_apps.error_call_tool_result(
                    "semantic_status_filter must be 'all', 'approved', or 'candidate'"
                )
            patch["semantic_status_filter"] = semantic_status_filter

        evidence_query_status: EvidenceQueryStatus | None = None
        if selected_node_id:
            # Refresh role info + cross-sector suggestions for the newly
            # selected node so the details panel stays in sync.
            roles, evidence_query_status = resolve_address_roles_with_status(
                ch, selected_node_id
            )
            node_roles = dict(record.view_state.get("node_roles") or {})
            node_roles[selected_node_id] = roles
            patch["node_roles"] = node_roles
            patch["suggested_next_hops"] = suggested_next_hops(
                seed_kind_of(roles), discover_profiles()
            )
            mini_apps.attach_dataset(
                view_id,
                "node_evidence",
                dataset_from_rows(
                    constants.NODE_EVIDENCE_COLUMNS,
                    node_evidence_rows(
                        selected_node_id,
                        roles,
                        request_id=effective_request_id,
                        subject_kind="node",
                    ),
                    "node_evidence",
                ),
            )
            if not selected_edge_id:
                # Selecting a NODE retires any edge selection. Without this the
                # stale edge rows survived (the patch loop below re-emits both
                # keys unconditionally), so the details panel showed the
                # previously selected EDGE's evidence while reporting
                # selection.edge_id == "" — evidence attributed to the wrong
                # object, which is the most dangerous failure a forensic panel
                # can have.
                mini_apps.attach_dataset(
                    view_id,
                    "edge_evidence",
                    dataset_from_rows(
                        constants.EDGE_EVIDENCE_COLUMNS, [], "edge_evidence"
                    ),
                )

        if selected_edge_id:
            edge_rows, evidence_query_status = edge_evidence_rows_with_status(
                ch,
                selected_edge_id,
                view_state=record.view_state,
                request_id=effective_request_id,
                subject_kind="edge",
            )
            mini_apps.attach_dataset(
                view_id,
                "edge_evidence",
                dataset_from_rows(
                    constants.EDGE_EVIDENCE_COLUMNS,
                    edge_rows,
                    "edge_evidence",
                ),
            )
            # Mirror of the node branch: an edge selection retires all node
            # evidence.  Both datasets ride on every focus patch, so leaving
            # this attached would attribute the prior node to the selected edge.
            mini_apps.attach_dataset(
                view_id,
                "node_evidence",
                dataset_from_rows(
                    constants.NODE_EVIDENCE_COLUMNS, [], "node_evidence"
                ),
            )

        if (
            (mode and not selected_node_id and not selected_edge_id)
            or (request_id and not mode and not selected_node_id and not selected_edge_id)
        ):
            # Mode/background clears are first-class focus operations.
            mini_apps.attach_dataset(
                view_id,
                "node_evidence",
                dataset_from_rows(
                    constants.NODE_EVIDENCE_COLUMNS, [], "node_evidence"
                ),
            )
            mini_apps.attach_dataset(
                view_id,
                "edge_evidence",
                dataset_from_rows(
                    constants.EDGE_EVIDENCE_COLUMNS, [], "edge_evidence"
                ),
            )

        if "selection" in patch:
            interim = mini_apps.get_view(view_id)
            assert interim is not None
            node_dataset = interim.datasets.get("node_evidence")
            edge_dataset = interim.datasets.get("edge_evidence")
            node_rows_count = len(node_dataset.rows) if node_dataset else 0
            edge_rows_count = len(edge_dataset.rows) if edge_dataset else 0
            focus_sources, focus_warnings = _focus_source_context(
                ch,
                selected_node_id,
                selected_edge_id,
                evidence_query_status,
            )
            has_subject = bool(selected_node_id or selected_edge_id)
            query_succeeded = bool(
                evidence_query_status and evidence_query_status.succeeded
            )
            source_succeeded = bool(focus_sources) and all(
                source.get("status") == "ok" for source in focus_sources
            )
            result_complete = bool(
                evidence_query_status and evidence_query_status.complete
            )
            exact = not has_subject or (
                query_succeeded
                and source_succeeded
                and result_complete
                and not focus_warnings
            )
            focus_status = (
                "ready"
                if exact
                else "failed"
                if has_subject and (not query_succeeded or not source_succeeded)
                else "partial"
            )
            source_horizons = [
                str(source.get("horizon"))
                for source in focus_sources
                if source.get("horizon") is not None
            ]
            evidence_row_count = node_rows_count + edge_rows_count
            focus_scope = forensic_scope(
                scope_id=new_scope_id("focus", effective_request_id),
                request_id=effective_request_id,
                status=focus_status,
                t0=None,
                t1=None,
                window_source="selected object",
                data_horizon=max(source_horizons) if source_horizons else None,
                sources=focus_sources,
                rows_returned=evidence_row_count,
                rows_total=evidence_row_count if exact else None,
                nodes_returned=node_rows_count,
                nodes_total=node_rows_count if exact else None,
                edges_returned=edge_rows_count,
                edges_total=edge_rows_count if exact else None,
                truncated=bool(
                    has_subject
                    and query_succeeded
                    and evidence_query_status
                    and not evidence_query_status.complete
                ),
                truncation_rule=(
                    "focus evidence query cap reached; total unknown"
                    if has_subject
                    and query_succeeded
                    and evidence_query_status
                    and not evidence_query_status.complete
                    else None
                ),
                residuals=(
                    "focus evidence is bounded to the selected object",
                ),
                warnings=focus_warnings,
                verification_status="verified" if exact else "unverified",
                verification_method=(
                    "subject/request stamped evidence rows from a successful "
                    "source query below its cap"
                    if exact and has_subject
                    else "atomic focus clear"
                    if exact
                    else "focus source/query reconciliation incomplete"
                ),
            )
            patch["focus_scope"] = focus_scope
            patch["dataset_scopes"] = {
                "node_evidence": focus_scope["scope_id"],
                "edge_evidence": focus_scope["scope_id"],
            }

        if not patch and not selected_node_id and not selected_edge_id:
            return mini_apps.error_call_tool_result("no fields to update")
        if patch:
            mini_apps.patch_view_state(view_id, patch)

        updated = mini_apps.get_view(view_id)
        assert updated is not None
        # Surface any evidence datasets refreshed above so the details panel
        # can render them without a full INITIAL_LOAD (which would reset the
        # graph layout/zoom). dataset_revisions ride along so the frontend
        # re-hydrates exactly the replaced keys.
        dataset_patch: dict[str, Any] = {}
        for key in ("node_evidence", "edge_evidence"):
            dataset = updated.datasets.get(key)
            if dataset is not None:
                dataset_patch[key] = mini_apps.build_dataset_descriptor(
                    key=key,
                    dataset=dataset,
                    title=constants.DATASET_TITLES.get(key, key),
                    scope_id=str((patch.get("dataset_scopes") or {}).get(key) or "") or None,
                    provenance=dict(patch.get("focus_scope") or {}),
                )
        view_state_patch = {
            **patch,
            "dataset_revisions": dict(updated.dataset_revisions),
        }
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=updated.view_id,
            app_id=constants.GRAPH_EXPLORER_APP_ID,
            title=updated.title,
            status="ready",
            patch={"view_state": view_state_patch, "datasets": dataset_patch}
            if dataset_patch
            else {"view_state": view_state_patch},
        )
        return mini_apps.payload_to_call_tool_result(
            payload, summary_text="Graph Explorer focus updated."
        )

    # ------------------------------------------------------------------
    # App-only tools (frontend-driven; hidden from the model tool list)
    # ------------------------------------------------------------------

    def _load_atlas_sample_impl(
        view_id: str,
        *,
        profiles: list[str],
        sample_size: int = 0,
        window_days: int = 0,
        request_id: int = 0,
    ) -> CallToolResult:
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        requested = [str(p) for p in (profiles or []) if str(p)]
        profile_objs = []
        unknown: list[str] = []
        for pid in requested:
            prof = profile_by_id(pid)
            if prof is None:
                unknown.append(pid)
            else:
                profile_objs.append(prof)
        if unknown:
            return mini_apps.error_call_tool_result(
                f"Unknown profile(s): {', '.join(unknown)}"
            )
        per_profile = max(1, int(sample_size or constants.DEFAULT_ATLAS_SAMPLE))
        win = max(1, int(window_days or constants.UI_DEFAULT_WINDOW_DAYS))
        request_id = max(0, int(request_id or 0))
        selected_profile_objs = list(profile_objs)
        profile_objs, sources, source_failures = _relationship_sources(
            ch, selected_profile_objs
        )

        # REPLACE semantics: the atlas datasets are rebuilt from scratch as
        # the union over exactly the requested profiles — deselecting a
        # profile and re-requesting leaves no stale edges. Empty profile
        # list = clear the atlas canvas.
        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        warnings: list[str] = []
        truncated = False
        for prof in profile_objs:
            nodes, edges, warn = fetch_profile_edges(
                ch, prof, seed_ids=None, window_days=win, limit=per_profile
            )
            _record_relationship_query_failure(
                prof, warn, sources, source_failures
            )
            warnings.extend(warn)
            truncated = truncated or len(edges) >= per_profile
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        node_rows, edge_rows = merge_graph([], [], all_nodes, all_edges)
        warnings.extend(source_failures)
        scope = _relationship_scope(
            kind="atlas",
            request_id=request_id,
            window_days=win,
            sources=sources,
            source_failures=source_failures,
            warnings=[warning for warning in warnings if warning not in source_failures],
            node_count=len(node_rows),
            edge_count=len(edge_rows),
            truncated=truncated,
            truncation_rule=f"top-weight sample cap {per_profile} per relationship",
        )

        mini_apps.attach_dataset(
            view_id,
            "atlas_nodes",
            dataset_from_rows(constants.NODES_COLUMNS, node_rows, "atlas_nodes"),
        )
        mini_apps.attach_dataset(
            view_id,
            "atlas_edges",
            dataset_from_rows(constants.EDGES_COLUMNS, edge_rows, "atlas_edges"),
        )
        # Data loader: atlas namespace + datasets only. mode/selection are
        # owned by explicit mode commands (Atlas is already the client's mode
        # when it samples), so this must not write them.
        mini_apps.patch_view_state(
            view_id,
            {
                "atlas": {
                    "selected_profiles": requested,
                    "sample_size": per_profile,
                    "window_days": win,
                    "scope": scope,
                },
                "dataset_scopes": {
                    "atlas_nodes": scope["scope_id"],
                    "atlas_edges": scope["scope_id"],
                },
                "warnings": warnings,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return mini_apps.payload_to_call_tool_result(
            build_payload(updated),
            summary_text=(
                f"Atlas sample: {len(edge_rows)} edges across "
                f"{len(profile_objs)} profile(s)."
            ),
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_graph_atlas_preview(
        view_id: str,
        profile: str,
        sample_size: int = 25,
        window_days: int = 0,
        request_id: int = 0,
    ) -> CallToolResult:
        """Load one inspect-only relationship sample without applying it."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        profile_obj = profile_by_id(str(profile or ""))
        if profile_obj is None:
            return mini_apps.error_call_tool_result(
                f"Unknown profile: {profile}"
            )
        limit = max(1, min(int(sample_size or 25), constants.DEFAULT_ATLAS_SAMPLE))
        win = max(1, int(window_days or constants.UI_DEFAULT_WINDOW_DAYS))
        request_id = max(0, int(request_id or 0))
        usable, sources, source_failures = _relationship_sources(
            ch, [profile_obj]
        )
        warnings: list[str] = []
        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        truncated = False
        for candidate in usable:
            nodes, edges, query_warnings = fetch_profile_edges(
                ch,
                candidate,
                seed_ids=None,
                window_days=win,
                limit=limit,
            )
            _record_relationship_query_failure(
                candidate, query_warnings, sources, source_failures
            )
            warnings.extend(query_warnings)
            truncated = truncated or len(edges) >= limit
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        node_rows, edge_rows = merge_graph([], [], all_nodes, all_edges)
        scope = _relationship_scope(
            kind="atlas_preview",
            request_id=request_id,
            window_days=win,
            sources=sources,
            source_failures=source_failures,
            warnings=warnings,
            node_count=len(node_rows),
            edge_count=len(edge_rows),
            truncated=truncated,
            truncation_rule=f"top-weight preview cap {limit}",
        )
        mini_apps.attach_dataset(
            view_id,
            "atlas_preview_nodes",
            dataset_from_rows(
                constants.NODES_COLUMNS, node_rows, "atlas_preview_nodes"
            ),
        )
        mini_apps.attach_dataset(
            view_id,
            "atlas_preview_edges",
            dataset_from_rows(
                constants.EDGES_COLUMNS, edge_rows, "atlas_preview_edges"
            ),
        )
        mini_apps.patch_view_state(
            view_id,
            {
                "atlas_preview": {
                    "profile": profile_obj.profile,
                    "sample_size": limit,
                    "window_days": win,
                    "scope": scope,
                    "warnings": [*warnings, *source_failures],
                },
                "dataset_scopes": {
                    "atlas_preview_nodes": scope["scope_id"],
                    "atlas_preview_edges": scope["scope_id"],
                },
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return mini_apps.payload_to_call_tool_result(
            build_payload(updated),
            summary_text=(
                f"Relationship preview {profile_obj.profile}: "
                f"{len(edge_rows)} edge(s); applied Atlas graph unchanged."
            ),
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_graph_atlas_sample(
        view_id: str,
        profiles: list[str],
        sample_size: int = 0,
        window_days: int = 0,
        request_id: int = 0,
    ) -> CallToolResult:
        """[App-only] Load top-weight sample subgraphs for the Atlas mode.

        Hidden from the model-facing tool list. REPLACE semantics: rebuilds
        ``atlas_nodes``/``atlas_edges`` as the union over exactly the
        requested profiles (``sample_size`` is PER PROFILE), so deselecting
        a profile leaves no stale edges.
        """
        return _load_atlas_sample_impl(
            view_id,
            profiles=profiles,
            sample_size=sample_size,
            window_days=window_days,
            request_id=request_id,
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    @_serialized_focus_mutation
    def set_graph_explorer_view(
        view_id: str, patch: dict[str, Any]
    ) -> CallToolResult:
        """[App-only] Bulk view-state sync target for the frontend reducer.

        Hidden from the model-facing tool list. Accepts only the v2 schema
        keys (mode/layout/semantic_status_filter + atlas.* / investigate.*);
        unknown keys are rejected. Selection changes go through
        ``update_graph_explorer_focus`` (it also refreshes evidence).
        """
        record = mini_apps.get_view(view_id)
        if record is None or record.app_id != constants.GRAPH_EXPLORER_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired Graph Explorer view_id: {view_id}"
            )
        if not isinstance(patch, dict) or not patch:
            return mini_apps.error_call_tool_result("patch must be a non-empty object")
        for key, value in patch.items():
            allowed = _VIEW_PATCH_SCHEMA.get(key)
            if allowed is None:
                return mini_apps.error_call_tool_result(
                    f"Unknown view-state key '{key}'. Allowed: "
                    f"{sorted(_VIEW_PATCH_SCHEMA)}"
                )
            if isinstance(allowed, set):
                if not isinstance(value, dict):
                    return mini_apps.error_call_tool_result(
                        f"'{key}' must be an object"
                    )
                bad = set(value) - allowed
                if bad:
                    return mini_apps.error_call_tool_result(
                        f"Unknown key(s) under '{key}': {sorted(bad)}"
                    )
        if patch.get("mode") not in (
            None, "atlas", "investigate", "timeline", "flows", "transactions"
        ):
            return mini_apps.error_call_tool_result(
                "mode must be one of: atlas, investigate, timeline, flows, transactions"
            )
        if "mode" in patch:
            # An explicit mode command: bump mode_revision (authorizes client
            # adoption) and clear selection (matches update_graph_explorer_focus).
            current_request_id = int(
                ((record.view_state.get("selection") or {}).get("request_id")) or 0
            )
            patch = {
                **patch,
                "mode_revision": int(record.view_state.get("mode_revision", 0)) + 1,
                "selection": {
                    "node_id": "",
                    "edge_id": "",
                    "request_id": current_request_id + 1,
                },
            }
            mini_apps.attach_dataset(
                view_id,
                "node_evidence",
                dataset_from_rows(
                    constants.NODE_EVIDENCE_COLUMNS, [], "node_evidence"
                ),
            )
            clear_scope = forensic_scope(
                scope_id=new_scope_id("focus", current_request_id + 1),
                request_id=current_request_id + 1,
                status="ready",
                t0=None,
                t1=None,
                window_source="task switch",
                data_horizon=None,
                sources=[],
                rows_returned=0,
                rows_total=0,
                nodes_returned=0,
                nodes_total=0,
                edges_returned=0,
                edges_total=0,
                residuals=("task switches intentionally clear focus evidence",),
                verification_status="verified",
                verification_method="atomic focus clear",
            )
            patch["focus_scope"] = clear_scope
            patch["dataset_scopes"] = {
                "node_evidence": clear_scope["scope_id"],
                "edge_evidence": clear_scope["scope_id"],
            }
            mini_apps.attach_dataset(
                view_id,
                "edge_evidence",
                dataset_from_rows(
                    constants.EDGE_EVIDENCE_COLUMNS, [], "edge_evidence"
                ),
            )
        mini_apps.patch_view_state(view_id, patch)
        dataset_patch: dict[str, Any] = {}
        if "mode" in patch:
            updated = mini_apps.get_view(view_id)
            assert updated is not None
            for key in ("node_evidence", "edge_evidence"):
                dataset = updated.datasets.get(key)
                if dataset is not None:
                    dataset_patch[key] = mini_apps.build_dataset_descriptor(
                        key=key,
                        dataset=dataset,
                        title=constants.DATASET_TITLES.get(key, key),
                        scope_id=str((patch.get("dataset_scopes") or {}).get(key) or "") or None,
                        provenance=dict(patch.get("focus_scope") or {}),
                    )
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=constants.GRAPH_EXPLORER_APP_ID,
            title=record.title,
            status="ready",
            patch={"view_state": patch, "datasets": dataset_patch}
            if dataset_patch
            else {"view_state": patch},
        )
        return mini_apps.payload_to_call_tool_result(
            payload, summary_text="Graph Explorer view updated."
        )

    mini_apps.mark_app_only("load_graph_atlas_sample")
    mini_apps.mark_app_only("load_graph_atlas_preview")
    mini_apps.mark_app_only("set_graph_explorer_view")

    return {
        "open_graph_explorer": open_graph_explorer,
        "load_graph_explorer_seed": load_graph_explorer_seed,
        "expand_graph_explorer_node": expand_graph_explorer_node,
        "update_graph_explorer_focus": update_graph_explorer_focus,
        "load_graph_atlas_sample": load_graph_atlas_sample,
        "load_graph_atlas_preview": load_graph_atlas_preview,
        "set_graph_explorer_view": set_graph_explorer_view,
    }
