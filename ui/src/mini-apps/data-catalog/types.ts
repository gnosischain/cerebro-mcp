// View payloads for the Data Platform mini app.
// Mirrors src/cerebro_mcp/tools/semantic/data_catalog.py.

import type { MiniAppPayload } from "../shared/miniAppTypes";

export type EntityType = "model" | "metric" | "glossary";
export type PlatformTab = "explore" | "observability" | "governance";

export interface CatalogHit {
  id: string;
  type: EntityType;
  name: string;
  title: string;
  fqn: string;
  description: string;
  module: string;
  tier: string;
  owner: string;
  tags: string[];
  score: number | null;
}

export interface CatalogFacets {
  type: Record<string, number>;
  module: Record<string, number>;
  tier: Record<string, number>;
  tags: Record<string, number>;
  owner: Record<string, number>;
}

export interface CatalogSearchResult {
  query: string;
  total: number;
  hits: CatalogHit[];
  facets: CatalogFacets;
  limit: number;
  suggestions?: Array<{ name: string; title: string; type: EntityType }>;
  warnings: string[];
}

export interface CatalogColumn {
  name: string;
  data_type: string;
  description: string;
}

export interface CatalogMetricRef {
  name: string;
  label: string;
  module: string;
  tier: string;
  description?: string;
}

export interface CatalogGraphProfile {
  profile: string;
  module: string;
  description: string;
  source_kind: string;
  target_kind: string;
  directed: boolean;
  weight_column: string;
  quality_tier: string;
}

export interface CatalogEntity {
  name: string;
  type: EntityType;
  fqn: string;
  description: string;
  owner: string;
  tags: string[];
  tier: string;
  quality_tier: string;
  materialization: string;
  module: string;
  path: string;
  relation_name: string;
  resource_type: string;
  columns: CatalogColumn[];
  column_count: number;
  dimensions: unknown[];
  measures: unknown[];
  upstream: string[];
  downstream: string[];
  upstream_count: number;
  downstream_count: number;
  metrics: CatalogMetricRef[];
  metric_count: number;
  graph_profiles: CatalogGraphProfile[];
  test_count?: number;
  raw_sql?: string;
  // Metric-entity fields:
  label?: string;
  root_model?: string;
  measure?: string;
  metric_type?: string;
  semantic_status?: string;
  default_filters?: string[];
  allowed_dimensions?: string[];
  supported_time_grains?: string[];
  question_synonyms?: string[];
  // Glossary-entity fields:
  source_kind?: string;
  target_kind?: string;
  directed?: boolean;
  weight_column?: string;
  model_name?: string;
  // Error case:
  error?: string;
  suggestions?: string[];
}

// ---- Overview (home) -------------------------------------------------------

export interface DomainStat {
  module: string;
  total: number;
  approved: number;
  candidate: number;
  docs_only: number;
}

export interface ConnectedModel {
  name: string;
  module: string;
  tier: string;
  materialized?: string;
  downstream_count: number;
}

export interface CatalogOverview {
  available: boolean;
  reason?: string;
  stats?: {
    models: number;
    metrics: number;
    glossary: number;
    domains: number;
    relationships: number;
    owned_pct: number;
    doc_coverage_pct: number;
    tier_counts: Record<string, number>;
  };
  domains?: DomainStat[];
  most_connected?: ConnectedModel[];
  entry_points?: ConnectedModel[];
  glossary_terms?: Array<{ name: string; module: string }>;
  top_metrics?: Array<{ name: string; label: string; module: string; tier: string }>;
}

// ---- Live data / runs / tests (degrade-to-payload) -------------------------

export interface TableStats {
  available: boolean;
  reason?: string;
  name?: string;
  materialization?: string;
  is_view?: boolean;
  engine?: string;
  row_count?: number | null;
  size_bytes?: number | null;
  note?: string;
}

export interface SampleData {
  available: boolean;
  reason?: string;
  restricted?: boolean;
  name?: string;
  columns?: string[];
  column_types?: string[];
  rows?: unknown[][];
  row_count?: number;
  truncated?: boolean;
  materialization?: string;
}

export interface RunConfig {
  available: boolean;
  reason?: string;
  materialization?: string;
  incremental_strategy?: string | null;
  unique_key?: unknown;
  partition_by?: unknown;
  on_schema_change?: string | null;
  full_refresh?: unknown;
  tags?: string[];
}

export interface RunRecord {
  status: string;
  completed_at: string;
  execution_time: number | null;
  full_refresh: unknown;
  rows_affected: number | null;
}

export interface RunState {
  available: boolean;
  reason?: string;
  name?: string;
  latest?: RunRecord | null;
  history?: RunRecord[];
}

export interface TestRecord {
  name: string;
  status: string;
  detected_at: string;
}

export interface TestResults {
  available: boolean;
  reason?: string;
  name?: string;
  tests?: TestRecord[];
  counts?: Record<string, number>;
}

export interface CatalogObservability {
  available: boolean;
  reason?: string;
  as_of?: string;
  models?: { ok: number; failed: number; skipped?: number; total: number };
  tests?: { failing: number; warning: number; total: number };
  needs_attention?: Array<{ name: string; status: string; completed_at: string }>;
  recent_runs?: Array<{ name: string; status: string; completed_at: string; execution_time: number | null }>;
}

export interface CatalogGovernance {
  available: boolean;
  reason?: string;
  model_count?: number;
  ownership?: Array<{ owner: string; count: number }>;
  tiers?: Record<string, number>;
  classification?: { restricted: number; public: number };
  doc_coverage_by_module?: Array<{ module: string; documented: number; total: number; pct: number }>;
  unowned_count?: number;
  unowned_sample?: Array<{ name: string; module: string }>;
}

export interface CatalogInitialState {
  view?: "search" | "entity";
  query?: string;
  search?: CatalogSearchResult;
  overview?: CatalogOverview;
  governance?: CatalogGovernance;
  observability?: CatalogObservability;
  entity?: CatalogEntity;
  entity_name?: string;
  entity_type?: EntityType;
}

export type CatalogPayload = MiniAppPayload<Record<string, unknown>> &
  CatalogInitialState;

export interface LineageNode {
  id: string;
  name: string;
  kind: string;
  materialized?: string;
  schema?: string;
  tags?: string[];
  description?: string;
  column_count?: number;
  test_count?: number;
  columns?: CatalogColumn[];
  raw_sql?: string;
  compiled_sql?: string;
}

export interface LineageEdge {
  id: string;
  source: string;
  target: string;
}

export interface LineageResult {
  seed: string;
  seed_id?: string;
  direction: string;
  depth: number;
  nodes: LineageNode[];
  edges: LineageEdge[];
  truncated?: boolean;
  node_count?: number;
  source?: string;
  error?: string;
}

export interface CatalogFilters {
  entityTypes: EntityType[];
  module: string;
  tier: string;
  tags: string[];
  owner: string;
}

export type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;
