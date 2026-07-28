// @vitest-environment jsdom
//
// Pure-logic coverage for the Live view redesign: client-side chain
// filtering, poll-cadence derivation (min-lag across chains in all-networks
// mode), KPI derivation, and the heartbeat chart spec.

import { describe, expect, it } from "vitest";

import {
  deriveLiveKpis,
  derivePollState,
  fillsByChain,
  filterRowsByChain,
  heartbeatOption,
} from "../sections/LiveSection";
import { CHAIN_SERIES_COLORS } from "../../shared/chainIcons";

type Row = Record<string, unknown>;

const pulseRow = (chain: number, lag: number | null): Row => ({
  chain_id: chain,
  checkpoint_block: 1,
  lag_seconds: lag,
});

describe("filterRowsByChain", () => {
  const rows: Row[] = [{ chain_id: 1, v: "a" }, { chain_id: 100, v: "b" }, { chain_id: 100, v: "c" }];

  it("passes everything through with a null filter", () => {
    expect(filterRowsByChain(rows, null)).toHaveLength(3);
  });

  it("keeps only the selected chain's rows", () => {
    expect(filterRowsByChain(rows, 100).map((r) => r.v)).toEqual(["b", "c"]);
    expect(filterRowsByChain(rows, 8453)).toHaveLength(0);
  });
});

describe("derivePollState", () => {
  it("single-chain: the selected chain's lag drives the cadence (unchanged)", () => {
    const pulse = [pulseRow(1, 45), pulseRow(100, 9000)];
    expect(derivePollState(pulse, 1)).toMatchObject({ lag: 45, stale: false });
    expect(derivePollState(pulse, 100)).toMatchObject({ lag: 9000, stale: true });
    // Missing chain / null lag → "no data", never stale.
    expect(derivePollState(pulse, 8453)).toMatchObject({ lag: null, stale: false });
    expect(derivePollState([pulseRow(1, null)], 1)).toMatchObject({ lag: null, stale: false });
  });

  it("all-networks: minimum lag wins — any fresh chain keeps the fast cadence", () => {
    const someFresh = [pulseRow(1, 9000), pulseRow(100, 30), pulseRow(42161, 2000)];
    expect(derivePollState(someFresh, 0)).toMatchObject({ lag: 30, stale: false, staleChains: 2, totalChains: 3 });
  });

  it("all-networks: stale only when EVERY reporting chain is behind", () => {
    const allStale = [pulseRow(1, 9000), pulseRow(100, 700), pulseRow(8453, null)];
    expect(derivePollState(allStale, 0)).toMatchObject({ lag: 700, stale: true, staleChains: 2, totalChains: 3 });
    // No lags at all → no data, not stale.
    expect(derivePollState([pulseRow(8453, null)], 0)).toMatchObject({ lag: null, stale: false, staleChains: 0 });
  });
});

describe("deriveLiveKpis", () => {
  const minuteActivity: Row[] = [
    { bucket: "m1", chain_id: 100, fills: 3, settlements: 2 },
    { bucket: "m1", chain_id: 1, fills: 2, settlements: 1 },
    { bucket: "m2", chain_id: 100, fills: 1, settlements: 1 },
  ];
  const openOrders: Row[] = [{ chain_id: 100 }, { chain_id: 100 }, { chain_id: 1 }];
  const settlements: Row[] = [
    { chain_id: 100, settlement_executor: "0xsolverA" },
    { chain_id: 100, settlement_executor: "0xsolverA" },
    { chain_id: 1, settlement_executor: "0xsolverB" },
  ];
  const pulse: Row[] = [pulseRow(1, 42), pulseRow(100, 18), pulseRow(42161, 2000), pulseRow(8453, null)];

  it("derives the unfiltered headline numbers", () => {
    expect(deriveLiveKpis({ minuteActivity, openOrders, settlements, pulse, chainFilter: null })).toEqual({
      fills1h: 6,
      settlements1h: 4,
      openIntents: 3,
      activeSolvers: 2,
      chainsFresh: 2,
      chainsTotal: 4,
    });
  });

  it("respects the chain filter for feed KPIs but never for chains-live", () => {
    expect(deriveLiveKpis({ minuteActivity, openOrders, settlements, pulse, chainFilter: 100 })).toEqual({
      fills1h: 4,
      settlements1h: 3,
      openIntents: 2,
      activeSolvers: 1,
      chainsFresh: 2,
      chainsTotal: 4,
    });
  });
});

describe("fillsByChain", () => {
  it("sums the last-hour fills per chain for the pulse chips", () => {
    const totals = fillsByChain([
      { chain_id: 100, fills: 3 },
      { chain_id: 100, fills: 1 },
      { chain_id: 1, fills: 2 },
      { chain_id: "junk", fills: 5 },
    ]);
    expect(totals.get(100)).toBe(4);
    expect(totals.get(1)).toBe(2);
    expect(totals.size).toBe(2);
  });
});

describe("heartbeatOption", () => {
  const rows: Row[] = [
    { bucket: "2026-07-23T10:00:00Z", chain_id: 100, fills: 3 },
    { bucket: "2026-07-23T10:00:00Z", chain_id: 1, fills: 2 },
    { bucket: "2026-07-23T10:01:00Z", chain_id: 100, fills: 1 },
  ];

  it("stacks per-chain bars with chain names, shared hues, and band height", () => {
    const spec = heartbeatOption(rows, null) as Record<string, unknown>;
    expect(spec._cerebro_height).toBe("160px");
    const series = spec.series as Array<Record<string, unknown>>;
    expect(series.map((s) => s.name).sort()).toEqual(["Ethereum", "Gnosis"]);
    expect(series.every((s) => s.type === "bar" && s.stack === "total")).toBe(true);
    const gnosis = series.find((s) => s.name === "Gnosis")!;
    expect((gnosis.itemStyle as Record<string, unknown>).color).toBe(CHAIN_SERIES_COLORS[100]);
    expect(gnosis.data).toEqual([3, 1]);
  });

  it("applies the client-side chain filter", () => {
    const spec = heartbeatOption(rows, 1) as Record<string, unknown>;
    const series = spec.series as Array<Record<string, unknown>>;
    expect(series.map((s) => s.name)).toEqual(["Ethereum"]);
    expect(series[0].data).toEqual([2]);
  });
});
