// Vite dev-mode fixture (`make dev` → cow-explorer.html). Dataset shapes MUST
// mirror the server SQL projections in cow_explorer.py — a fixture column the
// server does not emit hides real bugs (lesson learned on Governance).
//
// Dev-only section override: `?section=live` / `?section=markets` renders that
// section straight from the fixture (pure mock mode has no server to apply a
// section switch through). Tests import MOCK_PAYLOAD under a default URL, so
// the exported state's section stays "overview" there.

import type { DatasetDescriptor, MiniAppPayload } from "../shared/miniAppTypes";
import { FACET_VIEWS, isCowFacet } from "./model/navGroups";
import type { CowExplorerViewState, CowSection } from "./types";

function descriptor(key: string, columns: string[], rows: unknown[][]): DatasetDescriptor {
  return {
    key, title: key.split("_").join(" "), sql: "-- development fixture", database: "cow_db",
    columns: columns.map((name) => ({ name, type: "Unknown" })),
    stats: { row_count: rows.length, rows_returned: rows.length, mode: "exact_capped", source_rows: rows.length, row_cap: 10000, truncated: false, warnings: [] },
    preview_rows: rows,
    provenance: { coverage: { actual_start: "2026-07-01T00:00:00Z", actual_end: "2026-07-20T00:00:00Z", mode: "checkpoint_bounded", warning_codes: [] } },
  };
}

