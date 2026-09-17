// Typed, name-keyed parsers for every dataset the app renders. Every read goes
// through rowsToObjects (column names, never positions) and finite (strict
// numeric coercion — ""/booleans/NaN become null, never 0). Array columns
// (`checks_passed`, `assets`, ...) may arrive as real arrays or as a JSON /
// bracketed string depending on the transport; coerceStringArray accepts both.

import { shortAddr } from "../../../utils/format";
import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import type {
  ClassFeeRow, ConcentrationSummaryRow, CoverageSummaryRow, FeeGrowthRow, LivePoolTrendRow,
  MetadataGapRow, MissingDayRow, PoolDetailRow, PoolsSummaryRow, ProbeCoverageRow,
  ProfileConcentrationRow, ProfileRangeRow, PublicationCalendarRow, PublicationFactsRow,
  RangeWidthRow, ReservesHistoryRow, SourceFreshnessRow, StateHistoryRow, TickRow,
  TokenDetailRow, TokenPoolRow,
} from "../types";
import { truthy } from "./format";
import type { ProfileRange } from "./liquidityProfile";

export { parseHeatmapRows } from "./profileHeatmap";

/** First row of a single-row dataset as a name-keyed object (or null). */
export function firstRow(dataset?: RowDataset): Record<string, unknown> | null {
  if (!dataset || dataset.rows.length === 0) return null;
  return Object.fromEntries(dataset.columns.map((column, index) => [column, dataset.rows[0][index]]));
}

export function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function nullableText(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

/** Array column: a real array, a JSON array string, a ClickHouse-style
 * `['a','b']` string, or a comma list. Never throws. */
export function coerceStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((entry) => String(entry ?? "")).filter(Boolean);
  if (value === null || value === undefined) return [];
  const raw = String(value).trim();
  if (!raw) return [];
  if (raw.startsWith("[")) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.map((entry) => String(entry ?? "")).filter(Boolean);
    } catch {
      // ClickHouse renders Array(String) as ['a','b'] — single quotes.
      return raw
        .slice(1, -1)
        .split(",")
        .map((entry) => entry.trim().replace(/^['"]|['"]$/g, ""))
        .filter(Boolean);
    }
  }
  return raw.split(",").map((entry) => entry.trim()).filter(Boolean);
}

// ---- overview ---------------------------------------------------------------

export interface SourceFreshnessView {
  source: string;
  latestSnapshotDate: string | null;
  latestAnchorBlock: number | null;
  poolsPublished: number | null;
  latestPublishedAt: string | null;
}

export function parseSourceFreshness(dataset?: RowDataset): SourceFreshnessView[] {
  return rowsToObjects(dataset).map((raw) => {
    const row = raw as Partial<SourceFreshnessRow>;
    return {
      source: text(row.source),
      latestSnapshotDate: nullableText(row.latest_snapshot_date),
      latestAnchorBlock: finite(row.latest_anchor_block),
      poolsPublished: finite(row.pools_published),
      latestPublishedAt: nullableText(row.latest_published_at),
    };
  });
}

export function parsePoolsSummary(dataset?: RowDataset): Partial<PoolsSummaryRow> | null {
  const row = firstRow(dataset);
  return row ? (row as Partial<PoolsSummaryRow>) : null;
}

export interface ClassFeeView {
  poolClass: string;
  poolFamily: string;
  fee: number | null;
  feeBand: string | null;
  pools: number;
  livePools: number;
  probedPools: number;
}

export function parseClassFee(dataset?: RowDataset): ClassFeeView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<ClassFeeRow>;
    const pools = finite(row.pools);
    if (pools === null) return [];
    return [{
      poolClass: text(row.pool_class),
      poolFamily: text(row.pool_family),
      fee: finite(row.fee),
      feeBand: nullableText(row.fee_band),
      pools,
      livePools: finite(row.live_pools) ?? 0,
      probedPools: finite(row.probed_pools) ?? 0,
    }];
  });
}

