// Pure transforms over the three treasury HISTORY datasets
// (treasury_chain_history, treasury_token_history, treasury_wallet_history).
//
// These rows are WIDE and carry an explicit chain_id, deliberately NOT the
// long {metric, metric_value} shape parseActivity pivots: that pivot keys only
// on `bucket`, so two chains reporting the same month would silently overwrite
// each other and the survivor would look like a complete series. Every
// function here therefore takes a chainId and filters by it. Nothing in this
// module ever merges two chains into one series — the chains are indexed
// independently (chain 1 runs to 2026-07, chain 100 stops at 2022-11), so a
// blended series would describe no real portfolio.
//
// Everything is pure and injectable so the panels stay testable without a
// price feed, a view, or a chart runtime.

import { shortAddr } from "../../../utils/format";
import { finite } from "../../shared/rowDataset";
import { sanitizeSymbol } from "../../shared/TokenIdentity";

type Row = Record<string, unknown>;

/** Series cap. The mini theme palette has 6 colours but two of them are both
 * violet, so 5 is the real ceiling before two series become indistinguishable. */
export const DEFAULT_MAX_SERIES = 5;

/** The server folds the small-wallet tail into this literal wallet id. */
const OTHER_WALLET = "other";

/** Buckets are 'YYYY-MM-01', so byte order already IS chronological order.
 * Comparing strings avoids Date parsing, which would drag the viewer's
 * timezone into a pure transform and can shift a UTC month boundary by a day. */
function compareBucket(a: string, b: string): number {
  if (a < b) return -1;
  return a > b ? 1 : 0;
}

function bucketOf(row: Row): string {
  const raw = row.bucket;
  if (raw === null || raw === undefined) return "";
  return String(raw);
}

/** The address is the identity — lowercased so a checksummed and a lowercase
 * spelling of the same token never split into two series. */
function tokenOf(row: Row): string {
  return String(row.token_address ?? "").trim().toLowerCase();
}

function walletOf(row: Row): string {
  return String(row.wallet_address ?? "").trim().toLowerCase();
}

/** ClickHouse sends UInt8 flags as 0/1, but a JSON round-trip or a fixture can
 * hand back a bool or a string. Accept all three rather than trusting one.
 *
 * Exported because `finite()` returns null for booleans, so the obvious
 * `finite(row.is_ltd)` silently reads every Ltd wallet as not-Ltd. One
 * implementation, not two. */
export function truthy(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    return text === "true" || text === "1";
  }
  return (finite(value) ?? 0) > 0;
}

function onChain(rows: Row[], chainId: number): Row[] {
  // finite() so a stringly '1' from the wire matches the numeric argument.
  return rows.filter((row) => finite(row.chain_id) === chainId);
}

export interface ChainHistoryPoint {
  bucket: string;
  tokensHeld: number | null;
  tokensNamed: number | null;
  walletsHolding: number | null;
  positions: number | null;
  gnoUnits: number | null;
  gnoUnitsExLtd: number | null;
  anchorBlock: number | null;
}

/** Chain-level monthly series for ONE chain, oldest first. Every measure stays
 * nullable: a missing count is not a zero count, and the callers dash-guard it. */
export function chainHistory(rows: Row[], chainId: number): ChainHistoryPoint[] {
  return onChain(rows, chainId)
    .flatMap((row) => {
      const bucket = bucketOf(row);
      if (!bucket) return [];
      return [{
        bucket,
        tokensHeld: finite(row.tokens_held),
        tokensNamed: finite(row.tokens_named),
        walletsHolding: finite(row.wallets_holding),
        positions: finite(row.positions),
        gnoUnits: finite(row.gno_units),
        gnoUnitsExLtd: finite(row.gno_units_ex_ltd),
        anchorBlock: finite(row.anchor_block),
      }];
    })
    .sort((a, b) => compareBucket(a.bucket, b.bucket));
}

/** Distinct chain ids present, ascending — drives the per-chain panel split. */
export function chainsIn(rows: Row[]): number[] {
  const seen = new Set<number>();
  for (const row of rows) {
    const id = finite(row.chain_id);
    if (id !== null) seen.add(id);
  }
  return [...seen].sort((a, b) => a - b);
}

export interface TokenSeries {
  token: string;
  label: string;
  ambiguous: boolean;
  points: Array<{ bucket: string; units: number | null; usd: number | null }>;
  /** Constant spot price used for every point, or null when unpriced. Consumed
   * by `constantPriceStackOption`, which excludes any series without one. */
  price: number | null;
  latestUsd: number | null;
}

