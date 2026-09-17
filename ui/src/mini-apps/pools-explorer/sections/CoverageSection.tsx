import { useMemo } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { DatasetInfo } from "../components/InfoPopover";
import { KpiRow } from "../components/KpiRow";
import { PlxTable } from "../components/PlxTable";
import { checksSummaryOption, coverageCalendarOption, metadataGapOption } from "../model/chartOptions";
import { fmtDate, fmtInt } from "../model/format";
import { parseCalendar, parseCoverageSummary, parseMetadataGap } from "../model/parseRows";
import { GroupGate, useDataset, type PlxViewContext } from "./common";

// Coverage: what the indexer actually published, per job and per day, the
// days it missed, and how thin token metadata is. This is the honesty panel —
// every other view's completeness should be judged against it.

const SRC = "rpc_state_indexer";

export function CoverageSection({ ctx }: { ctx: PlxViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const summaryDs = useDataset(ctx, "coverage_summary");
  const summary = useMemo(() => parseCoverageSummary(summaryDs), [summaryDs]);
  const calendarDs = useDataset(ctx, "publication_calendar");
  const calendar = useMemo(() => parseCalendar(calendarDs), [calendarDs]);
  const calendarSpec = useMemo(() => coverageCalendarOption(calendar), [calendar]);
  const checksSpec = useMemo(() => checksSummaryOption(calendar), [calendar]);
  const gapDs = useDataset(ctx, "metadata_gap");
  const gapSpec = useMemo(() => metadataGapOption(parseMetadataGap(gapDs)), [gapDs]);
  const retry = (group: string) => () => ctx.retryGroup("coverage", group);

  return (
    <>
      <GroupGate ctx={ctx} section="coverage" group="core">
        <DatasetPanel
          title="Indexer jobs"
          descriptor={ctx.descriptors.coverage_summary}
          groupLoaded={groups["coverage.core"]}
          onRetry={retry("core")}
          meta={<DatasetInfo datasetKey="coverage_summary" descriptor={ctx.descriptors.coverage_summary} />}
        >
          {summary.map((job) => (
            <div key={job.job} className="plx-job">
              <div className="plx-job__name">{job.job}</div>
              <KpiRow
                items={[
                  { label: "First snapshot", value: fmtDate(job.firstSnapshotDate) },
                  { label: "Last snapshot", value: fmtDate(job.lastSnapshotDate) },
                  { label: "Days published", value: fmtInt(job.daysPublished) },
                  { label: "Pools configured", value: fmtInt(job.poolsConfigured) },
                  { label: "Published (latest)", value: fmtInt(job.poolsPublishedLatest) },
                  { label: "Below threshold (latest)", value: job.poolsBelowThresholdLatest === null ? "n/a" : fmtInt(job.poolsBelowThresholdLatest) },
                  { label: "Publications", value: fmtInt(job.publicationsTotal) },
                ]}
              />
            </div>
          ))}
        </DatasetPanel>
        <div className="plx-grid-2">
          <DatasetPanel
            title="Pools published per day"
            descriptor={ctx.descriptors.publication_calendar}
            groupLoaded={groups["coverage.core"]}
            hydrationPhase={ctx.hydrated.publication_calendar?.phase}
            hydrationError={ctx.hydrated.publication_calendar?.error}
            onRetry={retry("core")}
            meta={<DatasetInfo datasetKey="publication_calendar" descriptor={ctx.descriptors.publication_calendar} />}
          >
            <ChartCard chartId="plx-calendar" hideId sql={ctx.descriptors.publication_calendar?.sql} sourceModel={SRC} spec={calendarSpec} />
          </DatasetPanel>
          <DatasetPanel
            title="Integrity checks per day (CL job)"
            descriptor={ctx.descriptors.publication_calendar}
            groupLoaded={groups["coverage.core"]}
            hydrationPhase={ctx.hydrated.publication_calendar?.phase}
            hydrationError={ctx.hydrated.publication_calendar?.error}
            onRetry={retry("core")}
          >
            <ChartCard chartId="plx-checks" hideId spec={checksSpec} />
            <div className="plx-hint">Below-threshold pools are state-only: probed = published − below threshold.</div>
          </DatasetPanel>
        </div>
      </GroupGate>

      <GroupGate ctx={ctx} section="coverage" group="gaps">
        <GroupBanner groupLoaded={groups["coverage.gaps"]} onRetry={retry("gaps")} />
        <div className="plx-grid-2">
          <DatasetPanel
            title="Missing and partial days"
            descriptor={ctx.descriptors.missing_days}
            groupLoaded={groups["coverage.gaps"]}
            onRetry={retry("gaps")}
            emptyLabel="No missing or partial publication days."
            meta={<DatasetInfo datasetKey="missing_days" descriptor={ctx.descriptors.missing_days} />}
          >
            <PlxTable
              datasetKey="missing_days"
              descriptor={ctx.descriptors.missing_days}
              viewId={ctx.viewId}
              fetchRows={ctx.fetchRows}
              maxHeight="360px"
            />
          </DatasetPanel>
          <DatasetPanel
            title="Token metadata coverage"
            descriptor={ctx.descriptors.metadata_gap}
            groupLoaded={groups["coverage.gaps"]}
            hydrationPhase={ctx.hydrated.metadata_gap?.phase}
            hydrationError={ctx.hydrated.metadata_gap?.error}
            onRetry={retry("gaps")}
            meta={<DatasetInfo datasetKey="metadata_gap" descriptor={ctx.descriptors.metadata_gap} />}
          >
            <ChartCard chartId="plx-metadata" hideId sql={ctx.descriptors.metadata_gap?.sql} sourceModel={SRC} spec={gapSpec} />
            <div className="plx-hint">Everything not covered here renders in raw units — nothing is guessed.</div>
          </DatasetPanel>
        </div>
      </GroupGate>
    </>
  );
}
