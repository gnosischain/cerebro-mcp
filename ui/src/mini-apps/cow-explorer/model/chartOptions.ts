import type { EChartsOption } from "echarts";
import type { CandleRow, DepthRow, ExecutionGraphModel, FlowLink, ReferencePriceRow } from "../types";
import { rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import type { LadderPoint } from "./depthLadder";
import type { FootprintCell } from "./depthHeatmap";
import {
  CELL_GUTTER_MIN_W, NUMBER_MIN_CELL_H, NUMBER_MIN_CELL_W,
  compactDepth, footprintInk, isImbalanced, rampFor,
  type DepthScale,
} from "./depthFootprintScale";

const insideZoom = [{ type: "inside" as const, start: 0, end: 100 }];

function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]!);
}

/** Sane display for ladder prices/amounts that may span many decades. */
function formatLadderNumber(value: number): string {
  if (!Number.isFinite(value)) return "";
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 0.001 || abs >= 1e9)) return value.toExponential(3);
  return value.toLocaleString("en-US", { maximumSignificantDigits: 6 });
}

export function candleOption(rows: CandleRow[]): EChartsOption {
  return {
    _cerebro_height: "420px",
    tooltip: { trigger: "axis" },
    legend: { data: ["OHLC", "VWAP"] },
    grid: { left: 64, right: 24, top: 48, bottom: 52 },
    xAxis: { type: "category", data: rows.map((r) => r.bucket), boundaryGap: true },
    yAxis: { type: "value", scale: true, name: "quote / base" },
    dataZoom: insideZoom,
    series: [
      { name: "OHLC", type: "candlestick", data: rows.map((r) => [r.open, r.close, r.low, r.high]) },
      { name: "VWAP", type: "line", showSymbol: false, smooth: true, data: rows.map((r) => r.vwap) },
    ],
  } as EChartsOption;
}

export function volumeOption(rows: CandleRow[]): EChartsOption {
  return {
    _cerebro_height: "420px",
    tooltip: { trigger: "axis" },
    legend: { data: ["Base volume", "Fills"] },
    grid: { left: 64, right: 74, top: 40, bottom: 46 },
    xAxis: { type: "category", data: rows.map((r) => r.bucket) },
    yAxis: [{ type: "value", name: "base" }, { type: "value", name: "fills" }],
    dataZoom: insideZoom,
    series: [
      { name: "Base volume", type: "bar", data: rows.map((r) => r.baseVolume) },
      { name: "Fills", type: "line", yAxisIndex: 1, showSymbol: false, data: rows.map((r) => r.fills) },
    ],
  } as EChartsOption;
}

//: Ladder side colors — fixed trading-convention hues (green bids / red
//: asks), readable on both dark and light card surfaces.
const DEPTH_BID_COLOR = "#16a34a";
const DEPTH_ASK_COLOR = "#dc2626";

export interface PairDepthOptionArgs {
  /** From buildDepthLadder: ascending by price; cum in QUOTE units. */
  bids: LadderPoint[];
  /** From buildDepthLadder: ascending by price; cum in BASE units. */
  asks: LadderPoint[];
  mid: number | null;
  /** Client-side reference price (native/auction series); dotted marker.
   * Omitted (never faked) when null/undefined. */
  reference?: number | null;
  baseSymbol: string;
  quoteSymbol: string;
  /** Optional x-range (e.g. a ±10% preset around mid); full extent when absent. */
  range?: { min: number; max: number } | null;
}

/** Pair depth chart over the buildDepthLadder output. Asks (sell BASE) are a
 * red step-"end" line cumulated in BASE on the LEFT axis; bids (sell QUOTE)
 * are a green step-"start" line cumulated in QUOTE on the RIGHT axis. Price
 * is ALWAYS quote-per-base for both sides (server contract — Flip is a pure
 * client re-projection before the ladder is built). */
