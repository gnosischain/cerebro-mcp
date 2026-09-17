// Liquidity-profile-over-time heatmap math (no React, no ECharts).
//
// Server contract (`pool_profile_heatmap`): ONE row per (sampled date, tick
// bucket) with
//   bucket_date       the sampled publication date
//   tick_bucket_lo/hi the bucket's tick edges (step = tick_step)
//   liquidity_float   tick-weighted MEAN active liquidity inside the bucket
//                     (Σ L × overlap / step) — a density, not a total
//   current_tick      that date's current tick
//   axis_lo/axis_hi   the server axis (min/max current tick ± 20% of price)
//   tick_step, date_step_days, dates_total, dates_sampled  disclosure
//
// The client re-bins the server buckets onto a fixed PRICE_LEVELS grid in one
// of two modes: `tick` (absolute tick — the price axis) or `relative` (offset
// from EACH date's own current tick, so a trending price keeps the profile
// centred). Re-binning keeps the density semantics: a client level gets the
// overlap-weighted mean of the server buckets covering it.

import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import { buildDepthScale, type DepthScale } from "../../cow-explorer/model/depthFootprintScale";
import type { HeatmapBucketRow } from "../types";
import { fmtSignedPct, fmtTick } from "./format";
import { ticksToPct } from "./liquidityProfile";

export const PRICE_LEVELS = 41;

export type HeatmapAxisMode = "tick" | "relative";

export type HeatmapWindow = "90d" | "1y" | "all";
export const HEATMAP_WINDOWS: ReadonlyArray<{ id: HeatmapWindow; label: string }> = [
  { id: "90d", label: "90d" },
  { id: "1y", label: "1y" },
  { id: "all", label: "All" },
];
export const DEFAULT_HEATMAP_WINDOW: HeatmapWindow = "1y";
export function isHeatmapWindow(value: unknown): value is HeatmapWindow {
  return value === "90d" || value === "1y" || value === "all";
}

export interface HeatmapRow {
  date: string;
  lo: number;
  hi: number;
  liquidity: number;
  currentTick: number | null;
  tickStep: number;
  dateStepDays: number;
  datesTotal: number;
  datesSampled: number;
}

export function parseHeatmapRows(dataset?: RowDataset): HeatmapRow[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<HeatmapBucketRow>;
    const date = String(row.bucket_date ?? "");
    const lo = finite(row.tick_bucket_lo);
    const hi = finite(row.tick_bucket_hi);
    const liquidity = finite(row.liquidity_float);
    if (!date || lo === null || hi === null || hi <= lo) return [];
    return [{
      date,
      lo,
      hi,
      liquidity: liquidity !== null && liquidity > 0 ? liquidity : 0,
      currentTick: finite(row.current_tick),
      tickStep: finite(row.tick_step) ?? hi - lo,
      dateStepDays: finite(row.date_step_days) ?? 0,
      datesTotal: finite(row.dates_total) ?? 0,
      datesSampled: finite(row.dates_sampled) ?? 0,
    }];
  });
}

/** [xIndex, yIndex, liquidity density]. */
export type HeatmapCell = [number, number, number];

export interface ProfileHeatmapModel {
  xLabels: string[];
  yLabels: string[];
  /** Level centres in axis units (tick, or tick offset in relative mode). */
  yCentres: number[];
  yEdges: number[];
  cells: HeatmapCell[];
  /** [xIndex, fractional yIndex] of the current tick across dates. */
  currentLine: Array<[number, number]>;
  scale: DepthScale;
  axisMode: HeatmapAxisMode;
  levels: number;
  lo: number;
  hi: number;
  tickStep: number;
  dateStepDays: number;
  datesTotal: number;
  datesSampled: number;
  empty: boolean;
}

export interface BuildHeatmapArgs {
  rows: HeatmapRow[];
  axisMode: HeatmapAxisMode;
  levels?: number;
  /** Tick-mode y label (e.g. a price or tick label); default is the tick. */
  labelFor?: (tick: number) => string;
}

function emptyModel(axisMode: HeatmapAxisMode, levels: number): ProfileHeatmapModel {
  return {
    xLabels: [], yLabels: [], yCentres: [], yEdges: [], cells: [], currentLine: [],
    scale: buildDepthScale([]), axisMode, levels, lo: 0, hi: 0, tickStep: 0,
    dateStepDays: 0, datesTotal: 0, datesSampled: 0, empty: true,
  };
}

