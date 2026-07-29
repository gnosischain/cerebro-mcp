import { useMemo } from "react";

import { shortAddr } from "../../../utils/format";
import { finite } from "../../shared/rowDataset";
import { priceCoverage, type PriceSource, type PricedHolding } from "../model/treasuryPricing";
import { fmtNum } from "../sections/common";

// Per-chain treasury state card.
//
// One card PER CHAIN, never a blended row. Chain 1 snapshots through 2026-07
// and chain 100 stopped at 2022-11, so a single merged figure would mix a
// current snapshot with a ~1,336-day-old one and neither as-of date could then
// be stated. Each card carries its own anchor, its own date, and its own
// staleness verdict.
//
// The headline is a LOWER BOUND, never "NAV". We know how many held tokens have
// no price (169 of 231 on mainnet); we do not and cannot know what they are
// worth. Printing the priced subtotal as "NAV" would assert the unpriced tail
// is worth nothing — and that tail is where every one of the 19 tokens claiming
// the symbol "USDC" lives.
//
// Presentational only: no fetching, no tool calls. Everything arrives as props.

/** Chain id -> display name.
 *
 * Deliberately not `chainShortName()` from shared/chainIcons: that map is sized
 * for a 16px badge ("Gnosis") and falls back to a bare numeric id, which as a
 * card heading would read as a number with no label. */
const CHAIN_NAMES: Record<string, string> = { "1": "Ethereum", "100": "Gnosis Chain" };

export function chainName(chainId: unknown): string {
  const key = String(chainId ?? "").trim();
  if (key === "") return "Unknown chain";
  return CHAIN_NAMES[key] ?? `Chain ${key}`;
}

/** Snapshot age past which the card flags itself. The treasury job publishes far
 * more often than monthly, so a chain with no snapshot in 30 days has stopped
 * rather than slowed. */
export const DEFAULT_STALE_AFTER_DAYS = 30;

const DAY_MS = 86_400_000;

/** Whole days between the snapshot's calendar day and `now`, or null when the
 * row carries no parseable date.
 *
 * Only the YYYY-MM-DD prefix is read, as UTC. `Date.parse` on a ClickHouse
 * "2022-11-01 00:00:00" is implementation-defined and V8 reads it as LOCAL
 * time, so the same row would age differently for two viewers — a day-grained
 * staleness verdict has no business depending on the reader's timezone. */
export function snapshotAgeDays(asOf: unknown, now: number): number | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(asOf ?? "").trim());
  if (!parts) return null;
  const stamp = Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  // A snapshot dated in the future is clock skew, not negative staleness.
  return Math.max(0, Math.floor((now - stamp) / DAY_MS));
}

/** `fmtNum(null)` returns "0" because `Number(null) === 0`. That coercion
 * already shipped a "$0 NAV" once, so every nullable figure on this card is
 * dash-guarded before it reaches a formatter. */
function fmtOrDash(value: number | null): string {
  return value === null ? "—" : fmtNum(value);
}

/** Full-precision USD for the headline. `fmtUsdCompact` exists for axis ticks
 * and tooltips, where "$104.90M" is the whole point; here the chain cards stack
 * vertically under `font-variant-numeric: tabular-nums`, which only buys
 * alignment if the digits are actually present. */
