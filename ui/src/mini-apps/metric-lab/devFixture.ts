// Dev-mode fixtures. Select with ?demo=loaded | ?demo=multi | ?demo=dual
// (plain page = empty browse mode). Only used when import.meta.env.DEV.

import type { MiniAppPayload } from "../shared/miniAppTypes";
import type { MetricCatalogEntry, MetricLabState } from "./types";
import { APP_ID } from "./types";

export const MOCK_CATALOG: MetricCatalogEntry[] = [
  {
    kind: "model",
    name: "api_execution_transactions_daily",
    label: "api_execution_transactions_daily",
    description: "Daily confirmed transactions on Gnosis Chain execution layer.",
    module: "execution",
    sector: "execution",
    subsector: "transactions",
    layer: "api",
    materialized: "view",
    relation_name: "`dbt`.`api_execution_transactions_daily`",
    root_model: "api_execution_transactions_daily",
    quality_tier: "",
    unit: "",
    tags: ["production", "execution", "tier1", "granularity:daily"],
    allowed_dimensions: [],
    default_dimensions: [],
    supported_time_grains: [],
    executable: true,
    columns: [
      { name: "date", type: "Date" },
      { name: "tx_count", type: "UInt64" },
    ],
  },
  {
    kind: "model",
    name: "api_bridges_flows_daily",
    label: "api_bridges_flows_daily",
    description: "Daily bridge flow volume in USD across all Gnosis bridges.",
    module: "bridges",
    sector: "bridges",
    subsector: "flows",
    layer: "api",
    materialized: "view",
    relation_name: "`dbt`.`api_bridges_flows_daily`",
    root_model: "api_bridges_flows_daily",
    quality_tier: "",
    unit: "",
    tags: ["production", "bridges", "tier1", "granularity:daily"],
    allowed_dimensions: [],
    default_dimensions: [],
    supported_time_grains: [],
    executable: true,
    columns: [
      { name: "date", type: "Date" },
      { name: "bridge", type: "String" },
      { name: "volume_usd", type: "Float64" },
    ],
  },
  {
    kind: "model",
    name: "fct_bridges_kpis_snapshot",
    label: "fct_bridges_kpis_snapshot",
    description: "Point-in-time bridge KPI snapshot (no date column).",
    module: "bridges",
    sector: "bridges",
    layer: "fct",
    materialized: "table",
    relation_name: "`dbt`.`fct_bridges_kpis_snapshot`",
    root_model: "fct_bridges_kpis_snapshot",
    quality_tier: "",
    unit: "",
    tags: ["production", "bridges", "mart"],
    allowed_dimensions: [],
    default_dimensions: [],
    supported_time_grains: [],
    executable: true,
    columns: [
      { name: "bridge", type: "String" },
      { name: "tvl_usd", type: "Float64" },
    ],
  },
  {
    kind: "model",
    name: "int_gbc_deposits_daily",
    label: "int_gbc_deposits_daily",
    description: "Intermediate daily GBC deposit amounts.",
    module: "execution",
    sector: "execution",
    layer: "int",
    materialized: "incremental",
    relation_name: "`dbt`.`int_gbc_deposits_daily`",
    root_model: "int_gbc_deposits_daily",
    quality_tier: "",
    unit: "",
    tags: ["execution", "microbatch"],
    allowed_dimensions: [],
    default_dimensions: [],
    supported_time_grains: [],
    executable: true,
    columns: [
      { name: "date", type: "Date" },
      { name: "amount", type: "Float64" },
    ],
  },
  {
    kind: "model",
    name: "stg_consensus__attestations",
    label: "stg_consensus__attestations",
    description: "Staging view over raw consensus attestations.",
    module: "consensus",
    sector: "consensus",
    layer: "stg",
    materialized: "view",
    relation_name: "`dbt`.`stg_consensus__attestations`",
    root_model: "stg_consensus__attestations",
    quality_tier: "",
    unit: "",
    tags: ["consensus"],
    allowed_dimensions: [],
    default_dimensions: [],
    supported_time_grains: [],
    executable: true,
    columns: [
      { name: "slot", type: "UInt64" },
      { name: "timestamp", type: "DateTime" },
    ],
  },
  {
    kind: "model",
    name: "consensus.blocks",
    label: "consensus.blocks",
    description: "Raw source table of consensus blocks.",
    module: "consensus",
    sector: "consensus",
    layer: "source",
    materialized: "source",
    relation_name: "`consensus`.`blocks`",
    root_model: "consensus.blocks",
    quality_tier: "",
    unit: "",
    tags: [],
    allowed_dimensions: [],
    default_dimensions: [],
    supported_time_grains: [],
    executable: true,
    columns: [
      { name: "slot", type: "UInt64" },
      { name: "proposer_index", type: "UInt64" },
    ],
  },
];

const MOCK_FACETS = {
  sector: { execution: 2, bridges: 2, consensus: 2 },
  layer: { api: 2, fct: 1, int: 1, stg: 1, source: 1 },
  tag: { production: 3, execution: 2, bridges: 2, tier1: 2, "granularity:daily": 2, consensus: 1 },
};

function demoMode(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("demo") ?? "";
}

const DAYS = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(Date.UTC(2026, 4, 1 + i));
  return d.toISOString().slice(0, 10);
});