export function pairDepthOption(args: PairDepthOptionArgs): EChartsOption {
  const { baseSymbol, quoteSymbol } = args;
  const markLineData: Array<Record<string, unknown>> = [];
  if (args.mid !== null && Number.isFinite(args.mid)) {
    markLineData.push({
      xAxis: args.mid,
      lineStyle: { type: "dashed" },
      label: { formatter: "mid", position: "insideEndTop" },
    });
  }
  if (args.reference !== null && args.reference !== undefined && Number.isFinite(args.reference)) {
    markLineData.push({
      xAxis: args.reference,
      lineStyle: { type: "dotted" },
      label: { formatter: "ref", position: "insideEndBottom" },
    });
  }
  const point = (p: LadderPoint): [number, number, number] => [p.price, p.cum, p.orders];
  return {
    _cerebro_height: "380px",
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as Array<{
          seriesName?: string;
          value?: [number, number, number];
        }>;
        const lines: string[] = [];
        for (const item of list) {
          if (!item?.value) continue;
          const [price, cum, orders] = item.value;
          const unit = item.seriesName === "Bids" ? quoteSymbol : baseSymbol;
          lines.push(
            `${escapeHtml(item.seriesName)} @ ${escapeHtml(formatLadderNumber(price))} `
            + `${escapeHtml(quoteSymbol)}/${escapeHtml(baseSymbol)} — cum `
            + `${escapeHtml(formatLadderNumber(cum))} ${escapeHtml(unit)} `
            + `(${orders} ${orders === 1 ? "order" : "orders"})`,
          );
        }
        return lines.join("<br/>");
      },
    },
    legend: { data: ["Asks", "Bids"] },
    grid: { left: 64, right: 74, top: 44, bottom: 52 },
    xAxis: {
      type: "value",
      scale: true,
      name: `${quoteSymbol} per ${baseSymbol}`,
      nameLocation: "middle",
      nameGap: 30,
      ...(args.range ? { min: args.range.min, max: args.range.max } : {}),
    },
    yAxis: [
      { type: "value", name: `cum. ${baseSymbol}` },
      { type: "value", name: `cum. ${quoteSymbol}` },
    ],
    dataZoom: insideZoom,
    series: [
      {
        name: "Asks",
        type: "line",
        step: "end",
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { color: DEPTH_ASK_COLOR, width: 1.5 },
        itemStyle: { color: DEPTH_ASK_COLOR },
        areaStyle: { color: DEPTH_ASK_COLOR, opacity: 0.16 },
        data: args.asks.map(point),
        ...(markLineData.length
          ? {
              markLine: {
                symbol: "none",
                animation: false,
                // Neutral marker color — inheriting the ask red would read
                // as an ask level.
                lineStyle: { color: "#8b9db0" },
                label: { color: "#8b9db0" },
                data: markLineData,
              },
            }
          : {}),
      },
      {
        name: "Bids",
        type: "line",
        step: "start",
        yAxisIndex: 1,
        showSymbol: false,
        lineStyle: { color: DEPTH_BID_COLOR, width: 1.5 },
        itemStyle: { color: DEPTH_BID_COLOR },
        areaStyle: { color: DEPTH_BID_COLOR, opacity: 0.16 },
        data: args.bids.map(point),
      },
    ],
  } as EChartsOption;
}

export interface DepthFootprintOptionArgs {
  xLabels: string[];
  yLabels: string[];
  /** [xIndex, yIndex, bidDepth, askDepth, orders]. */
  cells: FootprintCell[];
  /** [xIndex, fractional yIndex] of the market price across time. */
  midLine: Array<[number, number]>;
  /** Per price level totals for the companion profile, indexed like yLabels. */
  profile: Array<{ bid: number; ask: number }>;
  scale: DepthScale;
  axisMode: "absolute" | "relative";
  baseSymbol: string;
  quoteSymbol: string;
  isDark: boolean;
}

/**
 * Depth-over-time FOOTPRINT: x = time bucket, y = price level, and inside each
 * cell the BID half sits left of the ASK half. Side is carried by POSITION and
 * magnitude by lightness on a per-side sequential ramp — the two never share a
 * channel, which is what the previous hue-for-side / intensity-for-magnitude
 * encoding got wrong (a 51/49 cell shouted as loudly as 100/0).
 *
 * A second grid on the right carries the window-aggregate bid x ask profile,
 * sharing the price axis row-for-row. It is the one part of the panel with a
 * real labelled magnitude axis, so it anchors what the cell colours mean.
 *
 * There is deliberately NO visualMap: it colours a single ramp, and this chart
 * has two plus an imbalance key. The legend is HTML (DepthFootprintLegend).
 */
