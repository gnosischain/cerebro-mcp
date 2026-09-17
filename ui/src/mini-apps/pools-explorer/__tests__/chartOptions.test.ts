// Chart-option guards: only series registered in ui/src/lib/echarts-setup.ts,
// INSIDE-only dataZoom (never a slider), a markLine at the current tick, and
// escaped tooltips (symbols are attacker-authored).

import { describe, expect, it } from "vitest";

import {
  checksSummaryOption, classFeeMixOption, concentrationOption, coverageCalendarOption, feesHistoryOption,
  liquidityHistoryOption, liquidityProfileOption, livePoolTrendOption, metadataGapOption, priceHistoryOption,
  probeCoverageOption, profileHeatmapOption, rangeWidthOption, reservesHistoryOption, ticksOption,
  tokenPoolsShareOption,
} from "../model/chartOptions";
import { buildProfileModel, profileWindow, type ProfileRange } from "../model/liquidityProfile";
import { liquidityClassLabels, liquidityFill, liquidityRamp } from "../model/liquidityScale";
import { buildProfileHeatmap, parseHeatmapRows } from "../model/profileHeatmap";
import type {
  CalendarPoint, ClassFeeView, ConcentrationSummaryView, FeePoint, LiveTrendPoint, MetadataGapView,
  ProbeCoverageView, RangeWidthView, ReserveSeries, StatePoint, TickPoint, TokenPoolView,
} from "../model/parseRows";
import { DATASET_COLUMNS } from "../types";

const REGISTERED = new Set(["line", "bar", "scatter", "heatmap", "custom"]);

function seriesTypes(option: unknown): string[] {
  const series = (option as { series?: unknown }).series;
  const list = Array.isArray(series) ? series : series ? [series] : [];
  return list.map((entry) => String((entry as { type?: string }).type));
}

const ranges: ProfileRange[] = [
  { lower: -887_220, upper: 77_400, liquidity: 1.2e18, isGap: false, containsCurrent: false },
  { lower: 77_400, upper: 78_300, liquidity: 7e19, isGap: false, containsCurrent: true },
  { lower: 78_300, upper: 78_400, liquidity: 0, isGap: true, containsCurrent: false },
  { lower: 78_400, upper: 887_220, liquidity: 1.2e18, isGap: false, containsCurrent: false },
  { lower: -887_220, upper: 887_220, liquidity: 5e15, isGap: false, containsCurrent: true },
];
const model = buildProfileModel({ ranges, currentTick: 78_244, window: profileWindow(78_244, "20pct", ranges) });
const labelCtx = { mode: "price" as const, currentTick: 78_244, dec0: 18, dec1: 18 };

const statePoints: StatePoint[] = [
  { date: "2026-01-01", anchorBlock: 1, tick: 1, priceRaw: 1, priceAdjusted: 1, liquidity: 10, live: true, tickCount: 2, probed: true, fee: 3000, fg0: "1", fg1: "1" },
  { date: "2026-01-02", anchorBlock: 2, tick: 2, priceRaw: 1000, priceAdjusted: 1000, liquidity: 12, live: true, tickCount: 3, probed: true, fee: 3000, fg0: "2", fg1: "2" },
];

const heatmap = buildProfileHeatmap({
  rows: parseHeatmapRows({
    columns: [...DATASET_COLUMNS.pool_profile_heatmap],
    rows: [["2026-01-01", 1000, 1100, 5, 1000, 0, 1, 100, 1, 1, 1], ["2026-01-02", 1100, 1200, 2, 1050, 0, 1, 100, 1, 1, 1]],
  }),
  axisMode: "tick",
});

