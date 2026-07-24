// @vitest-environment jsdom
//
// Pure-logic coverage for the Depth panel: reference-price selection over the
// already-loaded native reference series (at-or-before the viewed time, never
// fetched, inverted on Flip) and the ± range presets around mid.

import { describe, expect, it } from "vitest";

import { depthRange, pickReferencePrice } from "../components/DepthPanel";
import type { ReferencePriceRow } from "../types";

const ref = (bucket: string, price: number): ReferencePriceRow => ({
  bucket,
  price,
  sourceObservedAt: bucket,
});

const SERIES = [
  ref("2026-07-23T08:00:00Z", 99.5),
  ref("2026-07-23T10:00:00Z", 100.5),
  ref("2026-07-23T12:00:00Z", 101.5),
];

describe("pickReferencePrice", () => {
  it("live view (null viewed time) picks the latest point", () => {
    expect(pickReferencePrice(SERIES, null, false)).toBe(101.5);
  });

  it("historical view picks the latest point at-or-before the viewed time", () => {
    const at = Date.parse("2026-07-23T11:59:00Z");
    expect(pickReferencePrice(SERIES, at, false)).toBe(100.5);
    // Exactly at a bucket → that bucket qualifies (at-or-before).
    expect(pickReferencePrice(SERIES, Date.parse("2026-07-23T10:00:00Z"), false)).toBe(100.5);
  });

  it("omits (never fakes) when no point precedes the viewed time or the series is empty", () => {
    expect(pickReferencePrice(SERIES, Date.parse("2026-07-23T07:00:00Z"), false)).toBeNull();
    expect(pickReferencePrice([], null, false)).toBeNull();
    expect(pickReferencePrice([ref("not-a-date", 100)], null, false)).toBeNull();
  });

  it("inverts the reference on a flipped book", () => {
    expect(pickReferencePrice(SERIES, null, true)).toBeCloseTo(1 / 101.5, 12);
  });
});

describe("depthRange", () => {
  it("builds a symmetric window around mid", () => {
    const range = depthRange(100, 10);
    expect(range?.min).toBeCloseTo(90, 9);
    expect(range?.max).toBeCloseTo(110, 9);
  });

  it("returns null (full extent) for All, missing mid, or a degenerate mid", () => {
    expect(depthRange(100, null)).toBeNull();
    expect(depthRange(null, 10)).toBeNull();
    expect(depthRange(0, 10)).toBeNull();
    expect(depthRange(Number.NaN, 10)).toBeNull();
  });

  it("clamps the lower bound at zero for wide presets", () => {
    expect(depthRange(10, 200)).toEqual({ min: 0, max: 30 });
  });
});