interface TokenAccumulator {
  token: string;
  symbol: string;
  /** Bucket the symbol above came from — metadata resolves over time, so the
   * newest spelling wins rather than whichever row happened to arrive first. */
  symbolBucket: string;
  units: Map<string, number | null>;
}

/** Display name for a caption. Ambiguous symbols carry their address because
 * the whole point of the ambiguity flag is that "USDC" alone identifies
 * nothing — 19 distinct tokens in this treasury claim that symbol. */
function displayName(series: Pick<TokenSeries, "label" | "ambiguous" | "token">): string {
  return series.ambiguous ? `${series.label} (${shortAddr(series.token)})` : series.label;
}

/** Per-token series for one chain. `priceFor` is injected so this module stays
 *  pure and testable. usd is units x CURRENT spot => a constant-price
 *  revaluation; the CALLER must caption it as such.
 *  Series are ordered latestUsd DESC NULLS LAST then label, and capped at
 *  `maxSeries` (default 5). Returns `dropped` so the caller can name what was
 *  omitted in a caption rather than silently losing it. */
export function tokenSeries(
  rows: Row[],
  chainId: number,
  priceOf: (token: string) => number | null,
  opts?: { maxSeries?: number },
): { series: TokenSeries[]; dropped: string[] } {
  // A negative or fractional cap would make slice() silently drop from the
  // END of the kept list instead of the tail — clamp before it can.
  const cap = Math.max(0, Math.floor(opts?.maxSeries ?? DEFAULT_MAX_SERIES));

  const scoped = onChain(rows, chainId);
  const byToken = new Map<string, TokenAccumulator>();
  // Sanitized symbol -> the distinct addresses claiming it. Built per chain:
  // USDC on mainnet and USDC on Gnosis Chain are different addresses for the
  // same real asset, and flagging that cross-chain pair as ambiguous would cry
  // wolf on the signal that has to stay meaningful for the 19 fake USDCs.
  const claims = new Map<string, Set<string>>();

  for (const row of scoped) {
    const bucket = bucketOf(row);
    const token = tokenOf(row);
    if (!bucket || !token) continue;

    let acc = byToken.get(token);
    if (!acc) {
      acc = { token, symbol: "", symbolBucket: "", units: new Map() };
      byToken.set(token, acc);
    }
    if (compareBucket(bucket, acc.symbolBucket) >= 0) {
      acc.symbol = String(row.symbol ?? "");
      acc.symbolBucket = bucket;
    }
    // Map keyed by bucket, so a duplicated (token, bucket) row collapses to one
    // point instead of drawing a vertical spike. Last row wins.
    acc.units.set(bucket, finite(row.balance_units));

    const clean = sanitizeSymbol(row.symbol);
    if (clean) {
      const holders = claims.get(clean) ?? new Set<string>();
      holders.add(token);
      claims.set(clean, holders);
    }
  }

  // The chain's newest bucket. latestUsd is measured HERE, not at each series'
  // own last point: a token dumped in 2021 would otherwise be ranked on a
  // balance it no longer holds and would take one of the five slots from a
  // position that still exists.
  let latestBucket = "";
  for (const acc of byToken.values()) {
    for (const bucket of acc.units.keys()) {
      if (compareBucket(bucket, latestBucket) > 0) latestBucket = bucket;
    }
  }

  const all: TokenSeries[] = [...byToken.values()].map((acc) => {
    const clean = sanitizeSymbol(acc.symbol);
    // Unnamed tokens fall back to the address, which is unique by construction
    // and therefore never ambiguous.
    const label = clean || shortAddr(acc.token);
    const ambiguous = clean !== "" && (claims.get(clean)?.size ?? 0) > 1;
    // One price lookup per token, so every point in a series is revalued at the
    // same constant — that is the definition of the revaluation being claimed.
    const price = priceOf(acc.token);
    const points = [...acc.units.entries()]
      .sort((a, b) => compareBucket(a[0], b[0]))
      .map(([bucket, units]) => ({
        bucket,
        units,
        // null propagates: unpriced, or decimals never observed. A 0 here would
        // assert the position is worthless, which is a different claim.
        usd: units === null || price === null ? null : units * price,
      }));
    const latestUnits = latestBucket ? acc.units.get(latestBucket) ?? null : null;
    return {
      token: acc.token,
      label,
      ambiguous,
      points,
      // Carried, not just used above: constantPriceStackOption reads the price
      // off the series to decide what it can revalue. Without it every series
      // is excluded "for want of a price" and the chart renders empty while
      // captioning priced tokens as unpriced.
      price,
      latestUsd: latestUnits === null || price === null ? null : latestUnits * price,
    };
  });

  all.sort((a, b) => {
    if (a.latestUsd !== b.latestUsd) {
      // NULLS LAST: unpriced and no-longer-held are both "cannot be ranked",
      // and neither may outrank a position with a real measured value.
      if (a.latestUsd === null) return 1;
      if (b.latestUsd === null) return -1;
      return b.latestUsd - a.latestUsd;
    }
    return a.label.localeCompare(b.label);
  });

  return {
    series: all.slice(0, cap),
    dropped: all.slice(cap).map(displayName),
  };
}

