import { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

import "../lib/echarts-setup";
import { getWatermarkGraphic } from "../assets/watermark";
import { useTheme } from "../hooks/useTheme";
import { isNumberDisplay, type ChartSpec } from "../types";
import { NumberDisplay } from "./NumberDisplay";
import { ErrorBoundary } from "./ErrorBoundary";

interface Props {
  chartId: string;
  spec: ChartSpec;
  title?: string;
  sql?: string;
  sourceModel?: string;
  /** Hide the CHART_NN id badge (mini-app hosts have no chart numbering). */
  hideId?: boolean;
}

/** Palette for the dataView popup + its injected table. ECharts renders the
 * panel with a WHITE background by default while the table inherits the
 * page's (dark-theme) light text — unreadable. Both must be themed. */
function dataViewPalette(isDark: boolean) {
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

function buildDataViewTable(opt: EChartsOption, isDark: boolean): string {
  const pal = dataViewPalette(isDark);
  const th = (align: string) =>
    `padding:6px 10px;text-align:${align};border-bottom:2px solid ${pal.headBorder};font-weight:600;color:${pal.text}`;
  const td = (extra = "") =>
    `padding:4px 10px;border-bottom:1px solid ${pal.rowBorder};color:${pal.text}${extra}`;
  const xAxisRaw = opt.xAxis;
  const xAxis = (Array.isArray(xAxisRaw) ? xAxisRaw[0] : xAxisRaw) as
    | { data?: string[]; name?: string }
    | undefined;
  const series = (opt.series ?? []) as Array<{
    name?: string;
    data?: (number | string | null)[];
  }>;

  if (!xAxis?.data || series.length === 0) {
    const pieData = series[0]?.data as
      | Array<{ name?: string; value?: number }>
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
          `<td style="${td()}">${item.name ?? ""}</td>` +
          `<td style="${td(";text-align:right;font-family:monospace")}">${item.value ?? ""}</td>` +
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
  html += `<th style="${th("left")}">${xAxis?.name ?? ""}</th>`;
  for (const s of series) {
    html += `<th style="${th("right")}">${s.name ?? ""}</th>`;
  }
  html += "</tr></thead><tbody>";

  for (let i = 0; i < xAxis.data.length; i++) {
    html += "<tr>";
    html += `<td style="${td()}">${xAxis.data[i]}</td>`;
    for (const s of series) {
      const val = s.data?.[i] ?? "";
      html += `<td style="${td(";text-align:right;font-family:monospace")}">${val}</td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  return html;
}

function chartLabel(chartId: string): string {
  const m = chartId.match(/(\d+)$/);
  if (m) {
    return `CHART_${m[1].padStart(2, "0")}`;
  }
  return chartId.replace(/^chart[_-]?/i, "CHART_").toUpperCase();
}

function ChartCardInner({ chartId, spec, title, sql, sourceModel, hideId }: Props) {
  const { isDark } = useTheme();
  const [showSql, setShowSql] = useState(false);

  const label = chartLabel(chartId);
  const srcLabel = sourceModel ?? chartId;
  const showFoot = Boolean(sourceModel || sql);

  if (isNumberDisplay(spec)) {
    // KPI cards: drop the dbt source label — the SQL toggle alone is enough.
    return (
      <div id={`chart-${chartId}`} className="chart-card">
        <div className="chart-card-head">
          <div className="chart-card-title">{title || spec.title || ""}</div>
          {!hideId && <div className="chart-card-id">{label}</div>}
        </div>
        <NumberDisplay spec={spec} />
        {sql && (
          <div className="chart-card-foot chart-card-foot--kpi">
            <span className="spacer" />
            <button
              className="chart-sql-toggle"
              onClick={() => setShowSql(!showSql)}
            >
              {showSql ? "Hide SQL" : "View SQL"}
            </button>
          </div>
        )}
        {showSql && sql && (
          <div className="chart-sql-block">
            <pre>
              <code>{sql}</code>
            </pre>
          </div>
        )}
      </div>
    );
  }

  const echartsOption = useMemo(() => {
    const opt = { ...(spec as EChartsOption) };
    opt.graphic = getWatermarkGraphic(isDark);
    opt.animation = true;
    opt.animationDuration = 1000;
    opt.animationEasing = "cubicOut";
    opt.toolbox = {
      show: true,
      right: 16,
      top: 8,
      feature: {
        saveAsImage: { title: "Save as image", pixelRatio: 2 },
        dataView: {
          title: "View data",
          lang: ["Data view", "Close", "Refresh"],
          readOnly: true,
          // The popup panel does NOT inherit the ECharts theme — leave any
          // of these unset and dark mode gets a white panel with the page's
          // light text (unreadable).
          backgroundColor: dataViewPalette(isDark).background,
          textareaColor: dataViewPalette(isDark).textarea,
          textareaBorderColor: dataViewPalette(isDark).textareaBorder,
          textColor: dataViewPalette(isDark).text,
          buttonColor: dataViewPalette(isDark).button,
          buttonTextColor: dataViewPalette(isDark).buttonText,
          optionToContent: (o: unknown) =>
            buildDataViewTable(o as EChartsOption, isDark),
        },
      },
      iconStyle: {
        borderColor: isDark ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.4)",
      },
    };
    return opt;
  }, [spec, isDark]);

  const chartHeight =
    ((spec as Record<string, unknown>)?._cerebro_height as string) || "350px";

  return (
    <div id={`chart-${chartId}`} className="chart-card">
      <div className="chart-card-head">
        <div className="chart-card-title">{title || ""}</div>
        {!hideId && <div className="chart-card-id">{label}</div>}
      </div>
      <div className="chart-card-body">
        <ReactECharts
          option={echartsOption}
          theme={isDark ? "cerebro-dark" : "cerebro-light"}
          style={{ width: "100%", height: chartHeight }}
          notMerge
        />
      </div>
      {showFoot && (
        <div className="chart-card-foot">
          <span className="src">{srcLabel}</span>
          {sql && (
            <button
              className="chart-sql-toggle"
              onClick={() => setShowSql(!showSql)}
            >
              {showSql ? "Hide SQL" : "View SQL"}
            </button>
          )}
        </div>
      )}
      {showSql && sql && (
        <div className="chart-sql-block">
          <pre>
            <code>{sql}</code>
          </pre>
        </div>
      )}
    </div>
  );
}

export function ChartCard(props: Props) {
  return (
    <ErrorBoundary fallbackLabel="Chart">
      <ChartCardInner {...props} />
    </ErrorBoundary>
  );
}
