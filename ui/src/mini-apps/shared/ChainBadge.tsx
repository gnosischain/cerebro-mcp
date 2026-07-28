import { useState } from "react";
import { CHAIN_ICON_URLS, chainShortName } from "./chainIcons";

// 16px chain icon + optional name. Falls back to a deterministic-hue monogram
// when no icon exists (e.g. Sepolia, Celo) or the image fails to load.
// Styled by `.ma-chain*` in shared/mini-apps.css, which every mini-app entry
// point imports.

function monogramHue(chainId: number): number {
  return (chainId * 137) % 360;
}

export function ChainBadge({
  chainId,
  iconUrl,
  showName = true,
}: {
  chainId: number;
  /** Server-provided icon (chain_options[*].icon_url); registry is fallback. */
  iconUrl?: string;
  showName?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const name = chainShortName(chainId);
  const url = iconUrl || CHAIN_ICON_URLS[chainId] || "";
  return (
    <span className="ma-chain" title={`${name} (chain ${chainId})`}>
      {url && !failed ? (
        <img src={url} alt="" onError={() => setFailed(true)} />
      ) : (
        <span
          className="ma-chain__fallback"
          style={{ background: `hsl(${monogramHue(chainId)} 45% 32%)` }}
        >
          {name.slice(0, 1).toUpperCase()}
        </span>
      )}
      {showName && <span className="ma-chain__name">{name}</span>}
    </span>
  );
}
