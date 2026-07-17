"""Graph Explorer UI tools — the 4 agent-facing tools + 2 app-only tools.

view_state v2: per-mode namespaces (``atlas`` / ``investigate``) sharing one
canvas. Dataset writes are PER KEY (``attach_dataset``) — an investigate load
must never drop the ``atlas_*`` datasets and vice versa. Limits are published
via ``view_state["limits"]``; all limit reads go through ``constants.<NAME>``
attribute access so tests can monkeypatch them.
"""

from __future__ import annotations

import logging
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
    edge_evidence_rows,
    fetch_profile_edges,
    node_evidence_rows,
    pick_direction,
    resolve_address_roles,
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

# Keys set_graph_explorer_view may patch (nested per namespace). Selection
# changes go through update_graph_explorer_focus instead — it also refreshes
# evidence datasets.
_VIEW_PATCH_SCHEMA: dict[str, set[str] | type] = {
    "mode": str,
    "layout": str,
    "semantic_status_filter": str,
    "atlas": {"selected_profiles", "sample_size", "window_days"},
    "investigate": {"active_profiles", "window_days", "max_neighbors"},
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


def register_ui_tools(mcp, ch: ClickHouseManager) -> dict[str, Any]:
    """Register the UI tools; returns {name: fn} for web-app dispatch."""

    @mcp.tool(
        meta={
            "ui": {"resourceUri": constants.GRAPH_EXPLORER_URI},
            "ui/resourceUri": constants.GRAPH_EXPLORER_URI,
        }
    )
    def open_graph_explorer(
        seed_node_id: str = "", seed_model: str = "", title: str = ""
    ) -> CallToolResult:
        """Open the Graph Explorer mini app.

        Opens in ATLAS mode (browse the semantic graph catalog and sample
        profile subgraphs). If a seed address is given, opens straight into
        INVESTIGATE mode with a 1-hop subgraph and auto-detected profiles.
        """
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
                "node_evidence": empty_dataset(
                    "node_evidence", constants.NODE_EVIDENCE_COLUMNS
                ),
                "edge_evidence": empty_dataset(
                    "edge_evidence", constants.EDGE_EVIDENCE_COLUMNS
                ),
                "graph_metrics": empty_dataset(
                    "graph_metrics", constants.METRICS_COLUMNS
                ),
            },
        )
        if seed_node_id:
            return load_graph_explorer_seed(view_id, seed_node_id, seed_model)
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

        # Determine seed_kind early so we can use it for direction picking.
        seed_kind = seed_kind_of(roles) if roles else ""
        if not seed_kind and seed_id and profiles:
            seed_kind = profiles[0].source_kind or "address"

        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        warnings: list[str] = []
        successful: list[str] = []
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
                    "label": short_id(seed_id),
                    "profiles": successful,
                }
            )

        node_rows, edge_rows = merge_graph([], [], all_nodes, all_edges)
        if not edge_rows:
            warnings.append(
                "No edges found for this seed under "
                f"{', '.join(p.profile for p in profiles) or 'the detected profiles'} "
                f"in the last {window_days} days — try a wider time window or "
                "different profiles."
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
        mini_apps.patch_view_state(
            view_id,
            {
                "mode": "investigate",
                "investigate": {
                    "seed": {"id": seed_id, "kind": seed_kind or ""},
                    "active_profiles": [p.profile for p in profiles],
                    "window_days": window_days,
                    "max_neighbors": limit,
                    "hops_used": max(1, min(int(hops or 1), constants.MAX_HOPS)),
                    # Nodes whose neighborhoods have already been fetched.
                    # Expanding the seed again advances the FRONTIER (canvas
                    # nodes not yet in this set) instead of re-querying the
                    # seed — which was a structural no-op.
                    "expanded_ids": [seed_id],
                },
                "selection": {"node_id": "", "edge_id": ""},
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
                f"Loaded {len(edge_rows)} edges across {len(profiles)} profiles "
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
        profiles = [p for p in discover_profiles() if p.profile in chosen_ids]

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
        warnings: list[str] = []
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
        if gained_edges == 0 and expand_kind and len(frontier) == 1:
            widen_profiles = [
                p
                for p in discover_profiles()
                if p.profile not in chosen_ids
                and expand_kind in (p.source_kind, p.target_kind)
            ]
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
        prev_selected = set(investigate.get("active_profiles") or [])
        merged_profiles = sorted(prev_selected | chosen_ids)
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
    ) -> CallToolResult:
        """Mutate view state only (selection, filters, layout, mode). No
        refetch. Switching ``mode`` clears the selection — evidence panels
        are mode-specific."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        selected_node_id = _normalize_node_id(selected_node_id)
        patch: dict[str, Any] = {}
        if mode:
            if mode not in ("atlas", "investigate"):
                return mini_apps.error_call_tool_result(
                    "mode must be 'atlas' or 'investigate'"
                )
            patch["mode"] = mode
            patch["selection"] = {"node_id": "", "edge_id": ""}
        if selected_node_id or selected_edge_id:
            patch["selection"] = {
                "node_id": selected_node_id,
                "edge_id": selected_edge_id,
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

        if selected_node_id:
            # Refresh role info + cross-sector suggestions for the newly
            # selected node so the details panel stays in sync.
            roles = resolve_address_roles(ch, selected_node_id)
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
                    node_evidence_rows(selected_node_id, roles),
                    "node_evidence",
                ),
            )

        if selected_edge_id:
            mini_apps.attach_dataset(
                view_id,
                "edge_evidence",
                dataset_from_rows(
                    constants.EDGE_EVIDENCE_COLUMNS,
                    edge_evidence_rows(ch, selected_edge_id),
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
        # graph layout/zoom). dataset_revisions ride along so the frontend
        # re-hydrates exactly the replaced keys.
        dataset_patch: dict[str, Any] = {}
        for key in ("node_evidence", "edge_evidence"):
            dataset = updated.datasets.get(key)
            if dataset is not None:
                dataset_patch[key] = mini_apps.build_dataset_descriptor(
                    key=key, dataset=dataset, title=constants.DATASET_TITLES.get(key, key)
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

        # REPLACE semantics: the atlas datasets are rebuilt from scratch as
        # the union over exactly the requested profiles — deselecting a
        # profile and re-requesting leaves no stale edges. Empty profile
        # list = clear the atlas canvas.
        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        warnings: list[str] = []
        for prof in profile_objs:
            nodes, edges, warn = fetch_profile_edges(
                ch, prof, seed_ids=None, window_days=win, limit=per_profile
            )
            warnings.extend(warn)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        node_rows, edge_rows = merge_graph([], [], all_nodes, all_edges)

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
        mini_apps.patch_view_state(
            view_id,
            {
                "mode": "atlas",
                "atlas": {
                    "selected_profiles": requested,
                    "sample_size": per_profile,
                    "window_days": win,
                },
                "selection": {"node_id": "", "edge_id": ""},
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
    def load_graph_atlas_sample(
        view_id: str,
        profiles: list[str],
        sample_size: int = 0,
        window_days: int = 0,
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
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
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
        if patch.get("mode") not in (None, "atlas", "investigate"):
            return mini_apps.error_call_tool_result(
                "mode must be 'atlas' or 'investigate'"
            )
        if "mode" in patch:
            # Mode switch clears selection (matches update_graph_explorer_focus).
            patch = {**patch, "selection": {"node_id": "", "edge_id": ""}}
        mini_apps.patch_view_state(view_id, patch)
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=constants.GRAPH_EXPLORER_APP_ID,
            title=record.title,
            status="ready",
            patch={"view_state": patch},
        )
        return mini_apps.payload_to_call_tool_result(
            payload, summary_text="Graph Explorer view updated."
        )

    mini_apps.mark_app_only("load_graph_atlas_sample")
    mini_apps.mark_app_only("set_graph_explorer_view")

    return {
        "open_graph_explorer": open_graph_explorer,
        "load_graph_explorer_seed": load_graph_explorer_seed,
        "expand_graph_explorer_node": expand_graph_explorer_node,
        "update_graph_explorer_focus": update_graph_explorer_focus,
        "load_graph_atlas_sample": load_graph_atlas_sample,
        "set_graph_explorer_view": set_graph_explorer_view,
    }
