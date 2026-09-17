// ECharts option builders for the Pool Liquidity Explorer. Frozen conventions
// (shared across every cerebro chart surface): dataZoom is INSIDE-only —
// wheel/pinch, never a slider bar; labels use LABEL_FONT; tooltips are HTML so
// every string that reaches a formatter is escaped (symbols are untrusted);
// only series types registered in ui/src/lib/echarts-setup.ts appear here
// (line, bar, scatter, heatmap, custom + the MarkLine component).
//
// The tick axis is a VALUE axis: tick is a log-price coordinate, and the
// label modes (tick / price / % from current) relabel it without moving the
// bars. Full-range positions are a band, never a bar, and never set the axis.

import type { EChartsOption } from "echarts";

import { LABEL_FONT, escapeHtml, insideZoom, stackedSeriesOption } from "../../shared/chartOptions";
import { shortAddr } from "../../../utils/format";
import {
  classLabel, fmtInt, fmtLiquidity, fmtPct, fmtPrice, fmtRaw, fmtTick, fmtUnits, tokenLabel,
} from "./format";
import { labelFor, priceAtTick, type AxisLabelContext, type ProfileModel } from "./liquidityProfile";
import { liquidityFill, liquidityInk, liquidityRamp } from "./liquidityScale";
import type { ProfileHeatmapModel } from "./profileHeatmap";
import type {
  CalendarPoint, ClassFeeView, ConcentrationSummaryView, FeePoint, LiveTrendPoint,
  MetadataGapView, ProbeCoverageView, RangeWidthView, ReserveSeries, StatePoint, TickPoint,
  TokenPoolView,
} from "./parseRows";
import { FEE_BANDS } from "../types";

export { insideZoom };

const AXIS_LABEL = { fontFamily: LABEL_FONT, fontSize: 10 };
const NAME_STYLE = { fontFamily: LABEL_FONT, fontSize: 10 };

interface CustomApi {
  value: (index: number) => number;
  coord: (values: number[]) => number[];
}

// ---------------------------------------------------------------------------
// Liquidity profile (snapshot)
// ---------------------------------------------------------------------------

export interface LiquidityProfileOptionArgs {
  model: ProfileModel;
  labelCtx: AxisLabelContext;
  /** Axis unit name (from liquidityProfile.unitLabel). */
  unit: string;
  yLog: boolean;
  isDark: boolean;
}

