import { describe, expect, it } from "vitest";

import { rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import {
  bucketsOf,
  chainHistory,
  chainsIn,
  sparkValues,
  tokenSeries,
  walletSeries,
} from "../model/treasuryHistory";

/** Last point of a series. Not `.at(-1)`: the tsconfig lib is ES2020. */
function lastUnits(points: Array<{ units: number | null }>): number | null {
  return points.length === 0 ? null : points[points.length - 1].units;
}

/** Shuffle columns (with rows re-ordered to match) — transforms must not care. */
function shuffled(dataset: RowDataset): RowDataset {
  const order = dataset.columns.map((_, i) => i).reverse();
  return {
    columns: order.map((i) => dataset.columns[i]),
    rows: dataset.rows.map((row) => order.map((i) => row[i])),
  };
}

// Real treasury addresses. COW is deliberately CHECKSUMMED and the fake USDC
// deliberately spells its symbol with a zero-width space — both are the shapes
// this module has to normalize before it can be trusted.
const GNO = "0x6810e776880c02933d47db1b9fcd908a5a1ce6c8";
const COW = "0xDEf1CA1fb7FBcDC777520aa7f396b4E015F497aB";
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48";
const USDC_FAKE = "0xdeadbeef00000000000000000000000000000001";
const SAFE = "0x5aFE3855358E112B5647B952709E6165e1c1eEEe";
const WSTETH = "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0";
const UNNAMED = "0x1111111111111111111111111111111111111111";

const SPOT: Record<string, number> = {
  [GNO]: 100,
  [COW.toLowerCase()]: 0.4,
  [USDC]: 1,
  [SAFE.toLowerCase()]: 0.5,
  [WSTETH]: 3000,
  // USDC_FAKE and UNNAMED are absent: every spoofed token in the real treasury
  // is unpriced, which is exactly why the priced/unpriced split is a safety
  // signal and not just a coverage gap.
};

const priceOf = (token: string): number | null => SPOT[token] ?? null;

// chain 1 runs to 2026-07; chain 100 stopped publishing in 2022-11. The two
// share buckets, which is the case that would silently collapse under a
// bucket-keyed pivot.
const CHAIN_HISTORY: RowDataset = {
  columns: [
    "chain_id", "bucket", "anchor_block", "tokens_held", "tokens_named",
    "wallets_holding", "positions", "gno_units", "gno_units_ex_ltd",
  ],
  rows: [
    [100, "2022-11-01", 25311100, 14, 12, 3, 21, 1200, 1200],
    [1, "2026-07-01", 23001111, 231, 226, 9, 402, 310000, 240000],
    [1, "2022-10-01", 15700000, 88, null, 6, 140, 290000, 220000],
    [100, "2022-10-01", 24900000, 13, 11, 3, 19, 1150, 1150],
    // Out of order on purpose: the transform sorts, the query need not.
    [1, "2022-11-01", 15900000, 91, 84, 6, 147, 292000, 222000],
  ],
};

/** treasury_token_history rows for one (chain, bucket, token). */
function tokenRow(
  chainId: number,
  bucket: string,
  token: string,
  symbol: string,
  decimals: number | null,
  units: number | null,
  raw: string,
  wallets: number,
): unknown[] {
  return [
    chainId, bucket, token, symbol, decimals,
    decimals === null ? "unknown" : "resolved",
    units, raw, wallets,
  ];
}

const TOKEN_HISTORY: RowDataset = {
  columns: [
    "chain_id", "bucket", "token_address", "symbol", "decimals",
    "metadata_status", "balance_units", "balance_total_raw", "wallets_holding",
  ],
  rows: [
    tokenRow(1, "2026-05-01", GNO, "GNO", 18, 300000, "300000000000000000000000", 5),
    tokenRow(1, "2026-06-01", GNO, "GNO", 18, 305000, "305000000000000000000000", 5),
    tokenRow(1, "2026-07-01", GNO, "GNO", 18, 310000, "310000000000000000000000", 6),
    tokenRow(1, "2026-05-01", COW, "COW", 18, 10_000_000, "10000000000000000000000000", 3),
    tokenRow(1, "2026-06-01", COW, "COW", 18, 11_000_000, "11000000000000000000000000", 3),
    tokenRow(1, "2026-07-01", COW, "COW", 18, 12_000_000, "12000000000000000000000000", 3),
    // decimals observed only from June on — the May bucket is unscalable, and
    // must stay a gap rather than becoming a zero balance.
    tokenRow(1, "2026-05-01", WSTETH, "wstETH", null, null, "400000000000000000000", 2),
    tokenRow(1, "2026-06-01", WSTETH, "wstETH", 18, 400, "400000000000000000000", 2),
    tokenRow(1, "2026-07-01", WSTETH, "wstETH", 18, 410, "410000000000000000000", 2),
    tokenRow(1, "2026-05-01", USDC, "USDC", 6, 600000, "600000000000", 4),
    tokenRow(1, "2026-06-01", USDC, "USDC", 6, 620000, "620000000000", 4),
    tokenRow(1, "2026-07-01", USDC, "USDC", 6, 621000, "621000000000", 4),
    // Same sanitized symbol, different address: a zero-width space is the whole
    // disguise. Unpriced, as every spoof in the real treasury is.
    tokenRow(1, "2026-05-01", USDC_FAKE, "US​DC", 6, 1_000_000_000, "1000000000000000", 1),
    tokenRow(1, "2026-06-01", USDC_FAKE, "US​DC", 6, 1_000_000_000, "1000000000000000", 1),
    tokenRow(1, "2026-07-01", USDC_FAKE, "US​DC", 6, 1_000_000_000, "1000000000000000", 1),
    // Priced, but gone by the latest bucket — must not be ranked on a balance
    // the treasury no longer holds.
    tokenRow(1, "2026-05-01", SAFE, "SAFE", 18, 8_000_000, "8000000000000000000000000", 2),
    tokenRow(1, "2026-06-01", SAFE, "SAFE", 18, 8_000_000, "8000000000000000000000000", 2),
    tokenRow(1, "2026-06-01", UNNAMED, "", null, null, "5000000000000000000", 1),
    tokenRow(1, "2026-07-01", UNNAMED, "", null, null, "5000000000000000000", 1),
    // Chain 100: same token address, different balances, stale bucket.
    tokenRow(100, "2022-11-01", GNO, "GNO", 18, 12, "12000000000000000000", 1),
  ],
};

const TREASURY_SAFE = "0xaaaa000000000000000000000000000000000001";
const LTD_WALLET = "0xbbbb000000000000000000000000000000000002";
const DORMANT = "0xcccc000000000000000000000000000000000003";
const GC_WALLET = "0xdddd000000000000000000000000000000000004";

const WALLET_HISTORY: RowDataset = {
  columns: ["chain_id", "bucket", "wallet_address", "is_ltd", "units", "units_raw"],
  rows: [
    // The folded tail out-masses every named wallet — the case that must still
    // sort last.
    [1, "2026-06-01", "other", 0, 500000, "500000000000000000000000"],
    [1, "2026-07-01", "other", 0, 520000, "520000000000000000000000"],
    [1, "2026-06-01", TREASURY_SAFE, 0, 200000, "200000000000000000000000"],
    [1, "2026-07-01", TREASURY_SAFE, 0, 210000, "210000000000000000000000"],
    // is_ltd arrives as a UInt8 here and as a bool below — both are truthy.
    [1, "2026-06-01", LTD_WALLET, 1, 90000, "90000000000000000000000"],
    [1, "2026-07-01", LTD_WALLET, true, 95000, "95000000000000000000000"],
    // No row in the latest bucket: unrankable, sorts after the ranked wallets
    // but still ahead of the folded tail.
    [1, "2026-06-01", DORMANT, 0, 400000, "400000000000000000000000"],
    [100, "2022-11-01", GC_WALLET, 0, 42, "42000000000000000000"],
  ],
};

const chainRows = rowsToObjects(CHAIN_HISTORY);
const tokenRows = rowsToObjects(TOKEN_HISTORY);
const walletRows = rowsToObjects(WALLET_HISTORY);

describe("chainsIn", () => {
  it("returns distinct chain ids ascending", () => {
    expect(chainsIn(chainRows)).toEqual([1, 100]);
    expect(chainsIn(tokenRows)).toEqual([1, 100]);
  });

  it("coerces stringly ids and drops unusable ones", () => {
    const rows = [{ chain_id: "100" }, { chain_id: 1 }, { chain_id: "" }, { chain_id: null }];
    expect(chainsIn(rows)).toEqual([1, 100]);
  });
});

describe("chainHistory", () => {
  it("returns only the requested chain, oldest first", () => {
    const mainnet = chainHistory(chainRows, 1);
    expect(mainnet.map((point) => point.bucket)).toEqual([
      "2022-10-01", "2022-11-01", "2026-07-01",
    ]);
    const gnosis = chainHistory(chainRows, 100);
    expect(gnosis.map((point) => point.bucket)).toEqual(["2022-10-01", "2022-11-01"]);
  });

  it("never merges two chains that share a bucket", () => {
    // Both chains report 2022-11-01. A bucket-keyed pivot would keep one row
    // and the survivor would look like the whole treasury.
    const mainnet = chainHistory(chainRows, 1).find((p) => p.bucket === "2022-11-01");
    const gnosis = chainHistory(chainRows, 100).find((p) => p.bucket === "2022-11-01");
    expect(mainnet?.tokensHeld).toBe(91);
    expect(gnosis?.tokensHeld).toBe(14);
    expect(mainnet?.gnoUnits).toBe(292000);
    expect(gnosis?.gnoUnits).toBe(1200);
  });

  it("keeps missing measures null rather than zero", () => {
    const point = chainHistory(chainRows, 1)[0];
    expect(point.tokensNamed).toBeNull();
    expect(point.tokensHeld).toBe(88);
    expect(point.anchorBlock).toBe(15700000);
  });

  it("is robust to column order and to an absent chain", () => {
    expect(chainHistory(rowsToObjects(shuffled(CHAIN_HISTORY)), 1)).toEqual(chainHistory(chainRows, 1));
    expect(chainHistory(chainRows, 42161)).toEqual([]);
  });
});

describe("tokenSeries", () => {
  it("ranks by latest value, caps at 5, and names what it dropped", () => {
    const { series, dropped } = tokenSeries(tokenRows, 1, priceOf);
    // Seven tokens on chain 1; five fit the palette.
    expect(series).toHaveLength(5);
    expect(series.map((s) => s.label).slice(0, 4)).toEqual(["GNO", "COW", "wstETH", "USDC"]);
    expect(series[0].latestUsd).toBe(31_000_000);
    expect(series[1].latestUsd).toBe(4_800_000);
    expect(series[2].latestUsd).toBe(1_230_000);
    expect(series[3].latestUsd).toBe(621_000);
    // Everything unrankable lands after everything ranked.
    expect(series[4].latestUsd).toBeNull();
    // Dropped names are disambiguated, because "USDC" alone names nothing.
    expect(dropped).toEqual(["SAFE", "USDC (0xdead…0001)"]);
  });

  it("flags a symbol claimed by more than one address", () => {
    const { series, dropped } = tokenSeries(tokenRows, 1, priceOf, { maxSeries: 7 });
    // `as const` so the pairs infer as tuples — a bare array literal here is
    // (string | TokenSeries)[] and the Map constructor rejects it.
    const byToken = new Map(series.map((s) => [s.token, s] as const));
    expect(dropped).toEqual([]);
    // The zero-width space is stripped, so the spoof collapses onto "USDC" —
    // and that collision is precisely what makes both entries ambiguous.
    expect(byToken.get(USDC_FAKE)?.label).toBe("USDC");
    expect(byToken.get(USDC_FAKE)?.ambiguous).toBe(true);
    expect(byToken.get(USDC)?.ambiguous).toBe(true);
    expect(byToken.get(GNO)?.ambiguous).toBe(false);
    // No symbol at all: the address is the label, and a unique address can
    // never be ambiguous.
    expect(byToken.get(UNNAMED)?.label).toBe("0x1111…1111");
    expect(byToken.get(UNNAMED)?.ambiguous).toBe(false);
  });

  it("lowercases the address before pricing and keying", () => {
    const { series } = tokenSeries(tokenRows, 1, priceOf);
    const cow = series.find((s) => s.label === "COW");
    // The fixture spells COW checksummed; the price map is keyed lowercase.
    expect(cow?.token).toBe(COW.toLowerCase());
    expect(cow?.latestUsd).toBe(4_800_000);
  });

  it("values every point at the same spot price and never turns null into 0", () => {
    const { series } = tokenSeries(tokenRows, 1, priceOf, { maxSeries: 7 });
    const wsteth = series.find((s) => s.label === "wstETH");
    expect(wsteth?.points).toEqual([
      // decimals unobserved in May: unscalable, so unvaluable. Not $0.
      { bucket: "2026-05-01", units: null, usd: null },
      { bucket: "2026-06-01", units: 400, usd: 1_200_000 },
      { bucket: "2026-07-01", units: 410, usd: 1_230_000 },
    ]);
    // Unpriced token: units survive, usd stays null across every bucket.
    const fake = series.find((s) => s.token === USDC_FAKE);
    expect(fake?.points.map((p) => p.usd)).toEqual([null, null, null]);
    expect(fake?.points.map((p) => p.units)).toEqual([1e9, 1e9, 1e9]);
    expect(fake?.latestUsd).toBeNull();
  });

  it("does not rank a priced token that is absent from the latest bucket", () => {
    const { series } = tokenSeries(tokenRows, 1, priceOf, { maxSeries: 7 });
    const safe = series.find((s) => s.label === "SAFE");
    // 8M units x $0.50 would be $4M and a top-3 slot — but it was gone by July.
    expect(safe?.points).toHaveLength(2);
    expect(safe?.latestUsd).toBeNull();
  });

  it("keeps the two chains' series apart for the same token address", () => {
    const mainnet = tokenSeries(tokenRows, 1, priceOf).series.find((s) => s.token === GNO);
    const gnosis = tokenSeries(tokenRows, 100, priceOf).series.find((s) => s.token === GNO);
    expect(mainnet?.points.map((p) => p.bucket)).toEqual([
      "2026-05-01", "2026-06-01", "2026-07-01",
    ]);
    expect(gnosis?.points).toEqual([{ bucket: "2022-11-01", units: 12, usd: 1200 }]);
    // The stale chain's bucket must never appear in the live chain's series.
    expect(mainnet?.points.some((p) => p.bucket === "2022-11-01")).toBe(false);
    expect(mainnet?.latestUsd).toBe(31_000_000);
  });

  it("breaks unrankable ties by label and survives a nonsense cap", () => {
    const rows = [
      { chain_id: 1, bucket: "2026-07-01", token_address: UNNAMED, symbol: "BBB", balance_units: 5 },
      { chain_id: 1, bucket: "2026-07-01", token_address: USDC_FAKE, symbol: "AAA", balance_units: 9 },
    ];
    const { series } = tokenSeries(rows, 1, () => null);
    expect(series.map((s) => s.label)).toEqual(["AAA", "BBB"]);
    // A negative cap would make slice(0, -1) trim from the wrong end.
    const clamped = tokenSeries(rows, 1, () => null, { maxSeries: -1 });
    expect(clamped.series).toEqual([]);
    expect(clamped.dropped).toEqual(["AAA", "BBB"]);
  });

  it("collapses a duplicated (token, bucket) row instead of drawing a spike", () => {
    const rows = [
      { chain_id: 1, bucket: "2026-07-01", token_address: GNO, symbol: "GNO", balance_units: 100 },
      { chain_id: 1, bucket: "2026-07-01", token_address: GNO, symbol: "GNO", balance_units: 100 },
    ];
    expect(tokenSeries(rows, 1, priceOf).series[0].points).toEqual([
      { bucket: "2026-07-01", units: 100, usd: 10000 },
    ]);
  });

  it("is robust to column order", () => {
    const fromShuffled = tokenSeries(rowsToObjects(shuffled(TOKEN_HISTORY)), 1, priceOf);
    expect(fromShuffled).toEqual(tokenSeries(tokenRows, 1, priceOf));
  });
});

describe("walletSeries", () => {
  it("sorts the folded tail last however large it is", () => {
    const series = walletSeries(walletRows, 1);
    expect(series.map((s) => s.label)).toEqual([
      "0xaaaa…0001", "0xbbbb…0002", "0xcccc…0003", "Other",
    ]);
    const other = series[series.length - 1];
    expect(other.isOther).toBe(true);
    expect(other.wallet).toBe("other");
    // Largest by a wide margin, and still last.
    expect(lastUnits(other.points)).toBe(520000);
  });

  it("reads is_ltd whether it arrives as 0/1 or as a bool", () => {
    const series = walletSeries(walletRows, 1);
    const byWallet = new Map(series.map((s) => [s.wallet, s] as const));
    expect(byWallet.get(LTD_WALLET)?.isLtd).toBe(true);
    expect(byWallet.get(TREASURY_SAFE)?.isLtd).toBe(false);
    expect(byWallet.get("other")?.isLtd).toBe(false);
  });

  it("ranks by the latest bucket, with wallets missing from it after the ranked ones", () => {
    const series = walletSeries(walletRows, 1);
    // DORMANT holds 400k in June — more than either live wallet — but has no
    // July row, so it cannot be ranked against them.
    expect(lastUnits(series[0].points)).toBe(210000);
    expect(lastUnits(series[1].points)).toBe(95000);
    expect(series[2].wallet).toBe(DORMANT);
    expect(series[2].points).toHaveLength(1);
  });

  it("never leaks the other chain's wallets", () => {
    expect(walletSeries(walletRows, 1).some((s) => s.wallet === GC_WALLET)).toBe(false);
    const gnosis = walletSeries(walletRows, 100);
    expect(gnosis).toHaveLength(1);
    expect(gnosis[0].points).toEqual([{ bucket: "2022-11-01", units: 42 }]);
  });

  it("keeps an unscalable balance null and is robust to column order", () => {
    const rows = [{ chain_id: 1, bucket: "2026-07-01", wallet_address: TREASURY_SAFE, is_ltd: 0, units: null }];
    expect(walletSeries(rows, 1)[0].points).toEqual([{ bucket: "2026-07-01", units: null }]);
    expect(walletSeries(rowsToObjects(shuffled(WALLET_HISTORY)), 1)).toEqual(walletSeries(walletRows, 1));
  });
});

describe("sparkValues", () => {
  it("returns the token's units oldest first", () => {
    expect(sparkValues(tokenRows, 1, GNO)).toEqual([300000, 305000, 310000]);
  });

  it("accepts a checksummed address", () => {
    expect(sparkValues(tokenRows, 1, COW)).toEqual([10_000_000, 11_000_000, 12_000_000]);
  });

  it("keeps an unscalable bucket as a gap, not a zero", () => {
    const values = sparkValues(tokenRows, 1, WSTETH);
    expect(values).toHaveLength(3);
    expect(values[0]).toBeNaN();
    expect(values.slice(1)).toEqual([400, 410]);
  });

  it("returns [] below two drawable points", () => {
    // One bucket on chain 100: a lone point drawn flat would claim "held,
    // unchanged", which the data does not support.
    expect(sparkValues(tokenRows, 100, GNO)).toEqual([]);
    // Two rows but no observed decimals in either — nothing drawable at all.
    expect(sparkValues(tokenRows, 1, UNNAMED)).toEqual([]);
    expect(sparkValues(tokenRows, 1, "0x9999999999999999999999999999999999999999")).toEqual([]);
  });

  it("counts drawable points, not rows", () => {
    const rows = [
      { chain_id: 1, bucket: "2026-06-01", token_address: GNO, balance_units: null },
      { chain_id: 1, bucket: "2026-07-01", token_address: GNO, balance_units: 5 },
    ];
    expect(sparkValues(rows, 1, GNO)).toEqual([]);
  });
});

describe("bucketsOf", () => {
  it("unions sparse series into one ascending axis", () => {
    const { series } = tokenSeries(tokenRows, 1, priceOf, { maxSeries: 7 });
    expect(bucketsOf(series.map((s) => s.points))).toEqual([
      "2026-05-01", "2026-06-01", "2026-07-01",
    ]);
  });

  it("dedupes, sorts, and tolerates empty input", () => {
    expect(bucketsOf([
      [{ bucket: "2026-07-01" }, { bucket: "2020-11-01" }],
      [{ bucket: "2026-07-01" }, { bucket: "2023-01-01" }],
      [],
    ])).toEqual(["2020-11-01", "2023-01-01", "2026-07-01"]);
    expect(bucketsOf([])).toEqual([]);
  });
});

describe("tokenSeries carries the price (regression)", () => {
  // Both "holdings revalued at today's price" charts rendered EMPTY in
  // production because TokenSeries had no `price` field: the section passed no
  // `prices` arg, constantPriceStackOption fell through to `entry.price`, found
  // undefined, and excluded every series. The chart then captioned priced
  // tokens as "excluded for want of a price" — a false statement, which is why
  // this is pinned here as well as in the chart test.
  it("exposes the injected price on every series", () => {
    const { series } = tokenSeries(rowsToObjects(TOKEN_HISTORY), 1, priceOf);
    expect(series.length).toBeGreaterThan(0);
    for (const entry of series) {
      expect(entry.price).toBe(priceOf(entry.token));
    }
    // At least one series must actually carry a number, or the assertion above
    // passes vacuously on an all-null fixture.
    expect(series.some((entry) => entry.price !== null)).toBe(true);
  });

  it("keeps price null rather than 0 when the token is unpriced", () => {
    const { series } = tokenSeries(rowsToObjects(TOKEN_HISTORY), 1, () => null);
    for (const entry of series) expect(entry.price).toBeNull();
  });
});
