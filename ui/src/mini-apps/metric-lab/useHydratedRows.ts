// Central row hydration: pages a dataset's rows from the server (via
// get_mini_app_rows) up to a hard cap, starting from the preview rows already
// embedded in the descriptor. One hook instance per dataset key — every
// consumer (chart, table, analysis) reads the SAME array, so nothing
// double-fetches.

import { useEffect, useMemo, useRef, useState } from "react";
import type { DatasetDescriptor, PageRowsResponse } from "../shared/miniAppTypes";

const ROW_HYDRATION_CAP = 5000;

export interface HydratedDataset {
  rows: unknown[][];
  columns: string[];
  columnTypes: string[];
  hydrating: boolean;
  /** True when the server had more rows than the cap allowed. */
  truncated: boolean;
}

type FetchRows = (
  viewId: string,
  datasetKey: string,
  pageToken?: string,
) => Promise<PageRowsResponse | null>;

export function useHydratedRows(
  viewId: string | undefined,
  dataset: DatasetDescriptor | undefined,
  fetchRows: FetchRows,
): HydratedDataset {
  const columns = useMemo(
    () => (dataset?.columns ?? []).map((c) => c.name),
    [dataset],
  );
  const columnTypes = useMemo(
    () => (dataset?.columns ?? []).map((c) => c.type),
    [dataset],
  );

  const [rows, setRows] = useState<unknown[][]>(dataset?.preview_rows ?? []);
  const [hydrating, setHydrating] = useState(false);
  const [truncated, setTruncated] = useState(false);

  // Dataset identity: a new INITIAL_LOAD swaps the descriptor (new sql/stats),
  // which restarts hydration from its preview rows.
  const identity = `${viewId ?? ""}|${dataset?.key ?? ""}|${dataset?.sql ?? ""}|${
    dataset?.stats?.row_count ?? 0
  }`;

  const fetchRef = useRef(fetchRows);
  fetchRef.current = fetchRows;

  useEffect(() => {
    let cancelled = false;
    const preview = dataset?.preview_rows ?? [];
    setRows(preview);
    setTruncated(false);

    const totalAvailable = dataset?.stats?.row_count ?? preview.length;
    let token = dataset?.page_token ?? "";
    if (!viewId || !dataset || !token || preview.length >= totalAvailable) {
      setHydrating(false);
      return;
    }

    setHydrating(true);
    (async () => {
      let acc = preview;
      while (!cancelled && token && acc.length < ROW_HYDRATION_CAP) {
        const page = await fetchRef.current(viewId, dataset.key, token);
        if (cancelled || !page) break;
        acc = acc.concat(page.rows ?? []);
        setRows(acc);
        token = page.next_page_token ?? "";
      }
      if (!cancelled) {
        setTruncated(Boolean(token) && acc.length >= ROW_HYDRATION_CAP);
        setHydrating(false);
      }
    })().catch(() => {
      if (!cancelled) setHydrating(false);
    });

    return () => {
      cancelled = true;
    };
    // identity captures every dataset-swap signal; fetchRows via ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity]);

  return { rows, columns, columnTypes, hydrating, truncated };
}
