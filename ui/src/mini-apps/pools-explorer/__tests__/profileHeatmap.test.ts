import { describe, expect, it } from "vitest";

import { DATASET_COLUMNS } from "../types";
import { PRICE_LEVELS, buildProfileHeatmap, parseHeatmapRows, type HeatmapRow } from "../model/profileHeatmap";

const COLUMNS = [...DATASET_COLUMNS.pool_profile_heatmap];

function row(date: string, lo: number, hi: number, liquidity: unknown, current: number | null): unknown[] {
  return [date, lo, hi, liquidity, current, 0, 10_000, 100, 4, 365, 92];
}

const rows = (): HeatmapRow[] => parseHeatmapRows({
  columns: COLUMNS,
  rows: [
    row("2026-01-01", 1000, 1100, 5, 1000),
    row("2026-01-01", 1100, 1200, 1, 1000),
    row("2026-02-01", 2000, 2100, 5, 2000),
    row("2026-02-01", 2100, 2200, 1, 2000),
  ],
});

describe("parseHeatmapRows", () => {
  it("reads by column name, drops malformed buckets, and clamps negative liquidity to 0", () => {
    const parsed = parseHeatmapRows({
      columns: COLUMNS,
      rows: [row("2026-01-01", 10, 20, -3, 15), row("", 10, 20, 1, 15), row("2026-01-01", 20, 20, 1, 15), row("2026-01-01", 30, 40, "bad", 15)],
    });
    expect(parsed).toHaveLength(2);
    expect(parsed[0]).toMatchObject({ date: "2026-01-01", lo: 10, hi: 20, liquidity: 0, currentTick: 15, tickStep: 100, datesTotal: 365, datesSampled: 92 });
    expect(parsed[1].liquidity).toBe(0);
  });
});

describe("buildProfileHeatmap", () => {
  it("relative mode aligns equal offsets from DIFFERENT current ticks onto the same level", () => {
    const model = buildProfileHeatmap({ rows: rows(), axisMode: "relative" });
    expect(model.xLabels).toEqual(["2026-01-01", "2026-02-01"]);
    const byDate = new Map<number, Array<[number, number]>>();
    for (const [xi, yi, value] of model.cells) {
      byDate.set(xi, [...(byDate.get(xi) ?? []), [yi, value]]);
    }
    expect(byDate.get(0)).toEqual(byDate.get(1));
    // The current tick sits at the SAME (flat) level on every date.
    expect(new Set(model.currentLine.map(([, y]) => y)).size).toBe(1);
    expect(model.yLabels[0]).toMatch(/%$/);
  });

  it("tick mode places buckets by absolute tick and follows the current tick", () => {
    const model = buildProfileHeatmap({ rows: rows(), axisMode: "tick" });
    const levelsOf = (xi: number) => model.cells.filter((cell) => cell[0] === xi).map((cell) => cell[1]);
    expect(Math.max(...levelsOf(0))).toBeLessThan(Math.min(...levelsOf(1)));
    expect(model.currentLine[0][1]).toBeLessThan(model.currentLine[1][1]);
    expect(model.lo).toBe(1000);
    expect(model.hi).toBe(2200);
    expect(model.levels).toBe(PRICE_LEVELS);
  });

  it("re-bins as a density: a wide bucket contributes its mean, not its total", () => {
    const wide = parseHeatmapRows({ columns: COLUMNS, rows: [row("2026-01-01", 0, 4100, 7, 2000)] });
    const model = buildProfileHeatmap({ rows: wide, axisMode: "tick", levels: 41 });
    expect(model.cells).toHaveLength(41);
    for (const [, , value] of model.cells) expect(value).toBeCloseTo(7, 9);
  });

  it("uses the caller's tick label in tick mode and is empty-safe", () => {
    const model = buildProfileHeatmap({ rows: rows(), axisMode: "tick", labelFor: (tick) => `t${Math.round(tick)}` });
    expect(model.yLabels[0]).toMatch(/^t\d+$/);
    const empty = buildProfileHeatmap({ rows: [], axisMode: "relative" });
    expect(empty.empty).toBe(true);
    expect(empty.cells).toEqual([]);
    expect(empty.scale.labels).toEqual(["all cells"]);
  });

  it("skips rows with no current tick in relative mode but keeps them in tick mode", () => {
    const mixed = parseHeatmapRows({ columns: COLUMNS, rows: [row("2026-01-01", 10, 20, 3, null), row("2026-01-02", 10, 20, 3, 15)] });
    expect(buildProfileHeatmap({ rows: mixed, axisMode: "relative" }).xLabels).toEqual(["2026-01-01", "2026-01-02"]);
    expect(buildProfileHeatmap({ rows: mixed, axisMode: "relative" }).cells.every(([xi]) => xi === 1)).toBe(true);
    expect(buildProfileHeatmap({ rows: mixed, axisMode: "tick" }).cells.some(([xi]) => xi === 0)).toBe(true);
  });
});
