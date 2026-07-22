import { useMemo, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { MaKpi, MaKpiGrid, MaSkeletonKpiGrid, MaSkeletonRows } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor, PageRowsResponse } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import type { GovAggregates } from "../model/contextPrompt";
import type { RowDataset } from "../../shared/rowDataset";
import type { GovSectionId } from "../state/navigation";
import type { GovFilterDraft } from "../state/toolArgs";
import type { GovEntityType, GovernanceViewState } from "../types";

export type FetchRows = (
  viewId: string,
  datasetKey: string,
  pageToken?: string,
  options?: { datasetRevision?: number; pageSize?: number },
) => Promise<PageRowsResponse | null>;

/** Shared wiring handed from GovernanceApp to every section + detail view. */
export interface GovViewContext {
  state: GovernanceViewState;
  descriptors: Record<string, DatasetDescriptor>;
  hydrated: Record<string, HydratedDataset>;
  viewId: string;
  fetchRows: FetchRows;
  draft: GovFilterDraft;
  setDraft: Dispatch<SetStateAction<GovFilterDraft>>;
  /** Apply a section load with the given draft (defaults to the live draft). */
  apply: (section: GovSectionId, draftOverride?: GovFilterDraft) => void;
  loading: boolean;
  onEntity: (entityType: GovEntityType, identifier: string) => void;
  /** `${section}.${group}` keys whose deferred load failed client-side. */
  failedGroups: string[];
  retryGroup: (section: string, group: string) => void;
  openLink: (url: string) => void;
  sendMessage: (text: string) => Promise<boolean>;
  aggregates: GovAggregates;
}

/** Prefer the fully hydrated rows; fall back to the descriptor preview. */
export function dataset(ctx: GovViewContext, key: string): RowDataset | undefined {
  const hydrated = ctx.hydrated[key];
  if (hydrated) return { columns: hydrated.columns, rows: hydrated.rows };
  const descriptor = ctx.descriptors[key];
  if (!descriptor) return undefined;
  return { columns: descriptor.columns.map((column) => column.name), rows: descriptor.preview_rows };
}

/** dataset() with a stable identity: chart-option builders memoized on the
 * result only re-run when the underlying data reloads, so filter keystrokes
 * and other unrelated renders don't tear down and re-animate the charts
 * (ChartCard renders with notMerge + a 1s entry animation). */
export function useDataset(ctx: GovViewContext, key: string): RowDataset | undefined {
  const hydrated = ctx.hydrated[key];
  const descriptor = ctx.descriptors[key];
  return useMemo(() => {
    if (hydrated) return { columns: hydrated.columns, rows: hydrated.rows };
    if (!descriptor) return undefined;
    return { columns: descriptor.columns.map((column) => column.name), rows: descriptor.preview_rows };
  }, [hydrated, descriptor]);
}

/** First row of a (single-row) summary dataset as a name-keyed object. */
export function firstRow(ctx: GovViewContext, key: string): Record<string, unknown> | null {
  const ds = dataset(ctx, key);
  if (!ds || ds.rows.length === 0) return null;
  return Object.fromEntries(ds.columns.map((column, index) => [column, ds.rows[0][index]]));
}

export function fmtNum(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function fmtPct(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

export function pickNumber(row: Record<string, unknown> | null, keys: string[]): number | null {
  if (!row) return null;
  for (const key of keys) {
    if (row[key] !== undefined && row[key] !== null && row[key] !== "") {
      const n = Number(row[key]);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

export function pickString(row: Record<string, unknown> | null | undefined, keys: string[]): string {
  if (!row) return "";
  for (const key of keys) {
    const value = row[key];
    if (value !== undefined && value !== null && value !== "") return String(value);
  }
  return "";
}

/** Section KPI header row. */
export function KpiRow({ items, meta }: {
  items: Array<{ label: string; value: string }>;
  meta?: ReactNode;
}) {
  return (
    <div className="gov-kpi-head">
      <MaKpiGrid>
        {items.map((item) => <MaKpi key={item.label} label={item.label} value={item.value} />)}
      </MaKpiGrid>
      {meta ?? null}
    </div>
  );
}

/** Client-side group failure / loading gate (mirrors CoW's GroupGate): a
 * group whose `load_governance_datasets` call itself failed renders an
 * explicit retry card; an unloaded group renders skeletons. Server-side
 * per-dataset failures are handled by DatasetPanel below this gate. */
export function GroupGate({ ctx, section, group, children }: {
  ctx: GovViewContext;
  section: GovSectionId;
  group: string;
  children: ReactNode;
}) {
  const key = `${section}.${group}`;
  if (ctx.failedGroups.includes(key)) {
    return (
      <div className="gov-group-error" role="alert">
        <span>These datasets failed to load.</span>
        <button type="button" onClick={() => ctx.retryGroup(section, group)}>Retry</button>
      </div>
    );
  }
  if (ctx.state.loaded_groups?.[key] === false) {
    return (
      <div className="gov-skel" aria-busy="true" aria-label="Loading datasets">
        {group === "core" ? <MaSkeletonKpiGrid /> : null}
        <MaSkeletonRows count={group === "core" ? 4 : 6} />
      </div>
    );
  }
  return <>{children}</>;
}
