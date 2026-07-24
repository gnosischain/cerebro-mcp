import { describe, expect, it } from "vitest";

import {
  buildDepthHeatmap,
  parseHeatmapRows,
  type HeatmapRow,
} from "../model/depthHeatmap";
import { depthHeatmapOption } from "../model/chartOptions";
import type { RowDataset } from "../../shared/rowDataset";

// Mirrors the server `pair_depth_heatmap` projection column order.
const HEATMAP_COLUMNS = ["bucket", "price", "side", "depth_base", "indexed_from", "indexed_to"];

function dataset(rows: Array<[string, number, string, number]>): RowDataset {
  return {
    columns: HEATMAP_COLUMNS,
    rows: rows.map(([bucket, price, side, depth]) => [bucket, price, side, depth, bucket, bucket]),
  };
}

describe("parseHeatmapRows", () => {
  it("keeps well-formed rows and drops invalid ones", () => {
    const parsed = parseHeatmapRows(
      dataset([
        ["2026-07-20T10:00:00Z", 100, "ask", 1.5],
        ["2026-07-20T10:00:00Z", 98, "bid", 2],
        ["2026-07-20T10:00:00Z", -1, "ask", 1], // non-positive price -> dropped
        ["2026-07-20T10:00:00Z", 100, "sideways", 1], // bad side -> dropped
        ["2026-07-20T10:00:00Z", 100, "ask", 0], // non-positive depth -> dropped
      ]),
    );
    expect(parsed).toHaveLength(2);
    expect(parsed.map((r) => r.side).sort()).toEqual(["ask", "bid"]);
  });

  it("returns nothing for an undefined dataset", () => {
    expect(parseHeatmapRows(undefined)).toEqual([]);
  });
});

