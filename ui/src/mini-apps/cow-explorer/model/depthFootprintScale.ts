// Colour scale + ramps for the depth FOOTPRINT (no React, no ECharts).
//
// The footprint encodes SIDE BY POSITION (bid = left half of a cell, ask =
// right half) and MAGNITUDE BY LIGHTNESS on a per-side sequential ramp. That
// split is the whole point: the previous chart put "which side dominates" on
// hue and magnitude on intensity, so a 51/49 cell shouted as loudly as 100/0.
//
// Magnitude classes are DISCRETE and labelled in real base units. Two earlier
// attempts failed here and both failure modes are guarded against below:
//   - a sqrt ramp capped at p90 washed every cell onto the neutral stop;
//   - signed percentile RANK put exactly 50% of cells above the midpoint *by
//     construction, independent of the data*, which is why the grid turned
//     into a wall of red. Rank also has no units, so its legend was unlabelled.
// The ladder here is logarithmic (decodable, pretty edges) with a hard
// occupancy guarantee: if any single class would swallow more than
// MAX_CLASS_SHARE of the cells, the edges are recomputed from quantiles.

/** Number of colour classes per side. Six keeps the legend under the ~7-class
 * limit past which adjacent classes stop being tellable apart. */
export const DEPTH_CLASS_COUNT = 6;

/** A class may not hold more than this share of lit cells; crossing it trips
 * the quantile fallback. 0.6 is deliberately loose — the log ladder should
 * normally win, and the fallback exists to make collapse impossible, not to
 * micro-tune the distribution. */
export const MAX_CLASS_SHARE = 0.6;

/** Imbalance outline: the dominant half is outlined when it holds at least
 * this multiple of the other side AND clears `imbalanceFloor`, so that dust
 * (0.30 vs 0.05) never draws attention. */
export const IMBALANCE_RATIO = 3;

/** Cell geometry thresholds for in-cell numbers, in device px. Two 4-glyph
 * 9px mono numbers plus the seam padding need ~48px; below that a label would
 * be clipped, which is worse than no label. */
export const NUMBER_MIN_CELL_W = 48;
export const NUMBER_MIN_CELL_H = 11;
/** Below this width a gutter would eat the cell, so cells butt together. */
export const CELL_GUTTER_MIN_W = 6;

export interface RampStep {
  /** Cell fill. */
  fill: string;
  /** Text drawn ON that fill (>= 4.5:1 against it). */
  ink: string;
}

// Dark surface (#12161c): dim -> bright, so magnitude reads as contrast
// against the card. Light surface (#ffffff): pale -> deep. Both are monotone
// in relative luminance. Hue families match DEPTH_ASK_COLOR / DEPTH_BID_COLOR
// used by the 2-D ladder on the sibling tab, so the two tabs read as one
// system; the ask ramp leans warm and the bid ramp cool so the pair also
// separates on warm/cool, which survives deuteranopia (side is carried by
// position anyway — hue here is redundant reinforcement, not the encoding).
const INK_DARK_ON_DIM = "#e6e9ee";
const INK_DARK_ON_BRIGHT = "#0b0e12";
const INK_LIGHT_ON_PALE = "#111418";
const INK_LIGHT_ON_DEEP = "#ffffff";

export const RAMP_ASK_DARK: RampStep[] = [
  { fill: "#40181b", ink: INK_DARK_ON_DIM },
  { fill: "#66201f", ink: INK_DARK_ON_DIM },
  { fill: "#93262a", ink: INK_DARK_ON_DIM },
  { fill: "#b82a2c", ink: INK_DARK_ON_DIM },
  { fill: "#ef4444", ink: INK_DARK_ON_BRIGHT },
  { fill: "#ff8585", ink: INK_DARK_ON_BRIGHT },
];
export const RAMP_BID_DARK: RampStep[] = [
  { fill: "#12331f", ink: INK_DARK_ON_DIM },
  { fill: "#17492c", ink: INK_DARK_ON_DIM },
  { fill: "#1c6b3d", ink: INK_DARK_ON_DIM },
  { fill: "#229653", ink: INK_DARK_ON_BRIGHT },
  { fill: "#2bc36a", ink: INK_DARK_ON_BRIGHT },
  { fill: "#6fe6a4", ink: INK_DARK_ON_BRIGHT },
];
export const RAMP_ASK_LIGHT: RampStep[] = [
  { fill: "#fde5e5", ink: INK_LIGHT_ON_PALE },
  { fill: "#fbc0c0", ink: INK_LIGHT_ON_PALE },
  { fill: "#f28c8c", ink: INK_LIGHT_ON_PALE },
  { fill: "#e05454", ink: INK_LIGHT_ON_PALE },
  { fill: "#c22626", ink: INK_LIGHT_ON_DEEP },
  { fill: "#8c1414", ink: INK_LIGHT_ON_DEEP },
];
export const RAMP_BID_LIGHT: RampStep[] = [
  { fill: "#dcf4e6", ink: INK_LIGHT_ON_PALE },
  { fill: "#b2e5c6", ink: INK_LIGHT_ON_PALE },
  { fill: "#6bcb93", ink: INK_LIGHT_ON_PALE },
  { fill: "#2aa45e", ink: INK_LIGHT_ON_PALE },
  { fill: "#157e41", ink: INK_LIGHT_ON_DEEP },
  { fill: "#0a5a2c", ink: INK_LIGHT_ON_DEEP },
];