export function depthFootprintOption(args: DepthFootprintOptionArgs): EChartsOption {
  const { scale, isDark } = args;
  const ink = footprintInk(isDark);
  const askRamp = rampFor("ask", isDark);
  const bidRamp = rampFor("bid", isDark);
  const unit = args.axisMode === "relative"
    ? `% from market · ${args.quoteSymbol} per ${args.baseSymbol}`
    : `${args.quoteSymbol} per ${args.baseSymbol}`;
  const profileMax = args.profile.reduce((m, p) => Math.max(m, p.bid, p.ask), 0) || 1;
  const cellAt = new Map<number, FootprintCell>();
  for (const cell of args.cells) cellAt.set(cell[0] * args.yLabels.length + cell[1], cell);

  return {
    _cerebro_height: "520px",
    animation: false,
    tooltip: {
      confine: true,
      formatter: (params: unknown) => {
        const value = (params as { value?: FootprintCell }).value;
        if (!value) return "";
        const [xi, yi, bid, ask, orders] = value;
        const total = (bid ?? 0) + (ask ?? 0);
        const askShare = total > 0 ? Math.round((ask / total) * 100) : 0;
        return `${escapeHtml(args.xLabels[xi] ?? "")}<br/>`
          + `${escapeHtml(args.yLabels[yi] ?? "")} ${escapeHtml(unit)}<br/>`
          + `Bids ${escapeHtml(formatLadderNumber(bid ?? 0))} · `
          + `Asks ${escapeHtml(formatLadderNumber(ask ?? 0))} ${escapeHtml(args.baseSymbol)}<br/>`
          + `Total ${escapeHtml(formatLadderNumber(total))} ${escapeHtml(args.baseSymbol)}`
          + ` — ${escapeHtml(String(askShare))}% ask<br/>`
          + `${escapeHtml(formatLadderNumber(orders ?? 0))} resting orders`;
      },
    },
    // top >= 48: the y-axis NAME renders above the axis end and clips below it.
    // The right grid is the profile; its width is reserved out of grid[0].
    grid: [
      { left: 82, right: 152, top: 48, bottom: 66 },
      { right: 26, width: 118, top: 48, bottom: 66 },
    ],
    xAxis: [
      {
        type: "category",
        gridIndex: 0,
        data: args.xLabels,
        name: "time (UTC)",
        nameLocation: "middle",
        nameGap: 50,
        axisLabel: {
          rotate: 40,
          hideOverlap: true,
          // Keep the YEAR when the window spans more than one — a MM-DD stamp
          // on a multi-year footprint is unreadable.
          formatter: (value: string) => {
            if (value.length < 16) return value;
            const multiYear = args.xLabels.length > 1
              && args.xLabels[0].slice(0, 4) !== args.xLabels[args.xLabels.length - 1].slice(0, 4);
            return multiYear
              ? value.slice(0, 10)
              : value.slice(5, 16).replace("T", " ");
          },
        },
      },
      {
        type: "value",
        gridIndex: 1,
        min: -profileMax,
        max: profileMax,
        name: `resting ${args.baseSymbol}`,
        nameLocation: "middle",
        nameGap: 34,
        axisLabel: { formatter: (v: number) => compactDepth(Math.abs(v)), hideOverlap: true },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: "category",
        gridIndex: 0,
        data: args.yLabels,
        name: unit,
        nameGap: 14,
        axisLabel: { hideOverlap: true },
      },
      // Same categories, hidden: guarantees the profile lines up row-for-row.
      { type: "category", gridIndex: 1, data: args.yLabels, show: false },
    ],
    dataZoom: [
      // Inside-only (wheel/pinch), never a slider — frozen repo convention.
      // Zooming in is also what reveals the in-cell numbers.
      { type: "inside", xAxisIndex: [0], filterMode: "weakFilter" },
      { type: "inside", yAxisIndex: [0, 1], filterMode: "weakFilter" },
    ],
    series: [
      {
        type: "custom",
        name: "footprint",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: args.cells,
        clip: true,
        animation: false,
        progressive: 0,
        cursor: "pointer",
        encode: { x: 0, y: 1, tooltip: [2, 3, 4] },
        renderItem: (_params: unknown, api: {
          value: (i: number) => number;
          coord: (v: number[]) => number[];
          size?: (v: number[]) => number[];
        }) => {
          const xi = api.value(0);
          const yi = api.value(1);
          const bid = api.value(2);
          const ask = api.value(3);
          const [cx, cy] = api.coord([xi, yi]);
          const [w, h] = api.size ? api.size([1, 1]) : [6, 10];
          const pad = w >= CELL_GUTTER_MIN_W ? 1 : 0;
          const cw = Math.max(1, w - pad);
          const ch = Math.max(1, h - pad);
          const x0 = cx - cw / 2;
          const y0 = cy - ch / 2;
          const half = cw / 2;
          const showNumbers = cw >= NUMBER_MIN_CELL_W && ch >= NUMBER_MIN_CELL_H;
          const children: unknown[] = [];
          const halves: Array<["bid" | "ask", number, number]> = [
            ["bid", bid, x0],
            ["ask", ask, x0 + half],
          ];
          for (const [side, depth, hx] of halves) {
            // An empty half is NOT step 0 — it is not drawn at all, so "no book
            // here" reads as the card surface showing through.
            if (!(depth > 0)) continue;
            const step = (side === "ask" ? askRamp : bidRamp)[scale.stepIndex(depth)];
            children.push({
              type: "rect",
              silent: true,
              shape: { x: hx, y: y0, width: half, height: ch },
              style: { fill: step.fill },
            });
            if (showNumbers) {
              children.push({
                type: "text",
                silent: true,
                style: {
                  x: side === "bid" ? x0 + half - 3 : x0 + half + 3,
                  y: cy,
                  text: compactDepth(depth),
                  textAlign: side === "bid" ? "right" : "left",
                  textVerticalAlign: "middle",
                  fontSize: 9,
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                  fill: step.ink,
                },
              });
            }
          }
          // Imbalance rides SHAPE, never fill, so it cannot be mistaken for
          // more depth.
          const dominant = isImbalanced(ask, bid, scale.imbalanceFloor);
          if (dominant) {
            children.push({
              type: "rect",
              silent: true,
              shape: {
                x: dominant === "ask" ? x0 + half : x0,
                y: y0, width: half, height: ch, r: 1,
              },
              style: { fill: "none", stroke: ink.imbalance, lineWidth: 1 },
            });
          }
          return { type: "group", children };
        },
      },
      {
        type: "line",
        name: "market",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: args.midLine,
        symbol: "none",
        smooth: false,
        z: 3,
        silent: true,
        lineStyle: { color: ink.midLine, width: 1, type: "dashed", opacity: 0.65 },
        tooltip: { show: false },
      },
      {
        type: "bar",
        name: "profile bids",
        xAxisIndex: 1,
        yAxisIndex: 1,
        // Negated so the two sides mirror around the centre, matching the
        // bid|ask reading order of the footprint cells.
        data: args.profile.map((p) => -p.bid),
        itemStyle: { color: bidRamp[3].fill },
        barCategoryGap: "12%",
        silent: true,
      },
      {
        type: "bar",
        name: "profile asks",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: args.profile.map((p) => p.ask),
        itemStyle: { color: askRamp[3].fill },
        barCategoryGap: "12%",
        barGap: "-100%",
        silent: true,
      },
    ],
  } as EChartsOption;
}

