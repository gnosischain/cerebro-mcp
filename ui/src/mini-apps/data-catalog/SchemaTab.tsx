import { useMemo, useState } from "react";
import type { CatalogColumn } from "./types";

interface Props {
  columns: CatalogColumn[];
}

export function SchemaTab({ columns }: Props) {
  const [filter, setFilter] = useState("");

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return columns;
    return columns.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.data_type.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q),
    );
  }, [columns, filter]);

  if (columns.length === 0) {
    return <div className="dc-empty">No column schema documented for this model.</div>;
  }

  return (
    <div>
      <div className="dc-results-head">
        <span className="dc-results-count">
          {rows.length} of {columns.length} columns
        </span>
        <input
          className="ma-field-input"
          style={{ maxWidth: 240 }}
          placeholder="Filter columns…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter columns"
        />
      </div>
      <div className="dc-table-wrap">
        <table className="dc-table">
          <thead>
            <tr>
              <th style={{ width: "28%" }}>Column</th>
              <th style={{ width: "18%" }}>Type</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.name}>
                <td className="dc-col-name">{c.name}</td>
                <td className="dc-col-type">{c.data_type || "—"}</td>
                <td>{c.description || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