export interface WalletSeries {
  wallet: string;
  label: string;
  isLtd: boolean;
  isOther: boolean;
  points: Array<{ bucket: string; units: number | null }>;
}

interface WalletAccumulator {
  wallet: string;
  isLtd: boolean;
  flagBucket: string;
  units: Map<string, number | null>;
}

/** Per-wallet series for one chain, one token. The literal wallet 'other' is
 *  the server-folded tail — flag it, label it "Other", and always sort it LAST
 *  regardless of size, so it never reads as a real wallet. */
export function walletSeries(rows: Row[], chainId: number): WalletSeries[] {
  const byWallet = new Map<string, WalletAccumulator>();

  for (const row of onChain(rows, chainId)) {
    const bucket = bucketOf(row);
    const wallet = walletOf(row);
    if (!bucket || !wallet) continue;

    let acc = byWallet.get(wallet);
    if (!acc) {
      acc = { wallet, isLtd: false, flagBucket: "", units: new Map() };
      byWallet.set(wallet, acc);
    }
    // is_ltd is a per-wallet classification, so the newest bucket wins: an old
    // snapshot can predate the wallet being classified at all.
    if (compareBucket(bucket, acc.flagBucket) >= 0) {
      acc.isLtd = truthy(row.is_ltd);
      acc.flagBucket = bucket;
    }
    acc.units.set(bucket, finite(row.units));
  }

  let latestBucket = "";
  for (const acc of byWallet.values()) {
    for (const bucket of acc.units.keys()) {
      if (compareBucket(bucket, latestBucket) > 0) latestBucket = bucket;
    }
  }

  const ranked = [...byWallet.values()].map((acc) => ({
    latest: latestBucket ? acc.units.get(latestBucket) ?? null : null,
    series: {
      wallet: acc.wallet,
      isOther: acc.wallet === OTHER_WALLET,
      // 'other' is a bucket, not an address — shortAddr would render it as the
      // literal word and invite reading it as a wallet the reader could look up.
      label: acc.wallet === OTHER_WALLET ? "Other" : shortAddr(acc.wallet),
      isLtd: acc.isLtd,
      points: [...acc.units.entries()]
        .sort((a, b) => compareBucket(a[0], b[0]))
        .map(([bucket, units]) => ({ bucket, units })),
    } satisfies WalletSeries,
  }));

  ranked.sort((a, b) => {
    // The folded tail sorts last unconditionally — it can easily out-mass every
    // named wallet, and leading with it would read as one giant holder.
    if (a.series.isOther !== b.series.isOther) return a.series.isOther ? 1 : -1;
    if (a.latest !== b.latest) {
      if (a.latest === null) return 1;
      if (b.latest === null) return -1;
      return b.latest - a.latest;
    }
    return a.series.label.localeCompare(b.series.label);
  });

  return ranked.map((entry) => entry.series);
}

/** Sparkline values for one token across the whole series, oldest->newest.
 *  Returns [] when fewer than 2 points: a single point drawn as a flat line
 *  would read as "held, unchanged", which is a different claim. */
export function sparkValues(rows: Row[], chainId: number, token: string): number[] {
  const want = token.trim().toLowerCase();
  const units = new Map<string, number | null>();
  for (const row of onChain(rows, chainId)) {
    const bucket = bucketOf(row);
    if (!bucket || tokenOf(row) !== want) continue;
    units.set(bucket, finite(row.balance_units));
  }
  const values = [...units.entries()]
    .sort((a, b) => compareBucket(a[0], b[0]))
    // NaN, not 0, for an unscalable bucket: sparkPoints() skips non-finite
    // entries while keeping their x-slot, so the gap stays a gap.
    .map(([, value]) => (value === null ? Number.NaN : value));
  // Two DRAWABLE points, not two rows: sparkPoints renders a lone finite value
  // as a midline, which is the "held, unchanged" claim we refuse to make.
  return values.filter(Number.isFinite).length < 2 ? [] : values;
}

/** Union of buckets across the given points, ascending — the shared x-axis. */
export function bucketsOf(points: Array<{ bucket: string }>[]): string[] {
  const seen = new Set<string>();
  for (const series of points) {
    for (const point of series) {
      if (point.bucket) seen.add(point.bucket);
    }
  }
  return [...seen].sort(compareBucket);
}
