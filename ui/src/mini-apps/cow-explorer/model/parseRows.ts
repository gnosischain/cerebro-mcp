import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import type { CandleRow, Coverage, DepthRow, FlowLink, ReferencePriceRow } from "../types";

export function parseCandles(dataset?: RowDataset): CandleRow[] {
  return rowsToObjects(dataset).flatMap((row) => {
    const open = finite(row.open);
    const close = finite(row.close);
    const low = finite(row.low);
    const high = finite(row.high);
    const vwap = finite(row.vwap);
    const baseVolume = finite(row.base_volume);
    const quoteVolume = finite(row.quote_volume);
    const fills = finite(row.fill_count);
    if (!row.bucket || [open, close, low, high, vwap, baseVolume, quoteVolume, fills].some((v) => v === null)) return [];
    return [{
      bucket: String(row.bucket), open: open!, close: close!, low: low!, high: high!,
      vwap: vwap!, baseVolume: baseVolume!, quoteVolume: quoteVolume!, fills: fills!,
    }];
  });
}

export function parseDepth(dataset?: RowDataset): DepthRow[] {
  return rowsToObjects(dataset).flatMap((row) => {
    const side = row.side === "bid" || row.side === "ask" ? row.side : null;
    const price = finite(row.limit_price);
    const baseQuantity = finite(row.base_quantity);
    const intents = finite(row.intent_count);
    return side && price !== null && baseQuantity !== null && intents !== null
      ? [{ side, price, baseQuantity, intents }]
      : [];
  });
}

export function parseCoverage(value: unknown): Coverage | null {
  if (!value || typeof value !== "object") return null;
  const coverage = value as Coverage;
  return { ...coverage, warning_codes: Array.isArray(coverage.warning_codes) ? coverage.warning_codes : [] };
}

export function parseReferencePrices(dataset?: RowDataset): ReferencePriceRow[] {
  return rowsToObjects(dataset).flatMap((row) => {
    const price = finite(row.price);
    const bucket = row.bucket ?? row.auction_timestamp;
    if (!bucket || price === null || price <= 0) return [];
    return [{
      bucket: String(bucket),
      price,
      sourceObservedAt: String(row.source_observed_at ?? ""),
    }];
  });
}

export function parseExecutionFlow(dataset?: RowDataset): FlowLink[] {
  const grouped = new Map<string, FlowLink>();
  for (const row of rowsToObjects(dataset)) {
    const value = finite(row.fill_count);
    if (value === null || value <= 0 || !row.settlement_executor) continue;
    const t0 = String(row.token0_symbol || "") || `${String(row.token0).slice(0, 8)}…`;
    const t1 = String(row.token1_symbol || "") || `${String(row.token1).slice(0, 8)}…`;
    const source = `${t0}/${t1}`;
    const target = String(row.settlement_executor);
    const key = `${source}|${target}`;
    const existing = grouped.get(key);
    grouped.set(key, { source, target, value: value + (existing?.value ?? 0) });
  }
  const links = [...grouped.values()];
  const nodeWeights = new Map<string, number>();
  for (const link of links) {
    nodeWeights.set(link.source, (nodeWeights.get(link.source) ?? 0) + link.value);
    nodeWeights.set(link.target, (nodeWeights.get(link.target) ?? 0) + link.value);
  }
  const keep = new Set(
    [...nodeWeights].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 12).map(([name]) => name),
  );
  const compact = new Map<string, FlowLink>();
  for (const link of links) {
    const source = keep.has(link.source) ? link.source : "Other";
    const target = keep.has(link.target) ? link.target : "Other";
    const key = `${source}|${target}`;
    const current = compact.get(key);
    compact.set(key, { source, target, value: link.value + (current?.value ?? 0) });
  }
  return [...compact.values()].sort((a, b) => b.value - a.value);
}

export interface ShareHeatmapModel {
  xLabels: string[];
  yLabels: string[];
  /** [xIndex, yIndex, share 0..1] triplets for the ECharts heatmap. */
  cells: Array<[number, number, number]>;
}

/** Generic share heatmap builder: rows carry (rowKey, colKey, weight, share).
 * Keeps the heaviest `maxRows`/`maxCols` entities by summed weight so the
 * heatmap stays readable; drops rows with missing keys or non-finite values. */
export function buildShareHeatmap(args: {
  rows: Array<Record<string, unknown>>;
  rowLabel: (row: Record<string, unknown>) => string;
  colLabel: (row: Record<string, unknown>) => string;
  weightField: string;
  shareField: string;
  maxRows?: number;
  maxCols?: number;
}): ShareHeatmapModel {
  const maxRows = args.maxRows ?? 20;
  const maxCols = args.maxCols ?? 12;
  const rowWeights = new Map<string, number>();
  const colWeights = new Map<string, number>();
  const entries: Array<{ row: string; col: string; share: number }> = [];
  for (const row of args.rows) {
    const rowKey = args.rowLabel(row);
    const colKey = args.colLabel(row);
    const weight = Number(row[args.weightField] ?? 0);
    const share = Number(row[args.shareField]);
    if (!rowKey || !colKey || !Number.isFinite(share)) continue;
    rowWeights.set(rowKey, (rowWeights.get(rowKey) ?? 0) + weight);
    colWeights.set(colKey, (colWeights.get(colKey) ?? 0) + weight);
    entries.push({ row: rowKey, col: colKey, share: Math.max(0, Math.min(1, share)) });
  }
  const top = (weights: Map<string, number>, cap: number) =>
    [...weights].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, cap).map(([name]) => name);
  const yLabels = top(rowWeights, maxRows);
  const xLabels = top(colWeights, maxCols);
  const yIndex = new Map(yLabels.map((label, index) => [label, index]));
  const xIndex = new Map(xLabels.map((label, index) => [label, index]));
  const cells: Array<[number, number, number]> = [];
  for (const entry of entries) {
    const x = xIndex.get(entry.col);
    const y = yIndex.get(entry.row);
    if (x === undefined || y === undefined) continue;
    cells.push([x, y, entry.share]);
  }
  return { xLabels, yLabels, cells };
}
