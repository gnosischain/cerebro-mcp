// Colour ramp for the liquidity-over-time heatmap. ONE sequential lime ramp —
// there is no side here (unlike the CoW bid/ask footprint), so the encoding
// is magnitude alone. The magnitude CLASSES come from the CoW
// `buildDepthScale` ladder (log edges with a quantile fallback and an
// occupancy guarantee), reused rather than reimplemented so both heatmaps
// bin the same way.
//
// Dark surface (#12161c): dim olive -> bright lime. Light surface (#ffffff):
// pale -> deep. Both monotone in relative luminance; ink is readable on
// every step.

import { DEPTH_CLASS_COUNT, type DepthScale, type RampStep } from "../../cow-explorer/model/depthFootprintScale";
import { fmtLiquidity } from "./format";

export { DEPTH_CLASS_COUNT };
export type { RampStep };

/** Legend labels for the scale's classes in L units (SI-compact, not the CoW
 * `compactDepth`, which stops at "B" and would print 3e18 as 3000000000B). */
export function liquidityClassLabels(scale: DepthScale): string[] {
  const { edges } = scale;
  if (edges.length === 0) return ["all cells"];
  return [
    `≤ ${fmtLiquidity(edges[0])}`,
    ...edges.slice(1).map((edge, index) => `${fmtLiquidity(edges[index])}–${fmtLiquidity(edge)}`),
    `> ${fmtLiquidity(edges[edges.length - 1])}`,
  ];
}

/** Cell fill for a liquidity value: a pure function of the shared scale. */
export function liquidityFill(scale: DepthScale, ramp: RampStep[], value: number): string {
  return ramp[Math.min(ramp.length - 1, scale.stepIndex(value))].fill;
}

const INK_ON_DIM = "#e6e9ee";
const INK_ON_BRIGHT = "#0b0e12";
const INK_ON_PALE = "#111418";
const INK_ON_DEEP = "#ffffff";

export const RAMP_LIQUIDITY_DARK: RampStep[] = [
  { fill: "#1f2c12", ink: INK_ON_DIM },
  { fill: "#334a19", ink: INK_ON_DIM },
  { fill: "#4b6c1f", ink: INK_ON_DIM },
  { fill: "#6c9626", ink: INK_ON_BRIGHT },
  { fill: "#93c92f", ink: INK_ON_BRIGHT },
  { fill: "#c8f56a", ink: INK_ON_BRIGHT },
];

export const RAMP_LIQUIDITY_LIGHT: RampStep[] = [
  { fill: "#eef7d6", ink: INK_ON_PALE },
  { fill: "#d3eda4", ink: INK_ON_PALE },
  { fill: "#aedb62", ink: INK_ON_PALE },
  { fill: "#7fb32a", ink: INK_ON_PALE },
  { fill: "#527d10", ink: INK_ON_DEEP },
  { fill: "#2f4d06", ink: INK_ON_DEEP },
];

export function liquidityRamp(isDark: boolean): RampStep[] {
  return isDark ? RAMP_LIQUIDITY_DARK : RAMP_LIQUIDITY_LIGHT;
}

/** Non-lime strokes for the current-tick path and reference marks, so they
 * can never be read as more liquidity. */
export function liquidityInk(isDark: boolean) {
  return {
    currentLine: isDark ? "#e2e8f0" : "#0f172a",
    band: isDark ? "rgba(103,232,249,0.16)" : "rgba(8,145,178,0.14)",
    bandStroke: isDark ? "rgba(103,232,249,0.55)" : "rgba(8,145,178,0.6)",
    bar: isDark ? "#93c92f" : "#527d10",
    barCurrent: isDark ? "#c8f56a" : "#2f4d06",
    gap: isDark ? "rgba(230,233,238,0.18)" : "rgba(17,20,24,0.12)",
    axis: isDark ? "#aab3be" : "#5b6473",
  };
}
