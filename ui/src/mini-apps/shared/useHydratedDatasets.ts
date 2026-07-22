// Hydrates EVERY dataset attached to the view (not just primary/secondary)
// into a Record keyed by dataset key — one shared array per dataset for
// chart panels, tables, graph canvases, SQL drafts, and analysis.
// Re-hydration keys on the server's per-dataset REVISION (bumped on every
// attach/replace), not on SQL text: the same SQL re-run with different bound
// parameters, or a forced rerun, must still refetch.
//
// PER-KEY lifecycles: each dataset hydrates independently. A revision bump on
// one key (e.g. edge_evidence refreshed by a selection) restarts hydration for
// THAT key only — other keys keep their fully-hydrated rows untouched. Stale
// pages from a superseded loop are dropped by per-key identity mismatch.
//
// `rowCap` bounds how many rows are paged in per dataset (default 5000).
// Row-hungry consumers (e.g. the Graph Explorer node/edge datasets) pass a
// higher cap; `truncated` flags datasets that still had more rows server-side.
//
// `publish` controls how often accumulated pages are published to state:
//   "every-page" (default) — current behavior, one re-render per 500-row page.
//   "geometric"           — page 1 immediately, then only when the row count
//                            doubles (1k→2k→4k→8k…), always on completion.
//                            Row-hungry consumers avoid ~90 re-renders (and
//                            downstream model rebuilds) per large hydration.

import { useEffect, useRef, useState } from "react";
import type { DatasetDescriptor, PageRowsResponse } from "./miniAppTypes";

const DEFAULT_ROW_CAP = 5000;

export type HydrationPublish = "every-page" | "geometric";
export type HydrationPhase = "idle" | "loading" | "complete" | "failed";

export interface HydratedDataset {
  rows: unknown[][];
  columns: string[];
  columnTypes: string[];
  phase: HydrationPhase;
  rowsLoaded: number;
  /** Server-advertised row count. Null means the server did not provide one. */
  rowsExpected: number | null;
  error: string | null;
  /** Compatibility alias. Prefer `phase === "loading"` in new code. */
  hydrating: boolean;
  /** True when the server had more rows than the cap allowed. */
  truncated: boolean;
}

type FetchRows = (
  viewId: string,
  datasetKey: string,
  pageToken?: string,
  options?: {
    datasetRevision?: number;
    pageSize?: number;
  },
) => Promise<PageRowsResponse | null>;

export type HydrationRowCap =
  | number
  | ((datasetKey: string, descriptor: DatasetDescriptor) => number);

function resolveRowCap(
  policy: HydrationRowCap,
  key: string,
  descriptor: DatasetDescriptor,
): number {
  const candidate =
    typeof policy === "function" ? policy(key, descriptor) : policy;
  return Number.isFinite(candidate) && candidate > 0
    ? Math.floor(candidate)
    : DEFAULT_ROW_CAP;
}

/** Unique per-run loop handle; compared by OBJECT identity (StrictMode-safe —
 * equal identity strings across remounts must not revive stale loops). */
interface LoopToken {
  identity: string;
}

function descriptorExpectedRows(d: DatasetDescriptor): number | null {
  const count = Number(d.stats?.row_count);
  return Number.isFinite(count) && count >= 0 ? count : null;
}

function fromDescriptor(
  d: DatasetDescriptor,
  phase: HydrationPhase = "idle",
  truncated = false,
): HydratedDataset {
  const rows = d.preview_rows ?? [];
  return {
    rows,
    columns: (d.columns ?? []).map((c) => c.name),
    columnTypes: (d.columns ?? []).map((c) => c.type),
    phase,
    rowsLoaded: rows.length,
    rowsExpected: descriptorExpectedRows(d),
    error: null,
    hydrating: phase === "loading",
    truncated,
  };
}

function hydrationError(err: unknown): string {
  return err instanceof Error ? err.message : String(err || "Dataset hydration failed");
}

