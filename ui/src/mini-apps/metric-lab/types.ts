// Metric Lab types — mirrors the view_state built by
// src/cerebro_mcp/tools/visualization/metric_lab.py. New backend fields are
// optional so the frontend never requires a lockstep deploy.

import type { DatasetMode } from "../shared/miniAppTypes";

export type ChartType =
  | "table"
  | "line"
  | "bar"
  | "scatter"
  | "heatmap"
  | "pie"
  | "numberDisplay";

export type Aggregation = "count" | "sum" | "avg" | "min" | "max" | "median";

export const CHART_TYPES: ChartType[] = [
  "table",
  "line",
  "bar",
  "scatter",
  "heatmap",
  "pie",
  "numberDisplay",
];

export const AGGREGATIONS: Aggregation[] = [
  "count",
  "sum",
  "avg",
  "min",
  "max",
  "median",
];

export interface ChartConfig {
  xField: string;
  /** Normalized mirror of yFields[0] — kept for legacy payloads/consumers. */
  yField: string;
  chartType: ChartType;
  aggregation: Aggregation;
  groupBy: string;
  /** AUTHORITATIVE plotted value columns (deduped, max MAX_Y_FIELDS).
   * Rendering: [0] left axis, [1] right axis, rest left axis. When absent,
   * derive from yField/y2Field via normalizeChartConfig. */
  yFields?: string[];
  /** Normalized mirror of yFields[1]. */
  y2Field?: string;
  /** Color-encoding column for scatter (numeric → gradient, categorical →
   * colored groups). */
  colorBy?: string;
}

/** One panel of the chart-grid workspace. `view_state.chart` is only the
 * LEGACY SCALAR PROJECTION of charts[0] ({xField,yField,chartType,
 * aggregation,groupBy}) — charts[] is the source of truth. */
export interface ChartPanelConfig extends ChartConfig {
  /** Unique per view; /^[A-Za-z0-9_-]{1,16}$/. */
  id: string;
  /** Must reference an existing dataset key of the view. */
  datasetKey: string;
  /** <= 200 chars. */
  title?: string;
  sortDir?: "asc" | "desc";
  trendline?: boolean;
}

export const MAX_CHART_PANELS = 12;
export const MAX_Y_FIELDS = 8;

/** Normalize a chart config: `yFields` wins when present, else it is derived
 * from the legacy yField/y2Field pair; mirrors are re-synced from it. */
export function normalizeChartConfig<T extends ChartConfig>(c: T): T {
  const raw = c.yFields?.length
    ? c.yFields
    : [c.yField, ...(c.y2Field ? [c.y2Field] : [])];
  const yFields = raw
    .filter(Boolean)
    .filter((v, i, a) => a.indexOf(v) === i)
    .slice(0, MAX_Y_FIELDS);
  return { ...c, yFields, yField: yFields[0] ?? "", y2Field: yFields[1] };
}

/** Plotted value columns of a config, after normalization rules. */
export function effectiveYFields(c: ChartConfig): string[] {
  return normalizeChartConfig(c).yFields ?? [];
}

export interface CatalogColumn {
  name: string;
  type: string;
  description?: string;
}

export interface MetricCatalogEntry {
  kind?: "metric" | "model";
  name: string;
  label: string;
  description: string;
  module: string;
  sector?: string;
  subsector?: string;
  root_model: string;
  quality_tier: string;
  unit: string;
  /** dbt layer: api | fct | int | stg | source. */
  layer?: string;
  /** dbt materialization: view | table | incremental | source. */
  materialized?: string;
  /** Fully-qualified DB relation, e.g. `dbt`.`api_bridges_flows_daily`. */
  relation_name?: string;
  /** dbt tags. */
  tags?: string[];
  allowed_dimensions: string[];
  default_dimensions: string[];
  supported_time_grains?: string[];
  executable?: boolean;
  columns?: CatalogColumn[];
  /** Columns whose names matched the search query (server-ranked, max 5). */
  matched_columns?: { name: string; score: number }[];
}

/** Full detail returned by the app-only `get_metric_catalog_entry` tool. */
export interface CatalogEntryDetail extends MetricCatalogEntry {
  semantic_status?: string;
  measure?: string;
  metric_type?: string;
  question_synonyms?: string[];
  default_filters?: unknown[];
  /** Fully-qualified DB relation, e.g. `dbt.api_bridges_flows_daily`. */
  relation_name?: string;
}

/** Response of the app-only `search_metric_catalog` tool. */
export interface CatalogSearchResponse {
  entries: MetricCatalogEntry[];
  total_matching: number;
  facets: CatalogFacets;
  query?: string;
  sector?: string;
  kind?: string;
  tier?: string;
  limit?: number;
  offset?: number;
}

