// Graph Explorer wire + local types (view_state v2).
//
// The server (src/cerebro_mcp/tools/semantic/graph_explorer/state.py) is the
// single source of truth for limits — they arrive in view_state["limits"].
// There is deliberately NO compile-time MAX_HOPS mirror here; every clamp and
// counter reads the published limits.

export type SemanticStatus = "approved" | "candidate" | "docs_only";
export type StatusFilter = "all" | "approved" | "candidate";
export type GraphMode =
  | "atlas"
  | "investigate"
  | "timeline"
  | "flows"
  | "transactions";
export type FlowDirection = "out" | "in" | "both";
export type GraphLayout = "force" | "circular";
export type TimelineGrain = "day" | "week" | "month";
export type TemporalShape = "flow" | "state" | "interval" | "static";
export type RelationshipTime =
  | "event"
  | "state_at"
  | "interval"
  | "current_snapshot";

export interface ProfileCard {
  profile: string;
  model_name: string;
  module: string;
  description: string;
  source_kind: string;
  target_kind: string;
  semantic_status: SemanticStatus;
  quality_tier: string;
  question_synonyms: string[];
  semantic_source_file: string;
  time_aware: boolean;
  directed?: boolean;
  weight_column?: string | null;
  weight_unit?: string;
  sector?: string;
  freshness_sla?: string;
  coverage_note?: string;
  temporal_semantics?: RelationshipTime;
  temporal_shape?: TemporalShape;
}

export interface SeedNode {
  id: string;
  kind: string;
}

/** Server-published limits/defaults (view_state["limits"]). */
export interface Limits {
  max_hops: number;
  bfs_node_cap: number;
  default_expand_depth: number;
  ui_default_window_days: number;
  ui_default_max_neighbors: number;
  atlas_sample_size: number;
  flows_default_hops?: number;
  flows_max_hops?: number;
  flows_default_min_usd?: number;
  flows_default_range_days?: number;
  flows_max_edges?: number;
}

export interface AtlasState {
  selected_profiles: string[];
  /** PER PROFILE sample size for load_graph_atlas_sample. */
  sample_size: number;
  window_days: number;
  scope?: ForensicScope;
}

/** Inspect-only catalog sample. This namespace and its datasets are separate
 * from AtlasState so opening a card can never imply that the relationship was
 * applied to the analyst's graph. */
export interface AtlasPreviewState {
  profile: string;
  sample_size: number;
  window_days: number;
  scope?: ForensicScope;
  warnings: string[];
}

export interface InvestigateState {
  seed: SeedNode;
  active_profiles: string[];
  window_days: number;
  max_neighbors: number;
  hops_used: number;
  scope?: ForensicScope;
}

/** Server-owned timeline namespace. Cursor/playing/speed are CLIENT-LOCAL
 * by design — playback must never round-trip per step. */
export interface TimelineState {
  anchor: SeedNode;
  scope: "" | "investigate" | "seed" | "money";
  /** All applied Money Trail seeds; anchor remains the first for legacy UI. */
  seed_ids?: string[];
  profiles: string[];
  direction?: FlowDirection;
  tokens?: string[];
  min_usd?: number;
  node_budget?: number;
  grain: TimelineGrain;
  range_days: number;
  /** CH-bucketed ISO dates; the client steps the axis from these. */
  range_start: string;
  range_end: string;
  bucket_count: number;
  window_buckets: number;
  profile_shapes: Record<string, TemporalShape>;
  forensic_scope?: ForensicScope;
}

/** Top tokens on the traced flow graph, USD-desc (server-capped). */
export interface FlowTokenEntry {
  token_address: string;
  symbol: string;
  amount_usd: number;
}

export type ForensicScopeStatus = "ready" | "partial" | "failed";
export type ForensicSourceKind = "rpc" | "chain" | "dbt_aggregate";
export type ForensicSourceRole = "primary" | "discovery" | "enrichment";

export interface ForensicSource {
  kind: ForensicSourceKind | string;
  name: string;
  role: ForensicSourceRole | string;
  status: string;
  horizon: string | number | null;
  fetched_at: string;
  error?: string;
  contract_status?: string;
  horizon_basis?: string;
  freshness_note?: string;
  model_version?: string;
  manifest_version?: string;
}

