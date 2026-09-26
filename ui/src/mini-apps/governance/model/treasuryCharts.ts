// ECharts option builders for the treasury. Frozen conventions: dataZoom is
// INSIDE-only (wheel/pinch, never a slider), only series types registered in
// ui/src/lib/echarts-setup.ts appear (line, bar, treemap — and markLine, since
// MarkLineComponent is registered while MarkArea is NOT), and every string that
// reaches an HTML tooltip goes through escapeHtml.
//
// Gap months arrive as null in EVERY band (see model/treasuryHistory.ts) and
// are marked with a dashed vertical markLine; `connectNulls: false` keeps the
// stack from bridging them into a fake slope.

import type { EChartsOption } from "echarts";

import {
  LABEL_FONT,
  RESIDUAL_COLOR,
  escapeHtml,
  insideZoom,
  treemapOption,
  type TreemapItem,
} from "../../shared/chartOptions";
import { fmtMonth, fmtPrice, fmtUnitsCompact, fmtUsd } from "./treasuryFormat";
import type { PricedMarker, StackBand } from "./treasuryHistory";
import type { PriceRow } from "./treasuryRows";

/** Amber of the gap markers, matching the app's warning chips. */
const GAP_COLOR = "#F59E0B";
const MARKER_COLOR = "#94A3B8";

export type StackUnit = "usd" | "gno" | { units: string };

function unitName(unit: StackUnit): string {
  if (unit === "usd") return "USD";
  if (unit === "gno") return "GNO";
  return unit.units || "units";
}

function formatValue(unit: StackUnit, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return unit === "usd" ? fmtUsd(value) : fmtUnitsCompact(value);
}

/** Axis ticks: compact, no currency sign on unit axes. */
function axisFormatter(unit: StackUnit): (value: number) => string {
  return (value: number) => {
    const text = formatValue(unit, value);
    return text === "—" ? "" : text;
  };
}

export interface ValueStackArgs {
  buckets: string[];
  bands: StackBand[];
  /** Months to mark as incomplete upstream (null in every band). */
  gaps?: string[];
  /** "First priced" markers (asset stacks only). */
  markers?: PricedMarker[];
  /** Partial months -> what they are missing (drawn, disclosed in the tooltip). */
  partialNotes?: Readonly<Record<string, string>>;
  unit: StackUnit;
  height?: string;
}

interface AxisParam {
  axisValue?: string;
  seriesId?: string;
  seriesName?: string;
  value?: unknown;
  marker?: string;
}

/** The axis tooltip: every band's value, the stack total, and — in a gap
 * month — why there is nothing to show. Pure HTML-string builder, exported so
 * the escaping is testable without a chart runtime. */
export function stackTooltipHtml(
  params: AxisParam[],
  unit: StackUnit,
  gapSet: ReadonlySet<string>,
  partialNotes: Readonly<Record<string, string>> = {},
): string {
  const list = Array.isArray(params) ? params : [params];
  const bucket = String(list[0]?.axisValue ?? "");
  const rows: string[] = [`<strong>${escapeHtml(fmtMonth(bucket))}</strong>`];
  if (gapSet.has(bucket)) {
    rows.push(`<span style="opacity:.8">${escapeHtml("Not served upstream — left blank, not a dip")}</span>`);
    return rows.join("<br/>");
  }
  const partial = partialNotes[bucket];
  if (partial) rows.push(`<span style="opacity:.8">${escapeHtml(`${partial} — drawn from what was served`)}</span>`);
  let total = 0;
  let any = false;
  for (const param of list) {
    if (param.seriesId === "gaps" || param.seriesId === "markers") continue;
    const value = typeof param.value === "number" ? param.value : Number(param.value);
    if (!Number.isFinite(value)) continue;
    any = true;
    total += value;
    if (value === 0) continue;
    rows.push(`${escapeHtml(param.seriesName)}: ${escapeHtml(formatValue(unit, value))}`);
  }
  if (any) rows.push(`<strong>${escapeHtml(`Total: ${formatValue(unit, total)}`)}</strong>`);
  return rows.join("<br/>");
}

/** Stacked area of value (or GNO units) over month-ends, one series per band.
 * Series ids are the band ids, so a click can open the band's entity. */