export interface ProbeCoverageView {
  probed: boolean;
  live: boolean;
  pools: number;
  medianLiquidity: number | null;
  p90Liquidity: number | null;
}

export function parseProbeCoverage(dataset?: RowDataset): ProbeCoverageView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<ProbeCoverageRow>;
    const pools = finite(row.pools);
    if (pools === null) return [];
    return [{
      probed: truthy(row.ticks_probed),
      live: truthy(row.is_live),
      pools,
      medianLiquidity: finite(row.median_liquidity_float),
      p90Liquidity: finite(row.p90_liquidity_float),
    }];
  });
}

export interface LiveTrendPoint {
  bucket: string;
  publishedCl: number | null;
  liveCl: number | null;
  probed: number | null;
  publishedReserves: number | null;
}

export function parseLiveTrend(dataset?: RowDataset): LiveTrendPoint[] {
  return rowsToObjects(dataset)
    .flatMap((raw) => {
      const row = raw as Partial<LivePoolTrendRow>;
      const bucket = text(row.bucket);
      if (!bucket) return [];
      return [{
        bucket,
        publishedCl: finite(row.pools_published_cl),
        liveCl: finite(row.pools_live_cl),
        probed: finite(row.pools_probed),
        publishedReserves: finite(row.pools_published_reserves),
      }];
    })
    .sort((a, b) => a.bucket.localeCompare(b.bucket));
}

export interface ConcentrationSummaryView {
  metric: string;
  poolsMeasured: number;
  q25: number | null;
  median: number | null;
  q75: number | null;
  poolsTrue: number | null;
}

export function parseConcentrationSummary(dataset?: RowDataset): ConcentrationSummaryView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<ConcentrationSummaryRow>;
    const metric = text(row.metric);
    if (!metric) return [];
    return [{
      metric,
      poolsMeasured: finite(row.pools_measured) ?? 0,
      q25: finite(row.q25),
      median: finite(row.median),
      q75: finite(row.q75),
      poolsTrue: finite(row.pools_true),
    }];
  });
}

export interface RangeWidthView {
  order: number;
  bucket: string;
  ranges: number;
  pools: number;
  share: number | null;
}

export function parseRangeWidth(dataset?: RowDataset): RangeWidthView[] {
  return rowsToObjects(dataset)
    .flatMap((raw) => {
      const row = raw as Partial<RangeWidthRow>;
      const bucket = text(row.width_bucket);
      const ranges = finite(row.ranges);
      if (!bucket || ranges === null) return [];
      return [{
        order: finite(row.bucket_order) ?? 0,
        bucket,
        ranges,
        pools: finite(row.pools) ?? 0,
        share: finite(row.share_of_ranges),
      }];
    })
    .sort((a, b) => a.order - b.order);
}

// ---- coverage ---------------------------------------------------------------

export interface CoverageSummaryView {
  job: string;
  firstSnapshotDate: string | null;
  lastSnapshotDate: string | null;
  daysPublished: number | null;
  poolsConfigured: number | null;
  poolsPublishedLatest: number | null;
  poolsBelowThresholdLatest: number | null;
  publicationsTotal: number | null;
}

export function parseCoverageSummary(dataset?: RowDataset): CoverageSummaryView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<CoverageSummaryRow>;
    const job = text(row.job_name);
    if (!job) return [];
    return [{
      job,
      firstSnapshotDate: nullableText(row.first_snapshot_date),
      lastSnapshotDate: nullableText(row.last_snapshot_date),
      daysPublished: finite(row.days_published),
      poolsConfigured: finite(row.pools_configured),
      poolsPublishedLatest: finite(row.pools_published_latest),
      poolsBelowThresholdLatest: finite(row.pools_below_threshold_latest),
      publicationsTotal: finite(row.publications_total),
    }];
  });
}

