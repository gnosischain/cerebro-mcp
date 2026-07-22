// Column-name-keyed row access for mini-app datasets (order-robust — never
// index into rows by position). App parseRows modules build their typed
// parsers on top of these.

export interface RowDataset {
  columns: string[];
  rows: unknown[][];
}

/** Strict numeric coercion: null/undefined/""/booleans/NaN/±Infinity → null. */
export function finite(value: unknown): number | null {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function rowsToObjects(dataset?: RowDataset): Array<Record<string, unknown>> {
  if (!dataset) return [];
  return dataset.rows.map((row) => Object.fromEntries(dataset.columns.map((name, i) => [name, row[i]])));
}
