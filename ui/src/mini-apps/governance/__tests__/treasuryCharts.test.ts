// Treasury chart builders: frozen conventions (inside zoom only, registered
// series types only, markLine — never markArea — at gaps), series ids that a
// click can resolve, the unit on the axis, and HTML-escaped tooltips.

import type { EChartsOption } from "echarts";
import { describe, expect, it } from "vitest";

import {
  bandRefFromSeriesId,
  breadthOption,
  compositionTreemapOption,
  priceLineOption,
  stackTooltipHtml,
  valueStackOption,
} from "../model/treasuryCharts";
import type { StackBand } from "../model/treasuryHistory";

interface SeriesLike {
  id?: string;
  name?: string;
  type?: string;
  stack?: string;
  data?: unknown[];
  connectNulls?: boolean;
  triggerLineEvent?: boolean;
  areaStyle?: unknown;
  markLine?: { data?: Array<{ xAxis?: string }> };
  markArea?: unknown;
}

const series = (option: EChartsOption) => ((option as { series?: SeriesLike[] }).series ?? []);

function zoomTypes(option: EChartsOption): string[] {
  const zoom = (option as { dataZoom?: unknown }).dataZoom;
  if (!zoom) return [];
  return (Array.isArray(zoom) ? zoom : [zoom]).map((entry) => String((entry as { type?: string }).type));
}

function yAxisNames(option: EChartsOption): string[] {
  const axis = (option as { yAxis?: unknown }).yAxis;
  const list = Array.isArray(axis) ? axis : [axis];
  return list.map((entry) => String((entry as { name?: string })?.name ?? ""));
}

const BUCKETS = ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"];

function band(id: string, label: string, data: Array<number | null>, extra: Partial<StackBand> = {}): StackBand {
  return { key: id.slice(id.indexOf(":") + 1), id, label, data, isOther: false, folded: [], ...extra };
}

const BANDS = [
  band("asset:GNO", "GNO", [10, null, 12, 13]),
  band("asset:COW", "COW", [1, null, 2, 3]),
  band("other", "Other (+3)", [1, null, 1, 1], { isOther: true }),
];

const BUILT: Array<[string, EChartsOption]> = [
  ["valueStackOption", valueStackOption({ buckets: BUCKETS, bands: BANDS, gaps: ["2026-07-01"], unit: "usd", markers: [{ bucket: "2026-08-01", key: "COW", label: "COW" }] })],
  ["breadthOption", breadthOption({ buckets: BUCKETS, priced: [1, null, 2, 3], listed: [1, null, 1, 1], unverified: [0, null, 1, 1], spam: [4, null, 4, 5], positions: [5, null, 6, 7], gaps: ["2026-07-01"] })],
  ["priceLineOption", priceLineOption([{ day: "2026-09-01", priceSymbol: "GNO", priceUsd: 150, role: "priced" }], "GNO")],
  ["compositionTreemapOption", compositionTreemapOption([{ id: "asset:GNO", name: "GNO", value: 100 }])],
];

describe("frozen conventions", () => {
  it("only inside zoom — never a slider", () => {
    for (const [label, option] of BUILT) {
      for (const type of zoomTypes(option)) expect(type, label).toBe("inside");
      expect(zoomTypes(option), label).not.toContain("slider");
    }
  });

  it("uses only series types registered in echarts-setup, and never markArea", () => {
    const registered = new Set(["line", "bar", "pie", "scatter", "heatmap", "treemap", "sankey", "graph", "lines", "custom", "candlestick", "gauge", "funnel"]);
    for (const [label, option] of BUILT) {
      for (const entry of series(option)) {
        expect(registered.has(String(entry.type)), `${label} used ${entry.type}`).toBe(true);
        expect(entry.markArea, `${label} used markArea (MarkAreaComponent is not registered)`).toBeUndefined();
      }
    }
  });
});

describe("valueStackOption", () => {
  const option = BUILT[0][1];

  it("stacks every band as an area with ids = band ids, gaps never bridged", () => {
    const bands = series(option).filter((entry) => entry.stack === "total");
    expect(bands.map((entry) => entry.id)).toEqual(["asset:GNO", "asset:COW", "other"]);
    for (const entry of bands) {
      expect(entry.type).toBe("line");
      expect(entry.areaStyle).toBeDefined();
      expect(entry.connectNulls).toBe(false);
      expect(entry.triggerLineEvent).toBe(true);
    }
    expect(bands[0].data).toEqual([10, null, 12, 13]);
  });

  it("marks gap months with a dashed markLine on an unstacked helper series", () => {
    const gaps = series(option).find((entry) => entry.id === "gaps")!;
    expect(gaps.stack).toBeUndefined();
    expect(gaps.markLine?.data).toEqual([{ xAxis: "2026-07-01" }]);
    expect((gaps.data ?? []).every((value) => value === null)).toBe(true);
  });

  it("draws first-priced markers as markLines too", () => {
    const markers = series(option).find((entry) => entry.id === "markers")!;
    expect(markers.markLine?.data?.[0]).toMatchObject({ xAxis: "2026-08-01" });
  });

  it("names the axis USD or GNO, or the token's units", () => {
    expect(yAxisNames(option)).toEqual(["USD"]);
    expect(yAxisNames(valueStackOption({ buckets: BUCKETS, bands: BANDS, unit: "gno" }))).toEqual(["GNO"]);
    expect(yAxisNames(valueStackOption({ buckets: BUCKETS, bands: BANDS, unit: { units: "COW" } }))).toEqual(["COW"]);
  });

  it("drops a gap that is not on the axis instead of drawing a line nowhere", () => {
    const off = valueStackOption({ buckets: BUCKETS, bands: BANDS, gaps: ["2020-01-01"], unit: "usd" });
    expect(series(off).some((entry) => entry.id === "gaps")).toBe(false);
  });
});

