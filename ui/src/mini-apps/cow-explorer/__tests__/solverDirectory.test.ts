// Pure-logic coverage for the solver directory: name-keyed aggregation
// across networks, per-chain-anchor activity honesty (a stale indexer must
// not fake solver inactivity), and the client-side filter semantics.

import { describe, expect, it } from "vitest";
import {
  aggregateDirectory,
  filterDirectory,
  monogramHue,
  type DirectoryFilters,
} from "../components/SolverDirectoryTable";

// Registry-verified addresses (solverRegistry.ts):
const FRACTAL_ETH = "0x95480d3f27658e73b2785d30beb0c847d78294c7"; // Fractal prod, chain 1
const RIZZOLVER = "0x4dd1be0cd607e5382dd2844fa61d3a17e3e83d56"; // Rizzolver prod, chains 1 + 42161
const COPIUM_PROD = "0xb4694fe6590acd1281dc34a966bbae224559bad4"; // Copium_Capital prod, chain 100
const COPIUM_BARN = "0x53f5378a6f8bb24333ad8d68fd28816504a467b2"; // Copium_Capital barn, chain 100
const UNKNOWN = "0xdeadbeef00000000000000000000000000000001";

const ETH_ANCHOR = "2026-05-27T09:00:00Z"; // stale chain
const GNO_ANCHOR = "2026-07-23T09:00:00Z"; // freshest anchor
const ARB_ANCHOR = "2026-07-23T08:00:00Z";

function row(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    chain_id: 100,
    solver: COPIUM_PROD,
    first_settlement_at: "2024-01-01T00:00:00Z",
    last_settlement_at: "2026-07-23T08:00:00Z",
    settlements_all_time: 10,
    competitions_all: 20,
    wins_all: 5,
    chain_anchor_at: GNO_ANCHOR,
    ...overrides,
  };
}

const ROWS = [
  // Rizzolver on two chains -> ONE group keyed by registry name.
  row({ chain_id: 1, solver: RIZZOLVER, last_settlement_at: "2026-04-02T12:00:00Z", chain_anchor_at: ETH_ANCHOR, settlements_all_time: 100, wins_all: 10 }),
  row({ chain_id: 42161, solver: RIZZOLVER, last_settlement_at: "2026-07-23T07:00:00Z", chain_anchor_at: ARB_ANCHOR, settlements_all_time: 40, wins_all: 4 }),
  // Fractal on the STALE chain, settling right up to the chain's own anchor.
  row({ chain_id: 1, solver: FRACTAL_ETH, last_settlement_at: "2026-05-27T07:00:00Z", chain_anchor_at: ETH_ANCHOR, settlements_all_time: 500, wins_all: 50 }),
  // Copium prod + barn addresses on Gnosis -> one group, both env pills.
  row({ solver: COPIUM_PROD, last_settlement_at: "2026-07-01T00:00:00Z", settlements_all_time: 30, wins_all: 3 }),
  row({ solver: COPIUM_BARN, last_settlement_at: "2026-07-23T05:00:00Z", settlements_all_time: 2, wins_all: 0 }),
  // Unregistered address -> its own short-address group.
  row({ solver: UNKNOWN, last_settlement_at: "2026-07-23T06:00:00Z", settlements_all_time: 7, wins_all: 1 }),
  // Competition-only entry (no settlements observed).
  row({ solver: "0x2dd00f9f614e2d8e3ab14fbae1fda36395e76b85", first_settlement_at: null, last_settlement_at: null, settlements_all_time: 0, competitions_all: 12, wins_all: 0 }),
];

