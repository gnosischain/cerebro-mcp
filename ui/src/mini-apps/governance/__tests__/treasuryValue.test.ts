// Valuation rules: hub value from the server, CoinGecko spot ONLY as a
// fallback for spot-eligible rows with no server value, spam and retired
// mirrors never valued, and unknown never turned into 0.

import { describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { T, TREASURY_PRICE_OVERLAY } from "../devFixtureTreasury";
import { parseHoldings, type HoldingRow } from "../model/treasuryRows";
import {
  assetDisplayLabel,
  assetRows,
  compositionItems,
  spotQuote,
  spotRefused,
  spotSourceFrom,
  totalsOf,
  valuationMap,
  valuationOf,
} from "../model/treasuryValue";

function holding(overrides: Partial<HoldingRow> = {}): HoldingRow {
  const token = overrides.token ?? "0x00000000000000000000000000000000000000aa";
  const chainId = overrides.chainId ?? 1;
  const base: HoldingRow = {
    key: "",
    chainId,
    token,
    symbol: "AAA",
    name: "",
    registrySymbol: "",
    assetKey: "",
    assetClass: "",
    decimals: 18,
    metadataStatus: "resolved",
    tokenClass: "priced",
    spamReason: "",
    walletsHolding: 1,
    balanceRaw: "1",
    units: 10,
    unitsExLtd: 4,
    supplyShare: null,
    symbolCollisions: 0,
    treasuryShare: null,
    priceUsd: 2,
    priceDate: "2026-09-24",
    priceSource: "hub",
    valueUsd: 20,
    valueUsdExLtd: 8,
    hasExLtd: true,
    spotEligible: false,
    tokenDate: "2026-09-24",
    asOf: "2026-09-24",
    ...overrides,
  };
  return { ...base, key: `${chainId}:${token}` };
}

const SPOT = spotSourceFrom(
  {
    kind: "spot",
    role: "spot_fallback",
    by_chain: { "1": { "0x00000000000000000000000000000000000000BB": 3, "0x00000000000000000000000000000000000000cc": 5 } },
    excluded_implausible: { "1": ["0x00000000000000000000000000000000000000CC"] },
  },
  "2026-09-25T08:00:00Z",
);

describe("spotSourceFrom", () => {
  it("accepts ONLY kind 'spot' and returns null until the overlay lands", () => {
    expect(spotSourceFrom({}, "")).toBeNull();
    expect(spotSourceFrom(undefined, "")).toBeNull();
    expect(spotSourceFrom({ kind: "historical", by_chain: {} }, "")).toBeNull();
    expect(spotSourceFrom({ kind: "spot" }, "")).toBeNull();
    expect(spotSourceFrom({ kind: "spot", by_chain: {} }, "")).not.toBeNull();
  });

  it("lowercases keys, keeps a 0 quote, drops junk, and reads the refused set", () => {
    const spot = spotSourceFrom({
      kind: "spot",
      by_chain: { "1": { "0xAB": 0, "0xcd": "1.5", "0xef": true, "0x12": null, junk: 3 } },
      excluded_implausible: { "1": ["0xAB"] },
    }, "2026-09-25T08:00:00Z")!;
    expect(spotQuote(spot, 1, "0xab")).toBe(0);
    expect(spotQuote(spot, 1, "0xCD")).toBe(1.5);
    expect(spotQuote(spot, 1, "0xef")).toBeNull();
    expect(spotQuote(spot, 1, "0x12")).toBeNull();
    expect(spotRefused(spot, 1, "0xab")).toBe(true);
    expect(spot.at).toBe("2026-09-25T08:00:00Z");
  });
});

describe("valuationOf", () => {
  it("spam is never valued — even with a server value or a spot quote", () => {
    const spam = holding({ tokenClass: "spam", spamReason: "impersonation", valueUsd: 99, spotEligible: true });
    expect(valuationOf(spam, SPOT)).toMatchObject({ kind: "hidden", usd: null });
  });

  it("a retired mirror is never valued (it would double-count v2)", () => {
    expect(valuationOf(holding({ tokenClass: "retired_mirror", valueUsd: 50 }), SPOT)).toMatchObject({ kind: "retired", usd: null });
  });

  it("server value -> hub, and the ex-Ltd companion when Ltd is excluded", () => {
    expect(valuationOf(holding(), SPOT)).toMatchObject({ kind: "hub", usd: 20, price: 2 });
    expect(valuationOf(holding(), SPOT, true)).toMatchObject({ kind: "hub", usd: 8 });
  });

  it("spot only when spot_eligible AND the server value is NULL AND a quote exists", () => {
    const eligible = holding({ token: "0x00000000000000000000000000000000000000bb", tokenClass: "listed", valueUsd: null, valueUsdExLtd: null, spotEligible: true });
    expect(valuationOf(eligible, SPOT)).toMatchObject({ kind: "spot", usd: 30, price: 3, priceDate: "2026-09-25T08:00:00Z" });
    expect(valuationOf(eligible, SPOT, true)).toMatchObject({ kind: "spot", usd: 12 });
    // Not eligible: a quote exists, but it is not applied.
    expect(valuationOf({ ...eligible, spotEligible: false }, SPOT)).toMatchObject({ kind: "unpriced", usd: null });
    // A server value wins over a quote, always.
    expect(valuationOf({ ...eligible, valueUsd: 7 }, SPOT)).toMatchObject({ kind: "hub", usd: 7 });
    // No overlay yet: unpriced, never 0.
    expect(valuationOf(eligible, null)).toMatchObject({ kind: "unpriced", usd: null });
  });

  it("a quote the server refused as implausible is never applied", () => {
    const refused = holding({ token: "0x00000000000000000000000000000000000000cc", tokenClass: "listed", valueUsd: null, spotEligible: true });
    expect(valuationOf(refused, SPOT)).toMatchObject({ kind: "refused", usd: null });
  });

  it("unknown units stay unknown: no spot value without units", () => {
    const noUnits = holding({ token: "0x00000000000000000000000000000000000000bb", tokenClass: "listed", valueUsd: null, spotEligible: true, units: null });
    expect(valuationOf(noUnits, SPOT)).toMatchObject({ kind: "unpriced", usd: null });
  });

  it("an unverified token is NEVER spot-valued, even with a quote and a stray eligibility flag", () => {
    const unverified = holding({ token: "0x00000000000000000000000000000000000000bb", tokenClass: "unverified", valueUsd: null, spotEligible: true });
    expect(valuationOf(unverified, SPOT)).toMatchObject({ kind: "unpriced", usd: null });
  });

  it("a hub_proxy price is hub-priced, flagged as a peg proxy", () => {
    expect(valuationOf(holding({ priceSource: "hub_proxy" }), SPOT)).toMatchObject({ kind: "hub", usd: 20, proxy: true });
    expect(valuationOf(holding({ priceSource: "hub" }), SPOT)).toMatchObject({ kind: "hub", proxy: false });
  });
});

describe("totals over the fixture", () => {
  const holdings = parseHoldings({
    columns: MOCK_PAYLOAD.datasets!.treasury_holdings.columns.map((column) => column.name),
    rows: MOCK_PAYLOAD.datasets!.treasury_holdings.preview_rows,
  });
  const spot = spotSourceFrom(TREASURY_PRICE_OVERLAY, "2026-09-25T08:00:00Z");

  it("splits hub and spot, and counts every class", () => {
    const valuations = valuationMap(holdings, spot, false);
    const totals = totalsOf(holdings, valuations, 0);
    const hub = holdings.reduce((acc, row) => acc + (row.tokenClass === "priced" ? row.valueUsd ?? 0 : 0), 0);
    expect(totals.hubUsd).toBeCloseTo(hub, 4);
    // GRT (2M x 0.18) and xBZZ (1M x 0.25) are 'listed'; LDO's quote is refused.
    expect(totals.spotUsd).toBeCloseTo(2_000_000 * 0.18 + 1_000_000 * 0.25, 4);
    expect(totals.totalUsd).toBeCloseTo(totals.hubUsd + totals.spotUsd, 4);
    expect(totals.counts).toMatchObject({ spot: 2, refused: 1, hidden: 5, retired: 1 });
    expect(totals.byChain[1].spotUsd).toBeCloseTo(360_000, 4);
    expect(totals.byChain[100].spotUsd).toBeCloseTo(250_000, 4);
    // The fixture's live magnitudes: Ethereum ~$115.6M hub, Gnosis Chain ~$128.3M.
    expect(totals.byChain[1].hubUsd / 1e6).toBeCloseTo(115.6, 0);
    expect(totals.byChain[100].hubUsd / 1e6).toBeCloseTo(128.3, 0);
  });

  it("a chain filter scopes the totals", () => {
    const valuations = valuationMap(holdings, spot, false);
    const eth = totalsOf(holdings, valuations, 1);
    expect(eth.byChain[100]).toBeUndefined();
    expect(eth.counts.retired).toBe(0);
  });

  it("never lets spam or a mirror into a total", () => {
    const valuations = valuationMap(holdings, spot, false);
    const totals = totalsOf(holdings, valuations, 0);
    const spamOrMirror = holdings.filter((row) => row.tokenClass === "spam" || row.tokenClass === "retired_mirror");
    for (const row of spamOrMirror) expect(valuations.get(row.key)?.usd ?? null).toBeNull();
    expect(totals.counts.hub).toBe(holdings.filter((row) => row.tokenClass === "priced" && row.valueUsd !== null).length);
  });
});

describe("assetRows", () => {
  const holdings = [
    holding({ chainId: 1, token: "0x0000000000000000000000000000000000000001", registrySymbol: "GNO", assetKey: "GNO", valueUsd: 100, units: 1 }),
    holding({ chainId: 100, token: "0x0000000000000000000000000000000000000002", registrySymbol: "GNO", assetKey: "GNO", valueUsd: 300, units: 3 }),
    holding({ chainId: 1, token: "0x0000000000000000000000000000000000000003", registrySymbol: "USDC", assetKey: "USDC", valueUsd: 50, units: 50 }),
    holding({ chainId: 100, token: "0x0000000000000000000000000000000000000004", registrySymbol: "USDC.e", assetKey: "USDC", valueUsd: 25, units: 25 }),
    holding({ chainId: 100, token: "0x0000000000000000000000000000000000000005", registrySymbol: "EURe v1", assetKey: "EURe", tokenClass: "retired_mirror", valueUsd: null }),
    holding({ chainId: 100, token: "0x0000000000000000000000000000000000000006", registrySymbol: "EURe", assetKey: "EURe", valueUsd: 10 }),
    holding({ chainId: 1, token: "0x0000000000000000000000000000000000000007", symbol: "USDC", tokenClass: "spam", spamReason: "impersonation", valueUsd: null }),
  ];
  const valuations = valuationMap(holdings, null, false);

  it("merges registry assets across chains by asset_key; the lead member is the largest value", () => {
    const rows = assetRows(holdings, valuations, { merge: true });
    const gno = rows.find((row) => row.key === "asset:GNO")!;
    expect(gno.members.map((member) => member.holding.chainId)).toEqual([100, 1]);
    expect(gno.usd).toBe(400);
    expect(gno.units).toBe(4);
    expect(gno.label).toBe("GNO");
    // Several registry symbols (USDC, USDC.e) -> the asset key.
    expect(rows.find((row) => row.key === "asset:USDC")!.label).toBe("USDC");
  });

  it("never merges a retired mirror or spam into a real asset", () => {
    const rows = assetRows(holdings, valuations, { merge: true });
    const eure = rows.find((row) => row.key === "asset:EURe")!;
    expect(eure.members).toHaveLength(1);
    expect(rows.find((row) => row.tokenClass === "retired_mirror")!.key).toBe("100:0x0000000000000000000000000000000000000005");
    const spam = rows.find((row) => row.hidden)!;
    expect(spam.members).toHaveLength(1);
    expect(spam.trusted).toBe(false);
  });

  it("sorts by value, nulls last", () => {
    const rows = assetRows(holdings, valuations, { merge: false });
    expect(rows[0].usd).toBe(300);
    expect(rows[rows.length - 1].usd).toBeNull();
  });

  it("an untrusted label always carries its address", () => {
    const rows = assetRows(holdings, valuations, { merge: true });
    const spam = rows.find((row) => row.hidden)!;
    expect(assetDisplayLabel(spam)).toMatch(/^USDC 0x0000/);
    expect(assetDisplayLabel(rows.find((row) => row.key === "asset:GNO")!)).toBe("GNO");
  });
});

describe("compositionItems", () => {
  const many = Array.from({ length: 30 }, (_, index) => holding({
    token: `0x${String(index + 1).padStart(40, "0")}`,
    registrySymbol: `T${index}`,
    assetKey: `T${index}`,
    valueUsd: 1000 - index,
  }));
  const rows = assetRows(many, valuationMap(many, null, false), { merge: true });

  it("folds the tail into 'Other (n)' so the tiles still sum to the valued total", () => {
    const items = compositionItems(rows, { cap: 10 });
    expect(items).toHaveLength(10);
    expect(items[9]).toMatchObject({ id: "other", name: "Other (21)" });
    const total = items.reduce((acc, item) => acc + item.value, 0);
    expect(total).toBe(rows.reduce((acc, row) => acc + (row.usd ?? 0), 0));
  });

  it("can exclude an asset (the ex-GNO view) and never draws unvalued assets", () => {
    const items = compositionItems(rows, { excludeAssetKey: "T0" });
    expect(items.some((item) => item.id === "asset:T0")).toBe(false);
    const unvalued = assetRows([holding({ valueUsd: null })], valuationMap([holding({ valueUsd: null })], null, false));
    expect(compositionItems(unvalued)).toEqual([]);
  });

  it("the fixture's fake USDC never becomes a tile", () => {
    const holdings = parseHoldings({
      columns: MOCK_PAYLOAD.datasets!.treasury_holdings.columns.map((column) => column.name),
      rows: MOCK_PAYLOAD.datasets!.treasury_holdings.preview_rows,
    });
    const valuations = valuationMap(holdings, spotSourceFrom(TREASURY_PRICE_OVERLAY, ""), false);
    const items = compositionItems(assetRows(holdings, valuations, { merge: true }));
    expect(items.some((item) => (item.id ?? "").includes(T.FAKE_USDC_1))).toBe(false);
    expect(items.some((item) => item.name.includes("aave"))).toBe(false);
  });
});
