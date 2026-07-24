// Column-display policy for CoW Explorer tables (v2).
//
// The server ships helper columns next to every display column — `<x>_symbol`,
// `<x>_decimals`, `<x>_raw`, `chain_id` — which composed cells CONSUME. This
// module decides which columns render as their own <td>, what header label
// they get, what cell kind renders them, and which entity a click opens.
// Datasets without an explicit config still get the default policy, so a new
// dataset can never regress into a raw column dump.

import type { EntityType } from "../types";

export type CellKind =
  | "token"
  | "chain"
  | "address"
  | "solver"
  | "hash"
  | "orderUid"
  | "amount"
  | "time"
  | "number"
  | "text";

export interface ColumnSpec {
  key: string;
  label?: string;
  kind?: CellKind;
  entity?: EntityType;
  hidden?: boolean;
}

//: Helper columns absorbed by composed cells — hidden unless a config
//: explicitly re-enables one. `indexed_from/to` + `source_observed_at` are
//: coverage metadata surfaced through the Coverage popover, not as columns.
const DEFAULT_HIDDEN_RE =
  /(?:_symbol|_decimals|_raw|_icon_url)$|^(?:indexed_from|indexed_to|source_observed_at|payload|raw_payload)$/;

export function defaultHidden(name: string): boolean {
  return DEFAULT_HIDDEN_RE.test(name);
}

const TOKEN_COLUMN_RE = /^(?:token|token[01]|(?:base|quote|sell|buy|fee)_token)$/;
const SOLVER_COLUMN_RE =
  /^(?:solver|winner|competition_winner|competition_solver|settlement_executor|winning_solution_solver)$/;
const TIME_COLUMN_RE =
  /(?:_at|_timestamp|_date|_time|_seen)$|^(?:bucket|creation_date|block_timestamp|event_timestamp|auction_timestamp)$/;
const AMOUNT_COLUMN_RE =
  /^(?:sell|buy|fee|executed_sell|executed_buy|executed_fee)_amount$|^(?:amount|remaining_sell|remaining_buy|sell_amount_normalized|buy_amount_normalized)$/;

/** Heuristic cell kind for columns without an explicit config entry. */
export function kindForColumn(name: string): CellKind {
  if (TOKEN_COLUMN_RE.test(name)) return "token";
  if (name === "chain_id") return "chain";
  if (SOLVER_COLUMN_RE.test(name)) return "solver";
  if (name === "order_uid") return "orderUid";
  if (name === "tx_hash" || name === "transaction_hash" || name === "block_hash" || name === "solution_tx_hash") return "hash";
  if (name === "owner" || name === "receiver" || name === "target" || name === "trader") return "address";
  if (AMOUNT_COLUMN_RE.test(name)) return "amount";
  if (TIME_COLUMN_RE.test(name)) return "time";
  return "text";
}

/** Heuristic click-through entity (mirrors the server's identifier shapes). */
export function entityForColumn(name: string): EntityType | null {
  if (name === "order_uid") return "order";
  if (name === "tx_hash" || name === "transaction_hash" || name === "solution_tx_hash") return "transaction";
  if (name === "auction_id") return "auction";
  if (TOKEN_COLUMN_RE.test(name)) return "token";
  if (SOLVER_COLUMN_RE.test(name)) return "solver";
  if (name === "owner" || name === "target" || name === "receiver" || name === "trader") return "address";
  return null;
}

