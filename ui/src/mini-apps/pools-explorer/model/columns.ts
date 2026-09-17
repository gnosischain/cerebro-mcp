// Column-display policy for Pool Liquidity Explorer tables.
//
// The server ships helper columns next to every display column (`<x>_symbol`,
// `<x>_decimals`, `<x>_raw`, `<x>_label`, `<x>_resolved`) which composed
// cells CONSUME. This module decides which columns render as their own <td>,
// what header label they get, what cell kind renders them, and which entity a
// click opens. Datasets without an explicit config still get the default
// policy, so a new dataset can never regress into a raw column dump.

import type { PlxEntityType } from "../types";

export type CellKind =
  | "pool"
  | "token"
  | "tokenList"
  | "class"
  | "family"
  | "fee"
  | "feeBand"
  | "price"
  | "liquidity"
  | "amount"
  | "int"
  | "share"
  | "date"
  | "time"
  | "bool"
  | "probe"
  | "tick"
  | "address"
  | "hash"
  | "list"
  | "raw"
  | "text";

export interface ColumnSpec {
  key: string;
  label?: string;
  kind?: CellKind;
  entity?: PlxEntityType;
  hidden?: boolean;
}

//: Helper columns absorbed by composed cells — hidden unless a config
//: explicitly re-enables one.
//: `has_state` is hidden as a COLUMN but not swallowed: the `is_live` cell
//: renders "no state row" from it, which is the distinction that matters
//: (a pool with no CL state row is not a pool whose liquidity is zero).
const DEFAULT_HIDDEN_RE =
  /(?:_symbol|_symbols|_decimals|_resolved|_label|_labels)$|^(?:assets|reserve_tokens|reserve_raw|has_state|pool_id|profile_source|entity_label|counter_labels|prev_snapshot_date)$/;

//: `*_raw` twins of a `*_float` / `*_units` column are hidden; a bare raw
//: column with no display twin stays visible (rendered as raw units).
const RAW_TWIN_RE = /^(.*)_raw$/;

export function defaultHidden(name: string, siblings: readonly string[]): boolean {
  if (DEFAULT_HIDDEN_RE.test(name)) return true;
  const raw = name.match(RAW_TWIN_RE);
  if (raw) {
    const base = raw[1];
    return siblings.includes(`${base}_float`) || siblings.includes(`${base}_units`)
      || siblings.includes(`${base}_units_est`) || siblings.includes(`${base}_adjusted`);
  }
  return false;
}

const TOKEN_COLUMN_RE = /^(?:token|token0|token1|token_address)$/;
const DATE_COLUMN_RE =
  /(?:_date|_published|_from|_at$)|^(?:as_of|reserves_as_of|bucket|snapshot_date|first_published|last_published|profile_available_from)$/;
const TIME_COLUMN_RE = /(?:_at|_timestamp)$/;
const SHARE_COLUMN_RE = /(?:_share|share_of_ranges|tick_weighted_share|pct_known|reserve_share)$/;
const LIQUIDITY_COLUMN_RE = /^(?:liquidity_float|active_liquidity_float|liquidity_gross_float|liquidity_net_float|median_liquidity_float|p90_liquidity_float|liquidity_at_current_tick_float|balance_float)$/;
const PRICE_COLUMN_RE = /^(?:price_raw|price_adjusted|price_lower_raw|price_upper_raw|price_lower_adjusted|price_upper_adjusted|price_raw_at_tick|price_of_token_in_counter)$/;
const UNITS_COLUMN_RE = /(?:_units|_units_est)$/;
const BOOL_COLUMN_RE = /^(?:is_live|is_gap|contains_current_tick|is_full_range|matches_state_liquidity|is_below_current|is_resolved|token_is_token0|net_sum_zero_passed|reconciles_passed)$/;
const TICK_COLUMN_RE = /^(?:tick|tick_lower|tick_upper|current_tick|width_ticks|distance_ticks|tick_spacing|band_ticks|tick_bucket_lo|tick_bucket_hi|axis_lo|axis_hi|tick_step)$/;
const INT_COLUMN_RE = /^(?:pools|live_pools|probed_pools|pools_[a-z_]+|ranges|ranges_in_band|tick_count|n_assets|days_published|days_live|anchor_block|deployment_block|latest_anchor_block|universe_size|known|unknown|expected_pools|publications_total|gap_days|token_index|bucket_order|dates_total|dates_sampled|date_step_days|cl_pools|reserves_only_pools|decimals|evidence_count|attempt_id)$/;