export interface CalendarPoint {
  date: string;
  job: string;
  anchorBlock: number | null;
  poolsPublished: number | null;
  poolsBelowThreshold: number | null;
  poolsProbed: number | null;
  netSumZeroPassed: number | null;
  reconcilesPassed: number | null;
  poolsConfiguredNow: number | null;
}

export function parseCalendar(dataset?: RowDataset): CalendarPoint[] {
  return rowsToObjects(dataset)
    .flatMap((raw) => {
      const row = raw as Partial<PublicationCalendarRow>;
      const date = text(row.snapshot_date);
      const job = text(row.job_name);
      if (!date || !job) return [];
      return [{
        date,
        job,
        anchorBlock: finite(row.anchor_block),
        poolsPublished: finite(row.pools_published),
        poolsBelowThreshold: finite(row.pools_below_threshold),
        poolsProbed: finite(row.pools_probed),
        netSumZeroPassed: finite(row.net_sum_zero_passed),
        reconcilesPassed: finite(row.reconciles_passed),
        poolsConfiguredNow: finite(row.pools_configured_now),
      }];
    })
    .sort((a, b) => a.date.localeCompare(b.date) || a.job.localeCompare(b.job));
}

export interface MissingDayView {
  date: string;
  job: string;
  kind: string;
  poolsPublished: number | null;
  expectedPools: number | null;
}

export function parseMissingDays(dataset?: RowDataset): MissingDayView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<MissingDayRow>;
    const date = text(row.snapshot_date);
    if (!date) return [];
    return [{
      date,
      job: text(row.job_name),
      kind: text(row.gap_kind),
      poolsPublished: finite(row.pools_published),
      expectedPools: finite(row.expected_pools),
    }];
  });
}

export interface MetadataGapView {
  dimension: string;
  known: number;
  unknown: number;
  pctKnown: number | null;
}

export function parseMetadataGap(dataset?: RowDataset): MetadataGapView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<MetadataGapRow>;
    const dimension = text(row.dimension);
    if (!dimension) return [];
    return [{
      dimension,
      known: finite(row.known) ?? 0,
      unknown: finite(row.unknown) ?? 0,
      pctKnown: finite(row.pct_known),
    }];
  });
}

// ---- pool entity ------------------------------------------------------------

export interface PoolDetailView {
  address: string;
  name: string;
  poolClass: string;
  poolFamily: string;
  fee: number | null;
  feeBand: string | null;
  tickSpacing: number | null;
  nAssets: number;
  token0: string;
  token0Symbol: string | null;
  token0Decimals: number | null;
  token0Resolved: boolean;
  token1: string;
  token1Symbol: string | null;
  token1Decimals: number | null;
  token1Resolved: boolean;
  assets: string[];
  /** Display label per asset: the symbol when observed, else a short address. */
  assetLabels: string[];
  /** Per-asset symbol, null where the indexer emitted '' (not observed). */
  assetSymbols: Array<string | null>;
  /** Per-asset decimals, null where the indexer emitted -1 (not observed). */
  assetDecimals: Array<number | null>;
  asOf: string | null;
  /** A CL state row exists: `false` means "no state row", NOT "liquidity 0". */
  hasState: boolean;
  currentTick: number | null;
  priceRaw: number | null;
  priceAdjusted: number | null;
  liquidityRaw: string | null;
  liquidity: number | null;
  live: boolean;
  tickCount: number | null;
  probed: boolean;
  reservesAsOf: string | null;
  reserve0Raw: string | null;
  reserve1Raw: string | null;
  reserve0Units: number | null;
  reserve1Units: number | null;
  /** Raw reserves aligned with `reserveTokens` (defaults to `assets`). */
  reservesRaw: string[];
  reserveTokens: string[];
  firstPublished: string | null;
  lastPublished: string | null;
  daysPublished: number | null;
  deploymentBlock: number | null;
  anchorBlock: number | null;
  entityLabel: string;
  daysLive: number | null;
  poolId: string | null;
  profileAvailableFrom: string | null;
}

