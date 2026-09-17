import { useMemo } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { SegmentedControl } from "../../shared/SegmentedControl";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { DatasetInfo } from "../components/InfoPopover";
import { KpiRow } from "../components/KpiRow";
import {
  classFeeMixOption, concentrationOption, livePoolTrendOption, probeCoverageOption, rangeWidthOption,
} from "../model/chartOptions";
import { fmtDate, fmtInt, fmtTime } from "../model/format";
import {
  parseClassFee, parseConcentrationSummary, parseLiveTrend, parsePoolsSummary, parseProbeCoverage,
  parseRangeWidth, parseSourceFreshness,
} from "../model/parseRows";
import { WINDOWS, type PlxWindow } from "../types";
import { dataset, GroupGate, useDataset, type PlxViewContext } from "./common";

// Overview: headline counts + the two publication clocks (core), the class ×
// fee mix and the probe split (mix), the whole-history live-pool trend
// (trend — its own group, the one all-pool scan), and concentration (last).

const SRC = "rpc_state_indexer";

function FreshnessStrip({ ctx }: { ctx: PlxViewContext }) {
  const rows = parseSourceFreshness(dataset(ctx, "source_freshness"));
  const stale = ctx.state.freshness ?? { cl_state: { stale: false }, reserves: { stale: false } };
  const label = (source: string) => (source === "cl_state" ? "CL state (daily_cl_liquidity)" : "Reserves (daily_pool_reserves)");
  const isStale = (source: string) => (source === "cl_state" ? stale.cl_state?.stale : stale.reserves?.stale);
  return (
    <div className="plx-freshness">
      {rows.map((row) => (
        <span key={row.source} className="plx-fresh-chip">
          <strong>{label(row.source)}</strong>
          <span>latest {fmtDate(row.latestSnapshotDate)}</span>
          <span>· block {fmtInt(row.latestAnchorBlock)}</span>
          <span>· {fmtInt(row.poolsPublished)} pools</span>
          <span>· published {fmtTime(row.latestPublishedAt)}</span>
          {isStale(row.source) && <span className="plx-stale-badge">STALE</span>}
        </span>
      ))}
    </div>
  );
}

