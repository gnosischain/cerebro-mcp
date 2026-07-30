import { useMemo, useState } from "react";

import { SparkLine } from "../../cow-explorer/components/SparkLine";
import { chainShortName } from "../../shared/chainIcons";
import { fmtUsdCompact } from "../../shared/chartOptions";
import { TokenIdentity } from "../../shared/TokenIdentity";
import { priceCoverage, type PricedHolding } from "../model/treasuryPricing";

// Ranked holdings board — the portfolio's reading surface.
//
// Rows are <button>s rather than <tr>s because every row is an action (focus
// this token, which re-filters the history panels). A table would need a
// nested button per row anyway, and the grid template in governance.css is
// shared byte-for-byte between the head and the rows so the columns still line
// up without table semantics.
//
// SECURITY, load-bearing: token symbols are attacker-authored. 19 distinct
// addresses in this treasury claim the symbol "USDC". Nothing here ever renders
// a symbol string directly — identity goes through <TokenIdentity>, which
// sanitizes the symbol and, when `ambiguous` is set, prints the address next to
// it so a spoof can never pass as the real token.
//
// SparkLine is imported from cow-explorer on purpose: it is a plain SVG
// polyline, and the whole reason it exists is to avoid booting a chart runtime
// per row. 25 ECharts instances in a scroll container is exactly what it
// prevents. (It carries no CSS of its own — `.gov-token-row__spark svg` sizes it.)

export type TokenSortKey = "usd" | "units" | "wallets" | "supplyShare";

const SORTS: Array<{ key: TokenSortKey; label: string }> = [
  { key: "usd", label: "USD" },
  { key: "units", label: "units" },
  { key: "wallets", label: "wallets" },
  { key: "supplyShare", label: "supply share" },
];

/** 231 holdings would bury every panel below the board. */
const DEFAULT_MAX_ROWS = 25;

/** Sparkline box. The CSS stretches the SVG to the cell width, so these only
 * fix the viewBox aspect ratio. */
const SPARK_WIDTH = 72;
const SPARK_HEIGHT = 20;

export interface TokenBoardProps {
  /** Already priced + sorted by `pricedHoldings`; re-sorted here per the control. */
  holdings: PricedHolding[];
  /** Logo URL for a holding, or "" when none is known — never a placeholder. */
  iconFor?: (chainId: number, token: string) => string;
  /** Monthly balance series for the row's sparkline, oldest first (from
   * `sparkValues`, which already returns [] below 2 drawable points). Called
   * once per RENDERED row, so memoize a per-token map upstream rather than
   * rescanning the ~16k history rows on every call. */
  sparkFor?: (chainId: number, token: string) => number[];
  /** The token the rest of the tab is filtered to, if any. */
  focused?: { chainId: number; token: string } | null;
  onSelect?: (chainId: number, token: string) => void;
  maxRows?: number;
  defaultSort?: TokenSortKey;
}

/** Descending, nulls last. Mirrors the private comparator in treasuryPricing —
 * "no price"/"decimals unknown" is not "smallest", and a row that cannot be
 * ranked must never outrank one that can. */