function everyOption(): Array<[string, unknown]> {
  return [
    ["liquidityProfileOption", liquidityProfileOption({ model, labelCtx, unit: "WXDAI per WETH", yLog: false, isDark: true })],
    ["liquidityProfileOption(log)", liquidityProfileOption({ model, labelCtx, unit: "u", yLog: true, isDark: false })],
    ["profileHeatmapOption", profileHeatmapOption({ model: heatmap, isDark: true, unit: "u" })],
    ["priceHistoryOption", priceHistoryOption(statePoints, { raw: false, unit: "u" })],
    ["liquidityHistoryOption", liquidityHistoryOption(statePoints)],
    ["reservesHistoryOption", reservesHistoryOption([
      { index: 0, token: "0xa", symbol: "A", decimals: 18, points: [{ date: "2026-01-01", raw: "1", float: 1, units: 1 }] },
      { index: 1, token: "0xb", symbol: null, decimals: null, points: [{ date: "2026-01-01", raw: "2", float: 2, units: null }] },
      { index: 2, token: "0xc", symbol: "C", decimals: 6, points: [{ date: "2026-01-01", raw: "3", float: 3, units: 3 }] },
    ] satisfies ReserveSeries[])],
    ["feesHistoryOption", feesHistoryOption([
      { date: "2026-01-01", prevDate: null, gapDays: null, liquidity: 1, fees0Units: null, fees1Units: null, fees0Raw: null, fees1Raw: null, probed: true },
      { date: "2026-01-02", prevDate: "2026-01-01", gapDays: 1, liquidity: 1, fees0Units: 0.5, fees1Units: 2, fees0Raw: "5", fees1Raw: "2", probed: true },
    ] satisfies FeePoint[], { sym0: "A", sym1: "B", units0: true, units1: true })],
    ["ticksOption", ticksOption([
      { tick: 100, gross: 5, net: 5, grossRaw: "5", netRaw: "5", priceRaw: 1.01, belowCurrent: true },
      { tick: 200, gross: 5, net: -5, grossRaw: "5", netRaw: "-5", priceRaw: 1.02, belowCurrent: false },
    ] satisfies TickPoint[], { ...labelCtx, currentTick: 150 }, true)],
    ["livePoolTrendOption", livePoolTrendOption([{ bucket: "2026-01-01", publishedCl: 1, liveCl: 1, probed: 1, publishedReserves: 1 }] satisfies LiveTrendPoint[])],
    ["classFeeMixOption", classFeeMixOption([
      { poolClass: "uniswap_v3", poolFamily: "cl", fee: 3000, feeBand: "b3000", pools: 3, livePools: 2, probedPools: 1 },
      { poolClass: "balancer_v2", poolFamily: "reserves_only", fee: null, feeBand: null, pools: 4, livePools: 2, probedPools: 0 },
    ] satisfies ClassFeeView[])],
    ["probeCoverageOption", probeCoverageOption([{ probed: true, live: true, pools: 3, medianLiquidity: 1, p90Liquidity: 2 }] satisfies ProbeCoverageView[])],
    ["concentrationOption", concentrationOption([
      { metric: "share_1pct", poolsMeasured: 3, q25: 0.1, median: 0.2, q75: null, poolsTrue: null },
      { metric: "has_full_range", poolsMeasured: 3, q25: null, median: null, q75: null, poolsTrue: 2 },
    ] satisfies ConcentrationSummaryView[])],
    ["rangeWidthOption", rangeWidthOption([{ order: 1, bucket: "≤10", ranges: 3, pools: 2, share: 0.1 }] satisfies RangeWidthView[])],
    ["coverageCalendarOption", coverageCalendarOption([
      { date: "2026-01-01", job: "daily_cl_liquidity", anchorBlock: 1, poolsPublished: 5, poolsBelowThreshold: 3, poolsProbed: 2, netSumZeroPassed: 2, reconcilesPassed: 2, poolsConfiguredNow: 6 },
      { date: "2026-01-01", job: "daily_pool_reserves", anchorBlock: 1, poolsPublished: 8, poolsBelowThreshold: null, poolsProbed: null, netSumZeroPassed: null, reconcilesPassed: null, poolsConfiguredNow: 9 },
    ] satisfies CalendarPoint[])],
    ["checksSummaryOption", checksSummaryOption([
      { date: "2026-01-01", job: "daily_cl_liquidity", anchorBlock: 1, poolsPublished: 5, poolsBelowThreshold: 3, poolsProbed: 2, netSumZeroPassed: 2, reconcilesPassed: 2, poolsConfiguredNow: 6 },
    ] satisfies CalendarPoint[])],
    ["metadataGapOption", metadataGapOption([{ dimension: "tokens_symbol · cl", known: 34, unknown: 2278, pctKnown: 0.0147 }] satisfies MetadataGapView[])],
    ["tokenPoolsShareOption", tokenPoolsShareOption([{
      address: "0xpool", name: "<script>alert(1)</script>", poolClass: "uniswap_v3", poolFamily: "cl", hasState: true, fee: 3000,
      counterTokens: ["0xc"], counterLabels: ["<b>X</b>"], liquidity: 1, live: true, probed: true, reserveUnits: 1, reserveRaw: "1",
      tokenDecimals: 18, share: 0.5, priceInCounter: null,
    }] satisfies TokenPoolView[], "WXDAI")],
  ];
}

