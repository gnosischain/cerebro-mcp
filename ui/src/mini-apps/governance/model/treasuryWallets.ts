// Per-wallet treasury comparison, derived from `treasury_by_wallet`.
//
// HONEST LIMIT, and it shapes the whole module: `treasury_by_wallet` aggregates
// to (chain_id, wallet_address) and carries no per-token composition. So a
// wallet's TOTAL USD is not derivable here at any price coverage — the only
// figure the overlay can produce is GNO's, because GNO is the one token whose
// per-wallet units the dataset actually reports. The column is therefore
// "GNO value", never "wallet value"; the true total lives on the wallet detail
// page, where the composition data exists.

import { finite } from "../../shared/rowDataset";
import { truthy } from "./treasuryHistory";
import { priceFor, type PriceSource } from "./treasuryPricing";

/** Frontend mirror of governance_explorer.py GNO_TOKENS.
 *
 * Hardcoded ADDRESSES, never a symbol lookup: 19 addresses in this treasury
 * claim the symbol "USDC", and resolving GNO by symbol would be the same class
 * of mistake waiting to happen. */
export const GNO_TOKENS: Record<number, string> = {
  1: "0x6810e776880c02933d47db1b9fc05908e5386b96",
  100: "0x9c58bacc331c9aa871afd802db6379a98e80cedb",
};

export interface WalletHolding {
  chainId: number;
  /** Lowercase. NOT attacker-authored — these come from our own census list. */
  wallet: string;
  isLtd: boolean;
  tokensHeld: number | null;
  unnamedPositions: number | null;
  gnoUnits: number | null;
  /** gnoUnits x spot(GNO). NEVER this wallet's total value — see the header. */
  gnoUsd: number | null;
}

export type WalletSortKey = "gnoUsd" | "gnoUnits" | "tokensHeld" | "unnamedPositions";

/** Descending, nulls last. "Unpriced" and "unknown" are not "smallest": a row
 * that cannot be ranked must never outrank one that can. */
function descNullsLast(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

export function walletSortValue(holding: WalletHolding, key: WalletSortKey): number | null {
  if (key === "gnoUnits") return holding.gnoUnits;
  if (key === "tokensHeld") return holding.tokensHeld;
  if (key === "unnamedPositions") return holding.unnamedPositions;
  return holding.gnoUsd;
}

/** rowsToObjects(treasury_by_wallet) -> priced and sorted. */
export function walletHoldings(
  rows: Array<Record<string, unknown>>,
  src: PriceSource | null,
): WalletHolding[] {
  const wallets = rows.flatMap<WalletHolding>((row) => {
    const chainId = finite(row.chain_id);
    const wallet = String(row.wallet_address ?? "").trim().toLowerCase();
    // Both are grain keys upstream, so this only fires on a malformed dataset —
    // but a row without them cannot be identified, linked or drilled into, and
    // rendering it would put an unattributable balance in the table.
    if (chainId === null || !wallet) return [];
    const gnoUnits = finite(row.gno_units);
    const gnoToken = GNO_TOKENS[chainId];
    const gnoPrice = gnoToken ? priceFor(src, chainId, gnoToken) : null;
    return [{
      chainId,
      wallet,
      // finite() returns null for booleans, so the obvious finite(row.is_ltd)
      // would read every Ltd wallet as not-Ltd.
      isLtd: truthy(row.is_ltd),
      tokensHeld: finite(row.tokens_held),
      unnamedPositions: finite(row.unnamed_positions),
      gnoUnits,
      gnoUsd: gnoUnits === null || gnoPrice === null ? null : gnoUnits * gnoPrice,
    }];
  });

  return wallets.sort((a, b) => (
    descNullsLast(a.gnoUsd, b.gnoUsd)
    || descNullsLast(a.gnoUnits, b.gnoUnits)
    // Address last so the order is total and stable across renders.
    || (a.wallet < b.wallet ? -1 : a.wallet > b.wallet ? 1 : 0)
  ));
}

export function sortWallets(
  wallets: WalletHolding[],
  key: WalletSortKey,
): WalletHolding[] {
  return [...wallets].sort((a, b) => (
    descNullsLast(walletSortValue(a, key), walletSortValue(b, key))
    || (a.wallet < b.wallet ? -1 : a.wallet > b.wallet ? 1 : 0)
  ));
}
