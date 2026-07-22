import { useState } from "react";
import { AsyncButton } from "../../shared/AsyncButton";
import { ToastStack } from "../../shared/ToastStack";
import type { DatasetDescriptor, PageRowsResponse } from "../../shared/miniAppTypes";
import { exportGovernanceCsv } from "../model/csv";

const EXPORT_ROW_CAP = 10_000;

type FetchRows = (
  viewId: string,
  datasetKey: string,
  pageToken?: string,
) => Promise<PageRowsResponse | null>;

// CSV export: hydrates the target dataset fully on demand (pages through the
// server up to the 10k cap), then hands columns+rows to the shared
// spreadsheet-safe CSV builder. The button is disabled while hydration runs;
// a capped export gets a `_truncated` filename suffix and a toast.

export function ExportCsvButton({ viewId, datasetKey, descriptor, fetchRows, scope, label = "Export CSV", excludeColumns = [] }: {
  viewId: string;
  datasetKey: string;
  descriptor?: DatasetDescriptor;
  fetchRows: FetchRows;
  scope: string;
  label?: string;
  /** Columns dropped from the export (e.g. raw/cooked post bodies — forum
   * exports carry plain text only). */
  excludeColumns?: string[];
}) {
  const [notes, setNotes] = useState<string[]>([]);

  const onExport = async () => {
    if (!descriptor) return;
    let rows: unknown[][] = [...(descriptor.preview_rows ?? [])];
    let token = descriptor.page_token ?? "";
    while (token && rows.length < EXPORT_ROW_CAP) {
      const page = await fetchRows(viewId, datasetKey, token);
      if (!page) {
        setNotes([`Export aborted: hydration failed after ${rows.length.toLocaleString()} rows.`]);
        return;
      }
      rows = rows.concat(page.rows ?? []);
      const next = page.next_page_token ?? "";
      if (next === token) break; // no progress — bail rather than loop
      token = next;
    }
    const truncated =
      Boolean(descriptor.stats?.truncated)
      || (rows.length >= EXPORT_ROW_CAP && Boolean(token));
    rows = rows.slice(0, EXPORT_ROW_CAP);

    const excluded = new Set(excludeColumns);
    const keptIndexes = descriptor.columns
      .map((column, index) => ({ column, index }))
      .filter(({ column }) => !excluded.has(column.name));
    const columns = keptIndexes.map(({ column }) => column.name);
    const columnTypes = keptIndexes.map(({ column }) => column.type);
    const exportRows = excluded.size > 0
      ? rows.map((row) => keptIndexes.map(({ index }) => row[index]))
      : rows;

    const filename = exportGovernanceCsv({
      scope,
      datasetKey,
      timestamp: new Date().toISOString().slice(0, 19),
      columns,
      rows: exportRows,
      columnTypes,
      truncated,
    });
    setNotes(truncated
      ? [`Export capped at ${EXPORT_ROW_CAP.toLocaleString()} rows — saved as ${filename}.`]
      : []);
  };

  return (
    <>
      <AsyncButton
        variant="secondary"
        loadingLabel="Exporting"
        disabled={!descriptor || (descriptor.preview_rows ?? []).length === 0}
        onClick={onExport}
      >
        {label}
      </AsyncButton>
      <ToastStack warnings={notes} />
    </>
  );
}
