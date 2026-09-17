import { describe, expect, it } from "vitest";

import { P_BAL, P_FULL, P_WETH, entityPayload } from "../devFixture";
import {
  coerceStringArray, parseFeeGrowth, parsePoolDetail, parseProfileRanges, parsePublicationFacts,
  parseReservesHistory, parseStateHistory, parseTokenPools,
} from "../model/parseRows";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";

function rowset(descriptor: DatasetDescriptor) {
  return { columns: descriptor.columns.map((column) => column.name), rows: descriptor.preview_rows };
}

describe("coerceStringArray", () => {
  it("accepts arrays, JSON strings, ClickHouse-style bracket strings and comma lists", () => {
    expect(coerceStringArray(["a", "b"])).toEqual(["a", "b"]);
    expect(coerceStringArray('["a","b"]')).toEqual(["a", "b"]);
    expect(coerceStringArray("['a','b']")).toEqual(["a", "b"]);
    expect(coerceStringArray("a, b")).toEqual(["a", "b"]);
    expect(coerceStringArray(null)).toEqual([]);
    expect(coerceStringArray("")).toEqual([]);
    expect(coerceStringArray("[]")).toEqual([]);
  });
});

describe("pool entity parsers (against the fixture projections)", () => {
  const weth = entityPayload("pool", P_WETH).datasets!;

  it("parsePoolDetail reads every field by name, including arrays and flags", () => {
    const detail = parsePoolDetail(rowset(weth.pool_detail))!;
    expect(detail).toMatchObject({
      address: P_WETH, poolClass: "uniswap_v3", poolFamily: "cl", fee: 3000, tickSpacing: 60,
      token0Symbol: "WETH", token1Symbol: "WXDAI", token0Decimals: 18, token1Decimals: 18,
      currentTick: 78_244, live: true, probed: true, tickCount: 23, profileAvailableFrom: "2023-10-02",
    });
    expect(detail.assets).toHaveLength(2);
    expect(detail.reservesRaw).toHaveLength(2);
    expect(detail.priceAdjusted).toBeCloseTo(2499.91, 1);
    expect(detail.entityLabel).toMatch(/^uniswap_v3 · /);
    expect(parsePoolDetail(undefined)).toBeNull();
    expect(parsePoolDetail({ columns: ["pool_address"], rows: [] })).toBeNull();
  });

  it("a Balancer detail carries three assets and no CL state", () => {
    const detail = parsePoolDetail(rowset(entityPayload("pool", P_BAL).datasets!.pool_detail))!;
    expect(detail.poolFamily).toBe("reserves_only");
    expect(detail.assets).toHaveLength(3);
    expect(detail.currentTick).toBeNull();
    expect(detail.liquidity).toBeNull();
    expect(detail.poolId).toMatch(/^0x/);
  });

  it("parseProfileRanges returns sorted ranges, the applied date and the state match", () => {
    const parsed = parseProfileRanges(rowset(weth.pool_profile_at));
    expect(parsed.ranges).toHaveLength(22);
    expect(parsed.asOf).toBe("2026-09-16");
    expect(parsed.currentTick).toBe(78_244);
    expect(parsed.matchesState).toBe(true);
    expect(parsed.source).toBe("tick_window_recompute");
    expect(parsed.ranges.filter((range) => range.containsCurrent)).toHaveLength(1);
    for (let index = 1; index < parsed.ranges.length; index += 1) {
      expect(parsed.ranges[index].lower).toBeGreaterThanOrEqual(parsed.ranges[index - 1].lower);
    }
    const full = parseProfileRanges(rowset(entityPayload("pool", P_FULL).datasets!.pool_profile_at));
    expect(full.ranges).toHaveLength(1);
    expect(full.ranges[0]).toMatchObject({ lower: -887_220, upper: 887_220, containsCurrent: true });
  });

  it("parseReservesHistory groups one series per token in index order, sorted by date", () => {
    const series = parseReservesHistory(rowset(weth.pool_reserves_history));
    expect(series.map((entry) => entry.symbol)).toEqual(["WETH", "WXDAI"]);
    expect(series[0].points.length).toBeGreaterThan(30);
    expect(series[0].points[0].date < series[0].points[1].date).toBe(true);
    expect(series[0].points.every((point) => point.units !== null)).toBe(true);
    const balancer = parseReservesHistory(rowset(entityPayload("pool", P_BAL).datasets!.pool_reserves_history));
    expect(balancer).toHaveLength(3);
  });

  it("parseFeeGrowth preserves nulls (first row, negative delta) instead of inventing zeros", () => {
    const points = parseFeeGrowth(rowset(weth.pool_fee_growth));
    expect(points[0].fees0Units).toBeNull();
    expect(points[0].prevDate).toBeNull();
    expect(points.some((point) => point.fees0Units === null && point.prevDate !== null)).toBe(true);
    expect(points.some((point) => point.fees0Units !== null && point.fees0Units > 0)).toBe(true);
  });

  it("parseStateHistory and parsePublicationFacts read flags and arrays", () => {
    const state = parseStateHistory(rowset(weth.pool_state_history));
    expect(state).toHaveLength(90);
    expect(state[0].probed).toBe(true);
    expect(state[0].fg0).toMatch(/^\d+$/);
    const facts = parsePublicationFacts(rowset(weth.pool_publication_facts));
    expect(facts.map((fact) => fact.job)).toEqual(["daily_cl_liquidity", "daily_pool_reserves"]);
    expect(facts[0].checksPassed).toContain("net_sum_zero");
    expect(facts[0].netSumZeroPassed).toBe(true);
    expect(facts[1].netSumZeroPassed).toBeNull();
  });

  it("parseTokenPools reads counter tokens and shares by name", () => {
    const pools = parseTokenPools(rowset(entityPayload("token", "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d").datasets!.token_pools));
    expect(pools.length).toBeGreaterThan(3);
    const total = pools.reduce((acc, pool) => acc + (pool.share ?? 0), 0);
    expect(total).toBeCloseTo(1, 6);
    expect(pools.every((pool) => pool.counterTokens.length >= 1)).toBe(true);
  });
});
