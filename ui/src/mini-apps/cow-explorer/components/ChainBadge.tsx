import { useState } from "react";
import { CHAIN_ICON_URLS, CHAIN_SHORT_NAMES } from "../model/chainIcons";

// 16px chain icon + optional name. Falls back to a deterministic-hue monogram
// when no icon exists (e.g. Sepolia) or the image fails to load.

function monogramHue(chainId: number): number {
  return (chainId * 137) % 360;
}

export function ChainBadge({ chainId, iconUrl, showName = true }: {
  chainId: number;
  /** Server-provided icon (chain_options[*].icon_url); registry fallback. */
  iconUrl?: string;
  showName?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const name = CHAIN_SHORT_NAMES[chainId] ?? String(chainId);
  const url = iconUrl || CHAIN_ICON_URLS[chainId] || "";
  return (
    <span className="cow-chain" title={`${name} (chain ${chainId})`}>
      {url && !failed ? (
        <img src={url} alt="" onError={() => setFailed(true)} />
      ) : (
        <span
          className="cow-chain__fallback"
          style={{ background: `hsl(${monogramHue(chainId)} 45% 32%)` }}
        >
          {name.slice(0, 1).toUpperCase()}
        </span>
      )}
      {showName && <span className="cow-chain__name">{name}</span>}
    </span>
  );
}
