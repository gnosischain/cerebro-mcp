import type { TokenOverlay } from "../model/tokenOverlay";
import { TokenLabel } from "./TokenLabel";

// "token0 / token1" for a pair, or the whole asset list for an N-token
// Balancer pool. Assets are address-ascending (token0 < token1) — the order
// the indexer stores them in, never a "base/quote" guess.

export interface PairLabelProps {
  assets: string[];
  symbols: Array<string | null>;
  /** Chain-state fallbacks for assets the indexer never catalogued. */
  overlay?: TokenOverlay;
  onToken?: (address: string) => void;
}

export function PairLabel({ assets, symbols, overlay, onToken }: PairLabelProps) {
  if (assets.length === 0) return <span className="plx-pair plx-pair--empty">no assets</span>;
  return (
    <span className="plx-pair">
      {assets.map((address, index) => (
        <span key={address} className="plx-pair__item">
          {index > 0 && <span className="plx-pair__sep">/</span>}
          <TokenLabel address={address} symbol={symbols[index] ?? null} overlay={overlay} onClick={onToken} />
        </span>
      ))}
    </span>
  );
}
