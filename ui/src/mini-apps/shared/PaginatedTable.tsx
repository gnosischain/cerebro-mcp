import { useEffect, useState, type ReactNode } from "react";
import type { DatasetDescriptor, PageRowsResponse } from "./miniAppTypes";
import { AsyncButton } from "./AsyncButton";

interface Props {
  dataset?: DatasetDescriptor;
  datasetKey: string;
  viewId: string;
  fetchRows: (
    viewId: string,
    datasetKey: string,
    pageToken?: string,
  ) => Promise<PageRowsResponse | null>;
  emptyLabel?: string;
  maxHeight?: string;
  onCellClick?: (column: string, value: unknown, row: unknown[]) => void;
  hiddenColumns?: string[];
  renderCell?: (column: string, value: unknown, row: unknown[]) => ReactNode;
  sourceLabel?: string;
  showSourceFooter?: boolean;
  /** Optional display labels per column name (header falls back to the name). */
  columnLabels?: Record<string, string>;
}

function formatCell(value: unknown, type?: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    if (Math.abs(value) >= 1000)
      return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ");
  if (type === "DateTime" || type === "Date") return String(value);
  return String(value);
}

/**
 * Paginated table with visible "Loaded X of Y" footer and async load-more.
 * Supersedes DatasetTable — DatasetTable will be re-exported as a thin
 * wrapper during Stage 1 retrofits, then removed.
 */
export function PaginatedTable({
  dataset,
  datasetKey,
  viewId,
  fetchRows,
  emptyLabel = "No rows",
  maxHeight,
  onCellClick,
  hiddenColumns = [],
  renderCell,
  sourceLabel,
  showSourceFooter = true,
  columnLabels,
}: Props) {
  const [rows, setRows] = useState<unknown[][]>(dataset?.preview_rows ?? []);
  const [nextToken, setNextToken] = useState<string>(dataset?.page_token ?? "");
  const [error, setError] = useState("");

  useEffect(() => {
    setRows(dataset?.preview_rows ?? []);
    setNextToken(dataset?.page_token ?? "");
    setError("");
  }, [dataset, datasetKey]);

  if (!dataset || dataset.columns.length === 0 || rows.length === 0) {
    return <div className="mini-app-unavailable">{emptyLabel}</div>;
  }

  const total = Number(dataset.stats?.source_rows ?? dataset.stats?.row_count ?? rows.length);
  const hidden = new Set(hiddenColumns);
  const visibleColumns = dataset.columns
    .map((column, index) => ({ column, index }))
    .filter(({ column }) => !hidden.has(column.name));

  const loadMore = async () => {
    if (!nextToken) return;
    setError("");
    try {
      const page = await fetchRows(viewId, datasetKey, nextToken);
      if (!page) throw new Error("No page returned");
      setRows((cur) => [...cur, ...page.rows]);
      setNextToken(page.next_page_token ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch rows");
    }
  };

  return (
    <div className="ptable">
      <div
        className="ptable__scroll"
        style={maxHeight ? { maxHeight } : undefined}
      >
        <table>
          <thead>
            <tr>
              {visibleColumns.map(({ column: c }) => (
                <th key={c.name}>{columnLabels?.[c.name] ?? c.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`${datasetKey}-${rowIndex}`}>
                {visibleColumns.map(({ column: col, index: colIndex }) => (
                  <td
                    key={col.name}
                    title={String(row[colIndex] ?? "")}
                    className={onCellClick ? "ptable__cell--clickable" : undefined}
                    onClick={() => onCellClick?.(col.name, row[colIndex], row)}
                  >
                    {renderCell?.(col.name, row[colIndex], row) ?? formatCell(row[colIndex], col.type)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <footer className="ptable__footer">
        <span>
          Loaded {rows.length.toLocaleString()} of {total.toLocaleString()} rows
          {dataset.stats?.truncated ? " · newest exact capped result" : ""}
        </span>
        {nextToken && (
          <AsyncButton
            variant="secondary"
            loadingLabel="Fetching"
            onClick={loadMore}
          >
            Load more
          </AsyncButton>
        )}
      </footer>
      {error && <div className="ptable__error" role="alert">{error}</div>}
      {showSourceFooter && Boolean(sourceLabel || dataset.provenance?.coverage) && (
        <div className="ptable__source">
          {sourceLabel ?? "Indexed source"}
          {(() => {
            const coverage = dataset.provenance?.coverage as
              | { actual_start?: string | null; actual_end?: string | null; latest_source_observation?: string | null; fetched_at?: string | null }
              | undefined;
            if (!coverage) return null;
            const span = [coverage.actual_start, coverage.actual_end].filter(Boolean).join(" → ");
            const parts = [
              span,
              coverage.latest_source_observation ? `source observed ${coverage.latest_source_observation}` : "",
              coverage.fetched_at ? `fetched ${coverage.fetched_at}` : "",
            ].filter(Boolean);
            return parts.length ? ` · ${parts.join(" · ")}` : null;
          })()}
        </div>
      )}
    </div>
  );
}