export function liquidityProfileOption(args: LiquidityProfileOptionArgs): EChartsOption {
  const { model, labelCtx, isDark } = args;
  const ink = liquidityInk(isDark);
  const positives = model.bars.map((bar) => bar.liquidity).filter((value) => value > 0);
  if (model.fullRangeBand) positives.push(model.fullRangeBand.liquidity);
  const maxL = Math.max(model.maxLiquidity, model.fullRangeBand?.liquidity ?? 0, 1);
  const minPositive = positives.length ? Math.min(...positives) : 1;
  const yLog = args.yLog && positives.length > 0;
  const yBase = yLog ? minPositive / 2 : 0;
  const yMax = maxL * 1.08;
  const barData = model.bars.map((bar, index) => [bar.lo, bar.hi, Math.max(bar.liquidity, 0), index]);

  const priceText = (tick: number) => {
    const price = priceAtTick(tick, labelCtx);
    return `${fmtPrice(price.value)}${price.raw ? " (raw)" : ""}`;
  };

  const rangeTooltip = (index: number): string => {
    const bar = model.bars[index];
    if (!bar) return "";
    const lines = [
      `<strong>Ticks ${escapeHtml(fmtTick(bar.tickLower))} → ${escapeHtml(fmtTick(bar.tickUpper))}</strong>`,
      `Price ${escapeHtml(priceText(bar.tickLower))} → ${escapeHtml(priceText(bar.tickUpper))}`,
      `Active L ${escapeHtml(fmtLiquidity(bar.liquidity))}`,
    ];
    if (bar.containsCurrent) lines.push("Contains the current tick");
    if (bar.isGap) lines.push("Gap — no active liquidity");
    if (bar.touchesBoundary) lines.push("Extends to the full-range boundary");
    else if (bar.clippedLo || bar.clippedHi) lines.push("Clipped by the zoom window");
    return lines.join("<br/>");
  };

  const series: Array<Record<string, unknown>> = [];
  if (model.fullRangeBand) {
    const band = model.fullRangeBand;
    series.push({
      type: "custom",
      name: "full-range",
      data: [[model.window.lo, model.window.hi, band.liquidity]],
      encode: { x: [0, 1], y: [2] },
      clip: true,
      animation: false,
      z: 1,
      renderItem: (_params: unknown, api: CustomApi) => {
        const [x0, y0] = api.coord([api.value(0), Math.max(api.value(2), yBase)]);
        const [x1, y1] = api.coord([api.value(1), yBase]);
        return {
          type: "rect",
          shape: { x: x0, y: y0, width: Math.max(1, x1 - x0), height: Math.max(1, y1 - y0) },
          style: { fill: ink.band, stroke: ink.bandStroke, lineDash: [4, 3], lineWidth: 1 },
        };
      },
      tooltip: {
        formatter: () =>
          `<strong>Full-range position${band.count > 1 ? "s" : ""}</strong><br/>`
          + `Active L ${escapeHtml(fmtLiquidity(band.liquidity))} across the whole tick axis`,
      },
    });
  }
  series.push({
    type: "custom",
    name: "ranges",
    data: barData,
    encode: { x: [0, 1], y: [2] },
    clip: true,
    animation: false,
    z: 2,
    renderItem: (_params: unknown, api: CustomApi) => {
      const index = api.value(3);
      const bar = model.bars[index];
      const liquidity = api.value(2);
      const [x0, y0] = api.coord([api.value(0), Math.max(liquidity, yBase)]);
      const [x1, y1] = api.coord([api.value(1), yBase]);
      const width = Math.max(1, x1 - x0);
      const height = bar?.isGap ? Math.max(1, y1 - y0) : Math.max(1, y1 - y0);
      const children: unknown[] = [{
        type: "rect",
        shape: { x: x0, y: bar?.isGap ? y1 - 2 : y0, width, height: bar?.isGap ? 2 : height },
        style: bar?.isGap
          ? { fill: ink.gap }
          : { fill: bar?.containsCurrent ? ink.barCurrent : ink.bar, opacity: 0.92 },
      }];
      if (bar?.clippedLo) {
        children.push({
          type: "line",
          silent: true,
          shape: { x1: x0, y1: y0, x2: x0, y2: y1 },
          style: { stroke: ink.currentLine, lineDash: [3, 3], lineWidth: 1, opacity: 0.6 },
        });
      }
      if (bar?.clippedHi) {
        children.push({
          type: "line",
          silent: true,
          shape: { x1: x1, y1: y0, x2: x1, y2: y1 },
          style: { stroke: ink.currentLine, lineDash: [3, 3], lineWidth: 1, opacity: 0.6 },
        });
      }
      return { type: "group", children };
    },
    ...(model.currentX !== null
      ? {
          markLine: {
            symbol: "none",
            animation: false,
            silent: true,
            lineStyle: { color: ink.currentLine, type: "dashed", width: 1 },
            label: {
              formatter: "current",
              position: "insideEndTop",
              color: ink.currentLine,
              fontFamily: LABEL_FONT,
              fontSize: 10,
            },
            data: [{ xAxis: model.currentX }],
          },
        }
      : {}),
  });

  return {
    _cerebro_height: "360px",
    animation: false,
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: (params: unknown) => {
        const item = params as { seriesName?: string; value?: number[] };
        if (item.seriesName === "ranges" && item.value) return rangeTooltip(item.value[3]);
        if (item.seriesName === "full-range" && model.fullRangeBand) {
          return `<strong>Full-range position</strong><br/>Active L ${escapeHtml(fmtLiquidity(model.fullRangeBand.liquidity))}`;
        }
        return "";
      },
    },
    grid: { left: 74, right: 24, top: 28, bottom: 58 },
    xAxis: {
      type: "value",
      min: model.window.lo,
      max: model.window.hi,
      name: args.unit,
      nameLocation: "middle",
      nameGap: 32,
      nameTextStyle: NAME_STYLE,
      axisLabel: { ...AXIS_LABEL, hideOverlap: true, formatter: (value: number) => labelFor(value, labelCtx) },
      splitLine: { show: false },
    },
    yAxis: yLog
      ? {
          type: "log",
          min: yBase,
          max: yMax,
          name: "active liquidity (L, log)",
          nameTextStyle: NAME_STYLE,
          axisLabel: { ...AXIS_LABEL, formatter: (value: number) => fmtLiquidity(value) },
        }
      : {
          type: "value",
          min: 0,
          max: yMax,
          name: "active liquidity (L)",
          nameTextStyle: NAME_STYLE,
          axisLabel: { ...AXIS_LABEL, formatter: (value: number) => fmtLiquidity(value) },
        },
    dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none" }],
    series,
  } as EChartsOption;
}

