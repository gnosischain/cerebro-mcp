// Pair order-book depth panel (Markets section, `markets.depth` group).
//
// Self-contained: the sections agent mounts <DepthPanel {...props} /> inside
// Markets — props mirror SectionViews' SectionProps shape (state, descriptors,
// hydrated, viewId, fetchRows, onEntity, failedGroups/onRetryGroup) plus the
// DEPTH-HOOK `onLoadDepthAt` threaded from CowExplorerApp.
//
// Server contract (pair_depth): one row per known open order; `price` is
// ALWAYS quote-per-base for BOTH sides. Flip, cumulation, range zoom, and the
// reference marker are pure client re-projections. `state.depth_at` is "" for
// the live book or an ISO timestamp for a reconstructed point-in-time book;
// changing it is ONE additive group call (`onLoadDepthAt`), debounced 400ms.

import { useEffect, useMemo, useRef, useState } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { MaSection } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor, PageRowsResponse } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { datasetError } from "../../shared/datasetError";
import { rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import { depthHeatmapOption, pairDepthOption } from "../model/chartOptions";
import { DATASET_DOCS } from "../model/datasetDocs";
import {
  buildDepthLadder,
  countSummary,
  flipOrders,
  parsePairDepth,
} from "../model/depthLadder";
import { buildDepthHeatmap, parseHeatmapRows } from "../model/depthHeatmap";
import { parseReferencePrices } from "../model/parseRows";
import type { ReferencePriceRow } from "../types";
import type { CowExplorerViewState, EntityType } from "../types";
import { formatTime } from "./cells";
import { CuratedTable } from "./CuratedTable";
import { InfoBlocks, InfoPopover } from "./InfoPopover";

type FetchRows = (viewId: string, datasetKey: string, pageToken?: string) => Promise<PageRowsResponse | null>;

/** DEPTH-HOOK prop threaded through SectionViews (CowExplorerApp implements
 * it): request the `markets.depth` group at `ts` — an ISO-8601 timestamp
 * reconstructs the book at that moment, the literal "live" returns to the
 * live book. Optional so SectionViews compiles unchanged until wired. */
export interface DepthHostProps {
  onLoadDepthAt?: (ts: string | "live") => void;
  /** DEPTH-HEATMAP-HOOK: request the deferred `markets.depth_heatmap` group for
   * the given window ("24h"/"7d"/"all"). Loaded on demand when the Heatmap tab
   * is opened, never on a history-slider tick. */
  onLoadDepthHeatmap?: (window: HeatmapWindow) => void;
}

export type HeatmapWindow = "24h" | "7d" | "all";

const HEATMAP_WINDOWS: Array<{ label: string; value: HeatmapWindow }> = [
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "all", value: "all" },
];

export interface DepthPanelProps extends DepthHostProps {
  state: CowExplorerViewState;
  descriptors: Record<string, DatasetDescriptor>;
  hydrated: Record<string, HydratedDataset>;
  viewId: string;
  fetchRows: FetchRows;
  onEntity?: (entityType: EntityType, identifier: string, chainId?: number) => void;
  /** Pair click-through (SectionProps member — arrives via the props spread).
   * Powers the empty-state "pairs with a standing book" rescue chips. */
  onSelectPair?: (base: string, quote: string, chainId?: number) => void;
  /** `${section}.${group}` keys whose deferred load failed (current scope). */
  failedGroups?: string[];
  onRetryGroup?: (section: string, group: string) => void;
}

/** One `open_intent_pairs` row — pairs on this chain with a standing book. */
export interface OpenIntentPair {
  token0: string;
  token1: string;
  token0Symbol: string;
  token1Symbol: string;
  openOrders: number;
}

export function parseOpenIntentPairs(dataset: RowDataset | null | undefined): OpenIntentPair[] {
  if (!dataset) return [];
  const pairs: OpenIntentPair[] = [];
  for (const row of rowsToObjects(dataset)) {
    const token0 = String(row.token0 ?? "");
    const token1 = String(row.token1 ?? "");
    const openOrders = Number(row.open_orders ?? 0);
    if (!token0 || !token1 || !Number.isFinite(openOrders) || openOrders <= 0) continue;
    pairs.push({
      token0,
      token1,
      token0Symbol: String(row.token0_symbol ?? ""),
      token1Symbol: String(row.token1_symbol ?? ""),
      openOrders,
    });
  }
  return pairs;
}

