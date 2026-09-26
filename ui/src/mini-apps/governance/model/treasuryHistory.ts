// Pure transforms over the treasury history datasets (the `treasury_history`
// grain fan-out, its month coverage, and the entity series).
//
// Four rules this module exists to enforce:
//
//   1. A month the upstream did not serve at all ('gap') or never published
//      ('unpublished') is a GAP, not a dip: null for EVERY band, marked on the
//      chart. A 'partial' month IS drawn (from what was served) and disclosed
//      with the registry symbols it is missing. Coverage says which is which.
//   2. A month that was served and in which a thing was not held is a real 0.
//   3. Ranking only considers keys that carry value. An unpriced or spam token
//      has no value in history and must never take a top-N slot — the old
//      chart filled its five bands alphabetically with unpriced tokens.
//   4. Everything the ranking drops is folded into "Other (+k)", so the stack
//      still sums to the NAV line, and the fold count is disclosed.
//
// Month arithmetic is integer arithmetic on 'YYYY-MM-01' strings — never Date
// parsing, which would let the viewer's timezone move a month boundary.

import { shortAddr } from "../../../utils/format";
import { chainName, chainsIn, type ChainFilter } from "./treasuryChains";
import type { CoverageRow, HistoryRow, HolderSeriesRow, WalletSeriesRow } from "./treasuryRows";

export type MonthStatus = "ok" | "incomplete" | "missing" | "before";
export type StackMode = "asset" | "chain" | "wallet" | "class";
export type Measure = "usd" | "gno";
export type HistoryRange = "1y" | "3y" | "all";

/** Bands a viewer can tell apart (the mini palette's five usable hues). */
export const DEFAULT_MAX_BANDS = 5;

// ---------------------------------------------------------------------------
// Month arithmetic
// ---------------------------------------------------------------------------

/** year * 12 + (month - 1), or null for a non-bucket. */
export function monthIndex(bucket: string): number | null {
  const match = /^(\d{4})-(\d{2})/.exec(bucket ?? "");
  if (!match) return null;
  const month = Number(match[2]);
  if (month < 1 || month > 12) return null;
  return Number(match[1]) * 12 + (month - 1);
}

export function bucketOfIndex(index: number): string {
  const year = Math.floor(index / 12);
  const month = index - year * 12 + 1;
  return `${year}-${month < 10 ? `0${month}` : month}-01`;
}

export function addMonths(bucket: string, months: number): string {
  const index = monthIndex(bucket);
  return index === null ? "" : bucketOfIndex(index + months);
}

/** Every month from `start` to `end` inclusive, across year boundaries. */
export function monthSpine(start: string, end: string): string[] {
  const from = monthIndex(start);
  const to = monthIndex(end);
  if (from === null || to === null || to < from) return [];
  const out: string[] = [];
  for (let index = from; index <= to; index += 1) out.push(bucketOfIndex(index));
  return out;
}

/** The last 12 / 36 months of a spine, or all of it. */
export function windowSpine<T>(spine: T[], range: HistoryRange): T[] {
  if (range === "all") return spine;
  const months = range === "1y" ? 12 : 36;
  return spine.length > months ? spine.slice(spine.length - months) : spine;
}

// ---------------------------------------------------------------------------
// Month statuses
// ---------------------------------------------------------------------------

/** A month a chain's upstream did not fully serve. "missing" months are
 * blanked; "incomplete" (partial) months are drawn from what was served. */
export interface MonthIssue {
  bucket: string;
  status: "incomplete" | "missing";
  /** The coverage row behind it (null when inferred from absent data). */
  coverage: CoverageRow | null;
}

export type ChainIssues = Map<number, MonthIssue[]>;

export interface HistoryFrame {
  buckets: string[];
  statuses: MonthStatus[];
  /** Per chain: first bucket with history (the "tracked since" month). */
  firstByChain: Map<number, string>;
  /** Partial and missing months per chain, for disclosure. */
  issuesByChain: ChainIssues;
}

/** Months drawn with values: served in full ("ok") or in part ("incomplete"). */
export function isDrawnStatus(status: MonthStatus): boolean {
  return status === "ok" || status === "incomplete";
}