// ---------------------------------------------------------------------------
// Liquidity profile over time (heatmap)
// ---------------------------------------------------------------------------

export interface ProfileHeatmapOptionArgs {
  model: ProfileHeatmapModel;
  isDark: boolean;
  unit: string;
}

/** Heatmap of active liquidity by (date, price level), drawn as a CUSTOM
 * series of rects (the CoW footprint precedent): ECharts' `heatmap` series
 * refuses to render without a visualMap, and a visualMap colours one ramp it
 * owns — here colour is a pure function of the shared magnitude scale and the
 * HTML LiquidityLegend carries the labelled classes. */
export function profileHeatmapOption(args: ProfileHeatmapOptionArgs): EChartsOption {
  const { model, isDark } = args;
  const ramp = liquidityRamp(isDark);
  const ink = liquidityInk(isDark);
  const cellFill = (value: number) => liquidityFill(model.scale, ramp, value);
  return {
    _cerebro_height: "480px",
    animation: false,
    tooltip: {
      confine: true,
      formatter: (params: unknown) => {
        const value = (params as { value?: [number, number, number] }).value;
        if (!value) return "";
        return `${escapeHtml(model.xLabels[value[0]] ?? "")}<br/>`
          + `${escapeHtml(model.yLabels[value[1]] ?? "")} ${escapeHtml(args.unit)}<br/>`
          + `Active L ${escapeHtml(fmtLiquidity(value[2]))}`;
      },
    },
    grid: { left: 92, right: 24, top: 40, bottom: 74 },
    xAxis: {
      type: "category",
      data: model.xLabels,
      name: "publication date",
      nameLocation: "middle",
      nameGap: 56,
      nameTextStyle: NAME_STYLE,
      axisLabel: { ...AXIS_LABEL, rotate: 40, hideOverlap: true },
    },
    yAxis: {
      type: "category",
      data: model.yLabels,
      name: args.unit,
      nameGap: 14,
      nameTextStyle: NAME_STYLE,
      axisLabel: { ...AXIS_LABEL, hideOverlap: true },
    },
    dataZoom: [
      { type: "inside", xAxisIndex: [0], filterMode: "weakFilter" },
      { type: "inside", yAxisIndex: [0], filterMode: "weakFilter" },
    ],
    series: [
      {
        type: "custom",
        name: "liquidity",
        data: model.cells,
        encode: { x: 0, y: 1, tooltip: [2] },
        clip: true,
        progressive: 0,
        animation: false,
        renderItem: (_params: unknown, api: CustomApi & { size?: (values: number[]) => number[] }) => {
          const xi = api.value(0);
          const yi = api.value(1);
          const value = api.value(2);
          if (!(value > 0)) return null;
          const [cx, cy] = api.coord([xi, yi]);
          const [w, h] = api.size ? api.size([1, 1]) : [6, 10];
          const pad = w >= 6 ? 1 : 0;
          return {
            type: "rect",
            shape: { x: cx - w / 2, y: cy - h / 2, width: Math.max(1, w - pad), height: Math.max(1, h - pad) },
            style: { fill: cellFill(value) },
          };
        },
      },
      {
        type: "line",
        name: "current tick",
        data: model.currentLine,
        symbol: "none",
        smooth: false,
        silent: true,
        z: 3,
        lineStyle: { color: ink.currentLine, width: 1, type: "dashed", opacity: 0.75 },
        tooltip: { show: false },
      },
    ],
  } as EChartsOption;
}

// ---------------------------------------------------------------------------
// Pool history
// ---------------------------------------------------------------------------

export interface PriceHistoryArgs {
  /** True when only the raw ratio is available (decimals unknown). */
  raw: boolean;
  unit: string;
  inverted?: boolean;
}

