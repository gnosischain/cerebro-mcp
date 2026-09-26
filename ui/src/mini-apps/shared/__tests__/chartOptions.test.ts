// Shared chart builders (used by the governance treasury and pools-explorer).
// Moved here from the governance treasuryChartOptions suite when the treasury
// builders moved to governance/model/treasuryCharts.ts: these builders belong
// to every app that imports them, not to one app's test file.

import type { EChartsOption } from "echarts";
import { describe, expect, it } from "vitest";

import { escapeHtml, fmtUsdCompact, stackedSeriesOption, treemapOption } from "../chartOptions";

interface SeriesLike {
  data?: unknown;
}

const series = (option: EChartsOption): SeriesLike[] => ((option as { series?: SeriesLike[] }).series ?? []);

function zoomTypes(option: EChartsOption): string[] {
  const zoom = (option as { dataZoom?: unknown }).dataZoom;
  if (!zoom) return [];
  return (Array.isArray(zoom) ? zoom : [zoom]).map((entry) => String((entry as { type?: string }).type));
}

const addr = (n: number) => `0x${String(n).padStart(2, "0").repeat(20)}`;

describe("shared builders", () => {
  it("treemap nodes carry ids only when the caller made them clickable", () => {
    const items = [{ id: addr(1), name: "GNO", value: 10 }];
    const clickable = series(treemapOption(items, { clickable: true }))[0].data as Array<{ id?: string }>;
    const inert = series(treemapOption(items))[0].data as Array<{ id?: string }>;
    expect(clickable[0].id).toBe(addr(1));
    expect(inert[0]).not.toHaveProperty("id");
  });

  it("the treemap tooltip escapes node names", () => {
    const option = treemapOption([{ name: "<img src=x>", value: 1 }]);
    const formatter = (option.tooltip as { formatter: (params: unknown) => string }).formatter;
    const html = formatter({ name: "<img src=x>", value: 1 });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("caps bands at five by default, with inside zoom only", () => {
    const rows = Array.from({ length: 8 }, (_, index) => ({
      bucket: "2026-03-01",
      key: `k${index}`,
      value: 100 - index,
    }));
    const option = stackedSeriesOption(rows, { xField: "bucket", valueField: "value", seriesField: "key" });
    expect(series(option)).toHaveLength(5);
    expect(zoomTypes(option)).toEqual(["inside"]);
  });

  it("normalizes each bucket to 100% in share mode", () => {
    const rows = [
      { bucket: "2026-03-01", key: "a", value: 3 },
      { bucket: "2026-03-01", key: "b", value: 1 },
    ];
    const option = stackedSeriesOption(rows, {
      xField: "bucket", valueField: "value", seriesField: "key", mode: "share",
    });
    expect(series(option).map((entry) => entry.data)).toEqual([[75], [25]]);
  });

  it("fmtUsdCompact dashes on null rather than coercing it to $0", () => {
    expect(fmtUsdCompact(null)).toBe("—");
    expect(fmtUsdCompact(undefined)).toBe("—");
    expect(fmtUsdCompact(Number.NaN)).toBe("—");
    expect(fmtUsdCompact(104_898_402)).toBe("$104.90M");
    expect(fmtUsdCompact(0)).toBe("$0.00");
  });

  it("escapeHtml neutralizes every markup character", () => {
    expect(escapeHtml(`<a href="x">'&'</a>`)).toBe("&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
    expect(escapeHtml(null)).toBe("");
  });
});
