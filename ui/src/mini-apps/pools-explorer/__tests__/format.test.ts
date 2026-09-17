import { describe, expect, it } from "vitest";

import {
  classLabel, fmtAmount, fmtDate, fmtFee, fmtInt, fmtLiquidity, fmtPct, fmtPrice, fmtPriceFor, fmtRaw,
  fmtSignedPct, fmtTick, fmtTime, tokenLabel,
} from "../model/format";

describe("fmtLiquidity", () => {
  it("uses SI suffixes up to 1e24 and exponential beyond, dash for missing", () => {
    expect(fmtLiquidity(0)).toBe("0");
    expect(fmtLiquidity(950)).toBe("950");
    expect(fmtLiquidity(9.0e12)).toBe("9.00T");
    expect(fmtLiquidity(7.4e19)).toBe("74.0E");
    expect(fmtLiquidity(1.2e24)).toBe("1.20Y");
    expect(fmtLiquidity(3e27)).toBe("3.00e+27");
    expect(fmtLiquidity(null)).toBe("—");
    expect(fmtLiquidity("")).toBe("—");
    expect(fmtLiquidity("abc")).toBe("—");
    expect(fmtLiquidity(true)).toBe("—");
  });
});

describe("prices and amounts", () => {
  it("fmtPrice spans decades sanely", () => {
    expect(fmtPrice(2500.244)).toBe("2,500.24");
    expect(fmtPrice(1.0007e12)).toBe("1.001e+12");
    expect(fmtPrice(0.00004)).toBe("4.000e-5");
    expect(fmtPrice(null)).toBe("—");
  });

  it("fmtPriceFor prefers the adjusted price only when both decimals are known", () => {
    expect(fmtPriceFor(1e12, 1.0007, 6, 18)).toEqual({ text: "1.0007", raw: false });
    expect(fmtPriceFor(1e12, 1.0007, null, 18)).toEqual({ text: "1.000e+12", raw: true });
    expect(fmtPriceFor(0.85, null, null, 18)).toEqual({ text: "0.85", raw: true });
    expect(fmtPriceFor(null, null, null, null)).toEqual({ text: "—", raw: true });
  });

  it("fmtAmount falls back to flagged raw units without decimals", () => {
    expect(fmtAmount("412500000000000000000", 18, 412.5)).toEqual({ text: "412.5", rawUnits: false });
    expect(fmtAmount("412500000000000000000", 18)).toEqual({ text: "412.5", rawUnits: false });
    expect(fmtAmount("8100000000000000000000", null, null)).toEqual({ text: "8.100e+21", rawUnits: true });
    expect(fmtAmount(null, null, null)).toEqual({ text: "—", rawUnits: true });
    expect(fmtRaw("12345")).toBe("12,345");
    expect(fmtRaw("not a number")).toBe("not a number");
  });
});

describe("fees, labels, misc", () => {
  it("fmtFee converts pips to percent and marks Algebra fees dynamic", () => {
    expect(fmtFee(3000)).toBe("0.30%");
    expect(fmtFee(500)).toBe("0.05%");
    expect(fmtFee(100)).toBe("0.01%");
    expect(fmtFee(10000)).toBe("1.00%");
    expect(fmtFee(137, "swapr_v3_algebra")).toBe("0.01% dyn");
    expect(fmtFee(null, "swapr_v3_algebra")).toBe("dyn");
    expect(fmtFee(null, "balancer_v2")).toBe("—");
  });

  it("tokenLabel sanitizes the symbol and falls back to a short address", () => {
    expect(tokenLabel("WETH", `0x${"ab".repeat(20)}`)).toBe("WETH");
    expect(tokenLabel(null, `0x${"ab".repeat(20)}`)).toBe("0xabab…abab");
    expect(tokenLabel("$ USDCGift.com <- Visit to claim bonus", `0x${"ab".repeat(20)}`)).not.toContain("Visit");
    expect(tokenLabel("​", "")).toBe("—");
  });

  it("small formatters never print a fake 0 for a missing value", () => {
    expect(fmtInt(null)).toBe("—");
    expect(fmtInt(4022)).toBe("4,022");
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(0.0147)).toBe("1.5%");
    expect(fmtTick(78244.4)).toBe("78,244");
    expect(fmtSignedPct(0.004)).toBe("0.0%");
    expect(fmtSignedPct(5)).toBe("+5.0%");
    expect(fmtSignedPct(-4.76)).toBe("-4.8%");
    expect(fmtDate("2026-09-16T01:00:00Z")).toBe("2026-09-16");
    expect(fmtDate(null)).toBe("—");
    expect(fmtTime("2026-09-17T01:12:44Z")).toBe("2026-09-17 01:12:44");
    expect(classLabel("swapr_v3_algebra")).toBe("Swapr v3 (Algebra)");
    expect(classLabel("weird")).toBe("weird");
  });
});
