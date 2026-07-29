import { useState } from "react";

import { shortAddr } from "../../utils/format";

// Token identity chip. Promoted out of cow-explorer when the governance
// Treasury tab needed it — same move ChainBadge made when Contract Explorer
// arrived (see the note in cow-explorer.css).
//
// The address is the identity; the SYMBOL IS UNTRUSTED DISPLAY TEXT. Token
// metadata is attacker-authored and this is not hypothetical: the GnosisDAO
// treasury holds 19 distinct tokens claiming the symbol "USDC", plus tokens
// whose names are phishing lures ("Visit [aave-sr.xyz] and claim special
// rewards"). So symbols are sanitized and length-capped here, never linkified,
// and callers disambiguate collisions by showing the address alongside.

/** Max rendered symbol length. Real tickers are short; anything longer is a
 * lure trying to smuggle a sentence into the table. */
export const MAX_SYMBOL_LENGTH = 14;

/** Strip what a ticker can never legitimately contain: C0/C1 controls, the
 * bidi overrides used to disguise text, and zero-width joiners. Collapse
 * whitespace, then cap. Returns "" when nothing legible survives, which the
 * caller renders as the unnamed state. */
export function sanitizeSymbol(raw: unknown): string {
  const text = String(raw ?? "");
  if (!text) return "";
  const cleaned = text
    // C0/C1 control characters.
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, "")
    // Zero-width and bidi-override ranges — the standard way to disguise
    // one string as another (U+200B-200F, U+202A-202E, U+2066-2069, U+FEFF).
    .replace(/[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return "";
  return cleaned.length > MAX_SYMBOL_LENGTH
    ? `${cleaned.slice(0, MAX_SYMBOL_LENGTH - 1)}…`
    : cleaned;
}

/** Deterministic monogram hue, mirroring ChainBadge's rule. Applied ONLY where
 * a symbol exists: colour must never appear where identity is unknown. */
export function monogramHue(seed: string): number {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) % 360;
  }
  return hash;
}

export interface TokenIdentityProps {
  address: string;
  /** Resolved logo URL, or empty when none is known. Never a placeholder. */
  iconUrl?: string;
  symbol?: string;
  /** Render the icon only — for dense grids whose label lives in a sibling cell. */
  labelless?: boolean;
  /** Show the short address next to the symbol. Callers set this when the
   * symbol is NOT unique in view, so a spoofed "USDC" can never be mistaken
   * for the real one. */
  ambiguous?: boolean;
}

export function TokenIdentity({
  address,
  iconUrl,
  symbol,
  labelless,
  ambiguous,
}: TokenIdentityProps) {
  const [failedUrl, setFailedUrl] = useState("");
  const showImage = Boolean(iconUrl && iconUrl !== failedUrl);
  const clean = sanitizeSymbol(symbol);
  const named = clean !== "";
  const glyph = (clean || address.slice(2, 4) || "??").slice(0, 2).toUpperCase();
  return (
    <span className="ma-token" title={named ? `${clean} — ${address}` : address}>
      {showImage ? (
        <img
          src={iconUrl}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailedUrl(iconUrl!)}
        />
      ) : (
        <span
          className={named ? "ma-token__fallback" : "ma-token__fallback ma-token__fallback--raw"}
          style={named ? { background: `hsl(${monogramHue(clean)} 45% 32%)`, color: "#fff" } : undefined}
          aria-hidden="true"
        >
          {glyph}
        </span>
      )}
      {!labelless && (
        <span className={named ? "ma-token__label" : "ma-token__label ma-token__label--raw"}>
          {clean || shortAddr(address)}
          {named && ambiguous && (
            <span className="ma-token__disambig" title={address}>{shortAddr(address)}</span>
          )}
        </span>
      )}
    </span>
  );
}