describe("buildDepthHeatmap", () => {
  const rows: HeatmapRow[] = [
    { bucket: "2026-07-20T10:00:00Z", price: 101, side: "ask", depth: 2 },
    { bucket: "2026-07-20T10:00:00Z", price: 99, side: "bid", depth: 3 },
    { bucket: "2026-07-20T11:00:00Z", price: 103, side: "ask", depth: 1 },
    { bucket: "2026-07-20T11:00:00Z", price: 97, side: "bid", depth: 4 },
  ];

  it("orders time buckets and signs cells by side (asks +, bids -)", () => {
    const model = buildDepthHeatmap({ rows, flipped: false, rangePct: null });
    expect(model.empty).toBe(false);
    expect(model.xLabels).toEqual([
      "2026-07-20T10:00:00Z",
      "2026-07-20T11:00:00Z",
    ]);
    const askCells = model.cells.filter(([, , v]) => v > 0);
    const bidCells = model.cells.filter(([, , v]) => v < 0);
    expect(askCells.length).toBe(2);
    expect(bidCells.length).toBe(2);
    // maxAbs matches the largest single-cell magnitude (the 4-unit bid).
    expect(model.maxAbs).toBeCloseTo(4);
  });

  it("asks bin above bids on the price (y) axis", () => {
    const model = buildDepthHeatmap({ rows, flipped: false, rangePct: null });
    const maxAskY = Math.max(...model.cells.filter(([, , v]) => v > 0).map(([, y]) => y));
    const minBidY = Math.min(...model.cells.filter(([, , v]) => v < 0).map(([, y]) => y));
    expect(minBidY).toBeLessThan(maxAskY);
  });

  it("emits one mid-line point per bucket that has a two-sided book", () => {
    const model = buildDepthHeatmap({ rows, flipped: false, rangePct: null });
    expect(model.midLine).toHaveLength(2);
    // Mid line x indices track the sorted buckets.
    expect(model.midLine.map(([x]) => x)).toEqual([0, 1]);
  });

  it("flip swaps sides (an ask becomes a bid on the inverted pair)", () => {
    const model = buildDepthHeatmap({ rows, flipped: true, rangePct: null });
    // Raw ask/bid depth (slots 3/4, unaffected by the sqrt color transform on
    // slot 2) swaps under inversion. Original asks summed to 3, bids to 7.
    const askDepth = model.cells.reduce((s, c) => s + c[3], 0);
    const bidDepth = model.cells.reduce((s, c) => s + c[4], 0);
    expect(askDepth).toBeCloseTo(7);
    expect(bidDepth).toBeCloseTo(3);
  });

  it("uses TOTAL depth (not net) when a level holds both sides, signed by dominance", () => {
    // Asks and bids at the SAME price/time (the CoW overlap case): a net scale
    // would cancel to ~0; total-with-dominant-sign must stay bright.
    const overlap: HeatmapRow[] = [
      { bucket: "2026-07-20T10:00:00Z", price: 100, side: "ask", depth: 6 },
      { bucket: "2026-07-20T10:00:00Z", price: 100, side: "bid", depth: 4 },
    ];
    const model = buildDepthHeatmap({ rows: overlap, flipped: false, rangePct: null });
    expect(model.cells).toHaveLength(1);
    const [, , signed, ask, bid] = model.cells[0];
    expect(ask).toBeCloseTo(6);
    expect(bid).toBeCloseTo(4);
    // Color magnitude = sqrt(total)=sqrt(10), NOT net; sign positive (asks
    // dominate). maxAbs stays the raw total (10) for reference.
    expect(signed).toBeGreaterThan(0);
    expect(signed).toBeCloseTo(Math.sqrt(10));
    expect(model.maxAbs).toBeCloseTo(10);
  });

  it("mid line uses the per-bucket median price, robust to a crossed outlier", () => {
    const withOutlier: HeatmapRow[] = [
      { bucket: "2026-07-20T10:00:00Z", price: 1870, side: "bid", depth: 1 },
      { bucket: "2026-07-20T10:00:00Z", price: 1875, side: "ask", depth: 1 },
      { bucket: "2026-07-20T10:00:00Z", price: 1872, side: "bid", depth: 1 },
      { bucket: "2026-07-20T10:00:00Z", price: 200, side: "ask", depth: 1 }, // crossed junk
    ];
    const model = buildDepthHeatmap({ rows: withOutlier, flipped: false, rangePct: null });
    expect(model.midLine).toHaveLength(1);
    // Median of [200,1870,1872,1875] = 1872 (index 2, nearest-rank) — NOT dragged
    // toward 200 the way a best-bid/best-ask midpoint would be.
    const [, midY] = model.midLine[0];
    // midY is a fractional bucket index; map it back through the y range.
    expect(midY).toBeGreaterThan(model.yLabels.length * 0.4);
  });

  it("is empty for no rows", () => {
    const model = buildDepthHeatmap({ rows: [], flipped: false, rangePct: null });
    expect(model.empty).toBe(true);
    expect(model.cells).toEqual([]);
  });

  it("feeds a valid heatmap ECharts option", () => {
    const model = buildDepthHeatmap({ rows, flipped: false, rangePct: null });
    const option = depthHeatmapOption({
      xLabels: model.xLabels,
      yLabels: model.yLabels,
      cells: model.cells,
      midLine: model.midLine,
      colorBound: model.colorBound,
      baseSymbol: "GNO",
      quoteSymbol: "WXDAI",
    }) as Record<string, unknown>;
    const series = option.series as Array<{ type: string }>;
    expect(series.map((s) => s.type)).toEqual(["heatmap", "line"]);
    const visualMap = option.visualMap as { min: number; max: number; seriesIndex: number };
    // Color scale saturates at the percentile bound (<= the true max), so the
    // skewed bulk of cells is legible rather than washed out at the max.
    expect(model.colorBound).toBeGreaterThan(0);
    expect(model.colorBound).toBeLessThanOrEqual(model.maxAbs);
    expect(visualMap.min).toBeCloseTo(-model.colorBound);
    expect(visualMap.max).toBeCloseTo(model.colorBound);
    // visualMap must color only the heatmap, not the mid line.
    expect(visualMap.seriesIndex).toBe(0);
  });
});
