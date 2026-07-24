// @vitest-environment jsdom
//
// Pure-logic coverage for the Overview rebuild: protocol KPI totals (exact
// chain-0 row vs per-network fallback), sparkline bucket totals, and the
// DATA-DRIVEN staleness derivation (no hardcoded dates anywhere).

import { describe, expect, it } from "vitest";
import { bucketTotals, deriveStaleChains, pairName, protocolTotals } from "../sections/OverviewSection";

describe("protocolTotals", () => {
  const chainRows = [
    { chain_id: 1, fill_count: 100, settlement_transactions: 50, unique_traders: 30, unique_pairs: 10 },
    { chain_id: 100, fill_count: 200, settlement_transactions: 90, unique_traders: 60, unique_pairs: 25 },
    { chain_id: 137, fill_count: 0, settlement_transactions: 0, unique_traders: 0, unique_pairs: 0 },
  ];

  it("prefers the exact chain-0 protocol-wide row", () => {
    const totals = protocolTotals([
      { chain_id: 0, fill_count: 300, settlement_transactions: 140, unique_traders: 80, unique_pairs: 30 },
      ...chainRows,
    ]);
    expect(totals).toEqual({
      fills: 300, settlements: 140, traders: 80, pairs: 30, networksLive: 2, exact: true,
    });
  });

  it("falls back to per-network sums (disclosed via exact=false)", () => {
    const totals = protocolTotals(chainRows);
    expect(totals).toEqual({
      fills: 300, settlements: 140, traders: 90, pairs: 35, networksLive: 2, exact: false,
    });
  });

  it("returns null when there are no rows at all", () => {
    expect(protocolTotals([])).toBeNull();
  });
});

describe("bucketTotals", () => {
  it("sums per bucket across chains, ordered bucket-ascending", () => {
    expect(bucketTotals([
      { bucket: "2026-07-02", chain_id: 1, fill_count: 5 },
      { bucket: "2026-07-01", chain_id: 1, fill_count: 3 },
      { bucket: "2026-07-01", chain_id: 100, fill_count: 7 },
      { bucket: "2026-07-02", chain_id: 100, fill_count: "not-a-number" },
      { bucket: "", chain_id: 100, fill_count: 99 },
    ], "fill_count")).toEqual([10, 5]);
  });
});

describe("deriveStaleChains", () => {
  it("flags chains more than 7 days behind the freshest chain", () => {
    const stale = deriveStaleChains([
      { chainId: 100, latest: "2026-07-22T10:00:00Z" },
      { chainId: 1, latest: "2026-05-27T09:00:00Z" },
      { chainId: 42161, latest: "2026-07-21T00:00:00Z" },
      { chainId: 56, latest: "2025-11-26T00:00:00Z" },
    ]);
    expect(stale).toEqual([
      { chainId: 1, endsAt: "2026-05-27T09:00:00Z" },
      { chainId: 56, endsAt: "2025-11-26T00:00:00Z" },
    ]);
  });

  it("does not flag chains within the threshold", () => {
    expect(deriveStaleChains([
      { chainId: 100, latest: "2026-07-22T10:00:00Z" },
      { chainId: 1, latest: "2026-07-16T10:00:00Z" }, // exactly 6 days behind
    ])).toEqual([]);
  });

  it("skips unparseable timestamps instead of flagging them", () => {
    expect(deriveStaleChains([
      { chainId: 100, latest: "2026-07-22T10:00:00Z" },
      { chainId: 56, latest: null },
      { chainId: 1, latest: "2026-01-01T00:00:00Z" },
    ])).toEqual([{ chainId: 1, endsAt: "2026-01-01T00:00:00Z" }]);
  });

  it("needs at least two comparable chains (one chain is never 'stale vs itself')", () => {
    expect(deriveStaleChains([{ chainId: 1, latest: "2020-01-01T00:00:00Z" }])).toEqual([]);
  });

  it("honors a custom threshold", () => {
    const rows = [
      { chainId: 100, latest: "2026-07-22T00:00:00Z" },
      { chainId: 1, latest: "2026-07-19T00:00:00Z" },
    ];
    expect(deriveStaleChains(rows, 2)).toEqual([{ chainId: 1, endsAt: "2026-07-19T00:00:00Z" }]);
    expect(deriveStaleChains(rows, 7)).toEqual([]);
  });
});

describe("pairName", () => {
  it("uses symbols when known and short addresses otherwise", () => {
    expect(pairName({ token0_symbol: "WETH", token1_symbol: "USDC" })).toBe("WETH/USDC");
    expect(pairName({
      token0: "0x1234567890abcdef1234567890abcdef12345678",
      token0_symbol: "",
      token1_symbol: "USDC",
    })).toBe("0x1234…5678/USDC");
    expect(pairName({})).toBe("");
  });
});