const LABEL_OVERRIDES: Record<string, string> = {
  chain_id: "Chain",
  tx_hash: "Transaction",
  order_uid: "Order",
  auction_id: "Auction",
  token0: "Token A",
  token1: "Token B",
  sell_token: "Sell",
  buy_token: "Buy",
  sell_amount: "Sell amount",
  buy_amount: "Buy amount",
  fee_amount: "Fee",
  fill_count: "Fills",
  trade_count: "Fills",
  settlement_transactions: "Settlements",
  competition_count_all_indexed: "Competitions",
  observed_open_orders: "Open orders",
  policy_raw: "Fee policy",
  policy_family: "Policy family",
  fee_entries: "Fee entries",
  settlement_executor: "Executor",
  competition_solver: "Solver",
  competition_winner: "Winner",
  average_ranking: "Avg rank",
  best_ranking: "Best rank",
  block_timestamp: "Time",
  creation_date: "Created",
  valid_to: "Valid to",
  limit_price: "Limit price",
  base_remaining: "Base remaining",
  intent_count: "Intents",
  checkpoint_block: "Checkpoint block",
  checkpoint_timestamp: "Checkpoint time",
  checkpoint_updated_at: "Checkpoint updated",
  trade_observed_at: "Trades observed",
  order_observed_at: "Orders observed",
  competition_observed_at: "Competitions observed",
  native_price_observed_at: "Native prices observed",
  max_competition_block: "Max competition block",
  trader: "Trader",
  chains_active: "Chains",
  distinct_pairs: "Pairs",
  first_seen: "First seen",
  last_seen: "Last seen",
  active_traders: "Active traders",
  new_traders: "New traders",
  executed_settlements: "Settlements",
  win_rate: "Win rate",
  tokens_touched: "Tokens",
  unpriced_tokens: "Unpriced",
  net_native_wei_known: "Net (native wei, priced tokens)",
  net_amount: "Net amount",
  winning_score: "Winning score",
  reference_score: "Reference score",
  score_gap: "Score gap",
  scores_parsed: "Parsed",
  multi_winner_solutions: "Multi-winner wins",
  multi_winner_share: "Multi-winner share",
  score_parse_failures: "Score parse failures",
  auction_timestamp: "Auction time",
  avg_surplus_bps: "Avg surplus (bps)",
  median_surplus_bps: "Median surplus (bps)",
  p90_surplus_bps: "P90 surplus (bps)",
  avg_latency_seconds: "Avg latency (s)",
  median_latency_seconds: "Median latency (s)",
  latency_bucket: "Latency",
  surplus_bucket: "Surplus band",
  fills: "Fills",
  pair_share: "Pair share",
  trader_share: "Trader share",
  solver_global_share: "Solver share (all flow)",
  realized_surplus_bps: "Realized surplus (bps)",
  fill_ratio: "Fill ratio",
  first_fill_at: "First fill",
  // ---- v3: protocol KPIs / all-time totals / share trend ----
  unique_traders: "Traders",
  unique_pairs: "Pairs",
  approx_native_volume: "Volume (native, est.)",
  first_trade_at: "First trade",
  last_trade_at: "Last trade",
  // ---- v3: order types / programmatic / class quality ----
  order_class: "Class",
  order_kind: "Kind",
  signing_scheme: "Signing",
  order_count: "Orders",
  owners: "Owners",
  fulfilled_share: "Fulfilled share",
  open_now: "Open now",
  fulfilled_count: "Fulfilled",
  partially_fillable_count: "Partially fillable",
  event_type: "Event",
  events: "Events",
  creators: "Creators",
  appdata_hashes: "App-data docs",
  // ---- v3: solver directory / score gaps ----
  settlements_all_time: "Settlements (all time)",
  competitions_all: "Competitions (all time)",
  wins_all: "Wins (all time)",
  first_settlement_at: "First settlement",
  last_settlement_at: "Last settlement",
  chain_anchor_at: "Chain latest",
  wins_scored: "Scored wins",
  parse_failures: "Parse failures",
  avg_score_gap: "Avg score gap",
  median_score_gap: "Median score gap",
  p90_score_gap: "P90 score gap",
  // ---- v3: trader dynamics / retention ----
  period: "Month",
  returning_traders: "Returning",
  reactivated_traders: "Reactivated",
  churned_traders: "Churned",
  quick_ratio: "Quick ratio",
  retention_rate: "Retention rate",
  cohort_month: "Cohort",
  month_index: "Month #",
  cohort_size: "Cohort size",
  retention_share: "Retention share",
  // ---- v3: quote-delta quality ----
  delta_bucket: "Delta band",
  avg_delta_bps: "Avg delta (bps)",
  median_delta_bps: "Median delta (bps)",
  // ---- v3: pair depth / horizon ----
  amount_base: "Amount (base)",
  amount_quote: "Amount (quote)",
  earliest_supported_at: "Capture began",
  latest_observed_at: "Latest observed",
  captured_orders: "Captured orders",
  earliest_creation_seen: "Earliest creation seen",
  // ---- v3: live heartbeat ----
  settlements: "Settlements",
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
  network_summary: [
    { key: "order_indexed_from", hidden: true },
    { key: "order_indexed_to", hidden: true },
  ],
  coverage_matrix: [],
  known_orders: [
    { key: "kind", label: "Kind" },
    { key: "side", label: "Side" },
    { key: "executed_sell_amount_raw", hidden: true },
    { key: "executed_buy_amount_raw", hidden: true },
    { key: "residual_sell_amount_raw", hidden: true },
    { key: "residual_buy_amount_raw", hidden: true },
    { key: "sell_amount_normalized", label: "Sell amount" },
    { key: "buy_amount_normalized", label: "Buy amount" },
  ],
  auctions: [
    { key: "tx_hashes", hidden: true },
    { key: "reference_score", hidden: true },
  ],
  order_events: [{ key: "event_id", hidden: true }],
  solver_imbalance_tokens: [
    // The raw net matters when decimals are unknown; keep it visible.
    { key: "net_amount_raw", label: "Net (raw units)" },
  ],
  // ---- v3 datasets. Empty arrays mean the default heuristics (chain_id →
  // chain badge, solver/competition_solver → solver entity, owner → address,
  // *_at → time, *_raw/*_decimals/*_symbol hidden) already do the right
  // thing; only deviations are listed.
  protocol_kpis: [],
  alltime_chain_totals: [],
  chain_share_trend: [],
  order_type_summary: [],
  order_flavor_mix: [],
  order_type_trend: [],
  conditional_order_activity: [],
  appdata_order_classes: [],
  surplus_by_class: [],
  solver_directory: [],
  solver_score_gaps: [],
  trader_dynamics: [
    // Month-start date; not matched by the time-column heuristics.
    { key: "period", kind: "time" },
  ],
  trader_retention: [{ key: "cohort_month", kind: "time" }],
  quote_delta_quality: [],
  live_minute_activity: [],
  pair_depth: [
    { key: "kind", label: "Kind" },
    { key: "side", label: "Side" },
    // Ladder price is ALWAYS quote-per-base for both sides (server contract).
    { key: "price", label: "Limit price", kind: "number" },
    { key: "amount_base", kind: "number" },
    { key: "amount_quote", kind: "number" },
  ],
  depth_horizon: [],
  open_intent_pairs: [
    { key: "open_orders", label: "Open intents", kind: "number" },
  ],
};

export interface ResolvedColumnPolicy {
  hidden: string[];
  labels: Record<string, string>;
  kinds: Record<string, CellKind>;
  entities: Record<string, EntityType | null>;
}

export function resolveColumnPolicy(
  datasetKey: string,
  columnNames: string[],
): ResolvedColumnPolicy {
  const config = new Map(
    (COLUMN_CONFIGS[datasetKey] ?? []).map((spec) => [spec.key, spec]),
  );
  const hidden: string[] = [];
  const labels: Record<string, string> = {};
  const kinds: Record<string, CellKind> = {};
  const entities: Record<string, EntityType | null> = {};
  for (const name of columnNames) {
    const spec = config.get(name);
    if (spec?.hidden || (!spec && defaultHidden(name))) {
      hidden.push(name);
      continue;
    }
    labels[name] = spec?.label ?? labelForColumn(name);
    kinds[name] = spec?.kind ?? kindForColumn(name);
    entities[name] = spec?.entity ?? entityForColumn(name);
  }
  return { hidden, labels, kinds, entities };
}
