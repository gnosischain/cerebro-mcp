import { useMemo, useState } from "react";

import { shortAddr } from "../../../utils/format";
import { chainShortName } from "../../shared/chainIcons";
import { fmtUsdCompact } from "../../shared/chartOptions";
import { sortWallets, type WalletHolding, type WalletSortKey } from "../model/treasuryWallets";

// Per-wallet comparison board. Button rows in a CSS grid rather than a <table>,
// matching TokenBoard: every row is a drill-down, and head/row cells share one
// grid template so the columns stay aligned.
//
// No TokenIdentity here and no symbols anywhere — a wallet address comes from
// our own census list, not from attacker-authored token metadata. The only
// label ever shown is "Ltd.", the one wallet with verifiable provenance.

const DEFAULT_MAX_ROWS = 25;

const SORTS: Array<{ key: WalletSortKey; label: string }> = [
  { key: "gnoUsd", label: "GNO value" },
  { key: "gnoUnits", label: "GNO" },
  { key: "tokensHeld", label: "tokens" },
  { key: "unnamedPositions", label: "unnamed" },
];

export interface WalletBoardProps {
  wallets: WalletHolding[];
  onSelect?: (chainId: number, wallet: string) => void;
  focused?: { chainId: number; wallet: string } | null;
  maxRows?: number;
  defaultSort?: WalletSortKey;
  /** Ltd wallets are hidden by the toolbar filter. Stated in the footer, or the
   * empty Ltd column silently reads as "no Ltd wallet exists". */
  ltdExcluded?: boolean;
}

/** `fmtNum(null)` is "0" because Number(null) === 0. Counts go through this. */
function fmtCount(value: number | null): string {
  return value === null ? "—" : value.toLocaleString();
}

/** Units at whatever precision the magnitude deserves. Dust must never round to
 * "0": a 1e-9 balance is a held position, and printing it as zero makes the
 * stronger claim that the treasury exited it. */
function fmtUnits(value: number | null): string {
  if (value === null) return "—";
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (abs >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (abs >= 1e-6) return value.toLocaleString(undefined, { maximumSignificantDigits: 3 });
  return value.toExponential(2);
}

export function WalletBoard({
  wallets,
  onSelect,
  focused,
  maxRows = DEFAULT_MAX_ROWS,
  defaultSort = "gnoUsd",
  ltdExcluded,
}: WalletBoardProps) {
  const [sort, setSort] = useState<WalletSortKey>(defaultSort);
  const ordered = useMemo(() => sortWallets(wallets, sort), [wallets, sort]);
  const shown = ordered.slice(0, maxRows);
  const hidden = ordered.length - shown.length;
  const measure = SORTS.find((entry) => entry.key === sort)?.label ?? "GNO value";

  return (
    <>
      <div className="gov-panel-modes">
        <span className="gov-caption">Sort</span>
        {SORTS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            className={entry.key === sort ? "seg__btn seg__btn--active" : "seg__btn"}
            onClick={() => setSort(entry.key)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      <div className="gov-wallet-board">
        <div className="gov-wallet-board__head">
          <span>Wallet</span>
          <span>Chain</span>
          <span className="gov-wallet-board__head--num">Tokens</span>
          <span className="gov-wallet-board__head--num">Unnamed</span>
          <span className="gov-wallet-board__head--num">GNO</span>
          <span className="gov-wallet-board__head--num">GNO value</span>
          <span />
          <span />
        </div>

        {shown.map((wallet) => {
          const isFocused = focused
            && focused.chainId === wallet.chainId
            && focused.wallet === wallet.wallet;
          return (
            <button
              key={`${wallet.chainId}:${wallet.wallet}`}
              type="button"
              className={isFocused ? "gov-wallet-row gov-wallet-row--focused" : "gov-wallet-row"}
              onClick={() => onSelect?.(wallet.chainId, wallet.wallet)}
              title={wallet.wallet}
            >
              <span className="gov-mono">{shortAddr(wallet.wallet)}</span>
              <span className="gov-wallet-row__chain">{chainShortName(wallet.chainId)}</span>
              <span className="gov-wallet-row__num">{fmtCount(wallet.tokensHeld)}</span>
              <span className="gov-wallet-row__num gov-wallet-row__unnamed">
                {fmtCount(wallet.unnamedPositions)}
              </span>
              <span className="gov-wallet-row__num">{fmtUnits(wallet.gnoUnits)}</span>
              {wallet.gnoUnits === 0 ? (
                // "$0.00" in a column headed GNO VALUE, on a row showing 98
                // tokens held, reads as "this wallet is worth nothing". It
                // holds no GNO; what the rest is worth is not measured here.
                <span className="gov-wallet-row__usd gov-wallet-row__usd--none">no GNO</span>
              ) : wallet.gnoUsd === null ? (
                <span className="gov-wallet-row__usd gov-wallet-row__usd--none">unpriced</span>
              ) : (
                <span className="gov-wallet-row__usd">{fmtUsdCompact(wallet.gnoUsd)}</span>
              )}
              <span className="gov-wallet-row__ltd">
                {wallet.isLtd && <span className="gov-ltd-badge">Ltd.</span>}
              </span>
              <span className="gov-wallet-row__chevron gov-caption">›</span>
            </button>
          );
        })}
      </div>

      <p className="gov-caption">
        Showing {shown.length} of {ordered.length} wallets by {measure}
        {hidden > 0 ? ` — ${hidden} not shown` : ""}. <strong>GNO value</strong> is GNO units at
        today&apos;s spot price — <strong>not</strong> the wallet&apos;s total value: this dataset
        carries no per-token composition, so the rest of a wallet&apos;s holdings cannot be priced
        here. Open a wallet for its full position list.
        {ltdExcluded && " Gnosis Ltd. wallets are excluded by the toolbar filter."}
      </p>
    </>
  );
}
