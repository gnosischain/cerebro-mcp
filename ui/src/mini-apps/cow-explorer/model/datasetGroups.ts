// Frontend mirror of the backend SECTION_GROUPS map (cow_explorer.py).
// Used to (a) route a per-dataset error card's Retry to its owning group and
// (b) enumerate every dataset key for the docs-completeness test. Keep in
// sync with the Python side — a unit test asserts full coverage of the keys
// referenced by COLUMN_CONFIGS / DATASET_DOCS.

export const SECTION_GROUPS: Record<string, Record<string, readonly string[]>> = {
  overview: {
    core: ["network_summary", "coverage_matrix"],
    breakdown: ["network_activity", "top_pairs", "fee_policy_counts"],
  },
  markets: {
    core: ["market_summary", "pair_options"],
    charts: ["price_candles", "auction_reference_prices", "native_reference_prices"],
    tape: ["recent_market_trades"],
  },
  trades: {
    core: ["trade_activity", "trade_pair_breakdown"],
    tape: ["trades"],
  },
  orders: {
    core: ["order_status_summary", "order_activity"],
    intents: ["known_orders", "known_intents", "intent_depth"],
    quality: ["order_quality_summary", "fill_latency_distribution", "surplus_distribution"],
  },
  auctions: {
    core: ["auction_activity"],
    list: ["auctions"],
  },
  solvers: {
    core: ["solver_stats", "solver_activity"],
    detail: ["ranking_distribution", "execution_flow", "solver_cross_chain"],
  },
  traders: {
    core: ["trader_leaderboard", "trader_activity"],
  },
  patterns: {
    core: ["solver_pair_matrix"],
    affinity: ["trader_solver_affinity"],
    quality: ["fee_policy_quality"],
  },
  live: {
    core: ["live_pulse"],
    feed: ["live_trades", "live_settlements"],
    intents: ["live_open_orders", "live_order_events"],
  },
};

/** dataset key -> owning `{section, group}` (section datasets only). */
export const DATASET_GROUP: Record<string, { section: string; group: string }> = {};
for (const [section, groups] of Object.entries(SECTION_GROUPS)) {
  for (const [group, keys] of Object.entries(groups)) {
    for (const key of keys) DATASET_GROUP[key] = { section, group };
  }
}

/** Server-reported failure for one dataset (stub-descriptor contract): a
 * failed query ships a zero-row descriptor whose provenance.coverage carries
 * `error` + `warning_codes:["query_failed"]` — the UI renders an explicit
 * error card from this instead of letting the panel vanish. */
export function datasetError(
  descriptor?: { provenance?: Record<string, unknown> },
): string {
  const coverage = descriptor?.provenance?.coverage as
    | { error?: string; warning_codes?: string[] }
    | undefined;
  if (!coverage) return "";
  if (coverage.error) return coverage.error;
  return (coverage.warning_codes ?? []).includes("query_failed")
    ? "Query failed."
    : "";
}
