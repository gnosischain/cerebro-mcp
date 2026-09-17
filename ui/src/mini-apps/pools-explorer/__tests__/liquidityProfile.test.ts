import { describe, expect, it } from "vitest";

import {
  FULL_RANGE_TICK, adjustPrice, buildProfileModel, isFullRange, labelFor, pctToTicks, priceToTick,
  profileWindow, shareWithinWindow, tickToPrice, ticksToPct, unitLabel, type ProfileRange,
} from "../model/liquidityProfile";

const range = (lower: number, upper: number, liquidity: number, containsCurrent = false): ProfileRange => ({
  lower, upper, liquidity, isGap: liquidity <= 0, containsCurrent,
});

describe("tick math", () => {
  it("tick and price are inverses", () => {
    for (const tick of [-887_220, -1625, 0, 78_244, 276_324, 887_270]) {
      expect(priceToTick(tickToPrice(tick))).toBeCloseTo(tick, 6);
    }
    expect(tickToPrice(0)).toBe(1);
  });

  it("percent presets map to the backend's BAND_TICKS", () => {
    expect(pctToTicks(1)).toBe(100);
    expect(pctToTicks(5)).toBe(488);
    expect(pctToTicks(10)).toBe(953);
    expect(pctToTicks(20)).toBe(1823);
    expect(ticksToPct(pctToTicks(5))).toBeCloseTo(5, 1);
  });

  it("adjustPrice is null unless BOTH decimals are known — never fabricated", () => {
    expect(adjustPrice(1e12, 6, 18)).toBeCloseTo(1, 9);
    expect(adjustPrice(2500, 18, 18)).toBe(2500);
    expect(adjustPrice(0.85, null, 18)).toBeNull();
    expect(adjustPrice(0.85, 18, undefined)).toBeNull();
    expect(adjustPrice(null, 18, 18)).toBeNull();
    expect(adjustPrice("" as unknown as number, 18, 18)).toBeNull();
  });

  it("isFullRange accepts both spacing conventions and rejects one-sided edges", () => {
    expect(isFullRange(-887_220, 887_220)).toBe(true);
    expect(isFullRange(-887_270, 887_270)).toBe(true);
    expect(isFullRange(-887_220, 79_320)).toBe(false);
    expect(isFullRange(77_400, 887_220)).toBe(false);
    expect(FULL_RANGE_TICK).toBe(880_000);
  });
});

describe("profileWindow", () => {
  const ranges = [
    range(-887_220, 77_400, 1.2e18),
    range(77_400, 78_240, 3e19),
    range(78_240, 78_300, 7.4e19, true),
    range(78_300, 79_320, 2e19),
    range(79_320, 887_220, 1.2e18),
  ];

  it("percent presets centre on the current tick", () => {
    expect(profileWindow(78_244, "5pct", ranges)).toEqual({ lo: 78_244 - 488, hi: 78_244 + 488 });
    expect(profileWindow(78_244, "x2", ranges)).toEqual({ lo: 78_244 - pctToTicks(100), hi: 78_244 + pctToTicks(100) });
    expect(pctToTicks(100)).toBe(6932);
  });

  it("All ignores the boundary edges — a full-range/boundary segment never sets the window", () => {
    const window = profileWindow(78_244, "all", ranges);
    expect(window.lo).toBeGreaterThan(-FULL_RANGE_TICK);
    expect(window.hi).toBeLessThan(FULL_RANGE_TICK);
    expect(window.lo).toBeLessThanOrEqual(77_400);
    expect(window.hi).toBeGreaterThanOrEqual(79_320);
    expect(window.hi - window.lo).toBeLessThan(3000);
  });

  it("falls back to ±100% around the current tick when only full-range positions exist", () => {
    const only = [range(-887_220, 887_220, 5e15, true)];
    expect(profileWindow(-1625, "all", only)).toEqual({ lo: -1625 - pctToTicks(100), hi: -1625 + pctToTicks(100) });
    expect(profileWindow(null, "all", only)).toEqual({ lo: -pctToTicks(100), hi: pctToTicks(100) });
  });

  it("percent presets degrade to All without a current tick", () => {
    const window = profileWindow(null, "5pct", ranges);
    expect(window).toEqual(profileWindow(null, "all", ranges));
  });
});

