// Client-side treasury view state: which tab, which chain, whether Gnosis Ltd.
// is excluded, whether hidden (spam) tokens are shown, and the History /
// Assets controls.
//
// All of it is CLIENT-SIDE and instant — the section datasets always carry
// both chains and every wallet, so no filter here costs a round trip. It lives
// in GovernanceApp (so it survives entity drill-downs) and, in standalone
// mode, in the URL (replaceState only; defaults omitted so links stay clean).

import { isTreasuryChain, type ChainFilter } from "../model/treasuryChains";
import { measureAllowed, type HistoryRange, type Measure, type StackMode } from "../model/treasuryHistory";
import {
  DEFAULT_TREASURY_TAB,
  resolveTreasuryTab,
  type TreasuryTabId,
} from "../model/treasuryTabs";

export type AssetFilter = "all" | "hub" | "spot" | "unpriced" | "retired" | "hidden";

export const ASSET_FILTERS: readonly AssetFilter[] = ["all", "hub", "spot", "unpriced", "retired", "hidden"];
export const STACK_MODES: readonly StackMode[] = ["asset", "chain", "wallet", "class"];
export const MEASURES: readonly Measure[] = ["usd", "gno"];
export const RANGES: readonly HistoryRange[] = ["1y", "3y", "all"];

export interface TreasuryViewState {
  tab: TreasuryTabId;
  chain: ChainFilter;
  exLtd: boolean;
  showHidden: boolean;
  stackBy: StackMode;
  measure: Measure;
  range: HistoryRange;
  assetFilter: AssetFilter;
}

export const DEFAULT_TREASURY_VIEW: TreasuryViewState = {
  tab: DEFAULT_TREASURY_TAB,
  chain: 0,
  exLtd: false,
  showHidden: false,
  stackBy: "asset",
  measure: "usd",
  range: "all",
  assetFilter: "all",
};

/** Repair invalid combinations. GNO units stack only by chain or wallet, so
 * an asset/class stack keeps USD; the "hidden" asset filter only exists while
 * hidden tokens are shown. */
export function normalizeTreasuryView(view: TreasuryViewState): TreasuryViewState {
  let next = view;
  if (!measureAllowed(next.stackBy, next.measure)) next = { ...next, measure: "usd" };
  if (next.assetFilter === "hidden" && !next.showHidden) next = { ...next, assetFilter: "all" };
  return next;
}

/** Apply a user change. The field the user just set wins: choosing GNO units
 * while stacked by asset switches the stack to chain rather than refusing. */
export function applyTreasuryPatch(
  view: TreasuryViewState,
  patch: Partial<TreasuryViewState>,
): TreasuryViewState {
  let next: TreasuryViewState = { ...view, ...patch };
  if (patch.measure === "gno" && patch.stackBy === undefined && !measureAllowed(next.stackBy, "gno")) {
    next = { ...next, stackBy: "chain" };
  }
  return normalizeTreasuryView(next);
}

// ---------------------------------------------------------------------------
// URL round-trip
// ---------------------------------------------------------------------------

/** Managed query keys, all prefixed `t` so no governance key is named `token`. */
export const TREASURY_URL_KEYS = [
  "ttab", "tchain", "tltd", "thidden", "tstack", "tmeasure", "trange", "tassets",
] as const;

function oneOf<T extends string>(values: readonly T[], raw: string | null): T | undefined {
  return raw !== null && (values as readonly string[]).includes(raw) ? (raw as T) : undefined;
}

function boolParam(raw: string | null): boolean | undefined {
  if (raw === "1" || raw === "true") return true;
  if (raw === "0" || raw === "false") return false;
  return undefined;
}

/** The treasury keys PRESENT in the URL (absent or invalid keys are omitted,
 * so a caller can tell "not specified" from "default"). Old tab ids alias. */
export function treasuryViewFromParams(params: URLSearchParams): Partial<TreasuryViewState> {
  const out: Partial<TreasuryViewState> = {};
  const tab = resolveTreasuryTab(params.get("ttab"));
  if (tab) out.tab = tab;
  const chainRaw = params.get("tchain");
  if (chainRaw !== null) {
    const chain = Number(chainRaw);
    if (chain === 0 || isTreasuryChain(chain)) out.chain = chain as ChainFilter;
  }
  const ltd = boolParam(params.get("tltd"));
  if (ltd !== undefined) out.exLtd = ltd;
  const hidden = boolParam(params.get("thidden"));
  if (hidden !== undefined) out.showHidden = hidden;
  const stack = oneOf(STACK_MODES, params.get("tstack"));
  if (stack) out.stackBy = stack;
  const measure = oneOf(MEASURES, params.get("tmeasure"));
  if (measure) out.measure = measure;
  const range = oneOf(RANGES, params.get("trange"));
  if (range) out.range = range;
  const assets = oneOf(ASSET_FILTERS, params.get("tassets"));
  if (assets) out.assetFilter = assets;
  return out;
}

/** Write the non-default treasury keys into `params` (deleting every managed
 * key first, so stale values never survive). */
export function writeTreasuryParams(params: URLSearchParams, view: TreasuryViewState): void {
  for (const key of TREASURY_URL_KEYS) params.delete(key);
  const d = DEFAULT_TREASURY_VIEW;
  if (view.tab !== d.tab) params.set("ttab", view.tab);
  if (view.chain !== d.chain) params.set("tchain", String(view.chain));
  if (view.exLtd !== d.exLtd) params.set("tltd", view.exLtd ? "1" : "0");
  if (view.showHidden !== d.showHidden) params.set("thidden", view.showHidden ? "1" : "0");
  if (view.stackBy !== d.stackBy) params.set("tstack", view.stackBy);
  if (view.measure !== d.measure) params.set("tmeasure", view.measure);
  if (view.range !== d.range) params.set("trange", view.range);
  if (view.assetFilter !== d.assetFilter) params.set("tassets", view.assetFilter);
}

/** The initial view: defaults, then the server's initial-view hints
 * (view_state.filters.chain_id / exclude_ltd), then the URL, which wins. */
export function initialTreasuryView(
  fromUrl: Partial<TreasuryViewState>,
  hints: { chain_id?: unknown; exclude_ltd?: unknown } = {},
): TreasuryViewState {
  const seeded: TreasuryViewState = { ...DEFAULT_TREASURY_VIEW };
  const hintChain = Number(hints.chain_id);
  if (isTreasuryChain(hintChain)) seeded.chain = hintChain;
  if (hints.exclude_ltd === true) seeded.exLtd = true;
  return normalizeTreasuryView({ ...seeded, ...fromUrl });
}

/** One-line summary for the host model context. */
export function describeTreasuryView(view: TreasuryViewState): string {
  const chain = view.chain === 0 ? "all chains" : view.chain === 1 ? "Ethereum" : "Gnosis Chain";
  return [
    `tab=${view.tab}`,
    `chain=${chain}`,
    `exclude Gnosis Ltd.=${view.exLtd ? "yes" : "no"}`,
    `hidden (spam) tokens=${view.showHidden ? "shown" : "hidden"}`,
    `history=stacked by ${view.stackBy}, ${view.measure === "usd" ? "USD" : "GNO units"}, range ${view.range}`,
    `asset filter=${view.assetFilter}`,
  ].join("; ");
}
