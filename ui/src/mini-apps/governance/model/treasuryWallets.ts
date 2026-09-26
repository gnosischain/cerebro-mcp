// Treasury wallets, grouped by ADDRESS across chains.
//
// The census tracks the same addresses on Ethereum and Gnosis Chain (a Safe
// deployed at one address on both), so the Wallets view is one row per
// address with a chip per chain — not one row per (chain, wallet) pair, which
// made the same Safe appear twice and invited summing it wrong.
//
// Filters are client-side and instant: the chain filter keeps only that
// chain's rows (an address held only elsewhere disappears, and is COUNTED),
// and "exclude Gnosis Ltd." drops the Ltd rows (also counted). Nothing is
// ever hidden without the view saying how much.

import { chainsIn, type ChainFilter } from "./treasuryChains";
import type { WalletRow } from "./treasuryRows";

export interface WalletGroup {
  address: string;
  label: string;
  labelSource: string;
  isLtd: boolean;
  /** One row per chain in scope, ascending chain id. */
  chains: WalletRow[];
  /** Hub-priced value summed over the chains in scope; null when unknown. */
  navUsd: number | null;
  gnoUnits: number | null;
  tokensHeld: number | null;
  pricedPositions: number | null;
  unpricedPositions: number | null;
  hiddenPositions: number | null;
}

export interface WalletGrouping {
  groups: WalletGroup[];
  /** (chain, wallet) pairs shown. */
  pairs: number;
  /** Rows the chain filter hides, and addresses hidden ENTIRELY by it
   * (tracked only on the other chain). */
  hiddenByChain: { pairs: number; addresses: number };
  /** Rows / addresses the Gnosis Ltd. exclusion hides. */
  hiddenByLtd: { pairs: number; addresses: number };
}

function sumOrNull(values: Array<number | null>): number | null {
  let total: number | null = null;
  for (const value of values) {
    if (value === null) continue;
    total = (total ?? 0) + value;
  }
  return total;
}

export function walletGroups(
  rows: WalletRow[],
  opts: { chain: ChainFilter; exLtd: boolean },
): WalletGrouping {
  const inScope = new Set<number>(chainsIn(opts.chain));
  const byAddress = new Map<string, WalletRow[]>();
  for (const row of rows) {
    const list = byAddress.get(row.wallet) ?? [];
    list.push(row);
    byAddress.set(row.wallet, list);
  }
  const grouping: WalletGrouping = {
    groups: [],
    pairs: 0,
    hiddenByChain: { pairs: 0, addresses: 0 },
    hiddenByLtd: { pairs: 0, addresses: 0 },
  };
  for (const [address, all] of byAddress) {
    const isLtd = all.some((row) => row.isLtd);
    const scoped = all.filter((row) => inScope.has(row.chainId));
    if (opts.exLtd && isLtd) {
      grouping.hiddenByLtd.pairs += scoped.length;
      grouping.hiddenByLtd.addresses += scoped.length > 0 ? 1 : 0;
      continue;
    }
    grouping.hiddenByChain.pairs += all.length - scoped.length;
    if (scoped.length === 0) {
      grouping.hiddenByChain.addresses += 1;
      continue;
    }
    const chains = [...scoped].sort((a, b) => a.chainId - b.chainId);
    const labelled = all.find((row) => row.label !== "");
    grouping.pairs += chains.length;
    grouping.groups.push({
      address,
      label: labelled?.label ?? "",
      labelSource: labelled?.labelSource ?? "",
      isLtd,
      chains,
      navUsd: sumOrNull(chains.map((row) => row.navUsd)),
      gnoUnits: sumOrNull(chains.map((row) => row.gnoUnits)),
      tokensHeld: sumOrNull(chains.map((row) => row.tokensHeld)),
      pricedPositions: sumOrNull(chains.map((row) => row.pricedPositions)),
      unpricedPositions: sumOrNull(chains.map((row) => row.unpricedPositions)),
      hiddenPositions: sumOrNull(chains.map((row) => row.hiddenPositions)),
    });
  }
  grouping.groups = sortWalletGroups(grouping.groups, "value");
  return grouping;
}

export type WalletSortKey = "value" | "gno" | "tokens" | "name";

/** Descending, nulls last: "unknown" never outranks a measured value. */
function descNullsLast(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

/** Name order: labelled wallets alphabetically, unlabelled after them. */
function byName(a: WalletGroup, b: WalletGroup): number {
  if (a.label && !b.label) return -1;
  if (!a.label && b.label) return 1;
  return a.label.localeCompare(b.label);
}

export function sortWalletGroups(groups: WalletGroup[], key: WalletSortKey): WalletGroup[] {
  const measure = (group: WalletGroup): number | null => {
    if (key === "gno") return group.gnoUnits;
    if (key === "tokens") return group.tokensHeld;
    return group.navUsd;
  };
  return [...groups].sort((a, b) => (
    (key === "name" ? byName(a, b) : descNullsLast(measure(a), measure(b)))
    // Address last, so the order is total and stable across renders.
    || (a.address < b.address ? -1 : a.address > b.address ? 1 : 0)
  ));
}

/** Case-insensitive match on the label or any part of the address. */
export function matchesQuery(group: WalletGroup, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return group.label.toLowerCase().includes(needle) || group.address.includes(needle);
}

/** The chain a click on the address opens: the one holding the most value,
 * then the most tokens, then Ethereum. */
export function primaryChainOf(group: Pick<WalletGroup, "chains">): number {
  const [best] = [...group.chains].sort((a, b) => (
    descNullsLast(a.navUsd, b.navUsd)
    || descNullsLast(a.tokensHeld, b.tokensHeld)
    || a.chainId - b.chainId
  ));
  return best?.chainId ?? 1;
}
