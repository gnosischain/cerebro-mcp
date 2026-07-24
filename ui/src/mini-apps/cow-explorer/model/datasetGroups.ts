// Frontend mirror of the backend SECTION_GROUPS map (cow_explorer.py).
// Used to (a) route a per-dataset error card's Retry to its owning group and
// (b) enumerate every dataset key for the docs-completeness test. Keep in
// sync with the Python side — a unit test asserts full coverage of the keys
// referenced by COLUMN_CONFIGS / DATASET_DOCS.

export const SECTION_GROUPS: Record<string, Record<string, readonly string[]>> = {
  overview: {
    core: ["network_summary", "coverage_matrix"],
    breakdown: ["network_activity", "top_pairs", "fee_policy_counts"],
    protocol: ["protocol_kpis", "alltime_chain_totals"],
    share: ["chain_share_trend"],
  },
  markets: {
    core: ["market_summary", "pair_options"],
    charts: ["price_candles", "auction_reference_prices", "native_reference_prices"],
    depth: ["pair_depth", "depth_horizon", "open_intent_pairs"],
    depth_heatmap: ["pair_depth_heatmap"],
    tape: ["recent_market_trades"],
  },
  trades: {
    core: ["trade_activity", "trade_pair_breakdown"],
    tape: ["trades"],
  },
  orders: {
    core: ["order_status_summary", "order_activity"],
    // intents (pair-scoped) and the trades-join quality groups exist only
    // single-chain; types/programmatic are dual-mode — a group load simply
    // skips absent keys (execution_flow precedent).
    intents: ["known_orders", "known_intents", "intent_depth"],
    quality: ["order_quality_summary", "fill_latency_distribution", "surplus_distribution"],
    types: ["order_type_summary", "order_flavor_mix", "order_type_trend"],
    programmatic: ["conditional_order_activity", "appdata_order_classes"],
    class_quality: ["surplus_by_class"],
  },
  auctions: {
    core: ["auction_activity"],
    list: ["auctions"],
  },
  solvers: {
    core: ["solver_stats", "solver_activity"],
    // execution_flow exists only single-chain; solver_cross_chain only in
    // the all-networks rollup — a group load simply skips absent keys.
    detail: ["ranking_distribution", "execution_flow", "solver_cross_chain"],
    directory: ["solver_directory"],
    quality: ["solver_score_gaps"],
  },
  traders: {
    core: ["trader_leaderboard", "trader_activity"],
    // Growth accounting and retention are each the SOLE member of their
    // group server-side: the all-time first-seen hash (~90 MB) must never
    // run concurrently with a sibling scan.
    dynamics: ["trader_dynamics"],
    retention: ["trader_retention"],
  },
  patterns: {
    core: ["solver_pair_matrix"],
    affinity: ["trader_solver_affinity"],
    quality: ["fee_policy_quality", "quote_delta_quality"],
  },
  live: {
    core: ["live_pulse"],
    feed: ["live_trades", "live_settlements", "live_minute_activity"],
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
