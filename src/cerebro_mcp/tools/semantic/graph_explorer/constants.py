"""Graph Explorer constants — single source of truth for both tool layers.

Read these ATTRIBUTE-STYLE (``constants.MAX_HOPS``), never
``from .constants import MAX_HOPS`` — tests monkeypatch the module
attributes and a from-import would freeze the original value.
"""

from __future__ import annotations

from cerebro_mcp.config import settings

GRAPH_EXPLORER_APP_ID = "graph_explorer"
GRAPH_EXPLORER_URI = "ui://cerebro/graph_explorer"
DEFAULT_TITLE = "Graph Explorer"
# Session hop cap. Deliberately raised to 50 in WS9 and live-QA'd in WS10 —
# the DOCS saying 5 were the stale side. Do not lower without a product call.
MAX_HOPS = 50
# PUBLIC data-tool defaults (explore_neighborhood / calculate_flow_efficiency
# schemas) — a CONTRACT pinned by eval fixtures; never change silently.
DEFAULT_WINDOW_DAYS = 365
DEFAULT_MAX_NEIGHBORS = 250
# UI-ONLY defaults, published to the frontend via view_state["limits"] so the
# two ends can never drift again. Split from the public tool defaults above.
DEFAULT_EXPAND_DEPTH = 1   # one double-click = one comprehensible hop
UI_DEFAULT_WINDOW_DAYS = 90
UI_DEFAULT_MAX_NEIGHBORS = 100
DEFAULT_ATLAS_SAMPLE = 150  # per profile
# BFS expansion ceilings (overridable via Settings / env). Promoted from a
# function-local literal so the cap is centralized and tunable. The cap is
# checked *after* each frontier round so at least one hop always expands, with
# a per-hop budget so a dense first frontier can't consume the whole cap.
BFS_NODE_CAP = settings.GRAPH_EXPLORER_BFS_NODE_CAP
BFS_PER_HOP_BUDGET = settings.GRAPH_EXPLORER_BFS_PER_HOP_BUDGET

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
    "atlas_nodes": "Atlas Nodes",
    "atlas_edges": "Atlas Edges",
    "node_evidence": "Node Evidence",
    "edge_evidence": "Edge Evidence",
    "graph_metrics": "Graph Metrics",
}

# Quality-tier ordinal for the search gate (D1). Higher = more trusted.
TIER_ORDINAL = {"approved": 3, "candidate": 2, "docs_only": 1, "": 0}
