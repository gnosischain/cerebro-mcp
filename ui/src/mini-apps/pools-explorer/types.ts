// TS mirror of the Pool Liquidity Explorer backend view state
// (src/cerebro_mcp/tools/visualization/pools_explorer.py) plus the dataset
// column projections of every key the app renders. Wire types
// (MiniAppPayload / DatasetDescriptor) live in ../shared/miniAppTypes.ts —
// never redefined here.
//
// DATASET_COLUMNS is the frozen column contract, in server order. The row
// interfaces below are pinned to it at compile time (ColumnContractChecks) and
// the devFixture descriptors are pinned to it at test time — a fixture column
// the server does not emit would hide real bugs (lesson learned on Governance).

import type { TokenOverlay, TokenOverlayStats } from "./model/tokenOverlay";

export type PlxListSection = "overview" | "pools" | "tokens" | "coverage";
export type PlxEntityType = "pool" | "token";
export type PlxSection = PlxListSection | PlxEntityType;

export type PoolClass = "uniswap_v3" | "swapr_v3_algebra" | "balancer_v2" | "balancer_v3";
export type PoolFamily = "cl" | "reserves_only";
export type PlxWindow = "90d" | "1y" | "all";
export type FeeBand = "b100" | "b500" | "b3000" | "b10000" | "bhigh";

/** ClickHouse UInt8 flags arrive as 0/1; JSON fixtures may carry booleans. */
export type Bit = number | boolean;

export const POOL_CLASSES: readonly PoolClass[] = [
  "uniswap_v3", "swapr_v3_algebra", "balancer_v2", "balancer_v3",
];
export const POOL_FAMILIES: readonly PoolFamily[] = ["cl", "reserves_only"];
export const CL_CLASSES: ReadonlySet<string> = new Set(["uniswap_v3", "swapr_v3_algebra"]);

/** Mirrors the backend FEE_BANDS map (pips). */
export const FEE_BANDS: ReadonlyArray<{ id: FeeBand; label: string }> = [
  { id: "b100", label: "≤ 0.01%" },
  { id: "b500", label: "0.01–0.05%" },
  { id: "b3000", label: "0.05–0.30%" },
  { id: "b10000", label: "0.30–1.00%" },
  { id: "bhigh", label: "> 1.00%" },
];

/** Mirrors the backend WINDOWS map (days; 0 = all history). */
export const WINDOWS: ReadonlyArray<{ id: PlxWindow; label: string; days: number }> = [
  { id: "90d", label: "90d", days: 90 },
  { id: "1y", label: "1y", days: 365 },
  { id: "all", label: "All", days: 0 },
];

export function isPoolClass(value: unknown): value is PoolClass {
  return typeof value === "string" && (POOL_CLASSES as readonly string[]).includes(value);
}
export function isPoolFamily(value: unknown): value is PoolFamily {
  return value === "cl" || value === "reserves_only";
}
export function isPlxWindow(value: unknown): value is PlxWindow {
  return value === "90d" || value === "1y" || value === "all";
}
export function isFeeBand(value: unknown): value is FeeBand {
  return typeof value === "string" && FEE_BANDS.some((band) => band.id === value);
}

export interface PlxFilters {
  query: string;
  pool_class: string;
  pool_family: string;
  fee_band: string;
  /** Exact fee in pips; 0 = unset. Never sent together with fee_band. */
  fee: number;
  /** Token address filter (`tok` in the URL — never a key named `token`). */
  token: string;
  live_only: boolean;
  probed_only: boolean;
  sort_by: string;
}

export interface PlxSelectedEntity {
  entity_type: PlxEntityType;
  identifier: string;
  label: string;
}

export interface PlxBreadcrumb {
  label: string;
  entity_type: PlxEntityType;
  identifier: string;
}

export interface PlxSearchCandidate {
  entity_type: PlxEntityType;
  identifier: string;
  label: string;
  role: string;
  evidence_count: number;
  match_rank: number;
}

