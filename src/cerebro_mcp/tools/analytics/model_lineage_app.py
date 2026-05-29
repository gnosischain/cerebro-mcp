"""Model-lineage explorer mini app.

An interactive, dbt-Explorer-style view over the gnosis_dbt model DAG. The
backend hydrates a per-view dataset store (nodes / edges / column_edges) from
the manifest lineage graph and the semantic registry, and the React Flow
front end renders it with click-to-expand, layer toggle (model DAG vs.
semantic), and a column-lineage drawer.

Mirrors the structure of ``tools/semantic/graph_explorer.py``.
"""

from __future__ import annotations

import importlib.resources
import logging
from collections import defaultdict
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.loaders.column_lineage import get_column_lineage
from cerebro_mcp.loaders.manifest import manifest
from cerebro_mcp.loaders.semantic import semantic_runtime
from cerebro_mcp.models.mini_app import DatasetStats, MiniAppPayload
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.tools.visualization import mini_apps

logger = logging.getLogger(__name__)

MODEL_LINEAGE_APP_ID = "model_lineage"
MODEL_LINEAGE_URI = "ui://cerebro/model_lineage"
DEFAULT_TITLE = "Model Lineage Explorer"
MAX_DEPTH = 5
DEFAULT_DEPTH = 1

NODES_COLUMNS = [
    "id",
    "name",
    "kind",
    "materialized",
    "schema",
    "tags",
    "description",
    "column_count",
    "test_count",
]
EDGES_COLUMNS = ["id", "source", "target", "layer"]
COLUMN_EDGES_COLUMNS = [
    "id",
    "source_model",
    "source_column",
    "target_model",
    "target_column",
    "level",
]
METRICS_COLUMNS = ["metric", "value"]

_BUNDLED_HTML: str | None = None


def get_model_lineage_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_HTML
    if _BUNDLED_HTML is None:
        try:
            _BUNDLED_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/model_lineage.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>model_lineage.html not built</div>"
                "</body></html>"
            )
    return _BUNDLED_HTML


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------


def _dataset_from_rows(
    columns: list[str], rows: list[list[Any]], label: str
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
        sql=f"-- assembled in-process for {label}",
        database="dbt",
        parameters={},
    )


def _empty_dataset(label: str, columns: list[str]) -> CachedDataset:
    return _dataset_from_rows(columns, [], label)


def _node_to_row(node: dict[str, Any]) -> list[Any]:
    return [
        node.get("id", ""),
        node.get("name", ""),
        node.get("kind", ""),
        node.get("materialized", ""),
        node.get("schema", ""),
        node.get("tags", []),
        node.get("description", ""),
        node.get("column_count", 0),
        node.get("test_count", 0),
    ]


def _edge_to_row(edge: dict[str, Any], layer: str) -> list[Any]:
    return [
        edge.get("id", ""),
        edge.get("source", ""),
        edge.get("target", ""),
        layer,
    ]


def _merge_rows(
    existing: list[list[Any]], new: list[list[Any]]
) -> list[list[Any]]:
    """Merge row lists, de-duplicating on the first column (id)."""
    merged = list(existing)
    seen = {row[0] for row in merged if row}
    for row in new:
        if row and row[0] not in seen:
            merged.append(row)
            seen.add(row[0])
    return merged


# ---------------------------------------------------------------------------
# Subgraph builders
# ---------------------------------------------------------------------------


def _model_subgraph(
    seed: str,
    direction: str,
    depth: int,
    include_kinds: list[str] | None,
    tags: list[str] | None,
) -> dict[str, Any]:
    """Physical model DAG subgraph from the manifest lineage graph."""
    result = manifest.get_subgraph(
        seed=seed,
        direction=direction,
        depth=depth,
        include_kinds=include_kinds,
        tags=tags,
    )
    warnings = [result["error"]] if result.get("error") else []
    return {
        "nodes": result.get("nodes", []),
        "edges": result.get("edges", []),
        "seed_id": result.get("seed_id", ""),
        "warnings": warnings,
    }


def _semantic_node(model_name: str) -> dict[str, Any]:
    """Build a node descriptor for a semantic-layer model (id == model name)."""
    model = manifest.get_model(model_name) or {}
    return {
        "id": model_name,
        "name": model_name,
        "kind": "model",
        "materialized": model.get("config", {}).get("materialized", ""),
        "schema": model.get("schema", ""),
        "tags": list(model.get("tags", []) or []),
        "description": model.get("description", ""),
        "column_count": len(model.get("columns", {}) or {}),
        "test_count": 0,
    }


