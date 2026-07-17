// Thin adapter: (rows, columns, config, context) → the right option builder →
// report-grade ChartCard. Honest routing: the chart plots exactly what the
// controls say (X, Y, Y2, Series/Color) — no hidden overrides.

import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { ChartCard } from "../../components/ChartCard";
import { NumberDisplay } from "../../components/NumberDisplay";
import { effectiveYFields, type ChartConfig } from "./types";
import {
  aggregateRows,
  buildCartesianOption,
  buildCategoryScatterOption,
  buildHeatmapOption,
  buildMultiAxisOption,
  buildPieOption,
  buildXYScatterOption,
  type MergedDual,
} from "./chartOptions";
import { isNumericColumn } from "./analysis";

interface ChartPanelProps {
  rows: unknown[][];
  columns: string[];
  config: ChartConfig;
  sortAsc: boolean;
  trendline: boolean;
  sql?: string;
  sourceModel?: string;
  /** Merged dual-dataset shape (2 api_* tables) — overrides rows/columns
   * for line/bar so the two tables render on two axes. */
  mergedDual?: MergedDual | null;
}

export function ChartPanel({
  rows,
  columns,
  config,
  sortAsc,
  trendline,
  sql,
  sourceModel,
  mergedDual,
}: ChartPanelProps) {
  const option: EChartsOption = useMemo(() => {
    if (rows.length === 0 && !mergedDual) return {};

    switch (config.chartType) {
      case "scatter": {
        // True XY scatter when both axes are numeric (metric-vs-metric),
        // with optional color encoding for a third value.
        const xi = columns.indexOf(config.xField);
        const numericX = xi >= 0 && isNumericColumn(rows, xi);
        if (numericX && config.yField) {
          return buildXYScatterOption(
            rows,
            columns,
            config.xField,
            config.yField,
            trendline,
            config.colorBy ?? "",
          );
        }
        return buildCategoryScatterOption(rows, columns, config, sortAsc);
      }
      case "heatmap":
        return buildHeatmapOption(rows, columns, config, sortAsc);
      case "pie":
        return buildPieOption(rows, columns, config);
      case "line":
      case "bar": {
        // Dual api_* tables → merged two-axis chart (their own titles).
        if (mergedDual) {
          return buildMultiAxisOption(
            mergedDual.rows,
            mergedDual.columns,
            mergedDual.xField,
            mergedDual.metricColumns,
            config.chartType,
            sortAsc,
          );
        }
        // Several value columns (multi-Y aggregate / N-model join / explicit
        // Y2) take precedence over series grouping: [0] left, [1] right,
        // rest left.
        const yFields = effectiveYFields(config);
        if (yFields.length > 1) {
          return buildMultiAxisOption(
            rows,
            columns,
            config.xField,
            yFields,
            config.chartType,
            sortAsc,
          );
        }
        return buildCartesianOption(rows, columns, config, sortAsc);
      }
      default:
        return {};
    }
  }, [rows, columns, config, sortAsc, trendline, mergedDual]);

  if (config.chartType === "numberDisplay") {
    const { y } = aggregateRows(rows, columns, config);
    const total = y.reduce((a, b) => a + b, 0);
    return (
      <div className="mlab-chart-host">
        <div className="chart-card">
          <NumberDisplay
            spec={{
              type: "numberDisplay",
              title: `${config.aggregation}(${config.yField || "rows"})`,
              value: total,
            }}
          />
        </div>
      </div>
    );
  }

  const empty =
    !option || Object.keys(option).length === 0 || !(option as { series?: unknown }).series;
  if (empty) {
    return (
      <div className="mlab-chart-empty">
        {config.chartType === "heatmap"
          ? "Heatmap needs a Series column distinct from X — pick one in the controls."
          : "Nothing to plot — check the X / Y field selection."}
      </div>
    );
  }

  return (
    <div className="mlab-chart-host">
      <ChartCard
        chartId="metric-lab"
        spec={{ ...option, _cerebro_height: "440px" } as EChartsOption}
        hideId
        sql={sql}
        sourceModel={sourceModel}
      />
    </div>
  );
}