export interface PlxCoverage {
  basis?: string;
  actual_start?: string | null;
  actual_end?: string | null;
  returned_rows?: number;
  source_rows?: number | null;
  row_cap?: number | null;
  truncated?: boolean;
  mode?: string;
  error?: string;
  warning_codes?: string[];
}

/** One publication clock (`source_freshness` row shape + the stale verdict). */
export interface PlxSourceFreshness {
  latest_snapshot_date: string | null;
  /** The backend names this `anchor_block`; the dataset row `latest_anchor_block`. */
  anchor_block?: number | null;
  latest_anchor_block?: number | null;
  pools_published?: number | null;
  latest_published_at?: string | null;
  stale: boolean;
}

/** Two independent clocks: the CL state job and the reserves job. */
export interface PlxFreshness {
  cl_state: PlxSourceFreshness;
  reserves: PlxSourceFreshness;
}

export type PlxWarningCode =
  | "query_failed"
  | "stale_scope"
  | "as_of_shifted"
  | "pool_below_active_threshold"
  | "reserves_only_pool"
  | "metadata_unresolved"
  | "source_stale"
  | "no_indexed_data"
  // Token-overlay codes. They travel on the `load_pools_token_metadata`
  // PAYLOAD rather than in `view_state.warnings`, so the app also derives
  // them from `token_overlay_stats` (model/tokenOverlay.ts) — listed here so
  // the vocabulary stays in one place and the copy map covers them either way.
  | "token_rpc_unavailable"
  | "token_overlay_pending";

export const WARNING_CODES: readonly PlxWarningCode[] = [
  "query_failed", "stale_scope", "as_of_shifted", "pool_below_active_threshold",
  "reserves_only_pool", "metadata_unresolved", "source_stale", "no_indexed_data",
  "token_rpc_unavailable", "token_overlay_pending",
];

export interface PoolsExplorerViewState {
  section: PlxSection;
  title?: string;
  /** ISO date or "" (latest publication). */
  as_of: string;
  /** "90d" | "1y" | "all" | "" (server default). */
  window: string;
  /** Heatmap window ("1y" default); patched by load_pools_explorer_datasets. */
  heatmap_window: string;
  filters: PlxFilters;
  selected_entity: PlxSelectedEntity | null;
  breadcrumbs: PlxBreadcrumb[];
  search: { query: string; candidates: PlxSearchCandidate[] };
  applied_request_id: number;
  scope_id: string;
  coverage: Record<string, PlxCoverage>;
  coverage_warnings: string[];
  warnings: string[];
  dataset_revisions: Record<string, number>;
  /** `${section}.${group}` → loaded flag. `false` = not loaded (skeleton),
   * `true` = loaded clean, `"partial"` = loaded with at least one dataset
   * failed (error cards render; retry is user-driven). */
  loaded_groups?: Record<string, boolean | "partial">;
  section_fingerprints?: Record<string, string>;
  section_datasets?: Record<string, string[]>;
  section_lru?: string[];
  freshness: PlxFreshness;
  /** Chain-state token metadata patched in by `load_pools_token_metadata`,
   * keyed by lowercase address. Kept SEPARATE from the dataset columns on
   * purpose: an indexer value is verified at a pinned finalized block with a
   * publication behind it, an RPC value is current chain state with neither.
   * A token that could not be read is ABSENT — never present with a null
   * symbol. See model/tokenOverlay.ts. */
  token_overlay?: TokenOverlay;
  token_overlay_stats?: TokenOverlayStats;
}

// ---------------------------------------------------------------------------
// Dataset column projections — FROZEN, in server order.
// ---------------------------------------------------------------------------