export interface CatalogFacets {
  sector?: Record<string, number>;
  /** dbt layer counts (api/fct/int/stg/source). */
  layer?: Record<string, number>;
  /** Top dbt tags over the matched set (backend caps to ~24). */
  tag?: Record<string, number>;
}

export interface ChartSuggestion {
  chartType: ChartType;
  xField: string;
  yField: string;
  reason: string;
}

export interface MetricLabState {
  mode: "empty" | "loaded";
  metric_catalog: MetricCatalogEntry[];
  catalog_query?: string;
  catalog_total?: number;
  catalog_facets?: CatalogFacets;
  catalog_filters?: Record<string, string>;
  selected_metric: string;
  selected_metrics?: string[];
  selected_dimensions: string[];
  selected_limit: number;
  selected_order_by: string[];
  /** Legacy scalar projection of charts[0] — charts[] is authoritative. */
  chart: ChartConfig;
  /** The chart-panel grid (source of truth; may be absent on old payloads). */
  charts?: ChartPanelConfig[];
  /** Per-dataset revision counters — bumped on every attach/replace; the
   * frontend keys hydration and panel adoption on these. */
  dataset_revisions?: Record<string, number>;
  sort?: { field: string; direction: "asc" | "desc" };
  filters?: unknown[];
  analytics_disabled: boolean;
  estimates: boolean;
  dataset_mode: DatasetMode | null;
  sample_source_rows: number | null;
  metric_fields?: string[];
  chart_suggestions?: ChartSuggestion[];
  unvalidated_metrics?: string[];
  /** "join" = N-model date-joined wide table (server-side). */
  load_mode?: LoadMode | "join";
  aggregate_config?: Record<string, unknown>;
  raw_config?: Record<string, unknown>;
  /** Databases the SQL editor may target (settings.ALLOWED_DATABASES). */
  allowed_databases?: string[];
  /** Per-dataset provenance, e.g. {secondary: {source: "editor_sql"}}. */
  provenance?: Record<string, Record<string, unknown>>;
}

/** Draft query the user assembles in the browse view (basket + config).
 * Mutating it never fires a network call — only the explicit Run button
 * submits it via load_metric_lab_metric. */
export type LoadMode = "aggregate" | "raw";
export type AggFn = "sum" | "avg" | "min" | "max" | "median" | "count" | "uniq";
/** Time-bucket for aggregate mode ("" = group by the raw column).
 * NOT the same thing as LoadMode "raw". */
export type Grain = "" | "day" | "week" | "month";

export const AGG_FNS: AggFn[] = ["sum", "avg", "min", "max", "median", "count", "uniq"];
export const GRAINS: { value: Grain; label: string }[] = [
  { value: "", label: "Raw dates" },
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
];

export interface QuerySpec {
  /** Model names. 2-8 models in aggregate mode join on date into ONE wide
   * table (one value column per model); exactly 2 in raw mode load as the
   * legacy primary + secondary dual compare. */
  metrics: string[];
  dimensions: string[];
  limit: number;
  /** Trailing time window in days (0 = all history). */
  windowDays: number;
  /** "aggregate" runs agg(y) GROUP BY x [, series] IN CLICKHOUSE — the only
   * correct way to chart big per-entity panels. "raw" samples rows. */
  mode: LoadMode;
  aggX: string;
  /** Legacy single measure — mirrors aggYs[0]; aggYs is authoritative. */
  aggY: string;
  /** Measure columns (all aggregated with aggFn). Multi-select is mutually
   * exclusive with aggSeries and with aggFn "count". */
  aggYs: string[];
  aggFn: AggFn;
  /** Day/week/month rollup of aggX ("" = no bucketing). */
  grain: Grain;
  aggSeries: string;
  aggTopN: number;
  /** Raw-mode column projection ([] = all columns). */
  columns: string[];
  /** Multi-model joins — per-model measure/agg overrides keyed by model
   * name (server defaults: first numeric column, sum). */
  joinSpecs: Record<string, { y: string; agg: AggFn }>;
  filterCol: string;
  filterOp: "=" | "!=";
  filterValue: string;
  orderByField: string;
  orderDir: "asc" | "desc";
}

export const DEFAULT_QUERY_SPEC: QuerySpec = {
  metrics: [],
  dimensions: [],
  limit: 2000,
  windowDays: 0,
  mode: "raw",
  aggX: "",
  aggY: "",
  aggYs: [],
  aggFn: "sum",
  grain: "",
  aggSeries: "",
  aggTopN: 8,
  columns: [],
  joinSpecs: {},
  filterCol: "",
  filterOp: "=",
  filterValue: "",
  orderByField: "",
  orderDir: "desc",
};

export type WorkspaceTab = "chart" | "table" | "analysis";
export type CorrMethod = "pearson" | "spearman";

export const APP_ID = "metric_lab";