function minBucket(a: string, b: string): string {
  if (!a) return b;
  if (!b) return a;
  return a < b ? a : b;
}

function maxBucket(a: string, b: string): string {
  return a > b ? a : b;
}

/** Status of one chain's month. `first` = the chain's first month of history
 * ("" = none). 'partial' -> "incomplete" (drawn, disclosed); 'gap' and
 * 'unpublished' -> "missing" (blanked). */
export function chainMonthStatus(
  bucket: string,
  first: string,
  coverage: CoverageRow | undefined,
  hasData: boolean,
): MonthStatus {
  if (!first || bucket < first) return "before";
  if (coverage) {
    if (coverage.status === "complete") return "ok";
    if (coverage.status === "partial") return "incomplete";
    return "missing"; // gap | unpublished
  }
  // No coverage row (the coverage dataset failed, or the month is newer than
  // it): data proves the month was served. Without data we cannot tell "held
  // nothing" from "not served", and a 0 would be a claim — so it is a gap.
  return hasData ? "ok" : "missing";
}

/**
 * The month axis and per-month status for the chains in scope.
 *
 * `dataBuckets` are the months each chain actually carries history rows for.
 * The axis starts at the earliest chain's first month and ends at the latest
 * month any chain reports. A chain's months before its own first month are
 * "before" (ignored for the combined status — they are not gaps); a combined
 * month is blank if ANY started chain is missing then (every band of a stack
 * shares that month's total), and drawn-but-disclosed if any is partial.
 *
 * With `startAtData`, a chain's history starts at its first DATA month only
 * (entity pages: a wallet's history starts at its first position, not at the
 * census start of its chain).
 */
export function historyFrame(args: {
  chains: number[];
  coverage: CoverageRow[] | null;
  dataBuckets: Map<number, Set<string>>;
  startAtData?: boolean;
}): HistoryFrame {
  const coverageByChain = new Map<number, Map<string, CoverageRow>>();
  for (const row of args.coverage ?? []) {
    const perChain = coverageByChain.get(row.chainId) ?? new Map<string, CoverageRow>();
    perChain.set(row.bucket, row);
    coverageByChain.set(row.chainId, perChain);
  }
  const firstByChain = new Map<number, string>();
  let start = "";
  let end = "";
  for (const chainId of args.chains) {
    const data = args.dataBuckets.get(chainId) ?? new Set<string>();
    let first = "";
    let last = "";
    for (const bucket of data) {
      first = minBucket(first, bucket);
      last = maxBucket(last, bucket);
    }
    const cov = coverageByChain.get(chainId);
    if (!args.startAtData && cov) {
      for (const row of cov.values()) {
        if (row.status === "complete" || row.status === "partial") first = minBucket(first, row.bucket);
      }
    }
    if (!first) continue;
    // A month the upstream reports on — including a gap — extends the axis;
    // an 'unpublished' tail does not.
    if (cov) {
      for (const row of cov.values()) {
        if (row.status !== "unpublished" && row.bucket >= first) last = maxBucket(last, row.bucket);
      }
    }
    firstByChain.set(chainId, first);
    start = minBucket(start, first);
    end = maxBucket(end, last);
  }
  const buckets = start && end ? monthSpine(start, end) : [];
  const issuesByChain: ChainIssues = new Map();
  const statuses = buckets.map((bucket) => {
    const perChain: MonthStatus[] = [];
    for (const chainId of args.chains) {
      const coverage = coverageByChain.get(chainId)?.get(bucket);
      const status = chainMonthStatus(
        bucket,
        firstByChain.get(chainId) ?? "",
        coverage,
        args.dataBuckets.get(chainId)?.has(bucket) ?? false,
      );
      if (status === "incomplete" || status === "missing") {
        const list = issuesByChain.get(chainId) ?? [];
        list.push({ bucket, status, coverage: coverage ?? null });
        issuesByChain.set(chainId, list);
      }
      perChain.push(status);
    }
    return combineStatuses(perChain);
  });
  return { buckets, statuses, firstByChain, issuesByChain };
}

