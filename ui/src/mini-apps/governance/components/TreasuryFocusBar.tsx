import { shortAddr } from "../../../utils/format";
import { ChainBadge } from "../../shared/ChainBadge";
import { TokenIdentity } from "../../shared/TokenIdentity";
import type { PricedHolding } from "../model/treasuryPricing";
import { fmtNum, fmtPct } from "../sections/common";

// Header bar for an active token drill-down: what is focused, what it is worth,
// and how to stop. Everything below it is filtered to this one token, and the
// accent-tinted bar is the only thing keeping a filtered board from reading as
// an unfiltered one — so it is never conditional on having figures to show.
//
// The token's identity is its ADDRESS, never its symbol: 19 distinct addresses
// in this treasury claim the symbol "USDC", two claim "SAFE", two claim "COW".
// So the address is printed here unconditionally (this bar is where a reader
// confirms what they drilled into) and TokenIdentity always receives
// `ambiguous`, so a contested symbol can never appear bare.
//
// Presentational only: it holds no state and decides nothing about the focus —
// it renders the holding it is handed and calls onClear().
//
// `onClear` is OPTIONAL because this bar is also the identity header of the
// token detail PAGE, where leaving is the breadcrumb's job. A "Clear" button
// there would offer to un-filter a page that was never a filter.

/** The bar's read surface. A `Pick` rather than the whole `PricedHolding`
 * because what this component reads is part of its contract, and the type
 * should say so; any `PricedHolding` satisfies it. */
export type FocusedHolding = Pick<
  PricedHolding,
  "chainId" | "token" | "symbol" | "ambiguous" | "wallets" | "supplyShare" | "usd"
>;

/** Verbatim from the holdings table's supply-share cell. A reader who met this
 * condition there must not have to work out that it is the same condition. */
const SUPPLY_EXCEEDED_TITLE =
  "Reported balance exceeds the token's own total supply — the contract's balanceOf is not trustworthy";

/** Nullable USD never reaches fmtNum directly: fmtNum(null) is "0", because
 * Number(null) === 0, and this view already shipped a "$0 NAV" once.
 *
 * Exactly 0 survives as "$0" — a CoinGecko quote of 0 is a real answer
 * ("worthless"). A positive value that would round to $0 at 2dp is NOT that
 * answer, so it renders as "< $0.01" rather than borrowing its notation. */
function fmtUsd(usd: number | null): string {
  if (usd === null) return "—";
  if (usd > 0 && usd < 0.01) return "< $0.01";
  return `$${fmtNum(usd)}`;
}

function walletsText(wallets: number | null): string {
  if (wallets === null) return "— wallets";
  return `${fmtNum(wallets)} wallet${wallets === 1 ? "" : "s"}`;
}

/** Share of the token's OWN total supply, as text + tooltip. */
function supplyShareText(share: number | null): { text: string; title: string } {
  if (share === null) {
    return { text: "— of supply", title: "Total supply not observed for this token" };
  }
  // A holding cannot exceed the token's own supply. When it does, balanceOf is
  // lying: the classic spoofed-token shape returns a constant balance to every
  // caller, so N wallets each "hold" 100% and the total lands near N x supply.
  // Printing "2,300%" would dress that up as a measurement.
  if (share > 1) return { text: "> supply", title: SUPPLY_EXCEEDED_TITLE };
  // fmtPct rounds to 1dp, so a real dust position would read "0.0% of supply" —
  // typographically identical to holding none of the token at all.
  if (share > 0 && share < 0.001) {
    return { text: "< 0.1% of supply", title: "Less than 0.1% of this token's total supply" };
  }
  return { text: `${fmtPct(share)} of supply`, title: "Share of this token's own total supply" };
}

export function TreasuryFocusBar({ holding, iconUrl, onClear }: {
  holding: FocusedHolding;
  /** Resolved logo from state.icon_overlay; absent when none is known.
   * TokenIdentity falls back to a monogram — never a placeholder image. */
  iconUrl?: string;
  /** Omitted on the token detail page — see the header note. */
  onClear?: () => void;
}) {
  const share = supplyShareText(holding.supplyShare);
  // The button's visible text is just "Clear", which out of context says
  // nothing; screen readers get the thing being cleared. Falls back to the
  // address for an unnamed token, matching what TokenIdentity renders.
  const label = holding.symbol || shortAddr(holding.token);
  return (
    <div className="gov-token-focus">
      <TokenIdentity
        address={holding.token}
        iconUrl={iconUrl}
        symbol={holding.symbol}
        ambiguous={holding.ambiguous}
      />
      {/* USD sits outside the meta row so it inherits the bar's primary text
          colour: it is the headline figure, and the rest is provenance. */}
      <span
        className="gov-mono"
        title={holding.usd === null ? "No USD quote for this token — unpriced, not zero" : undefined}
      >
        {fmtUsd(holding.usd)}
      </span>
      <span className="gov-token-focus__meta">
        <ChainBadge chainId={holding.chainId} />
        <span title={holding.token}>{shortAddr(holding.token)}</span>
        <span title="Tracked wallets holding a non-zero balance of this token">
          {walletsText(holding.wallets)}
        </span>
        <span title={share.title}>{share.text}</span>
      </span>
      {onClear && (
        <button
          type="button"
          className="gov-token-focus__clear"
          onClick={onClear}
          aria-label={`Clear ${label} filter`}
        >
          Clear
        </button>
      )}
    </div>
  );
}
