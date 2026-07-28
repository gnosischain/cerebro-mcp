import { useMemo, type ReactNode } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { MaKpi, MaKpiGrid, MaSection, MaSkeletonKpiGrid, MaSkeletonRows } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor, PageRowsResponse } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { CollapsibleSection } from "../../shared/CollapsibleSection";
import { activityOption, candleOption, depthOption, rankingOption, referencePriceOption, shareHeatmapOption, volumeOption } from "../model/chartOptions";
import { rowsToObjects } from "../../shared/rowDataset";
import { buildShareHeatmap, parseCandles, parseDepth, parseExecutionFlow, parseReferencePrices } from "../model/parseRows";
import type { CowExplorerViewState, EntityType } from "../types";
import { ChainBadge } from "../../shared/ChainBadge";
import { CuratedTable } from "../components/CuratedTable";
import { InfoBlocks, InfoPopover } from "../components/InfoPopover";
import { datasetError } from "../../shared/datasetError";
import { DATASET_GROUP } from "../model/datasetGroups";
import { DATASET_DOCS } from "../model/datasetDocs";
import { FACET_VIEWS, type FacetHostProps } from "../model/navGroups";
import { solverName } from "../model/solverRegistry";
import { SankeySvg } from "../../shared/svg-flow/SankeySvg";
import { DepthPanel, type DepthHostProps } from "../components/DepthPanel";
import { LiveSection } from "./LiveSection";
import { OverviewSection } from "./OverviewSection";
import { OrderTypesSection } from "./OrderTypesSection";
import { SolverDirectorySection } from "./SolverDirectorySection";
import { TraderDynamicsSection } from "./TraderDynamicsSection";

type FetchRows = (viewId: string, datasetKey: string, pageToken?: string) => Promise<PageRowsResponse | null>;

/** The prop contract every section (and facet) view renders against.
 * FACET-HOOK: `facet` arrives via FacetHostProps (model/navGroups.ts) and is
 * non-null only when its host section equals the rendered `state.section`.
 * DEPTH-HOOK: `onLoadDepthAt` arrives via DepthHostProps (components/
 * DepthPanel.tsx) and is forwarded to the Markets depth panel. */
export interface SectionProps extends FacetHostProps, DepthHostProps {
  state: CowExplorerViewState;
  descriptors: Record<string, DatasetDescriptor>;
  hydrated: Record<string, HydratedDataset>;
  viewId: string;
  fetchRows: FetchRows;
  onEntity: (entityType: EntityType, identifier: string, chainId?: number) => void;
  /** Switch the active section to a single network (chain quick-links). */
  onSelectChain?: (chainId: number) => void;
  /** Open a pair's market history (fills-count click on pair tables). */
  onSelectPair?: (base: string, quote: string, chainId?: number) => void;
  /** `${section}.${group}` keys whose deferred load failed (current scope). */
  failedGroups?: string[];
  onRetryGroup?: (section: string, group: string) => void;
  /** Live section: re-enqueue the live dataset groups (poll tick). */
  onRefreshLive?: () => void;
  /** Live auto-refresh default (standalone true, embedded false). */
  liveAutoDefault?: boolean;
}

type Props = SectionProps;

export function toDataset(value?: HydratedDataset) {
  return value ? { columns: value.columns, rows: value.rows } : undefined;
}

export function dataset(hydrated: Record<string, HydratedDataset>, key: string) {
  return toDataset(hydrated[key]);
}

// Chart specs are memoized on the backing hydrated dataset (and any label
// inputs) — ChartCard renders with notMerge, so an unstable spec identity
// tears down and re-animates the chart on every unrelated parent render.

export function formatNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** Section KPI header: every section opens with headline numbers instead of
 * dumping straight into a chart or table. */
export function KpiRow({ items, meta }: {
  items: Array<{ label: string; value: string }>;
  meta?: ReactNode;
}) {
  return (
    <div className="cow-kpi-head">
      <MaKpiGrid>
        {items.map((item) => <MaKpi key={item.label} label={item.label} value={item.value} />)}
      </MaKpiGrid>
      {meta ? <div className="cow-kpi-head__meta">{meta}</div> : null}
    </div>
  );
}

export function sumField(rows: Array<Record<string, unknown>>, field: string): number {
  return rows.reduce((acc, row) => acc + Number(row[field] ?? 0), 0);
}

