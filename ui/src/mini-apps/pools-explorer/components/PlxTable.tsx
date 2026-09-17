// Curated wrapper around the shared PaginatedTable: applies the column policy
// (hidden helper columns, human labels) and dispatches composed cells by kind.
// Clicking a pool / token cell opens that entity.

import { useCallback, useMemo } from "react";

import { PaginatedTable } from "../../shared/PaginatedTable";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import { resolveColumnPolicy } from "../model/columns";
import type { TokenOverlay } from "../model/tokenOverlay";
import type { PlxEntityType } from "../types";
import type { FetchRows } from "../sections/common";
import { renderPlxCell, type CellContext } from "./cells";

interface Props {
  datasetKey: string;
  descriptor?: DatasetDescriptor;
  viewId: string;
  fetchRows: FetchRows;
  onEntity?: (entityType: PlxEntityType, identifier: string) => void;
  maxHeight?: string;
  emptyLabel?: string;
  /** Chain-state token metadata, threaded into every composed cell. */
  overlay?: TokenOverlay;
  /** The token entity this table belongs to (token_pools carries decimals but
   * not the token's own address). */
  entityAddress?: string;
  /** Fired after a "Load more" page lands: rows the preview never carried are
   * now on screen, so the token-overlay scope has changed. */
  onPageLoaded?: () => void;
}

export function PlxTable({
  datasetKey,
  descriptor,
  viewId,
  fetchRows,
  onEntity,
  maxHeight = "460px",
  emptyLabel,
  overlay,
  entityAddress,
  onPageLoaded,
}: Props) {
  const columnNames = useMemo(
    () => (descriptor?.columns ?? []).map((column) => column.name),
    [descriptor],
  );
  const policy = useMemo(
    () => resolveColumnPolicy(datasetKey, columnNames),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [datasetKey, columnNames.join("|")],
  );
  // Wraps the host's fetchRows so a page append can be observed WITHOUT
  // teaching the shared PaginatedTable about this app's overlay.
  const fetchPage = useCallback<FetchRows>(
    async (...args) => {
      const page = await fetchRows(...args);
      if (page) onPageLoaded?.();
      return page;
    },
    [fetchRows, onPageLoaded],
  );
  if (!descriptor) return null;
  const ctx: CellContext = {
    columnIndex: new Map(columnNames.map((name, index) => [name, index])),
    onEntity,
    overlay,
    entityAddress,
  };
  return (
    <div className="plx-table">
      <PaginatedTable
        dataset={descriptor}
        datasetKey={datasetKey}
        viewId={viewId}
        fetchRows={fetchPage}
        maxHeight={maxHeight}
        emptyLabel={emptyLabel ?? "No rows at this publication."}
        hiddenColumns={policy.hidden}
        columnLabels={policy.labels}
        showSourceFooter={false}
        renderCell={(column, value, row) => renderPlxCell(policy.kinds[column], column, value, row, ctx)}
        onCellClick={(column, value) => {
          const entity = policy.entities[column];
          if (!entity || !onEntity || value === null || value === undefined || value === "") return;
          if (Array.isArray(value)) return; // token lists open per chip
          onEntity(entity, String(value));
        }}
      />
    </div>
  );
}
