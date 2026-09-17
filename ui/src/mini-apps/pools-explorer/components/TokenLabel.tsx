import { TokenIdentity, sanitizeSymbol } from "../../shared/TokenIdentity";
import { overlayLabelTitle, resolveTokenLabel, type TokenOverlay } from "../model/tokenOverlay";

// Token identity for the pools app. The ADDRESS is the identity; the symbol is
// untrusted display text (sanitized by TokenIdentity). A token whose metadata
// the indexer could not resolve renders as its short address WITH an
// "unresolved" badge — the badge is the disclosure that prices and amounts
// involving this token are in raw units.
//
// THREE-WAY PRECEDENCE: indexer symbol > chain-state overlay symbol > short
// address. The overlay never overwrites an indexer symbol, and a label it did
// supply carries its own marker (dotted underline + a `chain` chip) rather
// than the "unresolved" badge — those are different states. "Unresolved" means
// the verified snapshot has no name for this token; the chain marker means we
// do have a name, read live, with no publication behind it.

export interface TokenLabelProps {
  address: string;
  symbol?: string | null;
  /** Explicit resolution flag; defaults to "has a symbol". */
  resolved?: boolean | null;
  ambiguous?: boolean;
  /** Chain-state metadata keyed by lowercase address (view_state.token_overlay). */
  overlay?: TokenOverlay;
  onClick?: (address: string) => void;
}

export function TokenLabel({ address, symbol, resolved, ambiguous, overlay, onClick }: TokenLabelProps) {
  const clean = sanitizeSymbol(symbol);
  const label = resolveTokenLabel(address, symbol, overlay);
  const fromChain = label.source === "overlay";
  const isResolved = resolved ?? clean !== "";
  const body = (
    <span className={fromChain ? "plx-token plx-token--chain" : "plx-token"}>
      <TokenIdentity
        address={address}
        symbol={(fromChain ? label.text : clean) || undefined}
        ambiguous={ambiguous}
      />
      {fromChain ? (
        <span className="plx-chainmark" title={overlayLabelTitle(label.blockNumber)}>chain</span>
      ) : (
        !isResolved && (
          <span className="plx-badge plx-badge--unresolved" title="Token metadata (symbol / decimals) is not resolved — figures involving this token are raw units">
            unresolved
          </span>
        )
      )}
    </span>
  );
  if (!onClick) return body;
  return (
    <button type="button" className="plx-token-btn" onClick={() => onClick(address)} title={address}>
      {body}
    </button>
  );
}
