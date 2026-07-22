// Display fallback rules for token identity and amounts (v2).
//
// The backend guarantees (tested invariant) that every token-bearing dataset
// projects `<x>_symbol` next to `<x>_token`. These helpers centralize what to
// SHOW when the metadata is missing or suspect:
//   symbol -> native symbol (for the native pseudo-token) -> short address.
//   amounts: normalized when decimals are known; raw + hint otherwise.
//   decimals === 0 is treated as unknown-SUSPECT (the indexer stores a failed
//   decimals() getter as 0, indistinguishable from a real 0-decimals token).

export const NATIVE_TOKEN = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";

export function shortAddr(address: string): string {
  const value = String(address || "");
  return value.length >= 12 ? `${value.slice(0, 6)}…${value.slice(-4)}` : value;
}

export function displayToken(
  address: string,
  symbol: string | null | undefined,
  nativeSymbol = "",
): string {
  const clean = String(symbol ?? "").trim();
  if (clean) return clean;
  const addr = String(address || "").toLowerCase();
  if (addr === NATIVE_TOKEN && nativeSymbol) return nativeSymbol;
  return shortAddr(addr);
}

export interface AmountDisplay {
  text: string;
  /** True when the value is raw base units because decimals are unknown. */
  rawUnits: boolean;
  /** True when decimals === 0 (structurally ambiguous in cow_db). */
  suspect: boolean;
}

export function displayAmount(
  raw: string | number | null | undefined,
  normalized: number | null | undefined,
  decimals: number | null | undefined,
  maximumFractionDigits = 4,
): AmountDisplay {
  if (decimals !== null && decimals !== undefined && normalized !== null && normalized !== undefined) {
    return {
      text: Number(normalized).toLocaleString(undefined, { maximumFractionDigits }),
      rawUnits: false,
      suspect: Number(decimals) === 0,
    };
  }
  const rawText = raw === null || raw === undefined ? "" : String(raw);
  return { text: rawText, rawUnits: rawText !== "", suspect: false };
}
