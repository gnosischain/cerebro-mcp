// Overview — protocol-wide aggregates (Dune-style) on top of the original
// per-network tables. Layout:
//   1. KPI tiles from protocol_kpis (chain 0 = exact protocol-wide row;
//      falls back to per-network sums, disclosed) + optional per-chain
//      estimated-volume mini table (populated only for windows <= 7 days).
//   2. Amber staleness strip derived FROM DATA (coverage_matrix newest trade
//      observation per chain; alltime last_trade_at fallback) — stale chains
//      stay IN the charts (flatline honesty), never hidden.
//   3. Daily fills by network (stacked area, Absolute|Share toggle) +
//      all-time per-network totals bar.
//   4. All-time fill-share donut + top-pairs treemap.
//   5. The original tables (coverage matrix visible; the rest demoted to
//      collapsibles — moved, not dropped).

import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { ChartCard } from "../../../components/ChartCard";
import { CollapsibleSection } from "../../shared/CollapsibleSection";
import { rowsToObjects } from "../../shared/rowDataset";
import { shortAddr } from "../../../utils/format";
import { activityOption, pieOption, stackedSeriesOption, treemapOption } from "../model/chartOptions";
import { CHAIN_SERIES_COLORS, CHAIN_SHORT_NAMES, chainSeriesColor } from "../../shared/chainIcons";
import { ChainBadge } from "../../shared/ChainBadge";
import { KpiTile } from "../components/KpiTile";
import {
  ChartSection,
  CoverageInfo,
  GroupGate,
  SegmentedToggle,
  Table,
  formatNumber,
  toDataset,
  type SectionProps,
} from "./SectionViews";

type Row = Record<string, unknown>;

const STALE_THRESHOLD_DAYS = 7;

/** Raw chain series key ("1", "100", …) -> stable per-chain hue. */
const CHAIN_COLOR_BY_KEY: Record<string, string> = Object.fromEntries(
  Object.entries(CHAIN_SERIES_COLORS),
);

function chainLabel(id: string | number): string {
  return CHAIN_SHORT_NAMES[Number(id)] ?? `Chain ${id}`;
}

export interface ProtocolTotals {
  fills: number;
  settlements: number;
  traders: number;
  pairs: number;
  /** Networks with at least one settled fill in the window. */
  networksLive: number;
  /** True when the exact protocol-wide row (chain 0) was present; false =
   * per-network sums (distinct counts overcount cross-network entities). */
  exact: boolean;
}

/** KPI totals from protocol_kpis rows: prefer the chain-0 protocol-wide row
 * (exact cross-network distincts); fall back to summing the chain rows. */
export function protocolTotals(rows: Row[]): ProtocolTotals | null {
  const chainRows = rows.filter((row) => Number(row.chain_id) !== 0);
  const totalRow = rows.find((row) => Number(row.chain_id) === 0);
  const networksLive = chainRows.filter((row) => Number(row.fill_count ?? 0) > 0).length;
  if (totalRow) {
    return {
      fills: Number(totalRow.fill_count ?? 0),
      settlements: Number(totalRow.settlement_transactions ?? 0),
      traders: Number(totalRow.unique_traders ?? 0),
      pairs: Number(totalRow.unique_pairs ?? 0),
      networksLive,
      exact: true,
    };
  }
  if (chainRows.length === 0) return null;
  const sum = (field: string) => chainRows.reduce((acc, row) => acc + Number(row[field] ?? 0), 0);
  return {
    fills: sum("fill_count"),
    settlements: sum("settlement_transactions"),
    traders: sum("unique_traders"),
    pairs: sum("unique_pairs"),
    networksLive,
    exact: false,
  };
}

/** Per-bucket totals across chains (sparkline feed), bucket-ascending. */
export function bucketTotals(rows: Row[], valueField: string): number[] {
  const byBucket = new Map<string, number>();
  for (const row of rows) {
    const bucket = String(row.bucket ?? "");
    if (!bucket) continue;
    const value = Number(row[valueField] ?? 0);
    byBucket.set(bucket, (byBucket.get(bucket) ?? 0) + (Number.isFinite(value) ? value : 0));
  }
  return [...byBucket.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([, value]) => value);
}