const NOW = Date.now();
const iso = (secondsAgo: number) =>
  new Date(NOW - secondsAgo * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
const minuteIso = (minutesAgo: number) =>
  new Date(Math.floor(NOW / 60_000) * 60_000 - minutesAgo * 60_000)
    .toISOString().replace(/\.\d{3}Z$/, "Z");
const unix = (secondsFromNow: number) => Math.floor(NOW / 1000) + secondsFromNow;

// Real token addresses so icon-overlay lookups exercise the actual code path.
const GNO = "0x9c58bacc331c9aa871afd802db6379a98e80cedb"; // Gnosis (chain 100)
const WXDAI = "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d"; // Gnosis (chain 100)
const WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"; // Ethereum
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"; // Ethereum
const OWNER_A = "0x40a50cf069e992aa4536211b23f286ef88752187";
const OWNER_B = "0x9008d19f58aabd9ed0d60971565aa8510560ab41";
const SOLVER_A = "0x95480d3f27658e73b2785d30beb0c847d78294c7";
const SOLVER_B = "0xc9ec550bea1c64d779124b23a26292cc223327b6";
const hash = (seed: string) => `0x${seed.repeat(64).slice(0, 64)}`;
const uid = (seed: string) => `0x${seed.repeat(112).slice(0, 112)}`;

// live_minute_activity: minute x chain x fills/settlements over the last hour.
const minuteActivityRows: unknown[][] = [];
for (let m = 45; m >= 1; m -= 1) {
  const bucket = minuteIso(m);
  minuteActivityRows.push([bucket, 100, 2 + (m % 3), 1 + (m % 2), bucket, bucket, iso(m * 60 - 20)]);
  if (m % 2 === 0) minuteActivityRows.push([bucket, 1, 1 + (m % 2), 1, bucket, bucket, iso(m * 60 - 25)]);
  if (m % 7 === 0) minuteActivityRows.push([bucket, 42161, 4, 2, bucket, bucket, iso(m * 60 - 15)]);
}

const LIVE_TRADE_COLUMNS = [
  "block_timestamp", "chain_id", "tx_hash", "log_index", "order_uid", "owner",
  "sell_token", "sell_symbol", "sell_decimals", "sell_amount_raw", "sell_amount",
  "buy_token", "buy_symbol", "buy_decimals", "buy_amount_raw", "buy_amount",
  "source_observed_at",
];
const liveTradeRows: unknown[][] = [
  [iso(70), 100, hash("a1"), 3, uid("a1"), OWNER_A, GNO, "GNO", 18, "1200000000000000000", 1.2, WXDAI, "WXDAI", 18, "121680000000000000000", 121.68, iso(45)],
  [iso(160), 100, hash("a2"), 1, uid("a2"), OWNER_B, WXDAI, "WXDAI", 18, "250000000000000000000", 250, GNO, "GNO", 18, "2447000000000000000", 2.447, iso(140)],
  [iso(230), 1, hash("b1"), 7, uid("b1"), OWNER_A, WETH, "WETH", 18, "500000000000000000", 0.5, USDC, "USDC", 6, "1834520000", 1834.52, iso(210)],
  [iso(410), 100, hash("a3"), 2, uid("a3"), OWNER_B, GNO, "GNO", 18, "3000000000000000000", 3, WXDAI, "WXDAI", 18, "303900000000000000000", 303.9, iso(380)],
  [iso(600), 1, hash("b2"), 4, uid("b2"), OWNER_B, USDC, "USDC", 6, "5000000000", 5000, WETH, "WETH", 18, "1361000000000000000", 1.361, iso(560)],
  [iso(910), 42161, hash("c1"), 9, uid("c1"), OWNER_A, WETH, "WETH", 18, "120000000000000000", 0.12, USDC, "", null, "440000000", null, iso(880)],
];

const pairDepthColumns = [
  "order_uid", "owner", "kind", "side", "order_class", "partially_fillable",
  "creation_date", "valid_to", "sell_token", "buy_token", "sell_symbol",
  "buy_symbol", "sell_decimals", "buy_decimals", "price", "amount_base",
  "amount_quote", "sell_amount_raw", "buy_amount_raw", "indexed_from",
  "indexed_to", "source_observed_at",
];
// 10 known open orders for GNO/WXDAI: 5 asks (sell GNO) above 5 bids (sell
// WXDAI) — NOT crossed (best bid 99 < best ask 101); price is quote-per-base.
function depthOrder(
  seed: string, side: "ask" | "bid", price: number, amountBase: number,
  partiallyFillable: boolean, createdSecondsAgo: number,
): unknown[] {
  const amountQuote = amountBase * price;
  const sellToken = side === "ask" ? GNO : WXDAI;
  const buyToken = side === "ask" ? WXDAI : GNO;
  const sellSymbol = side === "ask" ? "GNO" : "WXDAI";
  const buySymbol = side === "ask" ? "WXDAI" : "GNO";
  const sellAmount = side === "ask" ? amountBase : amountQuote;
  const buyAmount = side === "ask" ? amountQuote : amountBase;
  return [
    uid(seed), seed.endsWith("1") ? OWNER_A : OWNER_B, side === "ask" ? "sell" : "buy", side,
    "limit", partiallyFillable, iso(createdSecondsAgo), unix(86_400),
    sellToken, buyToken, sellSymbol, buySymbol, 18, 18,
    price, amountBase, amountQuote,
    `${Math.round(sellAmount * 1e6)}000000000000`, `${Math.round(buyAmount * 1e6)}000000000000`,
    iso(createdSecondsAgo), iso(createdSecondsAgo), iso(120),
  ];
}
const pairDepthRows: unknown[][] = [
  depthOrder("d1", "ask", 101, 1.2, false, 4200),
  depthOrder("d2", "ask", 102.5, 0.8, true, 9600),
  depthOrder("d3", "ask", 104, 2.4, false, 15_000),
  depthOrder("d4", "ask", 106, 3, true, 40_000),
  depthOrder("d5", "ask", 109, 1.5, false, 88_000),
  depthOrder("e1", "bid", 99, 1.5151, true, 3600),
  depthOrder("e2", "bid", 98, 2.449, false, 8100),
  depthOrder("e3", "bid", 96.5, 0.8290, false, 21_000),
  depthOrder("e4", "bid", 95, 5.2631, true, 52_000),
  depthOrder("e5", "bid", 93, 1.2903, false, 110_000),
];

// pair_depth_heatmap: the FOOTPRINT source for GNO/WXDAI, mirroring the server
// projection — price arrives as a percent offset from each bucket's own median
// (`rel_pct`, 1.0-point bins) with that median alongside, so the client can
// render either axis mode. Like a real CoW book, asks and bids OVERLAP around
// the market rather than sitting either side of a spread, and the mid DRIFTS,
// so the dev view exercises both the split-cell encoding and the relative axis.
// Depths span three decades so the colour ladder is actually exercised.
const heatmapColumns = [
  "bucket", "bucket_mid", "rel_pct", "side", "depth_base", "orders",
  "bucket_seconds", "indexed_from", "indexed_to",
];
const heatmapRows: unknown[][] = (() => {
  const rows: unknown[][] = [];
  const stepSeconds = 6 * 3600;
  for (let b = 8; b >= 1; b -= 1) {
    const bucket = iso(b * stepSeconds);
    // The market itself moves across the window — on an absolute axis this is
    // what smears the book; on the relative axis every bucket lines up.
    const mid = 100 * (1 + (8 - b) * 0.05);
    const push = (rel: number, side: "ask" | "bid", depth: number, orders: number) =>
      rows.push([bucket, mid, rel, side, depth, orders, stepSeconds, bucket, bucket]);
    // Offsets are on the server's 1.0-point grid.
    push(-1, "ask", 0.12 + (b % 3) * 0.05, 2);
    push(0, "ask", 26 + (b % 2) * 9, 31);
    push(2, "ask", 3.1 + (b % 4) * 0.9, 7);
    push(4, "ask", 0.9 + (b % 5) * 0.2, 3);
    push(9, "ask", 140 + (b % 3) * 40, 58); // a whale level, far from market
    push(0, "bid", 22 + (b % 2) * 7, 27); // same level as an ask — overlap
    push(-1, "bid", 4.4 + (b % 3) * 1.1, 9);
    push(-3, "bid", 1.2 + (b % 4) * 0.4, 4);
    push(-6, "bid", 0.05 + (b % 5) * 0.02, 1);
  }
  return rows;
})();

// ---- v3 datasets (columns mirror the cow_explorer.py SQL projections) ----
const dayIso = (daysAgo: number) => new Date(NOW - daysAgo * 86_400_000).toISOString().slice(0, 10);
const monthStart = (monthsAgo: number) => {
  const dt = new Date(NOW);
  dt.setUTCDate(1);
  dt.setUTCMonth(dt.getUTCMonth() - monthsAgo);
  return dt.toISOString().slice(0, 10);
};

// protocol_kpis: 10 chain rows + the chain-0 protocol-wide total row.
// approx_native_volume is value-inconsistent with the 30d fixture window on
// purpose (server NULLs it beyond 7d) — dev must exercise the volume table.
const PROTOCOL_KPI_COLUMNS = [
  "chain_id", "fill_count", "settlement_transactions", "unique_traders",
  "unique_pairs", "approx_native_volume", "indexed_from", "indexed_to", "source_observed_at",
];
const protocolKpiRows: unknown[][] = [
  [0, 1_252_300, 611_200, 96_500, 8_420, null, dayIso(29), iso(70), iso(45)],
  [1, 694_210, 331_400, 52_100, 4_210, 182_500.4, dayIso(29), "2026-05-27T09:00:00Z", iso(45)],
  [56, 12_400, 6_100, 900, 120, null, null, null, iso(50)],
  [100, 496_201, 250_800, 38_400, 2_950, 1_240_800.2, dayIso(29), iso(70), iso(45)],
  [137, 8_100, 4_050, 610, 95, null, dayIso(29), iso(300), iso(60)],
  [8453, 15_800, 7_400, 1_150, 160, null, dayIso(29), iso(240), iso(55)],
  [9745, 900, 460, 80, 14, null, dayIso(29), iso(500), iso(70)],
  [42161, 21_300, 10_100, 1_600, 210, 96.4, dayIso(29), iso(900), iso(45)],
  [43114, 1_450, 720, 130, 22, null, dayIso(29), iso(700), iso(65)],
  [57073, 320, 160, 30, 8, null, dayIso(29), iso(1100), iso(80)],
  [59144, 610, 310, 55, 12, null, dayIso(29), iso(1000), iso(75)],
];

// alltime_chain_totals: BNB (56) deliberately ships NULL first/last (its
// trade rows lack block timestamps); Ethereum's last trade is ~2 months old
// so the data-driven staleness strip has something to derive.
const ALLTIME_COLUMNS = [
  "chain_id", "fill_count", "settlement_transactions", "unique_traders",
  "first_trade_at", "last_trade_at", "indexed_from", "indexed_to", "source_observed_at",
];
const alltimeRows: unknown[][] = [
  [1, 9_412_800, 4_505_100, 512_300, "2021-03-05T11:20:00Z", "2026-05-27T09:00:00Z", "2021-03-05T11:20:00Z", "2026-05-27T09:00:00Z", iso(45)],
  [56, 96_200, 47_800, 6_400, null, null, null, null, iso(50)],
  [100, 2_612_400, 1_310_500, 148_900, "2021-05-12T08:00:00Z", iso(70), "2021-05-12T08:00:00Z", iso(70), iso(45)],
  [42161, 402_100, 199_800, 31_200, "2024-04-30T16:00:00Z", iso(900), "2024-04-30T16:00:00Z", iso(900), iso(45)],
];

// chain_share_trend: ~30 daily buckets x 3 chains. Ethereum stops emitting
// rows mid-window (stale indexer) — the stacked chart flatlines it honestly.
const SHARE_TREND_COLUMNS = [
  "bucket", "chain_id", "fill_count", "settlement_transactions",
  "indexed_from", "indexed_to", "source_observed_at",
];
const shareTrendRows: unknown[][] = [];
for (let d = 29; d >= 1; d -= 1) {
  const bucket = dayIso(d);
  if (d > 14) shareTrendRows.push([bucket, 1, 90 + (d % 5) * 10, 44 + (d % 5) * 5, bucket, bucket, iso(45)]);
  shareTrendRows.push([bucket, 100, 40 + (d % 7) * 6, 20 + (d % 7) * 3, bucket, bucket, iso(45)]);
  shareTrendRows.push([bucket, 42161, 25 + (d % 3) * 8, 12 + (d % 3) * 4, bucket, bucket, iso(45)]);
}

// ---- orders facet: types / programmatic / class_quality ----
const ORDER_TYPE_SUMMARY_COLUMNS = [
  "chain_id", "order_class", "order_count", "owners", "fulfilled", "expired",
  "cancelled", "open_now", "fulfilled_share", "partially_fillable_count",
  "indexed_from", "indexed_to", "source_observed_at",
];
const orderTypeSummaryRows: unknown[][] = [
  [100, "limit", 32_100, 5_400, 21_500, 7_900, 2_400, 300, 0.6698, 9_800, dayIso(29), iso(600), iso(60)],
  [100, "market", 4_800, 2_900, 4_460, 260, 60, 20, 0.9292, 150, dayIso(29), iso(600), iso(60)],
  [1, "limit", 36_400, 8_100, 20_900, 12_300, 3_000, 200, 0.5742, 11_200, dayIso(29), "2026-05-27T09:00:00Z", iso(60)],
  [1, "market", 4_500, 3_200, 4_180, 240, 60, 20, 0.9289, 90, dayIso(29), "2026-05-27T09:00:00Z", iso(60)],
  [42161, "limit", 1_900, 700, 1_240, 520, 130, 10, 0.6526, 480, dayIso(29), iso(900), iso(60)],
];
const ORDER_FLAVOR_COLUMNS = [
  "chain_id", "order_kind", "signing_scheme", "partially_fillable",
  "order_count", "owners", "fulfilled_share", "indexed_from", "indexed_to", "source_observed_at",
];
const orderFlavorRows: unknown[][] = [
  [100, "sell", "eip712", false, 21_400, 4_900, 0.71, dayIso(29), iso(600), iso(60)],
  [100, "sell", "eip1271", true, 8_200, 610, 0.54, dayIso(29), iso(600), iso(60)],
  [100, "buy", "eip712", false, 5_100, 2_300, 0.82, dayIso(29), iso(600), iso(60)],
  [100, "sell", "presign", false, 1_600, 240, 0.61, dayIso(29), iso(600), iso(60)],
  [1, "sell", "eip712", false, 26_800, 7_200, 0.62, dayIso(29), "2026-05-27T09:00:00Z", iso(60)],
  [1, "buy", "ethsign", false, 2_100, 1_400, 0.77, dayIso(29), "2026-05-27T09:00:00Z", iso(60)],
];
const ORDER_TYPE_TREND_COLUMNS = [
  "bucket", "chain_id", "order_class", "order_count", "fulfilled_count",
  "indexed_from", "indexed_to", "source_observed_at",
];
const orderTypeTrendRows: unknown[][] = [];
for (let d = 14; d >= 1; d -= 1) {
  const bucket = dayIso(d);
  orderTypeTrendRows.push([bucket, 100, "limit", 80 + (d % 6) * 12, 50 + (d % 6) * 7, bucket, bucket, iso(60)]);
  orderTypeTrendRows.push([bucket, 100, "market", 14 + (d % 4) * 3, 12 + (d % 4) * 3, bucket, bucket, iso(60)]);
  if (d > 7) orderTypeTrendRows.push([bucket, 1, "limit", 120 + (d % 5) * 9, 70 + (d % 5) * 5, bucket, bucket, iso(60)]);
}
const CONDITIONAL_COLUMNS = [
  "bucket", "chain_id", "event_type", "events", "creators",
  "indexed_from", "indexed_to", "source_observed_at",
];
const conditionalRows: unknown[][] = [];
for (let d = 14; d >= 1; d -= 1) {
  const bucket = dayIso(d);
  conditionalRows.push([bucket, 100, "ConditionalOrderCreated", 6 + (d % 5), 2 + (d % 3), bucket, bucket, iso(60)]);
  if (d % 3 === 0) conditionalRows.push([bucket, 100, "OrderInvalidation", 1 + (d % 2), 1, bucket, bucket, iso(60)]);
  if (d % 5 === 0) conditionalRows.push([bucket, 1, "MerkleRootSet", 2, 1, bucket, bucket, iso(60)]);
}
const APPDATA_CLASS_COLUMNS = [
  "chain_id", "order_class", "orders", "owners", "appdata_hashes", "source_observed_at",
];
const appdataClassRows: unknown[][] = [
  [100, "market", 14_200, 5_600, 310, iso(60)],
  [100, "limit", 6_100, 1_900, 140, iso(60)],
  [100, "twap", 38, 9, 6, iso(60)],
  [100, "untagged", 2_400, 800, 55, iso(60)],
  [100, "unresolved", 14_162, 4_100, 0, iso(60)],
  [1, "market", 16_800, 7_900, 420, iso(60)],
  [1, "unresolved", 24_100, 6_200, 0, iso(60)],
];
const SURPLUS_BY_CLASS_COLUMNS = [
  "order_class", "surplus_bucket", "fills", "avg_surplus_bps", "median_surplus_bps",
  "indexed_from", "indexed_to", "source_observed_at",
];
const surplusByClassRows: unknown[][] = [
  ["limit", "< -50 bps", 120, -96.2, -71.5, dayIso(29), iso(600), iso(60)],
  ["limit", "-50-0 bps", 890, -14.1, -9.8, dayIso(29), iso(600), iso(60)],
  ["limit", "0-10 bps", 4_210, 4.2, 3.9, dayIso(29), iso(600), iso(60)],
  ["limit", "10-50 bps", 2_150, 24.8, 21.2, dayIso(29), iso(600), iso(60)],
  ["limit", "50-200 bps", 640, 96.1, 82.4, dayIso(29), iso(600), iso(60)],
  ["limit", "> 200 bps", 88, 410.6, 310.2, dayIso(29), iso(600), iso(60)],
  ["market", "0-10 bps", 2_960, 3.1, 2.8, dayIso(29), iso(600), iso(60)],
  ["market", "10-50 bps", 1_310, 22.4, 18.9, dayIso(29), iso(600), iso(60)],
  ["market", "unknown", 240, null, null, dayIso(29), iso(600), iso(60)],
];

// ---- solver directory (mix of registry-known / unknown; Ethereum's anchor
// is STALE so per-chain-anchor activity honesty is exercised in dev) ----
const ETH_ANCHOR = "2026-05-27T09:00:00Z";
const FRACTAL_GNO = "0x727eb77c6f84ef148403f641aa32d75b7f6902a7"; // Fractal prod (100)
const COPIUM_GNO = "0xb4694fe6590acd1281dc34a966bbae224559bad4"; // Copium prod (100)
const COPIUM_GNO_BARN = "0x53f5378a6f8bb24333ad8d68fd28816504a467b2"; // Copium barn (100)
const SEASOLVER_GNO = "0xe3068acb5b5672408eadad4417e7d3ba41d4febe"; // Seasolver prod (100)
const BASELINE_GNO_BARN = "0x2dd00f9f614e2d8e3ab14fbae1fda36395e76b85"; // Baseline barn (100)
const RIZZOLVER = "0x4dd1be0cd607e5382dd2844fa61d3a17e3e83d56"; // Rizzolver prod (1 + 42161)
const TSOLVER_ARB = "0x3980daa7eaad0b7e0c53cfc5c2760037270da54d"; // Tsolver prod (42161)
const UNKNOWN_SOLVER = "0xdeadbeef00000000000000000000000000000001"; // not in registry
const SOLVER_DIRECTORY_COLUMNS = [
  "chain_id", "solver", "first_settlement_at", "last_settlement_at",
  "settlements_all_time", "competitions_all", "wins_all", "chain_anchor_at",
  "indexed_from", "indexed_to", "source_observed_at",
];
const solverDirectoryRows: unknown[][] = [
  [100, FRACTAL_GNO, "2024-02-10T10:00:00Z", iso(7200), 48_200, 61_500, 14_200, iso(3600), "2024-02-10T10:00:00Z", iso(7200), iso(60)],
  // Fractal on the STALE Ethereum chain: last settlement 2h before the
  // chain's own anchor -> ACTIVE despite being ~2 months behind wall clock.
  [1, "0x95480d3f27658e73b2785d30beb0c847d78294c7", "2023-06-01T00:00:00Z", "2026-05-27T07:00:00Z", 96_400, 120_800, 31_200, ETH_ANCHOR, "2023-06-01T00:00:00Z", "2026-05-27T07:00:00Z", iso(60)],
  [1, RIZZOLVER, "2024-01-15T00:00:00Z", "2026-04-02T12:00:00Z", 22_100, 30_400, 6_100, ETH_ANCHOR, "2024-01-15T00:00:00Z", "2026-04-02T12:00:00Z", iso(60)],
  [42161, RIZZOLVER, "2024-06-20T00:00:00Z", iso(5400), 9_800, 14_100, 2_900, iso(1800), "2024-06-20T00:00:00Z", iso(5400), iso(60)],
  [100, COPIUM_GNO, "2023-11-02T00:00:00Z", iso(20 * 86_400), 15_600, 21_900, 4_400, iso(3600), "2023-11-02T00:00:00Z", iso(20 * 86_400), iso(60)],
  [100, COPIUM_GNO_BARN, "2024-05-14T00:00:00Z", iso(86_400), 310, 900, 40, iso(3600), "2024-05-14T00:00:00Z", iso(86_400), iso(60)],
  [100, SEASOLVER_GNO, "2022-08-01T00:00:00Z", iso(10_800), 61_400, 78_100, 19_800, iso(3600), "2022-08-01T00:00:00Z", iso(10_800), iso(60)],
  [42161, TSOLVER_ARB, "2024-09-01T00:00:00Z", iso(45 * 86_400), 4_100, 6_800, 800, iso(1800), "2024-09-01T00:00:00Z", iso(45 * 86_400), iso(60)],
  [100, UNKNOWN_SOLVER, "2026-06-30T00:00:00Z", iso(14_400), 420, 610, 96, iso(3600), "2026-06-30T00:00:00Z", iso(14_400), iso(60)],
  // Competition-only entry: no settlements observed (first/last NULL).
  [100, BASELINE_GNO_BARN, null, null, 0, 1_240, 0, iso(3600), null, null, iso(60)],
  [1, "0xc9ec550bea1c64d779124b23a26292cc223327b6", "2022-04-01T00:00:00Z", "2026-04-17T00:00:00Z", 51_300, 70_200, 15_400, ETH_ANCHOR, "2022-04-01T00:00:00Z", "2026-04-17T00:00:00Z", iso(60)],
];
const SCORE_GAP_COLUMNS = [
  "chain_id", "competition_solver", "wins_scored", "parse_failures",
  "avg_score_gap", "median_score_gap", "p90_score_gap",
  "indexed_from", "indexed_to", "source_observed_at",
];
const scoreGapRows: unknown[][] = [
  [100, FRACTAL_GNO, 14_200, 12, 1.24e15, 8.1e14, 4.6e15, dayIso(29), iso(7200), iso(60)],
  [100, SEASOLVER_GNO, 19_800, 4, 9.4e14, 6.2e14, 3.1e15, dayIso(29), iso(10_800), iso(60)],
  [42161, RIZZOLVER, 2_900, 41, 2.02e15, 1.4e15, 6.8e15, dayIso(29), iso(5400), iso(60)],
];

// ---- trader dynamics + retention (12-month triangle) ----
const TRADER_DYNAMICS_COLUMNS = [
  "period", "active_traders", "new_traders", "returning_traders",
  "reactivated_traders", "churned_traders", "quick_ratio", "retention_rate",
  "indexed_from", "indexed_to", "source_observed_at",
];
const traderDynamicsRows: unknown[][] = [];
for (let m = 11; m >= 0; m -= 1) {
  const period = monthStart(m);
  const active = 5200 + (11 - m) * 240 + (m % 3) * 90;
  const fresh = 900 + (m % 4) * 120;
  const reactivated = 260 + (m % 3) * 40;
  const returning = active - fresh - reactivated;
  const churned = 700 + (m % 5) * 130;
  traderDynamicsRows.push([
    period, active, fresh, returning, reactivated, churned,
    Number(((fresh + reactivated) / churned).toFixed(3)),
    Number((returning / (returning + churned)).toFixed(3)),
    period, period, iso(60),
  ]);
}
const TRADER_RETENTION_COLUMNS = [
  "cohort_month", "month_index", "cohort_size", "active_traders", "retention_share",
  "indexed_from", "indexed_to", "source_observed_at",
];
const traderRetentionRows: unknown[][] = [];
for (let c = 11; c >= 0; c -= 1) {
  const cohort = monthStart(c);
  const size = 800 + (c % 5) * 160;
  for (let k = 0; k <= c; k += 1) {
    const share = k === 0 ? 1 : Math.max(0.04, 0.42 * Math.pow(0.82, k - 1) + (c % 3) * 0.01);
    traderRetentionRows.push([
      cohort, k, size, Math.round(size * share), Number(share.toFixed(4)),
      cohort, monthStart(c - k), iso(60),
    ]);
  }
}

const CHAIN_OPTIONS = [
  { chain_id: 1, name: "Ethereum", native_symbol: "ETH", environment: "production" as const, explorer: { provider: "blockscout" as const, brand: "Blockscout", base_url: "https://eth.blockscout.com", transaction_url_template: "https://eth.blockscout.com/tx/{hash}", address_url_template: "https://eth.blockscout.com/address/{address}", token_url_template: "https://eth.blockscout.com/token/{address}" } },
  { chain_id: 100, name: "Gnosis", native_symbol: "xDAI", environment: "production" as const, explorer: { provider: "blockscout" as const, brand: "Gnosisscan", base_url: "https://gnosis.blockscout.com", transaction_url_template: "https://gnosis.blockscout.com/tx/{hash}", address_url_template: "https://gnosis.blockscout.com/address/{address}", token_url_template: "https://gnosis.blockscout.com/token/{address}" } },
  { chain_id: 42161, name: "Arbitrum", native_symbol: "ETH", environment: "production" as const, explorer: { provider: "blockscout" as const, brand: "Blockscout", base_url: "https://arbitrum.blockscout.com", transaction_url_template: "https://arbitrum.blockscout.com/tx/{hash}", address_url_template: "https://arbitrum.blockscout.com/address/{address}", token_url_template: "https://arbitrum.blockscout.com/token/{address}" } },
];

const DEV_SECTIONS = new Set<string>([
  "live", "overview", "markets", "trades", "orders", "auctions", "solvers", "traders", "patterns",
]);

/** Dev-only: `?section=…` picks the fixture's rendered section (pure mock
 * mode cannot apply a section switch through the server), and `?facet=…`
 * alone implies its HOST section (order_types→orders, solver_directory→
 * solvers, trader_dynamics→traders) so a facet deep link renders without
 * also spelling out the section. Defaults to overview — which is also what
 * tests importing MOCK_PAYLOAD observe. */
function devSection(): Exclude<CowSection, "entity"> {
  if (typeof window === "undefined") return "overview";
  const search = new URLSearchParams(window.location.search);
  const requested = search.get("section") ?? "";
  if (DEV_SECTIONS.has(requested)) return requested as Exclude<CowSection, "entity">;
  const facet = search.get("facet") ?? "";
  if (isCowFacet(facet)) return FACET_VIEWS[facet].section;
  return "overview";
}

export const MOCK_PAYLOAD: MiniAppPayload<CowExplorerViewState> = {
  type: "INITIAL_LOAD", view_id: "cow-dev", app_id: "cow_explorer", title: "CoW Data Explorer", status: "ready",
  datasets: {
    network_summary: descriptor("network_summary", ["chain_id", "trade_count", "order_count", "competition_count_all_indexed"], [[1, 694210, 522000, 10220], [100, 496201, 310400, 8110]]),
    network_activity: descriptor("network_activity", ["bucket", "chain_id", "trade_count"], [["2026-07-18", 1, 820], ["2026-07-19", 1, 910]]),
    top_pairs: descriptor(
      "top_pairs",
      ["chain_id", "token0", "token1", "token0_symbol", "token1_symbol", "fill_count"],
      [
        [1, WETH, USDC, "WETH", "USDC", 12000],
        [100, GNO, WXDAI, "GNO", "WXDAI", 5321],
        [100, WXDAI, "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "WXDAI", "", 1830],
        [42161, WETH, USDC, "WETH", "USDC", 2210],
      ],
    ),
    // ---- overview v3: protocol aggregates + share trend -------------------
    protocol_kpis: descriptor("protocol_kpis", PROTOCOL_KPI_COLUMNS, protocolKpiRows),
    alltime_chain_totals: descriptor("alltime_chain_totals", ALLTIME_COLUMNS, alltimeRows),
    chain_share_trend: descriptor("chain_share_trend", SHARE_TREND_COLUMNS, shareTrendRows),
    // ---- orders v3: types / programmatic / class_quality ------------------
    order_type_summary: descriptor("order_type_summary", ORDER_TYPE_SUMMARY_COLUMNS, orderTypeSummaryRows),
    order_flavor_mix: descriptor("order_flavor_mix", ORDER_FLAVOR_COLUMNS, orderFlavorRows),
    order_type_trend: descriptor("order_type_trend", ORDER_TYPE_TREND_COLUMNS, orderTypeTrendRows),
    conditional_order_activity: descriptor("conditional_order_activity", CONDITIONAL_COLUMNS, conditionalRows),
    appdata_order_classes: descriptor("appdata_order_classes", APPDATA_CLASS_COLUMNS, appdataClassRows),
    surplus_by_class: descriptor("surplus_by_class", SURPLUS_BY_CLASS_COLUMNS, surplusByClassRows),
    // ---- solvers v3: directory + score gaps -------------------------------
    solver_directory: descriptor("solver_directory", SOLVER_DIRECTORY_COLUMNS, solverDirectoryRows),
    solver_score_gaps: descriptor("solver_score_gaps", SCORE_GAP_COLUMNS, scoreGapRows),
    // ---- traders v3: growth accounting + retention triangle ---------------
    trader_dynamics: descriptor("trader_dynamics", TRADER_DYNAMICS_COLUMNS, traderDynamicsRows),
    trader_retention: descriptor("trader_retention", TRADER_RETENTION_COLUMNS, traderRetentionRows),
    // ---- live (all-networks: every row carries chain_id) ------------------
    live_pulse: descriptor(
      "live_pulse",
      ["chain_id", "checkpoint_block", "checkpoint_timestamp", "checkpoint_updated_at", "lag_seconds"],
      [
        [1, 20_988_101, iso(42), iso(12), 42],
        [100, 41_552_390, iso(18), iso(6), 18],
        [42161, 261_004_477, iso(1900), iso(200), 1900],
        [8453, null, null, null, null],
      ],
    ),
    live_trades: descriptor("live_trades", LIVE_TRADE_COLUMNS, liveTradeRows),
    live_settlements: descriptor(
      "live_settlements",
      ["block_timestamp", "chain_id", "tx_hash", "block_number", "settlement_executor", "fill_count", "source_observed_at"],
      [
        [iso(70), 100, hash("a1"), 41_552_380, SOLVER_A, 2, iso(45)],
        [iso(230), 1, hash("b1"), 20_988_090, SOLVER_B, 1, iso(210)],
        [iso(410), 100, hash("a3"), 41_552_310, SOLVER_A, 3, iso(380)],
        [iso(910), 42161, hash("c1"), 261_004_200, SOLVER_B, 1, iso(880)],
      ],
    ),
    live_minute_activity: descriptor(
      "live_minute_activity",
      ["bucket", "chain_id", "fills", "settlements", "indexed_from", "indexed_to", "source_observed_at"],
      minuteActivityRows,
    ),
    live_open_orders: descriptor(
      "live_open_orders",
      ["order_uid", "chain_id", "owner", "kind", "status", "creation_date", "valid_to", "partially_fillable", "sell_token", "sell_symbol", "sell_decimals", "sell_amount_raw", "sell_amount", "buy_token", "buy_symbol", "buy_decimals", "buy_amount_raw", "buy_amount", "fill_ratio", "source_observed_at"],
      [
        [uid("f1"), 100, OWNER_A, "sell", "open", iso(600), unix(86_400), true, GNO, "GNO", 18, "5000000000000000000", 5, WXDAI, "WXDAI", 18, "515000000000000000000", 515, 0.4, iso(60)],
        [uid("f2"), 100, OWNER_B, "buy", "open", iso(2400), unix(43_200), false, WXDAI, "WXDAI", 18, "200000000000000000000", 200, GNO, "GNO", 18, "1960000000000000000", 1.96, 0, iso(60)],
        [uid("f3"), 1, OWNER_B, "sell", "open", iso(5400), unix(7200), false, WETH, "WETH", 18, "2000000000000000000", 2, USDC, "USDC", 6, "7350000000", 7350, 0.12, iso(60)],
        [uid("f4"), 42161, OWNER_A, "sell", "open", iso(9000), unix(3600), true, USDC, "", null, "900000000", null, WETH, "WETH", 18, "240000000000000000", 0.24, 0, iso(60)],
      ],
    ),
    live_order_events: descriptor(
      "live_order_events",
      ["event_type", "chain_id", "order_uid", "owner", "block_number", "transaction_hash", "event_timestamp", "source_observed_at"],
      [
        ["Trade", 100, uid("a1"), OWNER_A, 41_552_380, hash("a1"), iso(70), iso(45)],
        ["status:fulfilled", 100, uid("a1"), OWNER_A, null, "", null, iso(40)],
        ["status:open", 100, uid("f1"), OWNER_A, null, "", null, iso(600)],
        ["OrderInvalidation", 1, uid("g1"), OWNER_B, 20_988_050, hash("b3"), iso(1500), iso(1450)],
        ["status:cancelled", 42161, uid("g2"), OWNER_A, null, "", null, iso(2600)],
      ],
    ),
    // ---- markets (pair GNO/WXDAI on Gnosis) -------------------------------
    market_summary: descriptor("market_summary", ["fill_count", "first_fill_at", "last_fill_at"], [[5321, "2026-06-02T00:00:00Z", iso(70)]]),
    pair_options: descriptor("pair_options", ["token0", "token1", "token0_symbol", "token1_symbol", "fill_count"], [[GNO, WXDAI, "GNO", "WXDAI", 5321]]),
    price_candles: descriptor("price_candles", ["bucket", "open", "close", "low", "high", "vwap", "base_volume", "quote_volume", "fill_count"], []),
    auction_reference_prices: descriptor("auction_reference_prices", ["bucket", "price", "source_observed_at"], []),
    native_reference_prices: descriptor(
      "native_reference_prices",
      ["bucket", "price", "source_observed_at"],
      [
        [iso(14_400), 99.4, iso(14_390)],
        [iso(10_800), 99.9, iso(10_790)],
        [iso(7200), 100.6, iso(7190)],
        [iso(3600), 100.2, iso(3590)],
        [iso(900), 100.4, iso(880)],
      ],
    ),
    recent_market_trades: descriptor("recent_market_trades", ["block_timestamp", "tx_hash", "owner", "sell_token", "sell_symbol", "buy_token", "buy_symbol"], []),
    pair_depth: descriptor("pair_depth", pairDepthColumns, pairDepthRows),
    pair_depth_heatmap: descriptor("pair_depth_heatmap", heatmapColumns, heatmapRows),
    depth_horizon: descriptor(
      "depth_horizon",
      ["earliest_supported_at", "latest_observed_at", "captured_orders", "earliest_creation_seen", "source_observed_at"],
      [[iso(216_000), iso(30), 1287, "2026-03-02T09:12:00Z", iso(30)]],
    ),
    open_intent_pairs: descriptor(
      "open_intent_pairs",
      ["token0", "token1", "token0_symbol", "token1_symbol", "open_orders", "source_observed_at"],
      [
        [GNO, WXDAI, "GNO", "WXDAI", 12, iso(30)],
        ["0x" + "aa".repeat(20), WXDAI, "WETH", "WXDAI", 7, iso(30)],
        ["0x" + "bb".repeat(20), "0x" + "cc".repeat(20), "sDAI", "wstETH", 3, iso(30)],
      ],
    ),
  },
  view_state: {
    section: devSection(), environment_scope: "production", environment: "production", chain_id: 0, chain_name: "All networks",
    chain_options: CHAIN_OPTIONS,
    explorer: null,
    pair: { base: GNO, quote: WXDAI, base_symbol: "GNO", quote_symbol: "WXDAI", base_decimals: 18, quote_decimals: 18 },
    interval: "1h",
    date_range: { kind: "relative", anchor: "latest_indexed", window_days: 30, start_at: "", end_at: "" },
    filters: { status: "", owner: "", solver: "", token: "" }, selected_entity: null, breadcrumbs: [],
    search: { query: "", candidates: [] }, applied_request_id: 0, scope_id: "production:0:overview:0",
    heatmap_window: "7d",
    coverage: {}, coverage_warnings: ["partial_backfill"], warnings: ["partial_backfill"],
    dataset_revisions: {
      network_summary: 1, network_activity: 1, top_pairs: 1,
      protocol_kpis: 1, alltime_chain_totals: 1, chain_share_trend: 1,
      live_pulse: 1, live_trades: 1, live_settlements: 1, live_minute_activity: 1,
      live_open_orders: 1, live_order_events: 1,
      market_summary: 1, pair_options: 1, price_candles: 1,
      auction_reference_prices: 1, native_reference_prices: 1,
      recent_market_trades: 1, pair_depth: 1, pair_depth_heatmap: 1, depth_horizon: 1, open_intent_pairs: 1,
      order_type_summary: 1, order_flavor_mix: 1, order_type_trend: 1,
      conditional_order_activity: 1, appdata_order_classes: 1, surplus_by_class: 1,
      solver_directory: 1, solver_score_gaps: 1,
      trader_dynamics: 1, trader_retention: 1,
    },
    loaded_groups: {
      "overview.core": true, "overview.breakdown": true,
      "overview.protocol": true, "overview.share": true,
      "markets.core": true, "markets.charts": true, "markets.depth": true, "markets.depth_heatmap": true, "markets.tape": true,
      "trades.core": false, "trades.tape": false,
      "orders.core": false, "orders.intents": false, "orders.quality": false,
      "orders.types": true, "orders.programmatic": true, "orders.class_quality": true,
      "auctions.core": false, "auctions.list": false,
      "solvers.core": false, "solvers.detail": false,
      "solvers.directory": true, "solvers.quality": true,
      "traders.core": false,
      "traders.dynamics": true, "traders.retention": true,
      "patterns.core": false, "patterns.affinity": false, "patterns.quality": false,
      "live.core": true, "live.feed": true, "live.intents": true,
    },
    section_fingerprints: { overview: "dev", live: "dev", markets: "dev" },
    section_datasets: {
      overview: ["network_summary", "network_activity", "top_pairs", "protocol_kpis", "alltime_chain_totals", "chain_share_trend"],
      live: ["live_pulse", "live_trades", "live_settlements", "live_minute_activity", "live_open_orders", "live_order_events"],
      markets: ["market_summary", "pair_options", "price_candles", "auction_reference_prices", "native_reference_prices", "recent_market_trades", "pair_depth", "pair_depth_heatmap", "depth_horizon", "open_intent_pairs"],
      orders: ["order_type_summary", "order_flavor_mix", "order_type_trend", "conditional_order_activity", "appdata_order_classes", "surplus_by_class"],
      solvers: ["solver_directory", "solver_score_gaps"],
      traders: ["trader_dynamics", "trader_retention"],
    },
    section_lru: ["overview", "live", "markets"],
    icon_overlay: {
      "1": {
        [WETH]: "https://coin-images.coingecko.com/coins/images/2518/thumb/weth.png",
        [USDC]: "https://coin-images.coingecko.com/coins/images/6319/thumb/usdc.png",
      },
      "100": {
        [GNO]: "https://coin-images.coingecko.com/coins/images/662/thumb/logo_square_simple_300px.png",
        [WXDAI]: "https://coin-images.coingecko.com/coins/images/11062/thumb/Identity-Primary-DarkBG.png",
      },
    },
    depth_at: "",
    dataset_titles: {},
  },
};
