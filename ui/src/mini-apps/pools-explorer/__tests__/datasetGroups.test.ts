// Frozen-contract tests: the client SECTION_GROUPS mirror must match the
// backend byte-for-byte (the backend suite pins its own side), and the
// devFixture descriptors must carry EXACTLY the contract columns in order —
// a fixture column the server does not emit hides real bugs.

import { describe, expect, it } from "vitest";

import {
  MOCK_PAYLOAD, P_BAL, P_FULL, P_STATE, P_USDC, P_WETH, P_ZERO, WXDAI, CRC, devPayload, entityPayload,
  sectionPayload,
} from "../devFixture";
import { ALL_DATASET_KEYS, DATASET_GROUP, ON_DEMAND_GROUPS, SECTION_GROUPS } from "../model/datasetGroups";
import { DATASET_COLUMNS } from "../types";

const FROZEN_SECTION_GROUPS: Record<string, Record<string, string[]>> = {
  overview: {
    core: ["pools_summary", "source_freshness"],
    mix: ["pools_by_class_fee", "probe_coverage_split"],
    trend: ["live_pool_trend"],
    concentration: ["concentration_summary", "range_width_distribution"],
  },
  pools: { core: ["pool_directory"] },
  tokens: { core: ["token_directory"] },
  coverage: {
    core: ["coverage_summary", "publication_calendar"],
    gaps: ["missing_days", "metadata_gap"],
  },
  pool: {
    core: ["pool_detail", "pool_publication_facts"],
    profile: ["pool_profile_at", "pool_profile_concentration", "pool_ticks_at"],
    history: ["pool_state_history", "pool_reserves_history"],
    fees: ["pool_fee_growth"],
    heatmap: ["pool_profile_heatmap"],
  },
  token: { core: ["token_detail", "token_pools"] },
};

describe("frozen SECTION_GROUPS contract", () => {
  it("matches the frozen map exactly (including the pool and token sections)", () => {
    expect(SECTION_GROUPS).toEqual(FROZEN_SECTION_GROUPS);
  });

  it("every dataset key is globally unique across all sections and groups", () => {
    const seen = new Map<string, string>();
    for (const [section, groups] of Object.entries(SECTION_GROUPS)) {
      for (const [group, keys] of Object.entries(groups)) {
        for (const key of keys) {
          const owner = `${section}.${group}`;
          expect(seen.get(key), `${key} owned by both ${seen.get(key)} and ${owner}`).toBeUndefined();
          seen.set(key, owner);
        }
      }
    }
  });

  it("every section has a core group", () => {
    for (const groups of Object.values(SECTION_GROUPS)) {
      expect(Object.keys(groups)).toContain("core");
    }
  });

  it("only the pool heatmap is on demand", () => {
    expect([...ON_DEMAND_GROUPS]).toEqual(["pool.heatmap"]);
    expect(SECTION_GROUPS.pool.heatmap).toEqual(["pool_profile_heatmap"]);
  });

  it("DATASET_GROUP reverse index resolves every key to its owning group", () => {
    expect(DATASET_GROUP.pools_summary).toEqual({ section: "overview", group: "core" });
    expect(DATASET_GROUP.pool_profile_heatmap).toEqual({ section: "pool", group: "heatmap" });
    expect(DATASET_GROUP.token_pools).toEqual({ section: "token", group: "core" });
    const totalKeys = Object.values(SECTION_GROUPS).flatMap((groups) => Object.values(groups)).flat().length;
    expect(Object.keys(DATASET_GROUP)).toHaveLength(totalKeys);
    expect(ALL_DATASET_KEYS).toHaveLength(totalKeys);
  });

  it("the column contract covers exactly the grouped keys", () => {
    expect(Object.keys(DATASET_COLUMNS).sort()).toEqual([...ALL_DATASET_KEYS].sort());
  });
});