export function valueStackOption(args: ValueStackArgs): EChartsOption {
  const gaps = (args.gaps ?? []).filter((bucket) => args.buckets.includes(bucket));
  const gapSet = new Set(gaps);
  const series: Array<Record<string, unknown>> = args.bands.map((band) => {
    const color = band.isOther ? RESIDUAL_COLOR : band.color;
    return {
      id: band.id,
      name: band.label,
      type: "line",
      stack: "total",
      data: band.data,
      connectNulls: false,
      // Month-end values are points in time: a smoothed curve would invent
      // movement between them (and the mini theme smooths lines by default).
      smooth: false,
      showSymbol: false,
      symbolSize: 4,
      // Line AND area emit mouse events, so clicking a band opens its entity.
      triggerLineEvent: true,
      areaStyle: { opacity: 0.55 },
      lineStyle: { width: 1, ...(color ? { color } : {}) },
      emphasis: { focus: "series" },
      ...(color ? { itemStyle: { color } } : {}),
    };
  });
  if (gaps.length > 0) {
    // A dedicated, unstacked helper series owns the gap markers so it can be
    // toggled from the legend and never shifts the stack.
    series.push({
      id: "gaps",
      name: "Blank month",
      type: "line",
      data: args.buckets.map(() => null),
      showSymbol: false,
      silent: true,
      itemStyle: { color: GAP_COLOR },
      lineStyle: { color: GAP_COLOR, type: "dashed", width: 1 },
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: GAP_COLOR, type: "dashed", width: 1 },
        label: { show: false },
        data: gaps.map((bucket) => ({ xAxis: bucket })),
      },
    });
  }
  const markers = (args.markers ?? []).filter((marker) => args.buckets.includes(marker.bucket));
  if (markers.length > 0) {
    series.push({
      id: "markers",
      name: "First priced",
      type: "line",
      data: args.buckets.map(() => null),
      showSymbol: false,
      silent: true,
      itemStyle: { color: MARKER_COLOR },
      lineStyle: { color: MARKER_COLOR, type: "dotted", width: 1 },
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: MARKER_COLOR, type: "dotted", width: 1 },
        label: {
          show: true,
          position: "insideEndTop",
          fontFamily: LABEL_FONT,
          fontSize: 10,
          // A function, not a template: a "{b}"-shaped token in a label must
          // never be interpolated.
          formatter: (params: unknown) => `${String((params as { name?: string }).name ?? "")} priced`,
        },
        data: markers.map((marker) => ({ xAxis: marker.bucket, name: marker.label })),
      },
    });
  }
  return {
    _cerebro_height: args.height ?? "400px",
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: (params: unknown) => stackTooltipHtml(params as AxisParam[], args.unit, gapSet, args.partialNotes),
    },
    legend: { show: args.bands.length > 1 || gaps.length > 0, type: "scroll", top: 0, textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 64, right: 24, top: 44, bottom: 40 },
    xAxis: {
      type: "category",
      data: args.buckets,
      boundaryGap: false,
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, hideOverlap: true, formatter: (value: string) => fmtMonth(value) },
    },
    yAxis: {
      type: "value",
      name: unitName(args.unit),
      nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, formatter: axisFormatter(args.unit) },
    },
    dataZoom: insideZoom,
    series,
  } as EChartsOption;
}

export interface BreadthArgs {
  buckets: string[];
  priced: Array<number | null>;
  listed: Array<number | null>;
  unverified: Array<number | null>;
  spam: Array<number | null> | null;
  positions: Array<number | null>;
  gaps?: string[];
}

/** Portfolio breadth: token COUNTS by class stacked (counts of the same thing,
 * so the stack is legitimate), positions on a second axis. */
