// Live view: self-refreshing feeds over the freshest indexed data — fills as
// they settle, settlements landing, open intents waiting to execute, and the
// order-lifecycle stream — plus per-chain indexing pulse / backfill bars.
//
// Refresh model: a 1s countdown drives a poll every 30s (5 min when the
// selected chain's checkpoint lag exceeds the stale threshold — then the view
// shows a "catching up" banner instead of pretending to be live). The
// countdown pauses while the tab is hidden. The poll re-enqueues the live
// dataset groups; short server-side TTLs (10–30s) mean concurrent viewers
// share cache and ClickHouse sees at most one query per TTL per dataset.

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { MaSection } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { ChainBadge } from "../components/ChainBadge";
import { InfoPopover } from "../components/InfoPopover";
import { displayAmount, displayToken, shortAddr } from "../model/identity";
import { solverName } from "../model/solverRegistry";
import { rowsToObjects } from "../model/parseRows";
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

function amountText(row: Row, side: "sell" | "buy"): string {
  const display = displayAmount(
    row[`${side}_amount_raw`] as string | undefined,
    row[`${side}_amount`] as number | null | undefined,
    row[`${side}_decimals`] as number | null | undefined,
  );
  const symbol = displayToken(
    String(row[`${side}_token`] ?? ""),
    row[`${side}_symbol`] as string | undefined,
  );
  return `${display.text}${display.rawUnits ? "⚠" : ""} ${symbol}`;
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
  const pulse = rowsFor(props.hydrated, "live_pulse");
  const currentPulse = pulse.find((row) => Number(row.chain_id) === props.state.chain_id);
  const currentLag = currentPulse?.lag_seconds === null || currentPulse?.lag_seconds === undefined
    ? null
    : Number(currentPulse.lag_seconds);
  const stale = currentLag !== null && currentLag > STALE_LAG_SECONDS;
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

  const trades = rowsFor(props.hydrated, "live_trades");
  const settlements = rowsFor(props.hydrated, "live_settlements");
  const openOrders = rowsFor(props.hydrated, "live_open_orders");
  const events = rowsFor(props.hydrated, "live_order_events");
  const chainId = props.state.chain_id;

  return (
    <>
      <div className="cow-live-bar">
        <div className="cow-live-pulse">
          {pulse.map((row) => {
            const rowChain = Number(row.chain_id);
            const lag = row.lag_seconds === null || row.lag_seconds === undefined
              ? null
              : Number(row.lag_seconds);
            const tone = lag === null ? "none" : lag <= STALE_LAG_SECONDS ? "ok" : "stale";
            return (
              <span key={rowChain} className={`cow-live-chip cow-live-chip--${tone}${rowChain === chainId ? " is-active" : ""}`} title={`Checkpoint block ${row.checkpoint_block ?? "?"} · ${lagLabel(lag)}`}>
                <ChainBadge chainId={rowChain} showName={false} />
                <span>{lagLabel(lag)}</span>
              </span>
            );
          })}
        </div>
        <div className="cow-live-controls">
          <button type="button" onClick={() => setAuto((value) => !value)}>
            {auto ? `⏸ ${countdown}s` : "▶ Resume"}
          </button>
          <InfoPopover label="Live methodology">
            Feeds show data as the indexer commits it — indexed reality, not the mempool. The refresh loop polls every {LIVE_DELAY_SECONDS}s ({STALE_DELAY_SECONDS}s when the chain is behind), pauses while the tab is hidden, and never widens queries beyond the last hour.
          </InfoPopover>
        </div>
      </div>
      {stale && (
        <div className="cow-live-stale" role="status">
          Indexing is catching up on {props.state.chain_name} — the checkpoint is {lagLabel(currentLag)}. The feed shows the newest INDEXED data, not the chain head; polling slowed to {STALE_DELAY_SECONDS}s.
        </div>
      )}
      <div className="cow-live-grid">
        <MaSection title="Fills as they settle" meta={<InfoPopover label="Window">Last hour of indexed fills, newest first (max 50).</InfoPopover>}>
          <LiveFeed
            rows={trades}
            keyOf={(row) => `${row.tx_hash}:${row.log_index}`}
            emptyLabel="No fills indexed in the last hour."
            render={(row) => (
              <>
                <span className="cow-live-time">{ageLabel(row.block_timestamp, now)}</span>
                <button type="button" className="cow-live-main" onClick={() => props.onEntity("transaction", String(row.tx_hash), chainId)}>
                  {amountText(row, "sell")} → {amountText(row, "buy")}
                </button>
                <button type="button" className="cow-live-side" title={String(row.owner)} onClick={() => props.onEntity("address", String(row.owner), chainId)}>
                  {shortAddr(String(row.owner))}
                </button>
              </>
            )}
          />
        </MaSection>
        <MaSection title="Settlements landing" meta={<InfoPopover label="Window">Last hour of settlements with their fill counts (max 30).</InfoPopover>}>
          <LiveFeed
            rows={settlements}
            keyOf={(row) => String(row.tx_hash)}
            emptyLabel="No settlements indexed in the last hour."
            render={(row) => (
              <>
                <span className="cow-live-time">{ageLabel(row.block_timestamp, now)}</span>
                <button type="button" className="cow-live-main" onClick={() => props.onEntity("transaction", String(row.tx_hash), chainId)}>
                  {Number(row.fill_count).toLocaleString()} fill{Number(row.fill_count) === 1 ? "" : "s"} · {shortAddr(String(row.tx_hash))}
                </button>
                <button type="button" className="cow-live-side cow-solver" title={String(row.settlement_executor)} onClick={() => props.onEntity("solver", String(row.settlement_executor), chainId)}>
                  {solverName(chainId, String(row.settlement_executor)) || shortAddr(String(row.settlement_executor))}
                </button>
              </>
            )}
          />
        </MaSection>
        <MaSection title="Waiting to execute" meta={<InfoPopover label="What this is">Observed OPEN intents (valid, not yet fully executed) — a snapshot of what the indexer knows, NOT the complete live orderbook.</InfoPopover>}>
          <LiveFeed
            rows={openOrders}
            keyOf={(row) => String(row.order_uid)}
            emptyLabel="No known open intents on this chain."
            render={(row) => {
              const ratio = Math.round(Number(row.fill_ratio ?? 0) * 100);
              return (
                <>
                  <span className="cow-live-time" title={String(row.creation_date)}>{ageLabel(row.creation_date, now)}</span>
                  <button type="button" className="cow-live-main" onClick={() => props.onEntity("order", String(row.order_uid), chainId)}>
                    {amountText(row, "sell")} → {amountText(row, "buy")}
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
            keyOf={(row) => `${row.order_uid}:${row.event_type}:${row.source_observed_at}`}
            emptyLabel="No lifecycle events observed in the last hour."
            render={(row) => (
              <>
                <span className="cow-live-time">{ageLabel(row.source_observed_at, now)}</span>
                <span className="cow-live-event">{String(row.event_type).replace("status:", "")}</span>
                <button type="button" className="cow-live-side" title={String(row.order_uid)} onClick={() => props.onEntity("order", String(row.order_uid), chainId)}>
                  {shortAddr(String(row.order_uid))}
                </button>
              </>
            )}
          />
        </MaSection>
      </div>
    </>
  );
}
