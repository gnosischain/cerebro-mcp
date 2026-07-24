// ECharts option builders for the Governance Explorer. Convention (frozen
// across all cerebro chart surfaces): dataZoom is INSIDE-only — wheel/pinch,
// never a slider bar. Theming comes from ChartCard (cerebro-dark/light).

import type { EChartsOption } from "echarts";
import type { ActivityRow, ConcentrationRow } from "../types";

export const insideZoom = [{ type: "inside" as const, start: 0, end: 100 }];

// Chart labels use the body font (Inter), NOT JetBrains Mono: 10px mono on the
// near-black mini surface halates into a "bold labels" smear in dark mode. This
// matches the axis/legend font the mini ECharts themes already enforce
// (themes/echarts-dark-mini.ts) — the per-spec font here must not fight it.
const LABEL_FONT = "Inter, system-ui, -apple-system, sans-serif";

export interface ActivitySeriesDef {
  field: string;
  label: string;
  type: "bar" | "line";
  /** 1 = plot on a second value axis (dominant counts like votes). */
  yAxisIndex?: 0 | 1;
}

/** Time-bucketed activity combo (bars + lines). The bucket unit comes from
 * the rows' `bucket_unit` constant column, not from view state. */
export function activityComboOption(
  rows: ActivityRow[],
  series: ActivitySeriesDef[],
  secondAxisName = "",
): EChartsOption {
  const buckets = rows.map((row) => row.bucket);
  const unit = rows[0]?.bucket_unit ?? "day";
  const hasSecond = series.some((def) => def.yAxisIndex === 1);
  return {
    tooltip: { trigger: "axis" },
    legend: { show: series.length > 1 },
    grid: { left: 60, right: hasSecond ? 64 : 24, top: 42, bottom: 48 },
    xAxis: {
      type: "category",
      data: buckets,
      name: `per ${unit}`,
      nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
    },
    yAxis: hasSecond
      ? [{ type: "value" }, { type: "value", name: secondAxisName, splitLine: { show: false } }]
      : { type: "value" },
    dataZoom: insideZoom,
    series: series.map((def) => ({
      name: def.label,
      type: def.type,
      yAxisIndex: def.yAxisIndex ?? 0,
      ...(def.type === "bar" ? { barMaxWidth: 22 } : { showSymbol: false, smooth: true }),
      data: rows.map((row) => Number(row[def.field] ?? 0)),
    })),
  } as EChartsOption;
}

/** Category share donut (proposal types, …). */
export function donutOption(pairs: Array<{ name: string; value: number }>): EChartsOption {
  return {
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    series: [{
      type: "pie",
      radius: ["44%", "70%"],
      center: ["50%", "44%"],
      label: { fontFamily: LABEL_FONT, fontSize: 11 },
      data: pairs,
    }],
  } as EChartsOption;
}

/** Vertical bars over a categorical axis (quorum attainment, …). */
export function categoryCountOption(
  rows: Array<{ name: string; value: number }>,
  valueLabel: string,
): EChartsOption {
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 58, right: 24, top: 32, bottom: 42 },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.name),
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 11 },
    },
    yAxis: { type: "value" },
    series: [{ name: valueLabel, type: "bar", barMaxWidth: 44, data: rows.map((row) => row.value) }],
  } as EChartsOption;
}

/** Horizontal bars (forum category activity — long category names). */
export function horizontalBarOption(
  rows: Array<{ name: string; value: number }>,
  valueLabel: string,
): EChartsOption {
  const sorted = [...rows].sort((a, b) => a.value - b.value);
  const height = Math.max(300, Math.min(680, 90 + sorted.length * 26));
  return {
    _cerebro_height: `${height}px`,
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 170, right: 40, top: 20, bottom: 34 },
    xAxis: { type: "value" },
    yAxis: {
      type: "category",
      data: sorted.map((row) => row.name),
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, width: 150, overflow: "truncate" },
    },
    series: [{ name: valueLabel, type: "bar", barMaxWidth: 16, data: sorted.map((row) => row.value) }],
  } as EChartsOption;
}

/** Top-N voting-power / vote-count concentration tiers as percent bars. */
export function concentrationOption(
  rows: Array<Pick<ConcentrationRow, "tier" | "share">>,
  shareLabel: string,
): EChartsOption {
  const sorted = [...rows].sort((a, b) => a.tier - b.tier);
  return {
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown) => `${(Number(value) * 100).toFixed(1)}%`,
    },
    grid: { left: 58, right: 24, top: 32, bottom: 42 },
    xAxis: {
      type: "category",
      data: sorted.map((row) => `Top ${row.tier}`),
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 11 },
    },
    yAxis: {
      type: "value",
      max: 1,
      axisLabel: { formatter: (value: number) => `${Math.round(value * 100)}%` },
    },
    series: [{
      name: shareLabel,
      type: "bar",
      barMaxWidth: 44,
      data: sorted.map((row) => row.share ?? 0),
      label: {
        show: true,
        position: "top",
        fontFamily: LABEL_FONT,
        fontSize: 10,
        formatter: (params: unknown) =>
          `${(Number((params as { value?: number }).value ?? 0) * 100).toFixed(1)}%`,
      },
    }],
  } as EChartsOption;
}