export interface StaleChainNote {
  chainId: number;
  /** ISO timestamp of the chain's newest observation. */
  endsAt: string;
}

/** Chains whose newest observation trails the freshest chain by more than
 * `thresholdDays` — the data-driven staleness strip (NO hardcoded dates).
 * Entries without a parseable timestamp are skipped (absence of evidence,
 * not evidence of staleness). */
export function deriveStaleChains(
  entries: Array<{ chainId: number; latest: unknown }>,
  thresholdDays: number = STALE_THRESHOLD_DAYS,
): StaleChainNote[] {
  const parsed = entries
    .map((entry) => ({ chainId: entry.chainId, endsAt: String(entry.latest ?? ""), t: Date.parse(String(entry.latest ?? "")) }))
    .filter((entry) => Number.isFinite(entry.t));
  if (parsed.length < 2) return [];
  const freshest = Math.max(...parsed.map((entry) => entry.t));
  const cutoff = freshest - thresholdDays * 86_400_000;
  return parsed
    .filter((entry) => entry.t < cutoff)
    .sort((a, b) => a.chainId - b.chainId)
    .map(({ chainId, endsAt }) => ({ chainId, endsAt }));
}

/** Pair label for the treemap: symbols when known, short addresses otherwise. */
export function pairName(row: Row): string {
  const s0 = String(row.token0_symbol ?? "") || shortAddr(String(row.token0 ?? ""));
  const s1 = String(row.token1_symbol ?? "") || shortAddr(String(row.token1 ?? ""));
  return s0 && s1 ? `${s0}/${s1}` : "";
}

function chainTotalsBarOption(rows: Row[]): EChartsOption {
  const sorted = [...rows]
    .filter((row) => Number(row.chain_id) !== 0)
    .sort((a, b) => Number(b.fill_count ?? 0) - Number(a.fill_count ?? 0));
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 24, top: 32, bottom: 52 },
    xAxis: { type: "category", data: sorted.map((row) => chainLabel(String(row.chain_id))), axisLabel: { rotate: 30 } },
    yAxis: { type: "value" },
    series: [{
      type: "bar",
      name: "Settled fills",
      barMaxWidth: 34,
      data: sorted.map((row) => ({
        value: Number(row.fill_count ?? 0),
        itemStyle: { color: chainSeriesColor(Number(row.chain_id)) },
      })),
    }],
  } as EChartsOption;
}