export const POOL_DIRECTORY_COLUMNS = [
  "pool_address", "pool_name", "pool_class", "pool_family", "n_assets",
  // `asset_symbols` carries '' and `asset_decimals` -1 for "not observed" —
  // the client builds its own labels from them (never a fabricated decimal).
  "assets", "asset_symbols", "asset_decimals",
  "token0", "token0_symbol", "token0_decimals", "token0_resolved", "token0_label",
  "token1", "token1_symbol", "token1_decimals", "token1_resolved", "token1_label",
  // `has_state` separates "there is no CL state row" from "liquidity is zero".
  "as_of", "has_state", "current_tick", "price_raw", "price_adjusted",
  "liquidity_raw", "liquidity_float", "is_live", "tick_count", "tick_spacing",
  "fee", "fee_band", "ticks_probed",
  "reserves_as_of", "reserve0_raw", "reserve1_raw", "reserve0_units", "reserve1_units",
  "first_published", "last_published", "days_published",
  "deployment_block", "anchor_block",
] as const;

/** pool_detail is a SUPERSET of the directory row but in its own server order
 * (entity identity first, the N-asset reserve arrays beside the pair columns). */
export const POOL_DETAIL_COLUMNS = [
  "pool_address", "entity_label", "pool_name", "pool_class", "pool_family", "pool_id",
  "n_assets", "assets", "asset_symbols", "asset_decimals",
  "token0", "token0_symbol", "token0_decimals", "token0_resolved", "token0_label",
  "token1", "token1_symbol", "token1_decimals", "token1_resolved", "token1_label",
  "as_of", "has_state", "current_tick", "price_raw", "price_adjusted",
  "liquidity_raw", "liquidity_float", "is_live", "tick_count", "tick_spacing",
  "fee", "fee_band", "ticks_probed",
  "reserves_as_of", "reserve_tokens", "reserve_raw",
  "reserve0_raw", "reserve1_raw", "reserve0_units", "reserve1_units",
  "first_published", "last_published", "days_published", "days_live",
  "profile_available_from", "deployment_block", "anchor_block",
] as const;

