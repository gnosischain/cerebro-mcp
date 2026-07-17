// Dev-mode fixtures (Vite without an MCP host). Production reads the real
// payload from <script id="mini-app-data">; none of this ships active in a
// hosted view.
//
// Scenario switches (sessionStorage, so both screens can be iterated on
// without editing source):
//   sessionStorage.ge_force_empty = '1'   → pristine ATLAS mode (catalog
//                                           only, nothing sampled)
//   sessionStorage.ge_force_sample = '1'  → ATLAS mode with a sampled
//                                           profile union on the canvas
//   (default)                             → INVESTIGATE mode, seeded at
//                                           0xaaa with a 1-hop subgraph
//
// The fixture speaks view_state v2: per-mode namespaces (atlas/investigate),
// server-published limits, selection object, and dataset_revisions.

import type { DatasetDescriptor, MiniAppPayload } from "../shared/miniAppTypes";
import type { GraphExplorerViewState, Limits, ProfileCard } from "./types";

export const APP_ID = "graph_explorer";

const DEV_LIMITS: Limits = {
  max_hops: 50,
  bfs_node_cap: 2000,
  default_expand_depth: 1,
  ui_default_window_days: 90,
  ui_default_max_neighbors: 100,
  atlas_sample_size: 150,
};

export const DEV_CATALOG: ProfileCard[] = [
  { profile: "circles_trust", model_name: "execution_circles_v2_trust_relations_current", module: "Circles", description: "Circles v2 trust graph", source_kind: "circles_avatar", target_kind: "circles_avatar", semantic_status: "approved", quality_tier: "approved", question_synonyms: ["trust graph"], semantic_source_file: "", time_aware: true },
  { profile: "circles_avatar_balances", model_name: "fct_execution_circles_v2_avatar_balances_latest", module: "Circles", description: "Avatar holds CRC token", source_kind: "circles_avatar", target_kind: "token", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: false },
  { profile: "safe_ownership", model_name: "int_execution_safes_current_owners", module: "safe", description: "Who owns each Safe", source_kind: "address", target_kind: "safe", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: ["safe owners"], semantic_source_file: "", time_aware: false },
  { profile: "gpay_ownership", model_name: "int_execution_gpay_wallet_owners", module: "gpay", description: "GPay wallet owners", source_kind: "address", target_kind: "gpay_wallet", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "token_transfers", model_name: "int_execution_transfers_whitelisted_daily", module: "transfers", description: "Whitelisted ERC20 transfers", source_kind: "address", target_kind: "address", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "lp_in_pool", model_name: "int_execution_pools_dex_liquidity_events", module: "pools", description: "LP provider → pool", source_kind: "address", target_kind: "pool", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "pool_contains_token", model_name: "int_execution_pools_dex_liquidity_events", module: "pools", description: "Pool contains token", source_kind: "pool", target_kind: "token", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: false },
  { profile: "deposit_to_validator", model_name: "int_GBCDeposit_deposists_daily", module: "GBCDeposit", description: "Deposit → validator", source_kind: "address", target_kind: "validator", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "bridge_user_flows", model_name: "int_execution_bridges_address_flows_daily", module: "bridges", description: "User ↔ bridge", source_kind: "address", target_kind: "bridge", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: [], semantic_source_file: "", time_aware: true },
];

export const DEV_NODES: unknown[][] = [
  ["0xaaa", "address", "0xaaa…1", ["token_transfers", "safe_ownership"]],
  ["0xbbb", "safe", "0xbbb…2", ["safe_ownership"]],
  ["0xccc", "circles_avatar", "0xccc…3", ["circles_trust", "circles_avatar_balances"]],
  ["0xddd", "token", "0xddd…4 CRC", ["circles_avatar_balances", "token_transfers"]],
  ["0xeee", "pool", "0xeee…5 B-V2", ["lp_in_pool", "pool_contains_token"]],
  ["0xfff", "address", "0xfff…6", ["lp_in_pool", "token_transfers"]],
  ["0x111", "validator", "validator #4096", ["deposit_to_validator"]],
  ["0x222", "address", "0x222…8", ["token_transfers", "gpay_ownership"]],
  ["0x333", "gpay_wallet", "0x333…9", ["gpay_ownership"]],
  ["0x444", "bridge", "0x444…a xDai", ["bridge_user_flows"]],
  ["0x555", "address", "0x555…b", ["token_transfers"]],
  ["0x666", "token", "0x666…c USDC", ["token_transfers", "pool_contains_token"]],
];

export const DEV_EDGES: unknown[][] = [
  ["e1", "0xaaa", "0xbbb", "safe_ownership", 1, 1, true],
  ["e2", "0xaaa", "0xddd", "token_transfers", 1500, 12, true],
  ["e3", "0xccc", "0xddd", "circles_avatar_balances", 820, 1, true],
  ["e4", "0xccc", "0xaaa", "circles_trust", 1, 1, true],
  ["e5", "0xfff", "0xeee", "lp_in_pool", 50000, 3, true],
  ["e6", "0xeee", "0xddd", "pool_contains_token", 1, 1, false],
  ["e7", "0x111", "0x111", "deposit_to_validator", 32, 1, true],
  ["e8", "0x222", "0x333", "gpay_ownership", 1, 1, true],
  ["e9", "0x222", "0x444", "bridge_user_flows", 2500, 5, true],
  ["e10", "0x333", "0xddd", "token_transfers", 300, 2, true],
  ["e11", "0xbbb", "0xeee", "token_transfers", 4200, 7, true],
  ["e12", "0x555", "0xaaa", "token_transfers", 180, 4, true],
  ["e13", "0xeee", "0x666", "pool_contains_token", 1, 1, false],
  ["e14", "0xfff", "0x666", "token_transfers", 9800, 20, true],
];