/** Heuristic cell kind for columns without an explicit config entry. */
export function kindForColumn(name: string): CellKind {
  if (name === "pool_address") return "pool";
  if (TOKEN_COLUMN_RE.test(name)) return "token";
  if (name === "counter_tokens") return "tokenList";
  if (name === "pool_class") return "class";
  if (name === "pool_family") return "family";
  if (name === "fee") return "fee";
  if (name === "fee_band") return "feeBand";
  if (name === "ticks_probed") return "probe";
  if (name === "checks_passed") return "list";
  if (name === "anchor_hash" || name === "publication_id") return "hash";
  if (BOOL_COLUMN_RE.test(name)) return "bool";
  if (PRICE_COLUMN_RE.test(name)) return "price";
  if (LIQUIDITY_COLUMN_RE.test(name)) return "liquidity";
  if (UNITS_COLUMN_RE.test(name)) return "amount";
  if (SHARE_COLUMN_RE.test(name)) return "share";
  if (TICK_COLUMN_RE.test(name)) return "tick";
  if (TIME_COLUMN_RE.test(name)) return "time";
  if (DATE_COLUMN_RE.test(name)) return "date";
  if (INT_COLUMN_RE.test(name)) return "int";
  if (RAW_TWIN_RE.test(name)) return "raw";
  return "text";
}

/** Heuristic click-through entity. */
export function entityForColumn(name: string): PlxEntityType | null {
  if (name === "pool_address") return "pool";
  if (TOKEN_COLUMN_RE.test(name) || name === "counter_tokens") return "token";
  return null;
}

const LABEL_OVERRIDES: Record<string, string> = {
  pool_address: "Pool",
  pool_name: "Name",
  pool_class: "Class",
  pool_family: "Family",
  fee: "Fee",
  fee_band: "Fee band",
  tick_spacing: "Spacing",
  n_assets: "Assets",
  token0: "Token 0",
  token1: "Token 1",
  token_address: "Token",
  as_of: "As of",
  current_tick: "Tick",
  price_raw: "Price (raw)",
  price_adjusted: "Price",
  liquidity_float: "Liquidity (L)",
  active_liquidity_float: "Active L",
  liquidity_gross_float: "Gross L",
  liquidity_net_float: "Net L",
  is_live: "Live",
  tick_count: "Ticks",
  ticks_probed: "Probe",
  reserves_as_of: "Reserves as of",
  reserve0_units: "Reserve 0",
  reserve1_units: "Reserve 1",
  reserve0_raw: "Reserve 0 (raw)",
  reserve1_raw: "Reserve 1 (raw)",
  first_published: "First published",
  last_published: "Last published",
  days_published: "Days published",
  deployment_block: "Deployed at block",
  anchor_block: "Anchor block",
  days_live: "Days live",
  profile_available_from: "Profile since",
  symbol: "Symbol",
  token_name: "Name",
  executor_kind: "Executor",
  observations_total: "Observations",
  price_of_token_in_counter: "Price in counter",
  token_decimals: "Decimals",
  decimals: "Decimals",
  resolution_status: "Metadata",
  is_resolved: "Resolved",
  pools_count: "Pools",
  cl_pools: "CL pools",
  reserves_only_pools: "Reserves-only pools",
  live_pools: "Live pools",
  probed_pools: "Probed pools",
  job_name: "Job",
  first_snapshot_date: "First snapshot",
  last_snapshot_date: "Last snapshot",
  pools_configured: "Configured",
  pools_published_latest: "Published (latest)",
  pools_below_threshold_latest: "Below threshold (latest)",
  publications_total: "Publications",
  snapshot_date: "Snapshot",
  pools_published: "Published",
  pools_below_threshold: "Below threshold",
  pools_probed: "Probed",
  net_sum_zero_passed: "Net-sum-zero",
  reconciles_passed: "Reconciles",
  pools_configured_now: "Configured now",
  gap_kind: "Gap",
  expected_pools: "Expected",
  dimension: "Dimension",
  known: "Known",
  unknown: "Unknown",
  pct_known: "Known %",
  tick_lower: "Lower tick",
  tick_upper: "Upper tick",
  width_ticks: "Width (ticks)",
  price_lower_raw: "Lower price (raw)",
  price_upper_raw: "Upper price (raw)",
  price_lower_adjusted: "Lower price",
  price_upper_adjusted: "Upper price",
  is_gap: "Gap",
  contains_current_tick: "Current",
  is_full_range: "Full range",
  distance_ticks: "Distance (ticks)",
  matches_state_liquidity: "Matches state",
  band: "Band",
  band_ticks: "Band (ticks)",
  tick_weighted_share: "Share (tick-weighted)",
  ranges_in_band: "Ranges in band",
  liquidity_at_current_tick_float: "L at current tick",
  tick: "Tick",
  fee_growth_outside_0_raw: "Fee growth outside 0 (raw)",
  fee_growth_outside_1_raw: "Fee growth outside 1 (raw)",
  price_raw_at_tick: "Price at tick (raw)",
  is_below_current: "Below current",
  fee_growth_global_0_raw: "Fee growth global 0 (raw)",
  fee_growth_global_1_raw: "Fee growth global 1 (raw)",
  token_index: "Index",
  balance_float: "Balance",
  balance_units: "Balance (units)",
  balance_raw: "Balance (raw)",
  gap_days: "Gap (days)",
  delta_fg0_raw: "Δ fee growth 0 (raw)",
  delta_fg1_raw: "Δ fee growth 1 (raw)",
  fees0_raw_est: "Fees 0 est. (raw)",
  fees1_raw_est: "Fees 1 est. (raw)",
  fees0_units_est: "Fees 0 est.",
  fees1_units_est: "Fees 1 est.",
  anchor_hash: "Anchor hash",
  block_timestamp: "Block time",
  publication_id: "Publication",
  attempt_id: "Attempt",
  published_at: "Published at",
  integrity_mode: "Integrity",
  block_reference_kind: "Block reference",
  checks_passed: "Checks passed",
  token_is_token0: "Is token 0",
  counter_tokens: "Paired with",
  reserve_token_units: "Reserve (this token)",
  reserve_token_raw: "Reserve (raw)",
  reserve_share: "Share of token reserves",
  bucket: "Date",
  pools_published_cl: "Published (CL)",
  pools_live_cl: "Live (CL)",
  pools_published_reserves: "Published (reserves)",
  metric: "Metric",
  pools_measured: "Pools measured",
  pools_true: "Pools (true)",
  width_bucket: "Width",
  share_of_ranges: "Share of ranges",
  source: "Source",
  latest_snapshot_date: "Latest snapshot",
  latest_anchor_block: "Latest anchor block",
  latest_published_at: "Latest published",
};

