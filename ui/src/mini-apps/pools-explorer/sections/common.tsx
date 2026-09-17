import { useMemo, type Dispatch, type ReactNode, type SetStateAction } from "react";

import { MaSkeletonKpiGrid, MaSkeletonRows } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor, PageRowsResponse } from "../../shared/miniAppTypes";
import type { RowDataset } from "../../shared/rowDataset";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import type { HeatmapWindow } from "../model/profileHeatmap";
import type { TokenOverlay } from "../model/tokenOverlay";
import type { PlxFilterDraft } from "../state/toolArgs";
import type { PlxEntityType, PlxListSection, PlxSection, PoolsExplorerViewState } from "../types";
import type { PlxClientState } from "../urlState";

export type FetchRows = (
  viewId: string,
  datasetKey: string,
  pageToken?: string,
  options?: { datasetRevision?: number; pageSize?: number },
) => Promise<PageRowsResponse | null>;

export interface ApplyOptions {
  window?: string;
  asOf?: string;
  forceRefresh?: boolean;
}

/** Shared wiring handed from PoolsExplorerApp to every section + detail view. */
export interface PlxViewContext {
  state: PoolsExplorerViewState;
  descriptors: Record<string, DatasetDescriptor>;
  hydrated: Record<string, HydratedDataset>;
  viewId: string;
  fetchRows: FetchRows;
  draft: PlxFilterDraft;
  setDraft: Dispatch<SetStateAction<PlxFilterDraft>>;
  /** Apply a list section with the given draft (defaults to the live draft). */
  apply: (section: PlxListSection, draftOverride?: PlxFilterDraft, opts?: ApplyOptions) => void;
  loading: boolean;
  onEntity: (entityType: PlxEntityType, identifier: string) => void;
  /** `${section}.${group}` keys whose deferred load failed client-side. */
  failedGroups: string[];
  retryGroup: (section: string, group: string) => void;
  openLink: (url: string) => void;
  /** PROFILE-DATE-HOOK: re-run only `pool.profile` at this date ("" = latest). */
  onLoadProfileDate: (date: string) => void;
  /** HEATMAP-HOOK: one additive `pool.heatmap` load for the window. */
  onLoadHeatmap: (window: HeatmapWindow, opts?: { force?: boolean }) => void;
  /** Client-only pool-detail state (tab / zoom / axis / view / orientation). */
  client: PlxClientState;
  setClient: (patch: Partial<PlxClientState>) => void;
  /** Chain-state token metadata patched in by `load_pools_token_metadata`.
   * Fills symbol / decimals holes ONLY — an indexer value always wins — and
   * everything it supplies renders with the `chain` marker. */
  overlay?: TokenOverlay;
  /** A table appended a page: the visible token set changed, so the overlay
   * scope key must move. */
  onPageLoaded?: () => void;
}

/** Prefer the fully hydrated rows; fall back to the descriptor preview. */
export function dataset(ctx: PlxViewContext, key: string): RowDataset | undefined {
  const hydrated = ctx.hydrated[key];
  if (hydrated) return { columns: hydrated.columns, rows: hydrated.rows };
  const descriptor = ctx.descriptors[key];
  if (!descriptor) return undefined;
  return { columns: descriptor.columns.map((column) => column.name), rows: descriptor.preview_rows };
}

/** dataset() with a stable identity: chart-option builders memoized on the
 * result only re-run when the underlying data reloads, so filter keystrokes
 * and other unrelated renders don't tear down and re-animate the charts. */
export function useDataset(ctx: PlxViewContext, key: string): RowDataset | undefined {
  const hydrated = ctx.hydrated[key];
  const descriptor = ctx.descriptors[key];
  return useMemo(() => {
    if (hydrated) return { columns: hydrated.columns, rows: hydrated.rows };
    if (!descriptor) return undefined;
    return { columns: descriptor.columns.map((column) => column.name), rows: descriptor.preview_rows };
  }, [hydrated, descriptor]);
}

export function groupKey(section: PlxSection, group: string): string {
  return `${section}.${group}`;
}

/** Client-side group failure / loading gate (mirrors CoW's GroupGate): a
 * group whose `load_pools_explorer_datasets` call itself failed renders an
 * explicit retry card; an unloaded group renders skeletons. Server-side
 * per-dataset failures are handled by DatasetPanel below this gate. */
export function GroupGate({ ctx, section, group, children }: {
  ctx: PlxViewContext;
  section: PlxSection;
  group: string;
  children: ReactNode;
}) {
  const key = groupKey(section, group);
  if (ctx.failedGroups.includes(key)) {
    return (
      <div className="plx-group-error" role="alert">
        <span>These datasets failed to load.</span>
        <button type="button" onClick={() => ctx.retryGroup(section, group)}>Retry</button>
      </div>
    );
  }
  if (ctx.state.loaded_groups?.[key] === false) {
    return (
      <div className="plx-skel" aria-busy="true" aria-label="Loading datasets">
        {group === "core" ? <MaSkeletonKpiGrid /> : null}
        <MaSkeletonRows count={group === "core" ? 4 : 6} />
      </div>
    );
  }
  return <>{children}</>;
}
