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
  yField: string;
  chartType: ChartType;
  aggregation: Aggregation;
  groupBy: string;
  /** Secondary (right) y-axis column for line/bar — frontend-local, not
   * synced to the server chart state. */
  y2Field?: string;
  /** Color-encoding column for scatter (numeric → gradient, categorical →
   * colored groups) — frontend-local. */
  colorBy?: string;
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
  chart: ChartConfig;
  sort?: { field: string; direction: "asc" | "desc" };
  filters?: unknown[];
  analytics_disabled: boolean;
  estimates: boolean;
  dataset_mode: DatasetMode | null;
  sample_source_rows: number | null;
  metric_fields?: string[];
  chart_suggestions?: ChartSuggestion[];
  unvalidated_metrics?: string[];
  load_mode?: LoadMode;
  aggregate_config?: Record<string, unknown>;
}

/** Draft query the user assembles in the browse view (basket + config).
 * Mutating it never fires a network call — only the explicit Run button
 * submits it via load_metric_lab_metric. */
export type LoadMode = "aggregate" | "raw";
export type AggFn = "sum" | "avg" | "min" | "max" | "median" | "count" | "uniq";

export const AGG_FNS: AggFn[] = ["sum", "avg", "min", "max", "median", "count", "uniq"];

export interface QuerySpec {
  /** Model names (max 2 — primary + secondary compare; compare is raw-only). */
  metrics: string[];
  dimensions: string[];
  limit: number;
  /** Trailing time window in days (0 = all history). */
  windowDays: number;
  /** "aggregate" runs agg(y) GROUP BY x [, series] IN CLICKHOUSE — the only
   * correct way to chart big per-entity panels. "raw" samples rows. */
  mode: LoadMode;
  aggX: string;
  aggY: string;
  aggFn: AggFn;
  aggSeries: string;
  aggTopN: number;
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
  aggFn: "sum",
  aggSeries: "",
  aggTopN: 8,
  filterCol: "",
  filterOp: "=",
  filterValue: "",
  orderByField: "",
  orderDir: "desc",
};

export type WorkspaceTab = "chart" | "table" | "analysis";
export type CorrMethod = "pearson" | "spearman";

export const APP_ID = "metric_lab";