export function rampFor(side: "ask" | "bid", isDark: boolean): RampStep[] {
  if (side === "ask") return isDark ? RAMP_ASK_DARK : RAMP_ASK_LIGHT;
  return isDark ? RAMP_BID_DARK : RAMP_BID_LIGHT;
}

/** Side-neutral stroke for the imbalance outline and the median line — never a
 * side hue, so it cannot be misread as more depth. */
export function footprintInk(isDark: boolean) {
  return {
    imbalance: isDark ? "rgba(226,232,240,0.55)" : "rgba(11,11,11,0.50)",
    midLine: isDark ? "#e2e8f0" : "#0f172a",
    axis: isDark ? "#aab3be" : "#5b6473",
  };
}

/** Round UP to a "nice" mantissa (1, 2, 3, 5) x 10^k. */
export function niceUp(value: number): number {
  if (!(value > 0) || !Number.isFinite(value)) return value;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const mantissa = value / magnitude;
  const nice = mantissa <= 1 ? 1 : mantissa <= 2 ? 2 : mantissa <= 3 ? 3 : mantissa <= 5 ? 5 : 10;
  return nice * magnitude;
}

/** Round UP to one significant figure (quantile-fallback edges). */
export function ceil1sf(value: number): number {
  if (!(value > 0) || !Number.isFinite(value)) return value;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude - 1e-9) * magnitude;
}

/** Nearest-rank percentile of a pre-sorted ascending array. */
export function percentileSorted(sorted: number[], p: number): number {
  if (sorted.length === 0) return NaN;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))));
  return sorted[i];
}

/** At most 5 characters — in-cell labels have no room for `formatLadderNumber`'s
 * 6 significant digits. */