// Deterministic pseudo-noise (no Math.random — stable snapshots).
const noise = (i: number, seed: number) =>
  Math.sin(i * 12.9898 + seed * 78.233) * 0.5 + 0.5;

const TX = DAYS.map((_, i) => Math.round(120_000 + i * 800 + noise(i, 1) * 15_000));
const BRIDGE = DAYS.map((_, i) =>
  Math.round(2_000_000 + i * 22_000 + noise(i, 2) * 400_000 + TX[i] * 3),
);

function emptyState(): MetricLabState {
  return {
    mode: "empty",
    metric_catalog: MOCK_CATALOG,
    catalog_query: "",
    catalog_total: MOCK_CATALOG.length,
    catalog_facets: MOCK_FACETS,
    selected_metric: "",
    selected_metrics: [],
    selected_dimensions: [],
    selected_limit: 2000,
    selected_order_by: [],
    chart: { xField: "", yField: "", chartType: "table", aggregation: "sum", groupBy: "" },
    analytics_disabled: true,
    estimates: false,
    dataset_mode: null,
    sample_source_rows: null,
    metric_fields: [],
    chart_suggestions: [],
    unvalidated_metrics: [],
  };
}

function loadedSingle(): MiniAppPayload<MetricLabState> {
  return {
    type: "INITIAL_LOAD",
    view_id: "dev-view",
    app_id: APP_ID,
    title: "Metric Lab",
    status: "ready",
    summary_cards: [
      { label: "Rows loaded", value: String(DAYS.length), tone: "neutral" },
      { label: "Source rows", value: "—", tone: "neutral" },
      { label: "Mode", value: "Exact", tone: "positive" },
      { label: "Columns", value: "3", tone: "neutral" },
    ],
    datasets: {
      primary: {
        key: "primary",
        title: "api_bridges_flows_daily",
        sql: "SELECT * FROM `dbt`.`api_bridges_flows_daily` ORDER BY `date` DESC LIMIT 2000",
        database: "dbt",
        columns: [
          { name: "date", type: "Date" },
          { name: "bridge", type: "String" },
          { name: "volume_usd", type: "Float64" },
        ],
        stats: { row_count: DAYS.length, rows_returned: DAYS.length, mode: "exact_bounded", warnings: [] },
        preview_rows: DAYS.map((d, i) => [d, i % 2 ? "xdai" : "omni", BRIDGE[i]]),
      },
    },
    view_state: {
      ...emptyState(),
      mode: "loaded",
      selected_metric: "api_bridges_flows_daily",
      selected_metrics: ["api_bridges_flows_daily"],
      chart: { xField: "date", yField: "volume_usd", chartType: "line", aggregation: "sum", groupBy: "" },
      analytics_disabled: false,
      dataset_mode: "exact_bounded",
    },
    warnings: [],
  };
}

function loadedDual(): MiniAppPayload<MetricLabState> {
  return {
    type: "INITIAL_LOAD",
    view_id: "dev-view-dual",
    app_id: APP_ID,
    title: "Metric Lab",
    status: "ready",
    summary_cards: [
      { label: "Rows loaded", value: String(DAYS.length), tone: "neutral" },
      { label: "Mode", value: "Exact", tone: "positive" },
    ],
    datasets: {
      primary: {
        key: "primary",
        title: "api_execution_transactions_daily",
        sql: "SELECT * FROM `dbt`.`api_execution_transactions_daily` ORDER BY `date` DESC LIMIT 2000",
        database: "dbt",
        columns: [
          { name: "date", type: "Date" },
          { name: "tx_count", type: "UInt64" },
        ],
        stats: { row_count: DAYS.length, rows_returned: DAYS.length, mode: "exact_bounded", warnings: [] },
        preview_rows: DAYS.map((d, i) => [d, TX[i]]),
      },
      secondary: {
        key: "secondary",
        title: "api_bridges_flows_daily",
        sql: "SELECT * FROM `dbt`.`api_bridges_flows_daily` ORDER BY `date` DESC LIMIT 2000",
        database: "dbt",
        columns: [
          { name: "date", type: "Date" },
          { name: "volume_usd", type: "Float64" },
        ],
        stats: { row_count: DAYS.length, rows_returned: DAYS.length, mode: "exact_bounded", warnings: [] },
        preview_rows: DAYS.map((d, i) => [d, BRIDGE[i]]),
      },
    },
    view_state: {
      ...emptyState(),
      mode: "loaded",
      selected_metric: "api_execution_transactions_daily",
      selected_metrics: ["api_execution_transactions_daily", "api_bridges_flows_daily"],
      chart: { xField: "date", yField: "tx_count", chartType: "line", aggregation: "sum", groupBy: "" },
      analytics_disabled: false,
      dataset_mode: "exact_bounded",
    },
    warnings: [],
  };
}

export function buildMockPayload(): MiniAppPayload<MetricLabState> {
  const mode = demoMode();
  if (mode === "loaded" || mode === "multi") return loadedSingle();
  if (mode === "dual") return loadedDual();
  return {
    type: "INITIAL_LOAD",
    view_id: "dev-view",
    app_id: APP_ID,
    title: "Metric Lab",
    status: "ready",
    summary_cards: [
      { label: "Models available", value: String(MOCK_CATALOG.length), tone: "neutral" },
      { label: "Status", value: "Pick a model", tone: "neutral" },
    ],
    datasets: {},
    view_state: emptyState(),
    warnings: [],
  };
}
