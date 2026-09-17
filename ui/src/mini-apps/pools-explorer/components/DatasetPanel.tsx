import type { ReactNode } from "react";

import { MaSection, MaSkeletonRows } from "../../shared/MiniAppChrome";
import { datasetError } from "../../shared/datasetError";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { HydrationPhase } from "../../shared/useHydratedDatasets";
import { datasetDisplayState, groupBannerState } from "../model/datasetState";

// Uniform per-dataset chrome (governance copy): every panel renders the same
// empty/loading/failed/truncated/stale states from the shared mapper, with a
// per-panel Retry wired to the group loader. A FAILED DATASET STAYS VISIBLE
// as an explicit error card — it never silently vanishes.

export function DatasetPanel({
  title,
  descriptor,
  groupLoaded,
  hydrationPhase = "idle",
  hydrationError,
  onRetry,
  meta,
  emptyLabel = "No rows at this publication.",
  children,
}: {
  title: string;
  descriptor?: DatasetDescriptor;
  groupLoaded?: boolean | "partial";
  hydrationPhase?: HydrationPhase;
  hydrationError?: string | null;
  onRetry?: () => void;
  meta?: ReactNode;
  emptyLabel?: ReactNode;
  children: ReactNode;
}) {
  const state = datasetDisplayState(descriptor, groupLoaded, hydrationPhase);
  let body: ReactNode;
  switch (state) {
    case "loading":
      body = <MaSkeletonRows count={4} />;
      break;
    case "failed":
      body = (
        <div className="plx-panel-error" role="alert">
          <div>
            <strong>This dataset failed to load.</strong>
            <span>{datasetError(descriptor) || hydrationError || "Query failed."}</span>
          </div>
          {onRetry && <button type="button" onClick={onRetry}>Retry</button>}
        </div>
      );
      break;
    case "empty":
      body = <div className="plx-empty">{emptyLabel}</div>;
      break;
    case "truncated":
      body = (
        <>
          <div className="plx-ribbon" role="status">
            Result capped at 10,000 rows — narrow the filters for the full set.
          </div>
          {children}
        </>
      );
      break;
    case "stale":
      body = (
        <>
          <div className="plx-ribbon" role="status">
            <span className="plx-stale-badge">STALE</span> The latest publication for this source is more than two days old.
          </div>
          {children}
        </>
      );
      break;
    default:
      body = children;
  }
  return (
    <MaSection title={title} meta={meta}>
      {body}
    </MaSection>
  );
}

/** Group-level banner: the `"partial"` sentinel means the group loaded but at
 * least one of its datasets shipped a failure stub — an amber strip with a
 * group Retry (per-dataset error cards name the failures). */
export function GroupBanner({ groupLoaded, onRetry }: {
  groupLoaded: boolean | "partial" | undefined;
  onRetry?: () => void;
}) {
  if (groupBannerState(groupLoaded) !== "partial") return null;
  return (
    <div className="plx-group-banner" role="status">
      <span>Some datasets in this group failed to load.</span>
      {onRetry && <button type="button" onClick={onRetry}>Retry group</button>}
    </div>
  );
}