def _semantic_subgraph(seed: str, depth: int) -> dict[str, Any]:
    """Semantic-layer subgraph from registry relationships (undirected join graph)."""
    snap = semantic_runtime.snapshot
    if snap is None:
        return {
            "nodes": [],
            "edges": [],
            "seed_id": seed,
            "warnings": ["Semantic registry unavailable"],
        }

    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for rel in snap.relationships or []:
        lm = rel.get("left_model", "")
        rm = rel.get("right_model", "")
        if not lm or not rm:
            continue
        adjacency[lm].append((rm, rel))
        adjacency[rm].append((lm, rel))

    if seed not in adjacency and seed not in (snap.models or {}):
        return {
            "nodes": [_semantic_node(seed)] if manifest.get_model(seed) else [],
            "edges": [],
            "seed_id": seed,
            "warnings": [
                f"Model '{seed}' has no approved semantic relationships."
            ],
        }

    depth = max(0, int(depth))
    collected: set[str] = {seed}
    visited: set[str] = set()
    edge_map: dict[str, dict[str, Any]] = {}
    frontier: list[tuple[str, int]] = [(seed, 0)]
    while frontier:
        model_name, d = frontier.pop(0)
        if model_name in visited:
            continue
        visited.add(model_name)
        if d >= depth:
            continue
        for other, rel in adjacency.get(model_name, []):
            collected.add(other)
            # Stable, order-independent edge id between the two models.
            pair = "~".join(sorted((model_name, other)))
            rel_name = rel.get("name", "")
            edge_id = f"{pair}:{rel_name}"
            if edge_id not in edge_map:
                edge_map[edge_id] = {
                    "id": edge_id,
                    "source": rel.get("left_model", model_name),
                    "target": rel.get("right_model", other),
                }
            if other not in visited:
                frontier.append((other, d + 1))

    nodes = [_semantic_node(name) for name in sorted(collected)]
    edges = list(edge_map.values())
    return {"nodes": nodes, "edges": edges, "seed_id": seed, "warnings": []}


def _build_subgraph(
    seed: str,
    layer: str,
    direction: str,
    depth: int,
    include_kinds: list[str] | None,
    tags: list[str] | None,
) -> dict[str, Any]:
    if layer == "semantic":
        return _semantic_subgraph(seed, depth)
    return _model_subgraph(seed, direction, depth, include_kinds, tags)


# ---------------------------------------------------------------------------
# View state + payload
# ---------------------------------------------------------------------------


def _build_catalog() -> list[dict[str, Any]]:
    """Compact, searchable list of every model for the browse/start screen.

    Keeps the front end self-sufficient: users can scan/search ~1000 models by
    name, schema, materialization, and tags without knowing an exact seed.
    """
    catalog: list[dict[str, Any]] = []
    for name in manifest.get_all_model_names():
        node = manifest.get_model(name) or {}
        desc = (node.get("description", "") or "").strip()
        catalog.append(
            {
                "name": name,
                "schema": node.get("schema", "") or "",
                "materialized": node.get("config", {}).get("materialized", "")
                or "",
                "tags": list(node.get("tags", []) or [])[:6],
                "description": desc[:160],
            }
        )
    catalog.sort(key=lambda m: m["name"])
    return catalog


def _empty_state(title: str) -> dict[str, Any]:
    return {
        "title": title,
        "seed": "",
        "seed_id": "",
        "layer": "model",
        "direction": "both",
        "depth": DEFAULT_DEPTH,
        "include_kinds": [],
        "tags_filter": [],
        "selected_node_id": "",
        "selected_column": "",
        "catalog": [],
        "warnings": [],
    }


def _build_payload(record: mini_apps.ViewRecord) -> MiniAppPayload:
    titles = {
        "nodes": "Models",
        "edges": "Lineage Edges",
        "column_edges": "Column Lineage",
        "graph_metrics": "Graph Metrics",
    }
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
        app_id=MODEL_LINEAGE_APP_ID,
        title=record.title,
        status="ready",
        summary_cards=[],
        datasets=descriptors,
        view_state=record.view_state,
        provenance={"source": "dbt_manifest"},
        warnings=seen,
    )


