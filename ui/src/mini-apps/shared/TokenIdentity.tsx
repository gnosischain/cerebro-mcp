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

/** Everything attacker-authored display text can never legitimately need, in
 * ONE unicode-aware class so no range can be forgotten on one code path:
 *
 *   \p{Cc} \p{Cf}   C0/C1 controls, and format characters: the bidi overrides
 *                   (U+202A-202E, U+2066-2069) and zero-widths used to disguise
 *                   one string as another;
 *   \p{Co} \p{Cs}   private-use glyphs and lone surrogates (unrenderable);
 *   \p{Default_Ignorable_Code_Point}
 *                   invisible-by-definition characters that are NOT all Cf.
 *                   U+034F COMBINING GRAPHEME JOINER is how the live treasury's
 *                   "U+034F-USDC" spoof passed as USDC; VS16 (U+FE0F) and the
 *                   Hangul fillers ride here too;
 *   \p{Mn} \p{Me}   combining marks, stripped AFTER NFC so a precomposed "é"
 *                   survives while a stack of marks does not;
 *   U+2800 U+FFFC U+FFFD
 *                   the braille blank, object-replacement and replacement
 *                   characters, which render as nothing or as boxes;
 *   < >             so a symbol can never open markup in string-built HTML
 *                   (chart tooltips, the ChartCard data view).
 *
 * An emoji's base character is kept: stripping VS16 leaves the sword of a
 * "crossed swords + VS16" symbol. */
const UNPRINTABLE_RE =
  /[\p{Cc}\p{Cf}\p{Co}\p{Cs}\p{Default_Ignorable_Code_Point}\p{Mn}\p{Me}\u{2800}\u{FFFC}\u{FFFD}<>]/gu;

/** Sanitize untrusted display text (token symbols and names, wallet labels):
 * NFC-normalize, strip the unprintable set above, collapse whitespace, then cap
 * at `max` CODE POINTS with an ellipsis. Truncation counts code points, never
 * UTF-16 units, so an emoji's surrogate pair is never split into a lone half.
 * Returns "" when nothing legible survives: the caller renders the unnamed
 * state (a short address), never a placeholder that could pass as a name. */
export function sanitizeText(raw: unknown, max: number): string {
  if (raw === null || raw === undefined) return "";
  const text = String(raw);
  if (!text) return "";
  const cleaned = text
    .normalize("NFC")
    .replace(UNPRINTABLE_RE, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return "";
  const cap = Number.isFinite(max) && max >= 1 ? Math.floor(max) : MAX_SYMBOL_LENGTH;
  const points = Array.from(cleaned);
  if (points.length <= cap) return cleaned;
  return `${points.slice(0, Math.max(0, cap - 1)).join("").trimEnd()}…`;
}

/** A ticker: `sanitizeText` capped at MAX_SYMBOL_LENGTH. */
export function sanitizeSymbol(raw: unknown): string {
  return sanitizeText(raw, MAX_SYMBOL_LENGTH);
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
  // Code points, not UTF-16 units: an emoji symbol must not render half a
  // surrogate pair as its monogram.
  const glyph = Array.from(clean || address.slice(2, 4) || "??").slice(0, 2).join("").toUpperCase();
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
