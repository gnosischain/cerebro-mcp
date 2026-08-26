// The two like-timeseries builders: category stack + likers line (Forum tab)
// and per-post stack (topic drill-down). Both ride the shared
// stackedSeriesOption, so fold/residual behavior is inherited — these tests
// pin the wrapper contracts: line alignment, post labeling, and the counted
// residual once category cardinality exceeds the band ceiling.

import { describe, expect, it } from "vitest";

import { likesByCategoryOption, topicLikesOption } from "../model/chartOptions";

type AnySpec = {
  xAxis?: { data?: string[] };
  yAxis?: unknown;
  series?: Array<{ name?: string; type?: string; yAxisIndex?: number; stack?: string; data?: unknown[] }>;
};

const CATEGORY_ROWS = [
  { bucket: "2026-05-01", bucket_unit: "month", category: "GIPs", likes: 130 },
  { bucket: "2026-05-01", bucket_unit: "month", category: "General", likes: 60 },
  { bucket: "2026-06-01", bucket_unit: "month", category: "GIPs", likes: 110 },
];

describe("likesByCategoryOption", () => {
  it("stacks category bars and rides the likers line on a second axis", () => {
    const likers = new Map<string, number | null>([
      ["2026-05-01", 58],
      ["2026-06-01", 41],
    ]);
    const spec = likesByCategoryOption(CATEGORY_ROWS, likers) as AnySpec;
    expect(spec.xAxis?.data).toEqual(["2026-05-01", "2026-06-01"]);
    const line = spec.series?.find((s) => s.name === "Unique likers");
    expect(line?.type).toBe("line");
    expect(line?.yAxisIndex).toBe(1);
    // Aligned with buckets, null (not 0) where no likers figure exists.
    expect(line?.data).toEqual([58, 41]);
    expect(Array.isArray(spec.yAxis)).toBe(true);
    const bars = spec.series?.filter((s) => s.type === "bar") ?? [];
    expect(bars.map((s) => s.name)).toContain("GIPs");
    for (const bar of bars) expect(bar.stack).toBe("total");
  });

  it("a bucket without a likers row gets null, never a fabricated 0", () => {
    const spec = likesByCategoryOption(CATEGORY_ROWS, new Map([["2026-05-01", 58]])) as AnySpec;
    const line = spec.series?.find((s) => s.name === "Unique likers");
    expect(line?.data).toEqual([58, null]);
  });

  it("folds beyond the band ceiling into a COUNTED residual", () => {
    const rows = Array.from({ length: 10 }, (_, i) => ({
      bucket: "2026-05-01", bucket_unit: "month", category: `Cat ${i}`, likes: 10 - i,
    }));
    const spec = likesByCategoryOption(rows, new Map()) as AnySpec;
    const residual = spec.series?.find((s) => String(s.name).startsWith("Other"));
    expect(residual, "overflow categories must fold, not vanish").toBeTruthy();
    expect(String(residual!.name)).toMatch(/\(\+\d+\)/);
  });
});

describe("topicLikesOption", () => {
  // The payload carries no author names (WL-039 privacy alignment) — the
  // hostile-username sanitization case that lived here died with the input
  // contract; sanitizeSymbol keeps its own coverage (treasury chart tests).
  const ROWS = [
    { bucket: "2026-06-29", bucket_unit: "week", post_number: 1, likes: 9 },
    { bucket: "2026-06-29", bucket_unit: "week", post_number: 3, likes: 4 },
    { bucket: "2026-07-06", bucket_unit: "week", post_number: 0, likes: 2 },
  ];

  it("names posts by number and keeps the de-indexed residual visible", () => {
    const spec = topicLikesOption(ROWS) as AnySpec;
    const names = (spec.series ?? []).map((s) => s.name);
    expect(names).toContain("Post #1");
    expect(names).toContain("Post #3");
    expect(names).toContain("Unknown post");
  });

  it("stacks bars over the buckets and names the axis from the rows' unit", () => {
    const spec = topicLikesOption(ROWS) as AnySpec & { xAxis?: { name?: string } };
    expect(spec.xAxis?.data).toEqual(["2026-06-29", "2026-07-06"]);
    for (const s of spec.series ?? []) expect(s.stack).toBe("total");
    // Buckets are adaptive server-side — the axis label follows the data.
    expect(spec.xAxis?.name).toBe("per week");
    const daily = topicLikesOption([
      { bucket: "2026-07-01", bucket_unit: "day", post_number: 1, likes: 2 },
    ]) as AnySpec & { xAxis?: { name?: string } };
    expect(daily.xAxis?.name).toBe("per day");
  });
});
