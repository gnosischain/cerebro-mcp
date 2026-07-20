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
# First 7 columns positionally identical to EDGES_COLUMNS so the frontend's
# parseEdgeRow works unmodified; the two bucket columns carry the compressed
# interval (see build_timeline_sql for the per-shape semantics).
TIMELINE_EDGES_COLUMNS = EDGES_COLUMNS + ["bucket_start", "bucket_end"]
TIMELINE_NARRATIVE_COLUMNS = [
    "bucket_start",
    "direction",
    "event_kind",
    "counterparty_id",
    "counterparty_label",
    "token_address",
    "token_symbol",
    "raw_amount",
    "normalized_amount",
    "transfer_count",
    "previous_token_amount",
    "current_token_amount",
    "delta_token_amount",
    "previous_known_usd",
    "current_known_usd",
    "delta_known_usd",
    "price_coverage",
    "volume_driven_usd_effect",
    "price_driven_usd_effect",
    "change",
    "scope_id",
]
EDGE_EVIDENCE_COLUMNS = [
    "edge_id", "column", "value", "subject_kind", "request_id",
]
NODE_EVIDENCE_COLUMNS = [
    "node_id", "column", "value", "subject_kind", "request_id",
]
METRICS_COLUMNS = ["metric", "value"]

# Timeline caps + defaults. Per-profile budget is additionally bounded by
# TIMELINE_MAX_ROWS // n_profiles (fair share) at query time.
TIMELINE_ROWS_PER_PROFILE = 8_000
TIMELINE_MAX_ROWS = 24_000
TIMELINE_DEFAULT_GRAIN = "week"
TIMELINE_DEFAULT_RANGE_DAYS = 365
TIMELINE_DEFAULT_WINDOW_BUCKETS = 4

# Flows mode (forensic fund tracing).
FLOWS_DEFAULT_HOPS = 2
FLOWS_MAX_HOPS = 4
# 90d, not 30d: a forensic trace run weeks after an incident (e.g. a June
# exploit queried in July) must still reach back into the incident window by
# default. A 30d default silently excluded the very activity being traced.
FLOWS_DEFAULT_RANGE_DAYS = 90
FLOWS_DEFAULT_MIN_USD = 10.0
FLOWS_EDGES_PER_QUERY = 2000     # per hop-leg SQL limit (n+1 inside)
FLOWS_PER_HOP_NODE_BUDGET = 400  # new nodes admitted per hop per leg
FLOWS_MAX_NODES = 3000           # layered-layout legibility ceiling
FLOWS_MAX_EDGES = 8000
FLOWS_MAX_SEEDS = 50
# Auto-stop sectors (per-hop, checked BEFORE enqueueing the next frontier).
# Payments stays traversable (GP wallets must be walkable); seeds are always
# expandable; a per-node Trace action overrides terminal status.
FLOWS_TERMINAL_SECTORS = frozenset({"Bridges", "DEX", "Privacy"})

FLOW_NODES_COLUMNS = [
    "id", "label", "sector", "project", "hop_rank",
    "in_usd", "out_usd", "first_seen", "last_seen", "flags",
]

# Transactions mode (per-transfer-leg forensics).
TX_DEFAULT_RANGE_DAYS = 30
TX_DEFAULT_MAX_TXS = 25       # transactions opened per load
TX_MAX_TXS = 200
TX_MAX_LEGS = 4000            # legs rendered; a tx is never split across it
TX_LEG_NODES_COLUMNS = [
    "id", "label", "role", "project", "column_rank",
    "in_usd", "out_usd", "leg_count", "flags",
]
TX_LEG_EDGES_COLUMNS = [
    "id", "source", "target", "tx_hash", "log_index", "block_number",
    "transaction_index", "block_timestamp", "token_address", "symbol",
    "amount", "amount_usd", "seq", "tx_rank", "tx_status", "raw_amount",
]
# No amount_usd: USD needs the token-metadata + price join, which the
# discovery query deliberately skips (it must stay a cheap, unfiltered
# "which transactions" scan). Publishing a hard 0 would look like a real
# value, which is the dishonesty this mode exists to avoid.
TX_LIST_COLUMNS = [
    "tx_hash", "block_number", "transaction_index", "block_timestamp",
    "leg_count", "token_count",
]
TX_RAW_RECEIPTS_COLUMNS = [
    "tx_hash", "receipt_json", "receipt_sha256", "logs_sha256",
    "block_number", "transaction_index", "block_hash", "receipt_status",
    "retrieved_at",
]
FLOW_EDGES_COLUMNS = [
    "id", "source", "target", "edge_class", "token_address", "symbol",
    "amount", "amount_usd", "transfer_count", "first_seen", "last_seen",
    "unknown_usd_rows",
]

DATASET_TITLES = {
    "nodes": "Nodes",
    "edges": "Edges",
    "atlas_nodes": "Atlas Nodes",
    "atlas_edges": "Atlas Edges",
    "atlas_preview_nodes": "Relationship Preview Nodes",
    "atlas_preview_edges": "Relationship Preview Edges",
    "timeline_nodes": "Timeline Nodes",
    "timeline_edges": "Timeline Edges",
    "timeline_narrative": "Timeline Narrative",
    "flow_nodes": "Flow Nodes",
    "flow_edges": "Flow Edges",
    "tx_nodes": "Transaction Participants",
    "tx_legs": "Transfer Legs",
    "tx_list": "Transactions",
    "tx_raw_receipts": "Raw RPC Receipts",
    "node_evidence": "Node Evidence",
    "edge_evidence": "Edge Evidence",
    "graph_metrics": "Graph Metrics",
}

# Quality-tier ordinal for the search gate (D1). Higher = more trusted.
TIER_ORDINAL = {"approved": 3, "candidate": 2, "docs_only": 1, "": 0}
