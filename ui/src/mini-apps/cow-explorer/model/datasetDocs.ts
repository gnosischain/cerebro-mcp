// Plain-language documentation for EVERY dataset the CoW Explorer renders.
// Surfaced in each table/chart's (i) popover as "What this is" / "How it's
// computed" blocks, above the machine coverage line. A unit test asserts
// every dataset key in SECTION_GROUPS and the entity layouts has an entry.

export interface DatasetDoc {
  /** What the data IS — plain language, no SQL. */
  what: string;
  /** How it is computed + known caveats (dedup, approximation, exclusions). */
  method?: string;
}

const DEDUP_NOTE =
  "Counts skip ReplacingMergeTree version dedup (<0.1% duplicates, measured) so full-history scans stay in constant memory.";
const CHECKPOINT_NOTE =
  "Rows are bounded to each chain's committed indexing checkpoint for reorg safety.";
const TAPE_NOTE =
  "Newest-first bounded selection: the database keeps only the top rows by block time (memory-safe at any window), then deduplicates indexer versions and joins token symbols onto the selected rows only.";
const CAP_NOTE =
  " Correlation matrices join two large tables, so they are computed over at most the last 90 indexed days (a rolling analytical window) regardless of the global time selector — this keeps them memory-safe on the shared warehouse and is separate from the Trades/Markets history, which is never capped.";
const ORDERBOOK_SUBSET_NOTE =
  "The orderbook sync is a PARTIAL subset (~78K orders; limit-heavy, skewed to recent years) — class mixes describe the observed subset, NOT all CoW orders.";
const DYNAMICS_WINDOW_NOTE =
  "Fixed trailing 12-month analytical window regardless of the global time selector. A trader is an address. 'New' means first indexed fill EVER (all-time first-seen, not window-scoped). BNB fills lack block timestamps and are excluded; a stalled chain indexer depresses recent actives until it catches up.";