export function breadthOption(args: BreadthArgs): EChartsOption {
  const bar = (name: string, data: Array<number | null>, color?: string) => ({
    name,
    type: "bar",
    stack: "tokens",
    barMaxWidth: 18,
    data,
    ...(color ? { itemStyle: { color } } : {}),
  });
  const gaps = (args.gaps ?? []).filter((bucket) => args.buckets.includes(bucket));
  const series: Array<Record<string, unknown>> = [
    bar("Hub-priced", args.priced),
    bar("Listed", args.listed),
    // An unverified token is an absence of reviewed identity: neutral, like
    // TokenIdentity's unnamed state.
    bar("Unverified", args.unverified, RESIDUAL_COLOR),
  ];
  if (args.spam) series.push(bar("Spam (hidden)", args.spam, "#9CA3AF"));
  series.push({
    name: "Positions",
    type: "line",
    yAxisIndex: 1,
    showSymbol: false,
    connectNulls: false,
    data: args.positions,
    ...(gaps.length > 0
      ? {
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: GAP_COLOR, type: "dashed", width: 1 },
          label: { show: false },
          data: gaps.map((bucket) => ({ xAxis: bucket })),
        },
      }
      : {}),
  });
  return {
    _cerebro_height: "320px",
    tooltip: { trigger: "axis" },
    legend: { top: 0, type: "scroll", textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 52, right: 56, top: 44, bottom: 40 },
    xAxis: {
      type: "category",
      data: args.buckets,
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, hideOverlap: true, formatter: (value: string) => fmtMonth(value) },
    },
    yAxis: [
      {
        type: "value",
        name: "tokens",
        nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
        axisLabel: { fontFamily: LABEL_FONT, fontSize: 10 },
      },
      {
        type: "value",
        name: "positions",
        nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
        axisLabel: { fontFamily: LABEL_FONT, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    dataZoom: insideZoom,
    series,
  } as EChartsOption;
}

/** One token's hub price over time. Several registry roles (a token that was
 * `priced` and became a `retired_mirror`) draw as separate, non-bridged lines. */
export function priceLineOption(points: PriceRow[], label: string): EChartsOption {
  const days = [...new Set(points.map((point) => point.day))].sort();
  const roles = [...new Set(points.map((point) => point.role || "priced"))];
  const byRoleDay = new Map(points.map((point) => [`${point.role || "priced"}|${point.day}`, point.priceUsd]));
  const series = roles.map((role) => ({
    name: roles.length > 1 ? `${label} (${role.split("_").join(" ")})` : label,
    type: "line",
    showSymbol: false,
    connectNulls: false,
    ...(role === "retired_mirror" ? { lineStyle: { type: "dashed", width: 1.5 } } : { lineStyle: { width: 1.5 } }),
    data: days.map((day) => byRoleDay.get(`${role}|${day}`) ?? null),
  }));
  return {
    _cerebro_height: "300px",
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as AxisParam[];
        const rows = [`<strong>${escapeHtml(list[0]?.axisValue ?? "")}</strong>`];
        for (const param of list) {
          const value = Number(param.value);
          if (!Number.isFinite(value)) continue;
          rows.push(`${escapeHtml(param.seriesName)}: ${escapeHtml(fmtPrice(value))}`);
        }
        return rows.join("<br/>");
      },
    },
    legend: { show: series.length > 1, top: 0, textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 64, right: 24, top: 36, bottom: 40 },
    xAxis: {
      type: "category",
      data: days,
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      name: "USD",
      scale: true,
      nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, formatter: (value: number) => fmtPrice(value) },
    },
    dataZoom: insideZoom,
    series,
  } as EChartsOption;
}

/** USD composition treemap. Tile ids are asset-row keys, so a click opens the
 * asset rather than matching on a (spoofable) symbol. */
export function compositionTreemapOption(items: TreemapItem[]): EChartsOption {
  const option = treemapOption(items, { valueFormatter: (value) => fmtUsd(value), height: "360px", clickable: true });
  // Use the whole card: the treemap's default 80%-wide box leaves a frame of
  // dead space around the tiles.
  const series = (option.series as Array<Record<string, unknown>>).map((entry) => ({
    ...entry,
    left: 0,
    right: 0,
    top: 4,
    bottom: 4,
  }));
  return { ...option, series } as EChartsOption;
}

export type BandRef =
  | { kind: "chain"; chainId: number }
  | { kind: "asset"; key: string }
  | { kind: "wallet"; wallet: string }
  | { kind: "class"; key: string }
  | { kind: "other" };

/** Parse a band's series id back into what it stands for. Helper series
 * (gap and first-priced markers) and unknown ids resolve to null. */
export function bandRefFromSeriesId(id: unknown): BandRef | null {
  const text = String(id ?? "");
  if (text === "other") return { kind: "other" };
  const sep = text.indexOf(":");
  if (sep <= 0) return null;
  const prefix = text.slice(0, sep);
  const key = text.slice(sep + 1);
  if (!key) return null;
  if (prefix === "chain") {
    const chainId = Number(key);
    return Number.isInteger(chainId) && chainId > 0 ? { kind: "chain", chainId } : null;
  }
  if (prefix === "asset") return { kind: "asset", key };
  if (prefix === "wallet") return /^0x[0-9a-f]+$/.test(key) ? { kind: "wallet", wallet: key } : null;
  if (prefix === "class") return { kind: "class", key };
  return null;
}
