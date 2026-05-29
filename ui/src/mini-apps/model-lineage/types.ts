// View-state + row shapes for the Model Lineage Explorer mini app.
// Mirrors src/cerebro_mcp/tools/analytics/model_lineage_app.py.

export type LineageLayer = "model" | "semantic";
export type LineageDirection = "upstream" | "downstream" | "both";

export interface CatalogEntry {
  name: string;
  schema: string;
  materialized: string;
  tags: string[];
  description: string;
}

export interface ModelLineageState {
  title: string;
  seed: string;
  seed_id: string;
  layer: LineageLayer;
  direction: LineageDirection;
  depth: number;
  include_kinds: string[];
  tags_filter: string[];
  selected_node_id: string;
  selected_column: string;
  catalog: CatalogEntry[];
  warnings: string[];
}

// Dataset row column orders (must match the Python NODES_COLUMNS / EDGES_COLUMNS).
// nodes:  [id, name, kind, materialized, schema, tags, description, column_count, test_count]
// edges:  [id, source, target, layer]
// column_edges: [id, source_model, source_column, target_model, target_column, level]

export interface ModelNodeData {
  id: string;
  name: string;
  kind: string;
  materialized: string;
  schema: string;
  tags: string[];
  description: string;
  columnCount: number;
  testCount: number;
  [key: string]: unknown;
}

export interface LineageEdgeRow {
  id: string;
  source: string;
  target: string;
  layer: string;
}

export interface ColumnEdgeRow {
  id: string;
  sourceModel: string;
  sourceColumn: string | null;
  targetModel: string;
  targetColumn: string | null;
  level: string;
}

export function parseNodeRow(row: unknown[]): ModelNodeData {
  return {
    id: String(row[0] ?? ""),
    name: String(row[1] ?? ""),
    kind: String(row[2] ?? ""),
    materialized: String(row[3] ?? ""),
    schema: String(row[4] ?? ""),
    tags: Array.isArray(row[5]) ? (row[5] as string[]) : [],
    description: String(row[6] ?? ""),
    columnCount: Number(row[7] ?? 0),
    testCount: Number(row[8] ?? 0),
  };
}

export function parseEdgeRow(row: unknown[]): LineageEdgeRow {
  return {
    id: String(row[0] ?? ""),
    source: String(row[1] ?? ""),
    target: String(row[2] ?? ""),
    layer: String(row[3] ?? "model"),
  };
}

export function parseColumnEdgeRow(row: unknown[]): ColumnEdgeRow {
  return {
    id: String(row[0] ?? ""),
    sourceModel: String(row[1] ?? ""),
    sourceColumn: row[2] == null ? null : String(row[2]),
    targetModel: String(row[3] ?? ""),
    targetColumn: row[4] == null ? null : String(row[4]),
    level: String(row[5] ?? ""),
  };
}