export function depthOption(rows: DepthRow[]): EChartsOption {
  const bids = rows.filter((r) => r.side === "bid").sort((a, b) => a.price - b.price);
  const asks = rows.filter((r) => r.side === "ask").sort((a, b) => a.price - b.price);
  return {
    _cerebro_height: "380px",
    tooltip: { trigger: "axis" }, legend: { data: ["Known bids", "Known asks"] },
    grid: { left: 64, right: 24, top: 44, bottom: 52 },
    xAxis: { type: "value", name: "quote / base", scale: true },
    yAxis: { type: "value", name: "base quantity" }, dataZoom: insideZoom,
    series: [
      { name: "Known bids", type: "line", step: "end", areaStyle: {}, showSymbol: false, data: bids.map((r) => [r.price, r.baseQuantity]) },
      { name: "Known asks", type: "line", step: "end", areaStyle: {}, showSymbol: false, data: asks.map((r) => [r.price, r.baseQuantity]) },
    ],
  } as EChartsOption;
}

export function activityOption(
  dataset: RowDataset | undefined,
  valueField: string,
  seriesField?: string,
  chartType: "line" | "bar" = "line",
  seriesLabel?: (name: string) => string,
): EChartsOption {
  const rows = rowsToObjects(dataset);
  const buckets = [...new Set(rows.map((r) => String(r.bucket ?? "")))].filter(Boolean);
  const rawGroups = seriesField ? [...new Set(rows.map((r) => String(r[seriesField] ?? "Unknown")))] : [valueField];
  // With a series field, keep only the heaviest 8 series so sparse
  // per-solver charts stay legible; the rest folds into "Other".
  const totals = new Map<string, number>();
  if (seriesField) {
    for (const row of rows) {
      const key = String(row[seriesField] ?? "Unknown");
      totals.set(key, (totals.get(key) ?? 0) + Number(row[valueField] ?? 0));
    }
  }
  const kept = seriesField
    ? [...totals].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([name]) => name)
    : rawGroups;
  const groups = seriesField && rawGroups.length > kept.length ? [...kept, "Other"] : kept;
  const seriesValue = (group: string, bucket: string) => {
    if (!seriesField) {
      const row = rows.find((r) => String(r.bucket) === bucket);
      return Number(row?.[valueField] ?? 0);
    }
    return rows
      .filter((r) => String(r.bucket) === bucket)
      .filter((r) => group === "Other"
        ? !kept.includes(String(r[seriesField] ?? "Unknown"))
        : String(r[seriesField] ?? "Unknown") === group)
      .reduce((acc, r) => acc + Number(r[valueField] ?? 0), 0);
  };
  return {
    tooltip: { trigger: "axis" }, legend: { show: groups.length > 1 },
    grid: { left: 58, right: 24, top: 42, bottom: 48 },
    xAxis: { type: "category", data: buckets }, yAxis: { type: "value" }, dataZoom: insideZoom,
    series: groups.map((group) => ({
      name: group === "Other" || !seriesLabel ? group : seriesLabel(group),
      type: chartType,
      ...(chartType === "bar" ? { stack: seriesField ? "total" : undefined, barMaxWidth: 26 } : { showSymbol: false }),
      data: buckets.map((bucket) => seriesValue(group, bucket)),
    })),
  } as EChartsOption;
}