const NODE_COLS = ["id", "kind", "label", "profiles"];
const EDGE_COLS = ["id", "source", "target", "profile", "weight", "edge_count", "directed"];

function makeDataset(key: string, columns: string[], rows: unknown[][]): DatasetDescriptor {
  return {
    key,
    title: key,
    sql: `-- dev fixture: ${key}`,
    database: "dbt",
    columns: columns.map((name) => ({ name, type: "String" })),
    stats: {
      row_count: rows.length,
      rows_returned: rows.length,
      mode: "exact_bounded",
      warnings: [],
    },
    preview_rows: rows,
    page_token: "",
  };
}

function emptyViewState(): GraphExplorerViewState {
  return {
    title: "Graph Explorer",
    mode: "atlas",
    catalog: DEV_CATALOG,
    limits: DEV_LIMITS,
    atlas: {
      selected_profiles: [],
      sample_size: DEV_LIMITS.atlas_sample_size,
      window_days: DEV_LIMITS.ui_default_window_days,
    },
    investigate: {
      seed: { id: "", kind: "" },
      active_profiles: [],
      window_days: DEV_LIMITS.ui_default_window_days,
      max_neighbors: DEV_LIMITS.ui_default_max_neighbors,
      hops_used: 0,
    },
    selection: { node_id: "", edge_id: "" },
    layout: "force",
    semantic_status_filter: "all",
    node_roles: {},
    suggested_next_hops: [],
    warnings: [],
    dataset_revisions: { nodes: 1, edges: 1, atlas_nodes: 1, atlas_edges: 1 },
  };
}

function flag(name: string): boolean {
  return (
    typeof window !== "undefined" && window.sessionStorage?.getItem(name) === "1"
  );
}

export function buildMockPayload(): MiniAppPayload<GraphExplorerViewState> {
  const forceEmpty = flag("ge_force_empty");
  const forceSample = flag("ge_force_sample");

  const base: MiniAppPayload<GraphExplorerViewState> = {
    type: "INITIAL_LOAD",
    view_id: "dev-view",
    app_id: APP_ID,
    title: "Graph Explorer",
    status: "ready",
    summary_cards: [],
    datasets: {
      nodes: makeDataset("nodes", NODE_COLS, []),
      edges: makeDataset("edges", EDGE_COLS, []),
      atlas_nodes: makeDataset("atlas_nodes", NODE_COLS, []),
      atlas_edges: makeDataset("atlas_edges", EDGE_COLS, []),
      node_evidence: makeDataset("node_evidence", ["node_id", "column", "value"], []),
      edge_evidence: makeDataset("edge_evidence", ["edge_id", "column", "value"], []),
      graph_metrics: makeDataset("graph_metrics", ["metric", "value"], []),
    },
    view_state: emptyViewState(),
    warnings: [],
  };

  if (forceEmpty) return base;

  if (forceSample) {
    base.datasets!.atlas_nodes = makeDataset("atlas_nodes", NODE_COLS, DEV_NODES);
    base.datasets!.atlas_edges = makeDataset("atlas_edges", EDGE_COLS, DEV_EDGES);
    base.view_state = {
      ...emptyViewState(),
      mode: "atlas",
      atlas: {
        selected_profiles: ["gpay_ownership", "token_transfers"],
        sample_size: DEV_LIMITS.atlas_sample_size,
        window_days: DEV_LIMITS.ui_default_window_days,
      },
    };
    return base;
  }

  // Default: seeded INVESTIGATE mode.
  const activeProfiles = [
    "circles_trust", "circles_avatar_balances", "safe_ownership",
    "token_transfers", "lp_in_pool", "pool_contains_token",
  ];
  base.datasets!.nodes = makeDataset("nodes", NODE_COLS, DEV_NODES);
  base.datasets!.edges = makeDataset("edges", EDGE_COLS, DEV_EDGES);
  base.datasets!.graph_metrics = makeDataset(
    "graph_metrics",
    ["metric", "value"],
    [
      ["node_count", DEV_NODES.length],
      ["edge_count", DEV_EDGES.length],
      ["profile_count", activeProfiles.length],
      ["window_days", DEV_LIMITS.ui_default_window_days],
    ],
  );
  base.view_state = {
    ...emptyViewState(),
    mode: "investigate",
    investigate: {
      seed: { id: "0xaaa", kind: "address" },
      active_profiles: activeProfiles,
      window_days: DEV_LIMITS.ui_default_window_days,
      max_neighbors: DEV_LIMITS.ui_default_max_neighbors,
      hops_used: 1,
    },
    selection: { node_id: "0xccc", edge_id: "" },
    node_roles: {
      "0xaaa": { is_safe: 0, is_gpay_wallet: 0, is_ga_user: 0, is_circles_avatar: 0, is_safe_owner: 1, is_lp_provider: 0, has_dune_label: 1, dune_project: "GnosisDAO" },
      "0xccc": { is_safe: 0, is_circles_avatar: 1, circles_avatar_type: "Human", is_safe_owner: 0, has_dune_label: 0 },
    },
    suggested_next_hops: [
      { profile: "bridge_user_flows", label: "Bridge flows", rationale: "addr ↔ bridge", quality_tier: "candidate" },
    ],
  };
  return base;
}