/** Price over time. Log y when the series spans more than two decades. */
export function priceHistoryOption(points: StatePoint[], args: PriceHistoryArgs): EChartsOption {
  const values = points.map((point) => {
    const value = args.raw ? point.priceRaw : point.priceAdjusted;
    if (value === null || !(value > 0)) return null;
    return args.inverted ? 1 / value : value;
  });
  const positives = values.filter((value): value is number => value !== null);
  const spread = positives.length ? Math.max(...positives) / Math.min(...positives) : 1;
  const useLog = spread > 100;
  return {
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown) => fmtPrice(value),
    },
    grid: { left: 74, right: 24, top: 36, bottom: 48 },
    xAxis: { type: "category", data: points.map((point) => point.date), axisLabel: { ...AXIS_LABEL, hideOverlap: true } },
    yAxis: {
      type: useLog ? "log" : "value",
      scale: true,
      name: `${args.unit}${useLog ? " (log)" : ""}`,
      nameTextStyle: NAME_STYLE,
      axisLabel: { ...AXIS_LABEL, formatter: (value: number) => fmtPrice(value) },
    },
    dataZoom: insideZoom,
    series: [{
      name: args.raw ? "price (raw)" : "price",
      type: "line",
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      data: values,
    }],
  } as EChartsOption;
}

/** Liquidity (left axis) with the initialized-tick count on the right. */
export function liquidityHistoryOption(points: StatePoint[]): EChartsOption {
  return {
    tooltip: { trigger: "axis" },
    legend: { data: ["Liquidity (L)", "Initialized ticks"], textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 74, right: 64, top: 40, bottom: 48 },
    xAxis: { type: "category", data: points.map((point) => point.date), axisLabel: { ...AXIS_LABEL, hideOverlap: true } },
    yAxis: [
      {
        type: "value",
        name: "L",
        nameTextStyle: NAME_STYLE,
        axisLabel: { ...AXIS_LABEL, formatter: (value: number) => fmtLiquidity(value) },
      },
      { type: "value", name: "ticks", nameTextStyle: NAME_STYLE, axisLabel: AXIS_LABEL, splitLine: { show: false } },
    ],
    dataZoom: insideZoom,
    series: [
      {
        name: "Liquidity (L)",
        type: "line",
        showSymbol: false,
        smooth: false,
        areaStyle: { opacity: 0.18 },
        data: points.map((point) => point.liquidity),
        tooltip: { valueFormatter: (value: unknown) => fmtLiquidity(value) },
      },
      {
        name: "Initialized ticks",
        type: "line",
        yAxisIndex: 1,
        showSymbol: false,
        step: "end",
        lineStyle: { width: 1, type: "dashed" },
        data: points.map((point) => point.tickCount),
      },
    ],
  } as EChartsOption;
}

const RESERVE_ROW_HEIGHT = 128;

/** Raw reserves over time — ONE axis per token (stacked small multiples), so
 * a Balancer pool's eight tokens never share a scale. Units when decimals
 * are known; raw base units (flagged in the axis name) otherwise. */
export function reservesHistoryOption(series: ReserveSeries[]): EChartsOption {
  const rows = series.length;
  const dates = [...new Set(series.flatMap((entry) => entry.points.map((point) => point.date)))].sort();
  const grids: Array<Record<string, unknown>> = [];
  const xAxes: Array<Record<string, unknown>> = [];
  const yAxes: Array<Record<string, unknown>> = [];
  const out: Array<Record<string, unknown>> = [];
  series.forEach((entry, index) => {
    const useUnits = entry.decimals !== null && entry.points.some((point) => point.units !== null);
    const byDate = new Map(entry.points.map((point) => [point.date, useUnits ? point.units : point.float]));
    const label = tokenLabel(entry.symbol, entry.token);
    const top = 30 + index * RESERVE_ROW_HEIGHT;
    grids.push({ left: 88, right: 24, top, height: RESERVE_ROW_HEIGHT - 42 });
    xAxes.push({
      type: "category",
      gridIndex: index,
      data: dates,
      axisLabel: { ...AXIS_LABEL, hideOverlap: true, show: index === rows - 1 },
      axisTick: { show: false },
    });
    yAxes.push({
      type: "value",
      gridIndex: index,
      name: `${label}${useUnits ? "" : " (raw units)"}`,
      nameTextStyle: NAME_STYLE,
      axisLabel: { ...AXIS_LABEL, formatter: (value: number) => (useUnits ? fmtUnits(value) : fmtLiquidity(value)) },
    });
    out.push({
      name: label,
      type: "line",
      xAxisIndex: index,
      yAxisIndex: index,
      showSymbol: false,
      areaStyle: { opacity: 0.15 },
      connectNulls: false,
      data: dates.map((date) => byDate.get(date) ?? null),
      tooltip: { valueFormatter: (value: unknown) => (useUnits ? fmtUnits(value) : fmtRaw(value)) },
    });
  });
  return {
    _cerebro_height: `${Math.max(220, 30 + rows * RESERVE_ROW_HEIGHT + 30)}px`,
    tooltip: { trigger: "axis" },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    legend: { show: false },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [{ type: "inside", xAxisIndex: xAxes.map((_, index) => index), filterMode: "none" }],
    series: out,
  } as EChartsOption;
}

