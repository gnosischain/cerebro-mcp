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
}

function buildDataViewTable(opt: EChartsOption): string {
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
        '<th style="padding:6px 10px;text-align:left;border-bottom:2px solid #ddd;font-weight:600">Name</th>' +
        '<th style="padding:6px 10px;text-align:right;border-bottom:2px solid #ddd;font-weight:600">Value</th>' +
        "</tr></thead><tbody>";
      for (const item of pieData) {
        html +=
          "<tr>" +
          `<td style="padding:4px 10px;border-bottom:1px solid #eee">${item.name ?? ""}</td>` +
          `<td style="padding:4px 10px;text-align:right;border-bottom:1px solid #eee;font-family:monospace">${item.value ?? ""}</td>` +
          "</tr>";
      }
      html += "</tbody></table>";
      return html;
    }
    return "<p>No tabular data available</p>";
  }

  let html =
    '<table style="width:100%;border-collapse:collapse;font-size:13px">';
  html += "<thead><tr>";
  html += `<th style="padding:6px 10px;text-align:left;border-bottom:2px solid #ddd;font-weight:600">${xAxis?.name ?? ""}</th>`;
  for (const s of series) {
    html += `<th style="padding:6px 10px;text-align:right;border-bottom:2px solid #ddd;font-weight:600">${s.name ?? ""}</th>`;
  }
  html += "</tr></thead><tbody>";

  for (let i = 0; i < xAxis.data.length; i++) {
    html += "<tr>";
    html += `<td style="padding:4px 10px;border-bottom:1px solid #eee">${xAxis.data[i]}</td>`;
    for (const s of series) {
      const val = s.data?.[i] ?? "";
      html += `<td style="padding:4px 10px;text-align:right;border-bottom:1px solid #eee;font-family:monospace">${val}</td>`;
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

function ChartCardInner({ chartId, spec, title, sql, sourceModel }: Props) {
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
          <div className="chart-card-id">{label}</div>
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
          optionToContent: (o: unknown) =>
            buildDataViewTable(o as EChartsOption),
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
        <div className="chart-card-id">{label}</div>
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