describe("liquidity scale labels", () => {
  it("labels legend classes in SI liquidity units, never the CoW 'B'-capped compact form", () => {
    const labels = liquidityClassLabels(heatmap.scale);
    expect(labels.length).toBe(heatmap.scale.counts.length);
    for (const label of labels) expect(label).not.toMatch(/\d{6,}/);
    const big = buildProfileHeatmap({
      rows: parseHeatmapRows({
        columns: [...DATASET_COLUMNS.pool_profile_heatmap],
        rows: [["2026-01-01", 0, 100, 3e18, 50, 0, 100, 100, 1, 1, 1], ["2026-01-01", 100, 200, 7e19, 50, 0, 200, 100, 1, 1, 1]],
      }),
      axisMode: "tick",
    });
    expect(liquidityClassLabels(big.scale).join(" ")).toMatch(/E/);
    expect(liquidityClassLabels(big.scale).join(" ")).not.toContain("000000000B");
  });
});

describe("pools chart options", () => {
  it.each(everyOption())("%s uses only registered series types", (_name, option) => {
    const types = seriesTypes(option);
    expect(types.length).toBeGreaterThan(0);
    for (const type of types) expect(REGISTERED.has(type), `series type ${type}`).toBe(true);
  });

  it.each(everyOption())("%s never carries a slider dataZoom or a graph-on-cartesian series", (_name, option) => {
    const text = JSON.stringify(option);
    expect(text).not.toContain('"type":"slider"');
    expect(seriesTypes(option)).not.toContain("graph");
  });

  it("the profile draws bars as a custom series on VALUE axes with a markLine at the current tick", () => {
    const option = liquidityProfileOption({ model, labelCtx, unit: "u", yLog: false, isDark: true }) as {
      xAxis: { type: string; min: number; max: number };
      yAxis: { type: string };
      dataZoom: Array<{ type: string; filterMode?: string }>;
      series: Array<{ name: string; type: string; markLine?: { data: Array<{ xAxis: number }> } }>;
    };
    expect(option.xAxis.type).toBe("value");
    expect(option.xAxis.min).toBe(model.window.lo);
    expect(option.xAxis.max).toBe(model.window.hi);
    expect(option.dataZoom).toEqual([{ type: "inside", xAxisIndex: 0, filterMode: "none" }]);
    const ranges = option.series.find((entry) => entry.name === "ranges")!;
    expect(ranges.type).toBe("custom");
    expect(ranges.markLine?.data).toEqual([{ xAxis: 78_244 }]);
    // The full-range band is its own series, drawn across the window, never a bar.
    const band = option.series.find((entry) => entry.name === "full-range")!;
    expect(band.type).toBe("custom");
    expect(model.bars.every((bar) => bar.hi - bar.lo < 1_000_000)).toBe(true);
    // Log toggle switches the axis type.
    const log = liquidityProfileOption({ model, labelCtx, unit: "u", yLog: true, isDark: true }) as { yAxis: { type: string } };
    expect(log.yAxis.type).toBe("log");
  });

  it("the profile tooltip names the true tick extents and flags boundary segments", () => {
    const option = liquidityProfileOption({ model, labelCtx, unit: "u", yLog: false, isDark: true }) as {
      tooltip: { formatter: (params: unknown) => string };
    };
    const edge = model.bars.findIndex((bar) => bar.touchesBoundary);
    const text = option.tooltip.formatter({ seriesName: "ranges", value: [0, 0, 0, edge] });
    expect(text).toContain("full-range boundary");
    expect(text).toContain("887,220");
    const current = model.bars.findIndex((bar) => bar.containsCurrent);
    expect(option.tooltip.formatter({ seriesName: "ranges", value: [0, 0, 0, current] })).toContain("current tick");
  });

  it("the heatmap is a custom rect series (no visualMap) coloured from the shared scale, plus the current-tick path", () => {
    const option = profileHeatmapOption({ model: heatmap, isDark: true, unit: "u" }) as {
      visualMap?: unknown;
      series: Array<{
        type: string;
        data: number[][];
        renderItem?: (params: unknown, api: unknown) => { type: string; style: { fill: string } } | null;
      }>;
      dataZoom: Array<{ type: string }>;
    };
    expect(option.visualMap).toBeUndefined();
    expect(option.series[0].type).toBe("custom");
    expect(option.series[0].data.length).toBe(heatmap.cells.length);
    const api = (cell: number[]) => ({
      value: (index: number) => cell[index],
      coord: (values: number[]) => [values[0] * 10, values[1] * 10],
      size: () => [8, 8],
    });
    for (const cell of heatmap.cells) {
      const shape = option.series[0].renderItem!(null, api(cell))!;
      expect(shape.type).toBe("rect");
      expect(shape.style.fill).toBe(liquidityFill(heatmap.scale, liquidityRamp(true), cell[2]));
      expect(/^#[0-9a-f]{6}$/i.test(shape.style.fill)).toBe(true);
    }
    // A zero cell is not drawn at all — "no liquidity" reads as the card surface.
    expect(option.series[0].renderItem!(null, api([0, 0, 0]))).toBeNull();
    expect(option.series[1].type).toBe("line");
    expect(option.dataZoom.every((zoom) => zoom.type === "inside")).toBe(true);
  });

  it("reserves get ONE axis per token and log price kicks in beyond two decades", () => {
    const reserves = everyOption().find(([name]) => name === "reservesHistoryOption")![1] as { yAxis: unknown[]; grid: unknown[]; series: unknown[] };
    expect(reserves.yAxis).toHaveLength(3);
    expect(reserves.grid).toHaveLength(3);
    expect(reserves.series).toHaveLength(3);
    expect((reserves.yAxis[1] as { name: string }).name).toContain("raw units");
    const price = priceHistoryOption(statePoints, { raw: false, unit: "u" }) as { yAxis: { type: string } };
    expect(price.yAxis.type).toBe("log");
    const flat = priceHistoryOption([statePoints[0], { ...statePoints[1], priceAdjusted: 2 }], { raw: false, unit: "u" }) as { yAxis: { type: string } };
    expect(flat.yAxis.type).toBe("value");
  });

  it("ticks render net liquidity as bars and gross as scatter on a value axis", () => {
    const option = everyOption().find(([name]) => name === "ticksOption")![1] as {
      xAxis: { type: string };
      series: Array<{ type: string; markLine?: { data: Array<{ xAxis: number }> } }>;
    };
    expect(option.xAxis.type).toBe("value");
    expect(option.series.map((entry) => entry.type)).toEqual(["bar", "scatter"]);
    expect(option.series[0].markLine?.data).toEqual([{ xAxis: 150 }]);
  });

  it("escapes attacker-authored names in tooltips", () => {
    const option = everyOption().find(([name]) => name === "tokenPoolsShareOption")![1] as {
      tooltip: { formatter: (params: unknown) => string };
    };
    const text = option.tooltip.formatter({ dataIndex: 0 });
    expect(text).not.toContain("<script>");
    expect(text).toContain("&lt;script&gt;");
    expect(text).not.toContain("<b>");
  });
});
