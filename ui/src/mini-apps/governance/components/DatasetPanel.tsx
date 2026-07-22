import type { ReactNode } from "react";
import { MaSection, MaSkeletonRows } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { HydrationPhase } from "../../shared/useHydratedDatasets";
import { datasetDisplayState, groupBannerState } from "../model/datasetState";
import { datasetError } from "../../shared/datasetError";

// Uniform per-dataset chrome: every panel renders the same
// empty/loading/failed/truncated/stale states from the shared
// datasetDisplayState mapper, with a per-panel Retry wired to the group
// loader. A failed dataset stays visible as an explicit error card — it
// never silently vanishes.

export function DatasetPanel({
  title,
  descriptor,
  groupLoaded,
  hydrationPhase = "idle",
  hydrationError,
  onRetry,
  meta,
  emptyLabel = "No matching rows.",
  children,
}: {
  title: string;
  descriptor?: DatasetDescriptor;
  groupLoaded?: boolean | "partial";
  hydrationPhase?: HydrationPhase;
  hydrationError?: string | null;
  onRetry?: () => void;
  meta?: ReactNode;
  emptyLabel?: string;
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
        <div className="gov-panel-error" role="alert">
          <div>
            <strong>This dataset failed to load.</strong>
            <span>{datasetError(descriptor) || hydrationError || "Query failed."}</span>
          </div>
          {onRetry && <button type="button" onClick={onRetry}>Retry</button>}
        </div>
      );
      break;
    case "empty":
      body = <div className="gov-empty">{emptyLabel}</div>;
      break;
    case "truncated":
      body = (
        <>
          <div className="gov-ribbon" role="status">
            Result capped at the newest exact 10,000 rows — narrow filters for the full set.
          </div>
          {children}
        </>
      );
      break;
    case "stale":
      body = (
        <>
          <div className="gov-ribbon" role="status">
            <span className="gov-stale-badge">STALE</span> The latest ingestion for this source is older than 24 hours.
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

/** Group-level banner: the CoW `"partial"` sentinel means the group loaded
 * but at least one of its datasets shipped a failure stub — surface an amber
 * strip with a group Retry (per-dataset error cards name the failures). */
export function GroupBanner({ groupLoaded, onRetry }: {
  groupLoaded: boolean | "partial" | undefined;
  onRetry?: () => void;
}) {
  if (groupBannerState(groupLoaded) !== "partial") return null;
  return (
    <div className="gov-group-banner" role="status">
      <span>Some datasets in this group failed to load.</span>
      {onRetry && <button type="button" onClick={onRetry}>Retry group</button>}
    </div>
  );
}