export interface StackedSeriesArgs {
  /** Column holding the x bucket (e.g. "bucket", "period"). */
  xField: string;
  /** Column holding the numeric value to stack. */
  valueField: string;
  /** Column holding the series key (e.g. "chain_id", "order_class"). */
  seriesField: string;
  /** "share" normalizes each bucket to 100% (y axis rendered as %). */
  mode?: "absolute" | "share";
  kind?: "area" | "bar";
  /** Raw series key -> explicit color (e.g. CHAIN_SERIES_COLORS lookups). */
  seriesColors?: Record<string, string>;
  /** Raw series key -> display name (e.g. chain id -> chain name). */
  seriesLabeler?: (name: string) => string;
}

/** Stacked area/bar over (xField, seriesField) with an optional per-bucket
 * 100%-share normalization — the chain-share trend, order-class trend, and
 * live heartbeat all render through this one builder so stacking behavior
 * and share math can never drift between sections. */
export function stackedSeriesOption(
  rows: Array<Record<string, unknown>>,
  args: StackedSeriesArgs,
): EChartsOption {
  const mode = args.mode ?? "absolute";
  const kind = args.kind ?? "area";
  const buckets: string[] = [];
  const seen = new Set<string>();
  const totalsBySeries = new Map<string, number>();
  const valueByCell = new Map<string, number>();
  const cellKey = (series: string, bucket: string) => `${series}\u0000${bucket}`;
  for (const row of rows) {
    const bucket = String(row[args.xField] ?? "");
    if (!bucket) continue;
    if (!seen.has(bucket)) {
      seen.add(bucket);
      buckets.push(bucket);
    }
    const series = String(row[args.seriesField] ?? "Unknown");
    const value = Number(row[args.valueField] ?? 0);
    if (!Number.isFinite(value)) continue;
    totalsBySeries.set(series, (totalsBySeries.get(series) ?? 0) + value);
    const key = cellKey(series, bucket);
    valueByCell.set(key, (valueByCell.get(key) ?? 0) + value);
  }
  // Heaviest series stack first (bottom) for stable, legible layering.
  const seriesNames = [...totalsBySeries.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name]) => name);
  const bucketTotals = buckets.map((bucket) =>
    seriesNames.reduce((acc, name) => acc + (valueByCell.get(cellKey(name, bucket)) ?? 0), 0),
  );
  const series = seriesNames.map((name) => {
    const data = buckets.map((bucket, index) => {
      const value = valueByCell.get(cellKey(name, bucket)) ?? 0;
      if (mode !== "share") return value;
      const total = bucketTotals[index];
      return total > 0 ? (value / total) * 100 : 0;
    });
    const color = args.seriesColors?.[name];
    const entry: Record<string, unknown> = {
      name: args.seriesLabeler ? args.seriesLabeler(name) : name,
      type: kind === "bar" ? "bar" : "line",
      stack: "total",
      data,
    };
    if (kind === "bar") {
      entry.barMaxWidth = 26;
    } else {
      entry.areaStyle = { opacity: 0.55 };
      entry.showSymbol = false;
      entry.lineStyle = { width: 1, ...(color ? { color } : {}) };
      entry.emphasis = { focus: "series" };
    }
    if (color) entry.itemStyle = { color };
    return entry;
  });
  return {
    tooltip: {
      trigger: "axis",
      ...(mode === "share"
        ? { valueFormatter: (value: unknown) => `${Number(value).toFixed(1)}%` }
        : {}),
    },
    legend: { show: seriesNames.length > 1, type: "scroll" },
    grid: { left: 58, right: 24, top: 42, bottom: 48 },
    xAxis: { type: "category", data: buckets, boundaryGap: kind === "bar" },
    yAxis:
      mode === "share"
        ? { type: "value", max: 100, axisLabel: { formatter: "{value}%" } }
        : { type: "value" },
    dataZoom: insideZoom,
    series,
  } as EChartsOption;
}

