// Date-aware USD pricing for the GnosisDAO treasury portfolio view.
//
// Only a SPOT overlay exists today, but every call site goes through
// `priceFor(src, chain, token, date)` so that landing a historical quote plane
// later changes no caller. That is the entire point of the shape: the spot
// branch deliberately ignores `date` — valuing a 69-month history with today's
// quote is a constant-price revaluation, NOT historical market value, and the
// caller owns saying so in the caption — while the historical branch resolves
// the nearest quote on or before the date.
//
// SECURITY, load-bearing: token symbols are attacker-authored. 19 distinct
// addresses in this treasury claim the symbol "USDC", two claim "SAFE", two
// claim "COW", and some token NAMES are outright phishing lures. The ADDRESS
// is the identity; the symbol is untrusted display text. It is sanitized here
// and flagged `ambiguous` whenever more than one address claims it, so the UI
// can never show a bare "USDC" that is actually a spoof.

import { shortAddr } from "../../../utils/format";
import { finite } from "../../shared/rowDataset";
import { sanitizeSymbol } from "../../shared/TokenIdentity";

export type PriceSource =
  | { kind: "spot"; at: string; byChain: Record<string, Record<string, number>> }
  | { kind: "historical"; at: string; byChain: Record<string, Record<string, Record<string, number>>> };

/** Normalized map key. The price/icon planes are lowercase-keyed and holdings
 * rows arrive checksummed, so BOTH sides go through this — a case mismatch
 * returns "unpriced" for every token without erroring anywhere. */
