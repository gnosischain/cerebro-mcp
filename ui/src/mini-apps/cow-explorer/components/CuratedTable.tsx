// Curated wrapper around the shared PaginatedTable: applies the column policy
// (hidden helper columns, human labels) and dispatches composed cells by kind
// (token identity w/ overlay icon, chain badge, solver name, short hashes,
// normalized amounts w/ raw-units fallback). This is what kills the repeated
// `*_symbol` / `*_decimals` / `*_raw` columns everywhere at once.

import { useMemo } from "react";
import type { DatasetDescriptor, PageRowsResponse } from "../../shared/miniAppTypes";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { resolveColumnPolicy } from "../model/columns";
import type { CowExplorerViewState, EntityType } from "../types";
import { renderKindValue, rowChainId, type CellContext } from "./cells";

type FetchRows = (
  viewId: string,
  datasetKey: string,
  pageToken?: string,
) => Promise<PageRowsResponse | null>;

interface Props {
  datasetKey: string;
  descriptor?: DatasetDescriptor;
  state: CowExplorerViewState;
  viewId: string;
  fetchRows: FetchRows;
  onEntity: (entityType: EntityType, identifier: string, chainId?: number) => void;
  /** Pair datasets (token0+token1 columns): clicking the fills count opens
   * the pair's market history. */
  onSelectPair?: (base: string, quote: string, chainId?: number) => void;
  maxHeight?: string;
  emptyLabel?: string;
}

export function CuratedTable({
  datasetKey,
  descriptor,
  state,
  viewId,
  fetchRows,
  onEntity,
  onSelectPair,
  maxHeight = "430px",
  emptyLabel,
}: Props) {
  const columnNames = useMemo(
    () => (descriptor?.columns ?? []).map((column) => column.name),
    [descriptor],
  );
  const policy = useMemo(
    () => resolveColumnPolicy(datasetKey, columnNames),
    [datasetKey, columnNames.join("|")],
  );
  if (!descriptor) return null;
  const ctx: CellContext = {
    state,
    columnIndex: new Map(columnNames.map((name, index) => [name, index])),
  };
  return (
    <PaginatedTable
      dataset={descriptor}
      datasetKey={datasetKey}
      viewId={viewId}
      fetchRows={fetchRows}
      maxHeight={maxHeight}
      emptyLabel={emptyLabel ?? "No rows in the indexed window"}
      hiddenColumns={policy.hidden}
      columnLabels={policy.labels}
      showSourceFooter={false}
      renderCell={(column, value, row) =>
        renderKindValue(policy.kinds[column], column, value, row, ctx)
      }
      onCellClick={(column, value, row) => {
        const t0 = ctx.columnIndex.get("token0");
        const t1 = ctx.columnIndex.get("token1");
        if (
          onSelectPair
          && (column === "fill_count" || column === "settlement_transactions" || column === "trade_count")
          && t0 !== undefined && t1 !== undefined
        ) {
          const base = String(row[t0] ?? "");
          const quote = String(row[t1] ?? "");
          if (base && quote) {
            onSelectPair(base, quote, rowChainId(ctx, row));
            return;
          }
        }
        const entity = policy.entities[column];
        if (!entity || value === null || value === undefined || value === "") return;
        onEntity(entity, String(value), rowChainId(ctx, row));
      }}
    />
  );
}
