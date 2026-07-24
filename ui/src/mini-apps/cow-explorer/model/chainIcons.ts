// Static chain-icon registry (CoinGecko asset-platform images), mirroring the
// backend `COINGECKO_NATIVE_ICON_URLS` map so the dev fixture and any state
// missing `chain_options[*].icon_url` still render chain badges. The server
// payload's icon_url wins when present.

export const CHAIN_ICON_URLS: Record<number, string> = {
  1: "https://coin-images.coingecko.com/asset_platforms/images/279/thumb/ethereum.png?1706606803",
  56: "https://coin-images.coingecko.com/asset_platforms/images/1/thumb/bnb_smart_chain.png?1706606721",
  100: "https://coin-images.coingecko.com/asset_platforms/images/11062/thumb/Aatar_green_white.png?1706606458",
  137: "https://coin-images.coingecko.com/asset_platforms/images/15/thumb/polygon_pos.png?1706606645",
  8453: "https://coin-images.coingecko.com/asset_platforms/images/131/thumb/base.png?1759905869",
  9745: "https://coin-images.coingecko.com/asset_platforms/images/32256/thumb/plasma.jpg?1758000963",
  42161: "https://coin-images.coingecko.com/asset_platforms/images/33/thumb/AO_logomark.png?1706606717",
  43114: "https://coin-images.coingecko.com/asset_platforms/images/12/thumb/avalanche.png?1706606775",
  57073: "https://coin-images.coingecko.com/asset_platforms/images/22194/thumb/ink.jpg?1737600222",
  59144: "https://coin-images.coingecko.com/asset_platforms/images/135/thumb/linea.jpeg?1706606705",
};

export const CHAIN_SHORT_NAMES: Record<number, string> = {
  1: "Ethereum",
  56: "BNB",
  100: "Gnosis",
  137: "Polygon",
  8453: "Base",
  9745: "Plasma",
  42161: "Arbitrum",
  43114: "Avalanche",
  57073: "Ink",
  59144: "Linea",
  11155111: "Sepolia",
};

/** Stable per-chain series hues so chain-keyed charts match everywhere
 * (share trend, live heartbeat, pies, treemaps). Mid-lightness picks chosen
 * to stay distinguishable on BOTH the dark (#12161c) and light (white) card
 * surfaces — no pair differs only in lightness. Sepolia is deliberately a
 * neutral grey (testnet). */
export const CHAIN_SERIES_COLORS: Record<number, string> = {
  1: "#7B9CE1", // Ethereum — periwinkle blue
  56: "#F5B14C", // BNB — amber
  100: "#34d399", // Gnosis — green
  137: "#A78BFA", // Polygon — violet
  8453: "#5B7FE8", // Base — deeper cobalt (kept apart from Ethereum's lighter blue)
  9745: "#FDBA74", // Plasma — light orange (warmer + lighter than BNB)
  42161: "#67e8f9", // Arbitrum — cyan
  43114: "#FF7A9C", // Avalanche — pink-red
  57073: "#C6A6FF", // Ink — lavender (lighter than Polygon's violet)
  59144: "#94a3b8", // Linea — slate
  11155111: "#9ca3af", // Sepolia — neutral grey (testnet)
};

/** Series color for a chain id; deterministic fallback for unknown chains. */
export function chainSeriesColor(chainId: number): string {
  return CHAIN_SERIES_COLORS[chainId] ?? "#8b9db0";
}