export interface FeesHistoryArgs {
  sym0: string;
  sym1: string;
  units0: boolean;
  units1: boolean;
}

/** Estimated fees per day per token, each on its own axis. Nulls (first
 * row, negative delta, zero liquidity) stay null — never drawn as 0. */
export function feesHistoryOption(points: FeePoint[], args: FeesHistoryArgs): EChartsOption {
  const value0 = (point: FeePoint) => (args.units0 ? point.fees0Units : (point.fees0Raw === null ? null : Number(point.fees0Raw)));
  const value1 = (point: FeePoint) => (args.units1 ? point.fees1Units : (point.fees1Raw === null ? null : Number(point.fees1Raw)));
  const name0 = `${args.sym0}${args.units0 ? "" : " (raw)"}`;
  const name1 = `${args.sym1}${args.units1 ? "" : " (raw)"}`;
  return {
    tooltip: { trigger: "axis", valueFormatter: (value: unknown) => fmtUnits(value) },
    legend: { data: [name0, name1], textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 74, right: 74, top: 40, bottom: 48 },
    xAxis: { type: "category", data: points.map((point) => point.date), axisLabel: { ...AXIS_LABEL, hideOverlap: true } },
    yAxis: [
      { type: "value", name: name0, nameTextStyle: NAME_STYLE, axisLabel: { ...AXIS_LABEL, formatter: (value: number) => fmtUnits(value) } },
      { type: "value", name: name1, nameTextStyle: NAME_STYLE, axisLabel: { ...AXIS_LABEL, formatter: (value: number) => fmtUnits(value) }, splitLine: { show: false } },
    ],
    dataZoom: insideZoom,
    series: [
      { name: name0, type: "bar", barMaxWidth: 14, data: points.map((point) => value0(point)) },
      { name: name1, type: "bar", yAxisIndex: 1, barMaxWidth: 14, data: points.map((point) => value1(point)) },
    ],
  } as EChartsOption;
}

/** Initialized ticks on a value (tick) axis: signed net liquidity as bars,
 * gross liquidity as points, the current tick as a dashed mark. */
