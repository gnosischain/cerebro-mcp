import { describe, expect, it } from "vitest";

// Raw source of the model module — the UI tsconfig has no node types, so the
// NUL-byte guard below reads the file through Vite rather than node:fs.
import depthHeatmapSource from "../model/depthHeatmap.ts?raw";

import {
  buildDepthFootprint,
  parseHeatmapRows,
  type FootprintRow,
} from "../model/depthHeatmap";
import { depthFootprintOption } from "../model/chartOptions";
import type { RowDataset } from "../../shared/rowDataset";

// Mirrors the server `pair_depth_heatmap` projection column order.
const COLUMNS = [
  "bucket", "bucket_mid", "rel_pct", "side", "depth_base", "orders",
  "bucket_seconds", "indexed_from", "indexed_to",
];

function dataset(
  rows: Array<[string, number, number, string, number, number?]>,
): RowDataset {
  return {
    columns: COLUMNS,
    rows: rows.map(([bucket, mid, rel, side, depth, orders]) => [
      bucket, mid, rel, side, depth, orders ?? 1, 3600, bucket, bucket,
    ]),
  };
}

function row(
  bucket: string, relPct: number, side: "ask" | "bid", depth: number,
  bucketMid = 100, orders = 1,
): FootprintRow {
  return { bucket, relPct, side, depth, orders, bucketMid, bucketSeconds: 3600 };
}

describe("depthHeatmap source file", () => {
  it("contains no NUL bytes", () => {
    // The previous cell key used a literal NUL separator, which silently broke
    // every string-matching edit of this file. Keep it gone.
    expect(depthHeatmapSource).not.toContain("\u0000");
  });
});

describe("parseHeatmapRows", () => {
  it("keeps well-formed rows and drops invalid ones", () => {
    const parsed = parseHeatmapRows(dataset([
      ["2026-07-20T10:00:00Z", 100, 1.0, "ask", 1.5],
      ["2026-07-20T10:00:00Z", 100, -1.0, "bid", 2],
      ["2026-07-20T10:00:00Z", 100, 1.0, "sideways", 1], // bad side
      ["2026-07-20T10:00:00Z", 100, 1.0, "ask", 0], // non-positive depth
      ["2026-07-20T10:00:00Z", 0, 1.0, "ask", 1], // no reference price
    ]));
    expect(parsed).toHaveLength(2);
    expect(parsed.map((r) => r.side).sort()).toEqual(["ask", "bid"]);
    expect(parsed[0].orders).toBe(1);
    expect(parsed[0].bucketSeconds).toBe(3600);
  });

  it("returns nothing for an undefined dataset", () => {
    expect(parseHeatmapRows(undefined)).toEqual([]);
  });
});