const RANGE_PRESETS: Array<{ label: string; pct: number | null }> = [
  { label: "±5%", pct: 5 },
  { label: "±10%", pct: 10 },
  { label: "±25%", pct: 25 },
  { label: "±50%", pct: 50 },
  { label: "All", pct: null },
];

const HISTORY_PRESETS: Array<{ label: string; seconds: number }> = [
  { label: "-1h", seconds: 3600 },
  { label: "-6h", seconds: 6 * 3600 },
  { label: "-1d", seconds: 86_400 },
  { label: "-7d", seconds: 7 * 86_400 },
];

const DEBOUNCE_MS = 400;
const SLIDER_STEP_SECONDS = 300;

/** Sane display for prices that may span many decades (mirrors the chart
 * builder's ladder formatter, which is not exported). */
function formatPrice(value: number): string {
  if (!Number.isFinite(value)) return "";
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 0.001 || abs >= 1e9)) return value.toExponential(3);
  return value.toLocaleString("en-US", { maximumSignificantDigits: 6 });
}

function isoAtSeconds(seconds: number): string {
  return new Date(seconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** X-range around mid for a ± percentage preset; null = full extent. */
export function depthRange(
  mid: number | null,
  pct: number | null,
): { min: number; max: number } | null {
  if (mid === null || !Number.isFinite(mid) || mid <= 0 || pct === null) return null;
  return { min: Math.max(0, mid * (1 - pct / 100)), max: mid * (1 + pct / 100) };
}

/** Latest reference price at-or-before the viewed time from the ALREADY
 * LOADED native reference series (never fetched, never faked — null when no
 * eligible point exists). `viewedAtMs` null = live view (latest point).
 * Flipped books invert the reference (price' = 1/p). */
export function pickReferencePrice(
  rows: ReferencePriceRow[],
  viewedAtMs: number | null,
  flipped: boolean,
): number | null {
  let best: ReferencePriceRow | null = null;
  let bestT = -Infinity;
  for (const row of rows) {
    const t = Date.parse(row.bucket);
    if (!Number.isFinite(t)) continue;
    if (viewedAtMs !== null && t > viewedAtMs) continue;
    if (t >= bestT) {
      bestT = t;
      best = row;
    }
  }
  if (!best || !(best.price > 0)) return null;
  return flipped ? 1 / best.price : best.price;
}

function toDataset(value?: HydratedDataset): RowDataset | undefined {
  return value ? { columns: value.columns, rows: value.rows } : undefined;
}

function coverageMeta(descriptor?: DatasetDescriptor): string {
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

/** Minimal error card with the shared .cow-dataset-error classes — the shared
 * DatasetErrorCard lives inside sections/SectionViews.tsx and importing it
 * here would create a SectionViews → DepthPanel → SectionViews cycle once the
 * panel is mounted there. */
function DepthErrorCard({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="cow-dataset-error" role="alert">
      <div className="cow-dataset-error__msg">
        <strong>This dataset failed to load.</strong>
        <span>{error}</span>
      </div>
      {onRetry ? <button type="button" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}

export function DepthPanel(props: DepthPanelProps) {
  const { state } = props;
  const [tab, setTab] = useState<"chart" | "list" | "heatmap">("chart");
  const [flipped, setFlipped] = useState(false);
  const [rangePct, setRangePct] = useState<number | null>(50);
  const depthAt = state.depth_at ?? "";
  const stateWindow = state.heatmap_window;
  const [heatmapWindow, setHeatmapWindow] = useState<HeatmapWindow>(
    stateWindow === "24h" || stateWindow === "all" ? stateWindow : "7d",
  );
  // Draft controls for the historical bar (synced from the applied server
  // value; local edits dispatch through the shared 400ms debounce).
  const [draftAt, setDraftAt] = useState("");
  const [sliderSec, setSliderSec] = useState<number | null>(null);
  const debounceRef = useRef<number | null>(null);
  useEffect(() => () => {
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
  }, []);
  const pairKey = `${state.pair.base}|${state.pair.quote}`;
  useEffect(() => {
    setFlipped(false);
  }, [pairKey]);
  useEffect(() => {
    setDraftAt(depthAt ? depthAt.replace("Z", "").slice(0, 16) : "");
    const parsed = depthAt ? Date.parse(depthAt) : NaN;
    setSliderSec(Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null);
  }, [depthAt]);

  const requestDepthAt = (ts: string | "live") => {
    if (!props.onLoadDepthAt) return;
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      props.onLoadDepthAt?.(ts);
    }, DEBOUNCE_MS);
  };

  const depthHydrated = props.hydrated.pair_depth;
  const rawRows = useMemo(() => rowsToObjects(toDataset(depthHydrated)), [depthHydrated]);
  const orders = useMemo(() => parsePairDepth(toDataset(depthHydrated)), [depthHydrated]);
  const displayOrders = useMemo(() => (flipped ? flipOrders(orders) : orders), [orders, flipped]);
  const ladder = useMemo(() => buildDepthLadder(displayOrders), [displayOrders]);
  const referenceRows = useMemo(
    () => parseReferencePrices(toDataset(props.hydrated.native_reference_prices)),
    [props.hydrated.native_reference_prices],
  );

  const short = (value: string) => (value.length > 12 ? `${value.slice(0, 6)}…${value.slice(-4)}` : value);
  const baseSym0 = state.pair.base_symbol || (state.pair.base ? short(state.pair.base) : "Base");
  const quoteSym0 = state.pair.quote_symbol || (state.pair.quote ? short(state.pair.quote) : "Quote");
  const baseSymbol = flipped ? quoteSym0 : baseSym0;
  const quoteSymbol = flipped ? baseSym0 : quoteSym0;

  const viewedAtMs = depthAt ? Date.parse(depthAt) : null;
  const reference = useMemo(
    () => pickReferencePrice(referenceRows, Number.isFinite(viewedAtMs as number) ? viewedAtMs : null, flipped),
    [referenceRows, viewedAtMs, flipped],
  );
  const range = depthRange(ladder.mid, rangePct);
  const chartSpec = useMemo(
    () => pairDepthOption({
      bids: ladder.bids,
      asks: ladder.asks,
      mid: ladder.mid,
      reference,
      baseSymbol,
      quoteSymbol,
      range,
    }),
    [ladder, reference, baseSymbol, quoteSymbol, rangePct],
  );

  // Keep the local window in sync with the server's applied value (resets to
  // "7d" on every section apply, mirroring depth_at).
  useEffect(() => {
    if (stateWindow === "24h" || stateWindow === "7d" || stateWindow === "all") {
      setHeatmapWindow(stateWindow);
    }
  }, [stateWindow]);

  // DEPTH-HEATMAP-HOOK: request the deferred group when the Heatmap tab is first
  // opened and whenever the pair/scope/window changes while it is open. The
  // group is excluded from the app's background auto-sync, so this is the only
  // trigger — no wasted heavy query when the tab is never viewed.
  const hasPairForHeatmap = Boolean(state.pair.base && state.pair.quote);
  const heatmapReqRef = useRef<string>("");
  const heatmapReqKey = `${state.scope_id ?? ""}|${pairKey}|${heatmapWindow}`;
  useEffect(() => {
    if (tab !== "heatmap" || !hasPairForHeatmap || !props.onLoadDepthHeatmap) return;
    if (heatmapReqRef.current === heatmapReqKey) return;
    heatmapReqRef.current = heatmapReqKey;
    props.onLoadDepthHeatmap(heatmapWindow);
  }, [tab, heatmapReqKey, hasPairForHeatmap]);

  const heatmapHydrated = props.hydrated.pair_depth_heatmap;
  const heatmapRows = useMemo(
    () => parseHeatmapRows(toDataset(heatmapHydrated)),
    [heatmapHydrated],
  );
  const heatmapModel = useMemo(
    () => buildDepthHeatmap({ rows: heatmapRows, flipped, rangePct }),
    [heatmapRows, flipped, rangePct],
  );
  const heatmapSpec = useMemo(
    () => depthHeatmapOption({
      xLabels: heatmapModel.xLabels,
      yLabels: heatmapModel.yLabels,
      cells: heatmapModel.cells,
      midLine: heatmapModel.midLine,
      colorBound: heatmapModel.colorBound,
      baseSymbol,
      quoteSymbol,
    }),
    [heatmapModel, baseSymbol, quoteSymbol],
  );
  // Drill-down: click ANYWHERE in a heatmap column -> reconstruct that bucket's
  // full 2D order book and jump to the Depth chart tab. Bound at the zrender
  // level (not per-cell onEvents) with convertFromPixel, so a click between the
  // thin cells of a column still resolves to that column's timestamp. xLabels
  // is read from a ref so the one-time handler always sees the latest buckets.
  const xLabelsRef = useRef<string[]>([]);
  xLabelsRef.current = heatmapModel.xLabels;
  const onHeatmapReady = useMemo(
    () => (chart: unknown) => {
      const ec = chart as {
        getZr: () => { on: (e: string, cb: (p: { offsetX: number; offsetY: number }) => void) => void };
        convertFromPixel: (finder: unknown, pixel: [number, number]) => number[] | null;
      };
      ec.getZr().on("click", (ev) => {
        const grid = ec.convertFromPixel({ seriesIndex: 0 }, [ev.offsetX, ev.offsetY]);
        if (!grid) return;
        const xi = Math.round(grid[0]);
        const bucket = xLabelsRef.current[xi];
        if (!bucket || !props.onLoadDepthAt) return;
        const iso = bucket.length >= 16 && !bucket.endsWith("Z") ? `${bucket}Z` : bucket;
        setTab("chart");
        requestDepthAt(iso);
      });
    },
    // eslint-disable-line react-hooks/exhaustive-deps
    [],
  );
  const heatmapLoaded = state.loaded_groups?.["markets.depth_heatmap"] === true;
  const heatmapGroupFailed = props.failedGroups?.includes("markets.depth_heatmap") ?? false;

  const horizonRow = rowsToObjects(toDataset(props.hydrated.depth_horizon))[0];
  const horizonEarliest = String(horizonRow?.earliest_supported_at ?? "");
  const horizonEarliestSec = Math.ceil((Date.parse(horizonEarliest) || NaN) / 1000);
  const nowSec = Math.floor(Date.now() / 1000);
  const sliderMin = Number.isFinite(horizonEarliestSec) ? horizonEarliestSec : null;
  const sliderValue = sliderSec ?? nowSec;

  const observedAt = useMemo(() => {
    let latest = "";
    for (const row of rawRows) {
      const value = String(row.source_observed_at ?? "");
      if (value > latest) latest = value;
    }
    if (latest) return latest;
    const coverage = props.descriptors.pair_depth?.provenance?.coverage as
      | { latest_source_observation?: string | null }
      | undefined;
    return String(coverage?.latest_source_observation ?? "");
  }, [rawRows, props.descriptors.pair_depth]);

  const hasPair = Boolean(state.pair.base && state.pair.quote);
  const descriptor = props.descriptors.pair_depth;
  const error = descriptor ? datasetError(descriptor) : "";
  const groupFailed = props.failedGroups?.includes("markets.depth") ?? false;
  const groupLoading = state.loaded_groups?.["markets.depth"] === false;
  const retry = props.onRetryGroup ? () => props.onRetryGroup?.("markets", "depth") : undefined;

  const info = (
    <InfoPopover label="About this data">
      <InfoBlocks
        what={DATASET_DOCS.pair_depth?.what}
        method={DATASET_DOCS.pair_depth?.method}
        coverage={coverageMeta(descriptor)}
      />
    </InfoPopover>
  );

  if (!hasPair) {
    return (
      <MaSection title="Order-book depth" meta={info}>
        <div className="cow-empty">Pick a pair above to see its known order-book depth.</div>
      </MaSection>
    );
  }

  const midline = (() => {
    if (ladder.mid === null) return <span className="cow-depth-mid__empty">No known open intents</span>;
    return (
      <>
        <span className="cow-depth-mid__price">
          1 {baseSymbol} {ladder.midKind === "two_sided" ? "=" : "≈"} {formatPrice(ladder.mid)} {quoteSymbol}
        </span>
        {ladder.midKind === "bid_only" && <span className="cow-depth-mid__note">best bid only</span>}
        {ladder.midKind === "ask_only" && <span className="cow-depth-mid__note">best ask only</span>}
        {ladder.crossed && (
          <span
            className="cow-depth-crossed"
            title="Best bid above best ask — legitimate on CoW: batch auctions can clear crossed intents."
          >
            crossed book
          </span>
        )}
      </>
    );
  })();

  const emptyBook = orders.length === 0;
  const emptyLabel = depthAt
    ? "No reconstructable open intents at this time — the book may pre-date the capture window."
    : "No known open intents for this pair right now.";
  // Rescue path for empty books: some chains (e.g. Gnosis) run almost
  // entirely on short-lived market orders and hold ZERO standing intents at
  // any moment — steer the user toward pairs (or networks) that have a book.
  const openPairs = useMemo(
    () => parseOpenIntentPairs(toDataset(props.hydrated.open_intent_pairs)),
    [props.hydrated.open_intent_pairs],
  );
  const chainOpenTotal = openPairs.reduce((sum, p) => sum + p.openOrders, 0);
  const emptyGuidance = (() => {
    if (!emptyBook) return null;
    if (openPairs.length > 0) {
      return (
        <div className="cow-depth-rescue">
          <span className="cow-depth-rescue__label">
            Pairs with a standing book on {state.chain_name || "this network"} right now:
          </span>
          <div className="cow-depth-rescue__chips">
            {openPairs.slice(0, 8).map((p) => (
              <button
                key={`${p.token0}|${p.token1}`}
                type="button"
                className="cow-depth-rescue__chip"
                disabled={!props.onSelectPair}
                onClick={() => props.onSelectPair?.(p.token0, p.token1)}
                title={`${p.token0} / ${p.token1}`}
              >
                {(p.token0Symbol || short(p.token0))}/{p.token1Symbol || short(p.token1)}
                <span className="cow-depth-rescue__count">{p.openOrders}</span>
              </button>
            ))}
          </div>
        </div>
      );
    }
    if (props.hydrated.open_intent_pairs && chainOpenTotal === 0) {
      return (
        <div className="cow-depth-rescue">
          <span className="cow-depth-rescue__label">
            {state.chain_name || "This network"} currently has no standing open intents on any
            pair — its order flow is dominated by short-lived market orders that fill within
            minutes. Switch networks to find a live book.
          </span>
        </div>
      );
    }
    return null;
  })();

  const body = (() => {
    // Heatmap is its own deferred group (markets.depth_heatmap) with data
    // independent of the live/reconstructed snapshot — handle it before the
    // markets.depth group checks below.
    if (tab === "heatmap") {
      if (heatmapGroupFailed) {
        return (
          <DepthErrorCard
            error="The depth heatmap failed to load."
            onRetry={props.onLoadDepthHeatmap ? () => props.onLoadDepthHeatmap?.(heatmapWindow) : undefined}
          />
        );
      }
      if (!heatmapLoaded && !heatmapHydrated) {
        return (
          <div className="cow-skel" aria-busy="true" aria-label="Loading depth heatmap">
            <div className="cow-skel__bar" />
            <div className="cow-skel__block" />
          </div>
        );
      }
      if (heatmapModel.empty) {
        return (
          <div className="cow-empty">
            No resting depth reconstructed in this window — CoW books are transient, so most
            orders fill within minutes. Try a wider window.
          </div>
        );
      }
      return (
        <>
          <ChartCard renderer="svg" chartId="cow-depth-heatmap" hideId spec={heatmapSpec} onChartReady={onHeatmapReady} />
          <div className="cow-depth-hint">Click a column to reconstruct that moment's full order book.</div>
        </>
      );
    }
    if (groupFailed) {
      return <DepthErrorCard error="The depth dataset group failed to load." onRetry={retry} />;
    }
    if (groupLoading) {
      return (
        <div className="cow-skel" aria-busy="true" aria-label="Loading depth datasets">
          <div className="cow-skel__bar" />
          <div className="cow-skel__block" />
        </div>
      );
    }
    if (error) {
      return <DepthErrorCard error={error} onRetry={retry} />;
    }
    if (emptyBook) {
      return (
        <div className="cow-empty">
          {emptyLabel}
          {emptyGuidance}
        </div>
      );
    }
    if (tab === "list") {
      return (
        <CuratedTable
          datasetKey="pair_depth"
          descriptor={descriptor}
          state={state}
          viewId={props.viewId}
          fetchRows={props.fetchRows}
          onEntity={props.onEntity ?? (() => undefined)}
        />
      );
    }
    return (
      <ChartCard
        renderer="svg"
        chartId="cow-pair-depth"
        hideId
        spec={chartSpec}
      />
    );
  })();

  return (
    <MaSection title="Order-book depth" meta={info}>
      <div className="cow-depth-head">
        {depthAt ? (
          <span className="cow-depth-chip cow-depth-chip--asof" title="Reconstructed point-in-time book">
            As of {formatTime(depthAt)}
          </span>
        ) : (
          <span className="cow-depth-chip cow-depth-chip--live" title="Known open intents right now">
            Live
          </span>
        )}
        <span className="cow-depth-count">{countSummary(displayOrders, baseSymbol, quoteSymbol)}</span>
        {observedAt && <span className="cow-depth-observed">observed {formatTime(observedAt)}</span>}
      </div>
      <div className="cow-depth-mid">{midline}</div>
      <div className="cow-depth-controls">
        <div className="cow-depth-tabs" role="tablist" aria-label="Depth view">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "chart"}
            className={tab === "chart" ? "is-active" : ""}
            onClick={() => setTab("chart")}
          >
            Depth chart
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "list"}
            className={tab === "list" ? "is-active" : ""}
            onClick={() => setTab("list")}
          >
            Order list
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "heatmap"}
            className={tab === "heatmap" ? "is-active" : ""}
            onClick={() => setTab("heatmap")}
          >
            Heatmap
          </button>
        </div>
        <button
          type="button"
          className={`cow-depth-flip${flipped ? " is-active" : ""}`}
          title="Re-project the book onto the inverted pair (client-side, no reload)"
          onClick={() => setFlipped((value) => !value)}
        >
          ⇄ Flip
        </button>
        <div className="cow-depth-ranges" role="group" aria-label="Price range around mid">
          {RANGE_PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className={rangePct === preset.pct ? "is-active" : ""}
              disabled={preset.pct !== null && ladder.mid === null}
              onClick={() => setRangePct(preset.pct)}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>
      {tab === "heatmap" && (
        <div className="cow-depth-window" role="group" aria-label="Heatmap time window">
          <span className="cow-depth-window__label">Window</span>
          {HEATMAP_WINDOWS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={heatmapWindow === option.value ? "is-active" : ""}
              onClick={() => setHeatmapWindow(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
      {tab !== "heatmap" && (
      <div className="cow-depth-history">
        <div className="cow-depth-history__presets" role="group" aria-label="Book time">
          <button
            type="button"
            className={!depthAt ? "is-active" : ""}
            onClick={() => requestDepthAt("live")}
          >
            Live
          </button>
          {HISTORY_PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              onClick={() => {
                const target = Math.floor(Date.now() / 1000) - preset.seconds;
                const clamped = sliderMin !== null ? Math.max(sliderMin, target) : target;
                setSliderSec(clamped);
                requestDepthAt(isoAtSeconds(clamped));
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>
        <label className="cow-depth-history__at">
          At (UTC)
          <input
            type="datetime-local"
            value={draftAt}
            onChange={(event) => {
              const value = event.target.value;
              setDraftAt(value);
              if (!value) return;
              const iso = `${value.length === 16 ? `${value}:00` : value}Z`;
              const parsed = Date.parse(iso);
              if (!Number.isFinite(parsed)) return;
              setSliderSec(Math.floor(parsed / 1000));
              requestDepthAt(iso);
            }}
          />
        </label>
        {sliderMin !== null && sliderMin < nowSec && (
          <input
            className="cow-depth-history__slider"
            type="range"
            min={sliderMin}
            max={nowSec}
            step={SLIDER_STEP_SECONDS}
            value={Math.min(Math.max(sliderValue, sliderMin), nowSec)}
            aria-label="Reconstruct the book at a past time"
            onChange={(event) => {
              const seconds = Number(event.target.value);
              if (!Number.isFinite(seconds)) return;
              setSliderSec(seconds);
              requestDepthAt(isoAtSeconds(seconds));
            }}
          />
        )}
      </div>
      )}
      {horizonEarliest && (
        <div className="cow-depth-note">
          {tab === "heatmap"
            ? `Depth-over-time reconstructed from captured orders, fills, and cancellations (since ${formatTime(horizonEarliest)}); order size is not decayed within a resting span.`
            : `Historical reconstruction limited to the order-capture window (since ${formatTime(horizonEarliest)}).`}
        </div>
      )}
      {body}
    </MaSection>
  );
}
