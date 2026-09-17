// Liquidity-profile math (no React, no ECharts).
//
// Tick IS a log-price axis: price = 1.0001^tick, so a linear tick axis is a
// log price axis and the three label modes (tick / price / % from current)
// are pure relabelings of the same coordinate. Prices are token1-raw per
// token0-raw; a decimals-adjusted price exists only when BOTH decimals are
// known — never fabricated here.
//
// Full-range positions (both edges at ±887220 / ±887270) are drawn as a band
// and are NEVER allowed to set the axis window: an "All" zoom that honoured
// them would show one flat line 1.77M ticks wide.

import { finite } from "../../shared/rowDataset";
import { fmtPrice, fmtSignedPct, fmtTick } from "./format";

export const TICK_BASE = 1.0001;
const LN_TICK = Math.log(TICK_BASE);

/** Beyond this magnitude a tick edge is the full-range boundary under either
 * spacing convention (887220 for spacing 60, 887270 for spacing 10). */
export const FULL_RANGE_TICK = 880_000;

export function tickToPrice(tick: number): number {
  return Math.pow(TICK_BASE, tick);
}

export function priceToTick(price: number): number {
  return Math.log(price) / LN_TICK;
}

/** Percent move -> whole ticks (5% -> 488, 1% -> 100, 20% -> 1823). */
export function pctToTicks(pct: number): number {
  return Math.round(Math.log(1 + pct / 100) / LN_TICK);
}

/** Tick offset -> percent move. */
export function ticksToPct(ticks: number): number {
  return (Math.pow(TICK_BASE, ticks) - 1) * 100;
}

/** raw (token1-raw per token0-raw) -> human price. null unless BOTH decimals
 * are known — a missing decimal is disclosed, never guessed. */
export function adjustPrice(
  raw: number | null | undefined,
  dec0: number | null | undefined,
  dec1: number | null | undefined,
): number | null {
  const price = finite(raw);
  const d0 = finite(dec0);
  const d1 = finite(dec1);
  if (price === null || d0 === null || d1 === null) return null;
  return price * 10 ** (d0 - d1);
}

/** Both edges at the boundary (either spacing convention). */
export function isFullRange(lower: number, upper: number): boolean {
  return lower <= -FULL_RANGE_TICK && upper >= FULL_RANGE_TICK;
}

/** At least one edge at the boundary — the outer segments of a profile. */
export function touchesBoundary(lower: number, upper: number): boolean {
  return lower <= -FULL_RANGE_TICK || upper >= FULL_RANGE_TICK;
}

export interface ProfileRange {
  lower: number;
  upper: number;
  liquidity: number;
  isGap: boolean;
  containsCurrent: boolean;
}

export type ZoomPreset = "1pct" | "5pct" | "20pct" | "x2" | "all";

export const ZOOM_PRESETS: ReadonlyArray<{ id: ZoomPreset; label: string; ticks: number | null }> = [
  { id: "1pct", label: "±1%", ticks: pctToTicks(1) },
  { id: "5pct", label: "±5%", ticks: pctToTicks(5) },
  { id: "20pct", label: "±20%", ticks: pctToTicks(20) },
  { id: "x2", label: "×2", ticks: pctToTicks(100) },
  { id: "all", label: "All", ticks: null },
];

export const DEFAULT_ZOOM: ZoomPreset = "20pct";

export function isZoomPreset(value: unknown): value is ZoomPreset {
  return ZOOM_PRESETS.some((preset) => preset.id === value);
}

export type AxisMode = "tick" | "price" | "pct";
export const DEFAULT_AXIS: AxisMode = "price";
export function isAxisMode(value: unknown): value is AxisMode {
  return value === "tick" || value === "price" || value === "pct";
}

export interface TickWindow {
  lo: number;
  hi: number;
}

/** Window for the "All" preset: the span of every INNER tick edge (edges at
 * the boundary are ignored), padded 2%, always including the current tick.
 * With no inner edge at all (a pool holding only full-range positions) fall
 * back to ±100% around the current tick. */
function allWindow(currentTick: number | null, ranges: ProfileRange[]): TickWindow {
  const inner: number[] = [];
  for (const range of ranges) {
    if (range.lower > -FULL_RANGE_TICK) inner.push(range.lower);
    if (range.upper < FULL_RANGE_TICK) inner.push(range.upper);
  }
  const distinct = [...new Set(inner)].sort((a, b) => a - b);
  const half = pctToTicks(100);
  if (distinct.length === 0) {
    const centre = currentTick ?? 0;
    return { lo: centre - half, hi: centre + half };
  }
  let lo = distinct[0];
  let hi = distinct[distinct.length - 1];
  if (currentTick !== null) {
    lo = Math.min(lo, currentTick);
    hi = Math.max(hi, currentTick);
  }
  if (hi <= lo) {
    return { lo: lo - half, hi: lo + half };
  }
  const pad = Math.max(10, Math.round((hi - lo) * 0.02));
  return { lo: lo - pad, hi: hi + pad };
}

/** Axis window in ticks for a zoom preset. Percent presets centre on the
 * current tick; without a current tick they degrade to "All". */
export function profileWindow(
  currentTick: number | null,
  preset: ZoomPreset,
  ranges: ProfileRange[],
): TickWindow {
  const spec = ZOOM_PRESETS.find((entry) => entry.id === preset);
  if (!spec || spec.ticks === null || currentTick === null) {
    return allWindow(currentTick, ranges);
  }
  return { lo: currentTick - spec.ticks, hi: currentTick + spec.ticks };
}