/** Combine per-chain statuses of one month: chains that had not started are
 * ignored; any missing chain makes the month missing (every band of a stack
 * shares the month's total, so one blank chain blanks the month); any partial
 * chain makes it incomplete (still drawn). */
export function combineStatuses(statuses: MonthStatus[]): MonthStatus {
  const started = statuses.filter((status) => status !== "before");
  if (started.length === 0) return "before";
  if (started.includes("missing")) return "missing";
  if (started.includes("incomplete")) return "incomplete";
  return "ok";
}

/** A frame restricted to the requested window (1Y / 3Y / All). */
export function windowFrame(frame: HistoryFrame, range: HistoryRange): HistoryFrame {
  if (range === "all") return frame;
  return {
    ...frame,
    buckets: windowSpine(frame.buckets, range),
    statuses: windowSpine(frame.statuses, range),
  };
}

/** Buckets with chain-grain history rows, per chain — the section's data months. */
export function chainDataBuckets(rows: HistoryRow[]): Map<number, Set<string>> {
  const out = new Map<number, Set<string>>();
  for (const row of rows) {
    if (row.grain !== "chain") continue;
    const set = out.get(row.chainId) ?? new Set<string>();
    set.add(row.bucket);
    out.set(row.chainId, set);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Facts: (bucket, key, value) triples per stack mode
// ---------------------------------------------------------------------------

export interface Fact {
  bucket: string;
  key: string;
  label: string;
  value: number | null;
  /** Fixed hue (the Gnosis Ltd. band, chain bands). */
  color?: string;
}

export interface FactOptions {
  mode: StackMode;
  measure: Measure;
  chain: ChainFilter;
  exLtd: boolean;
}

/** Fixed amber for the Gnosis Ltd. band: the DAO/Ltd split is the point of a
 * wallet stack, so its hue must not move with legend order. */
export const LTD_BAND_COLOR = "#F5B14C";

export const CHAIN_BAND_COLORS: Record<number, string> = { 1: "#7B9CE1", 100: "#34d399" };

/** GNO units stack only by chain or wallet: a GNO measure across assets or
 * asset classes would add GNO to stablecoins. */
export function measureAllowed(mode: StackMode, measure: Measure): boolean {
  return measure === "usd" || mode === "chain" || mode === "wallet";
}

function assetLabels(rows: HistoryRow[]): Map<string, string> {
  const symbols = new Map<string, Set<string>>();
  for (const row of rows) {
    if (row.grain !== "token") continue;
    const key = row.assetKey || `${row.chainId}:${row.token}`;
    const set = symbols.get(key) ?? new Set<string>();
    if (row.registrySymbol) set.add(row.registrySymbol);
    symbols.set(key, set);
  }
  // One registry symbol -> that symbol ("WETH"); several across chains
  // ("USDC" + "USDC.e") -> the asset key; none -> the short address.
  const out = new Map<string, string>();
  for (const [key, set] of symbols) {
    const merged = !key.includes(":");
    const [first] = [...set];
    if (set.size === 1) out.set(key, first);
    else if (set.size > 1) out.set(key, merged ? key : first);
    else out.set(key, merged ? key : shortAddr(key.slice(key.indexOf(":") + 1)));
  }
  return out;
}

/** The facts behind one stack mode, honouring the chain filter and the
 * Gnosis Ltd. exclusion (ex-Ltd companion columns for aggregates; Ltd rows
 * dropped at wallet grain). */
export function historyFacts(rows: HistoryRow[], opts: FactOptions): Fact[] {
  const chains = new Set<number>(chainsIn(opts.chain));
  const measure = measureAllowed(opts.mode, opts.measure) ? opts.measure : "usd";
  const out: Fact[] = [];
  if (opts.mode === "chain") {
    for (const row of rows) {
      if (row.grain !== "chain" || !chains.has(row.chainId)) continue;
      const value = measure === "usd"
        ? (opts.exLtd ? row.navUsdExLtd : row.navUsd)
        : (opts.exLtd ? row.gnoUnitsExLtd : row.gnoUnits);
      out.push({
        bucket: row.bucket,
        key: String(row.chainId),
        label: chainName(row.chainId),
        value,
        color: CHAIN_BAND_COLORS[row.chainId],
      });
    }
    return out;
  }
  if (opts.mode === "wallet") {
    for (const row of rows) {
      if (row.grain !== "wallet" || !chains.has(row.chainId)) continue;
      if (opts.exLtd && row.isLtd) continue;
      out.push({
        bucket: row.bucket,
        key: row.wallet,
        label: row.walletLabel ? `${row.walletLabel} (${shortAddr(row.wallet)})` : shortAddr(row.wallet),
        value: measure === "usd" ? row.navUsd : row.gnoUnits,
        color: row.isLtd ? LTD_BAND_COLOR : undefined,
      });
    }
    return out;
  }
  const labels = opts.mode === "asset" ? assetLabels(rows) : null;
  for (const row of rows) {
    if (row.grain !== "token" || !chains.has(row.chainId)) continue;
    // The token grain is registry-priced tokens only; a stray non-priced row
    // (a retired mirror, say) is not part of NAV and must not be stacked.
    if (row.tokenClass !== "priced") continue;
    const value = opts.exLtd ? row.navUsdExLtd : row.navUsd;
    if (opts.mode === "asset") {
      const key = row.assetKey || `${row.chainId}:${row.token}`;
      out.push({ bucket: row.bucket, key, label: labels?.get(key) ?? key, value });
    } else {
      const key = row.assetClass || "Other";
      out.push({ bucket: row.bucket, key, label: key, value });
    }
  }
  return out;
}

/** Wallet page: one wallet's priced positions, stacked by asset. */
export function walletSeriesFacts(rows: WalletSeriesRow[]): Fact[] {
  return rows
    .filter((row) => row.tokenClass === "priced")
    .map((row) => ({
      bucket: row.bucket,
      key: row.assetKey || row.token,
      label: row.registrySymbol || shortAddr(row.token),
      value: row.valueUsd,
    }));
}

/** Asset page: one token's holders over time, in USD or units. */
export function holderSeriesFacts(
  rows: HolderSeriesRow[],
  opts: { measure: "usd" | "units"; exLtd: boolean },
): Fact[] {
  return rows
    .filter((row) => !(opts.exLtd && row.isLtd))
    .map((row) => ({
      bucket: row.bucket,
      key: row.wallet,
      label: row.label ? `${row.label} (${shortAddr(row.wallet)})` : shortAddr(row.wallet),
      value: opts.measure === "usd" ? row.valueUsd : row.units,
      color: row.isLtd ? LTD_BAND_COLOR : undefined,
    }));
}

/** Months (per chain) that carry any fact — the data months of an entity. */
export function factBuckets(facts: Fact[], chainId: number): Map<number, Set<string>> {
  const set = new Set<string>();
  for (const fact of facts) {
    if (fact.value !== null && fact.value !== 0) set.add(fact.bucket);
  }
  return new Map([[chainId, set]]);
}

// ---------------------------------------------------------------------------
// Stacking
// ---------------------------------------------------------------------------

export interface StackBand {
  key: string;
  /** Series id: `${prefix}:${key}`, or "other" for the fold. */
  id: string;
  label: string;
  data: Array<number | null>;
  isOther: boolean;
  color?: string;
  /** Keys folded into this band (Other only). */
  folded: string[];
}

export interface StackResult {
  buckets: string[];
  statuses: MonthStatus[];
  bands: StackBand[];
  /** Per-month stack total: equals the NAV line in drawn months, null in gaps. */
  totals: Array<number | null>;
  /** Months left blank (nothing served / not published upstream). */
  gaps: string[];
  /** Months drawn from a partial upstream snapshot (disclosed, not blanked). */
  partial: string[];
}

/** A blanked month: nothing served, or nothing published. */
export function isGapStatus(status: MonthStatus): boolean {
  return status === "missing";
}

/**
 * Stack `facts` onto the frame's months: the top `maxBands` keys by value over
 * the window, the rest folded into "Other (+k)". Values are null for EVERY
 * band in a blanked month and 0 for a key not held in a drawn month.
 */
export function stackFacts(
  facts: Fact[],
  frame: Pick<HistoryFrame, "buckets" | "statuses">,
  opts: { prefix: string; maxBands?: number } = { prefix: "band" },
): StackResult {
  const maxBands = Math.max(1, Math.floor(opts.maxBands ?? DEFAULT_MAX_BANDS));
  const bucketIndex = new Map(frame.buckets.map((bucket, index) => [bucket, index]));
  const valueByKey = new Map<string, Map<number, number>>();
  const labelByKey = new Map<string, string>();
  const colorByKey = new Map<string, string>();
  for (const fact of facts) {
    const index = bucketIndex.get(fact.bucket);
    if (index === undefined) continue;
    if (!labelByKey.has(fact.key)) labelByKey.set(fact.key, fact.label);
    if (fact.color && !colorByKey.has(fact.key)) colorByKey.set(fact.key, fact.color);
    if (fact.value === null) continue;
    const perKey = valueByKey.get(fact.key) ?? new Map<number, number>();
    perKey.set(index, (perKey.get(index) ?? 0) + fact.value);
    valueByKey.set(fact.key, perKey);
  }
  const okIndexes = frame.statuses
    .map((status, index) => (isDrawnStatus(status) ? index : -1))
    .filter((index) => index >= 0);

  // Rank ONLY keys that carry value in a served month of the window.
  const scored = [...valueByKey.entries()]
    .map(([key, values]) => ({
      key,
      score: okIndexes.reduce((acc, index) => acc + Math.max(0, values.get(index) ?? 0), 0),
    }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score
      || (labelByKey.get(a.key) ?? a.key).localeCompare(labelByKey.get(b.key) ?? b.key));
  const overflow = scored.length > maxBands;
  const kept = overflow ? scored.slice(0, maxBands - 1) : scored;
  const folded = overflow ? scored.slice(maxBands - 1).map((entry) => entry.key) : [];

  const valueAt = (key: string, index: number) => valueByKey.get(key)?.get(index) ?? 0;
  const series = (keys: string[]) => frame.buckets.map((_, index) => (
    isDrawnStatus(frame.statuses[index]) ? keys.reduce((acc, key) => acc + valueAt(key, index), 0) : null
  ));

  const bands: StackBand[] = kept.map(({ key }) => ({
    key,
    id: `${opts.prefix}:${key}`,
    label: labelByKey.get(key) ?? key,
    data: series([key]),
    isOther: false,
    color: colorByKey.get(key),
    folded: [],
  }));
  if (folded.length > 0) {
    bands.push({
      key: "other",
      id: "other",
      label: `Other (+${folded.length})`,
      data: series(folded),
      isOther: true,
      folded,
    });
  }
  const allKeys = [...valueByKey.keys()];
  return {
    buckets: frame.buckets,
    statuses: frame.statuses,
    bands,
    totals: series(allKeys),
    gaps: frame.buckets.filter((_, index) => isGapStatus(frame.statuses[index])),
    partial: frame.buckets.filter((_, index) => frame.statuses[index] === "incomplete"),
  };
}

// ---------------------------------------------------------------------------
// Breadth, sparklines, first-priced markers
// ---------------------------------------------------------------------------

export interface BreadthSeries {
  priced: Array<number | null>;
  listed: Array<number | null>;
  unverified: Array<number | null>;
  /** Present only when hidden tokens are shown. */
  spam: Array<number | null> | null;
  positions: Array<number | null>;
}

/** Token counts by class per month (summed over the chains in scope) and the
 * position count; null in blanked months, like every other history series. */
export function breadthSeries(
  rows: HistoryRow[],
  frame: Pick<HistoryFrame, "buckets" | "statuses">,
  opts: { chain: ChainFilter; showHidden: boolean },
): BreadthSeries {
  const chains = new Set<number>(chainsIn(opts.chain));
  const index = new Map(frame.buckets.map((bucket, i) => [bucket, i]));
  const blank = () => frame.buckets.map(() => 0 as number | null);
  const acc = { priced: blank(), listed: blank(), unverified: blank(), spam: blank(), positions: blank() };
  for (const row of rows) {
    if (row.grain !== "chain" || !chains.has(row.chainId)) continue;
    const i = index.get(row.bucket);
    if (i === undefined) continue;
    const add = (series: Array<number | null>, value: number | null) => {
      series[i] = (series[i] ?? 0) + (value ?? 0);
    };
    add(acc.priced, row.pricedTokens);
    add(acc.listed, row.listedTokens);
    add(acc.unverified, row.unverifiedTokens);
    add(acc.spam, row.hiddenSpamTokens);
    add(acc.positions, row.positions);
  }
  const gapped = (series: Array<number | null>) => series.map((value, i) => (
    isDrawnStatus(frame.statuses[i]) ? value : null
  ));
  return {
    priced: gapped(acc.priced),
    listed: gapped(acc.listed),
    unverified: gapped(acc.unverified),
    spam: opts.showHidden ? gapped(acc.spam) : null,
    positions: gapped(acc.positions),
  };
}

/** Months a sparkline covers. */
export const SPARK_MONTHS = 24;

/**
 * Sparkline values per token (`chain:token`) and per merged asset
 * (`asset:<asset_key>`), over the last 24 months of the frame: USD value in
 * drawn months (0 when not held), NaN in blanked months so the line breaks
 * there instead of dipping. Keys with no value at all get no sparkline.
 */
export function sparkIndex(
  rows: HistoryRow[],
  frame: Pick<HistoryFrame, "buckets" | "statuses">,
  opts: { chain: ChainFilter; exLtd: boolean },
): Map<string, number[]> {
  const buckets = frame.buckets.slice(-SPARK_MONTHS);
  const statuses = frame.statuses.slice(-SPARK_MONTHS);
  const index = new Map(buckets.map((bucket, i) => [bucket, i]));
  const chains = new Set<number>(chainsIn(opts.chain));
  const values = new Map<string, number[]>();
  const touch = (key: string) => {
    let series = values.get(key);
    if (!series) {
      series = buckets.map((_, i) => (isDrawnStatus(statuses[i]) ? 0 : Number.NaN));
      values.set(key, series);
    }
    return series;
  };
  for (const row of rows) {
    if (row.grain !== "token" || row.tokenClass !== "priced" || !chains.has(row.chainId)) continue;
    const i = index.get(row.bucket);
    if (i === undefined || !isDrawnStatus(statuses[i])) continue;
    const value = (opts.exLtd ? row.navUsdExLtd : row.navUsd) ?? 0;
    touch(`${row.chainId}:${row.token}`)[i] += value;
    if (row.assetKey) touch(`asset:${row.assetKey}`)[i] += value;
  }
  for (const [key, series] of values) {
    if (!series.some((value) => Number.isFinite(value) && value !== 0)) values.delete(key);
  }
  return values;
}

export interface PricedMarker {
  bucket: string;
  key: string;
  label: string;
}

/**
 * "First priced" markers: assets whose hub price series starts mid-history,
 * so the stack steps up because the asset BECAME VALUED, not because it was
 * bought. The contract only carries priced months in the token grain (a
 * registry token without a hub price that month is class 'listed'), so the
 * evidence is indirect and read conservatively:
 *
 *   * definitive — token-grain rows with a NULL price before the first priced
 *     month (held, not yet priced);
 *   * otherwise — in the month a token first appears in the token grain, the
 *     chain grain's `listed_tokens` falls from the previous month by at least
 *     the number of tokens first priced that month (each can have moved from
 *     listed to priced). Fewer, or no previous month to compare: no marker —
 *     a new acquisition must never be labelled "priced".
 *
 * Only markers inside the frame, after its first month, are returned.
 */
export function firstPricedMarkers(
  rows: HistoryRow[],
  frame: Pick<HistoryFrame, "buckets">,
  opts: { chain: ChainFilter },
): PricedMarker[] {
  const chains = new Set<number>(chainsIn(opts.chain));
  const listed = new Map<string, number | null>();
  const byToken = new Map<string, { chainId: number; first: string; firstPriced: string; key: string; label: string }>();
  for (const row of rows) {
    if (!chains.has(row.chainId)) continue;
    if (row.grain === "chain") {
      listed.set(`${row.chainId}|${row.bucket}`, row.listedTokens);
      continue;
    }
    if (row.grain !== "token") continue;
    const id = `${row.chainId}:${row.token}`;
    const entry = byToken.get(id) ?? {
      chainId: row.chainId,
      first: "",
      firstPriced: "",
      key: row.assetKey || id,
      label: row.registrySymbol || shortAddr(row.token),
    };
    entry.first = minBucket(entry.first, row.bucket);
    if (row.priceUsd !== null) entry.firstPriced = minBucket(entry.firstPriced, row.bucket);
    byToken.set(id, entry);
  }
  const firstChainBucket = new Map<number, string>();
  for (const key of listed.keys()) {
    const [chain, bucket] = key.split("|");
    const chainId = Number(chain);
    firstChainBucket.set(chainId, minBucket(firstChainBucket.get(chainId) ?? "", bucket));
  }
  // Tokens first priced in each (chain, month).
  const newlyPriced = new Map<string, number>();
  for (const entry of byToken.values()) {
    if (!entry.firstPriced) continue;
    const key = `${entry.chainId}|${entry.firstPriced}`;
    newlyPriced.set(key, (newlyPriced.get(key) ?? 0) + 1);
  }
  const start = frame.buckets[0] ?? "";
  const end = frame.buckets[frame.buckets.length - 1] ?? "";
  const byAsset = new Map<string, PricedMarker>();
  for (const entry of byToken.values()) {
    if (!entry.firstPriced || entry.firstPriced <= start || entry.firstPriced > end) continue;
    let evidence = entry.firstPriced > entry.first;
    if (!evidence && entry.firstPriced > (firstChainBucket.get(entry.chainId) ?? "")) {
      const now = listed.get(`${entry.chainId}|${entry.firstPriced}`);
      const before = listed.get(`${entry.chainId}|${addMonths(entry.firstPriced, -1)}`);
      const count = newlyPriced.get(`${entry.chainId}|${entry.firstPriced}`) ?? 0;
      evidence = now !== undefined && now !== null && before !== undefined && before !== null
        && before - now >= count && count > 0;
    }
    if (!evidence) continue;
    const existing = byAsset.get(entry.key);
    if (!existing || entry.firstPriced < existing.bucket) {
      byAsset.set(entry.key, { bucket: entry.firstPriced, key: entry.key, label: entry.label });
    }
  }
  return [...byAsset.values()].sort((a, b) => (a.bucket < b.bucket ? -1 : a.bucket > b.bucket ? 1 : 0));
}

/** Sparkline values per `chain:token` from an entity series (the wallet
 * page's positions): same rules as `sparkIndex` — last 24 months of the
 * frame, 0 when not held in a served month, NaN in gap months. */
export function seriesSparkIndex(
  rows: Array<{ bucket: string; chainId: number; token: string; value: number | null }>,
  frame: Pick<HistoryFrame, "buckets" | "statuses">,
): Map<string, number[]> {
  const buckets = frame.buckets.slice(-SPARK_MONTHS);
  const statuses = frame.statuses.slice(-SPARK_MONTHS);
  const index = new Map(buckets.map((bucket, i) => [bucket, i]));
  const values = new Map<string, number[]>();
  for (const row of rows) {
    const i = index.get(row.bucket);
    if (i === undefined || !isDrawnStatus(statuses[i])) continue;
    const key = `${row.chainId}:${row.token}`;
    let series = values.get(key);
    if (!series) {
      series = buckets.map((_, j) => (isDrawnStatus(statuses[j]) ? 0 : Number.NaN));
      values.set(key, series);
    }
    series[i] += row.value ?? 0;
  }
  for (const [key, series] of values) {
    if (!series.some((value) => Number.isFinite(value) && value !== 0)) values.delete(key);
  }
  return values;
}
