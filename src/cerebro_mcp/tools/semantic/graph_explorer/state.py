"""Graph Explorer view-state and dataset builders (pure, no ClickHouse)."""

from __future__ import annotations

from typing import Any

from cerebro_mcp.models.mini_app import DatasetStats, MiniAppPayload
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.semantic.graph_profiles import GraphProfile, discover_profiles
from cerebro_mcp.tools.visualization import mini_apps

from . import constants
from .forensics import canonical_row_hash


def empty_dataset(label: str, columns: list[str], sql: str = "") -> CachedDataset:
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


def dataset_from_rows(
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


def short_id(addr: str) -> str:
    if not addr:
        return ""
    return addr if len(addr) <= 14 else f"{addr[:6]}…{addr[-4:]}"


def profile_card(profile: GraphProfile) -> dict[str, Any]:
    coverage_note = profile.coverage_note
    if profile.profile == "gpay_ownership" and not coverage_note:
        coverage_note = (
            "Current deployed incremental ownership relation may retain an owner "
            "removed from an older untouched partition; corroborate ownership "
            "before treating it as current."
        )
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
        "weight_unit": profile.weight_unit,
        "sector": profile.sector,
        "freshness_sla": profile.freshness_sla,
        "coverage_note": coverage_note,
        "temporal_semantics": profile.relationship_time,
        "temporal_shape": profile.temporal_shape,
    }


def limits_block() -> dict[str, Any]:
    """Server-published limits/defaults — the ONLY place the UI learns them
    from (the old compile-time TS mirrors drifted for years)."""
    return {
        "max_hops": constants.MAX_HOPS,
        "bfs_node_cap": constants.BFS_NODE_CAP,
        "default_expand_depth": constants.DEFAULT_EXPAND_DEPTH,
        "ui_default_window_days": constants.UI_DEFAULT_WINDOW_DAYS,
        "ui_default_max_neighbors": constants.UI_DEFAULT_MAX_NEIGHBORS,
        "atlas_sample_size": constants.DEFAULT_ATLAS_SAMPLE,
        "flows_default_hops": constants.FLOWS_DEFAULT_HOPS,
        "flows_max_hops": constants.FLOWS_MAX_HOPS,
        "flows_default_min_usd": constants.FLOWS_DEFAULT_MIN_USD,
        "flows_default_range_days": constants.FLOWS_DEFAULT_RANGE_DAYS,
        "flows_max_edges": constants.FLOWS_MAX_EDGES,
        "timeline_default_grain": constants.TIMELINE_DEFAULT_GRAIN,
        "timeline_default_range_days": constants.TIMELINE_DEFAULT_RANGE_DAYS,
        "timeline_default_window_buckets": constants.TIMELINE_DEFAULT_WINDOW_BUCKETS,
        "timeline_max_rows": constants.TIMELINE_MAX_ROWS,
    }


def timeline_bucket_range(
    grain: str, range_days: int, today: "date | None" = None
) -> tuple[str, str, int]:
    """Server-side bucket axis: ``(range_start, range_end_exclusive, count)``.

    Both endpoints are ALREADY-BUCKETED ISO dates computed with the same
    bucketing ClickHouse applies (`toDate` / ISO-Monday `toStartOfWeek` /
    `toStartOfMonth`), so the SQL filters and the client axis can never
    drift. ``range_end_exclusive`` is the first bucket AFTER the last
    in-range one (half-open contract); ``count`` is the number of buckets in
    ``[range_start, range_end_exclusive)``.
    """
    from datetime import date, timedelta

    anchor = today or date.today()
    raw_start = anchor - timedelta(days=max(1, int(range_days)))

    def bucket(d: date) -> date:
        if grain == "day":
            return d
        if grain == "week":  # ISO Monday, mirrors toStartOfWeek(x, 1)
            return d - timedelta(days=d.weekday())
        if grain == "month":
            return d.replace(day=1)
        raise ValueError(f"unknown timeline grain: {grain!r}")

    def step(d: date) -> date:
        if grain == "day":
            return d + timedelta(days=1)
        if grain == "week":
            return d + timedelta(days=7)
        # month: next calendar month
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)

    start = bucket(raw_start)
    end_exclusive = step(bucket(anchor))
    count = 0
    cur = start
    while cur < end_exclusive:
        count += 1
        cur = step(cur)
    return start.isoformat(), end_exclusive.isoformat(), count


