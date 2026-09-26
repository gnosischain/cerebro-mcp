// Treasury valuation — which figure a holding is worth, and why.
//
// USD is SERVER-SIDE and real: `value_usd` = balance x the dbt price hub's
// price on that date, matched by ADDRESS through a reviewed registry. The
// CoinGecko spot overlay is a today-only FALLBACK for real tokens the hub does
// not cover; it never touches history. The rules, in order:
//
//   1. spam            -> never valued (hidden by default)
//   2. retired_mirror  -> never valued: EURe/GBPe v1 mirrors v2 after the
//                         2024-08-25 migration, so valuing both double-counts
//   3. server value    -> "hub" (price_source "hub" or "hub_proxy": a pegged
//                         asset's hub series, flagged `proxy` for a marker)
//   4. spot            -> only for a 'listed' token (a reviewed real token the
//                         hub does not price) that is spot_eligible AND has no
//                         server value AND whose quote the server did not
//                         refuse as implausible AND a quote exists. An
//                         'unverified' token is NEVER spot-valued.
//   5. otherwise       -> "unpriced": unknown, NEVER zero
//
// Nothing here ever turns a missing number into 0.

import { shortAddr } from "../../../utils/format";
import type { TreemapItem } from "../../shared/chartOptions";
import { finite } from "../../shared/rowDataset";
import { chainsIn, type ChainFilter } from "./treasuryChains";
import type { AssetClass, HoldingRow, SpamReason, TokenClass } from "./treasuryRows";

// ---------------------------------------------------------------------------
// Spot fallback overlay
// ---------------------------------------------------------------------------