export function OverviewSection(props: SectionProps) {
  const [trendMode, setTrendMode] = useState<"absolute" | "share">("absolute");

  const protocolHydrated = props.hydrated.protocol_kpis;
  const protocolRows = useMemo(() => rowsToObjects(toDataset(protocolHydrated)), [protocolHydrated]);
  const totals = useMemo(() => protocolTotals(protocolRows), [protocolRows]);

  const shareHydrated = props.hydrated.chain_share_trend;
  const shareRows = useMemo(() => rowsToObjects(toDataset(shareHydrated)), [shareHydrated]);
  const fillSpark = useMemo(() => bucketTotals(shareRows, "fill_count"), [shareRows]);
  const settlementSpark = useMemo(() => bucketTotals(shareRows, "settlement_transactions"), [shareRows]);
  const trendSpec = useMemo(
    () => stackedSeriesOption(shareRows, {
      xField: "bucket",
      valueField: "fill_count",
      seriesField: "chain_id",
      mode: trendMode,
      kind: "area",
      seriesColors: CHAIN_COLOR_BY_KEY,
      seriesLabeler: chainLabel,
    }),
    [shareRows, trendMode],
  );

  const alltimeHydrated = props.hydrated.alltime_chain_totals;
  const alltimeRows = useMemo(() => rowsToObjects(toDataset(alltimeHydrated)), [alltimeHydrated]);
  const totalsBarSpec = useMemo(() => chainTotalsBarOption(alltimeRows), [alltimeRows]);
  const alltimePieSpec = useMemo(
    () => pieOption(
      alltimeRows
        .filter((row) => Number(row.chain_id) !== 0)
        .map((row) => ({
          name: chainLabel(String(row.chain_id)),
          value: Number(row.fill_count ?? 0),
          itemStyle: { color: chainSeriesColor(Number(row.chain_id)) },
        })),
      { donut: true },
    ),
    [alltimeRows],
  );

  const topPairsHydrated = props.hydrated.top_pairs;
  const topPairRows = useMemo(() => rowsToObjects(toDataset(topPairsHydrated)), [topPairsHydrated]);
  const treemapSpec = useMemo(() => {
    const byPair = new Map<string, number>();
    for (const row of topPairRows) {
      const name = pairName(row);
      if (!name) continue;
      byPair.set(name, (byPair.get(name) ?? 0) + Number(row.fill_count ?? 0));
    }
    const items = [...byPair.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 24)
      .map(([name, value]) => ({ name, value }));
    return treemapOption(items);
  }, [topPairRows]);

  const networkActivity = props.hydrated.network_activity;
  const networkActivitySpec = useMemo(
    () => activityOption(toDataset(networkActivity), "trade_count", "chain_id"),
    [networkActivity],
  );

  // Staleness derivation — coverage_matrix's per-chain newest trade
  // observation is the primary signal (the trade sync stalls even while the
  // RPC checkpoint stays live); alltime last_trade_at is the fallback.
  const coverageHydrated = props.hydrated.coverage_matrix;
  const staleNotes = useMemo(() => {
    const coverageRows = rowsToObjects(toDataset(coverageHydrated));
    const fromCoverage = deriveStaleChains(coverageRows.map((row) => ({
      chainId: Number(row.chain_id),
      latest: row.trade_observed_at,
    })));
    if (coverageRows.length > 0) return fromCoverage;
    return deriveStaleChains(alltimeRows.map((row) => ({
      chainId: Number(row.chain_id),
      latest: row.last_trade_at,
    })));
  }, [coverageHydrated, alltimeRows]);

  // Estimated native volume rows (populated only for windows <= 7 days).
  const volumeRows = useMemo(
    () => protocolRows.filter((row) =>
      Number(row.chain_id) !== 0
      && row.approx_native_volume !== null
      && row.approx_native_volume !== undefined
      && Number(row.approx_native_volume) > 0),
    [protocolRows],
  );
  const nativeSymbol = (chainId: number) =>
    props.state.chain_options.find((chain) => chain.chain_id === chainId)?.native_symbol ?? "";

  const approxNote = totals && !totals.exact
    ? "per-network sums (protocol-wide row unavailable; distinct counts may overcount cross-network entities)"
    : undefined;

  return (
    <>
      <GroupGate props={props} group="protocol">
        <div className="cow-kpi-head">
          <div className="cow-kpi-tiles">
            <KpiTile label="Settled fills" value={totals ? formatNumber(totals.fills) : "—"} spark={fillSpark} note={approxNote} />
            <KpiTile label="Settlements" value={totals ? formatNumber(totals.settlements) : "—"} spark={settlementSpark} />
            <KpiTile label="Active traders" value={totals ? formatNumber(totals.traders) : "—"} />
            <KpiTile label="Unique pairs" value={totals ? formatNumber(totals.pairs) : "—"} />
            <KpiTile label="Networks live" value={totals ? formatNumber(totals.networksLive) : "—"} />
          </div>
          <div className="cow-kpi-head__meta">
            <CoverageInfo descriptor={props.descriptors.protocol_kpis} label="KPI methodology" />
          </div>
        </div>
        {volumeRows.length > 0 && (
          <div className="cow-volume-mini">
            <div className="cow-volume-mini__head">
              Estimated volume per network — valued at current prices, each in
              its own native unit (estimate, not an exact figure)
            </div>
            <table>
              <tbody>
                {volumeRows.map((row) => {
                  const chainId = Number(row.chain_id);
                  return (
                    <tr key={chainId}>
                      <td><ChainBadge chainId={chainId} /></td>
                      <td className="cow-volume-mini__value">
                        {formatNumber(Number(row.approx_native_volume))} {nativeSymbol(chainId)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </GroupGate>
      {staleNotes.length > 0 && (
        <div className="cow-stale-strip" role="note">
          <strong>Stale network data:</strong>
          {staleNotes.map((note) => (
            <span key={note.chainId} className="cow-stale-strip__chain">
              {chainLabel(note.chainId)} ends {note.endsAt.slice(0, 10)}
            </span>
          ))}
          <span className="cow-stale-strip__tail">
            — stale chains stay in the charts (recent buckets flatline) rather
            than being hidden.
          </span>
        </div>
      )}
      <div className="cow-grid-2">
        <GroupGate props={props} group="share">
          <ChartSection
            datasetKey="chain_share_trend"
            title={trendMode === "share" ? "Fill share by network" : "Fills by network over time"}
            props={props}
            actions={(
              <SegmentedToggle
                value={trendMode}
                options={[{ value: "absolute", label: "Absolute" }, { value: "share", label: "Share %" }]}
                onChange={setTrendMode}
                label="Trend mode"
              />
            )}
          >
            {shareRows.length > 0
              ? <ChartCard renderer="svg" chartId="cow-share-trend" hideId spec={trendSpec} />
              : <div className="cow-empty">No fills in this indexed window.</div>}
          </ChartSection>
        </GroupGate>
        <GroupGate props={props} group="protocol">
          <ChartSection datasetKey="alltime_chain_totals" title="All-time settled fills per network" props={props}>
            {alltimeRows.length > 0
              ? <ChartCard renderer="svg" chartId="cow-chain-totals" hideId spec={totalsBarSpec} />
              : <div className="cow-empty">No all-time totals available.</div>}
          </ChartSection>
        </GroupGate>
      </div>
      <div className="cow-grid-2">
        <GroupGate props={props} group="protocol">
          <ChartSection datasetKey="alltime_chain_totals" title="All-time fills by network (share)" props={props}>
            {alltimeRows.length > 0
              ? <ChartCard renderer="svg" chartId="cow-alltime-share" hideId spec={alltimePieSpec} />
              : <div className="cow-empty">No all-time totals available.</div>}
          </ChartSection>
        </GroupGate>
        <GroupGate props={props} group="breakdown">
          <ChartSection datasetKey="top_pairs" title="Top pairs by fills (indexed window)" props={props}>
            {topPairRows.length > 0
              ? <ChartCard renderer="svg" chartId="cow-top-pairs-map" hideId spec={treemapSpec} />
              : <div className="cow-empty">No pair activity in this indexed window.</div>}
          </ChartSection>
        </GroupGate>
      </div>
      <Table datasetKey="coverage_matrix" title="Coverage matrix" props={props} />
      <GroupGate props={props} group="breakdown">
        <CollapsibleSection title="Window breakdown — daily activity + top pairs table" defaultOpen={false}>
          <div className="cow-grid-2">
            <ChartSection datasetKey="network_activity" title="Execution activity" props={props}>
              {networkActivity ? <ChartCard renderer="svg" chartId="cow-network-activity" hideId spec={networkActivitySpec} /> : null}
            </ChartSection>
            <Table datasetKey="top_pairs" title="Top pairs by fill count" props={props} />
          </div>
        </CollapsibleSection>
      </GroupGate>
      <CollapsibleSection title="Indexed network summary" defaultOpen={false}>
        <Table datasetKey="network_summary" title="Indexed network summary" props={props} />
      </CollapsibleSection>
      <GroupGate props={props} group="breakdown">
        <CollapsibleSection title="Indexed fee-policy counts" defaultOpen={false}>
          <Table datasetKey="fee_policy_counts" title="Indexed fee-policy counts" props={props} />
        </CollapsibleSection>
      </GroupGate>
    </>
  );
}