function fmtUsd(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  // Cents on a nine-figure treasury are noise; below $1,000 they are the number.
  const digits = Math.abs(value) >= 1000 ? 0 : 2;
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function shortStamp(value: string): string {
  const text = value.trim();
  const parts = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(text);
  if (!parts) return text;
  // "UTC" only when the string actually carries the Z marker. The overlay's zone
  // is not otherwise knowable, and labelling a naive stamp UTC would be an
  // assertion rather than a reading.
  return `${parts[1]} ${parts[2]}${text.endsWith("Z") ? " UTC" : ""}`;
}

/** A price with no capture time is not evidence, so the timestamp travels with
 * the figure and a missing one is stated outright instead of omitted. */
function priceProvenance(source: PriceSource | null): string {
  if (!source) return "No price overlay loaded";
  const kind = source.kind === "spot" ? "Spot prices" : "Historical prices";
  const stamp = shortStamp(source.at);
  return stamp === "" ? `${kind}, capture time unknown` : `${kind} captured ${stamp}`;
}

export interface TreasuryChainCardProps {
  /** One `rowsToObjects(treasury_summary)` row. */
  row: Record<string, unknown>;
  /** Priced holdings for THIS chain. */
  holdings: PricedHolding[];
  /** The active overlay, or null before one lands. */
  priceSource: PriceSource | null;
  /** Snapshot age, in days, past which the stale badge appears. */
  staleAfterDays?: number;
  /** Epoch ms treated as "now". Injectable so the staleness verdict is testable
   *  without freezing the clock. */
  now?: number;
}

export function TreasuryChainCard({
  row,
  holdings,
  priceSource,
  staleAfterDays = DEFAULT_STALE_AFTER_DAYS,
  now,
}: TreasuryChainCardProps) {
  const chainId = finite(row.chain_id);
  const coverage = useMemo(() => {
    // Belt-and-braces against a caller handing over the whole holdings set: a
    // card that summed two chains would value a stale snapshot alongside a
    // current one, which is the exact confusion the per-chain card exists to
    // prevent. Skipped when the row has no chain to filter on.
    const own = chainId === null ? holdings : holdings.filter((held) => held.chainId === chainId);
    return priceCoverage(own);
  }, [holdings, chainId]);

  // priceCoverage sums to 0 when nothing is priced, and 0 is a value claim.
  // "Nothing here could be valued" must render as a dash, never as $0.
  const navUsd = coverage.priced > 0 ? coverage.usd : null;

  // The summary row owns the held-token count; `coverage.total` only knows the
  // holdings actually handed to this card, which may be a page of them.
  const heldTokens = finite(row.tokens_held) ?? coverage.total;
  const unpriced = Math.max(0, heldTokens - coverage.priced);
  const pricedShare = heldTokens > 0 ? Math.min(1, coverage.priced / heldTokens) : 0;
  const coverageLabel = heldTokens > 0
    ? `${fmtNum(coverage.priced)} of ${fmtNum(heldTokens)} held tokens priced`
    : "No held tokens at this snapshot";

  const asOf = String(row.as_of ?? "").slice(0, 10);
  const age = snapshotAgeDays(row.as_of, now ?? Date.now());
  // Kept as a nullable age rather than a boolean: the badge states HOW stale,
  // and the revaluation note below needs the same number.
  const staleAge = age !== null && age > staleAfterDays ? age : null;
  const anchorBlock = finite(row.anchor_block);
  const anchorHash = String(row.anchor_hash ?? "").trim();

  const name = chainName(row.chain_id);
  const chainKey = String(row.chain_id ?? "").trim();
  // The numeric id is identity; suppressed only when the name already IS the id.
  const showChainId = chainKey !== "" && name !== `Chain ${chainKey}`;

  const provenance = priceProvenance(priceSource);
  let navCaption: string;
  if (navUsd === null) {
    navCaption = `Nothing on this chain could be priced. ${provenance}.`;
  } else if (unpriced > 0) {
    navCaption = `Priced holdings only — ${fmtNum(unpriced)} unpriced `
      + `${unpriced === 1 ? "token" : "tokens"} excluded, so the true total is higher. ${provenance}.`;
  } else {
    navCaption = `Every held token on this chain carries a price. ${provenance}.`;
  }
  // Spot quotes against a snapshot this old revalue a stale balance sheet at
  // today's prices — a constant-price revaluation, not a current valuation.
  if (navUsd !== null && staleAge !== null && priceSource?.kind === "spot") {
    navCaption += ` Balances are ${fmtNum(staleAge)} days older than these prices.`;
  }

  const stats: Array<{ label: string; value: string; ltd?: boolean }> = [
    { label: "GNO", value: fmtOrDash(finite(row.gno_units)) },
    { label: "GNO ex-Ltd.", value: fmtOrDash(finite(row.gno_units_ex_ltd)), ltd: true },
    { label: "Tokens", value: fmtOrDash(finite(row.tokens_held)) },
    { label: "Named", value: fmtOrDash(finite(row.tokens_named)) },
    { label: "Wallets", value: fmtOrDash(finite(row.wallets_tracked)) },
  ];

  return (
    <div className="gov-chain-card">
      <div className="gov-chain-card__head">
        <strong>{name}</strong>
        {showChainId && <span>chain {chainKey}</span>}
      </div>

      <div className={navUsd === null ? "gov-chain-card__nav gov-chain-card__nav--none" : "gov-chain-card__nav"}>
        {/* "≥", not a bare figure: the unpriced tail's value is unknowable, so
            the priced subtotal bounds the treasury from below and nothing more. */}
        {navUsd === null ? "—" : `≥ ${fmtUsd(navUsd)}`}
        <small>{navCaption}</small>
      </div>

      <div>
        {/* Counts, never value: the share of the treasury the unpriced tail
            carries is by definition unknown, so a value-weighted fill would be
            a fabricated number. The label below carries the reading. */}
        <div className="gov-coverage-meter" aria-hidden="true">
          <span style={{ width: `${pricedShare * 100}%` }} />
        </div>
        <p className="gov-caption">{coverageLabel}</p>
      </div>

      <dl className="gov-chain-card__stats">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className={stat.ltd ? "gov-chain-card__stat gov-chain-card__stat--ltd" : "gov-chain-card__stat"}
          >
            <dt>{stat.label}</dt>
            <dd>{stat.value}</dd>
          </div>
        ))}
      </dl>

      <div className="gov-chain-card__anchor">
        As of {asOf || "unknown"}
        {staleAge !== null && <> <span className="gov-stale-badge">STALE {fmtNum(staleAge)}d</span></>}
        {anchorBlock !== null && <> · block {fmtNum(anchorBlock)}</>}
        {/* The hash is what makes the figures re-derivable: it pins the block a
            reorg could otherwise have replaced. Full value in the title. */}
        {anchorHash !== "" && <> · <span title={anchorHash}>{shortAddr(anchorHash, 10, 6)}</span></>}
      </div>
    </div>
  );
}