export const DATASET_COLUMNS = {
  source_freshness: [
    "source", "latest_snapshot_date", "latest_anchor_block", "pools_published", "latest_published_at",
  ],
  pools_summary: [
    "as_of", "anchor_block", "anchor_timestamp", "pools_configured", "pools_configured_cl",
    "pools_configured_reserves_only", "pools_published_cl", "pools_live_cl", "pools_probed",
    "pools_live_unprobed", "reserves_as_of", "pools_with_reserves", "pools_live_reserves_only",
  ],
  pools_by_class_fee: ["pool_class", "pool_family", "fee", "fee_band", "pools", "live_pools", "probed_pools"],
  probe_coverage_split: ["ticks_probed", "is_live", "pools", "median_liquidity_float", "p90_liquidity_float"],
  live_pool_trend: ["bucket", "pools_published_cl", "pools_live_cl", "pools_probed", "pools_published_reserves"],
  concentration_summary: ["metric", "pools_measured", "q25", "median", "q75", "pools_true"],
  range_width_distribution: ["bucket_order", "width_bucket", "ranges", "pools", "share_of_ranges"],
  pool_directory: POOL_DIRECTORY_COLUMNS,
  token_directory: [
    // `token_name`, not `name`: `name` collides with a source column.
    "token_address", "symbol", "token_name", "decimals", "resolution_status", "is_resolved",
    "pools_count", "cl_pools", "reserves_only_pools", "live_pools", "probed_pools", "as_of",
  ],
  coverage_summary: [
    "job_name", "first_snapshot_date", "last_snapshot_date", "days_published", "pools_configured",
    "pools_published_latest", "pools_below_threshold_latest", "publications_total",
  ],
  publication_calendar: [
    "snapshot_date", "job_name", "anchor_block", "pools_published", "pools_below_threshold",
    "pools_probed", "net_sum_zero_passed", "reconciles_passed", "pools_configured_now",
  ],
  missing_days: ["snapshot_date", "job_name", "gap_kind", "pools_published", "expected_pools"],
  metadata_gap: ["dimension", "known", "unknown", "pct_known"],
  pool_detail: POOL_DETAIL_COLUMNS,
  pool_publication_facts: [
    // `observations_total` + `executor_kind`, NOT `universe_size`: that column
    // is 0 for every pool publication, so emitting it would be a fake zero.
    "snapshot_date", "job_name", "anchor_block", "anchor_hash", "block_timestamp", "publication_id",
    "attempt_id", "published_at", "integrity_mode", "block_reference_kind", "executor_kind",
    "observations_total", "checks_passed", "ticks_probed", "net_sum_zero_passed", "reconciles_passed",
  ],
  pool_profile_at: [
    "pool_address", "as_of", "tick_lower", "tick_upper", "width_ticks", "price_lower_raw",
    "price_upper_raw", "price_lower_adjusted", "price_upper_adjusted", "active_liquidity_raw",
    "active_liquidity_float", "is_gap", "contains_current_tick", "is_full_range", "current_tick",
    "distance_ticks", "matches_state_liquidity", "profile_source",
  ],
  pool_profile_concentration: [
    "band", "band_ticks", "tick_weighted_share", "ranges_in_band", "liquidity_at_current_tick_float",
  ],
  pool_ticks_at: [
    "tick", "liquidity_gross_raw", "liquidity_gross_float", "liquidity_net_raw", "liquidity_net_float",
    "fee_growth_outside_0_raw", "fee_growth_outside_1_raw", "price_raw_at_tick", "is_below_current",
  ],
  pool_state_history: [
    "snapshot_date", "anchor_block", "current_tick", "price_raw", "price_adjusted", "liquidity_raw",
    "liquidity_float", "is_live", "tick_count", "fee", "tick_spacing",
    "fee_growth_global_0_raw", "fee_growth_global_1_raw", "ticks_probed",
  ],
  pool_reserves_history: [
    "snapshot_date", "token_address", "token_index", "symbol", "decimals", "balance_raw",
    "balance_float", "balance_units", "anchor_block",
  ],
  pool_fee_growth: [
    "snapshot_date", "prev_snapshot_date", "gap_days", "liquidity_float", "fee_growth_global_0_raw",
    "fee_growth_global_1_raw", "delta_fg0_raw", "delta_fg1_raw", "fees0_raw_est", "fees1_raw_est",
    "fees0_units_est", "fees1_units_est", "ticks_probed",
  ],
  pool_profile_heatmap: [
    "bucket_date", "tick_bucket_lo", "tick_bucket_hi", "liquidity_float", "current_tick", "axis_lo",
    "axis_hi", "tick_step", "date_step_days", "dates_total", "dates_sampled",
  ],
  token_detail: [
    "token_address", "entity_label", "symbol", "token_name", "decimals", "resolution_status", "is_resolved",
    "pools_count", "cl_pools", "reserves_only_pools", "live_pools", "probed_pools", "as_of",
    "reserves_as_of", "total_reserve_raw", "total_reserve_units",
  ],
  token_pools: [
    "pool_address", "pool_name", "pool_class", "pool_family", "has_state", "fee", "fee_band",
    "token_is_token0", "counter_tokens", "counter_labels", "current_tick", "price_raw",
    "price_adjusted", "price_of_token_in_counter", "liquidity_raw", "liquidity_float", "is_live",
    "ticks_probed", "reserve_token_raw", "token_decimals", "reserve_token_units", "reserve_share",
  ],
} as const satisfies Record<string, readonly string[]>;

export type DatasetKey = keyof typeof DATASET_COLUMNS;

// ---------------------------------------------------------------------------
// Typed rows (parsed via model/parseRows.ts). UInt256 columns arrive as
// `*_raw` strings beside a `*_float` Float64; Nullable columns are `| null`.
// ---------------------------------------------------------------------------

export interface SourceFreshnessRow {
  source: string;
  latest_snapshot_date: string | null;
  latest_anchor_block: number | null;
  pools_published: number | null;
  latest_published_at: string | null;
}

export interface PoolsSummaryRow {
  as_of: string | null;
  anchor_block: number | null;
  anchor_timestamp: string | null;
  pools_configured: number;
  pools_configured_cl: number;
  pools_configured_reserves_only: number;
  pools_published_cl: number;
  pools_live_cl: number;
  pools_probed: number;
  pools_live_unprobed: number;
  reserves_as_of: string | null;
  pools_with_reserves: number;
  pools_live_reserves_only: number;
}

