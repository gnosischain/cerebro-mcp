"""Graph-native, non-UI tools (WS5/6/7).

Plain data tools (no resourceUri): an agent can search the catalog, walk the
neighborhood, and measure flow without driving the mini-app. PUBLIC CONTRACT
— signatures, defaults, and output shapes are pinned by eval fixtures and
latency benchmarks; behavior-preserving move only.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.semantic import graph_telemetry
from cerebro_mcp.semantic.bm25 import BM25Doc, BM25Index
from cerebro_mcp.semantic.graph_profiles import (
    build_node_flow_sql,
    current_snapshot,
    discover_profiles,
    profile_by_id,
    profiles_for_kind,
)
from cerebro_mcp.tools.visualization import mini_apps

from . import constants
from .fetch import search_doc_hit
from .state import short_id
from .traverse import bfs_expand

logger = logging.getLogger(__name__)


def register_data_tools(mcp, ch: ClickHouseManager) -> dict[str, Any]:
    """Register the 4 graph-native tools; returns {name: fn}."""

    @mcp.tool()
    def search_graph_catalog(
        query: str = "",
        node_kind: str = "",
        min_quality_tier: str = "approved",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the knowledge-graph catalog (node types, edge profiles).

        BM25 over the catalog's search documents, gated by quality tier
        (``approved`` > ``candidate`` > ``docs_only``; ``all`` disables the gate)
        and optionally filtered to a single ``node_kind``. An empty query returns
        a browsable, tier-filtered listing. Falls back to live-discovered profiles
        when no catalog is published.
        """
        _t0 = time.perf_counter()
        snap = current_snapshot()
        if snap is None:
            return {
                "query": query,
                "results": [],
                "count": 0,
                "warnings": ["semantic snapshot unavailable"],
            }
        warnings: list[str] = []
        docs = list(getattr(snap, "graph_search_documents", ()) or [])
        from_fallback = not getattr(snap, "graph_catalog_hash", "")

        # node_kind restricts the RESULT set, but we rank over the full corpus so
        # BM25 IDF stays stable (ranking over a tiny filtered set drives the IDF
        # of a query term present in every doc negative -> everything dropped).
        allowed_ids: set[str] | None = None
        if node_kind:
            kind_profiles = {p.profile for p in profiles_for_kind(node_kind)}
            if not kind_profiles:
                known = sorted((getattr(snap, "kind_to_profiles", {}) or {}).keys())
                warnings.append(
                    f"no graph profiles for node_kind '{node_kind}'"
                    + (f"; known kinds: {known}" if known else "")
                )
            allowed_ids = {
                d["id"]
                for d in docs
                if (
                    d.get("type") == "edge_type"
                    and d.get("payload_ref") in kind_profiles
                )
                or (d.get("type") == "node_type" and d.get("title") == node_kind)
            }

        min_ord = (
            None
            if min_quality_tier == "all"
            else constants.TIER_ORDINAL.get(min_quality_tier, 3)
        )
        hidden = 0
        gated: list[dict[str, Any]] = []
        for d in docs:
            # The tier gate applies only to docs that carry a real quality tier
            # (edge profiles / metrics). Structural node-type docs always pass.
            if min_ord is None or d.get("type") == "node_type":
                gated.append(d)
            elif constants.TIER_ORDINAL.get(d.get("quality_tier", ""), 0) >= min_ord:
                gated.append(d)
            else:
                hidden += 1

        def _in_scope(doc_id: str) -> bool:
            return allowed_ids is None or doc_id in allowed_ids

        q = (query or "").strip()
        if not q:
            ordered = [
                d
                for d in sorted(
                    gated, key=lambda d: (d.get("type", ""), d.get("title", ""))
                )
                if _in_scope(d["id"])
            ][: max(0, limit)]
            results = [search_doc_hit(d, None) for d in ordered]
            graph_telemetry.record(
                "search_graph_catalog",
                node_kind=node_kind,
                query=query,
                latency_ms=(time.perf_counter() - _t0) * 1000.0,
            )
            return {
                "query": query,
                "results": results,
                "count": len(results),
                "hidden_by_tier_count": hidden,
                "results_from_fallback": from_fallback,
                "browse": True,
                "warnings": warnings,
            }

        index = BM25Index(
            BM25Doc(model_name=d["id"], text=d.get("body", "")) for d in gated
        )
        by_id = {d["id"]: d for d in gated}
        ranked = index.search(q, top_k=max(len(gated), limit) or 1)
        results = [
            search_doc_hit(by_id[doc_id], score)
            for doc_id, score in ranked
            if doc_id in by_id and _in_scope(doc_id)
        ][:limit]
        graph_telemetry.record(
            "search_graph_catalog",
            node_kind=node_kind,
            query=query,
            latency_ms=(time.perf_counter() - _t0) * 1000.0,
        )
        return {
            "query": query,
            "results": results,
            "count": len(results),
            "hidden_by_tier_count": hidden,
            "results_from_fallback": from_fallback,
            "warnings": warnings,
        }

    @mcp.tool()
    def explore_neighborhood(
        seed_ids: list[str],
        profiles: list[str] | None = None,
        direction: str = "both",
        hops: int = 1,
        window_days: int = constants.DEFAULT_WINDOW_DAYS,
        max_nodes: int = constants.DEFAULT_MAX_NEIGHBORS,
    ) -> dict[str, Any]:
        """Bounded multi-hop neighborhood traversal around seed node ids.

        Walks up to ``hops`` hops over the selected graph profiles (all profiles
        when ``profiles`` is omitted), capping the result at ``max_nodes`` nodes
        (sets ``truncated`` when the cap is hit). Undirected profiles collapse
        reciprocal edges onto one canonical edge with summed weight (Q6). A
        per-profile query failure degrades to a warning, not an error.
        """
        _t0 = time.perf_counter()
        snap = current_snapshot()
        if snap is None:
            return {
                "seed_ids": [],
                "nodes": [],
                "edges": [],
                "warnings": ["semantic snapshot unavailable"],
            }
        seeds = [str(s) for s in (seed_ids or []) if str(s)]
        if not seeds:
            return {
                "seed_ids": [],
                "nodes": [],
                "edges": [],
                "warnings": ["no seed_ids provided"],
            }
        hops = max(1, min(int(hops), constants.MAX_HOPS))
        # Upper bound too, not just a floor. `node_cap` limits how much data
        # comes back but not how long the walk takes — each hop issues a query
        # per profile group — so an unbounded cap combined with MAX_HOPS made
        # this an arbitrarily long tool call. Paired with the wall budget passed
        # to bfs_expand below.
        max_nodes = max(1, min(int(max_nodes), constants.MAX_NEIGHBORS_CEILING))
        if profiles:
            profile_objs = [
                p for p in (profile_by_id(pid) for pid in profiles) if p is not None
            ]
        else:
            profile_objs = list(discover_profiles())
        warnings: list[str] = []
        if not profile_objs:
            return {
                "seed_ids": seeds,
                "nodes": [],
                "edges": [],
                "warnings": ["no graph profiles selected"],
            }

        # The unified BFS in simple-cap mode: one batched query per (profile,
        # whole frontier) per hop, caller direction passed through — pinned by
        # the characterization goldens.
        walk = bfs_expand(
            ch,
            frontier=[(s, "") for s in seeds],
            chosen_profiles=profile_objs,
            direction=direction,
            auto_direction=False,
            kind_partition=False,
            hops=hops,
            window_days=window_days,
            per_query_limit=max_nodes,
            node_cap=max_nodes,
            per_hop_budget=None,
            initial_nodes={
                s: {"id": s, "kind": "", "label": short_id(s), "profiles": []}
                for s in seeds
            },
            visited=set(seeds),
            wall_budget_seconds=constants.NEIGHBORHOOD_WALL_BUDGET_SECONDS,
        )
        warnings.extend(walk.warnings)
        graph_telemetry.record(
            "explore_neighborhood",
            profiles=sorted(walk.profiles_used),
            latency_ms=(time.perf_counter() - _t0) * 1000.0,
        )
        return {
            "seed_ids": seeds,
            "nodes": list(walk.nodes.values()),
            "edges": list(walk.edges.values()),
            "profiles_used": sorted(walk.profiles_used),
            "hops_requested": hops,
            "hops_completed": walk.hops_completed,
            "node_count": len(walk.nodes),
            "edge_count": len(walk.edges),
            "truncated": walk.truncated,
            "max_nodes": max_nodes,
            "warnings": warnings,
        }

    @mcp.tool()
    def calculate_flow_efficiency(
        profile: str,
        node_ids: list[str],
        window_days: int = constants.DEFAULT_WINDOW_DAYS,
        exclude_self_loops: bool = True,
    ) -> dict[str, Any]:
        """Per-node weighted-flow efficiency = outflow / inflow for a profile.

        Self-loops are excluded by default so circular flow isn't counted as
        exiting flow. A node with zero inflow returns ``efficiency=null`` and
        ``status="no_inflow"`` (never a divide-by-zero). Uses the profile's
        ``weight_column`` when present, else edge counts (``weight_unit`` reports
        which).
        """
        _t0 = time.perf_counter()
        prof = profile_by_id(profile)
        if prof is None:
            return {
                "profile": profile,
                "nodes": [],
                "warnings": [f"unknown profile '{profile}'"],
            }
        nids = [str(n) for n in (node_ids or []) if str(n)]
        if not nids:
            return {
                "profile": profile,
                "nodes": [],
                "warnings": ["no node_ids provided"],
            }
        warnings: list[str] = []
        weight_unit = prof.weight_column or "edge_count"
        if not prof.weight_column:
            warnings.append("profile has no weight_column; flow uses edge counts")
        if not prof.directed:
            warnings.append(
                "profile is undirected; efficiency assumes directed flow and may double-count"
            )
        sql, params = build_node_flow_sql(
            prof,
            node_ids=nids,
            window_days=window_days,
            exclude_self_loops=exclude_self_loops,
        )
        try:
            result = mini_apps.run_structured_query(
                ch,
                sql,
                database="dbt",
                parameters=params,
                requested_max_rows=len(nids) + 16,
            )
        except Exception as exc:
            return {
                "profile": profile,
                "nodes": [],
                "warnings": warnings + [f"query failed: {exc}"],
                "sql": sql,
            }
        flows: dict[str, tuple[float, float]] = {}
        for row in result.rows:
            if not row:
                continue
            node_id = str(row[0])
            outflow = float(row[1]) if len(row) > 1 and row[1] is not None else 0.0
            inflow = float(row[2]) if len(row) > 2 and row[2] is not None else 0.0
            flows[node_id] = (outflow, inflow)
        nodes = []
        for nid in nids:
            outflow, inflow = flows.get(nid, (0.0, 0.0))
            if inflow > 0:
                efficiency: float | None = round(outflow / inflow, 6)
                status = "ok"
            else:
                efficiency = None
                status = "no_inflow"
            nodes.append(
                {
                    "node_id": nid,
                    "inflow": inflow,
                    "outflow": outflow,
                    "efficiency": efficiency,
                    "status": status,
                }
            )
        graph_telemetry.record(
            "calculate_flow_efficiency",
            profiles=[profile],
            latency_ms=(time.perf_counter() - _t0) * 1000.0,
        )
        return {
            "profile": profile,
            "weight_unit": weight_unit,
            "window_days": window_days,
            "exclude_self_loops": exclude_self_loops,
            "directed": prof.directed,
            "nodes": nodes,
            "warnings": warnings,
            "sql": sql,
        }

    @mcp.tool()
    def graph_usage_analytics(limit: int = 20) -> dict[str, Any]:
        """Adoption analytics for the graph tools (WS12).

        Most-explored profiles, popular search queries, per-tool call counts and
        latency percentiles, and coverage gaps (registered node kinds that have
        never been explored). Sourced from in-process telemetry recorded by the
        graph tools this session.
        """
        snap = current_snapshot()
        coverage_kinds = (
            set((getattr(snap, "kind_to_profiles", {}) or {}).keys()) if snap else set()
        )
        return graph_telemetry.snapshot(limit=limit, coverage_kinds=coverage_kinds)

    return {
        "search_graph_catalog": search_graph_catalog,
        "explore_neighborhood": explore_neighborhood,
        "calculate_flow_efficiency": calculate_flow_efficiency,
        "graph_usage_analytics": graph_usage_analytics,
    }