export function pieOption(
  items: Array<{ name: string; value: number }>,
  opts: { donut?: boolean } = {},
): EChartsOption {
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: { show: items.length > 1, type: "scroll", bottom: 0 },
    series: [{
      type: "pie",
      radius: opts.donut ? ["52%", "76%"] : "72%",
      center: ["50%", "46%"],
      data: items,
      label: { formatter: "{b}: {d}%", overflow: "truncate" },
      emphasis: { focus: "self" },
    }],
  } as EChartsOption;
}

export function treemapOption(items: Array<{ name: string; value: number }>): EChartsOption {
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c}" },
    series: [{
      type: "treemap",
      data: items,
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: { show: true, overflow: "truncate" },
      itemStyle: { gapWidth: 2 },
    }],
  } as EChartsOption;
}

//: Growth-accounting palette — explicit hues (not theme palette) so the four
//: movement classes keep a stable meaning across themes: growth greens/blues
//: above the axis, churn red below, amber ratio line.
const GROWTH_COLORS = {
  new: "#34d399",
  reactivated: "#A78BFA",
  returning: "#7B9CE1",
  churned: "#F87171",
  quickRatio: "#F5B14C",
};

/** Trader growth accounting (trader_dynamics rows): stacked monthly bars —
 * new + reactivated + returning above the axis, churned negated below —
 * with the quick ratio as a line on a secondary axis. */
