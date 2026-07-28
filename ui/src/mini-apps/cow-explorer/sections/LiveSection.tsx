// Live view: self-refreshing feeds over the freshest indexed data — fills as
// they settle, settlements landing, open intents waiting to execute, and the
// order-lifecycle stream — plus per-chain indexing pulse chips, a live KPI
// strip, and a per-minute heartbeat chart.
//
// All-networks mode (state.chain_id === 0, the server default for live): every
// feed row carries chain_id, the pulse chips become CLIENT-side chain filters
// (no refetch — the server feeds already merge every in-scope chain), and the
// poll cadence follows the MINIMUM checkpoint lag across chains: any fresh
// chain keeps the 30s cadence; only when every chain is behind does the view
// slow to 5 min and show the full stale banner ("N chains catching up" is the
// compact middle state).
//
// Refresh model: a 1s countdown drives a poll every 30s (5 min when stale —
// see above). The countdown pauses while the tab is hidden. The poll
// re-enqueues the live dataset groups; short server-side TTLs (10–30s) mean
// concurrent viewers share cache and ClickHouse sees at most one query per
// TTL per dataset.

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { EChartsOption } from "echarts";
import { ChartCard } from "../../../components/ChartCard";
import { MaSection } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { AmountWithToken } from "../components/AmountWithToken";
import { ChainBadge } from "../../shared/ChainBadge";
import { InfoPopover } from "../components/InfoPopover";
import { shortAddr } from "../../../utils/format";
import { CHAIN_SHORT_NAMES, chainSeriesColor } from "../../shared/chainIcons";
import { stackedSeriesOption } from "../model/chartOptions";
import { solverName } from "../model/solverRegistry";
import { rowsToObjects } from "../../shared/rowDataset";
import type { CowExplorerViewState, EntityType } from "../types";

const STALE_LAG_SECONDS = 600;
const LIVE_DELAY_SECONDS = 30;
const STALE_DELAY_SECONDS = 300;

interface LiveProps {
  state: CowExplorerViewState;
  descriptors: Record<string, DatasetDescriptor>;
  hydrated: Record<string, HydratedDataset>;
  onEntity: (entityType: EntityType, identifier: string, chainId?: number) => void;
  onRefreshLive?: () => void;
  liveAutoDefault?: boolean;
}

type Row = Record<string, unknown>;

function rowsFor(hydrated: Record<string, HydratedDataset>, key: string): Row[] {
  const value = hydrated[key];
  if (!value) return [];
  return rowsToObjects({ columns: value.columns, rows: value.rows });
}

function ageLabel(iso: unknown, now: number): string {
  const t = Date.parse(String(iso ?? ""));
  if (!Number.isFinite(t)) return "";
  const seconds = Math.max(0, Math.floor((now - t) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86_400)}d`;
}

function lagLabel(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "no data";
  if (seconds < 90) return `${Math.round(seconds)}s behind`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m behind`;
  if (seconds < 129_600) return `${Math.round(seconds / 3600)}h behind`;
  return `${Math.round(seconds / 86_400)}d behind`;
}

function rowLag(row: Row): number | null {
  return row.lag_seconds === null || row.lag_seconds === undefined
    ? null
    : Number(row.lag_seconds);
}

/** Client-side chain filter over merged all-networks feeds — pure, no refetch. */
export function filterRowsByChain(rows: Row[], chainFilter: number | null): Row[] {
  if (chainFilter === null) return rows;
  return rows.filter((row) => Number(row.chain_id) === chainFilter);
}

export interface LivePollState {
  /** Lag driving the poll cadence: the selected chain's lag single-chain, the
   * MINIMUM lag across chains all-networks (any fresh chain keeps 30s). */
  lag: number | null;
  /** True → 5-min cadence + full stale banner (single-chain: the selected
   * chain is behind; all-networks: EVERY reporting chain is behind). */
  stale: boolean;
  /** Chains whose lag exceeds the stale threshold (compact "catching up"
   * note when some-but-not-all are behind in all-networks mode). */
  staleChains: number;
  totalChains: number;
}