export function parsePoolDetail(dataset?: RowDataset): PoolDetailView | null {
  const raw = firstRow(dataset);
  if (!raw) return null;
  const row = raw as Partial<PoolDetailRow>;
  const address = text(row.pool_address);
  if (!address) return null;
  const assets = coerceStringArray(row.assets);
  // '' means "symbol not observed" and -1 "decimals not observed" — both become
  // null here so nothing downstream can mistake them for a real value.
  const symbolsRaw = coerceStringArray(row.asset_symbols);
  const assetSymbols: Array<string | null> = assets.map((_asset, index) => symbolsRaw[index] || null);
  const decimalsRaw = Array.isArray(row.asset_decimals) ? (row.asset_decimals as unknown[]) : [];
  const assetDecimals: Array<number | null> = assets.map((_asset, index) => {
    const value = finite(decimalsRaw[index]);
    return value === null || value < 0 ? null : value;
  });
  // The label is ours to build (the server ships the symbol, not the label).
  const assetLabels = assets.map((asset, index) => assetSymbols[index] ?? shortAddr(asset));
  const reserveTokens = coerceStringArray(row.reserve_tokens);
  const reservesRaw = coerceStringArray(row.reserve_raw);
  return {
    address,
    name: text(row.pool_name),
    poolClass: text(row.pool_class),
    poolFamily: text(row.pool_family),
    fee: finite(row.fee),
    feeBand: nullableText(row.fee_band),
    tickSpacing: finite(row.tick_spacing),
    nAssets: finite(row.n_assets) ?? 0,
    token0: text(row.token0),
    token0Symbol: nullableText(row.token0_symbol),
    token0Decimals: finite(row.token0_decimals),
    token0Resolved: truthy(row.token0_resolved),
    token1: text(row.token1),
    token1Symbol: nullableText(row.token1_symbol),
    token1Decimals: finite(row.token1_decimals),
    token1Resolved: truthy(row.token1_resolved),
    assets,
    assetLabels,
    assetSymbols,
    assetDecimals,
    asOf: nullableText(row.as_of),
    hasState: truthy(row.has_state),
    currentTick: finite(row.current_tick),
    priceRaw: finite(row.price_raw),
    priceAdjusted: finite(row.price_adjusted),
    liquidityRaw: nullableText(row.liquidity_raw),
    liquidity: finite(row.liquidity_float),
    live: truthy(row.is_live),
    tickCount: finite(row.tick_count),
    probed: truthy(row.ticks_probed),
    reservesAsOf: nullableText(row.reserves_as_of),
    reserve0Raw: nullableText(row.reserve0_raw),
    reserve1Raw: nullableText(row.reserve1_raw),
    reserve0Units: finite(row.reserve0_units),
    reserve1Units: finite(row.reserve1_units),
    reservesRaw,
    reserveTokens: reserveTokens.length ? reserveTokens : assets,
    firstPublished: nullableText(row.first_published),
    lastPublished: nullableText(row.last_published),
    daysPublished: finite(row.days_published),
    deploymentBlock: finite(row.deployment_block),
    anchorBlock: finite(row.anchor_block),
    entityLabel: text(row.entity_label),
    daysLive: finite(row.days_live),
    poolId: nullableText(row.pool_id),
    profileAvailableFrom: nullableText(row.profile_available_from),
  };
}

export interface ProfileParse {
  ranges: ProfileRange[];
  /** Applied snapshot date (authoritative — may differ from the request). */
  asOf: string;
  currentTick: number | null;
  /** true when the containing range's liquidity equals the state liquidity. */
  matchesState: boolean | null;
  source: string;
}

