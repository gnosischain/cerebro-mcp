"""Graph Explorer view-state and dataset builders (pure, no ClickHouse)."""

from __future__ import annotations

from typing import Any

from cerebro_mcp.models.mini_app import DatasetStats, MiniAppPayload
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.semantic.graph_profiles import GraphProfile, discover_profiles
from cerebro_mcp.tools.visualization import mini_apps

from . import constants


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
    }


def empty_state(title: str) -> dict[str, Any]:
    """view_state v2: per-mode namespaces (atlas / investigate) + shared
    selection/layout. `mode` decides which dataset pair the canvas renders
    (nodes/edges vs atlas_nodes/atlas_edges); switching modes is lossless."""
    profiles = [profile_card(p) for p in discover_profiles()]
    return {
        "title": title,
        "mode": "atlas",
        "catalog": profiles,
        "limits": limits_block(),
        "atlas": {
            "selected_profiles": [],
            "sample_size": constants.DEFAULT_ATLAS_SAMPLE,
            "window_days": constants.UI_DEFAULT_WINDOW_DAYS,
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
        },
        # Cleared on mode switch — evidence panels are mode-specific.
        "selection": {"node_id": "", "edge_id": ""},
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
        provenance={"source": "semantic_registry"},
        warnings=seen,
    )