export const DATASET_DOCS: Record<string, DatasetDoc> = {
  // ---- overview -----------------------------------------------------------
  network_summary: {
    what: "Per-network totals for the selected window: settled fills, settlement transactions, observed orders (with how many are currently open), and all-time settled competitions.",
    method: `One grouped pass over the indexed trades and orders per chain. Relative windows anchor to the most recent trade across ALL selected networks, so a stale chain (e.g. one whose indexer is behind) shows zero recent activity instead of passing off old data as current. ${DEDUP_NOTE} ${CHECKPOINT_NOTE}`,
  },
  coverage_matrix: {
    what: "What the index actually contains per network: committed checkpoint block and time, newest observation per source table, and how far behind wall-clock each chain is.",
    method: "Read from the indexer's own checkpoint bookkeeping (cheap; no trade scans). This is the honesty panel — every other view's completeness should be judged against it.",
  },
  network_activity: {
    what: "Daily settled fills per network over the selected window.",
    method: `Day-bucketed streaming aggregate over indexed trades. ${DEDUP_NOTE}`,
  },
  top_pairs: {
    what: "The most-traded token pairs (unordered, e.g. WETH/USDC regardless of direction) by fill count, per network.",
    method: "Pairs are canonicalized with least/greatest so both directions count together; symbols come from indexed token metadata with address fallback. Top 500 rows.",
  },
  fee_policy_counts: {
    what: "How many indexed fills carried each protocol fee policy family (volume / surplus / price improvement) and in which fee tokens.",
    method: "Fee rows exist only for API-enriched trades — coverage is a subset of all fills. The policy string is classified into families by keyword.",
  },
  protocol_kpis: {
    what: "Protocol-wide KPI tiles: settled fills, settlement transactions, distinct traders, and distinct pairs per network, plus one protocol-wide total row (chain 0) with exact cross-network distinct counts.",
    method: `Counts are exact over indexed trades. approx_native_volume is an ESTIMATE, not an exact figure: sell amounts valued at the CURRENT native-price snapshot (no historical price source exists in the index), expressed in each chain's own native unit — populated only for relative windows of 7 days or less, NULL otherwise and NULL on the protocol-wide row (native units are not comparable across chains). ${CHECKPOINT_NOTE}`,
  },
  alltime_chain_totals: {
    what: "All-time per-network totals: settled fills, settlement transactions, distinct traders, and first/last trade dates — the feed for the distribution pies.",
    method: "Always all indexed history, regardless of the global time selector (an all-time count needs no time axis). BNB is deliberately included: its trade rows lack block timestamps, so first/last dates may be NULL while the counts are real — excluding it would silently understate the totals.",
  },
  chain_share_trend: {
    what: "Each network's share of settled fills and settlement transactions over time.",
    method: `One grouped scan by (bucket, network): daily buckets, or weekly when the window exceeds 180 days (or is all history). The 100%-share view is normalized client-side per bucket. ${DEDUP_NOTE}`,
  },
  // ---- markets ------------------------------------------------------------
  pair_options: {
    what: "The 50 busiest token pairs of the last 30 days on this network — the choices behind the base/quote pair picker.",
    method: "Streaming aggregate over indexed fills with version dedup; symbols joined from token metadata with address fallback.",
  },
  market_summary: {
    what: "Headline stats for the selected base/quote pair: fill count, settlement transactions, and the indexed time span.",
    method: `Aggregate over indexed fills matching the pair in either direction. ${CHECKPOINT_NOTE}`,
  },
  price_candles: {
    what: "Execution-derived OHLC + VWAP candles for the selected pair — prices realized in settled fills, not an order book feed.",
    method: "Every fill is orientation-normalized to quote-per-base; VWAP = total quote volume / total base volume per bucket. Rendered only when both tokens' decimals are known — never from raw units.",
  },
  auction_reference_prices: {
    what: "The protocol's auction clearing-price ratio for the pair, per settled competition — a reference value, not a traded price.",
    method: "Base and quote clearing prices are joined per auction and adjusted by token decimals; timestamped by the auction block.",
  },
  native_reference_prices: {
    what: "CoW's native-price API observations for the pair over time — an off-chain reference series.",
    method: "Raw API snapshots (a time series, one row per observation); the ratio of base/quote native prices per minute bucket.",
  },
  recent_market_trades: {
    what: "The newest settled fills for the selected pair, with normalized amounts and fees.",
    method: TAPE_NOTE,
  },
  pair_depth: {
    what: "Per-order depth ladder for the selected pair: every known open order's limit price (ALWAYS quote-per-base, both sides), remaining base/quote amounts, and identity — the raw material for the depth chart and its order list. Asks sell the base token; bids sell the quote token.",
    method: "Known open intents ONLY — never the complete orderbook (intents the indexer never saw are absent). Historical mode reconstructs the book at a point in time from captured orders minus prior fills and cancellation events, and is bounded by the per-chain order-capture start (read from the depth horizon; continuous capture began ~2026-07-20, so the window is days deep and grows daily). API-observed cancel times carry minutes of jitter; BNB partial fills cannot be time-resolved (full fills fall back to status events).",
  },
  pair_depth_heatmap: {
    what: "Resting depth of the selected pair's book over time, drawn as a FOOTPRINT: one cell per (time bucket, price level), split so bids fill the left half and asks the right. Side is carried by position and magnitude by colour on a per-side scale, so a level that is barely ask-heavy no longer reads like an all-ask one; a half is left blank when that side has nothing resting. An outline marks a 3:1 imbalance. The right-hand profile sums each level over the whole window. Powers the Footprint tab.",
    method: "The window is cut into equal time buckets (auto = span/60, or the resolution you pick, coarsened if it would exceed the row budget). An order counts in a bucket when its resting span overlaps it — created before the bucket ends and not yet removed by expiry, a timestamped cancellation, or its completing fill — and its base-normalized size is TIME-WEIGHTED by the fraction of the bucket it rested. Price is binned relative to that bucket's own market price (±20% in 1-point steps), which is why long windows stay readable while the underlying price trends; that reference ships alongside so the axis can show either absolute price or distance from market. Three limits of the reference: it is the MEDIAN PRICE OF THE ORDERS CREATED IN THAT BUCKET, used as a proxy for the orders resting in it — sound while typical rest time (~30 min) is at or below the bucket width, which holds for every offered resolution, but an approximation; a bucket in which nothing was created falls back to the window-wide median; and intents resting more than ±20% away are NOT charted at all (on the worst case — a multi-year window with month-wide buckets — that is ~7.5% of orders, against 47% dropped by the window-median clamp this replaced). Intra-fill size decay is NOT modeled, API-observed cancel times carry minutes of jitter, and cancelled orders with no observed cancel time are excluded. Known open intents only, never the complete orderbook.",
  },
  depth_horizon: {
    what: "How far back historical depth reconstruction can honestly go on this network: earliest and latest order observations and how many orders have been captured.",
    method: "Read from the order index's own observation bookkeeping (pair-independent). Continuous order capture began ~2026-07-20 on all chains — the reconstruction window is a data property that grows daily, never a hard-coded date.",
  },
  open_intent_pairs: {
    what: "Pairs on this network that currently have a standing book: open, unexpired intents grouped by pair — the depth panel's rescue list when the selected pair (or the whole chain) has no open orders.",
    method: "Chain-scoped snapshot over known open intents only (never the complete orderbook). Some networks — Gnosis in particular — run almost entirely on short-lived market orders and legitimately hold zero standing intents at a given moment; that absence is shown, not hidden.",
  },
  // ---- trades -------------------------------------------------------------
  trade_activity: {
    what: "Daily settled-fill activity: fills, settlement transactions, and distinct traders per day and network.",
    method: `Streaming day-bucketed aggregate. Distinct counts use approximate uniq (~0.8% max error) for constant memory. ${DEDUP_NOTE}`,
  },
  trade_pair_breakdown: {
    what: "Fill counts by token pair over the selected window (top 500 pairs).",
    method: "Canonicalized unordered pairs; symbols joined from token metadata; approximate distinct counts.",
  },
  trades: {
    what: "The raw settled-fills tape: every indexed fill with owner, tokens, normalized amounts, fee, and source, newest first.",
    method: `${TAPE_NOTE} The row count is reported as 'at least' when the 10,000-row cap is hit.`,
  },
  // ---- orders -------------------------------------------------------------
  order_status_summary: {
    what: "Observed orders grouped by their latest indexed status (open / fulfilled / cancelled / expired).",
    method: "Latest version per order (argMax by observation time). This is the indexed view of orders, NOT a complete live orderbook.",
  },
  order_activity: {
    what: "Orders created per day, with how many are still open now.",
    method: "Day-bucketed over order creation dates, latest-version status.",
  },
  known_orders: {
    what: "Known open intents: orders observed open and unexpired at the last indexing pass.",
    method: "Snapshot of latest-version orders with status=open and valid_to in the future. Freshness does not imply completeness — intents the indexer never saw are absent.",
  },
  known_intents: {
    what: "Summary of the known open intents for the selected pair: counts and remaining executable amounts per side.",
    method: "Remaining amount = limit amount minus executed amount (floored at zero), normalized by token decimals.",
  },
  intent_depth: {
    what: "Bid/ask-style depth built ONLY from known open intents' limit prices and remaining amounts — labelled 'known intents', never full market depth.",
    method: "Each open order contributes its remaining base-denominated amount at its orientation-normalized limit price. Skipped when token decimals are unknown.",
  },
  order_quality_summary: {
    what: "Execution quality per day: realized surplus vs each order's limit price (bps) and creation-to-fill latency.",
    method: "Surplus bps = executed price vs limit price ratio − 1 (kind-independent; positive always means better than limit; decimals-free within a pair). Only fills whose order is indexed are included.",
  },
  fill_latency_distribution: {
    what: "How long fills took from order creation to on-chain execution, bucketed (<10s to >1h).",
    method: "Creation timestamps come from the order index; fills without a matching indexed order or with API-only timestamps fall into 'unknown'.",
  },
  surplus_distribution: {
    what: "Distribution of realized surplus vs limit price across fills, in basis-point bands.",
    method: "Same surplus formula as the quality summary; negative bands mean worse than the limit implies (e.g. fee accounting), positive bands mean price improvement.",
  },
  order_type_summary: {
    what: "Observed orders grouped by class (market / limit) per network: counts, distinct owners, lifecycle status splits, fulfilled share, and how many are partially fillable.",
    method: `Latest version per order (argMax by observation time). ${ORDERBOOK_SUBSET_NOTE}`,
  },
  order_flavor_mix: {
    what: "Order flavor mix per network: kind (sell / buy) x signing scheme x partial fillability, with owner counts and fulfilled share.",
    method: `Latest version per order. ${ORDERBOOK_SUBSET_NOTE}`,
  },
  order_type_trend: {
    what: "Orders created per day by class and network, with how many were eventually fulfilled.",
    method: `Day-bucketed on order creation dates, latest-version status. ${ORDERBOOK_SUBSET_NOTE}`,
  },
  conditional_order_activity: {
    what: "Programmatic (ComposableCoW) order activity: ConditionalOrderCreated and related lifecycle events per day, network, and event type, with distinct creators. The ~19.8K ConditionalOrderCreated events are the programmatic footprint — broader than any app-data tag.",
    method: "On-chain lifecycle events from the order-events index. Some events lack a chain timestamp and bucket on observation time instead (disclosed by the coalesce, not dropped).",
  },
  appdata_order_classes: {
    what: "Observed orders grouped by the orderClass tag in their app-data document (market / limit / twap), plus honest 'unresolved' (no stored app-data doc) and 'untagged' (doc without a tag) buckets.",
    method: `Only ~45% of order rows resolve a stored app-data document — the 'unresolved' bucket is the disclosed remainder, never dropped. TWAP tags exist on only ~63 orders (TWAP children land as class='limit', so this tag is the only honest TWAP signal); ConditionalOrderCreated events are the broader programmatic footprint. ${ORDERBOOK_SUBSET_NOTE}`,
  },
  surplus_by_class: {
    what: "Realized surplus vs limit price (bps) segmented by order class, bucketed into bands with per-class averages and medians.",
    method: `Same kind-independent surplus formula as the quality summary (positive always means better than limit); computed over at most the last 90 indexed days. ${ORDERBOOK_SUBSET_NOTE}`,
  },
  // ---- auctions -----------------------------------------------------------
  auction_activity: {
    what: "Settled solver competitions per day.",
    method: "Latest-version competitions bucketed by their auction block's timestamp.",
  },
  auctions: {
    what: "Recent settled competitions: auction id, winner, reference score, solution and settlement-transaction counts.",
    method: "Joined from the competitions, solutions, and competition-transaction indexes (latest versions).",
  },
  // ---- solvers ------------------------------------------------------------
  solver_stats: {
    what: "Per-solver competition statistics: solutions, competitions, wins, win rate, and ranking summary.",
    method: "From indexed competition solutions (latest versions). Solver names come from a bundled registry; unknown solvers show short addresses.",
  },
  solver_activity: {
    what: "Daily competition participation per solver.",
    method: "Day-bucketed solutions by auction-block timestamp.",
  },
  ranking_distribution: {
    what: "How submitted solutions ranked (1 = best). Multiple winners per auction are expected — CoW runs multi-winner combinatorial auctions.",
    method: "Histogram over latest-version solutions.",
  },
  execution_flow: {
    what: "Which settlement executors settle which token pairs — the pair → executor flow for this network.",
    method: "Fills joined to their settlement's executor address; top 12 nodes kept, the rest grouped as 'Other'." + CAP_NOTE,
  },
  solver_cross_chain: {
    what: "The same solver's wins/competitions on every network side by side.",
    method: "Per-solver, per-chain aggregate over competition solutions; addresses are matched exactly across chains (deployments may differ per chain).",
  },
  solver_directory: {
    what: "Every (network, solver) presence observed in the index: first/last settlement, all-time settlements, competitions entered, and wins — the solver directory.",
    method: "Presence is derived from indexed settlements + competition entries — an ACTIVITY PROXY, NOT the on-chain allow-list. Prod/barn labels come from the bundled registry; unregistered addresses render raw. All-time by design (presence needs no window). Each row ships its chain's own latest settlement (chain_anchor_at) so activity tiers are computed against the CHAIN's freshness — a stale indexer does not fake solver inactivity.",
  },
  solver_score_gaps: {
    what: "Winning score minus the protocol's reference score, per solver and network — the competitive margin over the protocol's benchmark for auctions the solver won.",
    method: "reference_score is a JSON map keyed by solver address; scores are opaque big-int strings parsed defensively — rows that fail to parse are counted in parse_failures instead of being silently dropped.",
  },
  // ---- traders ------------------------------------------------------------
  trader_leaderboard: {
    what: "The most active traders by settled fills: fills, settlement transactions, distinct pairs, first/last seen.",
    method: `Grouped by owner address over the selected window (top 200). Smart-account owners are per-chain identities. ${DEDUP_NOTE}`,
  },
  trader_activity: {
    what: "Active and first-time traders per day.",
    method: "A trader is 'new' on the day of their first indexed fill ever (not just in the window).",
  },
  trader_dynamics: {
    what: "Monthly trader growth accounting: active, new, returning, reactivated, and churned traders, plus quick ratio ((new + reactivated) / churned) and month-over-month retention rate.",
    method: DYNAMICS_WINDOW_NOTE,
  },
  trader_retention: {
    what: "Cohort retention triangle: for each monthly first-trade cohort, how many traders are still active N months later and what share of the cohort that is.",
    method: DYNAMICS_WINDOW_NOTE,
  },
  // ---- patterns -----------------------------------------------------------
  solver_pair_matrix: {
    what: "Solver-pair specialization: what share of each top pair's fills each settlement executor settles. Reveals solver niches the official explorer does not surface.",
    method: "Top 30 pairs by fills over the window; share = executor's fills / pair's total fills. Heatmap keeps the heaviest rows/columns." + CAP_NOTE,
  },
  trader_solver_affinity: {
    what: "Order-flow concentration: what share of each top trader's fills each settlement executor settles.",
    method: "Top 100 traders by fills; share = executor's fills of that trader / trader's total fills. Persistent dark cells = concentrated flow." + CAP_NOTE,
  },
  fee_policy_quality: {
    what: "Does the protocol fee policy correlate with execution quality? Surplus vs limit (bps) segmented by policy family.",
    method: "Fills joined to their fee rows (API-enriched subset only) and orders; median/p90 surplus per policy family. Correlation, not causation." + CAP_NOTE,
  },
  quote_delta_quality: {
    what: "Execution vs quoted price (bps) in basis-point bands per fee-policy family — how realized execution compared with the quote embedded in the fill's fee policy.",
    method: "Only fills carrying a priceImprovement fee policy have an embedded quote — the ONLY quote source in the index (the quotes table is empty). Fills without one land in the 'unquoted' bucket instead of being dropped. This is quote-vs-execution delta, NOT user-perceived slippage." + CAP_NOTE,
  },
  // ---- live ---------------------------------------------------------------
  live_pulse: {
    what: "Per-network indexing heartbeat: committed checkpoint block/time and how many seconds behind wall-clock the index is.",
    method: "Read from indexer checkpoints only (cheap; safe to poll). Drives the stale-chain banner and polling backoff.",
  },
  live_trades: {
    what: "Fills executed in the last hour on the selected network(s), newest first.",
    method: "Hard-bounded to one hour + small row limit; polled on a short shared cache so concurrent viewers cost one query.",
  },
  live_settlements: {
    what: "Settlement transactions landed in the last hour, with executor and fill counts.",
    method: "Same 1-hour bound; executor names from the bundled registry.",
  },
  live_minute_activity: {
    what: "Last-hour heartbeat: settled fills and settlement transactions per minute and network — the band chart above the live feeds.",
    method: "Hard-bounded to the last hour (the minute x network aggregate stays tiny at any load); polled on the same short shared cache as the feeds.",
  },
  live_open_orders: {
    what: "Orders currently waiting to execute: observed open, unexpired intents, newest first, with age and partial-fill progress.",
    method: "Latest-version open orders; an observed snapshot, not a complete live book.",
  },
  live_order_events: {
    what: "The latest order lifecycle events (placement, fulfillment, cancellation, status changes) as they are indexed.",
    method: "On-chain lifecycle events plus API status transitions, deduplicated to latest versions.",
  },
  // ---- entity: order ------------------------------------------------------
  order_detail: {
    what: "The order's indexed record: tokens, limit amounts, kind, validity, signing scheme, and executed totals (latest version).",
  },
  order_quality: {
    what: "This order's realized execution quality: surplus vs limit (bps), fill ratio, and creation-to-first-fill latency.",
    method: "Surplus is kind-independent (positive = better than limit); computed only from indexed fills.",
  },
  order_trades: {
    what: "Every indexed fill of this order, newest first, with normalized amounts.",
    method: TAPE_NOTE,
  },
  order_events: {
    what: "The order's observed lifecycle: placement, pre-signature, invalidation, refund, and API status transitions.",
  },
  order_fees: {
    what: "Protocol fee amounts charged on this order's fills, with the applied policy.",
    method: "Fee rows exist only for API-enriched fills.",
  },
  order_app_data: {
    what: "The order's app-data document (metadata the submitting app attached), if indexed.",
  },
  // ---- entity: transaction ------------------------------------------------
  transaction_detail: {
    what: "The settlement transaction's indexed record: block, executor, and log position.",
  },
  transaction_trades: {
    what: "All fills settled inside this transaction (the batch).",
  },
  transaction_interactions: {
    what: "External calls the settlement made (target contract, 4-byte selector, native value) — the visible shape of AMM legs.",
    method: "Only targets/selectors/values are indexed — NOT decoded token amounts, so this shows where the batch routed, not how much.",
  },
  transaction_competition: {
    what: "The auction this transaction settles and the winning solution(s) that produced it.",
  },
  // ---- entity: address ----------------------------------------------------
  address_summary: {
    what: "The address's indexed roles at a glance: fills owned, orders placed, settlements executed, competition solutions submitted.",
    method: DEDUP_NOTE,
  },
  address_trades: {
    what: "Fills this address owns (as trader), newest first.",
    method: TAPE_NOTE,
  },
  address_orders: {
    what: "Orders this address placed, newest first (latest versions).",
  },
  address_solver_activity: {
    what: "Solver-side roles of this address: settlements it executed and competition solutions it submitted.",
  },
  // ---- entity: token ------------------------------------------------------
  token_detail: {
    what: "The token's indexed metadata: symbol, name, decimals, and when it was observed.",
    method: "Metadata is backfilled on-chain in small batches; missing symbols/decimals mean the token has not been resolved yet, not that it is invalid.",
  },
  token_pairs: {
    what: "Every pair this token trades in, by fill count.",
  },
  token_execution_prices: {
    what: "Daily execution-derived VWAP of this token against each counter-token, from settled fills.",
    method: "Only fills where both tokens' decimals are known contribute.",
  },
  token_native_prices: {
    what: "CoW's native-price API observations for this token over time.",
  },
  // ---- entity: auction ----------------------------------------------------
  auction_detail: {
    what: "The settled competition's record: winner, reference score, and auction block.",
  },
  auction_solutions: {
    what: "Every submitted solution, ranked (1 = best). Multiple winners are expected under combinatorial auctions.",
  },
  auction_orders: {
    what: "The orders that were in this auction's batch.",
  },
  auction_prices: {
    what: "The auction's clearing-price vector: the protocol's native-denominated price per token (native_wei = atoms × price / 1e18).",
    method: "Raw protocol reference values — not market candles.",
  },
  auction_transactions: {
    what: "The on-chain settlement transaction(s) this auction produced (batch auctions can settle across several).",
  },
  // ---- entity: solver -----------------------------------------------------
  solver_summary: {
    what: "The solver's competition record: competitions, wins, win rate, executed settlements, multi-winner share, and score-parse health.",
    method: "Win rate = wins / competitions entered. Multi-winner share counts wins with ranking≠1 — expected under combinatorial auctions, not a violation.",
  },
  solver_imbalance_tokens: {
    what: "Per-token net flow between traders and the settlement contract across this solver's settlements (30 indexed days) — the order-level 'did it source or accrue' signal.",
    method: "Trade-implied ONLY: sell-amounts in minus buy-amounts out, valued at auction clearing prices. AMM leg amounts, plain ERC20 transfers, and buffer balances are not indexed — this is a behavioral signal, not audited buffer books.",
  },
  solver_imbalance_settlements: {
    what: "The same trade-implied imbalance per individual settlement, with how many tokens lacked a clearing price.",
    method: "Negative native value = the batch paid out more than it collected (sourced externally); positive = accrued. Unpriced tokens are excluded from the native valuation and counted.",
  },
  solver_score_gap: {
    what: "Winning score vs the protocol's reference score for auctions this solver won — the competitive margin.",
    method: "reference_score is a JSON map keyed by solver address; rows that fail to parse are flagged scores_parsed=false rather than dropped.",
  },
  solver_competitions: {
    what: "Every competition entry by this solver: score, ranking, winner flag, and settlement transaction.",
  },
  solver_solutions: {
    what: "This solver's ranking distribution across all its solutions.",
  },
  solver_settlements: {
    what: "Settlement transactions this solver executed, newest first.",
    method: TAPE_NOTE,
  },
};