export function parseProfileRanges(dataset?: RowDataset): ProfileParse {
  const rows = rowsToObjects(dataset) as Array<Partial<ProfileRangeRow>>;
  const ranges: ProfileRange[] = [];
  let asOf = "";
  let currentTick: number | null = null;
  let matchesState: boolean | null = null;
  let source = "";
  for (const row of rows) {
    const lower = finite(row.tick_lower);
    const upper = finite(row.tick_upper);
    if (lower === null || upper === null || upper <= lower) continue;
    const liquidity = finite(row.active_liquidity_float);
    if (!asOf) asOf = text(row.as_of);
    if (!source) source = text(row.profile_source);
    if (currentTick === null) currentTick = finite(row.current_tick);
    const containsCurrent = truthy(row.contains_current_tick);
    if (containsCurrent && row.matches_state_liquidity !== null && row.matches_state_liquidity !== undefined) {
      matchesState = truthy(row.matches_state_liquidity);
    }
    ranges.push({
      lower,
      upper,
      liquidity: liquidity !== null && liquidity > 0 ? liquidity : 0,
      isGap: truthy(row.is_gap) || liquidity === null || liquidity <= 0,
      containsCurrent,
    });
  }
  ranges.sort((a, b) => a.lower - b.lower);
  return { ranges, asOf, currentTick, matchesState, source };
}

export interface ConcentrationBandView {
  band: string;
  bandTicks: number | null;
  share: number | null;
  rangesInBand: number | null;
  liquidityAtCurrent: number | null;
}

export function parseProfileConcentration(dataset?: RowDataset): ConcentrationBandView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<ProfileConcentrationRow>;
    const band = text(row.band);
    if (!band) return [];
    return [{
      band,
      bandTicks: finite(row.band_ticks),
      share: finite(row.tick_weighted_share),
      rangesInBand: finite(row.ranges_in_band),
      liquidityAtCurrent: finite(row.liquidity_at_current_tick_float),
    }];
  });
}

export interface TickPoint {
  tick: number;
  gross: number | null;
  net: number | null;
  grossRaw: string | null;
  netRaw: string | null;
  priceRaw: number | null;
  belowCurrent: boolean;
}

export function parseTicks(dataset?: RowDataset): TickPoint[] {
  return rowsToObjects(dataset)
    .flatMap((raw) => {
      const row = raw as Partial<TickRow>;
      const tick = finite(row.tick);
      if (tick === null) return [];
      return [{
        tick,
        gross: finite(row.liquidity_gross_float),
        net: finite(row.liquidity_net_float),
        grossRaw: nullableText(row.liquidity_gross_raw),
        netRaw: nullableText(row.liquidity_net_raw),
        priceRaw: finite(row.price_raw_at_tick),
        belowCurrent: truthy(row.is_below_current),
      }];
    })
    .sort((a, b) => a.tick - b.tick);
}

export interface StatePoint {
  date: string;
  anchorBlock: number | null;
  tick: number | null;
  priceRaw: number | null;
  priceAdjusted: number | null;
  liquidity: number | null;
  live: boolean;
  tickCount: number | null;
  probed: boolean;
  fee: number | null;
  fg0: string | null;
  fg1: string | null;
}

export function parseStateHistory(dataset?: RowDataset): StatePoint[] {
  return rowsToObjects(dataset)
    .flatMap((raw) => {
      const row = raw as Partial<StateHistoryRow>;
      const date = text(row.snapshot_date);
      if (!date) return [];
      return [{
        date,
        anchorBlock: finite(row.anchor_block),
        tick: finite(row.current_tick),
        priceRaw: finite(row.price_raw),
        priceAdjusted: finite(row.price_adjusted),
        liquidity: finite(row.liquidity_float),
        live: truthy(row.is_live),
        tickCount: finite(row.tick_count),
        probed: truthy(row.ticks_probed),
        fee: finite(row.fee),
        fg0: nullableText(row.fee_growth_global_0_raw),
        fg1: nullableText(row.fee_growth_global_1_raw),
      }];
    })
    .sort((a, b) => a.date.localeCompare(b.date));
}

export interface ReservePoint {
  date: string;
  raw: string | null;
  float: number | null;
  units: number | null;
}

export interface ReserveSeries {
  index: number;
  token: string;
  symbol: string | null;
  decimals: number | null;
  points: ReservePoint[];
}

