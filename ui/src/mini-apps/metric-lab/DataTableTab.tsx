// Data table over the centrally hydrated rows (no separate fetching).

import { useState } from "react";

const PAGE = 100;

interface DataTableTabProps {
  rows: unknown[][];
  columns: string[];
  totalAvailable: number;
  hydrating: boolean;
}

export function DataTableTab({ rows, columns, totalAvailable, hydrating }: DataTableTabProps) {
  const [shown, setShown] = useState(PAGE);
  const visible = rows.slice(0, shown);

  return (
    <div className="mlab-table-tab">
      <div className="mini-app-table-wrap mlab-table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr key={i}>
                {columns.map((_, j) => (
                  <td key={j}>{formatCell(row[j])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mlab-table-foot">
        <span>
          Showing {Math.min(shown, rows.length).toLocaleString()} of{" "}
          {rows.length.toLocaleString()} loaded
          {totalAvailable > rows.length
            ? ` (${totalAvailable.toLocaleString()} in dataset)`
            : ""}
          {hydrating ? " — loading more…" : ""}
        </span>
        {shown < rows.length && (
          <button type="button" className="mlab-loadmore" onClick={() => setShown((s) => s + PAGE * 5)}>
            Show more
          </button>
        )}
      </div>
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}