export function derivePollState(pulse: Row[], chainId: number): LivePollState {
  const lags = pulse.map(rowLag).filter((value): value is number => value !== null && Number.isFinite(value));
  const staleChains = lags.filter((value) => value > STALE_LAG_SECONDS).length;
  if (chainId !== 0) {
    const row = pulse.find((r) => Number(r.chain_id) === chainId);
    const lag = row ? rowLag(row) : null;
    return {
      lag,
      stale: lag !== null && lag > STALE_LAG_SECONDS,
      staleChains,
      totalChains: pulse.length,
    };
  }
  const lag = lags.length ? Math.min(...lags) : null;
  return {
    lag,
    stale: lag !== null && lag > STALE_LAG_SECONDS,
    staleChains,
    totalChains: pulse.length,
  };
}

export interface LiveKpis {
  fills1h: number;
  settlements1h: number;
  openIntents: number;
  activeSolvers: number;
  chainsFresh: number;
  chainsTotal: number;
}

/** Headline numbers for the live band. Feed-derived KPIs respect the active
 * chain filter; the chains-live ratio always covers every pulse row. */
export function deriveLiveKpis(args: {
  minuteActivity: Row[];
  openOrders: Row[];
  settlements: Row[];
  pulse: Row[];
  chainFilter: number | null;
}): LiveKpis {
  const minutes = filterRowsByChain(args.minuteActivity, args.chainFilter);
  const sum = (rows: Row[], field: string) =>
    rows.reduce((acc, row) => {
      const value = Number(row[field] ?? 0);
      return acc + (Number.isFinite(value) ? value : 0);
    }, 0);
  const solvers = new Set(
    filterRowsByChain(args.settlements, args.chainFilter)
      .map((row) => String(row.settlement_executor ?? ""))
      .filter(Boolean),
  );
  const chainsFresh = args.pulse.filter((row) => {
    const lag = rowLag(row);
    return lag !== null && lag <= STALE_LAG_SECONDS;
  }).length;
  return {
    fills1h: sum(minutes, "fills"),
    settlements1h: sum(minutes, "settlements"),
    openIntents: filterRowsByChain(args.openOrders, args.chainFilter).length,
    activeSolvers: solvers.size,
    chainsFresh,
    chainsTotal: args.pulse.length,
  };
}

/** Per-chain fills over the last hour (pulse-chip counts), unfiltered. */
export function fillsByChain(minuteActivity: Row[]): Map<number, number> {
  const totals = new Map<number, number>();
  for (const row of minuteActivity) {
    const chain = Number(row.chain_id);
    if (!Number.isFinite(chain)) continue;
    const fills = Number(row.fills ?? 0);
    if (!Number.isFinite(fills)) continue;
    totals.set(chain, (totals.get(chain) ?? 0) + fills);
  }
  return totals;
}

/** Heartbeat chart: fills per minute stacked by chain, in the shared per-chain
 * hues, band-sized (~160px) so it reads as a pulse line, not an analysis. */
export function heartbeatOption(minuteActivity: Row[], chainFilter: number | null): EChartsOption {
  const rows = filterRowsByChain(minuteActivity, chainFilter);
  const chainIds = [...new Set(rows.map((row) => Number(row.chain_id)).filter(Number.isFinite))];
  return {
    ...stackedSeriesOption(rows, {
      xField: "bucket",
      valueField: "fills",
      seriesField: "chain_id",
      mode: "absolute",
      kind: "bar",
      seriesColors: Object.fromEntries(chainIds.map((id) => [String(id), chainSeriesColor(id)])),
      seriesLabeler: (name) => CHAIN_SHORT_NAMES[Number(name)] ?? name,
    }),
    // Tighten the inherited stacked grid for the 160px band: the default
    // bottom:48 is proportionally heavy here and leaves dead space under the
    // bars. Keep top:42 for the chain legend; bottom:30 still fits one row of
    // horizontal time labels.
    grid: { left: 58, right: 24, top: 42, bottom: 30 },
    _cerebro_height: "160px",
  } as EChartsOption;
}

/** List with a highlight on rows that appeared since the previous render. */
function LiveFeed({ rows, keyOf, render, emptyLabel }: {
  rows: Row[];
  keyOf: (row: Row) => string;
  render: (row: Row, isNew: boolean) => ReactNode;
  emptyLabel: string;
}) {
  const seenRef = useRef<Set<string> | null>(null);
  const previous = seenRef.current;
  useEffect(() => {
    seenRef.current = new Set(rows.map(keyOf));
  });
  if (rows.length === 0) return <div className="cow-empty">{emptyLabel}</div>;
  return (
    <ul className="cow-live-feed">
      {rows.map((row) => {
        const key = keyOf(row);
        const isNew = previous !== null && !previous.has(key);
        return (
          <li key={key} className={isNew ? "cow-live-feed__row is-new" : "cow-live-feed__row"}>
            {render(row, isNew)}
          </li>
        );
      })}
    </ul>
  );
}