export function useHydratedDatasets(
  viewId: string | undefined,
  descriptors: Record<string, DatasetDescriptor> | undefined,
  revisions: Record<string, number> | undefined,
  fetchRows: FetchRows,
  rowCap: HydrationRowCap = DEFAULT_ROW_CAP,
  publish: HydrationPublish = "every-page",
): Record<string, HydratedDataset> {
  const [datasets, setDatasets] = useState<Record<string, HydratedDataset>>({});

  const fetchRef = useRef(fetchRows);
  fetchRef.current = fetchRows;
  // Per-key live loop registry. Values are UNIQUE TOKEN OBJECTS (not identity
  // strings): a loop is stale iff `loops.get(key) !== myToken` by object
  // identity. This is what makes the hook StrictMode-safe — the dev
  // double-mount clears the registry (below) and the remount mints NEW token
  // objects for the SAME identity strings, so the first mount's loops read as
  // stale and die instead of running as duplicates.
  const loopsRef = useRef<Map<string, LoopToken>>(new Map());
  // Unmount (and StrictMode's simulated unmount): clearing the registry
  // orphans every in-flight loop. Deliberately NOT in the main effect —
  // its re-runs on unrelated identity changes must not kill unchanged keys.
  useEffect(
    () => () => {
      loopsRef.current.clear();
    },
    [],
  );

  // Combined signal so the effect runs whenever ANY key's swap signal changes;
  // inside, each key is diffed against its own previous identity.
  const identity = `${viewId ?? ""}|${Object.entries(descriptors ?? {})
    .map(([key, d]) => `${key}:${resolveRowCap(rowCap, key, d)}:${
      revisions?.[key] ?? 0
    }:${d.stats?.row_count ?? 0}`)
    .sort()
    .join("|")}`;

  useEffect(() => {
    const entries = Object.entries(descriptors ?? {});
    const liveKeys = new Set(entries.map(([key]) => key));
    const loops = loopsRef.current;

    // Drop keys that no longer exist on the view.
    for (const key of Array.from(loops.keys())) {
      if (!liveKeys.has(key)) loops.delete(key);
    }
    setDatasets((prev) => {
      let changed = false;
      const next: Record<string, HydratedDataset> = {};
      for (const k of Object.keys(prev)) {
        if (liveKeys.has(k)) next[k] = prev[k];
        else changed = true;
      }
      return changed ? next : prev;
    });

    for (const [key, d] of entries) {
      const keyRowCap = resolveRowCap(rowCap, key, d);
      const datasetRevision = revisions?.[key] ?? 0;
      const keyIdentity = `${viewId ?? ""}|${key}|${keyRowCap}|${
        revisions?.[key] ?? 0
      }|${d.stats?.row_count ?? 0}`;
      // Unchanged key: keep its rows and any in-flight loop as-is.
      if (loops.get(key)?.identity === keyIdentity) continue;
      const myToken: LoopToken = { identity: keyIdentity };
      loops.set(key, myToken);

      const preview = d.preview_rows ?? [];
      const expected = descriptorExpectedRows(d);
      const totalAvailable = expected ?? preview.length;
      let token = d.page_token ?? "";
      const canHydrate = Boolean(
        viewId &&
          token &&
          preview.length < totalAvailable &&
          preview.length < keyRowCap,
      );
      const cappedRemainder =
        preview.length < totalAvailable && preview.length >= keyRowCap;
      // A descriptor that promises more rows but supplies neither those rows
      // nor a continuation token is a broken hydration contract, not a
      // successful short dataset. Treating it as complete can make forensic
      // consumers publish COMPLETE while evidence is silently missing.
      const missingInitialToken =
        Boolean(viewId) &&
        preview.length < totalAvailable &&
        preview.length < keyRowCap &&
        !token;
      const initialPhase: HydrationPhase = !viewId
        ? "idle"
        : missingInitialToken
          ? "failed"
        : canHydrate
          ? "loading"
          : "complete";

      // (Re)start this key from its descriptor preview. A descriptor that is
      // already complete never flashes through a false loading state.
      setDatasets((prev) => ({
        ...prev,
        [key]: {
          ...fromDescriptor(d, initialPhase, cappedRemainder),
          error: missingInitialToken
            ? `Hydration token missing for ${key}: loaded ${preview.length} of ${totalAvailable}`
            : null,
        },
      }));
      if (!canHydrate || !viewId) continue;

      // Object-identity staleness: covers supersession (new token for the
      // key), unmount, AND StrictMode remounts (registry cleared + re-minted).
      const stale = () => loops.get(key) !== myToken;
      (async () => {
        let acc = preview;
        let rowsExpected = expected;
        let lastPublished = Math.max(1, preview.length);
        let publishedOnce = false;
        try {
          while (!stale() && token && acc.length < keyRowCap) {
            const previousToken = token;
            const page = await fetchRef.current(viewId, key, token, {
              datasetRevision,
              pageSize: Math.min(5_000, keyRowCap - acc.length),
            });
            if (stale()) return;
            if (!page) throw new Error(`No hydration page returned for ${key}`);
            if (
              page.dataset_revision != null &&
              page.dataset_revision !== datasetRevision
            ) {
              throw new Error(
                `Dataset revision changed for ${key}: expected ${datasetRevision}, received ${page.dataset_revision}`,
              );
            }
            const pageRows = page.rows ?? [];
            acc = acc.concat(pageRows);
            token = page.next_page_token ?? "";
            const responseTotal = Number(page.total_rows);
            if (Number.isFinite(responseTotal) && responseTotal < acc.length) {
              throw new Error(
                `Hydration exceeded advertised total for ${key}: loaded ${acc.length} of ${responseTotal}`,
              );
            }
            if (Number.isFinite(responseTotal)) {
              rowsExpected = responseTotal;
            }
            if (token && token === previousToken) {
              throw new Error(`Hydration made no progress for ${key}`);
            }
            const shouldPublish =
              publish === "every-page" ||
              !publishedOnce ||
              acc.length >= lastPublished * 2;
            if (shouldPublish) {
              publishedOnce = true;
              lastPublished = acc.length;
              const rows = acc;
              setDatasets((prev) => ({
                ...prev,
                [key]: {
                  ...(prev[key] ?? fromDescriptor(d)),
                  rows,
                  phase: "loading",
                  rowsLoaded: rows.length,
                  rowsExpected,
                  error: null,
                  hydrating: true,
                },
              }));
            }
          }
          if (!stale()) {
            // A continuation chain may also end prematurely after one or more
            // valid pages. The advertised total is authoritative for the
            // hydration lifecycle: token exhaustion below it is a failure.
            // Reaching the configured row cap is the one intentional short
            // read and remains inspectable as a truncated complete dataset.
            if (
              !token &&
              rowsExpected != null &&
              acc.length < rowsExpected &&
              acc.length < keyRowCap
            ) {
              throw new Error(
                `Hydration ended early for ${key}: loaded ${acc.length} of ${rowsExpected}`,
              );
            }
            // Completion always publishes the full accumulation (covers any
            // tail withheld by the geometric policy) and clears `hydrating`.
            const rows = acc;
            setDatasets((prev) => ({
              ...prev,
              [key]: {
                ...(prev[key] ?? fromDescriptor(d)),
                rows,
                phase: "complete",
                rowsLoaded: rows.length,
                rowsExpected,
                error: null,
                hydrating: false,
                truncated:
                  acc.length >= keyRowCap &&
                  (Boolean(token) ||
                    (rowsExpected != null && acc.length < rowsExpected)),
              },
            }));
          }
        } catch (err) {
          if (stale()) return;
          // Partial pages remain inspectable, but the lifecycle is FAILED so
          // a consumer can never turn a transport failure into COMPLETE.
          const rows = acc;
          setDatasets((prev) => ({
            ...prev,
            [key]: {
              ...(prev[key] ?? fromDescriptor(d)),
              rows,
              phase: "failed",
              rowsLoaded: rows.length,
              rowsExpected,
              error: hydrationError(err),
              hydrating: false,
              truncated: false,
            },
          }));
        }
      })();
    }

    // No per-run cleanup: superseded loops die via token mismatch, and
    // unmount/StrictMode via the registry-clearing effect above.
    // identity captures every dataset-swap signal; fetchRows via ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, publish]);

  return datasets;
}