export interface TokenUniverse {
  addresses: string[];
  count: number;
  as_of: string;
  source: string;
  sha256: string;
}

export interface ForensicCoverageCount {
  shown: number | null;
  /** Null is unknown; zero means an independently verified zero. */
  total: number | null;
}

export interface ForensicScope {
  schema_version?: 2;
  scope_id: string;
  request_id: number;
  chain_id?: number;
  query_kind?: string;
  evidence_class?: string;
  predicate?: {
    subjects: string[];
    t0: string | null;
    t1: string | null;
    as_of: string | null;
  };
  status: ForensicScopeStatus;
  window: { t0: string | null; t1: string | null; source: string };
  /** Conservative answering-source watermark. Never substitute result activity. */
  data_horizon: string | number | null;
  /** Latest activity actually present in this admitted result set. */
  result_observed_through?: string | number | null;
  sources: ForensicSource[];
  coverage: {
    rows: ForensicCoverageCount;
    nodes: ForensicCoverageCount;
    edges: ForensicCoverageCount;
    usd: {
      known: number | null;
      total: number | null;
      unknown_rows: number;
    };
  };
  truncation: { truncated: boolean; rule: string | null };
  coverage_note?: string | null;
  residuals: string[];
  warnings: string[];
  verification: { status: string; method: string | null };
  token_universe?: TokenUniverse;
  app_commit?: string;
  dbt_manifest_sha256?: string;
  retrieved_at?: string;
  result_row_hash?: string | null;
  /** Mode-specific compatibility/extension fields remain explicitly typed at
   * their consumers while the shared provenance core stays stable. */
  [key: string]: unknown;
}

/** Server-owned transactions namespace. tx_hashes/seed/counts/scope are
 * server-owned; `scope` in particular must never be client-writable, or the
 * app could be made to claim a coverage it does not have. */
/** A chain the server can actually answer for — built from the RPC endpoints
 * that are configured, so the picker never offers a dead option. */
export interface ChainOption {
  chain_id: number;
  name: string;
  native_symbol: string;
  explorer: string;
  icon_url: string;
  /** Address discovery needs the indexed execution tables (Gnosis only);
   * receipts by hash work on every configured chain. */
  supports_address_discovery: boolean;
}

export interface TransactionsState {
  chain_id?: number;
  chain_options?: ChainOption[];
  query?: {
    kind: "hash" | "address" | "money_edge" | "follow";
    hashes: string[];
    address: string | null;
    counterparties: string[];
    tokens: string[];
    window: { t0: string; t1: string; source: string } | null;
    activity_kinds?: Array<"direct" | "erc20">;
    cursor?: string | null;
    page_size?: number;
  };
  results?: {
    hashes: string[];
    selected_hash: string | null;
  };
  last_attempt?: {
    request_id: number;
    status: "pending" | "failed";
    elapsed_ms: number | null;
    error_code: string | null;
    message: string | null;
    retryable: boolean;
  } | null;
  tx_hashes?: string[];
  seed?: string;
  /** Addresses already followed forward — marks the trail taken. */
  expanded?: string[];
  counterparties?: string[];
  range_days?: number;
  /** Exact applied discovery window when opened from a Money Trail edge. */
  t0?: string;
  t1?: string;
  max_txs?: number;
  tokens?: string[];
  min_usd?: number;
  tx_count?: number;
  leg_count?: number;
  /** Machine-readable coverage: rows returned vs rows that EXIST, the window
   * actually applied, the data horizon, and the residuals this relation
   * cannot see. `exact` is true only when legs_returned === legs_total. */
  scope?: ForensicScope;
  discovery_scope?: ForensicScope;
  receipt_scope?: ForensicScope;
  discovery_coverage?: {
    complete: boolean;
    total_exact: number | null;
    total_lower_bound: number;
    next_cursor: string | null;
    scanned_ranges: Array<{ t0: string; t1: string }>;
    uncovered_ranges: Array<{ t0: string; t1: string; reason: string }>;
    older_history_unscanned: boolean;
  };
}

/** Server-owned flows namespace. Seeds/t0/t1/counts/expanded/token_catalog
 * are server-owned; only direction/hops/range_days/min_usd/tokens/
 * include_bridges are client knobs (the patch schema rejects the rest). */