export function LiveSection(props: LiveProps) {
  const [now, setNow] = useState(() => Date.now());
  const [auto, setAuto] = useState(props.liveAutoDefault ?? true);
  const [countdown, setCountdown] = useState(LIVE_DELAY_SECONDS);
  // Client-side chain filter (all-networks mode only) — chips toggle it; the
  // feeds are already merged server-side, so filtering never refetches.
  const [chainFilter, setChainFilter] = useState<number | null>(null);
  const allNetworks = props.state.chain_id === 0;
  const effectiveFilter = allNetworks ? chainFilter : null;
  useEffect(() => {
    setChainFilter(null);
  }, [props.state.scope_id]);

  const pulse = rowsFor(props.hydrated, "live_pulse");
  const poll = derivePollState(pulse, props.state.chain_id);
  const stale = poll.stale;
  const delay = stale ? STALE_DELAY_SECONDS : LIVE_DELAY_SECONDS;
  const refreshRef = useRef(props.onRefreshLive);
  refreshRef.current = props.onRefreshLive;

  useEffect(() => {
    if (!auto) return;
    setCountdown(delay);
    const timer = setInterval(() => {
      setNow(Date.now());
      if (document.visibilityState !== "visible") return;
      setCountdown((current) => {
        if (current <= 1) {
          refreshRef.current?.();
          return delay;
        }
        return current - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [auto, delay, props.state.scope_id]);

  const trades = filterRowsByChain(rowsFor(props.hydrated, "live_trades"), effectiveFilter);
  const settlementsAll = rowsFor(props.hydrated, "live_settlements");
  const settlements = filterRowsByChain(settlementsAll, effectiveFilter);
  const openOrdersAll = rowsFor(props.hydrated, "live_open_orders");
  const openOrders = filterRowsByChain(openOrdersAll, effectiveFilter);
  const events = filterRowsByChain(rowsFor(props.hydrated, "live_order_events"), effectiveFilter);
  const minuteHydrated = props.hydrated.live_minute_activity;
  const minuteRows = useMemo(
    () => rowsFor(props.hydrated, "live_minute_activity"),
    [minuteHydrated],
  );
  const chipFills = useMemo(() => fillsByChain(minuteRows), [minuteRows]);
  const kpis = useMemo(
    () => deriveLiveKpis({
      minuteActivity: minuteRows,
      openOrders: openOrdersAll,
      settlements: settlementsAll,
      pulse,
      chainFilter: effectiveFilter,
    }),
    [minuteHydrated, props.hydrated.live_open_orders, props.hydrated.live_settlements, props.hydrated.live_pulse, effectiveFilter],
  );
  const heartbeatSpec = useMemo(
    () => heartbeatOption(minuteRows, effectiveFilter),
    [minuteHydrated, effectiveFilter],
  );
  const heartbeatHasRows = filterRowsByChain(minuteRows, effectiveFilter).length > 0;
  const chainId = props.state.chain_id;
  // Row chain: merged all-networks feeds always carry chain_id; single-chain
  // rows fall back to the selected chain. Entity clicks and solver naming use
  // the ROW's chain, never the (possibly 0) view chain.
  const chainOf = (row: Row) => {
    const value = Number(row.chain_id);
    return Number.isFinite(value) && value > 0 ? value : chainId;
  };
  const feedAmount = (row: Row, side: "sell" | "buy", rowChain: number) => (
    <AmountWithToken
      state={props.state}
      chainId={rowChain}
      token={String(row[`${side}_token`] ?? "")}
      symbol={(row[`${side}_symbol`] as string | undefined) || undefined}
      amountRaw={row[`${side}_amount_raw`] as string | number | null | undefined}
      amount={row[`${side}_amount`] as number | null | undefined}
      decimals={row[`${side}_decimals`] as number | null | undefined}
    />
  );
  const rowBadge = (rowChain: number) => (
    allNetworks ? <ChainBadge chainId={rowChain} showName={false} /> : null
  );
  const format = (value: number) => value.toLocaleString();

  return (
    <>
      <div className="cow-live-bar">
        <div className="cow-live-pulse">
          {allNetworks && pulse.length > 0 && (
            <button
              type="button"
              className={`cow-live-chip cow-live-chip--all${chainFilter === null ? " is-active" : ""}`}
              onClick={() => setChainFilter(null)}
              title="Show every network"
            >
              All
            </button>
          )}
          {pulse.map((row) => {
            const rowChain = Number(row.chain_id);
            const lag = rowLag(row);
            const tone = lag === null ? "none" : lag <= STALE_LAG_SECONDS ? "ok" : "stale";
            const active = allNetworks ? chainFilter === rowChain : rowChain === chainId;
            const className = `cow-live-chip cow-live-chip--${tone}${active ? " is-active" : ""}`;
            const fills = chipFills.get(rowChain) ?? 0;
            const body = (
              <>
                <ChainBadge chainId={rowChain} showName={false} />
                <span className="cow-live-chip__lag">{lagLabel(lag)}</span>
                {allNetworks && <span className="cow-live-chip__count">{format(fills)} fills</span>}
              </>
            );
            const title = `Checkpoint block ${row.checkpoint_block ?? "?"} · ${lagLabel(lag)} · ${format(fills)} fills last hour`;
            return allNetworks ? (
              <button
                key={rowChain}
                type="button"
                className={className}
                title={`${title} — click to filter`}
                onClick={() => setChainFilter((current) => (current === rowChain ? null : rowChain))}
              >
                {body}
              </button>
            ) : (
              <span key={rowChain} className={className} title={title}>
                {body}
              </span>
            );
          })}
        </div>
        <div className="cow-live-controls">
          <button type="button" onClick={() => setAuto((value) => !value)}>
            {auto ? `⏸ ${countdown}s` : "▶ Resume"}
          </button>
          <InfoPopover label="Live methodology">
            Feeds show data as the indexer commits it — indexed reality, not the mempool. The refresh loop polls every {LIVE_DELAY_SECONDS}s ({STALE_DELAY_SECONDS}s when indexing is behind; in all-networks mode the cadence follows the FRESHEST chain), pauses while the tab is hidden, and never widens queries beyond the last hour. Chain chips filter the merged feeds client-side — no extra queries.
          </InfoPopover>
        </div>
      </div>
      {stale && (
        <div className="cow-live-stale" role="status">
          {allNetworks
            ? `Indexing is catching up on every network — the freshest checkpoint is ${lagLabel(poll.lag)}. The feed shows the newest INDEXED data, not the chain head; polling slowed to ${STALE_DELAY_SECONDS}s.`
            : `Indexing is catching up on ${props.state.chain_name} — the checkpoint is ${lagLabel(poll.lag)}. The feed shows the newest INDEXED data, not the chain head; polling slowed to ${STALE_DELAY_SECONDS}s.`}
        </div>
      )}
      {!stale && allNetworks && poll.staleChains > 0 && (
        <div className="cow-live-catchup" role="status">
          {poll.staleChains} chain{poll.staleChains === 1 ? "" : "s"} catching up — feeds stay live from the fresh chains.
        </div>
      )}
      <div className="cow-metric-strip" aria-label="Live headline numbers">
        <span><strong>{format(kpis.fills1h)}</strong>Fills (1h)</span>
        <span><strong>{format(kpis.settlements1h)}</strong>Settlements (1h)</span>
        <span><strong>{format(kpis.openIntents)}</strong>Open intents</span>
        <span><strong>{format(kpis.activeSolvers)}</strong>Active solvers (1h)</span>
        <span><strong>{kpis.chainsFresh}/{kpis.chainsTotal}</strong>Chains live</span>
      </div>
      {heartbeatHasRows && (
        <div className="cow-live-heartbeat">
          <ChartCard
            renderer="svg"
            chartId="cow-live-heartbeat"
            hideId
            title="Fills per minute (last hour)"
            spec={heartbeatSpec}
          />
        </div>
      )}
      <div className="cow-live-grid">
        <MaSection title="Fills as they settle" meta={<InfoPopover label="Window">Last hour of indexed fills, newest first (max 50).</InfoPopover>}>
          <LiveFeed
            rows={trades}
            keyOf={(row) => `${row.chain_id}:${row.tx_hash}:${row.log_index}`}
            emptyLabel="No fills indexed in the last hour."
            render={(row) => {
              const rowChain = chainOf(row);
              return (
                <>
                  <span className="cow-live-time">{ageLabel(row.block_timestamp, now)}</span>
                  {rowBadge(rowChain)}
                  <button type="button" className="cow-live-main" onClick={() => props.onEntity("transaction", String(row.tx_hash), rowChain)}>
                    {feedAmount(row, "sell", rowChain)}
                    <span className="cow-live-arrow">→</span>
                    {feedAmount(row, "buy", rowChain)}
                  </button>
                  <button type="button" className="cow-live-side" title={String(row.owner)} onClick={() => props.onEntity("address", String(row.owner), rowChain)}>
                    {shortAddr(String(row.owner))}
                  </button>
                </>
              );
            }}
          />
        </MaSection>
        <MaSection title="Settlements landing" meta={<InfoPopover label="Window">Last hour of settlements with their fill counts (max 30).</InfoPopover>}>
          <LiveFeed
            rows={settlements}
            keyOf={(row) => `${row.chain_id}:${row.tx_hash}`}
            emptyLabel="No settlements indexed in the last hour."
            render={(row) => {
              const rowChain = chainOf(row);
              return (
                <>
                  <span className="cow-live-time">{ageLabel(row.block_timestamp, now)}</span>
                  {rowBadge(rowChain)}
                  <button type="button" className="cow-live-main" onClick={() => props.onEntity("transaction", String(row.tx_hash), rowChain)}>
                    {Number(row.fill_count).toLocaleString()} fill{Number(row.fill_count) === 1 ? "" : "s"} · {shortAddr(String(row.tx_hash))}
                  </button>
                  <button type="button" className="cow-live-side cow-solver" title={String(row.settlement_executor)} onClick={() => props.onEntity("solver", String(row.settlement_executor), rowChain)}>
                    {solverName(rowChain, String(row.settlement_executor)) || shortAddr(String(row.settlement_executor))}
                  </button>
                </>
              );
            }}
          />
        </MaSection>
        <MaSection title="Waiting to execute" meta={<InfoPopover label="What this is">Observed OPEN intents (valid, not yet fully executed) — a snapshot of what the indexer knows, NOT the complete live orderbook.</InfoPopover>}>
          <LiveFeed
            rows={openOrders}
            keyOf={(row) => `${row.chain_id}:${row.order_uid}`}
            emptyLabel="No known open intents on this chain."
            render={(row) => {
              const rowChain = chainOf(row);
              const ratio = Math.round(Number(row.fill_ratio ?? 0) * 100);
              return (
                <>
                  <span className="cow-live-time" title={String(row.creation_date)}>{ageLabel(row.creation_date, now)}</span>
                  {rowBadge(rowChain)}
                  <button type="button" className="cow-live-main" onClick={() => props.onEntity("order", String(row.order_uid), rowChain)}>
                    {feedAmount(row, "sell", rowChain)}
                    <span className="cow-live-arrow">→</span>
                    {feedAmount(row, "buy", rowChain)}
                  </button>
                  <span className="cow-live-fill" title={`${ratio}% filled`}>
                    <span style={{ width: `${ratio}%` }} />
                  </span>
                </>
              );
            }}
          />
        </MaSection>
        <MaSection title="Order lifecycle stream" meta={<InfoPopover label="Window">Lifecycle events observed by the indexer in the last hour (on-chain events + API status transitions).</InfoPopover>}>
          <LiveFeed
            rows={events}
            keyOf={(row) => `${row.chain_id}:${row.order_uid}:${row.event_type}:${row.source_observed_at}`}
            emptyLabel="No lifecycle events observed in the last hour."
            render={(row) => {
              const rowChain = chainOf(row);
              return (
                <>
                  <span className="cow-live-time">{ageLabel(row.source_observed_at, now)}</span>
                  {rowBadge(rowChain)}
                  <span className="cow-live-event">{String(row.event_type).replace("status:", "")}</span>
                  <button type="button" className="cow-live-side" title={String(row.order_uid)} onClick={() => props.onEntity("order", String(row.order_uid), rowChain)}>
                    {shortAddr(String(row.order_uid))}
                  </button>
                </>
              );
            }}
          />
        </MaSection>
      </div>
    </>
  );
}