describe("aggregateDirectory", () => {
  const groups = aggregateDirectory(ROWS);
  const byName = new Map(groups.map((group) => [group.name, group]));

  it("groups registry-known addresses by solver name across networks", () => {
    const rizzolver = byName.get("Rizzolver");
    expect(rizzolver).toBeDefined();
    expect(rizzolver?.chains).toEqual([1, 42161]);
    expect(rizzolver?.settlements).toBe(140);
    expect(rizzolver?.wins).toBe(14);
    expect(rizzolver?.rows).toHaveLength(2);
  });

  it("keeps unregistered addresses as their own short-address rows", () => {
    const raw = groups.find((group) => !group.registered && group.rows[0]?.address === UNKNOWN);
    expect(raw).toBeDefined();
    expect(raw?.name).toBe("0xdead…0001");
    expect(raw?.envs).toEqual([]);
  });

  it("unions prod and barn addresses under one registry name", () => {
    const copium = byName.get("Copium_Capital");
    expect(copium?.envs.sort()).toEqual(["barn", "prod"]);
    expect(copium?.rows).toHaveLength(2);
  });

  it("judges activity against each row's OWN chain anchor (stale-chain honesty)", () => {
    // Fractal last settled 2h before the STALE chain's anchor -> active,
    // even though that is ~2 months behind wall clock / the freshest anchor.
    expect(byName.get("Fractal")?.active).toBe(true);
    expect(byName.get("Fractal")?.rows[0]?.active).toBe(true);
    // Rizzolver: inactive on the stale chain (40+ days before ITS anchor),
    // active on Arbitrum -> group is active.
    const rizzolver = byName.get("Rizzolver");
    expect(rizzolver?.rows.find((r) => r.chainId === 1)?.active).toBe(false);
    expect(rizzolver?.rows.find((r) => r.chainId === 42161)?.active).toBe(true);
    expect(rizzolver?.active).toBe(true);
  });

  it("measures last-seen days against the FRESHEST chain anchor", () => {
    // Fractal's newest settlement is 2026-05-27T07:00Z; freshest anchor is
    // 2026-07-23T09:00Z -> 57 whole days.
    expect(byName.get("Fractal")?.lastSeenDays).toBe(57);
    // A solver with no settlements has nothing to measure.
    expect(byName.get("Baseline")?.lastSeenDays).toBeNull();
    expect(byName.get("Baseline")?.active).toBe(false);
  });

  it("sorts by settlements descending", () => {
    expect(groups[0].name).toBe("Fractal");
    const counts = groups.map((group) => group.settlements);
    expect([...counts].sort((a, b) => b - a)).toEqual(counts);
  });
});

describe("filterDirectory", () => {
  const groups = aggregateDirectory(ROWS);
  const base: DirectoryFilters = { search: "", chains: [], env: "all", activeOnly: false };

  it("matches search against name and address, case-insensitive", () => {
    expect(filterDirectory(groups, { ...base, search: "rizz" }).map((g) => g.name)).toEqual(["Rizzolver"]);
    expect(filterDirectory(groups, { ...base, search: "0xdeadbeef" })).toHaveLength(1);
    expect(filterDirectory(groups, { ...base, search: "no-such-solver" })).toHaveLength(0);
  });

  it("filters by network membership (ANY selected chain)", () => {
    const arb = filterDirectory(groups, { ...base, chains: [42161] });
    expect(arb.map((g) => g.name)).toEqual(["Rizzolver"]);
    const ethOrArb = filterDirectory(groups, { ...base, chains: [1, 42161] });
    expect(ethOrArb.map((g) => g.name).sort()).toEqual(["Fractal", "Rizzolver"]);
  });

  it("filters by environment, including the unregistered bucket", () => {
    const barn = filterDirectory(groups, { ...base, env: "barn" });
    expect(barn.map((g) => g.name).sort()).toEqual(["Baseline", "Copium_Capital"]);
    const unknown = filterDirectory(groups, { ...base, env: "unknown" });
    expect(unknown).toHaveLength(1);
    expect(unknown[0].registered).toBe(false);
  });

  it("filters to active solvers only", () => {
    const active = filterDirectory(groups, { ...base, activeOnly: true });
    expect(active.every((group) => group.active)).toBe(true);
    expect(active.map((g) => g.name)).not.toContain("Baseline");
  });

  it("composes filters (active + chain + search)", () => {
    const result = filterDirectory(groups, { ...base, search: "fractal", chains: [1], activeOnly: true });
    expect(result.map((g) => g.name)).toEqual(["Fractal"]);
  });
});

describe("monogramHue", () => {
  it("is deterministic and within [0, 360)", () => {
    expect(monogramHue("name:Fractal")).toBe(monogramHue("name:Fractal"));
    for (const seed of ["a", "name:Rizzolver", UNKNOWN]) {
      const hue = monogramHue(seed);
      expect(hue).toBeGreaterThanOrEqual(0);
      expect(hue).toBeLessThan(360);
    }
  });
});
