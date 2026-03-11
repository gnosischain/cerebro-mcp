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
}

/**
 * Build a styled HTML table from an ECharts option for the dataView feature.
 */
function buildDataViewTable(opt: EChartsOption): string {
  const xAxis = opt.xAxis as { data?: string[] } | undefined;
  const series = (opt.series ?? []) as Array<{
    name?: string;
    data?: (number | string | null)[];
  }>;

  if (!xAxis?.data || series.length === 0) {
    // Fallback for pie charts or other formats
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

  // Build header row
  let html =
    '<table style="width:100%;border-collapse:collapse;font-size:13px">';
  html += "<thead><tr>";
  html += `<th style="padding:6px 10px;text-align:left;border-bottom:2px solid #ddd;font-weight:600">${(opt.xAxis as { name?: string })?.name ?? ""}</th>`;
  for (const s of series) {
    html += `<th style="padding:6px 10px;text-align:right;border-bottom:2px solid #ddd;font-weight:600">${s.name ?? ""}</th>`;
  }
  html += "</tr></thead><tbody>";

  // Build data rows
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

function ChartCardInner({ chartId, spec, title, sql }: Props) {
  const { isDark } = useTheme();
  const [showSql, setShowSql] = useState(false);

  if (isNumberDisplay(spec)) {
    return (
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-base)",
          boxShadow: "var(--shadow-sm)",
          overflow: "hidden",
          margin: "1rem 0",
        }}
      >
        {(title || sql) && (
          <div
            style={{
              padding: "0.75rem 1rem 0",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            {title && (
              <span
                style={{
                  fontSize: "0.875rem",
                  fontWeight: 600,
                  color: "var(--text-primary)",
                }}
              >
                {title}
              </span>
            )}
            {sql && (
              <button
                className="chart-sql-toggle"
                onClick={() => setShowSql(!showSql)}
                title="View SQL query"
              >
                {"</>"}
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
        <NumberDisplay spec={spec} />
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
        saveAsImage: {
          title: "Save as image",
          pixelRatio: 2,
        },
        dataView: {
          title: "View data",
          lang: ["Data view", "Close", "Refresh"],
          readOnly: true,
          optionToContent: (o: unknown) =>
            buildDataViewTable(o as EChartsOption),
        },
      },
      iconStyle: {
        borderColor: isDark
          ? "rgba(255,255,255,0.5)"
          : "rgba(0,0,0,0.4)",
      },
    };
    return opt;
  }, [spec, isDark]);

  return (
    <div
      id={`chart-${chartId}`}
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-base)",
        boxShadow: "var(--shadow-sm)",
        overflow: "hidden",
        margin: "1rem 0",
        transition: "border-color 0.2s, box-shadow 0.2s",
      }}
    >
      {(title || sql) && (
        <div
          style={{
            padding: "0.75rem 1rem 0",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          {title && (
            <span
              style={{
                fontSize: "0.875rem",
                fontWeight: 600,
                color: "var(--text-primary)",
              }}
            >
              {title}
            </span>
          )}
          {sql && (
            <button
              className="chart-sql-toggle"
              onClick={() => setShowSql(!showSql)}
              title="View SQL query"
            >
              {"</>"}
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
      <ReactECharts
        option={echartsOption}
        theme={isDark ? "cerebro-dark" : "cerebro-light"}
        style={{ width: "100%", height: "400px" }}
        notMerge
      />
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