describe("buildDepthFootprint", () => {
  const rows: FootprintRow[] = [
    row("2026-07-20T10:00:00Z", 1, "ask", 2),
    row("2026-07-20T10:00:00Z", -1, "bid", 3),
    row("2026-07-20T11:00:00Z", 3, "ask", 1),
    row("2026-07-20T11:00:00Z", -3, "bid", 4),
  ];

  it("orders time buckets and carries both sides per cell", () => {
    const model = buildDepthFootprint({ rows, flipped: false, rangePct: null, axisMode: "relative" });
    expect(model.empty).toBe(false);
    expect(model.xLabels).toEqual(["2026-07-20T10:00:00Z", "2026-07-20T11:00:00Z"]);
    // Cells carry [xi, yi, bid, ask, orders] — no colour value, so nothing can
    // conflate side with magnitude.
    expect(model.cells.every((c) => c.length === 5)).toBe(true);
    const askDepth = model.cells.reduce((s, c) => s + c[3], 0);
    const bidDepth = model.cells.reduce((s, c) => s + c[2], 0);
    expect(askDepth).toBeCloseTo(3);
    expect(bidDepth).toBeCloseTo(7);
  });

  it("asks bin above bids on the price axis", () => {
    const model = buildDepthFootprint({ rows, flipped: false, rangePct: null, axisMode: "relative" });
    const askY = Math.max(...model.cells.filter((c) => c[3] > 0).map((c) => c[1]));
    const bidY = Math.min(...model.cells.filter((c) => c[2] > 0).map((c) => c[1]));
    expect(bidY).toBeLessThan(askY);
  });

  it("puts equal %-offsets from DIFFERENT mids on the same level", () => {
    // The load-bearing property of the relative axis: two buckets whose market
    // price differs by 20x still line up, which is what stops a multi-year
    // window from collapsing the whole book into one or two rows.
    const model = buildDepthFootprint({
      rows: [
        row("2021-01-01T00:00:00Z", 2, "ask", 1, 100),
        row("2026-01-01T00:00:00Z", 2, "ask", 1, 2000),
      ],
      flipped: false,
      rangePct: null,
      axisMode: "relative",
    });
    expect(model.cells).toHaveLength(2);
    expect(model.cells[0][1]).toBe(model.cells[1][1]);
    // ...and in absolute mode they must NOT (100 vs 2000 are different prices).
    const abs = buildDepthFootprint({
      rows: [
        row("2021-01-01T00:00:00Z", 2, "ask", 1, 100),
        row("2026-01-01T00:00:00Z", 2, "ask", 1, 2000),
      ],
      flipped: false,
      rangePct: null,
      axisMode: "absolute",
    });
    expect(abs.cells[0][1]).not.toBe(abs.cells[1][1]);
  });

  it("reconstructs absolute price from the bucket reference", () => {
    const model = buildDepthFootprint({
      rows: [row("2026-07-20T10:00:00Z", 10, "ask", 1, 2000)],
      flipped: false,
      rangePct: null,
      axisMode: "absolute",
    });
    // 2000 * (1 + 10/100) = 2200 — the level label must be in that ballpark.
    const yi = model.cells[0][1];
    expect(model.yEdges[yi]).toBeLessThanOrEqual(2200);
    expect(model.yEdges[yi + 1]).toBeGreaterThanOrEqual(2200);
  });

  it("forward- and backward-fills the per-bucket reference price", () => {
    const model = buildDepthFootprint({
      rows: [
        row("2026-07-20T10:00:00Z", 0, "ask", 1, 100),
        row("2026-07-20T12:00:00Z", 0, "ask", 1, 120),
      ],
      flipped: false,
      rangePct: null,
      axisMode: "relative",
    });
    expect(model.bucketMid.every((v) => Number.isFinite(v) && v > 0)).toBe(true);
  });

  it("keeps the market line flat at 0% in relative mode", () => {
    const model = buildDepthFootprint({ rows, flipped: false, rangePct: null, axisMode: "relative" });
    expect(model.midLine).toHaveLength(2);
    expect(model.midLine[0][1]).toBeCloseTo(model.midLine[1][1]);
  });

  it("profile totals equal the per-level cell sums", () => {
    const model = buildDepthFootprint({ rows, flipped: false, rangePct: null, axisMode: "relative" });
    const expected = model.profile.map(() => ({ bid: 0, ask: 0 }));
    for (const [, yi, bid, ask] of model.cells) {
      expected[yi].bid += bid;
      expected[yi].ask += ask;
    }
    model.profile.forEach((p, i) => {
      expect(p.bid).toBeCloseTo(expected[i].bid);
      expect(p.ask).toBeCloseTo(expected[i].ask);
    });
  });

  it("yEdges bracket every level and are strictly increasing", () => {
    const model = buildDepthFootprint({ rows, flipped: false, rangePct: null, axisMode: "relative" });
    expect(model.yEdges).toHaveLength(model.yLabels.length + 1);
    for (let i = 1; i < model.yEdges.length; i += 1) {
      expect(model.yEdges[i]).toBeGreaterThan(model.yEdges[i - 1]);
    }
  });

  it("flip swaps sides and inverts the offset exactly", () => {
    const model = buildDepthFootprint({ rows, flipped: true, rangePct: null, axisMode: "relative" });
    // Original asks summed to 3, bids to 7 — they swap under inversion.
    const askDepth = model.cells.reduce((s, c) => s + c[3], 0);
    const bidDepth = model.cells.reduce((s, c) => s + c[2], 0);
    expect(askDepth).toBeCloseTo(7);
    expect(bidDepth).toBeCloseTo(3);
    expect(model.bucketMid[0]).toBeCloseTo(1 / 100);
  });

  it("is empty for no rows", () => {
    const model = buildDepthFootprint({ rows: [], flipped: false, rangePct: null, axisMode: "relative" });
    expect(model.empty).toBe(true);
    expect(model.cells).toEqual([]);
  });

  it("feeds a valid footprint option", () => {
    const model = buildDepthFootprint({ rows, flipped: false, rangePct: null, axisMode: "relative" });
    const option = depthFootprintOption({
      xLabels: model.xLabels,
      yLabels: model.yLabels,
      cells: model.cells,
      midLine: model.midLine,
      profile: model.profile,
      scale: model.scale,
      axisMode: model.axisMode,
      baseSymbol: "GNO",
      quoteSymbol: "WXDAI",
      isDark: true,
    }) as Record<string, unknown>;
    const series = option.series as Array<{ type: string }>;
    expect(series.map((s) => s.type)).toEqual(["custom", "line", "bar", "bar"]);
    // No visualMap: it colours ONE ramp, and the footprint has two plus an
    // imbalance key, so the legend is HTML instead.
    expect(option.visualMap).toBeUndefined();
    // Two grids — footprint + the row-aligned profile.
    expect(option.grid).toHaveLength(2);
    const yAxis = option.yAxis as Array<{ data: string[] }>;
    expect(yAxis[1].data).toEqual(yAxis[0].data);
    // Frozen repo convention: inside-only zoom, never a slider bar.
    expect(JSON.stringify(option.dataZoom)).not.toContain("slider");
    // The y-axis NAME renders above the axis end and clips below top: 48.
    const grid = option.grid as Array<{ top: number }>;
    expect(grid[0].top).toBeGreaterThanOrEqual(48);
  });

  it("keeps the year on a multi-year x axis", () => {
    const model = buildDepthFootprint({
      rows: [
        row("2021-04-29T09:04:48Z", 0, "ask", 1, 100),
        row("2026-06-25T15:48:59Z", 0, "ask", 1, 2000),
      ],
      flipped: false,
      rangePct: null,
      axisMode: "relative",
    });
    const option = depthFootprintOption({
      xLabels: model.xLabels,
      yLabels: model.yLabels,
      cells: model.cells,
      midLine: model.midLine,
      profile: model.profile,
      scale: model.scale,
      axisMode: model.axisMode,
      baseSymbol: "USDC",
      quoteSymbol: "WETH",
      isDark: false,
    }) as Record<string, unknown>;
    const xAxis = option.xAxis as Array<{ axisLabel: { formatter: (v: string) => string } }>;
    expect(xAxis[0].axisLabel.formatter("2021-04-29T09:04:48Z")).toBe("2021-04-29");
  });
});
