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
import { depthFootprintOption, pairDepthOption } from "../model/chartOptions";
import { DATASET_DOCS } from "../model/datasetDocs";
import {
  buildDepthLadder,
  countSummary,
  flipOrders,
  parsePairDepth,
} from "../model/depthLadder";
import { buildDepthFootprint, parseHeatmapRows, type PriceAxisMode } from "../model/depthHeatmap";
import { DepthFootprintLegend } from "./DepthFootprintLegend";
import { useTheme } from "../../../hooks/useTheme";
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
   * is opened, never on a history-slider tick. `opts.force` forwards
   * force_refresh so a retry bypasses the server's negative failure cache. */
  onLoadDepthHeatmap?: (
    window: HeatmapWindow,
    opts?: { force?: boolean; bucketSeconds?: number },
  ) => void;
}

export type HeatmapWindow = "24h" | "7d" | "30d" | "90d" | "all";

// 24h/7d are full-fidelity per-order windows; 30d/90d/all are deep windows the
// server serves pre-binned (time-weighted depth in ~1% price bins) so results
// stay bounded over the backfilled multi-year history.
const HEATMAP_WINDOWS: Array<{ label: string; value: HeatmapWindow }> = [
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "30d", value: "30d" },
  { label: "90d", value: "90d" },
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

/** Windows whose buckets are wide enough that an absolute price axis leaves
 * the book in one or two rows — these default to the relative axis. */
const DEEP_WINDOWS = new Set<HeatmapWindow>(["30d", "90d", "all"]);

/** Mirrors the server's `_FOOTPRINT_REL_PCT`: how far from each bucket's market
 * price the footprint is binned. Anything wider is not in the payload. */
const FOOTPRINT_REL_PCT = 20;

/** Explicit bucket widths. 0 = auto (the server cuts the window into ~60).
 * A width is offered only when it yields 8..120 buckets for the current
 * window; finer than that and the server would coarsen it anyway (the row
 * budget is buckets x price bins x 2 sides <= 10k). */
const RESOLUTIONS: Array<{ label: string; seconds: number }> = [
  { label: "auto", seconds: 0 },
  { label: "15m", seconds: 900 },
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 21_600 },
  { label: "1d", seconds: 86_400 },
  { label: "1w", seconds: 604_800 },
];

const WINDOW_SPAN_SECONDS: Record<HeatmapWindow, number> = {
  "24h": 86_400,
  "7d": 7 * 86_400,
  "30d": 30 * 86_400,
  "90d": 90 * 86_400,
  // "all" is data-dependent; treat it as very long so only coarse widths show.
  all: 5 * 365 * 86_400,
};

/** Which explicit widths make sense for a window (auto always does). */
export function resolutionsFor(window: HeatmapWindow): Array<{ label: string; seconds: number }> {
  const span = WINDOW_SPAN_SECONDS[window];
  return RESOLUTIONS.filter(({ seconds }) => {
    if (seconds === 0) return true;
    const buckets = span / seconds;
    return buckets >= 8 && buckets <= 120;
  });
}