export function compactDepth(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(abs >= 1e10 ? 0 : 1)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(abs >= 1e7 ? 0 : 1)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(abs >= 1e4 ? 0 : 1)}k`;
  if (abs >= 100) return value.toFixed(0);
  if (abs >= 1) return value.toFixed(1);
  if (abs >= 0.01) return value.toFixed(2);
  return value.toExponential(0);
}

/** Legend/axis label for a bin edge. */
function edgeLabel(value: number): string {
  return compactDepth(value);
}

export interface DepthScale {
  /** `DEPTH_CLASS_COUNT - 1` strictly increasing edges in BASE units (fewer
   * when the distribution is degenerate). Class k is (edges[k-1], edges[k]]. */
  edges: number[];
  /** How many cells landed in each class — drives the legend counts and the
   * occupancy guarantee. */
  counts: number[];
  /** Human labels, one per class, in real base units. */
  labels: string[];
  /** Class index (0 .. edges.length) for a per-side depth. */
  stepIndex: (depth: number) => number;
  /** A side must clear this to be outlined as imbalanced (p60 of positives). */
  imbalanceFloor: number;
  /** True when the log ladder was rejected for collapsing and quantile edges
   * were used instead — surfaced only in tests, not in the UI. */
  usedQuantileFallback: boolean;
}

function classOf(edges: number[], value: number): number {
  let i = 0;
  while (i < edges.length && value > edges[i]) i += 1;
  return i;
}

function countClasses(edges: number[], sorted: number[]): number[] {
  const counts = new Array(edges.length + 1).fill(0);
  for (const v of sorted) counts[classOf(edges, v)] += 1;
  return counts;
}

function dedupeIncreasing(values: number[], max: number): number[] {
  const out: number[] = [];
  for (const v of values) {
    if (!Number.isFinite(v) || v <= 0) continue;
    if (v >= max) continue; // keep the open-ended top class non-empty
    if (out.length === 0 || v > out[out.length - 1]) out.push(v);
  }
  return out;
}

/** Log ladder: `DEPTH_CLASS_COUNT` classes spanning `decades` decades below a
 * p98 anchor, each edge snapped to a nice mantissa. Decodable and evenly
 * spaced in ratio, which is how depth is actually distributed. */
function pruneEmpty(edges: number[], sorted: number[]): number[] {
  let out = edges;
  for (;;) {
    const counts = countClasses(out, sorted);
    const emptyAt = counts.findIndex((c) => c === 0);
    if (emptyAt < 0 || out.length === 0) return out;
    // Merge the empty class into a neighbour by dropping the edge that creates
    // it. The BOTTOM class can be empty too — log edges start at a computed
    // floor that may sit below the smallest observed depth, unlike quantile
    // edges whose first edge is the median. The top class is kept non-empty by
    // `dedupeIncreasing` dropping edges >= max.
    const drop = emptyAt === 0 ? 0 : emptyAt - 1;
    out = out.filter((_, k) => k !== drop);
  }
}

function logEdges(sorted: number[]): number[] {
  const hi = niceUp(percentileSorted(sorted, 0.98));
  const max = sorted[sorted.length - 1];
  if (!(hi > 0)) return [];
  const low = Math.max(percentileSorted(sorted, 0.1), hi / 1e5, Number.MIN_VALUE);
  const decades = Math.min(4, Math.max(2, Math.ceil(Math.log10(hi / low))));
  const lo = hi / 10 ** decades;
  const raw: number[] = [];
  for (let i = 1; i < DEPTH_CLASS_COUNT; i += 1) {
    raw.push(niceUp(lo * 10 ** ((decades * i) / DEPTH_CLASS_COUNT)));
  }
  return pruneEmpty(dedupeIncreasing(raw, max), sorted);
}

/** Quantile ladder: the hard floor. Occupancy is fixed by the quantiles
 * themselves, so no class can swallow the distribution however skewed it is. */
function quantileEdges(sorted: number[]): number[] {
  const max = sorted[sorted.length - 1];
  const edges = dedupeIncreasing(
    [0.5, 0.75, 0.9, 0.98].map((q) => ceil1sf(percentileSorted(sorted, q))),
    max,
  );
  // Rounding up can empty an interior class; merge it downward so every
  // rendered swatch corresponds to at least one real cell.
  return pruneEmpty(edges, sorted);
}

/**
 * Build the shared magnitude scale from every POSITIVE per-side cell depth
 * (ask depths and bid depths pooled). Pooling is what makes "the bid half is
 * darker than the ask half" mean "there are more bids here" — with separate
 * domains the two halves would not be comparable.
 */
export function buildDepthScale(values: number[]): DepthScale {
  const sorted = values.filter((v) => Number.isFinite(v) && v > 0).sort((a, b) => a - b);
  const finish = (edges: number[], usedQuantileFallback: boolean): DepthScale => {
    const counts = countClasses(edges, sorted);
    const labels = edges.length === 0
      ? ["all cells"]
      : [
        `≤ ${edgeLabel(edges[0])}`,
        ...edges.slice(1).map((e, i) => `${edgeLabel(edges[i])}–${edgeLabel(e)}`),
        `> ${edgeLabel(edges[edges.length - 1])}`,
      ];
    return {
      edges,
      counts,
      labels,
      stepIndex: (depth: number) => classOf(edges, depth),
      imbalanceFloor: sorted.length ? percentileSorted(sorted, 0.6) : 0,
      usedQuantileFallback,
    };
  };
  if (sorted.length === 0) return finish([], false);

  const log = logEdges(sorted);
  const logCounts = countClasses(log, sorted);
  const worstShare = Math.max(...logCounts) / sorted.length;
  // The log ladder is preferred (nice, decodable edges) but only survives if
  // it actually spreads the data — this is the guard that makes the wall-of-one
  // -colour failure impossible for ANY distribution.
  if (log.length > 0 && worstShare <= MAX_CLASS_SHARE) return finish(log, false);
  return finish(quantileEdges(sorted), true);
}

/** Which side (if either) should be outlined as dominant at this cell. */
export function isImbalanced(
  ask: number,
  bid: number,
  floor: number,
): "ask" | "bid" | null {
  const a = ask > 0 ? ask : 0;
  const b = bid > 0 ? bid : 0;
  if (a >= b * IMBALANCE_RATIO && a >= floor && a > 0) return "ask";
  if (b >= a * IMBALANCE_RATIO && b >= floor && b > 0) return "bid";
  return null;
}
