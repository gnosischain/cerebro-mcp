// Plain-language documentation for EVERY dataset the Pool Liquidity Explorer
// renders. Surfaced in each panel's (i) popover as "What this is" / "How it's
// computed" blocks above the machine coverage line. A unit test asserts every
// key in SECTION_GROUPS has an entry.
//
// Four disclosures recur because the data forces them:
//   PROBED    only ~427 of ~2,500 CL pools are probed for ticks; the rest carry
//             `cl_below_active_threshold` and are STATE-ONLY (no profile).
//   RAW UNITS token metadata resolves a small minority of CL tokens; prices
//             default to raw units and are adjusted only when both decimals
//             are known.
//   FULL RANGE a full-range position (±887220 / ±887270) dominates any
//             tick-weighted denominator by construction.
//   HISTORY   reserves start 2022-12-12; CL state 2023-09-25; ticks 2023-10.

export interface DatasetDoc {
  what: string;
  method?: string;
}

const PROBED_NOTE =
  "Only pools probed for ticks (`ticks_probed = 1`) have a profile; the others carry `cl_below_active_threshold` in the publication checks and are state-only — their profile, tick and fee datasets are empty, never faked.";
const RAW_NOTE =
  "Prices are token1-raw per token0-raw; a decimals-adjusted price exists only when BOTH token decimals are known (token metadata resolves a small minority of CL tokens — most CRC20 Circles tokens are unresolved). Nothing is guessed.";
const FULL_RANGE_NOTE =
  "A full-range position (both edges at the tick boundary) dominates any tick-weighted denominator by construction; concentration shares disclose that rather than hide it.";
const PUBLISHED_NOTE =
  "Read only from the publication-verified views (`v_pool_*_published`, one row per pool and date); as-of dates resolve from `census_publications`.";
const RESERVES_HISTORY_NOTE =
  "Reserves are indexed from 2022-12-12; CL state from 2023-09-25; initialized ticks from 2023-10.";

