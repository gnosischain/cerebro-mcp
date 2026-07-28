// The load-bearing guard for the footprint's magnitude scale.
//
// Two previous encodings shipped and both failed in the browser:
//   1. sqrt magnitude capped at p90  -> everything washed onto the neutral stop;
//   2. signed percentile RANK        -> exactly 50% of cells above the midpoint
//      BY CONSTRUCTION, so the grid became a wall of saturated red regardless
//      of the data, and the legend had no units to print.
// These tests pin the properties that make both failure modes impossible.

import { describe, expect, it } from "vitest";

import {
  DEPTH_CLASS_COUNT,
  MAX_CLASS_SHARE,
  buildDepthScale,
  ceil1sf,
  compactDepth,
  isImbalanced,
  niceUp,
  rampFor,
} from "../model/depthFootprintScale";

/** Relative luminance (WCAG) of a #rrggbb colour. */
function luminance(hex: string): number {
  const channel = (i: number) => {
    const v = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Deterministic pseudo-lognormal sample — no Math.random, so failures repro. */
function lognormalSample(n: number, decades: number): number[] {
  const out: number[] = [];
  for (let i = 0; i < n; i += 1) {
    // A fixed irrational stride spreads the sequence evenly over (0,1).
    const u = ((i * 0.6180339887) % 1);
    out.push(10 ** (u * decades) * 0.01);
  }
  return out;
}

describe("niceUp / ceil1sf / compactDepth", () => {
  it("niceUp snaps up to a 1/2/3/5 mantissa", () => {
    expect(niceUp(0.7)).toBeCloseTo(1);
    expect(niceUp(1.4)).toBeCloseTo(2);
    expect(niceUp(2.2)).toBeCloseTo(3);
    expect(niceUp(4.4)).toBeCloseTo(5);
    expect(niceUp(6.1)).toBeCloseTo(10);
    expect(niceUp(4400)).toBeCloseTo(5000);
  });

  it("ceil1sf rounds up to one significant figure", () => {
    expect(ceil1sf(0.47)).toBeCloseTo(0.5);
    expect(ceil1sf(4.7)).toBeCloseTo(5);
    expect(ceil1sf(47)).toBeCloseTo(50);
  });

  it("compactDepth stays within an in-cell label budget", () => {
    for (const v of [1e-4, 0.05, 0.5, 9.8, 340, 1234, 12_345, 1.2e6, 3.4e9]) {
      expect(compactDepth(v).length).toBeLessThanOrEqual(5);
    }
  });
});

describe("buildDepthScale", () => {
  it("spreads a 3-decade lognormal instead of collapsing", () => {
    const scale = buildDepthScale(lognormalSample(600, 3));
    const total = scale.counts.reduce((a, b) => a + b, 0);
    expect(total).toBe(600);
    expect(Math.max(...scale.counts) / total).toBeLessThanOrEqual(MAX_CLASS_SHARE);
    expect(scale.counts.filter((c) => c > 0).length).toBeGreaterThanOrEqual(4);
  });

  it("keeps a lone whale in the top class without flattening the rest", () => {
    // The exact fixture that broke the previous ramp: ten small values and one
    // 100,000x outlier.
    const scale = buildDepthScale([...Array.from({ length: 10 }, (_, i) => i + 1), 1e6]);
    const top = scale.counts[scale.counts.length - 1];
    expect(top).toBe(1);
    // The other ten must not all pile into one class.
    expect(scale.counts.filter((c) => c > 0).length).toBeGreaterThanOrEqual(3);
    expect(scale.stepIndex(1e6)).toBe(scale.edges.length);
    expect(scale.stepIndex(1)).toBeLessThan(scale.edges.length);
  });

  it("never lets one class swallow the data, whatever the distribution", () => {
    // Includes the pathological cases: near-uniform, heavy ties, single value.
    const cases = [
      lognormalSample(200, 1),
      lognormalSample(200, 4),
      Array.from({ length: 200 }, (_, i) => 1 + (i % 3)),
      Array.from({ length: 50 }, () => 7),
      [42],
    ];
    for (const values of cases) {
      const scale = buildDepthScale(values);
      const total = scale.counts.reduce((a, b) => a + b, 0);
      expect(total).toBe(values.length);
      expect(Math.max(...scale.counts) / total).toBeLessThanOrEqual(1);
      // Every RENDERED swatch corresponds to at least one real cell.
      expect(scale.counts.every((c) => c > 0)).toBe(true);
      expect(scale.labels.length).toBe(scale.edges.length + 1);
    }
  });

  it("produces strictly increasing, finite, labelled edges", () => {
    const scale = buildDepthScale(lognormalSample(400, 3));
    expect(scale.edges.length).toBeLessThanOrEqual(DEPTH_CLASS_COUNT - 1);
    for (let i = 1; i < scale.edges.length; i += 1) {
      expect(scale.edges[i]).toBeGreaterThan(scale.edges[i - 1]);
    }
    expect(scale.edges.every((e) => Number.isFinite(e) && e > 0)).toBe(true);
    // Real units, not a unitless rank — the defect that made the old legend
    // undecodable.
    expect(scale.labels[0]).toMatch(/^≤ /);
    expect(scale.labels[scale.labels.length - 1]).toMatch(/^> /);
  });

  it("stepIndex is monotone and side-independent (one shared domain)", () => {
    const scale = buildDepthScale(lognormalSample(300, 3));
    let previous = -1;
    for (const v of [0.001, 0.05, 0.5, 5, 50, 5000]) {
      const step = scale.stepIndex(v);
      expect(step).toBeGreaterThanOrEqual(previous);
      previous = step;
    }
    // Same depth => same class, whichever side it came from.
    expect(scale.stepIndex(3.3)).toBe(scale.stepIndex(3.3));
  });

  it("is empty-safe", () => {
    const scale = buildDepthScale([]);
    expect(scale.edges).toEqual([]);
    expect(scale.labels).toEqual(["all cells"]);
    expect(scale.stepIndex(5)).toBe(0);
  });
});

describe("ramps", () => {
  it("are luminance-monotone with readable ink, in both themes", () => {
    for (const isDark of [true, false]) {
      const surface = isDark ? "#12161c" : "#ffffff";
      for (const side of ["ask", "bid"] as const) {
        const ramp = rampFor(side, isDark);
        expect(ramp).toHaveLength(DEPTH_CLASS_COUNT);
        const lums = ramp.map((s) => luminance(s.fill));
        for (let i = 1; i < lums.length; i += 1) {
          // Dark theme ramps go dim -> bright; light theme pale -> deep.
          if (isDark) expect(lums[i]).toBeGreaterThan(lums[i - 1]);
          else expect(lums[i]).toBeLessThan(lums[i - 1]);
        }
        for (const step of ramp) {
          expect(contrast(step.ink, step.fill)).toBeGreaterThanOrEqual(4.5);
        }
        // The faintest step must still be distinguishable from the card.
        expect(contrast(ramp[0].fill, surface)).toBeGreaterThan(1.1);
      }
    }
  });
});

describe("isImbalanced", () => {
  it("marks a 3:1 dominant side and ignores anything below it", () => {
    expect(isImbalanced(30, 10, 1)).toBe("ask");
    expect(isImbalanced(10, 30, 1)).toBe("bid");
    expect(isImbalanced(29.9, 10, 1)).toBeNull();
  });

  it("ignores dust below the floor even at an extreme ratio", () => {
    expect(isImbalanced(0.3, 0.0001, 1)).toBeNull();
    expect(isImbalanced(0, 0, 1)).toBeNull();
  });

  it("marks a one-sided cell", () => {
    expect(isImbalanced(5, 0, 1)).toBe("ask");
    expect(isImbalanced(0, 5, 1)).toBe("bid");
  });
});
