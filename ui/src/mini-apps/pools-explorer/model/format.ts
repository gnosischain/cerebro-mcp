// Display formatting for the Pool Liquidity Explorer. Everything here is
// honest about units: liquidity is a raw L figure (never USD), prices are
// token1-raw per token0-raw unless BOTH decimals are known, amounts fall back
// to raw base units with a marker, and a null symbol is a short address.

import { sanitizeSymbol } from "../../shared/TokenIdentity";
import { shortAddr } from "../../../utils/format";
import { finite } from "../../shared/rowDataset";

export const DASH = "—";

const SI_SUFFIXES: Array<[number, string]> = [
  [1e24, "Y"], [1e21, "Z"], [1e18, "E"], [1e15, "P"], [1e12, "T"],
  [1e9, "B"], [1e6, "M"], [1e3, "k"],
];

/** Compact liquidity: SI suffix up to 1e24, exponential beyond. `null` (never
 * `0`) for a missing value — `Number(null) === 0` is how a "$0 NAV" shipped
 * once elsewhere in this repo. */
export function fmtLiquidity(value: unknown): string {
  const n = finite(value);
  if (n === null) return DASH;
  if (n === 0) return "0";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e27) return `${sign}${abs.toExponential(2)}`;
  for (const [threshold, suffix] of SI_SUFFIXES) {
    if (abs >= threshold) {
      const scaled = abs / threshold;
      return `${sign}${scaled.toFixed(scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2)}${suffix}`;
    }
  }
  return `${sign}${abs.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

/** Raw UInt256 strings (`*_raw`): shown compactly but flagged as raw units. */
export function fmtRaw(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  const text = String(value);
  const n = Number(text);
  if (!Number.isFinite(n)) return text;
  if (Math.abs(n) < 1e6) return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
  return n.toExponential(3);
}

/** Prices span many decades (1e-12 .. 1e12 in raw units). */
export function fmtPrice(value: unknown): string {
  const n = finite(value);
  if (n === null) return DASH;
  if (n === 0) return "0";
  const abs = Math.abs(n);
  if (abs < 1e-4 || abs >= 1e9) return n.toExponential(3);
  return n.toLocaleString("en-US", { maximumSignificantDigits: 6 });
}

export interface PriceText {
  text: string;
  /** True when the figure is in raw units (token1-raw per token0-raw). */
  raw: boolean;
  /** True when the adjustment used decimals read from chain state rather than
   * a verified indexer snapshot. Never true together with `raw`. */
  chain?: boolean;
  /** The numeric figure `text` renders, so a caller that needs to invert the
   * orientation does not have to parse formatted text back. */
  value?: number | null;
}

/** Decimals-adjusted price only when BOTH decimals are known AND the server
 * supplied an adjusted figure; otherwise the raw ratio, flagged. Decimals are
 * never fabricated client-side. */
export function fmtPriceFor(
  raw: unknown,
  adjusted: unknown,
  dec0: unknown,
  dec1: unknown,
): PriceText {
  const adj = finite(adjusted);
  if (adj !== null && finite(dec0) !== null && finite(dec1) !== null) {
    return { text: fmtPrice(adj), raw: false };
  }
  return { text: fmtPrice(raw), raw: true };
}

/**
 * fmtPriceFor with a third state: adjusted from CHAIN-STATE decimals.
 *
 * The server only ships `price_adjusted` when it knows BOTH decimals, so a
 * pool with one unknown side has no adjusted figure at all. When the overlay
 * supplies the missing side(s) the ratio can be scaled client-side —
 * `price_raw × 10^(d0 − d1)`, the same rule the server applies — but the
 * result is flagged, because neither the decimals nor the product is
 * publication-verified. The indexer path is tried first and is never
 * overwritten.
 */
export function fmtPriceWithOverlay(
  raw: unknown,
  adjusted: unknown,
  dec0: unknown,
  dec1: unknown,
  overlayDec0: unknown,
  overlayDec1: unknown,
): PriceText {
  const indexer = fmtPriceFor(raw, adjusted, dec0, dec1);
  // `value` is attached HERE, not on fmtPriceFor: that function's two-field
  // return is pinned by its own test and used everywhere else.
  if (!indexer.raw) return { ...indexer, value: finite(adjusted) };
  const known0 = finite(dec0);
  const known1 = finite(dec1);
  const d0 = known0 ?? finite(overlayDec0);
  const d1 = known1 ?? finite(overlayDec1);
  // At least one side must have come from the overlay, or this is an
  // indexer-only figure that simply had no server-computed twin — marking it
  // "from chain state" would be a lie in the other direction.
  const usedOverlay = (known0 === null && d0 !== null) || (known1 === null && d1 !== null);
  const ratio = finite(raw);
  if (!usedOverlay || d0 === null || d1 === null || ratio === null) {
    return { ...indexer, value: ratio };
  }
  const scaled = ratio * 10 ** (d0 - d1);
  return { text: fmtPrice(scaled), raw: false, chain: true, value: scaled };
}

export interface AmountText {
  text: string;
  /** True when decimals are unknown and the figure is raw base units. */
  rawUnits: boolean;
  /** True when the scaling decimals came from the chain-state overlay rather
   * than a verified indexer snapshot. Never true together with `rawUnits`. */
  chain?: boolean;
}

/** Token amount: units when decimals are known, raw base units otherwise. */
export function fmtAmount(raw: unknown, decimals: unknown, units?: unknown): AmountText {
  const dec = finite(decimals);
  const unitValue = finite(units);
  if (dec !== null && unitValue !== null) {
    return { text: fmtUnits(unitValue), rawUnits: false };
  }
  if (dec !== null && raw !== null && raw !== undefined && raw !== "") {
    const n = Number(raw);
    if (Number.isFinite(n)) return { text: fmtUnits(n / 10 ** dec), rawUnits: false };
  }
  return { text: fmtRaw(raw), rawUnits: true };
}

/**
 * fmtAmount with a third state: scaled by decimals read from CHAIN STATE.
 *
 * The indexer path is tried first and unchanged — an overlay value may only
 * fill a hole, never overwrite a verified one — and the result is flagged so
 * the caller marks it rather than letting it read as a verified figure.
 */
export function fmtAmountWithOverlay(
  raw: unknown,
  decimals: unknown,
  units: unknown,
  overlayDecimals: unknown,
): AmountText {
  const indexer = fmtAmount(raw, decimals, units);
  if (!indexer.rawUnits) return indexer;
  const dec = finite(overlayDecimals);
  if (dec === null || raw === null || raw === undefined || raw === "") return indexer;
  const n = Number(raw);
  if (!Number.isFinite(n)) return indexer;
  return { text: fmtUnits(n / 10 ** dec), rawUnits: false, chain: true };
}

export function fmtUnits(value: unknown): string {
  const n = finite(value);
  if (n === null) return DASH;
  const abs = Math.abs(n);
  if (abs !== 0 && abs < 1e-4) return n.toExponential(2);
  if (abs >= 1e15) return n.toExponential(3);
  return n.toLocaleString("en-US", { maximumFractionDigits: abs >= 1000 ? 0 : abs >= 1 ? 2 : 4 });
}

/** Fee in pips -> percent. Algebra pools carry a dynamic fee ("dyn" marker);
 * reserves-only pools have no fee here. */
export function fmtFee(fee: unknown, poolClass?: string | null): string {
  const n = finite(fee);
  if (n === null) {
    if (poolClass === "swapr_v3_algebra") return "dyn";
    return DASH;
  }
  const pct = n / 10_000;
  const text = `${pct.toFixed(pct >= 1 ? 2 : pct >= 0.01 ? 2 : 3)}%`;
  return poolClass === "swapr_v3_algebra" ? `${text} dyn` : text;
}

/** Symbol (sanitized — untrusted text) or a short address. */
export function tokenLabel(symbol: unknown, address: unknown): string {
  const clean = sanitizeSymbol(symbol);
  if (clean) return clean;
  return shortAddr(String(address ?? "")) || DASH;
}

export function fmtInt(value: unknown): string {
  const n = finite(value);
  if (n === null) return DASH;
  return Math.round(n).toLocaleString("en-US");
}

/** Fraction (0..1) -> percent text. */
export function fmtPct(value: unknown, digits = 1): string {
  const n = finite(value);
  if (n === null) return DASH;
  return `${(n * 100).toFixed(digits)}%`;
}

/** Signed percent offset (already in percent units). */
export function fmtSignedPct(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return DASH;
  const rounded = Math.abs(value) < 10 ** -digits / 2 ? 0 : value;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(digits)}%`;
}

