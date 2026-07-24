// Trader-dynamics facet (client-side view over the `traders` server section;
// groups dynamics + retention — each the SOLE member of its group server-side
// and gated separately here). Growth accounting (new / returning /
// reactivated / churned + quick ratio) and the cohort-retention triangle.
//
// Disclosure: a trader is an ADDRESS, and both datasets use a fixed trailing
// 12-month analytical window regardless of the global time selector (the
// dataset docs carry the full method caveats, incl. BNB exclusion).

import { useMemo } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { rowsToObjects } from "../../shared/rowDataset";
import { growthAccountingOption, shareHeatmapOption } from "../model/chartOptions";
import { KpiTile } from "../components/KpiTile";
import {
  ChartSection,
  CoverageInfo,
  GroupGate,
  Table,
  formatNumber,
  toDataset,
  type SectionProps,
} from "./SectionViews";

type Row = Record<string, unknown>;

export interface RetentionHeatmapModel {
  /** Raw month indexes as strings ("0".."11") — display adds the M+ prefix. */
  xLabels: string[];
  /** Cohort months ascending (raw values; display trims to YYYY-MM). */
  yLabels: string[];
  /** [xIndex, yIndex, share 0..1] triples for shareHeatmapOption. */
  cells: Array<[number, number, number]>;
}

/** Cohort-retention triangle from trader_retention rows. Month indexes are
 * rendered as a CONTIGUOUS 0..max axis so missing cells read as gaps, not as
 * silently compacted columns. Shares are clamped to [0,1]; rows without a
 * finite share are dropped. */
export function buildRetentionHeatmap(rows: Row[]): RetentionHeatmapModel {
  let maxIndex = -1;
  const cohorts = new Set<string>();
  const parsed: Array<{ cohort: string; index: number; share: number }> = [];
  for (const row of rows) {
    const cohort = String(row.cohort_month ?? "");
    const index = Number(row.month_index);
    const share = Number(row.retention_share);
    if (!cohort || !Number.isInteger(index) || index < 0 || !Number.isFinite(share)) continue;
    cohorts.add(cohort);
    maxIndex = Math.max(maxIndex, index);
    parsed.push({ cohort, index, share: Math.min(1, Math.max(0, share)) });
  }
  if (parsed.length === 0) return { xLabels: [], yLabels: [], cells: [] };
  const xLabels = Array.from({ length: maxIndex + 1 }, (_, i) => String(i));
  const yLabels = [...cohorts].sort();
  const yIndex = new Map(yLabels.map((label, i) => [label, i]));
  const cells: Array<[number, number, number]> = parsed.map((entry) => [
    entry.index,
    yIndex.get(entry.cohort) ?? 0,
    entry.share,
  ]);
  return { xLabels, yLabels, cells };
}

/** The most recent growth-accounting row (period-ascending input or not). */
export function latestPeriod(rows: Row[]): Row | null {
  let best: Row | null = null;
  let bestKey = "";
  for (const row of rows) {
    const key = String(row.period ?? "");
    if (!key) continue;
    if (key > bestKey) {
      bestKey = key;
      best = row;
    }
  }
  return best;
}

function metric(row: Row | null, field: string): string {
  if (!row) return "—";
  const value = Number(row[field]);
  return Number.isFinite(value) ? formatNumber(value) : "—";
}

export function TraderDynamicsSection(props: SectionProps) {
  const dynamicsHydrated = props.hydrated.trader_dynamics;
  const dynamicsRows = useMemo(() => rowsToObjects(toDataset(dynamicsHydrated)), [dynamicsHydrated]);
  const latest = useMemo(() => latestPeriod(dynamicsRows), [dynamicsRows]);
  const growthSpec = useMemo(() => growthAccountingOption(dynamicsRows), [dynamicsRows]);

  const retentionHydrated = props.hydrated.trader_retention;
  const retentionRows = useMemo(() => rowsToObjects(toDataset(retentionHydrated)), [retentionHydrated]);
  const retention = useMemo(() => buildRetentionHeatmap(retentionRows), [retentionRows]);
  const retentionSpec = useMemo(
    () => shareHeatmapOption({
      ...retention,
      colorLabel: "retained",
      xAxisRotate: 0,
      xLabelFormatter: (value: string) => `M+${value}`,
      yLabelFormatter: (value: string) => value.slice(0, 7),
      gridLeft: 90,
    }),
    [retention],
  );

  const quickRatio = latest && Number.isFinite(Number(latest.quick_ratio))
    ? Number(latest.quick_ratio).toFixed(2)
    : "—";
  const latestLabel = latest ? String(latest.period ?? "").slice(0, 7) : "";

  return (
    <>
      <GroupGate props={props} group="dynamics">
        <div className="cow-kpi-head">
          <div className="cow-kpi-tiles">
            <KpiTile label={`Active${latestLabel ? ` (${latestLabel})` : ""}`} value={metric(latest, "active_traders")} />
            <KpiTile label="New" value={metric(latest, "new_traders")} />
            <KpiTile label="Returning" value={metric(latest, "returning_traders")} />
            <KpiTile label="Reactivated" value={metric(latest, "reactivated_traders")} />
            <KpiTile label="Churned" value={metric(latest, "churned_traders")} />
            <KpiTile label="Quick ratio" value={quickRatio} note="(new + reactivated) / churned" />
          </div>
          <div className="cow-kpi-head__meta">
            <CoverageInfo descriptor={props.descriptors.trader_dynamics} label="KPI methodology" />
          </div>
        </div>
        <div className="cow-note">
          Trader = address · fixed trailing 12-month window (see each
          dataset&apos;s info popover for the full method).
        </div>
        <ChartSection datasetKey="trader_dynamics" title="Trader growth accounting (monthly)" props={props}>
          {dynamicsRows.length > 0
            ? <ChartCard renderer="svg" chartId="cow-trader-growth" hideId spec={growthSpec} />
            : <div className="cow-empty">No growth-accounting periods available (the first indexed month has no prior month to compare against).</div>}
        </ChartSection>
      </GroupGate>
      <GroupGate props={props} group="retention">
        <ChartSection datasetKey="trader_retention" title="Cohort retention (share of cohort active N months later)" props={props}>
          {retention.cells.length > 0
            ? <ChartCard renderer="svg" chartId="cow-trader-retention" hideId spec={retentionSpec} />
            : <div className="cow-empty">No cohort retention rows in the trailing 12-month window.</div>}
        </ChartSection>
      </GroupGate>
      <div className="cow-grid-2">
        <GroupGate props={props} group="dynamics">
          <Table datasetKey="trader_dynamics" title="Growth accounting rows" props={props} />
        </GroupGate>
        <GroupGate props={props} group="retention">
          <Table datasetKey="trader_retention" title="Cohort retention rows" props={props} />
        </GroupGate>
      </div>
    </>
  );
}