/** One series per token (index order), points sorted by date. */
export function parseReservesHistory(dataset?: RowDataset): ReserveSeries[] {
  const byToken = new Map<string, ReserveSeries>();
  for (const raw of rowsToObjects(dataset)) {
    const row = raw as Partial<ReservesHistoryRow>;
    const date = text(row.snapshot_date);
    const token = text(row.token_address).toLowerCase();
    if (!date || !token) continue;
    let series = byToken.get(token);
    if (!series) {
      series = {
        index: finite(row.token_index) ?? byToken.size,
        token,
        symbol: nullableText(row.symbol),
        decimals: finite(row.decimals),
        points: [],
      };
      byToken.set(token, series);
    }
    series.points.push({
      date,
      raw: nullableText(row.balance_raw),
      float: finite(row.balance_float),
      units: finite(row.balance_units),
    });
  }
  const out = [...byToken.values()].sort((a, b) => a.index - b.index);
  for (const series of out) series.points.sort((a, b) => a.date.localeCompare(b.date));
  return out;
}

export interface FeePoint {
  date: string;
  prevDate: string | null;
  gapDays: number | null;
  liquidity: number | null;
  fees0Units: number | null;
  fees1Units: number | null;
  fees0Raw: string | null;
  fees1Raw: string | null;
  probed: boolean;
}

export function parseFeeGrowth(dataset?: RowDataset): FeePoint[] {
  return rowsToObjects(dataset)
    .flatMap((raw) => {
      const row = raw as Partial<FeeGrowthRow>;
      const date = text(row.snapshot_date);
      if (!date) return [];
      return [{
        date,
        prevDate: nullableText(row.prev_snapshot_date),
        gapDays: finite(row.gap_days),
        liquidity: finite(row.liquidity_float),
        fees0Units: finite(row.fees0_units_est),
        fees1Units: finite(row.fees1_units_est),
        fees0Raw: nullableText(row.fees0_raw_est),
        fees1Raw: nullableText(row.fees1_raw_est),
        probed: truthy(row.ticks_probed),
      }];
    })
    .sort((a, b) => a.date.localeCompare(b.date));
}

/** Units for the fee estimates when the server ships only `fees*_raw_est`:
 * derived ONLY from known decimals (never guessed), else left null. */
export function withDerivedFeeUnits(points: FeePoint[], dec0: number | null, dec1: number | null): FeePoint[] {
  const derive = (units: number | null, rawValue: string | null, decimals: number | null) => {
    if (units !== null || decimals === null || rawValue === null) return units;
    const value = Number(rawValue);
    return Number.isFinite(value) ? value / 10 ** decimals : null;
  };
  return points.map((point) => ({
    ...point,
    fees0Units: derive(point.fees0Units, point.fees0Raw, dec0),
    fees1Units: derive(point.fees1Units, point.fees1Raw, dec1),
  }));
}

export interface PublicationFactView {
  date: string;
  job: string;
  anchorBlock: number | null;
  anchorHash: string | null;
  blockTimestamp: string | null;
  publicationId: string | null;
  attemptId: string | null;
  publishedAt: string | null;
  integrityMode: string | null;
  blockReferenceKind: string | null;
  executorKind: string | null;
  observationsTotal: number | null;
  checksPassed: string[];
  probed: boolean;
  netSumZeroPassed: boolean | null;
  reconcilesPassed: boolean | null;
}