def empty_state(title: str) -> dict[str, Any]:
    """view_state v2: per-mode namespaces (atlas / investigate) + shared
    selection/layout. `mode` decides which dataset pair the canvas renders
    (nodes/edges vs atlas_nodes/atlas_edges); switching modes is lossless."""
    profiles = [profile_card(p) for p in discover_profiles()]
    return {
        "title": title,
        "mode": "atlas",
        # Bumped ONLY by explicit mode commands (update_graph_explorer_focus /
        # set_graph_explorer_view with a mode). The client adopts server mode
        # only when this advances — data loads can't flip the visible tab.
        "mode_revision": 0,
        "catalog": profiles,
        "limits": limits_block(),
        "atlas": {
            "selected_profiles": [],
            "sample_size": constants.DEFAULT_ATLAS_SAMPLE,
            "window_days": constants.UI_DEFAULT_WINDOW_DAYS,
            "scope": {},
        },
        # Catalog preview is deliberately separate from the applied Atlas
        # selection. An analyst can inspect one relationship's real sample
        # and provenance before deciding to add it to the graph.
        "atlas_preview": {
            "profile": "",
            "sample_size": 25,
            "window_days": constants.UI_DEFAULT_WINDOW_DAYS,
            "scope": {},
            "warnings": [],
        },
        "investigate": {
            "seed": {"id": "", "kind": ""},
            "active_profiles": [],
            "window_days": constants.UI_DEFAULT_WINDOW_DAYS,
            "max_neighbors": constants.UI_DEFAULT_MAX_NEIGHBORS,
            "hops_used": 0,
            # Server-owned: nodes whose neighborhoods have been fetched.
            # Seed-expands advance the frontier = canvas nodes not in here.
            "expanded_ids": [],
            "scope": {},
        },
        "timeline": {
            "anchor": {"id": "", "kind": ""},
            # How the node set was chosen: "investigate" (current subgraph)
            # or "seed" (1-hop around the anchor).
            "scope": "",
            "profiles": [],
            "grain": constants.TIMELINE_DEFAULT_GRAIN,
            "range_days": constants.TIMELINE_DEFAULT_RANGE_DAYS,
            # CH-bucketed ISO dates; the client steps the axis from these.
            "range_start": "",
            "range_end": "",
            "bucket_count": 0,
            "window_buckets": constants.TIMELINE_DEFAULT_WINDOW_BUCKETS,
            # {profile_id: "flow"|"state"|"interval"|"static"}
            "profile_shapes": {},
            "forensic_scope": {},
            # NOTE: cursor / playing / speed are CLIENT-LOCAL by design —
            # playback must never round-trip per step.
        },
        "flows": {
            "seeds": [],
            "direction": "out",
            "hops": constants.FLOWS_DEFAULT_HOPS,
            "range_days": constants.FLOWS_DEFAULT_RANGE_DAYS,
            # Resolved ISO datetimes actually used (server-owned).
            "t0": "",
            "t1": "",
            "min_usd": constants.FLOWS_DEFAULT_MIN_USD,
            "tokens": [],
            "include_bridges": True,
            "node_count": 0,
            "edge_count": 0,
            "truncated": False,
            "truncated_hops": [],
            # {node_id: ["out","in"]} merge bookkeeping (per-node traces).
            "expanded": {},
            # [{token_address, symbol, amount_usd}] desc, capped.
            "token_catalog": [],
            "scope": {},
        },
        "transactions": {
            "query": {
                "kind": "address",
                "hashes": [],
                "address": None,
                "counterparties": [],
                "tokens": [],
                "window": None,
            },
            "results": {"hashes": [], "selected_hash": None},
            "last_attempt": None,
            "tx_hashes": [],
            "seed": "",
            "expanded": [],
            "counterparties": [],
            "range_days": constants.TX_DEFAULT_RANGE_DAYS,
            "max_txs": constants.TX_DEFAULT_MAX_TXS,
            "tokens": [],
            "min_usd": 0.0,
            "tx_count": 0,
            "leg_count": 0,
            # Machine-readable coverage: rows returned vs rows that EXIST, the
            # window actually applied, and the residuals this relation cannot
            # see. `exact` is only ever true when returned == total.
            "scope": {},
        },
        # Dataset key -> forensic scope id.  The scope itself lives in the
        # owning mode namespace; this map prevents a hydrated dataset from
        # being attributed to a newer/older load by accident.
        "dataset_scopes": {},
        # Cleared on mode switch — evidence panels are mode-specific.
        "selection": {"node_id": "", "edge_id": "", "request_id": 0},
        "layout": "force",
        "semantic_status_filter": "all",
        "suggested_next_hops": [],
        "node_roles": {},
        "warnings": [],
    }


def seed_kind_of(roles: dict[str, Any] | None) -> str:
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


def build_payload(
    record: "mini_apps.ViewRecord",
    app_id: str = constants.GRAPH_EXPLORER_APP_ID,
) -> MiniAppPayload:
    titles = constants.DATASET_TITLES
    dataset_scopes = dict(record.view_state.get("dataset_scopes") or {})
    possible_scopes = [
        (record.view_state.get("atlas") or {}).get("scope"),
        (record.view_state.get("atlas_preview") or {}).get("scope"),
        (record.view_state.get("investigate") or {}).get("scope"),
        (record.view_state.get("flows") or {}).get("scope"),
        (record.view_state.get("timeline") or {}).get("forensic_scope"),
        (record.view_state.get("transactions") or {}).get("scope"),
        record.view_state.get("focus_scope"),
    ]
    scope_by_id = {
        str(scope.get("scope_id")): scope
        for scope in possible_scopes
        if isinstance(scope, dict) and scope.get("scope_id")
    }
    descriptors = {}
    for key, dataset in record.datasets.items():
        scope_id = str(dataset_scopes[key]) if dataset_scopes.get(key) else None
        provenance = dict(scope_by_id.get(str(scope_id or ""), {}))
        provenance.update(
            {
                "dataset_key": key,
                "result_row_hash": canonical_row_hash(dataset.rows),
            }
        )
        descriptors[key] = mini_apps.build_dataset_descriptor(
            key=key,
            dataset=dataset,
            title=titles.get(key, key),
            scope_id=scope_id,
            provenance=provenance,
        )
    warnings = list(record.view_state.get("warnings") or [])
    warnings += mini_apps.collect_dataset_warnings(*record.datasets.values())
    seen: list[str] = []
    for warning in warnings:
        if warning and warning not in seen:
            seen.append(warning)
    # dataset_revisions ride on EVERY dataset-bearing payload — the frontend
    # keys hydration and adoption on them, never on SQL text.
    view_state = {
        **record.view_state,
        "dataset_revisions": dict(record.dataset_revisions),
    }
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=record.view_id,
        app_id=app_id,
        title=record.title,
        status="ready",
        summary_cards=[],
        datasets=descriptors,
        view_state=view_state,
        provenance={
            "source": "semantic_registry",
            "dataset_scopes": dataset_scopes,
        },
        warnings=seen,
    )
