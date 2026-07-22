import type { EChartsOption } from "echarts";
import type { CandleRow, DepthRow, ExecutionGraphModel, FlowLink, ReferencePriceRow } from "../types";
import type { RowDataset } from "./parseRows";
import { rowsToObjects } from "./parseRows";

const insideZoom = [{ type: "inside" as const, start: 0, end: 100 }];

function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]!);
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
 * Used for solver-pair specialization and trader-solver affinity — the
 * correlation views the official explorer does not surface. */
export function shareHeatmapOption(args: {
  xLabels: string[];
  yLabels: string[];
  cells: Array<[number, number, number]>;
  colorLabel: string;
}): EChartsOption {
  const height = Math.max(340, Math.min(760, 120 + args.yLabels.length * 22));
  return {
    _cerebro_height: `${height}px`,
    tooltip: {
      position: "top",
      formatter: (params: unknown) => {
        const value = (params as { value?: [number, number, number] }).value;
        if (!value) return "";
        return `${escapeHtml(args.yLabels[value[1]])} × ${escapeHtml(args.xLabels[value[0]])}: ${(value[2] * 100).toFixed(1)}%`;
      },
    },
    grid: { left: 170, right: 70, top: 26, bottom: 90 },
    xAxis: {
      type: "category", data: args.xLabels,
      axisLabel: { rotate: 40, fontSize: 10, fontFamily: '"JetBrains Mono", ui-monospace, monospace' },
    },
    yAxis: {
      type: "category", data: args.yLabels,
      axisLabel: { fontSize: 10, fontFamily: '"JetBrains Mono", ui-monospace, monospace' },
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
        label: { show: true, position: "bottom", fontSize: 11, fontWeight: 600, distance: 7 },
      })),
      links: model.edges.map((edge) => ({
        id: edge.id, source: edge.source, target: edge.target, relation: edge.relation,
        evidenceSource: edge.evidenceSource,
        label: { show: false, formatter: edge.label, fontSize: 9 },
        lineStyle: { type: edge.scope === "auction_scoped" ? "dashed" : "solid", width: 1.5, opacity: 0.72, curveness: 0.08 },
      })),
      edgeSymbol: ["none", "arrow"],
      edgeSymbolSize: 7,
      emphasis: { focus: "adjacency" },
    }],
  } as EChartsOption;
}