describe("tooltips", () => {
  it("escape attacker-authored labels — an <img> never reaches the DOM as markup", () => {
    const html = stackTooltipHtml(
      [{ axisValue: "2026-08-01", seriesId: "asset:x", seriesName: "<img src=x onerror=alert(1)>", value: 5 }],
      "usd",
      new Set(),
    );
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).toContain("Total: $5.00");
  });

  it("says a gap month is left blank, not a dip", () => {
    const html = stackTooltipHtml([{ axisValue: "2026-07-01", seriesId: "asset:GNO", seriesName: "GNO", value: null }], "usd", new Set(["2026-07-01"]));
    expect(html).toContain("left blank, not a dip");
  });

  it("names what a partial month is missing, and still shows its values", () => {
    const html = stackTooltipHtml(
      [{ axisValue: "2026-07-01", seriesId: "asset:GNO", seriesName: "GNO", value: 5 }],
      "usd",
      new Set(),
      { "2026-07-01": "Ethereum: partial upstream: 1 registry token not served (SAFE)" },
    );
    expect(html).toContain("(SAFE) — drawn from what was served");
    expect(html).toContain("GNO: $5.00");
  });

  it("the price tooltip escapes too", () => {
    const option = priceLineOption([{ day: "2026-09-01", priceSymbol: "X", priceUsd: 2, role: "priced" }], "<b>x</b>");
    const formatter = (option.tooltip as { formatter: (params: unknown) => string }).formatter;
    const html = formatter([{ axisValue: "2026-09-01", seriesName: "<b>x</b>", value: 2 }]);
    expect(html).not.toContain("<b>x</b>");
    expect(html).toContain("&lt;b&gt;");
  });
});

describe("bandRefFromSeriesId", () => {
  it("resolves every band kind and rejects helper series", () => {
    expect(bandRefFromSeriesId("chain:100")).toEqual({ kind: "chain", chainId: 100 });
    expect(bandRefFromSeriesId("asset:GNO")).toEqual({ kind: "asset", key: "GNO" });
    expect(bandRefFromSeriesId("asset:1:0xabc")).toEqual({ kind: "asset", key: "1:0xabc" });
    expect(bandRefFromSeriesId("wallet:0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f"))
      .toEqual({ kind: "wallet", wallet: "0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f" });
    expect(bandRefFromSeriesId("class:Stablecoins")).toEqual({ kind: "class", key: "Stablecoins" });
    expect(bandRefFromSeriesId("other")).toEqual({ kind: "other" });
    expect(bandRefFromSeriesId("gaps")).toBeNull();
    expect(bandRefFromSeriesId("markers")).toBeNull();
    expect(bandRefFromSeriesId("wallet:not-an-address")).toBeNull();
    expect(bandRefFromSeriesId(undefined)).toBeNull();
  });
});

describe("breadth and price", () => {
  it("breadth stacks token counts and puts positions on the second axis", () => {
    const option = BUILT[1][1];
    const bars = series(option).filter((entry) => entry.type === "bar");
    expect(bars.map((entry) => entry.name)).toEqual(["Hub-priced", "Listed", "Unverified", "Spam (hidden)"]);
    const positions = series(option).find((entry) => entry.name === "Positions")!;
    expect(positions.markLine?.data).toEqual([{ xAxis: "2026-07-01" }]);
    expect(yAxisNames(option)).toEqual(["tokens", "positions"]);
  });

  it("price history splits registry roles into separate, unbridged lines", () => {
    const option = priceLineOption([
      { day: "2024-08-01", priceSymbol: "EURE", priceUsd: 1.08, role: "priced" },
      { day: "2024-09-01", priceSymbol: "EURE", priceUsd: 1.1, role: "retired_mirror" },
    ], "EURe v1");
    expect(series(option)).toHaveLength(2);
    expect(series(option)[0].data).toEqual([1.08, null]);
    expect(series(option)[1].data).toEqual([null, 1.1]);
  });
});
