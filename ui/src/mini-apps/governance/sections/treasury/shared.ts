import { useMemo } from "react";

import { finite, rowsToObjects } from "../../../shared/rowDataset";
import { chainsIn, sparkValues } from "../../model/treasuryHistory";
import { pricedHoldings, priceSourceFrom, type PricedHolding, type PriceSource } from "../../model/treasuryPricing";
import { walletHoldings, type WalletHolding } from "../../model/treasuryWallets";
import { useDataset, type GovViewContext } from "../common";

// One derivation shared by all four treasury views.
//
// Lifted out of TreasurySection so the tabs cannot disagree with each other:
// when each view derived its own holdings and its own chain scope, the hero
// ended up summing USD across two snapshots taken years apart.

export interface TreasuryModel {
  priceSource: PriceSource | null;
  iconFor: (chainId: number, token: string) => string;
  holdings: PricedHolding[];
  wallets: WalletHolding[];
  summaryRows: Array<Record<string, unknown>>;
  chainHistRows: Array<Record<string, unknown>>;
  tokenHistRows: Array<Record<string, unknown>>;
  walletHistRows: Array<Record<string, unknown>>;
  sparkFor: (chainId: number, token: string) => number[];
  /** Chains present in the history data, ascending. */
  historyChains: number[];
}

export function useTreasuryModel(ctx: GovViewContext): TreasuryModel {
  const summaryDs = useDataset(ctx, "treasury_summary");
  const holdingsDs = useDataset(ctx, "treasury_holdings");
  const byWalletDs = useDataset(ctx, "treasury_by_wallet");
  const chainHistDs = useDataset(ctx, "treasury_chain_history");
  const tokenHistDs = useDataset(ctx, "treasury_token_history");
  const walletHistDs = useDataset(ctx, "treasury_wallet_history");

  const priceSource = useMemo(
    () => priceSourceFrom(ctx.state.price_overlay, ctx.state.price_overlay_at),
    [ctx.state.price_overlay, ctx.state.price_overlay_at],
  );

  const icons = ctx.state.icon_overlay ?? {};
  const iconFor = useMemo(
    () => (chainId: number, token: string) => icons[String(chainId)]?.[token.toLowerCase()] ?? "",
    [icons],
  );

  const summaryRows = useMemo(() => rowsToObjects(summaryDs), [summaryDs]);
  const holdings = useMemo(
    () => pricedHoldings(rowsToObjects(holdingsDs), priceSource),
    [holdingsDs, priceSource],
  );
  const wallets = useMemo(
    () => walletHoldings(rowsToObjects(byWalletDs), priceSource),
    [byWalletDs, priceSource],
  );
  const chainHistRows = useMemo(() => rowsToObjects(chainHistDs), [chainHistDs]);
  const tokenHistRows = useMemo(() => rowsToObjects(tokenHistDs), [tokenHistDs]);
  const walletHistRows = useMemo(() => rowsToObjects(walletHistDs), [walletHistDs]);

  // Precomputed so the board never rescans the history dataset once per row.
  const sparkIndex = useMemo(() => {
    const index = new Map<string, number[]>();
    for (const chainId of chainsIn(tokenHistRows)) {
      for (const held of holdings) {
        if (held.chainId !== chainId) continue;
        const values = sparkValues(tokenHistRows, chainId, held.token);
        if (values.length) index.set(`${chainId}:${held.token}`, values);
      }
    }
    return index;
  }, [tokenHistRows, holdings]);

  const sparkFor = useMemo(
    () => (chainId: number, token: string) =>
      sparkIndex.get(`${chainId}:${token.toLowerCase()}`) ?? [],
    [sparkIndex],
  );

  const historyChains = useMemo(() => chainsIn(chainHistRows), [chainHistRows]);

  return {
    priceSource, iconFor, holdings, wallets, summaryRows,
    chainHistRows, tokenHistRows, walletHistRows, sparkFor, historyChains,
  };
}

/** The chain a single-chain panel should speak for.
 *
 * Pinned to the toolbar when it is set; otherwise the chain with the NEWEST
 * snapshot. Never a blend: Ethereum's latest snapshot and Gnosis Chain's are
 * years apart, so a figure summed over both describes no single moment — and a
 * concentration reading taken over a years-old snapshot is not a reading of
 * today. Returns null only when there is no summary row at all.
 */
export function useChainScope(
  summaryRows: Array<Record<string, unknown>>,
  chainFilter: unknown,
): number | null {
  return useMemo(() => {
    const pinned = finite(chainFilter);
    if (pinned) return pinned;
    let best: { chainId: number; asOf: string } | null = null;
    for (const row of summaryRows) {
      const chainId = finite(row.chain_id);
      const asOf = String(row.as_of ?? "");
      if (chainId === null || !asOf) continue;
      if (!best || asOf > best.asOf) best = { chainId, asOf };
    }
    return best?.chainId ?? null;
  }, [summaryRows, chainFilter]);
}

/** Holdings restricted to one chain, or all of them when there is no scope. */
export function scopeHoldings(
  holdings: PricedHolding[],
  chainId: number | null,
): PricedHolding[] {
  return chainId === null ? holdings : holdings.filter((held) => held.chainId === chainId);
}