export function labelForColumn(name: string): string {
  if (LABEL_OVERRIDES[name]) return LABEL_OVERRIDES[name];
  return name
    .split("_")
    .map((part, index) => (index === 0 ? part.charAt(0).toUpperCase() + part.slice(1) : part))
    .join(" ");
}

//: Per-dataset overrides: extra hides beyond the default policy, or label /
//: kind tweaks. Only list what deviates from the heuristics.
export const COLUMN_CONFIGS: Record<string, ColumnSpec[]> = {
  pool_directory: [
    // The directory is dense: hide what the composed cells already say.
    { key: "pool_family", hidden: true },
    { key: "fee_band", hidden: true },
    { key: "n_assets", hidden: true },
    { key: "as_of", hidden: true },
    { key: "reserves_as_of", hidden: true },
    // `price_raw` is the column that always has a value; PriceCell upgrades it
    // to the adjusted twin when BOTH decimals are known, and marks it raw
    // otherwise. Hiding it showed a dash for every unresolved-decimals pool.
    { key: "price_raw", label: "Price", kind: "price" },
    { key: "price_adjusted", hidden: true },
    { key: "reserve0_units", label: "Reserve 0", kind: "amount" },
    { key: "reserve1_units", label: "Reserve 1", kind: "amount" },
    { key: "deployment_block", hidden: true },
    { key: "anchor_block", hidden: true },
    { key: "last_published", hidden: true },
  ],
  token_directory: [
    { key: "as_of", hidden: true },
  ],
  pool_profile_at: [
    { key: "pool_address", hidden: true },
    { key: "as_of", hidden: true },
    { key: "current_tick", hidden: true },
    // Same rule as the directory: the raw column is the one that always has a
    // value, and PriceCell upgrades it when the decimals are known.
    { key: "price_lower_raw", label: "Lower price", kind: "price" },
    { key: "price_upper_raw", label: "Upper price", kind: "price" },
    { key: "price_lower_adjusted", hidden: true },
    { key: "price_upper_adjusted", hidden: true },
  ],
  pool_ticks_at: [],
  pool_state_history: [
    { key: "fee", hidden: true },
    { key: "tick_spacing", hidden: true },
    { key: "price_raw", label: "Price", kind: "price" },
    { key: "price_adjusted", hidden: true },
  ],
  pool_reserves_history: [],
  pool_fee_growth: [],
  pool_publication_facts: [],
  token_pools: [
    { key: "pool_family", hidden: true },
    { key: "fee_band", hidden: true },
    { key: "price_raw", hidden: true },
    { key: "price_adjusted", hidden: true },
    { key: "current_tick", hidden: true },
  ],
  coverage_summary: [],
  publication_calendar: [],
  missing_days: [],
  metadata_gap: [],
};

export interface ResolvedColumnPolicy {
  hidden: string[];
  labels: Record<string, string>;
  kinds: Record<string, CellKind>;
  entities: Record<string, PlxEntityType | null>;
}

export function resolveColumnPolicy(datasetKey: string, columnNames: string[]): ResolvedColumnPolicy {
  const config = new Map((COLUMN_CONFIGS[datasetKey] ?? []).map((spec) => [spec.key, spec]));
  const hidden: string[] = [];
  const labels: Record<string, string> = {};
  const kinds: Record<string, CellKind> = {};
  const entities: Record<string, PlxEntityType | null> = {};
  for (const name of columnNames) {
    const spec = config.get(name);
    if (spec?.hidden || (!spec && defaultHidden(name, columnNames))) {
      hidden.push(name);
      continue;
    }
    labels[name] = spec?.label ?? labelForColumn(name);
    kinds[name] = spec?.kind ?? kindForColumn(name);
    entities[name] = spec?.entity ?? entityForColumn(name);
  }
  return { hidden, labels, kinds, entities };
}
