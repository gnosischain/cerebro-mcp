import { fmtLiquidity } from "../model/format";

/** Liquidity (L) — a raw Uniswap-style figure, never USD. The exact raw
 * integer rides in the title. */
export function LiquidityCell({ value, raw }: { value: unknown; raw?: unknown }) {
  const text = fmtLiquidity(value);
  return (
    <span className={`plx-liq${text === "—" ? " plx-liq--empty" : ""}`} title={raw === null || raw === undefined ? undefined : `L = ${String(raw)}`}>
      {text}
    </span>
  );
}
