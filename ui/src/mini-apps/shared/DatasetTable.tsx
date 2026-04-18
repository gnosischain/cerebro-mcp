import { useEffect, useState } from "react";
import type { DatasetDescriptor } from "./miniAppTypes";

interface PageRowsResponse {
  rows: unknown[][];
  next_page_token: string;
}

interface Props {
  dataset?: DatasetDescriptor;
  datasetKey: string;
  emptyLabel: string;
  viewId: string;
  fetchRows: (
    viewId: string,
    datasetKey: string,
    pageToken?: string,
  ) => Promise<PageRowsResponse | null>;
  compact?: boolean;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

export function DatasetTable({
  dataset,
  datasetKey,
  emptyLabel,
  viewId,
  fetchRows,
  compact = false,
}: Props) {
  const [rows, setRows] = useState<unknown[][]>(dataset?.preview_rows ?? []);
  const [nextPageToken, setNextPageToken] = useState<string>(dataset?.page_token ?? "");
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    setRows(dataset?.preview_rows ?? []);
    setNextPageToken(dataset?.page_token ?? "");
    setLoadingMore(false);
  }, [dataset, datasetKey]);

  if (!dataset || dataset.columns.length === 0 || rows.length === 0) {
    return <div className="mini-app-unavailable">{emptyLabel}</div>;
  }

  const loadMore = async () => {
    if (!nextPageToken || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await fetchRows(viewId, datasetKey, nextPageToken);
      if (!page) return;
      setRows((current) => [...current, ...page.rows]);
      setNextPageToken(page.next_page_token);
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className={compact ? "mini-app-table-wrap mini-app-table-wrap--compact" : "mini-app-table-wrap"}>
      <table>
        <thead>
          <tr>
            {dataset.columns.map((column) => (
              <th key={column.name}>{column.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}-${datasetKey}`}>
              {dataset.columns.map((column, columnIndex) => (
                <td key={`${rowIndex}-${column.name}`}>{formatCell(row[columnIndex])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {nextPageToken ? (
        <div className="mini-app-table-footer">
          <button type="button" className="mini-app-toolbar-btn" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