export interface SpotSource {
  /** "spot_fallback" on the wire. */
  role: string;
  /** ISO instant the quotes were captured ("" when the server sent none). */
  at: string;
  /** chainId -> lowercase token -> USD. */
  quotes: Record<string, Record<string, number>>;
  /** chainId -> tokens whose quote the server refused as implausible. */
  refused: Record<string, Set<string>>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function chainKey(value: unknown): string {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? String(n) : "";
}

function tokenKey(value: unknown): string {
  const text = String(value ?? "").trim().toLowerCase();
  return /^0x[0-9a-f]+$/.test(text) ? text : "";
}

/** Adapt view_state.price_overlay (+ price_overlay_at). Accepts ONLY
 * `kind: "spot"`: a historical overlay is not part of this contract, and
 * guessing at its shape would put a fabricated series into history. Null
 * until the overlay lands (the server starts with `{}`). */
export function spotSourceFrom(overlay: unknown, at: unknown): SpotSource | null {
  if (!isRecord(overlay) || overlay.kind !== "spot" || !isRecord(overlay.by_chain)) return null;
  const quotes: Record<string, Record<string, number>> = {};
  for (const [chain, tokens] of Object.entries(overlay.by_chain)) {
    const ck = chainKey(chain);
    if (!ck || !isRecord(tokens)) continue;
    const perChain = quotes[ck] ?? {};
    for (const [token, price] of Object.entries(tokens)) {
      const tk = tokenKey(token);
      const usd = finite(price);
      // 0 survives: a quote of exactly 0 is an answer ("worthless"), and
      // differs from "no quote". A negative quote is not a price.
      if (tk && usd !== null && usd >= 0) perChain[tk] = usd;
    }
    quotes[ck] = perChain;
  }
  const refused: Record<string, Set<string>> = {};
  if (isRecord(overlay.excluded_implausible)) {
    for (const [chain, tokens] of Object.entries(overlay.excluded_implausible)) {
      const ck = chainKey(chain);
      if (!ck || !Array.isArray(tokens)) continue;
      refused[ck] = new Set(tokens.map(tokenKey).filter(Boolean));
    }
  }
  return {
    role: typeof overlay.role === "string" ? overlay.role : "",
    at: typeof at === "string" ? at : "",
    quotes,
    refused,
  };
}

export function spotQuote(spot: SpotSource | null, chainId: number, token: string): number | null {
  if (!spot) return null;
  const quote = spot.quotes[chainKey(chainId)]?.[tokenKey(token)];
  return quote === undefined ? null : quote;
}

export function spotRefused(spot: SpotSource | null, chainId: number, token: string): boolean {
  return Boolean(spot?.refused[chainKey(chainId)]?.has(tokenKey(token)));
}

// ---------------------------------------------------------------------------
// Valuation of one position
// ---------------------------------------------------------------------------

export type ValueKind = "hub" | "spot" | "unpriced" | "refused" | "hidden" | "retired";

export interface Valuation {
  kind: ValueKind;
  /** USD, or null when not valued. Never 0 for "unknown". */
  usd: number | null;
  /** The price behind `usd`: hub price, or the spot quote. */
  price: number | null;
  /** Hub price date, or the spot capture instant. */
  priceDate: string;
  /** Hub-priced through a pegged asset's series (price_source "hub_proxy"). */
  proxy: boolean;
}

export type Valuable = Pick<
  HoldingRow,
  "chainId" | "token" | "tokenClass" | "units" | "unitsExLtd" | "valueUsd" | "valueUsdExLtd"
  | "priceUsd" | "priceDate" | "priceSource" | "spotEligible" | "hasExLtd"
>;

export function valuationOf(holding: Valuable, spot: SpotSource | null, exLtd = false): Valuation {
  const none = { price: null, priceDate: "", proxy: false };
  if (holding.tokenClass === "spam") {
    return { kind: "hidden", usd: null, ...none };
  }
  if (holding.tokenClass === "retired_mirror") {
    return { kind: "retired", usd: null, price: holding.priceUsd, priceDate: holding.priceDate, proxy: false };
  }
  const useExLtd = exLtd && holding.hasExLtd;
  const hubPrice = { price: holding.priceUsd, priceDate: holding.priceDate, proxy: holding.priceSource === "hub_proxy" };
  if (holding.valueUsd !== null) {
    const usd = useExLtd ? holding.valueUsdExLtd : holding.valueUsd;
    return usd === null ? { kind: "unpriced", usd: null, ...hubPrice } : { kind: "hub", usd, ...hubPrice };
  }
  // Spot is a fallback for REVIEWED tokens the hub does not price. The class
  // check is belt-and-braces: the server sets spot_eligible for 'listed' only,
  // and an unverified token must never be valued even if a flag says so.
  if (holding.tokenClass === "listed" && holding.spotEligible) {
    if (spotRefused(spot, holding.chainId, holding.token)) {
      return { kind: "refused", usd: null, ...none };
    }
    const quote = spotQuote(spot, holding.chainId, holding.token);
    const units = useExLtd ? holding.unitsExLtd : holding.units;
    if (quote !== null && units !== null) {
      return { kind: "spot", usd: units * quote, price: quote, priceDate: spot?.at ?? "", proxy: false };
    }
  }
  return { kind: "unpriced", usd: null, ...hubPrice, proxy: false };
}

/** Valuations keyed by `holding.key`, computed once per (rows, overlay, Ltd). */
export function valuationMap(
  holdings: HoldingRow[],
  spot: SpotSource | null,
  exLtd: boolean,
): Map<string, Valuation> {
  return new Map(holdings.map((holding) => [holding.key, valuationOf(holding, spot, exLtd)]));
}

// ---------------------------------------------------------------------------
// Totals
// ---------------------------------------------------------------------------

export interface ValueSplit {
  hubUsd: number;
  spotUsd: number;
  totalUsd: number;
}

export interface TreasuryTotals extends ValueSplit {
  byChain: Record<number, ValueSplit>;
  counts: {
    hub: number;
    spot: number;
    unpriced: number;
    refused: number;
    hidden: number;
    retired: number;
    /** priced + listed + unverified tokens actually held (not spam, not retired). */
    visible: number;
  };
}

function emptySplit(): ValueSplit {
  return { hubUsd: 0, spotUsd: 0, totalUsd: 0 };
}

/** Hub + spot totals over the holdings in scope. Counts are per token row. */
export function totalsOf(
  holdings: HoldingRow[],
  valuations: Map<string, Valuation>,
  chain: ChainFilter = 0,
): TreasuryTotals {
  const chains = new Set<number>(chainsIn(chain));
  const totals: TreasuryTotals = {
    ...emptySplit(),
    byChain: {},
    counts: { hub: 0, spot: 0, unpriced: 0, refused: 0, hidden: 0, retired: 0, visible: 0 },
  };
  for (const holding of holdings) {
    if (!chains.has(holding.chainId)) continue;
    const valuation = valuations.get(holding.key);
    if (!valuation) continue;
    const split = totals.byChain[holding.chainId] ?? emptySplit();
    totals.byChain[holding.chainId] = split;
    switch (valuation.kind) {
      case "hub":
        totals.counts.hub += 1;
        totals.counts.visible += 1;
        split.hubUsd += valuation.usd ?? 0;
        totals.hubUsd += valuation.usd ?? 0;
        break;
      case "spot":
        totals.counts.spot += 1;
        totals.counts.visible += 1;
        split.spotUsd += valuation.usd ?? 0;
        totals.spotUsd += valuation.usd ?? 0;
        break;
      case "refused":
        totals.counts.refused += 1;
        totals.counts.visible += 1;
        break;
      case "unpriced":
        totals.counts.unpriced += 1;
        totals.counts.visible += 1;
        break;
      case "hidden":
        totals.counts.hidden += 1;
        break;
      case "retired":
        totals.counts.retired += 1;
        break;
    }
    split.totalUsd = split.hubUsd + split.spotUsd;
  }
  totals.totalUsd = totals.hubUsd + totals.spotUsd;
  return totals;
}

// ---------------------------------------------------------------------------
// Asset rows (optionally merged across chains by registry asset_key)
// ---------------------------------------------------------------------------

export interface AssetMember {
  holding: HoldingRow;
  valuation: Valuation;
}

export type AssetKind = ValueKind | "mixed";

export interface AssetRow {
  /** `asset:<asset_key>` for a merged registry asset, else `<chain>:<token>`. */
  key: string;
  /** Display label: the trusted registry symbol when there is one, else the
   * SANITIZED on-chain symbol ("" when none — render the address). */
  label: string;
  /** Label comes from the reviewed registry. */
  trusted: boolean;
  tokenClass: TokenClass;
  spamReason: SpamReason;
  assetClass: AssetClass;
  assetKey: string;
  /** Members, largest value first; `members[0]` is what a click opens. */
  members: AssetMember[];
  usd: number | null;
  hubUsd: number;
  spotUsd: number;
  kind: AssetKind;
  /** A hub-priced member is priced through a pegged asset's series. */
  proxy: boolean;
  units: number | null;
  holders: number | null;
  priceUsd: number | null;
  priceDate: string;
  supplyShare: number | null;
  symbolCollisions: number | null;
  /** Spam: rendered only when hidden tokens are shown. */
  hidden: boolean;
  /** Lowercased haystack for search: labels, symbols, names, addresses. */
  search: string;
}

function descNullsLast(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

function kindOf(members: AssetMember[]): AssetKind {
  const kinds = new Set(members.map((member) => member.valuation.kind));
  if (kinds.size === 1) return members[0].valuation.kind;
  if (kinds.has("hub") && kinds.has("spot")) return "mixed";
  if (kinds.has("hub")) return "hub";
  if (kinds.has("spot")) return "spot";
  return members[0].valuation.kind;
}

function sumOrNull(values: Array<number | null>): number | null {
  let total = 0;
  for (const value of values) {
    if (value === null) return null;
    total += value;
  }
  return total;
}

function mergeable(holding: HoldingRow): boolean {
  return holding.assetKey !== ""
    && holding.tokenClass !== "spam"
    && holding.tokenClass !== "retired_mirror";
}

/** Asset rows. With `merge`, registry tokens sharing an asset_key collapse
 * into one row (GNO on Ethereum + GNO on Gnosis Chain). Spam and retired
 * mirrors never merge: a mirror folded into its v2 row would double-count,
 * and a spoof must never borrow a real asset's row. */
export function assetRows(
  holdings: HoldingRow[],
  valuations: Map<string, Valuation>,
  opts: { merge?: boolean; exLtd?: boolean } = {},
): AssetRow[] {
  const groups = new Map<string, AssetMember[]>();
  for (const holding of holdings) {
    const valuation = valuations.get(holding.key);
    if (!valuation) continue;
    const key = opts.merge && mergeable(holding) ? `asset:${holding.assetKey}` : holding.key;
    const members = groups.get(key) ?? [];
    members.push({ holding, valuation });
    groups.set(key, members);
  }
  const rows: AssetRow[] = [];
  for (const [key, unsorted] of groups) {
    const members = [...unsorted].sort((a, b) => (
      descNullsLast(a.valuation.usd, b.valuation.usd)
      || descNullsLast(a.holding.units, b.holding.units)
      || a.holding.chainId - b.holding.chainId
    ));
    const lead = members[0].holding;
    const registrySymbols = [...new Set(members.map((member) => member.holding.registrySymbol).filter(Boolean))];
    const trusted = registrySymbols.length > 0;
    const label = registrySymbols.length === 1
      ? registrySymbols[0]
      : registrySymbols.length > 1
        ? lead.assetKey || registrySymbols[0]
        : lead.symbol;
    const valued = members.filter((member) => member.valuation.usd !== null);
    const hubUsd = members.reduce((acc, member) => acc + (member.valuation.kind === "hub" ? member.valuation.usd ?? 0 : 0), 0);
    const spotUsd = members.reduce((acc, member) => acc + (member.valuation.kind === "spot" ? member.valuation.usd ?? 0 : 0), 0);
    const units = sumOrNull(members.map((member) => (opts.exLtd && member.holding.hasExLtd
      ? member.holding.unitsExLtd
      : member.holding.units)));
    const holders = sumOrNull(members.map((member) => member.holding.walletsHolding));
    rows.push({
      key,
      label,
      trusted,
      tokenClass: lead.tokenClass,
      spamReason: lead.spamReason,
      assetClass: lead.assetClass,
      assetKey: lead.assetKey,
      members,
      usd: valued.length > 0 ? hubUsd + spotUsd : null,
      hubUsd,
      spotUsd,
      kind: kindOf(members),
      proxy: members.some((member) => member.valuation.kind === "hub" && member.valuation.proxy),
      units,
      holders,
      priceUsd: members[0].valuation.price,
      priceDate: members[0].valuation.priceDate,
      supplyShare: members.length === 1 ? lead.supplyShare : null,
      symbolCollisions: members.length === 1 ? lead.symbolCollisions : null,
      hidden: lead.tokenClass === "spam",
      search: members.map((member) => [
        member.holding.registrySymbol, member.holding.symbol, member.holding.name,
        member.holding.assetKey, member.holding.token,
      ].join(" ")).join(" ").toLowerCase(),
    });
  }
  return rows.sort((a, b) => (
    descNullsLast(a.usd, b.usd)
    || descNullsLast(a.units, b.units)
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
  ));
}

/** Display label for charts and tiles. Untrusted labels always carry the
 * address, so a tile can never read as a bare, spoofable "USDC". */
export function assetDisplayLabel(asset: Pick<AssetRow, "label" | "trusted" | "members">): string {
  const token = asset.members[0]?.holding.token ?? "";
  if (!asset.label) return shortAddr(token);
  return asset.trusted ? asset.label : `${asset.label} ${shortAddr(token)}`;
}

/** Treemap items: valued assets only (area IS value, so an unpriced asset has
 * no honest tile), descending, with the tail folded into "Other (n)" so the
 * tiles still sum to the valued total. */
export function compositionItems(
  assets: AssetRow[],
  opts: { excludeAssetKey?: string; cap?: number } = {},
): TreemapItem[] {
  const cap = Math.max(1, Math.floor(opts.cap ?? 24));
  const items = assets
    .filter((asset) => !asset.hidden && asset.usd !== null && asset.usd > 0)
    .filter((asset) => !opts.excludeAssetKey || asset.assetKey !== opts.excludeAssetKey)
    .map((asset) => ({ id: asset.key, name: assetDisplayLabel(asset), value: asset.usd ?? 0 }))
    .sort((a, b) => b.value - a.value);
  if (items.length <= cap) return items;
  const tail = items.slice(cap - 1);
  return [
    ...items.slice(0, cap - 1),
    { id: "other", name: `Other (${tail.length})`, value: tail.reduce((acc, item) => acc + item.value, 0) },
  ];
}