function descNullsLast(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

function sortValue(holding: PricedHolding, key: TokenSortKey): number | null {
  if (key === "units") return holding.units;
  if (key === "wallets") return holding.wallets;
  if (key === "supplyShare") return holding.supplyShare;
  return holding.usd;
}

/** Token units at whatever precision the magnitude deserves. Dust must never
 * round to "0": a 1e-9 balance is a held position, and printing it as zero
 * makes the stronger claim that the treasury exited it. */
function fmtUnits(value: number): string {
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (abs >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (abs >= 1e-6) return value.toLocaleString(undefined, { maximumSignificantDigits: 3 });
  return value.toExponential(2);
}

export function TokenBoard({
  holdings,
  iconFor,
  sparkFor,
  focused,
  onSelect,
  maxRows = DEFAULT_MAX_ROWS,
  defaultSort = "usd",
}: TokenBoardProps) {
  const [sort, setSort] = useState<TokenSortKey>(defaultSort);
  // Hide rows with no USD price. DEFAULT OFF, and that is not a preference:
  // the price overlay is fetched asynchronously, so `usd` is null for EVERY row
  // until it lands. Defaulting this on would show an empty board on first paint
  // and look like a load failure. The control also disables itself while no
  // price source has arrived, so it cannot be turned on into an empty board.
  const [pricedOnly, setPricedOnly] = useState(false);

  // Denominator for the share meter. priceCoverage() is the same total the NAV
  // headline and `concentration()` use, so the meters sum to the figure printed
  // above them instead of to some private subtotal computed here.
  const { usd: pricedTotal, priced, total } = useMemo(
    () => priceCoverage(holdings), [holdings],
  );
  const unpriced = total - priced;
  // No price has landed yet (or none is obtainable) — nothing to filter ON.
  const pricingReady = priced > 0;
  const filtered = useMemo(
    () => (pricedOnly && pricingReady ? holdings.filter((h) => h.usd !== null) : holdings),
    [holdings, pricedOnly, pricingReady],
  );

  const ranked = useMemo(() => (
    [...filtered].sort((a, b) => (
      descNullsLast(sortValue(a, sort), sortValue(b, sort))
      // Address last so the order is total: without it, the ~169 unpriced rows
      // all compare equal and the browser's sort may reshuffle them between
      // renders, which reads as data churn.
      || (a.token < b.token ? -1 : a.token > b.token ? 1 : 0)
    ))
  ), [filtered, sort]);

  const cap = Number.isFinite(maxRows) && maxRows >= 1 ? Math.floor(maxRows) : DEFAULT_MAX_ROWS;
  const shown = ranked.slice(0, cap);
  const hidden = ranked.length - shown.length;

  if (holdings.length === 0) {
    return <div className="gov-empty">No holdings in this snapshot.</div>;
  }

  return (
    <>
      <div className="gov-panel-modes" role="group" aria-label="Sort holdings by">
        <span className="gov-caption">Sort</span>
        {SORTS.map((option) => (
          <button
            key={option.key}
            type="button"
            className={option.key === sort ? "is-active" : undefined}
            aria-pressed={option.key === sort}
            onClick={() => setSort(option.key)}
          >
            {option.label}
          </button>
        ))}
        {/* Its own pill, in the board's OWN strip — not the section toolbar,
            which holds server-side filters and would cost a round trip. */}
        <span className="gov-caption">Price</span>
        <button
          type="button"
          className={pricedOnly ? "is-active" : undefined}
          aria-pressed={pricedOnly}
          disabled={!pricingReady}
          title={pricingReady
            ? `Hide the ${unpriced} holding(s) with no USD price`
            : "No price data has loaded yet"}
          onClick={() => setPricedOnly((on) => !on)}
        >
          {pricingReady ? `Priced only (${priced}/${total})` : "Priced only"}
        </button>
      </div>
      <p className="gov-caption">
        {pricingReady
          ? `${priced} of ${total} held tokens priced.`
          : "No USD prices loaded for this scope."}
        {" "}
        Unpriced is <strong>unmeasured, not worthless</strong> — a token with no
        CoinGecko listing is shown without a value, never as $0.
        {pricedOnly ? (
          <>
            {" "}Hiding {unpriced} unpriced holding(s). Note this also hides the
            spoofed look-alikes: of the tokens claiming a well-known symbol,
            nearly all are unpriced, so the filter doubles as a spam filter and
            you are no longer seeing them.
          </>
        ) : null}
      </p>

      <div className="gov-token-board">
        {/* Eight head cells in the same order as every row: the grid template is
            positional, and a missing cell would slide every later column left.
            Left readable (not aria-hidden) because rows are buttons, not table
            cells — nothing associates a value with its column, so announcing the
            column order once up front is the only labelling a reader gets. */}
        <div className="gov-token-board__head">
          <span>token</span>
          <span>chain</span>
          <span>USD</span>
          <span>share</span>
          <span>units</span>
          <span>wallets</span>
          <span>units trend</span>
          <span />
        </div>

        {shown.map((holding) => {
          const isFocused = focused != null
            && focused.chainId === holding.chainId
            && focused.token.toLowerCase() === holding.token;
          // Share of PRICED value. Unpriced rows get no share at all — dividing
          // an unknown by the priced total would invent a 0%.
          const share = holding.usd !== null && pricedTotal > 0
            ? Math.min(1, Math.max(0, holding.usd / pricedTotal))
            : null;
          const spark = sparkFor ? sparkFor(holding.chainId, holding.token) : [];
          return (
            <button
              key={`${holding.chainId}:${holding.token}`}
              type="button"
              className={`gov-token-row${isFocused ? " gov-token-row--focused" : ""}`}
              aria-current={isFocused ? "true" : undefined}
              onClick={() => onSelect?.(holding.chainId, holding.token)}
            >
              <TokenIdentity
                address={holding.token}
                iconUrl={iconFor ? iconFor(holding.chainId, holding.token) : undefined}
                symbol={holding.symbol}
                ambiguous={holding.ambiguous}
              />
              <span className="gov-token-row__chain" title={`chain ${holding.chainId}`}>
                {chainShortName(holding.chainId)}
              </span>
              {/* Unpriced is unmeasured, not worthless — the word, never "$0". */}
              {holding.usd === null ? (
                <span
                  className="gov-token-row__usd gov-token-row__usd--none"
                  title="No price quote for this address — the balance is real, its USD value is unknown."
                >
                  unpriced
                </span>
              ) : (
                <span className="gov-token-row__usd">{fmtUsdCompact(holding.usd)}</span>
              )}
              {share === null ? (
                <span className="gov-token-row__meter" aria-hidden="true" />
              ) : (
                <span
                  className="gov-token-row__meter"
                  role="img"
                  aria-label={`${(share * 100).toFixed(1)}% of priced value`}
                >
                  <span style={{ width: `${share * 100}%` }} />
                </span>
              )}
              {holding.units === null ? (
                // Decimals were never observed, so no scaling factor is known.
                // The exact on-chain integer is the only true thing we have; a
                // guessed 1e18 divisor would be a fabricated balance.
                <span
                  className="gov-token-row__units"
                  title={
                    holding.rawBalance
                      ? `Raw on-chain integer — token decimals were never observed, so units cannot be derived: ${holding.rawBalance}`
                      : "No balance recorded"
                  }
                >
                  {holding.rawBalance || "—"}
                </span>
              ) : (
                <span className="gov-token-row__units">{fmtUnits(holding.units)}</span>
              )}
              <span className="gov-token-row__wallets">
                {holding.wallets === null ? "—" : holding.wallets.toLocaleString()}
              </span>
              {/* The cell is always present even when empty: it is a grid slot,
                  and dropping it would shift the chevron under the sparkline.
                  An absent line means "not tracked" — a flat one would claim
                  "held, unchanged", which is a different statement. */}
              <span className="gov-token-row__spark">
                {spark.length > 0 && (
                  <SparkLine values={spark} width={SPARK_WIDTH} height={SPARK_HEIGHT} />
                )}
              </span>
              <span className="gov-token-row__chevron" aria-hidden="true">›</span>
            </button>
          );
        })}
      </div>

      <p className="gov-caption">
        {hidden > 0
          ? `Showing the top ${shown.length} of ${ranked.length} holdings by ${SORTS.find((option) => option.key === sort)?.label ?? sort} — ${hidden} not shown. Holdings with no value for that measure sort last.`
          : `All ${ranked.length} holding${ranked.length === 1 ? "" : "s"} shown.`}
      </p>
    </>
  );
}
