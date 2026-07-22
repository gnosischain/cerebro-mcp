export type CowSection =
  | "live"
  | "overview"
  | "markets"
  | "trades"
  | "orders"
  | "auctions"
  | "solvers"
  | "traders"
  | "patterns"
  | "entity";

export type EnvironmentScope = "production" | "testnet";
export type EntityType = "order" | "transaction" | "address" | "token" | "auction" | "solver";

export interface ExplorerInfo {
  provider: "blockscout" | "bscscan" | "avalanche" | "plasmascan";
  brand: string;
  base_url: string;
  transaction_url_template: string;
  address_url_template: string;
  token_url_template: string;
}

export interface ChainOption {
  chain_id: number;
  name: string;
  native_symbol: string;
  environment: EnvironmentScope;
  explorer: ExplorerInfo;
  /** CoinGecko asset-platform image; empty string → monogram fallback. */
  icon_url?: string;
}

export interface CowPair {
  base: string;
  quote: string;
  base_symbol: string;
  quote_symbol: string;
  base_decimals?: number | null;
  quote_decimals?: number | null;
}

export interface CowDateRange {
  kind: "relative" | "absolute" | "all";
  anchor: "latest_indexed" | "explicit";
  window_days: number | null;
  start_at: string;
  end_at: string;
}

export interface Coverage {
  basis?: string;
  requested_start?: string | null;
  requested_end?: string | null;
  actual_start?: string | null;
  actual_end?: string | null;
  anchor?: string;
  latest_source_observation?: string | null;
  fetched_at?: string | null;
  checkpoint_block?: number | null;
  checkpoint_timestamp?: string | null;
  returned_rows?: number;
  source_rows?: number | null;
  row_cap?: number | null;
  truncated?: boolean;
  mode?: string;
  warning_codes?: string[];
}

export interface SearchCandidate {
  entity_type: EntityType;
  identifier: string;
  chain_id: number;
  chain_name: string;
  role: string;
  evidence_count: number;
}

export interface SelectedEntity {
  entity_type: EntityType;
  identifier: string;
  chain_id: number;
  chain_name: string;
}

export interface CowExplorerViewState {
  section: CowSection;
  environment_scope: EnvironmentScope;
  environment: EnvironmentScope;
  chain_id: number;
  chain_name: string;
  chain_options: ChainOption[];
  explorer: ExplorerInfo | null;
  pair: CowPair;
  interval: string;
  date_range: CowDateRange;
  filters: { status: string; owner: string; solver: string; token: string };
  selected_entity: SelectedEntity | null;
  breadcrumbs: Array<{ label: string; entity_type: EntityType; identifier: string; chain_id: number }>;
  search: { query: string; candidates: SearchCandidate[] };
  applied_request_id: number;
  scope_id: string;
  coverage: Record<string, Coverage>;
  coverage_warnings: string[];
  warnings: string[];
  dataset_revisions: Record<string, number>;
  /** v2 deferred-load bookkeeping: `${section}.${group}` → loaded flag.
   * `false` = not loaded (skeleton), `true` = loaded clean,
   * `"partial"` = loaded but at least one dataset failed (error cards render;
   * NOT auto-retried — retry is user-driven or the live poll). */
  loaded_groups?: Record<string, boolean | "partial">;
  /** Scope fingerprint each retained section's datasets were loaded under. */
  section_fingerprints?: Record<string, string>;
  /** Dataset keys currently retained per section (server-side LRU). */
  section_datasets?: Record<string, string[]>;
  section_lru?: string[];
  /** Async CoinGecko icon overlay: chain_id (string) → token → icon URL. */
  icon_overlay?: Record<string, Record<string, string>>;
  /** Persisted dataset titles for keys whose specs are no longer in scope. */
  dataset_titles?: Record<string, string>;
  title?: string;
}

export interface CandleRow {
  bucket: string;
  open: number;
  close: number;
  low: number;
  high: number;
  vwap: number;
  baseVolume: number;
  quoteVolume: number;
  fills: number;
}

export interface DepthRow {
  side: "bid" | "ask";
  price: number;
  baseQuantity: number;
  intents: number;
}

export interface FlowLink {
  source: string;
  target: string;
  value: number;
}

export interface ReferencePriceRow {
  bucket: string;
  price: number;
  sourceObservedAt: string;
}

export type ExecutionNodeKind = "order" | "fill" | "token" | "transaction" | "interaction" | "auction" | "actor";

export interface ExecutionGraphNode {
  id: string;
  kind: ExecutionNodeKind;
  label: string;
  subtitle?: string;
  entityType?: EntityType;
  identifier?: string;
  role?: string;
  evidenceSource: string;
  x: number;
  y: number;
}

export interface ExecutionGraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  label?: string;
  evidenceSource: string;
  scope: "direct" | "auction_scoped";
}

export interface ExecutionGraphModel {
  nodes: ExecutionGraphNode[];
  edges: ExecutionGraphEdge[];
}