export function ticksOption(points: TickPoint[], labelCtx: AxisLabelContext, isDark: boolean): EChartsOption {
  const ink = liquidityInk(isDark);
  const nets = points.filter((point) => point.net !== null).map((point) => [point.tick, point.net]);
  const grosses = points.filter((point) => point.gross !== null).map((point) => [point.tick, point.gross]);
  return {
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const item = params as { seriesName?: string; value?: [number, number] };
        if (!item.value) return "";
        return `${escapeHtml(item.seriesName ?? "")} @ tick ${escapeHtml(fmtTick(item.value[0]))}`
          + ` (${escapeHtml(labelFor(item.value[0], { ...labelCtx, mode: "price" }))})<br/>`
          + `${escapeHtml(fmtLiquidity(item.value[1]))}`;
      },
    },
    legend: { data: ["Net liquidity", "Gross liquidity"], textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 74, right: 24, top: 40, bottom: 56 },
    xAxis: {
      type: "value",
      scale: true,
      name: "tick",
      nameLocation: "middle",
      nameGap: 30,
      nameTextStyle: NAME_STYLE,
      axisLabel: { ...AXIS_LABEL, hideOverlap: true, formatter: (value: number) => labelFor(value, labelCtx) },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "L",
      nameTextStyle: NAME_STYLE,
      axisLabel: { ...AXIS_LABEL, formatter: (value: number) => fmtLiquidity(value) },
    },
    dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none" }],
    series: [
      {
        name: "Net liquidity",
        type: "bar",
        barMaxWidth: 10,
        data: nets,
        itemStyle: {
          color: (params: unknown) => {
            const value = (params as { value?: [number, number] }).value;
            return value && value[1] < 0 ? "#f87171" : ink.bar;
          },
        },
        ...(labelCtx.currentTick !== null
          ? {
              markLine: {
                symbol: "none",
                animation: false,
                silent: true,
                lineStyle: { color: ink.currentLine, type: "dashed", width: 1 },
                label: { formatter: "current", position: "insideEndTop", color: ink.currentLine, fontFamily: LABEL_FONT, fontSize: 10 },
                data: [{ xAxis: labelCtx.currentTick }],
              },
            }
          : {}),
      },
      {
        name: "Gross liquidity",
        type: "scatter",
        symbolSize: 6,
        data: grosses,
      },
    ],
  } as EChartsOption;
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

export function livePoolTrendOption(points: LiveTrendPoint[]): EChartsOption {
  const line = (name: string, pick: (point: LiveTrendPoint) => number | null) => ({
    name,
    type: "line",
    showSymbol: false,
    smooth: false,
    connectNulls: false,
    data: points.map(pick),
  });
  return {
    tooltip: { trigger: "axis" },
    legend: { textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 58, right: 24, top: 42, bottom: 48 },
    xAxis: { type: "category", data: points.map((point) => point.bucket), axisLabel: { ...AXIS_LABEL, hideOverlap: true } },
    yAxis: { type: "value", name: "pools", nameTextStyle: NAME_STYLE, axisLabel: AXIS_LABEL },
    dataZoom: insideZoom,
    series: [
      line("Published (CL)", (point) => point.publishedCl),
      line("Live (CL)", (point) => point.liveCl),
      line("Probed", (point) => point.probed),
      line("Published (reserves)", (point) => point.publishedReserves),
    ],
  } as EChartsOption;
}

const NO_FEE_BUCKET = "no fee (reserves-only)";

function feeBandLabel(band: string | null): string {
  if (!band) return NO_FEE_BUCKET;
  return FEE_BANDS.find((entry) => entry.id === band)?.label ?? band;
}

/** Pools by fee band, stacked by class. */
export function classFeeMixOption(rows: ClassFeeView[]): EChartsOption {
  const order = new Map<string, number>(FEE_BANDS.map((band, index) => [band.label, index]));
  order.set(NO_FEE_BUCKET, FEE_BANDS.length);
  const mapped = rows
    .map((row) => ({ band: feeBandLabel(row.feeBand), pool_class: row.poolClass, pools: row.pools }))
    .sort((a, b) => (order.get(a.band) ?? 99) - (order.get(b.band) ?? 99));
  return stackedSeriesOption(mapped, {
    xField: "band",
    valueField: "pools",
    seriesField: "pool_class",
    kind: "bar",
    seriesLabeler: classLabel,
    yName: "pools",
    valueFormatter: (value) => fmtInt(value),
  });
}

/** CL pools by probe status × liveness, with the cell's median/p90 L in the tooltip. */
export function probeCoverageOption(rows: ProbeCoverageView[]): EChartsOption {
  const label = (row: ProbeCoverageView) => `${row.probed ? "probed" : "state-only"} · ${row.live ? "live" : "dead"}`;
  const ordered = [...rows].sort((a, b) => Number(b.probed) - Number(a.probed) || Number(b.live) - Number(a.live));
  return {
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const item = params as { dataIndex?: number };
        const row = ordered[item.dataIndex ?? -1];
        if (!row) return "";
        return `${escapeHtml(label(row))}<br/>${escapeHtml(fmtInt(row.pools))} pools<br/>`
          + `median L ${escapeHtml(fmtLiquidity(row.medianLiquidity))} · p90 L ${escapeHtml(fmtLiquidity(row.p90Liquidity))}`;
      },
    },
    grid: { left: 58, right: 24, top: 28, bottom: 56 },
    xAxis: { type: "category", data: ordered.map(label), axisLabel: { ...AXIS_LABEL, interval: 0 } },
    yAxis: { type: "value", name: "pools", nameTextStyle: NAME_STYLE, axisLabel: AXIS_LABEL },
    series: [{ name: "Pools", type: "bar", barMaxWidth: 46, data: ordered.map((row) => row.pools) }],
  } as EChartsOption;
}