def _write_subgraph(
    view_id: str,
    record: mini_apps.ViewRecord,
    sub: dict[str, Any],
    layer: str,
    *,
    merge: bool,
) -> None:
    """Write nodes/edges datasets + metrics for a subgraph, optionally merging."""
    new_node_rows = [_node_to_row(n) for n in sub.get("nodes", [])]
    new_edge_rows = [_edge_to_row(e, layer) for e in sub.get("edges", [])]

    if merge:
        existing_nodes = record.datasets.get("nodes")
        existing_edges = record.datasets.get("edges")
        node_rows = _merge_rows(
            existing_nodes.rows if existing_nodes else [], new_node_rows
        )
        edge_rows = _merge_rows(
            existing_edges.rows if existing_edges else [], new_edge_rows
        )
    else:
        node_rows, edge_rows = new_node_rows, new_edge_rows

    mini_apps.attach_dataset(
        view_id, "nodes", _dataset_from_rows(NODES_COLUMNS, node_rows, "nodes")
    )
    mini_apps.attach_dataset(
        view_id, "edges", _dataset_from_rows(EDGES_COLUMNS, edge_rows, "edges")
    )
    metrics_rows = [
        ["node_count", float(len(node_rows))],
        ["edge_count", float(len(edge_rows))],
    ]
    mini_apps.attach_dataset(
        view_id,
        "graph_metrics",
        _dataset_from_rows(METRICS_COLUMNS, metrics_rows, "graph_metrics"),
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_model_lineage_tools(mcp, ch: ClickHouseManager) -> None:
    mini_apps.register_app(
        MODEL_LINEAGE_APP_ID, title=DEFAULT_TITLE, resource_uri=MODEL_LINEAGE_URI
    )

    @mcp.resource(MODEL_LINEAGE_URI, mime_type="text/html;profile=mcp-app")
    def serve_model_lineage_app() -> str:
        return get_model_lineage_html()

    _UI_META = {
        "ui": {"resourceUri": MODEL_LINEAGE_URI},
        "ui/resourceUri": MODEL_LINEAGE_URI,
    }

    @mcp.tool(meta=_UI_META)
    def open_model_lineage(
        seed_model: str = "",
        direction: str = "both",
        depth: int = DEFAULT_DEPTH,
        layer: str = "model",
        title: str = "",
    ) -> CallToolResult:
        """Open the Model Lineage Explorer mini app.

        With a `seed_model`, loads the bounded lineage subgraph around it.
        `layer="model"` renders the physical dbt DAG; `layer="semantic"`
        renders approved semantic-registry join relationships.
        """
        effective_title = title or DEFAULT_TITLE
        view_id = mini_apps.create_view(MODEL_LINEAGE_APP_ID, effective_title)
        mini_apps.patch_view_state(view_id, _empty_state(effective_title))
        mini_apps.replace_view_datasets(
            view_id,
            {
                "nodes": _empty_dataset("nodes", NODES_COLUMNS),
                "edges": _empty_dataset("edges", EDGES_COLUMNS),
                "column_edges": _empty_dataset("column_edges", COLUMN_EDGES_COLUMNS),
                "graph_metrics": _empty_dataset("graph_metrics", METRICS_COLUMNS),
            },
        )

        if not manifest.is_loaded:
            record = mini_apps.get_view(view_id)
            assert record is not None
            mini_apps.patch_view_state(
                view_id, {"warnings": ["dbt manifest not loaded"]}
            )
            payload = _build_payload(mini_apps.get_view(view_id))
            return mini_apps.payload_to_call_tool_result(
                payload, summary_text="Model Lineage Explorer (manifest unavailable)"
            )

        capped_depth = min(max(int(depth), 0), MAX_DEPTH)
        layer = "semantic" if layer == "semantic" else "model"

        # Always ship the browse catalog so the UI has a discovery start screen
        # (users don't need to know an exact model name up front).
        mini_apps.patch_view_state(view_id, {"catalog": _build_catalog()})

        if seed_model:
            sub = _build_subgraph(
                seed_model, layer, direction, capped_depth, None, None
            )
            record = mini_apps.get_view(view_id)
            assert record is not None
            _write_subgraph(view_id, record, sub, layer, merge=False)
            mini_apps.patch_view_state(
                view_id,
                {
                    "seed": seed_model,
                    "seed_id": sub.get("seed_id", ""),
                    "layer": layer,
                    "direction": direction,
                    "depth": capped_depth,
                    "selected_node_id": sub.get("seed_id", ""),
                    "warnings": sub.get("warnings", []),
                },
            )

        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload(record)
        node_count = len(payload.datasets.get("nodes").preview_rows) if payload.datasets.get("nodes") else 0
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Model Lineage Explorer ready ({node_count} nodes, "
                f"layer={layer}, view_id={view_id[:8]})"
            ),
        )

    @mcp.tool(meta=_UI_META)
    def expand_model_lineage_node(
        view_id: str,
        node_id: str,
        direction: str = "both",
        depth: int = 1,
    ) -> CallToolResult:
        """Expand the lineage graph by one hop around `node_id` and merge it in."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        if not node_id:
            return mini_apps.error_call_tool_result("node_id is required")

        layer = record.view_state.get("layer", "model")
        capped_depth = min(max(int(depth), 1), MAX_DEPTH)
        # For the model layer node ids are unique_ids ("model.<proj>.<name>");
        # the seed argument expects the short model name.
        seed_name = node_id.split(".")[-1] if layer == "model" else node_id

        sub = _build_subgraph(
            seed_name, layer, direction, capped_depth, None, None
        )
        _write_subgraph(view_id, record, sub, layer, merge=True)
        mini_apps.patch_view_state(
            view_id,
            {"selected_node_id": node_id, "warnings": sub.get("warnings", [])},
        )

        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Expanded {seed_name} (view_id={view_id[:8]})",
        )

    @mcp.tool(meta=_UI_META)
    def set_model_lineage_filters(
        view_id: str,
        direction: str | None = None,
        depth: int | None = None,
        include_kinds: list[str] | None = None,
        tags: list[str] | None = None,
        layer: str | None = None,
    ) -> CallToolResult:
        """Re-run the subgraph from the current seed with new filters/layer."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        state = record.view_state
        seed = state.get("seed", "")
        if not seed:
            return mini_apps.error_call_tool_result(
                "No seed model loaded; call open_model_lineage with a seed_model first."
            )

        new_layer = layer if layer in ("model", "semantic") else state.get("layer", "model")
        new_direction = direction or state.get("direction", "both")
        new_depth = (
            min(max(int(depth), 0), MAX_DEPTH)
            if depth is not None
            else state.get("depth", DEFAULT_DEPTH)
        )
        new_kinds = include_kinds if include_kinds is not None else (state.get("include_kinds") or None)
        new_tags = tags if tags is not None else (state.get("tags_filter") or None)

        sub = _build_subgraph(
            seed, new_layer, new_direction, new_depth, new_kinds, new_tags
        )
        _write_subgraph(view_id, record, sub, new_layer, merge=False)
        mini_apps.patch_view_state(
            view_id,
            {
                "layer": new_layer,
                "direction": new_direction,
                "depth": new_depth,
                "include_kinds": new_kinds or [],
                "tags_filter": new_tags or [],
                "seed_id": sub.get("seed_id", ""),
                "warnings": sub.get("warnings", []),
            },
        )

        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Filters applied (layer={new_layer}, view_id={view_id[:8]})",
        )

    @mcp.tool(meta=_UI_META)
    def load_column_lineage(
        view_id: str,
        model_name: str,
        column: str,
        direction: str = "upstream",
        depth: int = 1,
    ) -> CallToolResult:
        """Compute column-level lineage and load it into the column drawer."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        capped_depth = min(max(int(depth), 1), MAX_DEPTH)
        result = get_column_lineage(
            model_name=model_name,
            column=column,
            direction=direction,
            depth=capped_depth,
        )
        rows = [
            [
                e.get("id", ""),
                e.get("source_model", ""),
                e.get("source_column", ""),
                e.get("target_model", ""),
                e.get("target_column", ""),
                e.get("level", ""),
            ]
            for e in result.get("edges", [])
        ]
        mini_apps.attach_dataset(
            view_id,
            "column_edges",
            _dataset_from_rows(COLUMN_EDGES_COLUMNS, rows, "column_edges"),
        )
        mini_apps.patch_view_state(
            view_id,
            {
                "selected_node_id": record.view_state.get("selected_node_id", ""),
                "selected_column": column,
                "warnings": result.get("warnings", []),
            },
        )

        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Column lineage for {model_name}.{column}: {len(rows)} edges "
                f"(level={result.get('level')}, view_id={view_id[:8]})"
            ),
        )