export interface FlowsState {
  seeds: string[];
  direction: FlowDirection;
  hops: number;
  range_days: number;
  /** Resolved ISO datetimes actually used by the trace. */
  t0: string;
  t1: string;
  min_usd: number;
  tokens: string[];
  include_bridges: boolean;
  node_count: number;
  edge_count: number;
  truncated: boolean;
  truncated_hops: string[];
  /** {node_id: ["out","in"]} — per-node Trace bookkeeping (merge loads). */
  expanded: Record<string, string[]>;
  token_catalog: FlowTokenEntry[];
  scope?: ForensicScope;
}

export interface SelectionState {
  node_id: string;
  edge_id: string;
  /** Monotonic focus revision echoed by update_graph_explorer_focus. */
  request_id?: number;
}

export interface HopSuggestion {
  profile: string;
  label: string;
  rationale: string;
  quality_tier: string;
  target_kind?: string;
}

export interface AddressRoles {
  is_safe?: number;
  is_gpay_wallet?: number;
  is_ga_user?: number;
  controls_gpay_wallet?: string | null;
  is_circles_avatar?: number;
  circles_avatar_type?: string | null;
  is_circles_wrapper?: number;
  is_safe_owner?: number;
  is_lp_provider?: number;
  pool_protocol?: string | null;
  is_pool?: number;
  is_lending_user?: number;
  is_validator_depositor?: number;
  has_dune_label?: number;
  dune_project?: string | null;
}

/** view_state v2 — per-mode namespaces sharing one canvas. */
export interface GraphExplorerViewState {
  title: string;
  mode: GraphMode;
  /** Server-owned mode authority — advances only on explicit mode commands.
   * The client adopts `mode`/`selection` only on the initial load or when
   * this increases (see adoptServerState). */
  mode_revision?: number;
  catalog: ProfileCard[];
  limits: Limits;
  atlas: AtlasState;
  atlas_preview?: AtlasPreviewState;
  investigate: InvestigateState;
  timeline?: TimelineState;
  flows?: FlowsState;
  transactions?: TransactionsState;
  /** Cleared on mode switch by the server. */
  selection: SelectionState;
  layout: GraphLayout;
  semantic_status_filter: StatusFilter;
  node_roles: Record<string, AddressRoles>;
  suggested_next_hops: HopSuggestion[];
  warnings: string[];
  /** Present on EVERY dataset-bearing payload; keys hydration + adoption. */
  dataset_revisions?: Record<string, number>;
  /** Dataset key → provenance contract id answering that dataset. */
  dataset_scopes?: Record<string, string>;
  focus_scope?: ForensicScope;
}

// ---- Parsed dataset row models --------------------------------------------

export interface GraphNodeRow {
  id: string;
  kind: string;
  label: string;
  profiles: string[];
}

export interface GraphEdgeRow {
  id: string;
  source: string;
  target: string;
  profile: string;
  weight: number;
  edge_count: number;
  directed: boolean;
}

export interface EvidenceRow {
  /** Node id or edge id this evidence was computed for. */
  ownerId: string;
  column: string;
  value: string;
  subjectKind: "node" | "edge";
  /** Focus request that produced this row. Zero is legacy/unattributed. */
  requestId: number;
}

/** Client-authoritative focus intent. Evidence is renderable only when all
 * three fields match; a late server response can therefore never be shown
 * under a newer selection. */
export interface EvidenceExpectation {
  subjectKind: "node" | "edge";
  subjectId: string;
  requestId: number;
}

/** Profile filters have explicit authority. An empty unresolved list means
 * the client has not adopted the server's set yet; an empty applied list is
 * an instruction to render an empty graph. */
export type ProfileSelectionPhase = "unresolved" | "draft" | "applied";

export interface ProfileSelection {
  phase: ProfileSelectionPhase;
  draft: string[];
  applied: string[];
  scopeId: string | null;
}

/** Draft/pending/applied state shared by every server-backed control group. */
export interface ScopedControls<T> {
  draft: T;
  applied: T | null;
  pending: {
    requestId: number;
    snapshot: T;
  } | null;
  scopeId: string | null;
  error: string | null;
}