const SHARE_METRIC_LABELS: Record<string, string> = {
  share_1pct: "±1%",
  share_5pct: "±5%",
  share_10pct: "±10%",
};

/** Quartiles of the tick-weighted share within each band across probed pools. */
export function concentrationOption(rows: ConcentrationSummaryView[]): EChartsOption {
  const shares = rows.filter((row) => row.metric in SHARE_METRIC_LABELS)
    .sort((a, b) => Object.keys(SHARE_METRIC_LABELS).indexOf(a.metric) - Object.keys(SHARE_METRIC_LABELS).indexOf(b.metric));
  const pct = (value: number | null) => (value === null ? null : value * 100);
  return {
    tooltip: { trigger: "axis", valueFormatter: (value: unknown) => (value === null ? "—" : `${Number(value).toFixed(1)}%`) },
    legend: { data: ["q25", "median", "q75"], textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 58, right: 24, top: 40, bottom: 48 },
    xAxis: { type: "category", data: shares.map((row) => SHARE_METRIC_LABELS[row.metric]), axisLabel: AXIS_LABEL },
    yAxis: { type: "value", max: 100, name: "share of L·width", nameTextStyle: NAME_STYLE, axisLabel: { ...AXIS_LABEL, formatter: "{value}%" } },
    series: [
      { name: "q25", type: "bar", barMaxWidth: 26, data: shares.map((row) => pct(row.q25)) },
      { name: "median", type: "bar", barMaxWidth: 26, data: shares.map((row) => pct(row.median)) },
      { name: "q75", type: "bar", barMaxWidth: 26, data: shares.map((row) => pct(row.q75)) },
    ],
  } as EChartsOption;
}

export function rangeWidthOption(rows: RangeWidthView[]): EChartsOption {
  return {
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const item = params as { dataIndex?: number };
        const row = rows[item.dataIndex ?? -1];
        if (!row) return "";
        return `${escapeHtml(row.bucket)} ticks<br/>${escapeHtml(fmtInt(row.ranges))} ranges in ${escapeHtml(fmtInt(row.pools))} pools`
          + `<br/>${escapeHtml(fmtPct(row.share))} of ranges`;
      },
    },
    grid: { left: 58, right: 24, top: 28, bottom: 48 },
    xAxis: { type: "category", data: rows.map((row) => row.bucket), axisLabel: { ...AXIS_LABEL, interval: 0 } },
    yAxis: { type: "value", name: "ranges", nameTextStyle: NAME_STYLE, axisLabel: AXIS_LABEL },
    series: [{ name: "Ranges", type: "bar", barMaxWidth: 40, data: rows.map((row) => row.ranges) }],
  } as EChartsOption;
}

// ---------------------------------------------------------------------------
// Coverage
// ---------------------------------------------------------------------------

export function coverageCalendarOption(points: CalendarPoint[]): EChartsOption {
  const dates = [...new Set(points.map((point) => point.date))].sort();
  const jobs = [...new Set(points.map((point) => point.job))].sort();
  const byKey = new Map(points.map((point) => [`${point.job}|${point.date}`, point.poolsPublished]));
  return {
    tooltip: { trigger: "axis" },
    legend: { textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 58, right: 24, top: 42, bottom: 48 },
    xAxis: { type: "category", data: dates, axisLabel: { ...AXIS_LABEL, hideOverlap: true } },
    yAxis: { type: "value", name: "pools published", nameTextStyle: NAME_STYLE, axisLabel: AXIS_LABEL },
    dataZoom: insideZoom,
    series: jobs.map((job) => ({
      name: job,
      type: "line",
      showSymbol: false,
      step: "end",
      connectNulls: false,
      data: dates.map((date) => byKey.get(`${job}|${date}`) ?? null),
    })),
  } as EChartsOption;
}

