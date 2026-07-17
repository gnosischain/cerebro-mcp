// Hydrates EVERY dataset attached to the view (not just primary/secondary)
// into a Record keyed by dataset key — one shared array per dataset for
// chart panels, tables, graph canvases, SQL drafts, and analysis.
// Re-hydration keys on the server's per-dataset REVISION (bumped on every
// attach/replace), not on SQL text: the same SQL re-run with different bound
// parameters, or a forced rerun, must still refetch.
//
// `rowCap` bounds how many rows are paged in per dataset (default 5000).
// Row-hungry consumers (e.g. the Graph Explorer node/edge datasets) pass a
// higher cap; `truncated` flags datasets that still had more rows server-side.

import { useEffect, useRef, useState } from "react";
import type { DatasetDescriptor, PageRowsResponse } from "./miniAppTypes";

const DEFAULT_ROW_CAP = 5000;

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

function fromDescriptor(d: DatasetDescriptor): HydratedDataset {
  return {
    rows: d.preview_rows ?? [],
    columns: (d.columns ?? []).map((c) => c.name),
    columnTypes: (d.columns ?? []).map((c) => c.type),
    hydrating: false,
    truncated: false,
  };
}

export function useHydratedDatasets(
  viewId: string | undefined,
  descriptors: Record<string, DatasetDescriptor> | undefined,
  revisions: Record<string, number> | undefined,
  fetchRows: FetchRows,
  rowCap: number = DEFAULT_ROW_CAP,
): Record<string, HydratedDataset> {
  const [datasets, setDatasets] = useState<Record<string, HydratedDataset>>({});

  const fetchRef = useRef(fetchRows);
  fetchRef.current = fetchRows;

  // One identity string covering every dataset's swap signal.
  const identity = `${viewId ?? ""}|${rowCap}|${Object.entries(descriptors ?? {})
    .map(([key, d]) => `${key}:${revisions?.[key] ?? 0}:${d.stats?.row_count ?? 0}`)
    .sort()
    .join("|")}`;

  useEffect(() => {
    let cancelled = false;
    const entries = Object.entries(descriptors ?? {});
    const initial: Record<string, HydratedDataset> = {};
    for (const [key, d] of entries) {
      initial[key] = fromDescriptor(d);
    }
    setDatasets(initial);
    if (!viewId) return;

    for (const [key, d] of entries) {
      const preview = d.preview_rows ?? [];
      const totalAvailable = d.stats?.row_count ?? preview.length;
      let token = d.page_token ?? "";
      if (!token || preview.length >= totalAvailable) continue;

      setDatasets((prev) => ({
        ...prev,
        [key]: { ...(prev[key] ?? fromDescriptor(d)), hydrating: true },
      }));
      (async () => {
        let acc = preview;
        while (!cancelled && token && acc.length < rowCap) {
          const page = await fetchRef.current(viewId, key, token);
          if (cancelled || !page) break;
          acc = acc.concat(page.rows ?? []);
          const rows = acc;
          setDatasets((prev) => ({
            ...prev,
            [key]: { ...(prev[key] ?? fromDescriptor(d)), rows },
          }));
          token = page.next_page_token ?? "";
        }
        if (!cancelled) {
          setDatasets((prev) => ({
            ...prev,
            [key]: {
              ...(prev[key] ?? fromDescriptor(d)),
              hydrating: false,
              truncated: Boolean(token) && acc.length >= rowCap,
            },
          }));
        }
      })().catch(() => {
        if (!cancelled) {
          setDatasets((prev) => ({
            ...prev,
            [key]: { ...(prev[key] ?? fromDescriptor(d)), hydrating: false },
          }));
        }
      });
    }

    return () => {
      cancelled = true;
    };
    // identity captures every dataset-swap signal; fetchRows via ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity]);

  return datasets;
}
