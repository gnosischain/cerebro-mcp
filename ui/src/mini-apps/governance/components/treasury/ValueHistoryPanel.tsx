import { useMemo, useRef, type ReactNode } from "react";

import { ChartCard } from "../../../../components/ChartCard";
import type { ChartSpec } from "../../../../types";
import type { DatasetDescriptor } from "../../../shared/miniAppTypes";
import type { HydrationPhase } from "../../../shared/useHydratedDatasets";
import { DatasetPanel } from "../DatasetPanel";

// A treasury chart inside the uniform dataset chrome. Three things it owns so
// no caller can forget them:
//
//   * The chart renders only once the dataset is FULLY hydrated. The hydration
//     hook publishes partial pages as they arrive, and a stack drawn from half
//     of the history reads as a collapse.
//   * `onEvents` is memoized with a stable identity (the handler is read
//     through a ref). echarts-for-react rebinds every listener whenever the
//     object changes, which on every render meant dropped clicks mid-rebind.
//   * When the month-coverage dataset is unavailable, the chart says gaps
//     cannot be marked, rather than silently drawing every month as complete.

export function ValueHistoryPanel({
  title,
  chartId,
  descriptor,
  groupLoaded,
  phase,
  error,
  onRetry,
  spec,
  sql,
  sourceModel,
  onSeriesClick,
  coverageKnown = true,
  meta,
  emptyLabel = "No history for this selection.",
  isEmpty = false,
  children,
}: {
  title: string;
  chartId: string;
  descriptor?: DatasetDescriptor;
  groupLoaded?: boolean | "partial";
  phase: HydrationPhase;
  error?: string | null;
  onRetry?: () => void;
  spec: ChartSpec | null;
  sql?: string;
  sourceModel?: string;
  /** Receives the clicked series id (band id). */
  onSeriesClick?: (seriesId: string, params: unknown) => void;
  coverageKnown?: boolean;
  meta?: ReactNode;
  emptyLabel?: string;
  /** The data loaded but this selection has nothing to draw. */
  isEmpty?: boolean;
  children?: ReactNode;
}) {
  const handler = useRef(onSeriesClick);
  handler.current = onSeriesClick;
  const onEvents = useMemo(() => ({
    click: (params: unknown) => {
      const id = (params as { seriesId?: unknown }).seriesId;
      if (typeof id === "string" && id) handler.current?.(id, params);
    },
  }), []);

  return (
    <DatasetPanel
      title={title}
      descriptor={descriptor}
      groupLoaded={groupLoaded}
      hydrationPhase={phase}
      hydrationError={error}
      onRetry={onRetry}
      meta={meta}
      emptyLabel={emptyLabel}
    >
      {!coverageKnown ? (
        <div className="gov-ribbon" role="status">
          Month completeness is unknown (the coverage dataset did not load), so incomplete
          months cannot be marked — a dip may be missing data rather than a disposal.
        </div>
      ) : null}
      {isEmpty || !spec ? (
        <div className="gov-empty">{emptyLabel}</div>
      ) : (
        <ChartCard
          chartId={chartId}
          hideId
          spec={spec}
          sql={sql}
          sourceModel={sourceModel}
          onEvents={onSeriesClick ? onEvents : undefined}
        />
      )}
      {children}
    </DatasetPanel>
  );
}