export function fmtTick(value: unknown): string {
  const n = finite(value);
  if (n === null) return DASH;
  return Math.round(n).toLocaleString("en-US");
}

/** YYYY-MM-DD from a date / datetime string; passthrough otherwise. */
export function fmtDate(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  const text = String(value);
  const match = text.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : text;
}

export function fmtTime(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  const text = String(value);
  const match = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/);
  return match ? `${match[1]} ${match[2]}` : text;
}

export function fmtBool(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  return value === true || value === 1 || value === "1" || value === "true" ? "Yes" : "No";
}

export function truthy(value: unknown): boolean {
  return value === true || value === 1 || value === "1" || value === "true";
}

export const CLASS_LABELS: Record<string, string> = {
  uniswap_v3: "Uniswap v3",
  swapr_v3_algebra: "Swapr v3 (Algebra)",
  balancer_v2: "Balancer v2",
  balancer_v3: "Balancer v3",
};

export function classLabel(poolClass: unknown): string {
  const key = String(poolClass ?? "");
  return CLASS_LABELS[key] ?? (key || DASH);
}

export const FAMILY_LABELS: Record<string, string> = {
  cl: "Concentrated",
  reserves_only: "Reserves only",
};

export function familyLabel(family: unknown): string {
  const key = String(family ?? "");
  return FAMILY_LABELS[key] ?? (key || DASH);
}