export function parsePublicationFacts(dataset?: RowDataset): PublicationFactView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<PublicationFactsRow>;
    const job = text(row.job_name);
    if (!job) return [];
    const flag = (value: unknown): boolean | null =>
      (value === null || value === undefined || value === "" ? null : truthy(value));
    return [{
      date: text(row.snapshot_date),
      job,
      anchorBlock: finite(row.anchor_block),
      anchorHash: nullableText(row.anchor_hash),
      blockTimestamp: nullableText(row.block_timestamp),
      publicationId: nullableText(row.publication_id),
      attemptId: nullableText(row.attempt_id),
      publishedAt: nullableText(row.published_at),
      integrityMode: nullableText(row.integrity_mode),
      blockReferenceKind: nullableText(row.block_reference_kind),
      executorKind: nullableText(row.executor_kind),
      // `observations_total`, not `universe_size`: the latter is 0 for every
      // pool publication, so surfacing it would be a fabricated zero.
      observationsTotal: finite(row.observations_total),
      checksPassed: coerceStringArray(row.checks_passed),
      probed: truthy(row.ticks_probed),
      netSumZeroPassed: flag(row.net_sum_zero_passed),
      reconcilesPassed: flag(row.reconciles_passed),
    }];
  });
}

// ---- token entity -----------------------------------------------------------

export interface TokenDetailView {
  address: string;
  entityLabel: string;
  symbol: string | null;
  name: string | null;
  decimals: number | null;
  resolutionStatus: string;
  resolved: boolean;
  poolsCount: number | null;
  clPools: number | null;
  reservesOnlyPools: number | null;
  livePools: number | null;
  probedPools: number | null;
  asOf: string | null;
  reservesAsOf: string | null;
  totalReserveRaw: string | null;
  totalReserveUnits: number | null;
}

export function parseTokenDetail(dataset?: RowDataset): TokenDetailView | null {
  const raw = firstRow(dataset);
  if (!raw) return null;
  const row = raw as Partial<TokenDetailRow>;
  const address = text(row.token_address);
  if (!address) return null;
  return {
    address,
    entityLabel: text(row.entity_label),
    symbol: nullableText(row.symbol),
    name: nullableText(row.token_name),
    decimals: finite(row.decimals),
    resolutionStatus: text(row.resolution_status),
    resolved: truthy(row.is_resolved),
    poolsCount: finite(row.pools_count),
    clPools: finite(row.cl_pools),
    reservesOnlyPools: finite(row.reserves_only_pools),
    livePools: finite(row.live_pools),
    probedPools: finite(row.probed_pools),
    asOf: nullableText(row.as_of),
    reservesAsOf: nullableText(row.reserves_as_of),
    totalReserveRaw: nullableText(row.total_reserve_raw),
    totalReserveUnits: finite(row.total_reserve_units),
  };
}

export interface TokenPoolView {
  address: string;
  name: string;
  poolClass: string;
  poolFamily: string;
  hasState: boolean;
  fee: number | null;
  counterTokens: string[];
  counterLabels: string[];
  liquidity: number | null;
  live: boolean;
  probed: boolean;
  reserveUnits: number | null;
  reserveRaw: string | null;
  tokenDecimals: number | null;
  share: number | null;
  priceInCounter: number | null;
}

export function parseTokenPools(dataset?: RowDataset): TokenPoolView[] {
  return rowsToObjects(dataset).flatMap((raw) => {
    const row = raw as Partial<TokenPoolRow>;
    const address = text(row.pool_address);
    if (!address) return [];
    return [{
      address,
      name: text(row.pool_name),
      poolClass: text(row.pool_class),
      poolFamily: text(row.pool_family),
      hasState: truthy(row.has_state),
      fee: finite(row.fee),
      counterTokens: coerceStringArray(row.counter_tokens),
      counterLabels: coerceStringArray(row.counter_labels),
      liquidity: finite(row.liquidity_float),
      live: truthy(row.is_live),
      probed: truthy(row.ticks_probed),
      reserveUnits: finite(row.reserve_token_units),
      reserveRaw: nullableText(row.reserve_token_raw),
      tokenDecimals: finite(row.token_decimals),
      share: finite(row.reserve_share),
      // Decimals-ADJUSTED, subject token as base; null when either side's
      // decimals are unknown (the raw orientation stays derivable from
      // price_raw + token_is_token0).
      priceInCounter: finite(row.price_of_token_in_counter),
    }];
  });
}