export const DATASET_DOCS: Record<string, DatasetDoc> = {
  // ---- overview -----------------------------------------------------------
  pools_summary: {
    what: "Headline counts at the latest publication: configured pools by family, CL pools published / live (liquidity > 0) / probed for ticks / live-but-unprobed, and pools with reserves.",
    method: `One pruned scan of the CL state view and one of the balances view at their latest publication dates, joined to the config registry. ${PROBED_NOTE} ${PUBLISHED_NOTE}`,
  },
  source_freshness: {
    what: "The two publication clocks: the latest CL-state publication and the latest reserves publication, with their anchor blocks and pool counts.",
    method: "Read from `census_publications` only (no view scans). A source is STALE when its latest snapshot is more than two days old.",
  },
  pools_by_class_fee: {
    what: "Pools by class (Uniswap v3, Swapr v3 / Algebra, Balancer v2 / v3) and fee band, with how many are live and how many are probed.",
    method: "Config registry joined to the latest CL state. Fee is in pips (Uniswap 100 / 500 / 3000 / 10000; Algebra fees are dynamic); Balancer pools have no fee here, so their band is null.",
  },
  probe_coverage_split: {
    what: "CL pools split four ways — probed vs state-only, live vs dead — with the median and p90 liquidity of each cell.",
    method: `Latest CL state grouped by the probe flag and liquidity > 0. ${PROBED_NOTE} Median liquidity of probed live pools is orders of magnitude above the state-only ones (7e19 vs 9e12 at design time).`,
  },
  live_pool_trend: {
    what: "Daily count of CL pools published, live and probed, plus reserves pools published, over the selected window.",
    method: `The one whole-history all-pool scan in the app (its own load group). Window presets 90d / 1y / all; 'all' spans the full publication history. ${RESERVES_HISTORY_NOTE}`,
  },
  concentration_summary: {
    what: "How concentrated liquidity is around the current price across probed pools: quartiles of the tick-weighted share within ±1% / ±5% / ±10%, ranges per pool, and how many pools hold a full-range position.",
    method: `Per pool, Σ L × overlap(range, current ± band) / Σ L × width over the profile recomputed from initialized ticks at the latest date. ${FULL_RANGE_NOTE} ${PROBED_NOTE}`,
  },
  range_width_distribution: {
    what: "Distribution of liquidity-range widths (in ticks) across probed pools: ≤10, ≤100, ≤1k, ≤10k, ≤100k, wide, full range.",
    method: "Ranges are the segments between consecutive initialized ticks with non-zero active liquidity; zero-liquidity gaps are excluded and disclosed.",
  },
  // ---- pools ----------------------------------------------------------------
  pool_directory: {
    what: "Every configured pool at the as-of date: class, fee, tokens, current tick and price, liquidity, probe status, reserves, and publication lifetime. Server-paged.",
    method: `Config registry LEFT JOIN latest CL state LEFT JOIN latest reserves LEFT JOIN probe flags, lifetimes and token metadata. 'Live' means liquidity > 0 for CL pools and any reserve > 0 for reserves-only pools, and the has_state flag separates a pool with NO published state row from one whose liquidity is genuinely zero. ${RAW_NOTE} ${PROBED_NOTE}`,
  },
  // ---- tokens ---------------------------------------------------------------
  token_directory: {
    what: "Every token that appears in a configured pool, with its metadata resolution and how many pools (CL / reserves-only / live / probed) it sits in.",
    method: "Config registry assets ARRAY JOIN'd to token metadata. Symbols are untrusted display text and are sanitized; an unresolved token renders as its short address with an 'unresolved' badge.",
  },
  // ---- coverage -------------------------------------------------------------
  coverage_summary: {
    what: "Per indexer job (CL liquidity, pool reserves): first and last snapshot, days published, pools configured vs published at the latest snapshot, and pools below the active threshold.",
    method: "Read from `census_publications` + the config registry (no view scans).",
  },
  publication_calendar: {
    what: "Per snapshot date and job: anchor block, pools published, and the integrity checks that passed (below-threshold, probed, net-sum-zero, reconciles).",
    method: "Aggregated from `census_publications` with uniqExact per day; check counts are null for the reserves job, which does not run tick checks.",
  },
  missing_days: {
    what: "Snapshot dates where a job published nothing ('absent') or far fewer pools than its recent norm ('partial').",
    method: "A generated calendar LEFT JOIN'd to daily publication counts; 'partial' = fewer than 90% of the maximum over the previous seven published days (e.g. 2026-08-23 published 1,082 of 2,519 CL pools).",
  },
  metadata_gap: {
    what: "How much of the universe token metadata actually covers: tokens with a symbol, tokens with decimals, pools whose price can be adjusted, fully labelled pools — by family.",
    method: `One config-registry ⋈ metadata pass pivoted per dimension. ${RAW_NOTE}`,
  },
  // ---- pool entity ----------------------------------------------------------
  pool_detail: {
    what: "The directory row for one pool plus its lifetime facts: days live, pool id (Balancer), and the first date a profile is available (first probed publication).",
    method: `Same joins as the directory, bound to a single pool address; N-asset pools carry their reserves as paired reserve_tokens / reserve_raw arrays. The entity label is the class plus a short address — never a token symbol. ${RAW_NOTE}`,
  },
  pool_publication_facts: {
    what: "The publication rows behind this pool's as-of snapshot: anchor block and hash, block time, publication and attempt ids, integrity mode, executor kind, how many observations the run wrote, and every check that passed.",
    method: "Both jobs' rows at the as-of date joined to the canonical day anchors. `cl_below_active_threshold` in checks_passed means the pool was NOT probed for ticks.",
  },
  pool_profile_at: {
    what: "Where the liquidity sits: one row per range between consecutive initialized ticks at the as-of date, with active liquidity, the price bounds, and flags for gaps, the range containing the current tick, and full-range positions.",
    method: `Recomputed in SQL from the published initialized ticks (running sum of liquidity_net ordered by tick + the next tick) — one contract over the full tick history rather than the derived view, which only spans 2025-09 onward. ${FULL_RANGE_NOTE} ${RAW_NOTE} ${PROBED_NOTE}`,
  },
  pool_profile_concentration: {
    what: "Tick-weighted share of this pool's liquidity within ±1% / ±5% / ±10% of the current price, the full-range share, and the liquidity active at the current tick.",
    method: `Σ L × overlap / Σ L × width per band over the recomputed profile. ${FULL_RANGE_NOTE} Null for unprobed pools.`,
  },
  pool_ticks_at: {
    what: "The initialized ticks themselves at the as-of date: gross and net liquidity, fee-growth-outside accumulators (raw), and the price at each tick.",
    method: `Read from the published tick view for one pool and date. ${PROBED_NOTE}`,
  },
  pool_state_history: {
    what: "Daily slot0-style state over the window: current tick, price, liquidity, live flag, tick count, and the global fee-growth accumulators (raw).",
    method: `Single-pool scan of the published CL state view bounded by the window. ${RAW_NOTE}`,
  },
  pool_reserves_history: {
    what: "Daily raw token balances held by the pool over the window — every token for Balancer pools (up to 8), both tokens for CL pools.",
    method: `Single-pool scan of the published balances view. Units are shown only when the token's decimals are known; otherwise raw base units are shown and flagged. ${RESERVES_HISTORY_NOTE}`,
  },
  pool_fee_growth: {
    what: "Estimated daily fees accrued to in-range liquidity, derived from the change in the global fee-growth accumulators.",
    method: "Δ fee_growth_global (UInt256, computed before conversion to float) × liquidity / 2^128 per token, between consecutive published days. Null on the first row, on a negative delta, and when liquidity is zero — an estimate over the state's own liquidity, not a fee ledger.",
  },
  pool_profile_heatmap: {
    what: "The liquidity profile over time: for up to 120 sampled probed dates, active liquidity per tick bucket along an axis spanning the observed current ticks ± 20% of price.",
    method: "Probed dates are modulo-sampled to ≤120; the axis is min/max current tick over the sample ± 1823 ticks; bucket step = max(tick spacing, span/80); each range contributes L × overlap / step to the buckets it covers (a density, not a total). Loaded on demand when the 'Over time' view is opened.",
  },
  // ---- token entity ---------------------------------------------------------
  token_detail: {
    what: "One token: metadata resolution, how many pools it sits in (CL / reserves-only / live / probed), and its total reserves across those pools.",
    method: "Config registry + token metadata + the latest reserves publication. The total is in the token's own unit (raw when decimals are unknown) — never USD.",
  },
  token_pools: {
    what: "Every pool holding this token, with its share of the token's reserves across those pools, the counter tokens, and — for CL pools — the current price of this token in the counter token (decimals-adjusted, so it is empty when either side's decimals are unknown).",
    method: "Share = this pool's reserve of the token / the token's reserves across all its pools (same unit). Liquidity (L) is never summed across pairs.",
  },
};
