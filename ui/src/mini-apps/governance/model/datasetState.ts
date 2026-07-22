// Pure per-dataset display-state mapper. One place understands the server's
// warning-code contract so every panel renders the same chrome:
//
//   query_failed / coverage.error  -> "failed"   (stub descriptor, zero rows)
//   descriptor absent / group off  -> "loading"  (skeleton)
//   hydration in flight            -> "loading"
//   hydration failed               -> "failed"
//   result_truncated / truncated   -> "truncated" (data shown + ribbon)
//   source_stale                   -> "stale"     (data shown + stale badge)
//   no_data / zero rows            -> "empty"
//   otherwise                      -> "ready"
//
// `groupLoaded` is the `loaded_groups["section.group"]` value; the CoW
// `"partial"` sentinel means the group finished loading but at least one of
// its datasets shipped a failure stub — per-dataset codes still decide which
// dataset failed, so "partial" alone never forces a failed panel.

import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { HydrationPhase } from "../../shared/useHydratedDatasets";

export type DatasetDisplayState =
  | "empty"
  | "loading"
  | "failed"
  | "truncated"
  | "stale"
  | "ready";

interface CoverageLike {
  error?: string;
  truncated?: boolean;
  warning_codes?: string[];
}

function coverageOf(descriptor?: DatasetDescriptor): CoverageLike {
  const coverage = descriptor?.provenance?.coverage;
  if (!coverage || typeof coverage !== "object") return {};
  return coverage as CoverageLike;
}

export function datasetDisplayState(
  descriptor: DatasetDescriptor | undefined,
  groupLoaded: boolean | "partial" | undefined,
  hydrationPhase: HydrationPhase = "idle",
): DatasetDisplayState {
  if (!descriptor) {
    // No descriptor yet: skeleton while the group streams in; a group that
    // claims to be loaded but shipped no descriptor is a failure.
    return groupLoaded === true || groupLoaded === "partial" ? "failed" : "loading";
  }
  const coverage = coverageOf(descriptor);
  const codes = coverage.warning_codes ?? [];
  if (coverage.error || codes.includes("query_failed")) return "failed";
  if (hydrationPhase === "loading") return "loading";
  if (hydrationPhase === "failed") return "failed";
  const truncated =
    codes.includes("result_truncated")
    || coverage.truncated === true
    || descriptor.stats?.truncated === true;
  if (truncated) return "truncated";
  if (codes.includes("source_stale")) return "stale";
  const rowCount = descriptor.stats?.row_count ?? descriptor.preview_rows?.length ?? 0;
  if (codes.includes("no_data") || rowCount === 0) return "empty";
  return "ready";
}

/** Group-level banner state: `"partial"` -> the amber strip with a group
 * Retry; `false`/undefined -> group still loading; `true` -> nothing. */
export function groupBannerState(
  groupLoaded: boolean | "partial" | undefined,
): "loading" | "partial" | "none" {
  if (groupLoaded === "partial") return "partial";
  return groupLoaded === true ? "none" : "loading";
}
