// Wire types mirroring src/cerebro_mcp/mini_app_models.py.
// Keep field names in sync — these come straight from
// CallToolResult.structuredContent (Pydantic .model_dump output).

export type DatasetMode =
  | "exact_bounded"
  | "exact_capped"
  | "random_sample"
  | "preview_only";
export type PayloadType =
  | "INITIAL_LOAD"
  | "PATCH_VIEW_STATE"
  | "SHOW_WARNING";

export interface DatasetStats {
  row_count: number;
  rows_returned: number;
  mode: DatasetMode;
  sample_source_rows?: number | null;
  source_rows?: number | null;
  row_cap?: number | null;
  truncated?: boolean | null;
  fetched_at?: string | null;
  elapsed_seconds?: number | null;
  warnings: string[];
}

export interface DatasetSchemaColumn {
  name: string;
  type: string;
}

export interface DatasetDescriptor {
  key: string;
  title: string;
  sql: string;
  database: string;
  columns: DatasetSchemaColumn[];
  stats: DatasetStats;
  preview_rows: unknown[][];
  page_token?: string | null;
  scope_id?: string | null;
  provenance?: Record<string, unknown>;
}

/**
 * Revision-guarded append used by server-side keyset pagination.
 *
 * `fallback` deliberately starts at page token `offset:0`.  A client that
 * does not still hold the exact `base_revision` (or no longer has every base
 * row materialised in its descriptor) adopts that descriptor and hydrates the
 * complete current dataset instead of risking a mixed-revision append.
 */
export interface DatasetAppendDelta {
  operation: "append";
  base_revision: number;
  dataset_revision: number;
  base_row_count: number;
  rows: unknown[][];
  fallback: DatasetDescriptor;
}

export interface SummaryCard {
  label: string;
  value: string;
  delta?: string | null;
  tone?: "neutral" | "positive" | "negative" | "warning";
}

export interface MiniAppPayload<TState = Record<string, unknown>> {
  type: PayloadType;
  view_id: string;
  app_id: string;
  title: string;
  status?: "ready" | "loading" | "error";
  summary_cards?: SummaryCard[];
  datasets?: Record<string, DatasetDescriptor>;
  view_state?: TState;
  provenance?: Record<string, unknown>;
  warnings?: string[];
  patch?: Partial<TState> | Record<string, unknown>;
}

export interface PageRowsResponse {
  view_id: string;
  dataset_key: string;
  /** Revision of the dataset snapshot that produced this page. */
  dataset_revision?: number;
  /** Actual row count selected after server byte/row limits. */
  page_size?: number;
  columns: string[];
  column_types: string[];
  rows: unknown[][];
  next_page_token: string;
  total_rows: number;
  stats: DatasetStats;
}
