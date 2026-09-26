// The ECharts toolbox "data view" for ChartCard: an HTML table ECharts injects
// with innerHTML (optionToContent). Every value interpolated into it is escaped:
// series names, axis names, category labels and cells can all come from on-chain
// text (ERC-20 symbols/names are attacker-authored — the GnosisDAO treasury holds
// tokens named "Visit website ... to claim rewards" and homoglyph spam), and
// ChartCard is shared by every mini-app and the report surfaces.
//
// Pure (no React, no ECharts runtime) so it is testable in node.

import type { EChartsOption } from "echarts";

import { escapeHtml } from "../utils/format";

/** Palette for the dataView popup + its injected table. ECharts renders the
 * panel with a WHITE background by default while the table inherits the
 * page's (dark-theme) light text — unreadable. Both must be themed. */
export function dataViewPalette(isDark: boolean) {
  return {
    background: isDark ? "#12161c" : "#ffffff",
    text: isDark ? "#e6e9ee" : "#111418",
    headBorder: isDark ? "rgba(255,255,255,0.28)" : "#ddd",
    rowBorder: isDark ? "rgba(255,255,255,0.12)" : "#eee",
    textarea: isDark ? "#1a1f26" : "#f4f6f8",
    textareaBorder: isDark ? "rgba(255,255,255,0.12)" : "rgba(15,23,42,0.18)",
    button: isDark ? "#67e8f9" : "#0891b2",
    buttonText: isDark ? "#0b0e12" : "#ffffff",
  };
}

export function buildDataViewTable(opt: EChartsOption, isDark: boolean): string {
  const pal = dataViewPalette(isDark);
  const th = (align: string) =>
    `padding:6px 10px;text-align:${align};border-bottom:2px solid ${pal.headBorder};font-weight:600;color:${pal.text}`;
  const td = (extra = "") =>
    `padding:4px 10px;border-bottom:1px solid ${pal.rowBorder};color:${pal.text}${extra}`;
  const xAxisRaw = opt.xAxis;
  const xAxis = (Array.isArray(xAxisRaw) ? xAxisRaw[0] : xAxisRaw) as
    | { data?: unknown[]; name?: string }
    | undefined;
  const series = (opt.series ?? []) as Array<{
    name?: string;
    data?: unknown[];
  }>;

  if (!xAxis?.data || series.length === 0) {
    const pieData = series[0]?.data as
      | Array<{ name?: string; value?: unknown }>
      | undefined;
    if (pieData && pieData.length > 0 && typeof pieData[0] === "object") {
      let html =
        '<table style="width:100%;border-collapse:collapse;font-size:13px">';
      html +=
        "<thead><tr>" +
        `<th style="${th("left")}">Name</th>` +
        `<th style="${th("right")}">Value</th>` +
        "</tr></thead><tbody>";
      for (const item of pieData) {
        html +=
          "<tr>" +
          `<td style="${td()}">${escapeHtml(item?.name)}</td>` +
          `<td style="${td(";text-align:right;font-family:monospace")}">${escapeHtml(item?.value)}</td>` +
          "</tr>";
      }
      html += "</tbody></table>";
      return html;
    }
    return `<p style="color:${pal.text}">No tabular data available</p>`;
  }

  let html =
    '<table style="width:100%;border-collapse:collapse;font-size:13px">';
  html += "<thead><tr>";
  html += `<th style="${th("left")}">${escapeHtml(xAxis.name)}</th>`;
  for (const s of series) {
    html += `<th style="${th("right")}">${escapeHtml(s.name)}</th>`;
  }
  html += "</tr></thead><tbody>";

  for (let i = 0; i < xAxis.data.length; i++) {
    html += "<tr>";
    html += `<td style="${td()}">${escapeHtml(xAxis.data[i])}</td>`;
    for (const s of series) {
      html += `<td style="${td(";text-align:right;font-family:monospace")}">${escapeHtml(s.data?.[i])}</td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  return html;
}
