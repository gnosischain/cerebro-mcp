import { useEffect, useState } from "react";
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
}: Props) {
  const [rows, setRows] = useState<unknown[][]>(dataset?.preview_rows ?? []);
  const [nextToken, setNextToken] = useState<string>(dataset?.page_token ?? "");

  useEffect(() => {
    setRows(dataset?.preview_rows ?? []);
    setNextToken(dataset?.page_token ?? "");
  }, [dataset, datasetKey]);

  if (!dataset || dataset.columns.length === 0 || rows.length === 0) {
    return <div className="mini-app-unavailable">{emptyLabel}</div>;
  }

  const total = dataset.stats?.row_count ?? rows.length;

  const loadMore = async () => {
    if (!nextToken) return;
    const page = await fetchRows(viewId, datasetKey, nextToken);
    if (!page) return;
    setRows((cur) => [...cur, ...page.rows]);
    setNextToken(page.next_page_token ?? "");
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
              {dataset.columns.map((c) => (
                <th key={c.name}>{c.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`${datasetKey}-${rowIndex}`}>
                {dataset.columns.map((col, colIndex) => (
                  <td key={col.name} title={String(row[colIndex] ?? "")}>
                    {formatCell(row[colIndex], col.type)}
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
    </div>
  );
}
