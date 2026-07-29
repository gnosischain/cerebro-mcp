import { describe, expect, it } from "vitest";

import { rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import {
  compositionItems,
  concentration,
  priceCoverage,
  priceFor,
  priceSourceFrom,
  pricedHoldings,
  usdValue,
} from "../model/treasuryPricing";

// Real mainnet addresses where they exist, so the case-sensitivity and
// cross-chain-twin cases are the ones that actually occur in this treasury.
const GNO = "0x6810e776880c02933d47db1b9fc05908e5386b96";
const GNO_GC = "0x9c58bacc331c9aa871afd802db6379a98e80cedb"; // bridged GNO, chain 100
const COW = "0xdef1ca1fb7fbcdc777520aa7f396b4e015f497ab";
const SAFE = "0x5afe3855358e112b5647b952709e6165e1c1eeee";
// Checksummed on purpose: holdings rows arrive mixed-case, the price plane is
// lowercase. Getting this wrong silently unprices the entire portfolio.
const USDC_CHECKSUMMED = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";
const USDC = USDC_CHECKSUMMED.toLowerCase();
const SDAI = "0xaf204776c7245bf4147c2612bf6e5972ee483701"; // same address on both chains
const SPOOF_USDC_A = `0x${"a1".repeat(20)}`;
const SPOOF_USDC_B = `0x${"b2".repeat(20)}`;
const SPOOF_SAFE = `0x${"c3".repeat(20)}`;
const LURE = `0x${"d4".repeat(20)}`;
const OLDLP = `0x${"e5".repeat(20)}`; // quoted at exactly 0 — worthless, not unpriced

const SPOT = priceSourceFrom(
  {
    kind: "spot",
    by_chain: {
      "1": {
        [GNO]: 100,
        [COW]: 1,
        [SAFE]: 2,
        [USDC]: 1,
        [OLDLP]: 0,
      },
    },
  },
  "2026-07-29T09:00:00Z",
);

const HOLDINGS: RowDataset = {
  columns: [
    "chain_id", "token_address", "symbol", "decimals", "metadata_status", "metadata_known",
    "wallets_holding", "balance_total_raw", "balance_units", "supply_share", "value_usd",
  ],
  rows: [
    [1, GNO, "GNO", 18, "resolved", 1, 6, "844700000000000000000000", 844_700, 0.0325, null],
    [1, COW, "COW", 18, "resolved", 1, 3, "6920000000000000000000000", 6_920_000, 0.0069, null],
    [1, SAFE, "SAFE", 18, "resolved", 1, 2, "2135000000000000000000000", 2_135_000, 0.0021, null],
    [1, USDC_CHECKSUMMED, "USDC", 6, "resolved", 1, 4, "621000000000", 621_000, null, null],
    // Spoofs. Both claim USDC; the second smuggles a zero-width space, which
    // sanitizeSymbol strips — so the collision must be detected AFTER
    // sanitizing, not on the raw symbol.
    [1, SPOOF_USDC_A, "USDC", 18, "resolved", 1, 1, "1000000000000000000000000000000", 1e12, 0.42, null],
    [1, SPOOF_USDC_B, "USD​C", 18, "resolved", 1, 1, "5000000000000000000000000", 5_000_000, 0.99, null],
    [1, SPOOF_SAFE, "SAFE", 18, "partial", 0, 1, "1000000000000000000", null, null, null],
    [1, LURE, "Visit [aave-sr.xyz] and claim special rewards", 18, "resolved", 1, 1, "42", 42, null, null],
    [1, OLDLP, "OLDLP", 18, "resolved", 1, 1, "5000000000000000000000", 5_000, null, null],
    [100, GNO_GC, "GNO", 18, "resolved", 1, 2, "1000000000000000000000", 1_000, null, null],
    [1, SDAI, "sDAI", 18, "resolved", 1, 1, "7000000000000000000000", 7_000, null, null],
    [100, SDAI, "sDAI", 18, "resolved", 1, 1, "3000000000000000000000", 3_000, null, null],
  ],
};

const GNO_USD = 84_470_000;
const PRICED_NAV = GNO_USD + 6_920_000 + 4_270_000 + 621_000;

/** Reverse the column order (rows re-ordered to match) — parsing is
 * column-name-keyed and must not care. */
function shuffled(dataset: RowDataset): RowDataset {
  const order = dataset.columns.map((_, i) => i).reverse();
  return {
    columns: order.map((i) => dataset.columns[i]),
    rows: dataset.rows.map((row) => order.map((i) => row[i])),
  };
}

function held(src = SPOT) {
  return pricedHoldings(rowsToObjects(HOLDINGS), src);
}

describe("priceSourceFrom", () => {
  it("returns null until the overlay lands, and for malformed shapes", () => {
    expect(priceSourceFrom(undefined, undefined)).toBeNull();
    expect(priceSourceFrom(null, "2026-07-29T09:00:00Z")).toBeNull();
    expect(priceSourceFrom({ by_chain: {} }, "x")).toBeNull(); // no kind
    expect(priceSourceFrom({ kind: "guess", by_chain: {} }, "x")).toBeNull();
    expect(priceSourceFrom({ kind: "spot" }, "x")).toBeNull(); // no by_chain
    expect(priceSourceFrom([1, 2, 3], "x")).toBeNull();
  });

  it("normalizes keys to lowercase and keeps a 0 quote", () => {
    const src = priceSourceFrom(
      { kind: "spot", by_chain: { "1": { [USDC_CHECKSUMMED]: "1.0", [OLDLP]: 0, bogus: "n/a" } } },
      "2026-07-29T09:00:00Z",
    );
    expect(src).toEqual({
      kind: "spot",
      at: "2026-07-29T09:00:00Z",
      // "n/a" is not finite so it is dropped; 0 is a real answer and survives.
      byChain: { "1": { [USDC]: 1, [OLDLP]: 0 } },
    });
  });

  it("keeps quotes when the as-of timestamp is missing rather than rendering $0 NAV", () => {
    const src = priceSourceFrom({ kind: "spot", by_chain: { "1": { [GNO]: 100 } } }, undefined);
    expect(src?.at).toBe("");
    expect(priceFor(src, 1, GNO)).toBe(100);
  });

  it("drops undated numbers under a historical overlay instead of flat-lining them", () => {
    const src = priceSourceFrom(
      { kind: "historical", by_chain: { "1": { [GNO]: { "2026-06-01": 90 }, [COW]: 1 } } },
      "2026-07-29T09:00:00Z",
    );
    expect(src).toEqual({
      kind: "historical",
      at: "2026-07-29T09:00:00Z",
      byChain: { "1": { [GNO]: { "2026-06-01": 90 } } },
    });
  });
});

describe("priceFor", () => {
  it("is case-insensitive on both sides and accepts a numeric or string chain id", () => {
    expect(priceFor(SPOT, 1, USDC_CHECKSUMMED)).toBe(1);
    expect(priceFor(SPOT, "1", USDC)).toBe(1);
    expect(priceFor(SPOT, 1, GNO.toUpperCase().replace("0X", "0x"))).toBe(100);
  });

  it("returns null (never 0) for an unknown source, chain, or token", () => {
    expect(priceFor(null, 1, GNO)).toBeNull();
    expect(priceFor(SPOT, 100, GNO_GC)).toBeNull(); // chain 100 has no quotes
    expect(priceFor(SPOT, 1, SPOOF_USDC_A)).toBeNull();
    expect(priceFor(SPOT, undefined, GNO)).toBeNull();
    expect(priceFor(SPOT, 1, "")).toBeNull();
  });

  it("keeps a genuine 0 quote distinct from 'no quote'", () => {
    expect(priceFor(SPOT, 1, OLDLP)).toBe(0);
    expect(priceFor(SPOT, 1, LURE)).toBeNull();
  });

  it("ignores `date` in spot mode — that is a constant-price revaluation", () => {
    expect(priceFor(SPOT, 1, GNO, "2021-01-01")).toBe(100);
    expect(priceFor(SPOT, 1, GNO, "2026-07-01")).toBe(100);
  });

  describe("historical", () => {
    const HIST = priceSourceFrom(
      {
        kind: "historical",
        by_chain: {
          "1": {
            [GNO]: { "2026-05-01": 90, "2026-06-01": 100, "2026-07-01": 110 },
            [COW]: { "2026-06-01T00:00:00Z": 0.7 },
          },
        },
      },
      "2026-07-29T09:00:00Z",
    );

    it("resolves the nearest quote on or before the date", () => {
      expect(priceFor(HIST, 1, GNO, "2026-06-15")).toBe(100);
      expect(priceFor(HIST, 1, GNO, "2026-06-01")).toBe(100); // inclusive
      expect(priceFor(HIST, 1, GNO, "2026-12-31")).toBe(110);
    });

    it("returns null for a date before every quote — those cells stay unpriced", () => {
      // Chain 1 has buckets back to 2020-11 but CoinGecko history will not:
      // the early months must render as a gap, never as $0.
      expect(priceFor(HIST, 1, GNO, "2020-11-01")).toBeNull();
      expect(priceFor(HIST, 1, GNO, "2026-04-30")).toBeNull();
    });

    it("falls back to the latest quote when no date is given", () => {
      expect(priceFor(HIST, 1, GNO)).toBe(110);
    });

    it("matches an instant-stamped quote against a plain day bucket", () => {
      // "2026-06-01T00:00:00Z" sorts AFTER "2026-06-01" lexicographically even
      // though it is the same day; day-prefix comparison is what makes the
      // bucket resolve at all.
      expect(priceFor(HIST, 1, COW, "2026-06-01")).toBe(0.7);
      expect(priceFor(HIST, 1, COW, "2026-05-31")).toBeNull();
    });
  });
});

describe("usdValue", () => {
  it("keeps a 0 price as 0 — a worthless token is priced, not unpriced", () => {
    expect(usdValue(5_000, 0)).toBe(0);
    expect(usdValue(0, 1.5)).toBe(0);
  });

  it("returns null (never 0) when either side is missing", () => {
    expect(usdValue(null, 100)).toBeNull();
    expect(usdValue(844_700, null)).toBeNull();
    expect(usdValue(null, null)).toBeNull();
  });

  it("refuses to emit NaN", () => {
    expect(usdValue(Number.NaN, 1)).toBeNull();
    expect(usdValue(1, Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("multiplies otherwise", () => {
    expect(usdValue(844_700, 100)).toBe(GNO_USD);
  });
});

describe("pricedHoldings", () => {
  it("parses column-name-keyed, so column order never matters", () => {
    expect(pricedHoldings(rowsToObjects(shuffled(HOLDINGS)), SPOT)).toEqual(held());
  });

  it("prices through a checksummed address against the lowercase plane", () => {
    const usdc = held().find((h) => h.token === USDC);
    expect(usdc?.token).toBe(USDC); // normalized, so overlays key consistently
    expect(usdc?.usd).toBe(621_000);
  });

  it("leaves unpriced holdings null rather than 0", () => {
    const spoof = held().find((h) => h.token === SPOOF_USDC_A);
    expect(spoof?.units).toBe(1e12);
    expect(spoof?.usd).toBeNull();
    // Missing balance AND missing price — still null, not 0.
    expect(held().find((h) => h.token === SPOOF_SAFE)?.usd).toBeNull();
  });

  it("sanitizes the symbol but preserves the raw one for tooltips", () => {
    const lure = held().find((h) => h.token === LURE);
    // A 45-char phishing lure cannot be allowed to become a chart label.
    expect(lure?.symbolRaw).toBe("Visit [aave-sr.xyz] and claim special rewards");
    expect(lure?.symbol).toBe("Visit [aave-s…");
    expect(lure?.symbol.length).toBeLessThanOrEqual(14);
  });

  it("sorts usd DESC nulls last, then supplyShare DESC nulls last, then address", () => {
    expect(held().map((h) => h.token)).toEqual([
      GNO,            // 84,470,000
      COW,            //  6,920,000
      SAFE,           //  4,270,000
      USDC,           //    621,000
      OLDLP,          //          0 — priced, so ahead of every unpriced row
      SPOOF_USDC_B,   // unpriced, supply_share 0.99
      SPOOF_USDC_A,   // unpriced, supply_share 0.42
      // Remaining rows have neither price nor supply share: address ascending.
      GNO_GC,         // 0x9c…
      SDAI,           // 0xaf… (chain 1)
      SDAI,           // 0xaf… (chain 100)
      SPOOF_SAFE,     // 0xc3…
      LURE,           // 0xd4…
    ]);
  });

  it("drops rows with no address — the address IS the identity", () => {
    const rows = [
      { chain_id: 1, token_address: "", symbol: "GNO", balance_units: 1 },
      { chain_id: null, token_address: GNO, symbol: "GNO", balance_units: 1 },
      { chain_id: 1, token_address: GNO, symbol: "GNO", balance_units: 1 },
    ];
    expect(pricedHoldings(rows, SPOT).map((h) => h.token)).toEqual([GNO]);
  });

  describe("ambiguity (security-critical)", () => {
    it("flags every address that shares a sanitized symbol", () => {
      const byToken = new Map(held().map((h) => [h.token, h] as const));
      // Three distinct addresses claim "USDC" — including one that hid a
      // zero-width space in it. All three must carry their address in the UI.
      expect(byToken.get(USDC)?.ambiguous).toBe(true);
      expect(byToken.get(SPOOF_USDC_A)?.ambiguous).toBe(true);
      expect(byToken.get(SPOOF_USDC_B)?.symbol).toBe("USDC"); // sanitized before counting
      expect(byToken.get(SPOOF_USDC_B)?.ambiguous).toBe(true);
      // Two claim "SAFE".
      expect(byToken.get(SAFE)?.ambiguous).toBe(true);
      // COW is claimed once.
      expect(byToken.get(COW)?.ambiguous).toBe(false);
    });

    it("does NOT flag a cross-chain twin — a different chain is not a spoof", () => {
      // GNO on Ethereum and bridged GNO on Gnosis Chain are different addresses
      // for the SAME real asset. Flagging that pair would put an address beside
      // almost every major holding (GNO, COW, WETH all exist on both chains) and
      // drown the signal that has to stay legible: 19 addresses claiming "USDC"
      // on ONE chain. The chain column already tells a twin apart.
      const byToken = new Map(held().map((h) => [h.token, h] as const));
      expect(byToken.get(GNO)?.ambiguous).toBe(false);
      expect(byToken.get(GNO_GC)?.ambiguous).toBe(false);
      // ...while a same-chain collision still is a spoof.
      expect(byToken.get(SPOOF_USDC_A)?.chainId).toBe(byToken.get(USDC)?.chainId);
      expect(byToken.get(SPOOF_USDC_A)?.ambiguous).toBe(true);
    });

    it("counts distinct addresses, not rows — one token on two chains is not ambiguous", () => {
      const sdai = held().filter((h) => h.token === SDAI);
      expect(sdai.map((h) => h.chainId)).toEqual([1, 100]);
      expect(sdai.some((h) => h.ambiguous)).toBe(false);
    });

    it("never flags an unnamed token — it already renders as its address", () => {
      const rows = [
        { chain_id: 1, token_address: SPOOF_USDC_A, symbol: "", balance_units: 1 },
        { chain_id: 1, token_address: SPOOF_USDC_B, symbol: "​", balance_units: 1 },
      ];
      expect(pricedHoldings(rows, SPOT).every((h) => h.symbol === "" && !h.ambiguous)).toBe(true);
    });

    it("handles the measured case: 19 addresses claim USDC, exactly one is priced", () => {
      const spoofs = Array.from({ length: 18 }, (_, i) => (
        `0x${String(i + 1).padStart(2, "0").repeat(20)}`
      ));
      const rows = [
        { chain_id: 1, token_address: USDC_CHECKSUMMED, symbol: "USDC", balance_units: 621_000 },
        ...spoofs.map((token) => ({ chain_id: 1, token_address: token, symbol: "USDC", balance_units: 1e9 })),
      ];
      const parsed = pricedHoldings(rows, SPOT);
      expect(parsed).toHaveLength(19);
      expect(parsed.every((h) => h.ambiguous)).toBe(true);
      // The priced/unpriced split doubles as the safety signal: every fake is
      // unpriced, so NAV is the real token's alone.
      expect(parsed.filter((h) => h.usd !== null)).toHaveLength(1);
      expect(priceCoverage(parsed)).toEqual({ priced: 1, total: 19, usd: 621_000 });
    });
  });
});

describe("priceCoverage", () => {
  it("counts priced holdings and sums only their USD", () => {
    expect(priceCoverage(held())).toEqual({ priced: 5, total: 12, usd: PRICED_NAV });
  });

  it("counts a 0-USD holding as priced", () => {
    expect(held().find((h) => h.token === OLDLP)?.usd).toBe(0);
  });

  it("reports nothing priced when the overlay has not landed", () => {
    expect(priceCoverage(held(null))).toEqual({ priced: 0, total: 12, usd: 0 });
  });

  it("returns zeroes for an empty portfolio", () => {
    expect(priceCoverage([])).toEqual({ priced: 0, total: 0, usd: 0 });
  });
});

describe("compositionItems", () => {
  it("keeps priced holdings only, descending, and disambiguates contested labels", () => {
    expect(compositionItems(held())).toEqual([
      { token: GNO, label: "GNO", usd: GNO_USD },
      { token: COW, label: "COW", usd: 6_920_000 },
      { token: SAFE, label: "SAFE 0x5afe…eeee", usd: 4_270_000 },
      { token: USDC, label: "USDC 0xa0b8…eb48", usd: 621_000 },
    ]);
  });

  it("drops the 0-USD tile without changing the total", () => {
    const items = compositionItems(held());
    expect(items.some((item) => item.token === OLDLP)).toBe(false);
    expect(items.reduce((sum, item) => sum + item.usd, 0)).toBe(PRICED_NAV);
  });

  it("excludes a token case-insensitively (the ex-GNO view)", () => {
    const exGno = compositionItems(held(), { exclude: GNO.toUpperCase().replace("0X", "0x") });
    expect(exGno.map((item) => item.token)).toEqual([COW, SAFE, USDC]);
    expect(exGno.reduce((sum, item) => sum + item.usd, 0)).toBe(PRICED_NAV - GNO_USD);
  });

  it("filters by chain", () => {
    expect(compositionItems(held(), { chainId: 1 })).toHaveLength(4);
    // Chain 100's history ends 2022-11 and nothing on it is priced.
    expect(compositionItems(held(), { chainId: 100 })).toEqual([]);
  });

  it("folds the tail into 'other' rather than truncating, so tiles still sum to NAV", () => {
    const capped = compositionItems(held(), { cap: 2 });
    expect(capped).toEqual([
      { token: GNO, label: "GNO", usd: GNO_USD },
      { token: COW, label: "COW", usd: 6_920_000 },
      { token: "other", label: "Other (2 tokens)", usd: 4_270_000 + 621_000 },
    ]);
    expect(capped.reduce((sum, item) => sum + item.usd, 0)).toBe(PRICED_NAV);
  });

  it("singularizes a one-token tail and ignores a cap that cannot bite", () => {
    expect(compositionItems(held(), { cap: 3 })[3])
      .toEqual({ token: "other", label: "Other (1 token)", usd: 621_000 });
    expect(compositionItems(held(), { cap: 4 })).toHaveLength(4);
    expect(compositionItems(held(), { cap: 99 })).toHaveLength(4);
    expect(compositionItems(held(), { cap: 0 })).toHaveLength(4);
  });

  it("combines exclude and cap for the ex-GNO tail view", () => {
    expect(compositionItems(held(), { exclude: GNO, cap: 1 })).toEqual([
      { token: COW, label: "COW", usd: 6_920_000 },
      { token: "other", label: "Other (2 tokens)", usd: 4_891_000 },
    ]);
  });

  it("is empty when nothing is priced", () => {
    expect(compositionItems(held(null))).toEqual([]);
  });
});

describe("concentration", () => {
  it("reports the largest holding's share of priced NAV", () => {
    expect(concentration(held())).toEqual({
      token: GNO,
      label: "GNO",
      usd: GNO_USD,
      share: GNO_USD / PRICED_NAV,
    });
  });

  it("does not assume the input is sorted", () => {
    expect(concentration([...held()].reverse())?.token).toBe(GNO);
  });

  it("re-computes over a filtered list (ex-GNO concentration is COW)", () => {
    const exGno = held().filter((h) => h.token !== GNO);
    const top = concentration(exGno);
    expect(top?.token).toBe(COW);
    expect(top?.share).toBe(6_920_000 / (PRICED_NAV - GNO_USD));
  });

  it("returns null when nothing is priced, or when everything is priced at 0", () => {
    expect(concentration(held(null))).toBeNull();
    expect(concentration([])).toBeNull();
    const worthless = pricedHoldings(
      [{ chain_id: 1, token_address: OLDLP, symbol: "OLDLP", balance_units: 5_000 }],
      SPOT,
    );
    expect(worthless[0].usd).toBe(0);
    expect(concentration(worthless)).toBeNull(); // 0/0 is undefined, not 0%
  });
});

describe("cross-chain blending (regression)", () => {
  // The hero summed USD across BOTH chains, adding Ethereum's 2026-07 snapshot
  // to Gnosis Chain's 2022-12 one. That "total" describes no single moment.
  // These pin that the primitives are chain-agnostic BY DESIGN — they compute
  // over whatever they are given — so the scoping obligation sits with the
  // caller, and a future caller can see in one place what it must do.
  it("priceCoverage and concentration operate on exactly the rows given", () => {
    const all = held();
    const chain1 = all.filter((h) => h.chainId === 1);
    const chain100 = all.filter((h) => h.chainId === 100);
    expect(chain100.length).toBeGreaterThan(0);

    const both = priceCoverage(all);
    const one = priceCoverage(chain1);
    // Blended total is strictly larger, which is precisely why the caller must
    // scope: the extra value comes from a snapshot years apart.
    expect(both.total).toBeGreaterThan(one.total);
    expect(priceCoverage(chain100).total).toBe(chain100.length);
  });

  it("a chain-scoped lead never draws its share from another chain's snapshot", () => {
    const chain1 = held().filter((h) => h.chainId === 1);
    const lead = concentration(chain1);
    expect(lead).not.toBeNull();
    const scopedUsd = priceCoverage(chain1).usd;
    // share is measured against the SAME scope it was computed from.
    expect(lead!.share).toBeCloseTo(lead!.usd / scopedUsd, 10);
    expect(lead!.share).toBeLessThanOrEqual(1);
  });
});