/** Tick-weighted share of liquidity inside a window:
 * Σ L_i × overlap_i / Σ L_i × width_i. A full-range position dominates the
 * denominator by construction — that is disclosed, not hidden. null when
 * nothing is measured. */
export function shareWithinWindow(ranges: ProfileRange[], window: TickWindow): number | null {
  let numerator = 0;
  let denominator = 0;
  for (const range of ranges) {
    if (!(range.liquidity > 0)) continue;
    const width = range.upper - range.lower;
    if (!(width > 0)) continue;
    denominator += range.liquidity * width;
    const overlap = Math.min(range.upper, window.hi) - Math.max(range.lower, window.lo);
    if (overlap > 0) numerator += range.liquidity * overlap;
  }
  if (!(denominator > 0)) return null;
  return numerator / denominator;
}

export interface ProfileBar {
  /** Drawn extent (clipped to the window). */
  lo: number;
  hi: number;
  /** True extent of the range. */
  tickLower: number;
  tickUpper: number;
  liquidity: number;
  clippedLo: boolean;
  clippedHi: boolean;
  isGap: boolean;
  containsCurrent: boolean;
  /** Range reaches a full-range boundary on one side. */
  touchesBoundary: boolean;
}

export interface ProfileModel {
  bars: ProfileBar[];
  /** Summed liquidity of the true full-range rows, drawn as a band. */
  fullRangeBand: { liquidity: number; count: number } | null;
  window: TickWindow;
  currentX: number | null;
  /** Tick-weighted share of liquidity inside the window (over ALL ranges). */
  shareInWindow: number | null;
  maxLiquidity: number;
  /** Ranges (incl. gaps) that fell entirely outside the window. */
  rangesOutside: number;
  empty: boolean;
}

export interface BuildProfileArgs {
  ranges: ProfileRange[];
  currentTick: number | null;
  window: TickWindow;
}

export function buildProfileModel(args: BuildProfileArgs): ProfileModel {
  const { ranges, currentTick, window } = args;
  const bars: ProfileBar[] = [];
  let fullLiquidity = 0;
  let fullCount = 0;
  let maxLiquidity = 0;
  let rangesOutside = 0;
  for (const range of ranges) {
    if (isFullRange(range.lower, range.upper)) {
      if (range.liquidity > 0) {
        fullLiquidity += range.liquidity;
        fullCount += 1;
        maxLiquidity = Math.max(maxLiquidity, range.liquidity);
      }
      continue;
    }
    const lo = Math.max(range.lower, window.lo);
    const hi = Math.min(range.upper, window.hi);
    if (hi <= lo) {
      rangesOutside += 1;
      continue;
    }
    maxLiquidity = Math.max(maxLiquidity, range.liquidity);
    bars.push({
      lo,
      hi,
      tickLower: range.lower,
      tickUpper: range.upper,
      liquidity: range.liquidity,
      clippedLo: range.lower < window.lo,
      clippedHi: range.upper > window.hi,
      isGap: range.isGap || !(range.liquidity > 0),
      containsCurrent: range.containsCurrent,
      touchesBoundary: touchesBoundary(range.lower, range.upper),
    });
  }
  bars.sort((a, b) => a.lo - b.lo);
  return {
    bars,
    fullRangeBand: fullCount > 0 ? { liquidity: fullLiquidity, count: fullCount } : null,
    window,
    currentX: currentTick,
    shareInWindow: shareWithinWindow(ranges, window),
    maxLiquidity,
    rangesOutside,
    empty: bars.length === 0 && fullCount === 0,
  };
}

export interface AxisLabelContext {
  mode: AxisMode;
  currentTick: number | null;
  dec0: number | null;
  dec1: number | null;
  /** Price orientation: false = token1 per token0 (server), true = inverted. */
  inverted?: boolean;
}

/** Price at a tick in display orientation — adjusted when possible, raw
 * otherwise (`raw` flag says which). */
export function priceAtTick(
  tick: number,
  ctx: Pick<AxisLabelContext, "dec0" | "dec1" | "inverted">,
): { value: number; raw: boolean } {
  const rawPrice = tickToPrice(tick);
  const adjusted = adjustPrice(rawPrice, ctx.dec0, ctx.dec1);
  const value = adjusted ?? rawPrice;
  return { value: ctx.inverted ? 1 / value : value, raw: adjusted === null };
}

/** Axis label for a tick under the chosen mode. Tick labels never lie about
 * units: a raw price is still a price ratio, only the unit name changes. */
export function labelFor(tick: number, ctx: AxisLabelContext): string {
  if (ctx.mode === "price") return fmtPrice(priceAtTick(tick, ctx).value);
  if (ctx.mode === "pct") {
    if (ctx.currentTick === null) return fmtTick(tick);
    let ratio = Math.pow(TICK_BASE, tick - ctx.currentTick);
    if (ctx.inverted) ratio = 1 / ratio;
    return fmtSignedPct((ratio - 1) * 100, Math.abs(ratio - 1) < 0.001 ? 2 : 1);
  }
  return fmtTick(tick);
}

/** Axis unit name. "raw units" whenever a decimal is missing. */
export function unitLabel(args: {
  mode: AxisMode;
  sym0: string;
  sym1: string;
  dec0: number | null;
  dec1: number | null;
  inverted?: boolean;
}): string {
  if (args.mode === "tick") return "tick";
  if (args.mode === "pct") return "% from current price";
  const known = finite(args.dec0) !== null && finite(args.dec1) !== null;
  const [numer, denom] = args.inverted ? [args.sym0, args.sym1] : [args.sym1, args.sym0];
  return known ? `${numer} per ${denom}` : `${numer} per ${denom} (raw units)`;
}
