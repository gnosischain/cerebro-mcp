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