/** Human label for a bucket width in seconds. */
export function formatResolution(seconds: number): string {
  if (!(seconds > 0)) return "auto";
  if (seconds % 604_800 === 0) return `${seconds / 604_800}w`;
  if (seconds % 86_400 === 0) return `${seconds / 86_400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  return `${Math.round(seconds / 60)}m`;
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
    HEATMAP_WINDOWS.some((w) => w.value === stateWindow) ? (stateWindow as HeatmapWindow) : "7d",
  );
  // 0 = auto (the server cuts the window into ~60 buckets). Anything else asks
  // for a fixed bucket width; the server coarsens rather than blowing the row
  // budget, and reports back what it actually used.
  const [bucketSeconds, setBucketSeconds] = useState(0);
  // Absolute price is literal but leaves long windows almost empty (the book
  // spans single-digit percent while a multi-year axis spans multiples), so
  // deep windows default to the relative axis.
  const [axisModeOverride, setAxisModeOverride] = useState<PriceAxisMode | null>(null);
  const axisMode: PriceAxisMode = axisModeOverride
    ?? (DEEP_WINDOWS.has(heatmapWindow) ? "relative" : "absolute");
  const { isDark } = useTheme();
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
    if (HEATMAP_WINDOWS.some((w) => w.value === stateWindow)) {
      setHeatmapWindow(stateWindow as HeatmapWindow);
    }
  }, [stateWindow]);

  // DEPTH-HEATMAP-HOOK: request the deferred group when the Heatmap tab is first
  // opened and whenever the pair/scope/window changes while it is open. The
  // group is excluded from the app's background auto-sync, so this is the only
  // trigger — no wasted heavy query when the tab is never viewed.
  const hasPairForHeatmap = Boolean(state.pair.base && state.pair.quote);
  const heatmapReqRef = useRef<string>("");
  const heatmapReqKey = `${state.scope_id ?? ""}|${pairKey}|${heatmapWindow}|${bucketSeconds}`;
  useEffect(() => {
    if (tab !== "heatmap" || !hasPairForHeatmap || !props.onLoadDepthHeatmap) return;
    if (heatmapReqRef.current === heatmapReqKey) return;
    heatmapReqRef.current = heatmapReqKey;
    props.onLoadDepthHeatmap(heatmapWindow, { bucketSeconds });
  }, [tab, heatmapReqKey, hasPairForHeatmap]);

  const heatmapHydrated = props.hydrated.pair_depth_heatmap;
  const heatmapRows = useMemo(
    () => parseHeatmapRows(toDataset(heatmapHydrated)),
    [heatmapHydrated],
  );
  const heatmapModel = useMemo(
    () => buildDepthFootprint({ rows: heatmapRows, flipped, rangePct, axisMode }),
    [heatmapRows, flipped, rangePct, axisMode],
  );
  const heatmapSpec = useMemo(
    () => depthFootprintOption({
      xLabels: heatmapModel.xLabels,
      yLabels: heatmapModel.yLabels,
      cells: heatmapModel.cells,
      midLine: heatmapModel.midLine,
      profile: heatmapModel.profile,
      scale: heatmapModel.scale,
      axisMode: heatmapModel.axisMode,
      baseSymbol,
      quoteSymbol,
      isDark,
    }),
    [heatmapModel, baseSymbol, quoteSymbol, isDark],
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
      // Inside-zoom pans on drag and still emits a zrender click at the end,
      // which would fire a spurious drill-down. Remember where the press
      // started and ignore a click that moved more than a few pixels.
      let downAt: [number, number] | null = null;
      ec.getZr().on("mousedown", (ev) => {
        downAt = [ev.offsetX, ev.offsetY];
      });
      ec.getZr().on("click", (ev) => {
        const moved = downAt
          ? Math.abs(ev.offsetX - downAt[0]) + Math.abs(ev.offsetY - downAt[1])
          : 0;
        downAt = null;
        if (moved > 4) return;
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
  // Stub-descriptor contract: a failed heatmap query ships a zero-row dataset
  // whose provenance carries the real error — surface it, never the empty state.
  const heatmapError = datasetError(props.descriptors.pair_depth_heatmap);
  const retryHeatmap = () => {
    // The load hook dedupes on heatmapReqKey; clear it so the retry re-fires,
    // and force-refresh so the server bypasses its negative failure cache.
    heatmapReqRef.current = "";
    props.onLoadDepthHeatmap?.(heatmapWindow, { force: true, bucketSeconds });
  };

  const horizonRow = rowsToObjects(toDataset(props.hydrated.depth_horizon))[0];
  // Two floors: earliest_supported_at = full-fidelity capture start
  // (min(observed_at)); earliest_creation_seen = the backfill-reconstructed
  // floor (min(creation_date), ~2021-08 on mainnet). The slider reaches the
  // deep floor; books before the capture floor are reconstructed-tier.
  const horizonEarliest = String(horizonRow?.earliest_supported_at ?? "");
  const horizonEarliestSec = Math.ceil((Date.parse(horizonEarliest) || NaN) / 1000);
  const horizonDeep = String(horizonRow?.earliest_creation_seen ?? "");
  const horizonDeepSec = Math.ceil((Date.parse(horizonDeep) || NaN) / 1000);
  const nowSec = Math.floor(Date.now() / 1000);
  const sliderMin = Number.isFinite(horizonDeepSec)
    ? horizonDeepSec
    : Number.isFinite(horizonEarliestSec) ? horizonEarliestSec : null;
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

  // The tabs render different datasets, so the (i) must follow the tab —
  // it previously always showed the 2-D ladder's docs and coverage, which
  // left the footprint's own caveats unreachable.
  const docKey = tab === "heatmap" ? "pair_depth_heatmap" : "pair_depth";
  const info = (
    <InfoPopover label="About this data">
      <InfoBlocks
        what={DATASET_DOCS[docKey]?.what}
        method={DATASET_DOCS[docKey]?.method}
        coverage={coverageMeta(
          tab === "heatmap" ? props.descriptors.pair_depth_heatmap : descriptor,
        )}
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
    ? "No reconstructable open intents at this time — the book may pre-date the reconstructable order history."
    : "No known open intents for this pair right now.";
  // Rescue path for empty books: some chains (e.g. Gnosis) run almost
  // entirely on short-lived market orders and hold ZERO standing intents at
  // any moment — steer the user toward pairs (or networks) that have a book.
  const openPairs = useMemo(
    () => parseOpenIntentPairs(toDataset(props.hydrated.open_intent_pairs)),
    [props.hydrated.open_intent_pairs],
  );
  const chainOpenTotal = openPairs.reduce((sum, p) => sum + p.openOrders, 0);
  // Rescue guidance is shared by the 2D empty state AND the heatmap empty
  // state (both dead-end without a route to a pair that has a book).
  const rescueGuidance = (() => {
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
  const emptyGuidance = emptyBook ? rescueGuidance : null;

  const body = (() => {
    // Heatmap is its own deferred group (markets.depth_heatmap) with data
    // independent of the live/reconstructed snapshot — handle it before the
    // markets.depth group checks below.
    if (tab === "heatmap") {
      // A failed heatmap QUERY does not reject the tool call — the server
      // stubs the dataset (zero rows + provenance.coverage.error) and marks
      // the group "partial". Without this check the stub fell through to the
      // "no resting depth" empty state, hiding real failures (e.g. the shared
      // instance running out of memory). Retry must clear the request-dedup
      // ref and force-refresh past the server's negative failure cache.
      if (heatmapGroupFailed || heatmapError) {
        return (
          <DepthErrorCard
            error={heatmapError || "The depth heatmap failed to load."}
            onRetry={props.onLoadDepthHeatmap ? retryHeatmap : undefined}
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
            {rescueGuidance}
          </div>
        );
      }
      return (
        <>
          <DepthFootprintLegend scale={heatmapModel.scale} baseSymbol={baseSymbol} isDark={isDark} />
          <ChartCard renderer="canvas" chartId="cow-depth-heatmap" hideId spec={heatmapSpec} onChartReady={onHeatmapReady} />
          <div className="cow-depth-hint">
            Each cell splits bids (left) from asks (right). Scroll to zoom — zooming in
            reveals the per-side numbers. Click a column to reconstruct that moment's book.
            {heatmapModel.bucketSeconds > 0
              ? ` Buckets are ${formatResolution(heatmapModel.bucketSeconds)}${
                bucketSeconds > 0 && heatmapModel.bucketSeconds !== bucketSeconds
                  ? " (coarsened to fit the row budget)"
                  : ""}.`
              : ""}
          </div>
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
            Footprint
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
              // On the relative footprint axis the server already clamps to
              // ±FOOTPRINT_REL_PCT, so a wider preset would promise range that
              // does not exist. Offer only the ones that actually bite.
              title={
                tab === "heatmap" && axisMode === "relative"
                  && (preset.pct === null || preset.pct > FOOTPRINT_REL_PCT)
                  ? `The footprint is binned within ±${FOOTPRINT_REL_PCT}% of each bucket's market price`
                  : undefined
              }
              disabled={
                tab === "heatmap" && axisMode === "relative"
                  ? preset.pct === null || preset.pct > FOOTPRINT_REL_PCT
                  : preset.pct !== null && ladder.mid === null
              }
              onClick={() => setRangePct(preset.pct)}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>
      {tab === "heatmap" && (
        <div className="cow-depth-window">
          <span className="cow-depth-window__label">Window</span>
          <div role="group" aria-label="Footprint time window">
            {HEATMAP_WINDOWS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={heatmapWindow === option.value ? "is-active" : ""}
                onClick={() => {
                  setHeatmapWindow(option.value);
                  // A width valid for the old window may not be for the new one.
                  if (!resolutionsFor(option.value).some((r) => r.seconds === bucketSeconds)) {
                    setBucketSeconds(0);
                  }
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
          <span className="cow-depth-window__label">Buckets</span>
          <div role="group" aria-label="Footprint time resolution">
            {resolutionsFor(heatmapWindow).map((option) => (
              <button
                key={option.label}
                type="button"
                className={bucketSeconds === option.seconds ? "is-active" : ""}
                onClick={() => setBucketSeconds(option.seconds)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <span className="cow-depth-window__label">Price</span>
          <div role="group" aria-label="Footprint price axis">
            <button
              type="button"
              className={axisMode === "absolute" ? "is-active" : ""}
              onClick={() => setAxisModeOverride("absolute")}
              title="Literal quote-per-base price"
            >
              price
            </button>
            <button
              type="button"
              className={axisMode === "relative" ? "is-active" : ""}
              onClick={() => setAxisModeOverride("relative")}
              title="Distance from each bucket's own market price — keeps the book in view when the price trends"
            >
              % from market
            </button>
          </div>
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
            ? `Resting depth reconstructed from captured orders, fills, and cancellations. Full fidelity since ${formatTime(horizonEarliest)}${horizonDeep ? `; longer windows reach backfill-reconstructed history back to ${formatTime(horizonDeep)}` : ""}. Depth is time-weighted — an order resting a third of a bucket counts a third — and the profile on the right sums that over the window, so it measures how long liquidity sat at a level, not how much traded there. Cancelled orders without an observed cancel time are excluded, and only intents resting within ±${FOOTPRINT_REL_PCT}% of each bucket's market price are charted.`
            : `Historical reconstruction reaches back to ${formatTime(horizonDeep || horizonEarliest)}. Books before ${formatTime(horizonEarliest)} are backfill-reconstructed: cancelled orders without an observed cancel time are excluded.`}
        </div>
      )}
      {body}
    </MaSection>
  );
}
