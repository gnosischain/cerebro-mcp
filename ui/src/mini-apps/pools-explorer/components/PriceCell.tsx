import { fmtPriceWithOverlay } from "../model/format";
import { overlayAmountTitle, resolveDecimals, type TokenOverlay } from "../model/tokenOverlay";

// A price is token1-raw per token0-raw unless BOTH decimals are known. The raw
// marker is the disclosure; the title says which unit you are looking at.
//
// Three states, not two:
//   1. adjusted    — the server's decimals-adjusted figure (publication-verified)
//   2. chain       — adjusted client-side because the chain-state overlay
//                    supplied a decimals the indexer never catalogued
//   3. raw         — no decimals anywhere; token1-raw per token0-raw
// State 2 is marked so it can never read as state 1.

export interface PriceCellProps {
  raw: unknown;
  adjusted: unknown;
  dec0: unknown;
  dec1: unknown;
  /** Show the inverted orientation (token0 per token1). */
  inverted?: boolean;
  /** Chain-state metadata + the pair's addresses, so a missing decimals can be
   * filled from the overlay. Omit either address to keep the existing rule. */
  overlay?: TokenOverlay;
  token0?: unknown;
  token1?: unknown;
}

export function PriceCell({ raw, adjusted, dec0, dec1, inverted, overlay, token0, token1 }: PriceCellProps) {
  const from0 = resolveDecimals(dec0, token0, overlay);
  const from1 = resolveDecimals(dec1, token1, overlay);
  const price = fmtPriceWithOverlay(
    raw, adjusted, dec0, dec1,
    from0.source === "overlay" ? from0.decimals : null,
    from1.source === "overlay" ? from1.decimals : null,
  );
  const block = from0.source === "overlay" ? from0.blockNumber : from1.blockNumber;
  let text = price.text;
  if (inverted) {
    // Invert the figure ACTUALLY on screen (`value`), so the flip stays in the
    // same unit system and keeps the same marker.
    const value = price.value ?? null;
    text = value !== null && Number.isFinite(value) && value > 0
      ? fmtPriceWithOverlay(1 / value, 1 / value, 0, 0, null, null).text
      : "—";
  }
  const className = price.raw
    ? "plx-price plx-price--raw"
    : price.chain
      ? "plx-price plx-price--chain"
      : "plx-price";
  return (
    <span
      className={className}
      title={price.raw
        ? "Raw units: token1-raw per token0-raw — token decimals are not resolved"
        : price.chain
          ? overlayAmountTitle(block)
          : inverted ? "token0 per token1 (decimals-adjusted)" : "token1 per token0 (decimals-adjusted)"}
    >
      {text}
      {price.raw && <sup>raw</sup>}
      {price.chain && <sup>chain</sup>}
    </span>
  );
}