describe("devFixture consistency", () => {
  it("loaded_groups covers every frozen section.group key exactly", () => {
    const expected = Object.entries(SECTION_GROUPS)
      .flatMap(([section, groups]) => Object.keys(groups).map((group) => `${section}.${group}`))
      .sort();
    const actual = Object.keys(MOCK_PAYLOAD.view_state!.loaded_groups!).sort();
    expect(actual).toEqual(expected);
  });

  it("marks the current section's groups loaded and nothing else (heatmap stays on demand)", () => {
    const groups = MOCK_PAYLOAD.view_state!.loaded_groups!;
    expect(groups["overview.core"]).toBe(true);
    expect(groups["overview.trend"]).toBe(true);
    expect(groups["pools.core"]).toBe(false);
    expect(groups["pool.core"]).toBe(false);
    const pool = entityPayload("pool", P_FULL).view_state!.loaded_groups!;
    expect(pool["pool.core"]).toBe(true);
    expect(pool["pool.profile"]).toBe(true);
    expect(pool["pool.heatmap"]).toBe(false);
    expect(pool["overview.core"]).toBe(false);
  });

  const payloads = [
    ["overview", MOCK_PAYLOAD],
    ["pools", sectionPayload("pools")],
    ["tokens", sectionPayload("tokens")],
    ["coverage", sectionPayload("coverage")],
    ["pool P_WETH", entityPayload("pool", P_WETH)],
    ["pool P_USDC", entityPayload("pool", P_USDC)],
    ["pool P_FULL", entityPayload("pool", P_FULL)],
    ["pool P_ZERO", entityPayload("pool", P_ZERO)],
    ["pool P_STATE", entityPayload("pool", P_STATE)],
    ["pool P_BAL", entityPayload("pool", P_BAL)],
    ["token WXDAI", entityPayload("token", WXDAI)],
    ["token CRC", entityPayload("token", CRC)],
  ] as const;

  it.each(payloads)("%s: fixture descriptors are shaped like real ones", (_label, payload) => {
    for (const descriptor of Object.values(payload.datasets!)) {
      expect(descriptor.database).toBe("rpc_state_indexer");
      expect(descriptor.stats.mode).toBe("exact_capped");
      expect(descriptor.stats.row_cap).toBe(10000);
    }
  });

  it.each(payloads)("%s: every fixture descriptor carries EXACTLY the contract columns in order", (_label, payload) => {
    for (const [key, descriptor] of Object.entries(payload.datasets!)) {
      const contract = DATASET_COLUMNS[key as keyof typeof DATASET_COLUMNS];
      expect(contract, `${key} has no column contract`).toBeDefined();
      expect(descriptor.columns.map((column) => column.name)).toEqual([...contract]);
      for (const row of descriptor.preview_rows) {
        expect(row, `${key} row width`).toHaveLength(contract.length);
      }
    }
  });

  it("a reserves-only pool omits every CL-only key; a state-only pool ships them empty", () => {
    const balancer = entityPayload("pool", P_BAL).datasets!;
    expect(Object.keys(balancer).sort()).toEqual(["pool_detail", "pool_publication_facts", "pool_reserves_history"]);
    const stateOnly = entityPayload("pool", P_ZERO).datasets!;
    expect(stateOnly.pool_profile_at.preview_rows).toHaveLength(0);
    expect(stateOnly.pool_ticks_at.preview_rows).toHaveLength(0);
    expect(stateOnly.pool_fee_growth.preview_rows).toHaveLength(0);
    expect(stateOnly.pool_state_history.preview_rows.length).toBeGreaterThan(0);
  });

  it("the P_WETH heatmap stays within the server row budget", () => {
    const rows = entityPayload("pool", P_WETH).datasets!.pool_profile_heatmap.preview_rows;
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThanOrEqual(120 * 80);
  });

  it("devPayload routes ?section= and ?entity= and defaults to overview", () => {
    expect(devPayload("").view_state!.section).toBe("overview");
    expect(devPayload("?section=coverage").view_state!.section).toBe("coverage");
    expect(devPayload("?section=bogus").view_state!.section).toBe("overview");
    const pool = devPayload(`?entity=pool:${P_WETH}`).view_state!;
    expect(pool.section).toBe("pool");
    expect(pool.selected_entity).toMatchObject({ entity_type: "pool", identifier: P_WETH });
    // The entity label is class + short address — never a symbol.
    expect(pool.selected_entity!.label).toMatch(/^uniswap_v3 · 0x0cf4/);
    const unknown = devPayload("?entity=pool:0x0000000000000000000000000000000000000001").datasets!;
    expect(unknown.pool_detail.preview_rows).toHaveLength(0);
  });
});