function key(value: unknown): string | null {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  const text = String(value).trim().toLowerCase();
  return text === "" ? null : text;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Adapt raw view state (state.price_overlay + state.price_overlay_at) into a
 *  PriceSource, or null when the overlay has not landed yet. */
export function priceSourceFrom(overlay: unknown, at: unknown): PriceSource | null {
  if (!isRecord(overlay)) return null;
  const kind = overlay.kind;
  if (kind !== "spot" && kind !== "historical") return null;
  const raw = overlay.by_chain;
  if (!isRecord(raw)) return null;

  // A missing timestamp must NOT discard the quotes: silently dropping the
  // overlay renders "$0 NAV", a bug this view already shipped once. The caller
  // renders an empty `at` as an unknown as-of instead.
  const stamp = typeof at === "string" ? at : "";

  if (kind === "spot") {
    const spotByChain: Record<string, Record<string, number>> = {};
    for (const [chain, tokens] of Object.entries(raw)) {
      const chainKey = key(chain);
      if (chainKey === null || !isRecord(tokens)) continue;
      const quotes = spotByChain[chainKey] ?? {};
      for (const [token, price] of Object.entries(tokens)) {
        const tokenKey = key(token);
        const usd = finite(price);
        // 0 survives — a CoinGecko quote of exactly 0 is a real answer
        // ("worthless"), semantically different from "no quote".
        if (tokenKey !== null && usd !== null) quotes[tokenKey] = usd;
      }
      spotByChain[chainKey] = quotes;
    }
    return { kind: "spot", at: stamp, byChain: spotByChain };
  }

  const histByChain: Record<string, Record<string, Record<string, number>>> = {};
  for (const [chain, tokens] of Object.entries(raw)) {
    const chainKey = key(chain);
    if (chainKey === null || !isRecord(tokens)) continue;
    const perToken = histByChain[chainKey] ?? {};
    for (const [token, series] of Object.entries(tokens)) {
      const tokenKey = key(token);
      // A bare number under a "historical" overlay is a shape error, not a
      // quote we can date. Dropping it beats dating it "now" and drawing a
      // flat line through six years of history.
      if (tokenKey === null || !isRecord(series)) continue;
      const quotes = perToken[tokenKey] ?? {};
      for (const [date, price] of Object.entries(series)) {
        const usd = finite(price);
        if (date !== "" && usd !== null) quotes[date] = usd;
      }
      perToken[tokenKey] = quotes;
    }
    histByChain[chainKey] = perToken;
  }
  return { kind: "historical", at: stamp, byChain: histByChain };
}

/** Calendar-day prefix. Quotes are daily, and a bucket label ("2026-07-01")
 * must compare equal to an instant on that day ("2026-07-01T00:00:00Z") —
 * raw lexicographic comparison gets that backwards, since the shorter string
 * sorts FIRST and the instant would look like it landed after its own day. */
function dayKey(value: string): string {
  return value.slice(0, 10);
}

/** Nearest quote on or before `date`; the latest quote when `date` is omitted;
 * null when every quote is strictly after it (e.g. a 2020 bucket against a
 * price series that starts in 2024 — those cells stay unpriced, never 0). */
function nearestOnOrBefore(quotes: Record<string, number>, date?: string): number | null {
  const target = typeof date === "string" && date !== "" ? dayKey(date) : null;
  let bestDate: string | null = null;
  let best: number | null = null;
  for (const [quoteDate, price] of Object.entries(quotes)) {
    if (target !== null && dayKey(quoteDate) > target) continue;
    // Full-string tie-break: ISO strings order by day first, so this only
    // decides between several quotes stamped within the same day.
    if (bestDate === null || quoteDate > bestDate) {
      bestDate = quoteDate;
      best = price;
    }
  }
  return best;
}

/** Spot: `date` is ignored (caller must caption the chart as a constant-price
 *  revaluation). Historical: resolves the nearest quote ON OR BEFORE `date`.
 *  Lookups lowercase BOTH sides — the plane stores lowercase and a case
 *  mismatch would silently return "unpriced" for everything. */
export function priceFor(src: PriceSource | null, chainId: unknown, token: unknown, date?: string): number | null {
  if (!src) return null;
  const chainKey = key(chainId);
  const tokenKey = key(token);
  if (chainKey === null || tokenKey === null) return null;
  if (src.kind === "spot") {
    const price = src.byChain[chainKey]?.[tokenKey];
    return price === undefined ? null : price;
  }
  const series = src.byChain[chainKey]?.[tokenKey];
  return series ? nearestOnOrBefore(series, date) : null;
}

/** null (never 0) when either input is missing. A CoinGecko quote of exactly 0
 *  IS legitimate (a worthless token) and must survive as 0. */
export function usdValue(units: number | null, price: number | null): number | null {
  if (units === null || price === null) return null;
  // Belt-and-braces against a NaN leaking in from an un-`finite`d caller: a
  // rendered "NaN" is worse than a dash, and both read as "not a number".
  if (!Number.isFinite(units) || !Number.isFinite(price)) return null;
  return units * price;
}

export interface PricedHolding {
  chainId: number; token: string;
  symbol: string;            // ALREADY sanitized via sanitizeSymbol
  symbolRaw: string;         // untouched, for tooltips/debug only
  ambiguous: boolean;        // symbol not unique within the input set
  metadataStatus: string; units: number | null; rawBalance: string;
  wallets: number | null; supplyShare: number | null; usd: number | null;
}

/** Descending, nulls last — "no price" is not "cheapest". */
function descNullsLast(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

/** rowsToObjects(treasury_holdings) -> priced, sorted:
 *  usd DESC NULLS LAST, then supplyShare DESC NULLS LAST, then token address. */
export function pricedHoldings(rows: Array<Record<string, unknown>>, src: PriceSource | null): PricedHolding[] {
  const priced = rows.flatMap<PricedHolding>((row) => {
    const token = key(row.token_address);
    const chainId = finite(row.chain_id);
    // The address IS the identity; a row without one — or without a chain to
    // resolve it on — cannot be priced, deduplicated or linked, and rendering
    // it would put an unattributable balance in the table. Both are grain keys
    // upstream, so this only fires on a malformed dataset.
    if (token === null || chainId === null) return [];
    const units = finite(row.balance_units);
    return [{
      chainId,
      token,
      symbol: sanitizeSymbol(row.symbol),
      symbolRaw: String(row.symbol ?? ""),
      ambiguous: false, // filled in below, once the whole set is known
      metadataStatus: String(row.metadata_status ?? ""),
      units,
      rawBalance: String(row.balance_total_raw ?? ""),
      wallets: finite(row.wallets_holding),
      supplyShare: finite(row.supply_share),
      // The dataset's own `value_usd` is always NULL — nothing prices this
      // server-side. USD exists only where the overlay has a quote.
      usd: usdValue(units, priceFor(src, chainId, token)),
    }];
  });

  // Ambiguity = how many DISTINCT addresses claim the same sanitized symbol
  // ON THE SAME CHAIN.
  //
  // Per chain, not globally, and the distinction matters: GNO, COW and WETH all
  // exist on both Ethereum and Gnosis Chain under different addresses, and they
  // are the SAME real asset. Flagging those cross-chain twins would put an
  // address beside almost every major holding and drown the signal that has to
  // stay legible — 19 distinct addresses claiming "USDC" on one chain. The
  // chain column already tells a twin apart; only a same-chain collision is a
  // spoof. Unnamed rows are skipped: they already render as their address.
  const claimants = new Map<string, Set<string>>();
  for (const holding of priced) {
    if (holding.symbol === "") continue;
    const scope = `${holding.chainId}:${holding.symbol}`;
    const seen = claimants.get(scope) ?? new Set<string>();
    seen.add(holding.token);
    claimants.set(scope, seen);
  }
  for (const holding of priced) {
    holding.ambiguous = holding.symbol !== ""
      && (claimants.get(`${holding.chainId}:${holding.symbol}`)?.size ?? 0) > 1;
  }

  return priced.sort((a, b) => (
    descNullsLast(a.usd, b.usd)
    || descNullsLast(a.supplyShare, b.supplyShare)
    // Address last so the order is total and stable across renders.
    || (a.token < b.token ? -1 : a.token > b.token ? 1 : 0)
  ));
}

export function priceCoverage(holdings: PricedHolding[]): { priced: number; total: number; usd: number } {
  let priced = 0;
  let usd = 0;
  for (const holding of holdings) {
    if (holding.usd === null) continue;
    priced += 1;
    usd += holding.usd;
  }
  return { priced, total: holdings.length, usd };
}

/** Chart-safe label. A treemap tile reading a bare "USDC" is precisely what
 * the 18 spoofs are hoping for, so a contested symbol always carries its
 * address; an unnamed token is shown as the address alone. */
function holdingLabel(holding: PricedHolding): string {
  if (holding.symbol === "") return shortAddr(holding.token);
  return holding.ambiguous ? `${holding.symbol} ${shortAddr(holding.token)}` : holding.symbol;
}

/** Composition items for a treemap, priced only, descending, optionally
 *  excluding a token (the ex-GNO view). */
export function compositionItems(
  holdings: PricedHolding[],
  opts: { chainId?: number; exclude?: string; cap?: number } = {},
): Array<{ token: string; label: string; usd: number }> {
  const exclude = key(opts.exclude);
  const items = holdings.flatMap((holding) => {
    // A 0-USD holding is priced but has no area: it would render as an
    // invisible tile whose only visible artifact is its (possibly spoofed)
    // label in the legend. Dropping it changes no total.
    if (holding.usd === null || holding.usd <= 0) return [];
    if (opts.chainId !== undefined && holding.chainId !== opts.chainId) return [];
    if (exclude !== null && holding.token === exclude) return [];
    return [{ token: holding.token, label: holdingLabel(holding), usd: holding.usd }];
  }).sort((a, b) => b.usd - a.usd);

  const cap = opts.cap;
  if (cap === undefined || !Number.isFinite(cap) || cap < 1 || items.length <= cap) return items;
  // Fold rather than truncate so the tiles still sum to priced NAV. 'other' as
  // the folded-tail id mirrors treasury_wallet_history, which uses the literal
  // string 'other' for exactly this.
  const tail = items.slice(cap);
  return [...items.slice(0, cap), {
    token: "other",
    label: `Other (${tail.length} token${tail.length === 1 ? "" : "s"})`,
    usd: tail.reduce((sum, item) => sum + item.usd, 0),
  }];
}

/** Concentration headline: the largest holding's share of priced NAV. */
export function concentration(
  holdings: PricedHolding[],
): { token: string; label: string; usd: number; share: number } | null {
  let top: PricedHolding | null = null;
  let topUsd = 0;
  let total = 0;
  for (const holding of holdings) {
    if (holding.usd === null) continue;
    total += holding.usd;
    if (top === null || holding.usd > topUsd) {
      top = holding;
      topUsd = holding.usd;
    }
  }
  // total <= 0 covers "nothing priced" and "everything priced at 0", where a
  // share is 0/0 — an undefined headline, not a 0% one.
  if (top === null || total <= 0) return null;
  return { token: top.token, label: holdingLabel(top), usd: topUsd, share: topUsd / total };
}