export interface ClassFeeRow {
  pool_class: string;
  pool_family: string;
  fee: number | null;
  fee_band: string | null;
  pools: number;
  live_pools: number;
  probed_pools: number;
}

export interface ProbeCoverageRow {
  ticks_probed: Bit;
  is_live: Bit;
  pools: number;
  median_liquidity_float: number | null;
  p90_liquidity_float: number | null;
}

export interface LivePoolTrendRow {
  bucket: string;
  pools_published_cl: number;
  pools_live_cl: number;
  pools_probed: number;
  pools_published_reserves: number;
}

export interface ConcentrationSummaryRow {
  metric: string;
  pools_measured: number;
  q25: number | null;
  median: number | null;
  q75: number | null;
  pools_true: number | null;
}

export interface RangeWidthRow {
  bucket_order: number;
  width_bucket: string;
  ranges: number;
  pools: number;
  share_of_ranges: number | null;
}

export interface PoolDirectoryRow {
  pool_address: string;
  pool_name: string | null;
  pool_class: string;
  pool_family: string;
  n_assets: number;
  token0: string | null;
  token0_symbol: string | null;
  token0_decimals: number | null;
  token0_resolved: Bit;
  token0_label: string;
  token1: string | null;
  token1_symbol: string | null;
  token1_decimals: number | null;
  token1_resolved: Bit;
  token1_label: string;
  assets: string[] | string;
  /** '' where the indexer never observed a symbol. */
  asset_symbols: string[] | string;
  /** -1 where the indexer never observed decimals — never treated as 0. */
  asset_decimals: number[] | string;
  as_of: string | null;
  /** A CL state row exists for this pool at `as_of`. */
  has_state: Bit;
  current_tick: number | null;
  price_raw: number | null;
  price_adjusted: number | null;
  liquidity_raw: string | null;
  liquidity_float: number | null;
  is_live: Bit;
  tick_count: number | null;
  tick_spacing: number | null;
  fee: number | null;
  fee_band: string | null;
  ticks_probed: Bit;
  reserves_as_of: string | null;
  reserve0_raw: string | null;
  reserve1_raw: string | null;
  reserve0_units: number | null;
  reserve1_units: number | null;
  first_published: string | null;
  last_published: string | null;
  days_published: number | null;
  deployment_block: number | null;
  anchor_block: number | null;
}

export interface TokenDirectoryRow {
  token_address: string;
  symbol: string | null;
  token_name: string | null;
  decimals: number | null;
  resolution_status: string;
  is_resolved: Bit;
  pools_count: number;
  cl_pools: number;
  reserves_only_pools: number;
  live_pools: number;
  probed_pools: number;
  as_of: string | null;
}

export interface CoverageSummaryRow {
  job_name: string;
  first_snapshot_date: string | null;
  last_snapshot_date: string | null;
  days_published: number;
  pools_configured: number;
  pools_published_latest: number | null;
  pools_below_threshold_latest: number | null;
  publications_total: number;
}

export interface PublicationCalendarRow {
  snapshot_date: string;
  job_name: string;
  anchor_block: number | null;
  pools_published: number;
  pools_below_threshold: number | null;
  pools_probed: number | null;
  net_sum_zero_passed: number | null;
  reconciles_passed: number | null;
  pools_configured_now: number;
}

export interface MissingDayRow {
  snapshot_date: string;
  job_name: string;
  gap_kind: "absent" | "partial" | string;
  pools_published: number | null;
  expected_pools: number | null;
}

export interface MetadataGapRow {
  dimension: string;
  known: number;
  unknown: number;
  pct_known: number | null;
}

export interface PoolDetailRow extends PoolDirectoryRow {
  /** Class + short address — never a symbol (symbols are untrusted text). */
  entity_label: string;
  days_live: number | null;
  pool_id: string | null;
  /** First probed publication; null when the pool was never probed. */
  profile_available_from: string | null;
  /** N-asset reserves (Balancer), aligned pairwise. */
  reserve_tokens: string[] | string;
  reserve_raw: string[] | string;
}