export function OverviewSection({ ctx }: { ctx: PlxViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const summary = parsePoolsSummary(dataset(ctx, "pools_summary"));

  const classFeeDs = useDataset(ctx, "pools_by_class_fee");
  const classFeeSpec = useMemo(() => classFeeMixOption(parseClassFee(classFeeDs)), [classFeeDs]);
  const probeDs = useDataset(ctx, "probe_coverage_split");
  const probeSpec = useMemo(() => probeCoverageOption(parseProbeCoverage(probeDs)), [probeDs]);
  const trendDs = useDataset(ctx, "live_pool_trend");
  const trendSpec = useMemo(() => livePoolTrendOption(parseLiveTrend(trendDs)), [trendDs]);
  const concentrationDs = useDataset(ctx, "concentration_summary");
  const concentrationRows = useMemo(() => parseConcentrationSummary(concentrationDs), [concentrationDs]);
  const concentrationSpec = useMemo(() => concentrationOption(concentrationRows), [concentrationRows]);
  const widthDs = useDataset(ctx, "range_width_distribution");
  const widthSpec = useMemo(() => rangeWidthOption(parseRangeWidth(widthDs)), [widthDs]);

  const rangesPerPool = concentrationRows.find((row) => row.metric === "ranges_per_pool");
  const fullRange = concentrationRows.find((row) => row.metric === "has_full_range");
  const window: PlxWindow = WINDOWS.some((entry) => entry.id === ctx.state.window) ? (ctx.state.window as PlxWindow) : "1y";

  const retry = (group: string) => () => ctx.retryGroup("overview", group);

  return (
    <>
      <GroupGate ctx={ctx} section="overview" group="core">
        <KpiRow
          items={[
            { label: "Pools configured", value: fmtInt(summary?.pools_configured) },
            { label: "CL pools", value: fmtInt(summary?.pools_configured_cl) },
            { label: "Reserves-only pools", value: fmtInt(summary?.pools_configured_reserves_only) },
            { label: "CL published", value: fmtInt(summary?.pools_published_cl) },
            { label: "CL live", value: fmtInt(summary?.pools_live_cl) },
            { label: "Probed for ticks", value: fmtInt(summary?.pools_probed) },
            { label: "Live · state only", value: fmtInt(summary?.pools_live_unprobed) },
            { label: "With reserves", value: fmtInt(summary?.pools_with_reserves) },
          ]}
          meta={<DatasetInfo datasetKey="pools_summary" descriptor={ctx.descriptors.pools_summary} />}
        />
        <DatasetPanel
          title="Publication clocks"
          descriptor={ctx.descriptors.source_freshness}
          groupLoaded={groups["overview.core"]}
          onRetry={retry("core")}
          meta={<DatasetInfo datasetKey="source_freshness" descriptor={ctx.descriptors.source_freshness} />}
        >
          <FreshnessStrip ctx={ctx} />
          <div className="plx-hint">
            As of {fmtDate(summary?.as_of)} · anchor block {fmtInt(summary?.anchor_block)} ({fmtTime(summary?.anchor_timestamp)}).
            Only {fmtInt(summary?.pools_probed)} of {fmtInt(summary?.pools_live_cl)} live CL pools are probed for ticks; the rest are state-only.
          </div>
        </DatasetPanel>
      </GroupGate>

      <GroupGate ctx={ctx} section="overview" group="mix">
        <GroupBanner groupLoaded={groups["overview.mix"]} onRetry={retry("mix")} />
        <div className="plx-grid-2">
          <DatasetPanel
            title="Pools by class and fee band"
            descriptor={ctx.descriptors.pools_by_class_fee}
            groupLoaded={groups["overview.mix"]}
            hydrationPhase={ctx.hydrated.pools_by_class_fee?.phase}
            hydrationError={ctx.hydrated.pools_by_class_fee?.error}
            onRetry={retry("mix")}
            meta={<DatasetInfo datasetKey="pools_by_class_fee" descriptor={ctx.descriptors.pools_by_class_fee} />}
          >
            <ChartCard chartId="plx-class-fee" hideId sql={ctx.descriptors.pools_by_class_fee?.sql} sourceModel={SRC} spec={classFeeSpec} />
          </DatasetPanel>
          <DatasetPanel
            title="Probe coverage (CL pools)"
            descriptor={ctx.descriptors.probe_coverage_split}
            groupLoaded={groups["overview.mix"]}
            hydrationPhase={ctx.hydrated.probe_coverage_split?.phase}
            hydrationError={ctx.hydrated.probe_coverage_split?.error}
            onRetry={retry("mix")}
            meta={<DatasetInfo datasetKey="probe_coverage_split" descriptor={ctx.descriptors.probe_coverage_split} />}
          >
            <ChartCard chartId="plx-probe" hideId sql={ctx.descriptors.probe_coverage_split?.sql} sourceModel={SRC} spec={probeSpec} />
          </DatasetPanel>
        </div>
      </GroupGate>

      <GroupGate ctx={ctx} section="overview" group="trend">
        <DatasetPanel
          title="Published, live and probed pools over time"
          descriptor={ctx.descriptors.live_pool_trend}
          groupLoaded={groups["overview.trend"]}
          hydrationPhase={ctx.hydrated.live_pool_trend?.phase}
          hydrationError={ctx.hydrated.live_pool_trend?.error}
          onRetry={retry("trend")}
          meta={(
            <span className="plx-section-actions">
              <SegmentedControl<PlxWindow>
                size="sm"
                ariaLabel="Trend window"
                value={window}
                options={WINDOWS.map((entry) => ({ value: entry.id, label: entry.label }))}
                onChange={(next) => ctx.apply("overview", undefined, { window: next, asOf: ctx.state.as_of })}
              />
              <DatasetInfo datasetKey="live_pool_trend" descriptor={ctx.descriptors.live_pool_trend} />
            </span>
          )}
        >
          <ChartCard chartId="plx-trend" hideId sql={ctx.descriptors.live_pool_trend?.sql} sourceModel={SRC} spec={trendSpec} />
        </DatasetPanel>
      </GroupGate>

      <GroupGate ctx={ctx} section="overview" group="concentration">
        <GroupBanner groupLoaded={groups["overview.concentration"]} onRetry={retry("concentration")} />
        <div className="plx-grid-2">
          <DatasetPanel
            title="Concentration around the current price"
            descriptor={ctx.descriptors.concentration_summary}
            groupLoaded={groups["overview.concentration"]}
            hydrationPhase={ctx.hydrated.concentration_summary?.phase}
            hydrationError={ctx.hydrated.concentration_summary?.error}
            onRetry={retry("concentration")}
            meta={<DatasetInfo datasetKey="concentration_summary" descriptor={ctx.descriptors.concentration_summary} />}
          >
            <div className="plx-strip">
              <span>ranges per pool · median <strong>{fmtInt(rangesPerPool?.median)}</strong> (q25 {fmtInt(rangesPerPool?.q25)}, q75 {fmtInt(rangesPerPool?.q75)})</span>
              <span>pools with a full-range position <strong>{fmtInt(fullRange?.poolsTrue)}</strong> of {fmtInt(fullRange?.poolsMeasured)}</span>
            </div>
            <ChartCard chartId="plx-concentration" hideId sql={ctx.descriptors.concentration_summary?.sql} sourceModel={SRC} spec={concentrationSpec} />
            <div className="plx-hint">Tick-weighted shares; a full-range position dominates each pool's denominator by construction.</div>
          </DatasetPanel>
          <DatasetPanel
            title="Range widths"
            descriptor={ctx.descriptors.range_width_distribution}
            groupLoaded={groups["overview.concentration"]}
            hydrationPhase={ctx.hydrated.range_width_distribution?.phase}
            hydrationError={ctx.hydrated.range_width_distribution?.error}
            onRetry={retry("concentration")}
            meta={<DatasetInfo datasetKey="range_width_distribution" descriptor={ctx.descriptors.range_width_distribution} />}
          >
            <ChartCard chartId="plx-widths" hideId sql={ctx.descriptors.range_width_distribution?.sql} sourceModel={SRC} spec={widthSpec} />
            <div className="plx-hint">Zero-liquidity gaps are excluded.</div>
          </DatasetPanel>
        </div>
      </GroupGate>
    </>
  );
}