export function buildProfileHeatmap(args: BuildHeatmapArgs): ProfileHeatmapModel {
  const levels = Math.max(1, args.levels ?? PRICE_LEVELS);
  const { axisMode } = args;
  if (args.rows.length === 0) return emptyModel(axisMode, levels);

  const xLabels = [...new Set(args.rows.map((row) => row.date))].sort();
  const xIndex = new Map(xLabels.map((date, index) => [date, index]));
  const currentByDate = new Map<string, number>();
  for (const row of args.rows) {
    if (row.currentTick !== null && !currentByDate.has(row.date)) currentByDate.set(row.date, row.currentTick);
  }

  // Axis value of a bucket edge: absolute tick, or offset from that date's
  // current tick (rows without a current tick cannot be placed relatively).
  const project = (row: HeatmapRow): [number, number] | null => {
    if (axisMode === "tick") return [row.lo, row.hi];
    const current = currentByDate.get(row.date);
    if (current === undefined) return null;
    return [row.lo - current, row.hi - current];
  };

  let lo = Infinity;
  let hi = -Infinity;
  const projected: Array<{ row: HeatmapRow; vlo: number; vhi: number }> = [];
  for (const row of args.rows) {
    const edges = project(row);
    if (!edges) continue;
    projected.push({ row, vlo: edges[0], vhi: edges[1] });
    lo = Math.min(lo, edges[0]);
    hi = Math.max(hi, edges[1]);
  }
  if (projected.length === 0 || !Number.isFinite(lo) || !Number.isFinite(hi)) {
    return emptyModel(axisMode, levels);
  }
  if (hi <= lo) hi = lo + 1;
  const span = hi - lo;
  const levelWidth = span / levels;
  const yEdges = Array.from({ length: levels + 1 }, (_, i) => lo + i * levelWidth);
  const yCentres = Array.from({ length: levels }, (_, i) => lo + (i + 0.5) * levelWidth);

  // Overlap-weighted mean density per (date, level). Numeric key, never a
  // string with a NUL separator (that broke every string-matching edit of
  // the CoW footprint once).
  const byCell = new Map<number, number>();
  for (const { row, vlo, vhi } of projected) {
    if (!(row.liquidity > 0)) continue;
    const first = Math.max(0, Math.floor((vlo - lo) / levelWidth));
    const last = Math.min(levels - 1, Math.ceil((vhi - lo) / levelWidth) - 1);
    const xi = xIndex.get(row.date);
    if (xi === undefined) continue;
    for (let level = first; level <= last; level += 1) {
      const overlap = Math.min(vhi, yEdges[level + 1]) - Math.max(vlo, yEdges[level]);
      if (!(overlap > 0)) continue;
      const key = xi * levels + level;
      byCell.set(key, (byCell.get(key) ?? 0) + (row.liquidity * overlap) / levelWidth);
    }
  }

  const cells: HeatmapCell[] = [];
  const values: number[] = [];
  for (const [key, value] of byCell) {
    if (!(value > 0)) continue;
    cells.push([Math.floor(key / levels), key % levels, value]);
    values.push(value);
  }
  cells.sort((a, b) => a[0] - b[0] || a[1] - b[1]);

  const currentLine: Array<[number, number]> = [];
  for (let xi = 0; xi < xLabels.length; xi += 1) {
    const current = currentByDate.get(xLabels[xi]);
    if (current === undefined) continue;
    const value = axisMode === "relative" ? 0 : current;
    if (value < lo || value > hi) continue;
    currentLine.push([xi, ((value - lo) / span) * levels - 0.5]);
  }

  const label = args.labelFor ?? ((tick: number) => fmtTick(tick));
  const yLabels = yCentres.map((centre) => (
    axisMode === "relative" ? fmtSignedPct(ticksToPct(centre)) : label(centre)
  ));
  const first = args.rows[0];

  return {
    xLabels,
    yLabels,
    yCentres,
    yEdges,
    cells,
    currentLine,
    scale: buildDepthScale(values),
    axisMode,
    levels,
    lo,
    hi,
    tickStep: first.tickStep,
    dateStepDays: first.dateStepDays,
    datesTotal: first.datesTotal,
    datesSampled: first.datesSampled,
    empty: cells.length === 0,
  };
}
