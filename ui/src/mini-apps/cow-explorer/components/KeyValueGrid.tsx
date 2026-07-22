// Single-row detail panel: renders the first row of a descriptor as a curated
// label/value grid (same column policy + composed cells as CuratedTable).
// Used by entity detail headers instead of dumping a one-row table.

import { useMemo } from "react";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import { resolveColumnPolicy } from "../model/columns";
import type { CowExplorerViewState } from "../types";
import { renderKindValue, type CellContext } from "./cells";

export function KeyValueGrid({ descriptor, state }: {
  descriptor?: DatasetDescriptor;
  state: CowExplorerViewState;
}) {
  const columnNames = useMemo(
    () => (descriptor?.columns ?? []).map((column) => column.name),
    [descriptor],
  );
  const policy = useMemo(
    () => resolveColumnPolicy(descriptor?.key ?? "", columnNames),
    [descriptor?.key, columnNames.join("|")],
  );
  const row = descriptor?.preview_rows?.[0];
  if (!descriptor || !row) {
    return <div className="cow-empty">No indexed record found.</div>;
  }
  const ctx: CellContext = {
    state,
    columnIndex: new Map(columnNames.map((name, index) => [name, index])),
  };
  const hidden = new Set(policy.hidden);
  return (
    <dl className="cow-kv">
      {columnNames.map((name, index) => {
        if (hidden.has(name)) return null;
        const value = row[index];
        if (value === null || value === undefined || value === "") return null;
        const rendered = renderKindValue(policy.kinds[name], name, value, row, ctx);
        return (
          <div key={name} className="cow-kv__item">
            <dt>{policy.labels[name] ?? name}</dt>
            <dd>{rendered ?? String(value)}</dd>
          </div>
        );
      })}
    </dl>
  );
}
