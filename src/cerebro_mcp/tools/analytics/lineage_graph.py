"""Structured model-lineage subgraph tool.

Exposes ``get_model_subgraph`` — the agent-facing JSON traversal endpoint
for the dbt model DAG. Unlike the markdown lineage tools in ``dbt.py``
(``get_upstream_lineage`` / ``get_downstream_impact``), this returns a flat
``{nodes, edges}`` structure suitable for programmatic traversal and for
hydrating the model-lineage mini app.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from cerebro_mcp.config import settings
from cerebro_mcp.loaders.manifest import manifest

_last_manifest_check: float = 0.0

# Keep agent payloads bounded — a depth-3 "both" sweep on a hub model can
# fan out to hundreds of nodes. The mini app paginates; the agent endpoint
# truncates and says so.
MAX_NODES = 300


def _maybe_refresh_manifest() -> None:
    global _last_manifest_check
    now = time.time()
    if now - _last_manifest_check > settings.MANIFEST_REFRESH_INTERVAL_SECONDS:
        _last_manifest_check = now
        manifest.reload_if_changed()


def register_lineage_graph_tools(mcp) -> None:
    @mcp.tool()
    def get_model_subgraph(
        model_name: str,
        direction: str = "both",
        depth: int = 1,
        include_kinds: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> str:
        """Return a bounded model-lineage subgraph as JSON for traversal.

        Use this to programmatically walk the dbt model DAG around a seed
        model: which models feed it (upstream) and which it feeds
        (downstream). Returns a flat ``{nodes, edges}`` graph — prefer this
        over `get_upstream_lineage` / `get_downstream_impact` when you need
        structured data rather than a human-readable summary.

        Args:
            model_name: Seed model short name (e.g. "fct_execution_pools_daily").
            direction: "upstream", "downstream", or "both" (default).
            depth: Hops from the seed, 0-5 (default 1). Higher = larger graph.
            include_kinds: Optional node-kind whitelist: "model", "source",
                "unknown". The seed is always included.
            tags: Optional tag whitelist; non-seed model nodes lacking all of
                these tags are dropped.

        Returns:
            JSON object: {seed, seed_id, direction, depth, nodes[], edges[],
            truncated?}. Each node carries id, name, kind, materialized,
            schema, tags, description, column_count, test_count. Each edge is
            {id, source, target} referencing node ids (parent -> child).
        """
        _maybe_refresh_manifest()

        if not manifest.is_loaded:
            return json.dumps(
                {"error": "dbt manifest not loaded; lineage unavailable"}
            )

        capped_depth = min(max(int(depth), 0), 5)
        result = manifest.get_subgraph(
            seed=model_name,
            direction=direction,
            depth=capped_depth,
            include_kinds=include_kinds,
            tags=tags,
        )

        if result.get("error"):
            return json.dumps(result)

        nodes = result.get("nodes", [])
        if len(nodes) > MAX_NODES:
            kept_ids = {n["id"] for n in nodes[:MAX_NODES]}
            result["nodes"] = nodes[:MAX_NODES]
            result["edges"] = [
                e
                for e in result.get("edges", [])
                if e["source"] in kept_ids and e["target"] in kept_ids
            ]
            result["truncated"] = True
            result["truncated_note"] = (
                f"Subgraph exceeded {MAX_NODES} nodes; truncated. "
                "Lower `depth` or set `direction`/`tags` to narrow."
            )

        return json.dumps(result)

    @mcp.tool()
    def get_column_lineage(
        model_name: str,
        column: str,
        direction: str = "upstream",
        depth: int = 1,
    ) -> str:
        """Trace column-level lineage for a model column as JSON.

        Derives column-to-column edges by parsing the model's compiled SQL
        with sqlglot (ClickHouse dialect). Use this to answer "where does
        this column come from?". Upstream is fully supported; downstream and
        unparseable (macro-heavy) SQL degrade gracefully to model-level edges
        with a warning.

        Args:
            model_name: dbt model short name.
            column: Column within the model to trace.
            direction: "upstream" (default) or "downstream".
            depth: Upstream hops to follow, 1-5 (default 1).

        Returns:
            JSON object: {model, column, direction, level, edges[], warnings[]}.
            `level` is "column" (true column lineage) or "model" (fallback).
            Each edge: {id, source_model, source_column, target_model,
            target_column, level}.
        """
        _maybe_refresh_manifest()

        if not manifest.is_loaded:
            return json.dumps(
                {"error": "dbt manifest not loaded; column lineage unavailable"}
            )

        from cerebro_mcp.loaders.column_lineage import get_column_lineage as _impl

        capped_depth = min(max(int(depth), 1), 5)
        result = _impl(
            model_name=model_name,
            column=column,
            direction=direction,
            depth=capped_depth,
        )
        return json.dumps(result)
