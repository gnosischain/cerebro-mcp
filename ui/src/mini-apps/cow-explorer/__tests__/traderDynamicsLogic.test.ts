// @vitest-environment jsdom
//
// Pure-logic coverage for the trader-dynamics facet: the cohort-retention
// heatmap model (contiguous M+N axis, clamped shares) and latest-period pick.

import { describe, expect, it } from "vitest";
import { buildRetentionHeatmap, latestPeriod } from "../sections/TraderDynamicsSection";

describe("buildRetentionHeatmap", () => {
  it("builds a contiguous month-index axis and indexes cells against it", () => {
    const model = buildRetentionHeatmap([
      { cohort_month: "2026-05-01", month_index: 0, retention_share: 1 },
      { cohort_month: "2026-05-01", month_index: 2, retention_share: 0.4 }, // gap at M+1
      { cohort_month: "2026-06-01", month_index: 0, retention_share: 1 },
      { cohort_month: "2026-06-01", month_index: 1, retention_share: 0.35 },
    ]);
    expect(model.xLabels).toEqual(["0", "1", "2"]); // contiguous, gap preserved as absent cell
    expect(model.yLabels).toEqual(["2026-05-01", "2026-06-01"]);
    expect(model.cells).toContainEqual([2, 0, 0.4]);
    expect(model.cells).toContainEqual([1, 1, 0.35]);
    expect(model.cells).toHaveLength(4);
  });

  it("clamps shares into [0,1] and drops invalid rows", () => {
    const model = buildRetentionHeatmap([
      { cohort_month: "2026-05-01", month_index: 0, retention_share: 1.2 },
      { cohort_month: "2026-05-01", month_index: 1, retention_share: -0.1 },
      { cohort_month: "2026-05-01", month_index: 2, retention_share: "nope" },
      { cohort_month: "", month_index: 0, retention_share: 0.5 },
      { cohort_month: "2026-05-01", month_index: -3, retention_share: 0.5 },
    ]);
    expect(model.cells).toEqual([[0, 0, 1], [1, 0, 0]]);
  });

  it("returns an empty model for no usable rows", () => {
    expect(buildRetentionHeatmap([])).toEqual({ xLabels: [], yLabels: [], cells: [] });
  });
});

describe("latestPeriod", () => {
  it("picks the lexicographically newest period regardless of row order", () => {
    const rows = [
      { period: "2026-06-01", active_traders: 10 },
      { period: "2026-07-01", active_traders: 20 },
      { period: "2025-12-01", active_traders: 5 },
    ];
    expect(latestPeriod(rows)?.active_traders).toBe(20);
  });

  it("returns null for empty or period-less rows", () => {
    expect(latestPeriod([])).toBeNull();
    expect(latestPeriod([{ active_traders: 1 }])).toBeNull();
  });
});