export interface PublicationFactsRow {
  snapshot_date: string;
  job_name: string;
  anchor_block: number | null;
  anchor_hash: string | null;
  block_timestamp: string | null;
  publication_id: string | null;
  attempt_id: string | number | null;
  published_at: string | null;
  integrity_mode: string | null;
  block_reference_kind: string | null;
  executor_kind: string | null;
  observations_total: number | null;
  checks_passed: string[] | string;
  ticks_probed: Bit;
  net_sum_zero_passed: Bit | null;
  reconciles_passed: Bit | null;
}

export interface ProfileRangeRow {
  pool_address: string;
  as_of: string;
  tick_lower: number;
  tick_upper: number;
  width_ticks: number;
  price_lower_raw: number | null;
  price_upper_raw: number | null;
  price_lower_adjusted: number | null;
  price_upper_adjusted: number | null;
  active_liquidity_raw: string | null;
  active_liquidity_float: number | null;
  is_gap: Bit;
  contains_current_tick: Bit;
  is_full_range: Bit;
  current_tick: number | null;
  distance_ticks: number | null;
  matches_state_liquidity: Bit | null;
  profile_source: string;
}

export interface ProfileConcentrationRow {
  band: string;
  band_ticks: number | null;
  tick_weighted_share: number | null;
  ranges_in_band: number | null;
  liquidity_at_current_tick_float: number | null;
}

export interface TickRow {
  tick: number;
  liquidity_gross_raw: string | null;
  liquidity_gross_float: number | null;
  liquidity_net_raw: string | null;
  liquidity_net_float: number | null;
  fee_growth_outside_0_raw: string | null;
  fee_growth_outside_1_raw: string | null;
  price_raw_at_tick: number | null;
  is_below_current: Bit;
}

export interface StateHistoryRow {
  snapshot_date: string;
  anchor_block: number | null;
  current_tick: number | null;
  price_raw: number | null;
  price_adjusted: number | null;
  liquidity_raw: string | null;
  liquidity_float: number | null;
  is_live: Bit;
  tick_count: number | null;
  fee: number | null;
  tick_spacing: number | null;
  ticks_probed: Bit;
  fee_growth_global_0_raw: string | null;
  fee_growth_global_1_raw: string | null;
}

export interface ReservesHistoryRow {
  snapshot_date: string;
  token_address: string;
  token_index: number;
  symbol: string | null;
  decimals: number | null;
  balance_raw: string | null;
  balance_float: number | null;
  balance_units: number | null;
  anchor_block: number | null;
}

export interface FeeGrowthRow {
  snapshot_date: string;
  prev_snapshot_date: string | null;
  gap_days: number | null;
  liquidity_float: number | null;
  fee_growth_global_0_raw: string | null;
  fee_growth_global_1_raw: string | null;
  delta_fg0_raw: string | null;
  delta_fg1_raw: string | null;
  fees0_raw_est: string | null;
  fees1_raw_est: string | null;
  fees0_units_est: number | null;
  fees1_units_est: number | null;
  ticks_probed: Bit;
}

export interface HeatmapBucketRow {
  bucket_date: string;
  tick_bucket_lo: number;
  tick_bucket_hi: number;
  liquidity_float: number | null;
  current_tick: number | null;
  axis_lo: number;
  axis_hi: number;
  tick_step: number;
  date_step_days: number;
  dates_total: number;
  dates_sampled: number;
}

export interface TokenDetailRow {
  token_address: string;
  entity_label: string;
  symbol: string | null;
  token_name: string | null;
  decimals: number | null;
  resolution_status: string;
  is_resolved: Bit;
  pools_count: number;
  cl_pools: number;
  reserves_only_pools: number;
  live_pools: number;
  probed_pools: number;
  as_of: string | null;
  reserves_as_of: string | null;
  total_reserve_raw: string | null;
  total_reserve_units: number | null;
}