export function growthAccountingOption(rows: Array<Record<string, unknown>>): EChartsOption {
  const num = (row: Record<string, unknown>, field: string): number => {
    const value = Number(row[field] ?? 0);
    return Number.isFinite(value) ? value : 0;
  };
  const bar = (name: string, field: string, color: string, negate = false) => ({
    name,
    type: "bar" as const,
    stack: "traders",
    barMaxWidth: 26,
    itemStyle: { color },
    data: rows.map((row) => (negate ? -num(row, field) : num(row, field))),
  });
  return {
    tooltip: { trigger: "axis" },
    legend: { data: ["New", "Reactivated", "Returning", "Churned", "Quick ratio"] },
    grid: { left: 64, right: 74, top: 42, bottom: 48 },
    xAxis: { type: "category", data: rows.map((row) => String(row.period ?? "")) },
    yAxis: [
      { type: "value", name: "traders" },
      { type: "value", name: "quick ratio", scale: true },
    ],
    dataZoom: insideZoom,
    series: [
      bar("New", "new_traders", GROWTH_COLORS.new),
      bar("Reactivated", "reactivated_traders", GROWTH_COLORS.reactivated),
      bar("Returning", "returning_traders", GROWTH_COLORS.returning),
      // Churn plots below the axis (negated); the server value is positive.
      bar("Churned", "churned_traders", GROWTH_COLORS.churned, true),
      {
        name: "Quick ratio",
        type: "line",
        yAxisIndex: 1,
        showSymbol: false,
        lineStyle: { color: GROWTH_COLORS.quickRatio, width: 2 },
        itemStyle: { color: GROWTH_COLORS.quickRatio },
        data: rows.map((row) => {
          const value = Number(row.quick_ratio);
          return Number.isFinite(value) ? value : null;
        }),
      },
    ],
  } as EChartsOption;
}

export function rankingOption(dataset?: RowDataset): EChartsOption {
  const rows = rowsToObjects(dataset);
  return {
    tooltip: { trigger: "axis" }, grid: { left: 58, right: 24, top: 32, bottom: 46 },
    xAxis: { type: "category", data: rows.map((r) => String(r.ranking)) },
    yAxis: { type: "value" },
    series: [{ name: "Solutions", type: "bar", data: rows.map((r) => Number(r.solution_count ?? 0)) }],
  };
}

export function referencePriceOption(rows: ReferencePriceRow[], name: string): EChartsOption {
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 64, right: 24, top: 38, bottom: 50 },
    xAxis: { type: "category", data: rows.map((row) => row.bucket) },
    yAxis: { type: "value", scale: true, name: "quote / base" },
    dataZoom: insideZoom,
    series: [{ name, type: "line", showSymbol: false, data: rows.map((row) => row.price) }],
  };
}

export function sankeyOption(links: FlowLink[]): EChartsOption {
  const nodes = [...new Set(links.flatMap((link) => [link.source, link.target]))].map((name) => ({ name }));
  return {
    // Taller than the 350px default: the sankey was cramped and its ribbons
    // unreadable at chart-default height.
    _cerebro_height: "520px",
    tooltip: { trigger: "item" },
    series: [{
      type: "sankey",
      data: nodes,
      links,
      emphasis: { focus: "adjacency" },
      lineStyle: { curveness: 0.5 },
      // Explicit label config — inheriting canvas defaults produced tiny
      // rasterized text; with the SVG renderer these stay vector-crisp.
      label: {
        show: true,
        fontSize: 12,
        fontFamily: '"JetBrains Mono", ui-monospace, monospace',
        overflow: "truncate",
        width: 200,
      },
      nodeGap: 12,
    }],
  } as EChartsOption;
}

/** Share heatmap (x = column entity, y = row entity, color = 0..1 share).
 * Used for solver-pair specialization, trader-solver affinity, and (via the
 * optional axis args) the trader cohort-retention triangle. All optional
 * args default to the original behavior — existing callers are unchanged. */
