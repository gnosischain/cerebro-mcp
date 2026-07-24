// Compact "amount + token identity" leg for dense feed rows (Live view) and
// any other surface that shows `<amount> <icon><symbol>` inline. Mirrors the
// display rules of CuratedTable's amount/token cells (cells.tsx) — normalized
// amount when decimals are known, raw base units + warning otherwise, icon
// from the async CoinGecko overlay keyed by the ROW's chain — so feed rows and
// tables can never drift.

import { displayAmount } from "../model/identity";
import type { CowExplorerViewState } from "../types";
import { TokenIdentity } from "./TokenIdentity";

export interface AmountWithTokenProps {
  /** View state — only `icon_overlay` and the fallback `chain_id` are read. */
  state: CowExplorerViewState;
  /** Chain the ROW belongs to; 0/NaN falls back to `state.chain_id`
   * (mirrors cells.tsx `rowChainId`). */
  chainId: number;
  token: string;
  symbol?: string;
  amountRaw?: string | number | null;
  amount?: number | null;
  decimals?: number | null;
}

export function AmountWithToken({
  state,
  chainId,
  token,
  symbol,
  amountRaw,
  amount,
  decimals,
}: AmountWithTokenProps) {
  const display = displayAmount(amountRaw, amount, decimals);
  const overlayChain = Number.isFinite(chainId) && chainId > 0 ? chainId : state.chain_id;
  const iconUrl = state.icon_overlay?.[String(overlayChain)]?.[token.toLowerCase()] ?? "";
  const warn = display.rawUnits || display.suspect;
  return (
    <span className="cow-amount-token">
      <span
        className={warn ? "cow-amount cow-amount--raw" : "cow-amount"}
        title={
          display.rawUnits
            ? "Raw base units — token decimals unknown"
            : display.suspect
              ? "decimals=0 is ambiguous in the indexer — verify before trusting scale"
              : undefined
        }
      >
        {display.text}
        {warn && <sup>⚠</sup>}
      </span>
      <TokenIdentity address={token} iconUrl={iconUrl} symbol={symbol} />
    </span>
  );
}