/** Integrity-check counts per day for the CL job (the only job that runs them). */
export function checksSummaryOption(points: CalendarPoint[]): EChartsOption {
  const cl = points.filter((point) => point.poolsProbed !== null || point.poolsBelowThreshold !== null);
  const dates = cl.map((point) => point.date);
  const line = (name: string, pick: (point: CalendarPoint) => number | null) => ({
    name, type: "line", showSymbol: false, connectNulls: false, data: cl.map(pick),
  });
  return {
    tooltip: { trigger: "axis" },
    legend: { textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 58, right: 24, top: 42, bottom: 48 },
    xAxis: { type: "category", data: dates, axisLabel: { ...AXIS_LABEL, hideOverlap: true } },
    yAxis: { type: "value", name: "pools", nameTextStyle: NAME_STYLE, axisLabel: AXIS_LABEL },
    dataZoom: insideZoom,
    series: [
      line("Below active threshold", (point) => point.poolsBelowThreshold),
      line("Probed", (point) => point.poolsProbed),
      line("Net-sum-zero passed", (point) => point.netSumZeroPassed),
      line("Reconciles passed", (point) => point.reconcilesPassed),
    ],
  } as EChartsOption;
}

export function metadataGapOption(rows: MetadataGapView[]): EChartsOption {
  return {
    _cerebro_height: `${Math.max(240, 80 + rows.length * 30)}px`,
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const item = params as { dataIndex?: number };
        const row = rows[item.dataIndex ?? -1];
        if (!row) return "";
        return `${escapeHtml(row.dimension)}<br/>${escapeHtml(fmtInt(row.known))} known · ${escapeHtml(fmtInt(row.unknown))} unknown<br/>${escapeHtml(fmtPct(row.pctKnown))} known`;
      },
    },
    grid: { left: 180, right: 40, top: 16, bottom: 36 },
    xAxis: { type: "value", max: 100, axisLabel: { ...AXIS_LABEL, formatter: "{value}%" } },
    yAxis: { type: "category", data: rows.map((row) => row.dimension), axisLabel: AXIS_LABEL, inverse: true },
    series: [{
      name: "Known",
      type: "bar",
      barMaxWidth: 18,
      data: rows.map((row) => (row.pctKnown === null ? null : row.pctKnown * 100)),
      label: { show: true, position: "right", fontFamily: LABEL_FONT, fontSize: 10, formatter: (params: unknown) => `${Number((params as { value?: number }).value ?? 0).toFixed(1)}%` },
    }],
  } as EChartsOption;
}

// ---------------------------------------------------------------------------
// Token entity
// ---------------------------------------------------------------------------

/** Share of the token's reserves held by each pool (top 15). */
export function tokenPoolsShareOption(rows: TokenPoolView[], unit: string): EChartsOption {
  const ranked = rows
    .filter((row) => row.share !== null)
    .sort((a, b) => (b.share ?? 0) - (a.share ?? 0))
    .slice(0, 15);
  const label = (row: TokenPoolView) =>
    `${row.name || shortAddr(row.address)} · ${row.counterLabels.map((entry) => tokenLabel(entry, entry)).join("/") || classLabel(row.poolClass)}`;
  return {
    _cerebro_height: `${Math.max(240, 80 + ranked.length * 28)}px`,
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const item = params as { dataIndex?: number };
        const row = ranked[item.dataIndex ?? -1];
        if (!row) return "";
        return `${escapeHtml(label(row))}<br/>${escapeHtml(fmtPct(row.share))} of reserves`
          + `<br/>${escapeHtml(row.reserveUnits === null ? `${fmtRaw(row.reserveRaw)} raw` : `${fmtUnits(row.reserveUnits)} ${unit}`)}`;
      },
    },
    grid: { left: 200, right: 40, top: 16, bottom: 36 },
    xAxis: { type: "value", max: 100, axisLabel: { ...AXIS_LABEL, formatter: "{value}%" } },
    yAxis: { type: "category", data: ranked.map(label), axisLabel: { ...AXIS_LABEL, overflow: "truncate", width: 180 }, inverse: true },
    series: [{ name: "Share", type: "bar", barMaxWidth: 18, data: ranked.map((row) => (row.share ?? 0) * 100) }],
  } as EChartsOption;
}