export function shareHeatmapOption(args: {
  xLabels: string[];
  yLabels: string[];
  cells: Array<[number, number, number]>;
  colorLabel: string;
  /** Cohort-axes reuse: short labels (e.g. "M+3") need no 40° rotation. */
  xAxisRotate?: number;
  /** Display-only label formatters; cells stay indexed on the raw labels. */
  xLabelFormatter?: (value: string) => string;
  yLabelFormatter?: (value: string) => string;
  /** Narrower left gutter for short y labels (default 170 fits solver names). */
  gridLeft?: number;
}): EChartsOption {
  const height = Math.max(340, Math.min(760, 120 + args.yLabels.length * 22));
  const formatX = args.xLabelFormatter ?? ((value: string) => value);
  const formatY = args.yLabelFormatter ?? ((value: string) => value);
  return {
    _cerebro_height: `${height}px`,
    tooltip: {
      position: "top",
      formatter: (params: unknown) => {
        const value = (params as { value?: [number, number, number] }).value;
        if (!value) return "";
        return `${escapeHtml(formatY(args.yLabels[value[1]]))} × ${escapeHtml(formatX(args.xLabels[value[0]]))}: ${(value[2] * 100).toFixed(1)}%`;
      },
    },
    grid: { left: args.gridLeft ?? 170, right: 70, top: 26, bottom: 90 },
    xAxis: {
      type: "category", data: args.xLabels,
      // Font family/size inherit from the ECharts theme (mini themes use Inter 11).
      axisLabel: {
        rotate: args.xAxisRotate ?? 40,
        ...(args.xLabelFormatter ? { formatter: args.xLabelFormatter } : {}),
      },
    },
    yAxis: {
      type: "category", data: args.yLabels,
      ...(args.yLabelFormatter ? { axisLabel: { formatter: args.yLabelFormatter } } : {}),
    },
    visualMap: {
      min: 0, max: 1, calculable: true, orient: "vertical", right: 0, top: "center",
      itemHeight: 120, text: [args.colorLabel, ""], textGap: 8,
      formatter: (value: unknown) => `${Math.round(Number(value) * 100)}%`,
    },
    series: [{
      type: "heatmap",
      data: args.cells,
      label: { show: false },
      emphasis: { itemStyle: { shadowBlur: 6 } },
    }],
  } as EChartsOption;
}

export function transactionExecutionGraphOption(model: ExecutionGraphModel): EChartsOption {
  const categoryNames = ["Order", "Fill", "Token", "Transaction", "Interaction", "Auction", "Actor"];
  const categoryIndex: Record<string, number> = { order: 0, fill: 1, token: 2, transaction: 3, interaction: 4, auction: 5, actor: 6 };
  const yValues = model.nodes.map((node) => node.y);
  const verticalSpan = yValues.length ? Math.max(...yValues) - Math.min(...yValues) : 0;
  return {
    _cerebro_height: `${Math.max(360, Math.min(850, 260 + verticalSpan * 0.72))}px`,
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const data = (params as { data?: Record<string, unknown> }).data ?? {};
        return [data.name, data.subtitle, data.relation, data.evidenceSource ? `Evidence: ${data.evidenceSource}` : ""].filter(Boolean).map(escapeHtml).join("<br/>");
      },
    },
    series: [{
      type: "graph",
      layout: "none",
      roam: true,
      // Bound the roam zoom so the graph can never be wheel-zoomed into an
      // unrecoverable off-viewport state; ECharts fits the coordinate extent
      // on first render and this keeps interactions near that fit.
      scaleLimit: { min: 0.5, max: 3 },
      labelLayout: { hideOverlap: true },
      draggable: false,
      symbolSize: 54,
      categories: categoryNames.map((name) => ({ name })),
      data: model.nodes.map((node) => ({
        id: node.id, name: node.label, x: node.x, y: node.y,
        category: categoryIndex[node.kind], evidenceSource: node.evidenceSource, subtitle: node.subtitle || node.identifier,
        entityType: node.entityType, identifier: node.identifier,
        symbolSize: node.kind === "transaction" ? 58 : node.kind === "actor" ? 48 : 42,
        label: { show: true, position: "bottom", fontSize: 11, fontWeight: 500, distance: 7 },
      })),
      links: model.edges.map((edge) => ({
        id: edge.id, source: edge.source, target: edge.target, relation: edge.relation,
        evidenceSource: edge.evidenceSource,
        label: { show: false, formatter: edge.label, fontSize: 10 },
        lineStyle: { type: edge.scope === "auction_scoped" ? "dashed" : "solid", width: 1.5, opacity: 0.72, curveness: 0.08 },
      })),
      edgeSymbol: ["none", "arrow"],
      edgeSymbolSize: 7,
      emphasis: { focus: "adjacency" },
    }],
  } as EChartsOption;
}