export interface TokenPoolRow {
  pool_address: string;
  pool_name: string | null;
  pool_class: string;
  pool_family: string;
  has_state: Bit;
  fee: number | null;
  fee_band: string | null;
  token_is_token0: Bit | null;
  counter_tokens: string[] | string;
  counter_labels: string[] | string;
  current_tick: number | null;
  price_raw: number | null;
  price_adjusted: number | null;
  price_of_token_in_counter: number | null;
  liquidity_raw: string | null;
  liquidity_float: number | null;
  is_live: Bit;
  ticks_probed: Bit;
  reserve_token_raw: string | null;
  token_decimals: number | null;
  reserve_token_units: number | null;
  reserve_share: number | null;
}

// ---------------------------------------------------------------------------
// Compile-time pin: every row interface's keys equal its projection exactly.
// A drift in either direction fails `tsc` (Type 'false' ... 'true').
// ---------------------------------------------------------------------------

type SameKeys<Row, Cols extends readonly string[]> =
  Exclude<keyof Row, Cols[number]> extends never
    ? Exclude<Cols[number], keyof Row> extends never ? true : false
    : false;
type Expect<T extends true> = T;

export type ColumnContractChecks = [
  Expect<SameKeys<SourceFreshnessRow, typeof DATASET_COLUMNS.source_freshness>>,
  Expect<SameKeys<PoolsSummaryRow, typeof DATASET_COLUMNS.pools_summary>>,
  Expect<SameKeys<ClassFeeRow, typeof DATASET_COLUMNS.pools_by_class_fee>>,
  Expect<SameKeys<ProbeCoverageRow, typeof DATASET_COLUMNS.probe_coverage_split>>,
  Expect<SameKeys<LivePoolTrendRow, typeof DATASET_COLUMNS.live_pool_trend>>,
  Expect<SameKeys<ConcentrationSummaryRow, typeof DATASET_COLUMNS.concentration_summary>>,
  Expect<SameKeys<RangeWidthRow, typeof DATASET_COLUMNS.range_width_distribution>>,
  Expect<SameKeys<PoolDirectoryRow, typeof DATASET_COLUMNS.pool_directory>>,
  Expect<SameKeys<TokenDirectoryRow, typeof DATASET_COLUMNS.token_directory>>,
  Expect<SameKeys<CoverageSummaryRow, typeof DATASET_COLUMNS.coverage_summary>>,
  Expect<SameKeys<PublicationCalendarRow, typeof DATASET_COLUMNS.publication_calendar>>,
  Expect<SameKeys<MissingDayRow, typeof DATASET_COLUMNS.missing_days>>,
  Expect<SameKeys<MetadataGapRow, typeof DATASET_COLUMNS.metadata_gap>>,
  Expect<SameKeys<PoolDetailRow, typeof DATASET_COLUMNS.pool_detail>>,
  Expect<SameKeys<PublicationFactsRow, typeof DATASET_COLUMNS.pool_publication_facts>>,
  Expect<SameKeys<ProfileRangeRow, typeof DATASET_COLUMNS.pool_profile_at>>,
  Expect<SameKeys<ProfileConcentrationRow, typeof DATASET_COLUMNS.pool_profile_concentration>>,
  Expect<SameKeys<TickRow, typeof DATASET_COLUMNS.pool_ticks_at>>,
  Expect<SameKeys<StateHistoryRow, typeof DATASET_COLUMNS.pool_state_history>>,
  Expect<SameKeys<ReservesHistoryRow, typeof DATASET_COLUMNS.pool_reserves_history>>,
  Expect<SameKeys<FeeGrowthRow, typeof DATASET_COLUMNS.pool_fee_growth>>,
  Expect<SameKeys<HeatmapBucketRow, typeof DATASET_COLUMNS.pool_profile_heatmap>>,
  Expect<SameKeys<TokenDetailRow, typeof DATASET_COLUMNS.token_detail>>,
  Expect<SameKeys<TokenPoolRow, typeof DATASET_COLUMNS.token_pools>>,
];
