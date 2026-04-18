export type SemanticStatus = "approved" | "candidate" | "docs_only";
export type StatusFilter = "all" | "approved" | "candidate";

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

export interface GraphExplorerState {
  title: string;
  catalog: ProfileCard[];
  selected_profiles: string[];
  seed_node: SeedNode;
  selected_node_id: string;
  selected_edge_id: string;
  relation_types: string[];
  layout: "force" | "circular";
  transfer_window_days: number;
  max_neighbors: number;
  hops: number;
  semantic_status_filter: StatusFilter;
  suggested_next_hops: HopSuggestion[];
  node_roles: Record<string, AddressRoles>;
  warnings: string[];
  /** "seed" = seeded from a real node, "sample" = previewing a profile without a seed. */
  mode?: "seed" | "sample";
}

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
