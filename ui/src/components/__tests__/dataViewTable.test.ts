// The ChartCard toolbox "data view" is an HTML string ECharts injects with
// innerHTML. Series names, axis names, category labels and pie slice names can
// all be on-chain text (ERC-20 symbols/names are attacker-authored), so every
// interpolated value must arrive escaped — in every mini-app, since ChartCard is
// shared by all of them and by the report surfaces.

import type { EChartsOption } from "echarts";
import { describe, expect, it } from "vitest";

import { buildDataViewTable } from "../dataViewTable";
import { escapeHtml } from "../../utils/format";
import { escapeHtml as sharedEscapeHtml } from "../../mini-apps/shared/chartOptions";

const PAYLOAD = "<img src=x onerror=alert(1)>";
const ESCAPED = "&lt;img src=x onerror=alert(1)&gt;";

function assertInert(html: string): void {
  expect(html).not.toContain("<img");
  expect(html).not.toMatch(/<[a-z]+[^>]*\sonerror=/i);
  expect(html).toContain(ESCAPED);
}

describe("buildDataViewTable escapes every interpolated value", () => {
  it("escapes a series name", () => {
    const opt = {
      xAxis: { type: "category", data: ["2026-09"] },
      series: [{ type: "line", name: PAYLOAD, data: [1] }],
    } as EChartsOption;
    assertInert(buildDataViewTable(opt, true));
  });

  it("escapes a category label and the axis name", () => {
    const opt = {
      xAxis: { type: "category", name: PAYLOAD, data: [PAYLOAD, "ok"] },
      series: [{ type: "bar", name: "GNO", data: [1, 2] }],
    } as EChartsOption;
    const html = buildDataViewTable(opt, false);
    assertInert(html);
    // header cell (axis name) + one body row (category) both carry the payload
    expect(html.split(ESCAPED).length - 1).toBe(2);
  });

  it("escapes a pie slice name and a string cell value", () => {
    const opt = {
      series: [{ type: "pie", data: [{ name: PAYLOAD, value: 3 }, { name: "SAFE", value: PAYLOAD }] }],
    } as EChartsOption;
    const html = buildDataViewTable(opt, true);
    assertInert(html);
    expect(html.split(ESCAPED).length - 1).toBe(2);
  });

  it("escapes a string cell in a cartesian series and keeps numbers readable", () => {
    const opt = {
      xAxis: { type: "category", data: ["a", "b"] },
      series: [{ type: "bar", name: "USDC", data: [1234.5, PAYLOAD] }],
    } as EChartsOption;
    const html = buildDataViewTable(opt, true);
    assertInert(html);
    expect(html).toContain(">1234.5</td>");
  });

  it("renders missing values as empty cells, not the text 'undefined'", () => {
    const opt = {
      xAxis: { type: "category", data: ["a", undefined] },
      series: [{ type: "bar", name: "X", data: [null] }],
    } as unknown as EChartsOption;
    expect(buildDataViewTable(opt, true)).not.toContain("undefined");
  });
});

describe("escapeHtml has one implementation", () => {
  it("is the same function from utils and from the mini-app shared module", () => {
    expect(sharedEscapeHtml).toBe(escapeHtml);
    expect(escapeHtml(`<a href="x">'&'</a>`)).toBe(
      "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;",
    );
  });
});