describe("buildProfileModel", () => {
  it("never puts a full-range row in the bars; it becomes ONE band", () => {
    const ranges = [range(-887_220, 887_220, 5e15, true), range(-887_220, 887_220, 1e15)];
    const model = buildProfileModel({ ranges, currentTick: -1625, window: profileWindow(-1625, "all", ranges) });
    expect(model.bars).toHaveLength(0);
    expect(model.fullRangeBand).toEqual({ liquidity: 6e15, count: 2 });
    expect(model.empty).toBe(false);
    expect(model.currentX).toBe(-1625);
  });

  it("clips boundary segments to the window and flags the clipped side", () => {
    const ranges = [range(-887_220, 77_400, 1.2e18), range(77_400, 78_300, 7e19, true), range(78_300, 887_220, 1.2e18)];
    const window = { lo: 78_244 - 488, hi: 78_244 + 488 };
    const model = buildProfileModel({ ranges, currentTick: 78_244, window });
    expect(model.fullRangeBand).toBeNull();
    expect(model.bars).toHaveLength(2);
    const [inner, edge] = model.bars;
    expect(inner).toMatchObject({ lo: window.lo, hi: 78_300, clippedLo: true, clippedHi: false, containsCurrent: true, touchesBoundary: false });
    expect(edge).toMatchObject({ lo: 78_300, hi: window.hi, clippedLo: false, clippedHi: true, touchesBoundary: true });
    expect(model.rangesOutside).toBe(1);
    expect(model.maxLiquidity).toBe(7e19);
  });

  it("marks zero-liquidity ranges as gaps", () => {
    const ranges = [range(100, 200, 0), range(200, 300, 5)];
    const model = buildProfileModel({ ranges, currentTick: 250, window: { lo: 0, hi: 400 } });
    expect(model.bars.map((bar) => bar.isGap)).toEqual([true, false]);
  });
});

describe("shareWithinWindow", () => {
  it("is tick-weighted and dominated by a full-range position", () => {
    const inner = [range(100, 200, 10), range(200, 300, 10)];
    expect(shareWithinWindow(inner, { lo: 100, hi: 200 })).toBeCloseTo(0.5, 9);
    expect(shareWithinWindow(inner, { lo: 150, hi: 250 })).toBeCloseTo(0.5, 9);
    expect(shareWithinWindow(inner, { lo: 0, hi: 1000 })).toBeCloseTo(1, 9);
    const withFull = [...inner, range(-887_220, 887_220, 10)];
    expect(shareWithinWindow(withFull, { lo: 100, hi: 200 })!).toBeLessThan(0.001);
  });

  it("returns null (never 0) when nothing is measured", () => {
    expect(shareWithinWindow([], { lo: 0, hi: 1 })).toBeNull();
    expect(shareWithinWindow([range(0, 10, 0)], { lo: 0, hi: 10 })).toBeNull();
  });
});

describe("axis labels and units", () => {
  it("relabels the same tick in tick / price / % modes", () => {
    const ctx = { currentTick: 78_244, dec0: 18, dec1: 18 };
    expect(labelFor(78_244, { ...ctx, mode: "tick" })).toBe("78,244");
    expect(labelFor(78_244, { ...ctx, mode: "price" })).toBe("2,499.91");
    expect(labelFor(78_244, { ...ctx, mode: "pct" })).toBe("0.00%");
    expect(labelFor(78_244 + 488, { ...ctx, mode: "pct" })).toBe("+5.0%");
    expect(labelFor(78_244 - 488, { ...ctx, mode: "pct" })).toBe("-4.8%");
  });

  it("price labels fall back to the raw ratio without decimals, and invert on request", () => {
    expect(labelFor(276_324, { mode: "price", currentTick: 276_324, dec0: 6, dec1: 18 })).toBe("0.999997");
    expect(labelFor(276_324, { mode: "price", currentTick: 276_324, dec0: null, dec1: 18 })).toBe("1.000e+12");
    expect(labelFor(78_244, { mode: "price", currentTick: 78_244, dec0: 18, dec1: 18, inverted: true })).toBe("0.000400015");
    expect(labelFor(78_244 + 488, { mode: "pct", currentTick: 78_244, dec0: 18, dec1: 18, inverted: true })).toBe("-4.8%");
  });

  it("names raw units whenever a decimal is missing", () => {
    expect(unitLabel({ mode: "price", sym0: "WETH", sym1: "WXDAI", dec0: 18, dec1: 18 })).toBe("WXDAI per WETH");
    expect(unitLabel({ mode: "price", sym0: "WETH", sym1: "WXDAI", dec0: 18, dec1: 18, inverted: true })).toBe("WETH per WXDAI");
    expect(unitLabel({ mode: "price", sym0: "0x3ab2…6a7b", sym1: "sDAI", dec0: null, dec1: 18 })).toBe("sDAI per 0x3ab2…6a7b (raw units)");
    expect(unitLabel({ mode: "tick", sym0: "a", sym1: "b", dec0: null, dec1: null })).toBe("tick");
    expect(unitLabel({ mode: "pct", sym0: "a", sym1: "b", dec0: null, dec1: null })).toBe("% from current price");
  });
});