/** Tiny segmented control (e.g. the Absolute | Share % chart toggles). */
export function SegmentedToggle<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  label?: string;
}) {
  return (
    <div className="cow-seg" role="group" aria-label={label ?? "View mode"}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={option.value === value ? "is-active" : ""}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function GroupGate({ props, group, children }: {
  props: Props;
  group: string;
  children: ReactNode;
}) {
  const section = props.state.section;
  const key = `${section}.${group}`;
  if (props.failedGroups?.includes(key)) {
    return (
      <div className="cow-group-error" role="alert">
        <span>These datasets failed to load.</span>
        <button type="button" onClick={() => props.onRetryGroup?.(section, group)}>
          Retry
        </button>
      </div>
    );
  }
  if (props.state.loaded_groups?.[key] === false) {
    return (
      <div className="cow-skel" aria-busy="true" aria-label="Loading datasets">
        {group === "core" ? <MaSkeletonKpiGrid /> : <div className="cow-skel__bar" />}
        <MaSkeletonRows count={group === "core" ? 4 : 6} />
      </div>
    );
  }
  return <>{children}</>;
}

/** Explicit error card — a failed dataset must stay visible, never vanish. */
export function DatasetErrorCard({ datasetKey, title, props, error }: {
  datasetKey: string;
  title: string;
  props: Props;
  error: string;
}) {
  const owner = DATASET_GROUP[datasetKey];
  return (
    <MaSection title={title}>
      <div className="cow-dataset-error" role="alert">
        <div className="cow-dataset-error__msg">
          <strong>This dataset failed to load.</strong>
          <span>{error}</span>
        </div>
        {owner ? (
          <button
            type="button"
            onClick={() => props.onRetryGroup?.(owner.section, owner.group)}
          >
            Retry
          </button>
        ) : null}
      </div>
    </MaSection>
  );
}

/** Chart wrapper with the same failure contract as Table: a dataset whose
 * query failed renders an explicit error card, never a blank/empty chart. */
export function ChartSection({ datasetKey, title, metaLabel, props, actions, children }: {
  datasetKey: string;
  title: string;
  metaLabel?: string;
  props: Props;
  /** Extra header affordances (e.g. an Absolute|Share toggle). */
  actions?: ReactNode;
  children: ReactNode;
}) {
  const descriptor = props.descriptors[datasetKey];
  const error = descriptor ? datasetError(descriptor) : "";
  if (error) {
    return <DatasetErrorCard datasetKey={datasetKey} title={title} props={props} error={error} />;
  }
  const meta = actions
    ? <span className="cow-section-actions">{actions}<CoverageInfo descriptor={descriptor} label={metaLabel} /></span>
    : <CoverageInfo descriptor={descriptor} label={metaLabel} />;
  return (
    <MaSection title={title} meta={meta}>
      {children}
    </MaSection>
  );
}

export function Table({ datasetKey, title, props }: { datasetKey: string; title: string; props: Props }) {
  const descriptor = props.descriptors[datasetKey];
  if (!descriptor) return null;
  const error = datasetError(descriptor);
  if (error) {
    return <DatasetErrorCard datasetKey={datasetKey} title={title} props={props} error={error} />;
  }
  return (
    <MaSection title={title} meta={<CoverageInfo descriptor={descriptor} />}>
      <CuratedTable
        datasetKey={datasetKey}
        descriptor={descriptor}
        state={props.state}
        viewId={props.viewId}
        fetchRows={props.fetchRows}
        onEntity={props.onEntity}
        onSelectPair={props.onSelectPair}
      />
    </MaSection>
  );
}

export function CoverageInfo({ descriptor, label = "About this data" }: { descriptor?: DatasetDescriptor; label?: string }) {
  const docs = descriptor ? DATASET_DOCS[descriptor.key] : undefined;
  return (
    <InfoPopover label={label}>
      <InfoBlocks what={docs?.what} method={docs?.method} coverage={coverageMeta(descriptor)} />
    </InfoPopover>
  );
}

export function coverageMeta(descriptor?: DatasetDescriptor): string {
  const coverage = descriptor?.provenance?.coverage as
    | { actual_start?: string | null; actual_end?: string | null; mode?: string; latest_source_observation?: string | null; fetched_at?: string | null; truncated?: boolean }
    | undefined;
  if (!coverage) return "Indexed window disclosed in source metadata";
  const range = [coverage.actual_start, coverage.actual_end].filter(Boolean).join(" → ");
  return [
    coverage.mode,
    range,
    coverage.latest_source_observation ? `source observed ${coverage.latest_source_observation}` : "",
    coverage.fetched_at ? `fetched ${coverage.fetched_at}` : "",
    coverage.truncated ? "result truncated" : "",
  ].filter(Boolean).join(" · ") || "No matching rows in indexed window";
}

function Markets(props: Props) {
  const priceCandles = props.hydrated.price_candles;
  const candles = useMemo(() => parseCandles(toDataset(priceCandles)), [priceCandles]);
  const candleSpec = useMemo(() => candleOption(candles), [candles]);
  const volumeSpec = useMemo(() => volumeOption(candles), [candles]);
  const auctionRefHydrated = props.hydrated.auction_reference_prices;
  const auctionReferences = useMemo(() => parseReferencePrices(toDataset(auctionRefHydrated)), [auctionRefHydrated]);
  const auctionRefSpec = useMemo(() => referencePriceOption(auctionReferences, "Auction reference"), [auctionReferences]);
  const nativeRefHydrated = props.hydrated.native_reference_prices;
  const nativeReferences = useMemo(() => parseReferencePrices(toDataset(nativeRefHydrated)), [nativeRefHydrated]);
  const nativeRefSpec = useMemo(() => referencePriceOption(nativeReferences, "Native-price observation"), [nativeReferences]);
  return (
    <>
      <KpiRow
        items={[
          { label: "Base", value: props.state.pair.base_symbol || "Base" },
          { label: "Quote", value: props.state.pair.quote_symbol || "Quote" },
          { label: "Resolution", value: props.state.interval },
          { label: "Candles", value: formatNumber(candles.length) },
        ]}
        meta={<CoverageInfo descriptor={props.descriptors.market_summary} label="Market methodology" />}
      />
      <GroupGate props={props} group="charts">
        <div className="cow-grid-2">
        <ChartSection datasetKey="price_candles" title="Execution prices (settled fills)" props={props}>
          {candles.length ? <ChartCard renderer="svg" chartId="cow-candles" hideId spec={candleSpec} /> : <div className="cow-empty">No normalized execution candles. Token decimals may be missing.</div>}
        </ChartSection>
        {candles.length > 0 && <MaSection title="Execution volume" meta={<CoverageInfo descriptor={props.descriptors.price_candles} label="Base units · coverage" />}><ChartCard renderer="svg" chartId="cow-volume" hideId spec={volumeSpec} /></MaSection>}
        <ChartSection datasetKey="auction_reference_prices" title="Auction reference prices" props={props}>
          {auctionReferences.length > 0 ? <ChartCard renderer="svg" chartId="cow-auction-reference" hideId spec={auctionRefSpec} /> : <div className="cow-empty">No auction reference prices with mapped auction block timestamps in this indexed window.</div>}
        </ChartSection>
        <ChartSection datasetKey="native_reference_prices" title="Native-price API observations" props={props}>
          {nativeReferences.length > 0 ? <ChartCard renderer="svg" chartId="cow-native-reference" hideId spec={nativeRefSpec} /> : <div className="cow-empty">No matching native-price API observations in this indexed window.</div>}
        </ChartSection>
        </div>
      </GroupGate>
      {/* DEPTH-HOOK mount: the pair order-book depth panel (markets.depth
        * group). DepthPanelProps is a subset of SectionProps + onLoadDepthAt,
        * so the full section props spread satisfies it. */}
      <GroupGate props={props} group="depth">
        <DepthPanel {...props} />
      </GroupGate>
      <GroupGate props={props} group="tape">
        <Table datasetKey="recent_market_trades" title="Recent settled fills" props={props} />
      </GroupGate>
    </>
  );
}

function Trades(props: Props) {
  const tradeActivity = props.hydrated.trade_activity;
  const tradeActivitySpec = useMemo(() => activityOption(toDataset(tradeActivity), "fill_count"), [tradeActivity]);
  const activityRows = rowsToObjects(dataset(props.hydrated, "trade_activity"));
  return (
    <GroupGate props={props} group="core">
      <KpiRow
        items={[
          { label: "Settled fills", value: formatNumber(sumField(activityRows, "fill_count")) },
          { label: "Settlement txs", value: formatNumber(sumField(activityRows, "settlement_transactions")) },
          { label: "Active traders", value: formatNumber(sumField(activityRows, "owners")) },
        ]}
        meta={<CoverageInfo descriptor={props.descriptors.trade_activity} label="KPI methodology" />}
      />
      <ChartSection datasetKey="trade_activity" title="Settled fill activity" props={props}>
        {tradeActivity ? <ChartCard renderer="svg" chartId="cow-trade-activity" hideId spec={tradeActivitySpec} /> : null}
      </ChartSection>
      <Table datasetKey="trade_pair_breakdown" title="Pair breakdown" props={props} />
      <GroupGate props={props} group="tape">
        <Table datasetKey="trades" title="Settled fills" props={props} />
      </GroupGate>
    </GroupGate>
  );
}

function Orders(props: Props) {
  const intentDepth = props.hydrated.intent_depth;
  const depth = useMemo(() => parseDepth(toDataset(intentDepth)), [intentDepth]);
  const depthSpec = useMemo(() => depthOption(depth), [depth]);
  const orderActivity = props.hydrated.order_activity;
  const orderActivitySpec = useMemo(() => activityOption(toDataset(orderActivity), "order_count"), [orderActivity]);
  const statusRows = rowsToObjects(dataset(props.hydrated, "order_status_summary"));
  const openCount = statusRows.filter((row) => String(row.status) === "open").reduce((acc, row) => acc + Number(row.order_count ?? 0), 0);
  return (
    <GroupGate props={props} group="core">
      <KpiRow
        items={[
          { label: "Observed orders", value: formatNumber(sumField(statusRows, "order_count")) },
          { label: "Known open intents", value: formatNumber(openCount) },
          { label: "Statuses", value: formatNumber(statusRows.length) },
        ]}
        meta={<CoverageInfo descriptor={props.descriptors.order_status_summary} label="KPI methodology" />}
      />
      <div className="cow-inline-info"><InfoPopover label="What “known intents” means"><strong>Known open intents (observed snapshot).</strong> This is not a complete live orderbook. Freshness does not imply completeness.</InfoPopover></div>
      <ChartSection datasetKey="order_activity" title="Observed order lifecycle" props={props}>
        {orderActivity ? <ChartCard renderer="svg" chartId="cow-order-activity" hideId spec={orderActivitySpec} /> : null}
      </ChartSection>
      <Table datasetKey="order_status_summary" title="Observed status summary" props={props} />
      <GroupGate props={props} group="intents">
        <ChartSection datasetKey="intent_depth" title="Known intents" props={props}>
          {depth.length ? <ChartCard renderer="svg" chartId="cow-depth" hideId spec={depthSpec} /> : <div className="cow-empty">No executable normalized intent depth for this pair.</div>}
        </ChartSection>
        <Table datasetKey="known_intents" title="Known intent summary" props={props} />
        <Table datasetKey="known_orders" title="Known open intents (observed snapshot)" props={props} />
      </GroupGate>
      <GroupGate props={props} group="quality">
        <Table datasetKey="order_quality_summary" title="Execution quality — surplus vs limit, per day" props={props} />
        <CollapsibleSection title="Quality distributions (latency + surplus)" defaultOpen={false}>
          <div className="cow-grid-2">
            <Table datasetKey="fill_latency_distribution" title="Creation-to-fill latency" props={props} />
            <Table datasetKey="surplus_distribution" title="Surplus distribution (bps vs limit)" props={props} />
          </div>
        </CollapsibleSection>
      </GroupGate>
    </GroupGate>
  );
}

function Auctions(props: Props) {
  const auctionActivity = props.hydrated.auction_activity;
  const auctionActivitySpec = useMemo(
    () => activityOption(toDataset(auctionActivity), "competition_count", "chain_id", "bar"),
    [auctionActivity],
  );
  const activityRows = rowsToObjects(dataset(props.hydrated, "auction_activity"));
  return (
    <GroupGate props={props} group="core">
      <KpiRow
        items={[
          { label: "Settled competitions", value: formatNumber(sumField(activityRows, "competition_count")) },
          { label: "Days covered", value: formatNumber(activityRows.length) },
        ]}
        meta={<CoverageInfo descriptor={props.descriptors.auction_activity} label="KPI methodology" />}
      />
      <ChartSection datasetKey="auction_activity" title="Settled competitions per day" props={props}>
        {activityRows.length > 0
          ? <ChartCard renderer="svg" chartId="cow-auction-activity" hideId spec={auctionActivitySpec} />
          : <div className="cow-empty">No settled competitions indexed in this window on this chain — competition data comes from the CoW API enrichment and covers a shorter span than on-chain fills.</div>}
      </ChartSection>
      <GroupGate props={props} group="list">
        <Table datasetKey="auctions" title="Indexed settled competitions" props={props} />
      </GroupGate>
    </GroupGate>
  );
}

export function CrossChainMatrix(props: Props) {
  const rows = rowsToObjects(dataset(props.hydrated, "solver_cross_chain"));
  if (rows.length === 0) {
    // Never blank — an all-networks Solvers view with no cross-chain rows
    // previously rendered NOTHING here, which read as a broken page.
    return (
      <MaSection title="Cross-chain comparison (wins / competitions)">
        <div className="cow-empty">
          No cross-chain competition rows in this indexed window. Widen the
          time window, or select a single network to see per-chain solver
          detail and the pair-to-executor flow.
        </div>
      </MaSection>
    );
  }
  const chains = [...new Set(rows.map((row) => Number(row.chain_id)))].sort((a, b) => a - b);
  const bySolver = new Map<string, Map<number, { wins: number; competitions: number }>>();
  for (const row of rows) {
    const solver = String(row.competition_solver);
    const entry = bySolver.get(solver) ?? new Map();
    entry.set(Number(row.chain_id), {
      wins: Number(row.wins ?? 0),
      competitions: Number(row.competitions ?? 0),
    });
    bySolver.set(solver, entry);
  }
  const solvers = [...bySolver.entries()]
    .sort((a, b) => {
      const total = (m: Map<number, { wins: number }>) =>
        [...m.values()].reduce((acc, v) => acc + v.wins, 0);
      return total(b[1]) - total(a[1]);
    })
    .slice(0, 25);
  return (
    <MaSection title="Cross-chain comparison (wins / competitions)" meta={<CoverageInfo descriptor={props.descriptors.solver_cross_chain} />}>
      <div className="cow-matrix-scroll">
        <table className="cow-matrix">
          <thead>
            <tr>
              <th>Solver</th>
              {chains.map((chainId) => <th key={chainId}><ChainBadge chainId={chainId} showName={false} /></th>)}
            </tr>
          </thead>
          <tbody>
            {solvers.map(([solver, entry]) => (
              <tr key={solver}>
                <td>
                  <button type="button" className="cow-live-side cow-solver" title={solver} onClick={() => props.onEntity("solver", solver)}>
                    {solverName(props.state.chain_id || 1, solver) || `${solver.slice(0, 6)}…${solver.slice(-4)}`}
                  </button>
                </td>
                {chains.map((chainId) => {
                  const cell = entry.get(chainId);
                  return (
                    <td key={chainId} className={cell ? "" : "cow-matrix__empty"}>
                      {cell ? `${cell.wins} / ${cell.competitions}` : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </MaSection>
  );
}

function Solvers(props: Props) {
  const chainId = props.state.chain_id || 1;
  const solverActivity = props.hydrated.solver_activity;
  const solverActivitySpec = useMemo(
    () => activityOption(toDataset(solverActivity), "competitions", "competition_solver", "bar", (value) => solverName(chainId, value) || `${value.slice(0, 6)}…${value.slice(-4)}`),
    [solverActivity, chainId],
  );
  const rankingDistribution = props.hydrated.ranking_distribution;
  const rankingSpec = useMemo(() => rankingOption(toDataset(rankingDistribution)), [rankingDistribution]);
  const flow = parseExecutionFlow(dataset(props.hydrated, "execution_flow"));
  const allNetworks = props.state.chain_id === 0;
  const statRows = rowsToObjects(dataset(props.hydrated, "solver_stats"));
  return (
    <GroupGate props={props} group="core">
      <KpiRow
        items={[
          { label: "Competition solvers", value: formatNumber(statRows.length) },
          { label: "Solutions", value: formatNumber(sumField(statRows, "solutions")) },
          { label: "Wins", value: formatNumber(sumField(statRows, "wins")) },
        ]}
        meta={<CoverageInfo descriptor={props.descriptors.solver_stats} label="KPI methodology" />}
      />
      <ChartSection datasetKey="solver_activity" title="Competition entries per day (top solvers)" props={props}>
        {(solverActivity?.rows.length ?? 0) > 0
          ? <ChartCard renderer="svg" chartId="cow-solver-activity" hideId spec={solverActivitySpec} />
          : <div className="cow-empty">No competition entries indexed in this window on this chain — competition data comes from the CoW API enrichment and covers a shorter span than on-chain fills.</div>}
      </ChartSection>
      <GroupGate props={props} group="detail">
        <ChartSection datasetKey="ranking_distribution" title="Solution ranking distribution" props={props}>
          {rankingDistribution ? <ChartCard renderer="svg" chartId="cow-rankings" hideId spec={rankingSpec} /> : null}
        </ChartSection>
        {allNetworks ? (
          <>
            <CrossChainMatrix {...props} />
            <div className="cow-inline-cta">
              <span>The pair → settlement-executor flow is per-network.</span>
              {(props.state.chain_options ?? []).slice(0, 6).map((chain) => (
                <button
                  key={chain.chain_id}
                  type="button"
                  onClick={() => props.onSelectChain?.(chain.chain_id)}
                >
                  <ChainBadge chainId={chain.chain_id} showName={false} /> {chain.name}
                </button>
              ))}
            </div>
          </>
        ) : (
          <ChartSection datasetKey="execution_flow" title="Pair → settlement executor" metaLabel="Flow methodology" props={props}>
            {flow.length > 0 ? (
              <SankeySvg
                links={flow.map((link) => ({ source: link.source, target: link.target, value: link.value }))}
                nodeLabel={(id, side) => side === "right"
                  ? (solverName(props.state.chain_id || 1, id) || `${id.slice(0, 6)}…${id.slice(-4)}`)
                  : id}
                formatValue={(value) => `${value.toLocaleString()} fills`}
                leftTitle="Token pair"
                rightTitle="Settlement executor"
                onNodeClick={(id, side) => { if (side === "right") props.onEntity("solver", id); }}
              />
            ) : <div className="cow-empty">No settlement-executor flow matches this pair, role filter, and indexed window.</div>}
          </ChartSection>
        )}
      </GroupGate>
      <Table datasetKey="solver_stats" title="Competition solver statistics" props={props} />
    </GroupGate>
  );
}

function Traders(props: Props) {
  const traderActivity = props.hydrated.trader_activity;
  const traderActivitySpec = useMemo(() => activityOption(toDataset(traderActivity), "active_traders"), [traderActivity]);
  const leaderRows = rowsToObjects(dataset(props.hydrated, "trader_leaderboard"));
  return (
    <GroupGate props={props} group="core">
      <KpiRow
        items={[
          { label: "Traders (top set)", value: formatNumber(leaderRows.length) },
          { label: "Fills", value: formatNumber(sumField(leaderRows, "fill_count")) },
          { label: "Distinct pairs", value: formatNumber(sumField(leaderRows, "distinct_pairs")) },
        ]}
        meta={<CoverageInfo descriptor={props.descriptors.trader_leaderboard} label="KPI methodology" />}
      />
      <ChartSection datasetKey="trader_activity" title="Active and new traders" props={props}>
        {traderActivity ? <ChartCard renderer="svg" chartId="cow-trader-activity" hideId spec={traderActivitySpec} /> : null}
      </ChartSection>
      <Table datasetKey="trader_leaderboard" title="Trader leaderboard" props={props} />
    </GroupGate>
  );
}

function Patterns(props: Props) {
  const chainId = props.state.chain_id || 1;
  const short = (value: unknown) => `${String(value).slice(0, 6)}…${String(value).slice(-4)}`;
  const solverLabel = (row: Record<string, unknown>, field: string) =>
    solverName(chainId, String(row[field] ?? "")) || short(row[field]);
  const pairMatrix = props.hydrated.solver_pair_matrix;
  const pairHeatmap = useMemo(() => buildShareHeatmap({
    rows: rowsToObjects(toDataset(pairMatrix)),
    rowLabel: (row) => {
      const t0 = String(row.token0_symbol || "") || short(row.token0);
      const t1 = String(row.token1_symbol || "") || short(row.token1);
      return t0 && t1 ? `${t0}/${t1}` : "";
    },
    colLabel: (row) => solverLabel(row, "settlement_executor"),
    weightField: "fill_count",
    shareField: "pair_share",
  }), [pairMatrix, chainId]);
  const pairSpec = useMemo(() => shareHeatmapOption({ ...pairHeatmap, colorLabel: "pair share" }), [pairHeatmap]);
  const affinity = props.hydrated.trader_solver_affinity;
  const affinityHeatmap = useMemo(() => buildShareHeatmap({
    rows: rowsToObjects(toDataset(affinity)),
    rowLabel: (row) => short(row.trader ?? row.owner),
    colLabel: (row) => solverLabel(row, "settlement_executor"),
    weightField: "fill_count",
    shareField: "trader_share",
    maxRows: 25,
  }), [affinity, chainId]);
  const affinitySpec = useMemo(() => shareHeatmapOption({ ...affinityHeatmap, colorLabel: "trader share" }), [affinityHeatmap]);
  return (
    <>
      <div className="cow-inline-info">
        <InfoPopover label="What this section shows">
          Correlations the official explorer does not surface: which solvers win which pairs (specialization), whose order flow each solver settles (affinity), and whether the protocol fee policy correlates with execution quality. All figures cover the indexed window only.
        </InfoPopover>
      </div>
      <ChartSection datasetKey="solver_pair_matrix" title="Solver-pair specialization (share of each pair's fills)" props={props}>
        {pairHeatmap.cells.length > 0
          ? <ChartCard renderer="svg" chartId="cow-pair-matrix" hideId spec={pairSpec} />
          : <div className="cow-empty">No settled fills with resolvable executors in this indexed window.</div>}
      </ChartSection>
      <CollapsibleSection title="Specialization rows (raw)" defaultOpen={false}>
        <Table datasetKey="solver_pair_matrix" title="Solver-pair specialization (top 30 pairs)" props={props} />
      </CollapsibleSection>
      <GroupGate props={props} group="affinity">
        <ChartSection datasetKey="trader_solver_affinity" title="Trader-solver affinity (share of each trader's fills)" props={props}>
          {affinityHeatmap.cells.length > 0
            ? <ChartCard renderer="svg" chartId="cow-affinity" hideId spec={affinitySpec} />
            : <div className="cow-empty">No trader-solver affinity rows in this indexed window.</div>}
        </ChartSection>
        <CollapsibleSection title="Affinity rows (raw)" defaultOpen={false}>
          <Table datasetKey="trader_solver_affinity" title="Trader-solver affinity (top 100 traders)" props={props} />
        </CollapsibleSection>
      </GroupGate>
      <GroupGate props={props} group="quality">
        <Table datasetKey="fee_policy_quality" title="Fee-policy impact on execution quality" props={props} />
        <Table datasetKey="quote_delta_quality" title="Execution vs quote (bps) by fee-policy family" props={props} />
      </GroupGate>
    </>
  );
}

export function SectionViews(props: Props) {
  // FACET-HOOK dispatch: a non-null facet whose host section is the rendered
  // server section replaces the host section's default view. CowExplorerApp
  // guarantees the host equality by construction; the check here keeps a
  // stale facet prop from ever rendering over the wrong section's datasets.
  const facet = props.facet ?? null;
  if (facet && FACET_VIEWS[facet].section === props.state.section) {
    switch (facet) {
      case "order_types": return <OrderTypesSection {...props} />;
      case "solver_directory": return <SolverDirectorySection {...props} />;
      case "trader_dynamics": return <TraderDynamicsSection {...props} />;
    }
  }
  switch (props.state.section) {
    case "live":
      return (
        <GroupGate props={props} group="core">
          <LiveSection
            state={props.state}
            descriptors={props.descriptors}
            hydrated={props.hydrated}
            onEntity={props.onEntity}
            onRefreshLive={props.onRefreshLive}
            liveAutoDefault={props.liveAutoDefault}
          />
        </GroupGate>
      );
    case "overview": return <OverviewSection {...props} />;
    case "markets": return <Markets {...props} />;
    case "trades": return <Trades {...props} />;
    case "orders": return <Orders {...props} />;
    case "auctions": return <Auctions {...props} />;
    case "solvers": return <Solvers {...props} />;
    case "traders": return <Traders {...props} />;
    case "patterns": return <Patterns {...props} />;
    default: return null;
  }
}
