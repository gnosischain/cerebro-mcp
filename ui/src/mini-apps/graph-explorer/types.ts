// Graph Explorer wire + local types (view_state v2).
//
// The server (src/cerebro_mcp/tools/semantic/graph_explorer/state.py) is the
// single source of truth for limits — they arrive in view_state["limits"].
// There is deliberately NO compile-time MAX_HOPS mirror here; every clamp and
// counter reads the published limits.

export type SemanticStatus = "approved" | "candidate" | "docs_only";
export type StatusFilter = "all" | "approved" | "candidate";
export type GraphMode = "atlas" | "investigate";
export type GraphLayout = "force" | "circular";

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
}

export interface AtlasState {
  selected_profiles: string[];
  /** PER PROFILE sample size for load_graph_atlas_sample. */
  sample_size: number;
  window_days: number;
}

export interface InvestigateState {
  seed: SeedNode;
  active_profiles: string[];
  window_days: number;
  max_neighbors: number;
  hops_used: number;
}

export interface SelectionState {
  node_id: string;
  edge_id: string;
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
  catalog: ProfileCard[];
  limits: Limits;
  atlas: AtlasState;
  investigate: InvestigateState;
  /** Cleared on mode switch by the server. */
  selection: SelectionState;
  layout: GraphLayout;
  semantic_status_filter: StatusFilter;
  node_roles: Record<string, AddressRoles>;
  suggested_next_hops: HopSuggestion[];
  warnings: string[];
  /** Present on EVERY dataset-bearing payload; keys hydration + adoption. */
  dataset_revisions?: Record<string, number>;
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
  column: string;
  value: string;
}
