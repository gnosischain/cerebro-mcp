// Theme contract tests: the mini-app themes fix LABELS ONLY (Inter 11 400,
// token-mirrored colors) while keeping the report series palettes byte-equal,
// and the report themes themselves stay untouched (reports must not regress).
// Run with `npm test` (vitest).

import { describe, expect, it } from "vitest";
import { ECHARTS_DARK } from "../echarts-dark";
import { ECHARTS_LIGHT } from "../echarts-light";
import { ECHARTS_DARK_MINI } from "../echarts-dark-mini";
import { ECHARTS_LIGHT_MINI } from "../echarts-light-mini";

const MINI_DARK_LABEL = { color: "#aab3be", fontSize: 11, fontWeight: 400 };
const MINI_LIGHT_LABEL = { color: "#5b6473", fontSize: 11, fontWeight: 400 };

describe("mini dark theme labels", () => {
  it.each([
    ["categoryAxis", ECHARTS_DARK_MINI.categoryAxis.axisLabel],
    ["valueAxis", ECHARTS_DARK_MINI.valueAxis.axisLabel],
  ])("%s axisLabel is --text-secondary Inter 11 400", (_axis, axisLabel) => {
    expect(axisLabel).toMatchObject(MINI_DARK_LABEL);
    expect(axisLabel.fontFamily.startsWith("Inter")).toBe(true);
  });

  it("legend and body text use the token-mirrored muted color", () => {
    expect(ECHARTS_DARK_MINI.legend.textStyle.color).toBe("#aab3be");
    expect(ECHARTS_DARK_MINI.textStyle.color).toBe("#aab3be");
    expect(ECHARTS_DARK_MINI.textStyle.fontFamily.startsWith("Inter")).toBe(true);
  });
});

describe("mini light theme labels", () => {
  it.each([
    ["categoryAxis", ECHARTS_LIGHT_MINI.categoryAxis.axisLabel],
    ["valueAxis", ECHARTS_LIGHT_MINI.valueAxis.axisLabel],
  ])("%s axisLabel is --text-muted Inter 11 400", (_axis, axisLabel) => {
    expect(axisLabel).toMatchObject(MINI_LIGHT_LABEL);
    expect(axisLabel.fontFamily.startsWith("Inter")).toBe(true);
  });
});

describe("mini palettes keep the terminal identity", () => {
  // Reports moved to the editorial "research desk" palette (teal/amber lead,
  // validated with the dataviz six-checks validator); mini-apps deliberately
  // keep the lime/violet terminal palette their designs were approved on.
  it("dark-mini series palette is the terminal lime/violet set", () => {
    expect(ECHARTS_DARK_MINI.color[0]).toBe("#B4F03C");
    expect(ECHARTS_DARK_MINI.color[1]).toBe("#7B61FF");
  });

  it("mini palettes no longer track the report palettes", () => {
    expect(ECHARTS_DARK_MINI.color).not.toEqual(ECHARTS_DARK.color);
    expect(ECHARTS_LIGHT_MINI.color).not.toEqual(ECHARTS_LIGHT.color);
  });
});

describe("report themes: editorial research-desk contract", () => {
  it.each([
    ["categoryAxis", ECHARTS_DARK.categoryAxis.axisLabel],
    ["valueAxis", ECHARTS_DARK.valueAxis.axisLabel],
  ])("dark %s axisLabel keeps the indigo-tuned mono label", (_axis, axisLabel) => {
    expect(axisLabel).toMatchObject({ color: "#8b84b5", fontSize: 11 });
    expect(axisLabel.fontFamily).toContain("JetBrains Mono");
  });

  it("lead slots are teal then amber in both modes (validated order)", () => {
    expect(ECHARTS_DARK.color.slice(0, 2)).toEqual(["#21A87F", "#B5891F"]);
    expect(ECHARTS_LIGHT.color.slice(0, 2)).toEqual(["#0E8C6E", "#C0862A"]);
    expect(ECHARTS_DARK.color).toHaveLength(8);
    expect(ECHARTS_LIGHT.color).toHaveLength(8);
  });

  it("lines draw without per-point symbols", () => {
    expect(ECHARTS_DARK.line).toMatchObject({ symbol: "none" });
    expect(ECHARTS_LIGHT.line).toMatchObject({ symbol: "none" });
  });
});
