// Dev-only treasury fixture (imported by devFixture.ts). MIRRORS the SQL
// contract in model/treasuryColumns.json — the same column names in the same
// order — for these query files:
//
//   queries/governance/treasury_summary.sql          -> treasury_summary
//   queries/governance/treasury_holdings.sql         -> treasury_holdings
//   queries/governance/treasury_by_wallet.sql        -> treasury_by_wallet
//   queries/governance/treasury_history.sql          -> treasury_history (grain fan-out)
//   queries/governance/treasury_history_coverage.sql -> treasury_history_coverage,
//                                                       treasury_wallet_months,
//                                                       treasury_token_months (chain-pinned)
//   queries/governance/wallet_detail.sql / wallet_positions.sql /
//     wallet_series.sql / wallet_chains.sql          -> treasury_wallet_*
//   queries/governance/token_detail.sql / token_holders.sql / holder_series.sql /
//     token_price_history.sql                         -> treasury_token_*
//
// Every dataset is DERIVED from one position model below (wallet x chain x
// token x month), so the totals agree by construction: summary NAV = sum of
// holdings value = chain-grain NAV at the as-of month = sum of the token grain
// = sum of the wallet grain. A fixture that hand-typed each dataset would drift
// and make a real bug look like fixture noise.
//
// The SQL semantics it mirrors, each of which the UI depends on:
//   * a registry-priced token is class 'listed' in any month the hub has no
//     price for it (so the token grain carries PRICED months only);
//   * 'hub_proxy' = priced through a pegged asset's hub series (stETH/WETH);
//   * spot_eligible = 1 only for 'listed' tokens with known decimals;
//   * coverage runs from each chain's first month: 'gap' / 'unpublished'
//     months have no history rows, a 'partial' month has rows for what WAS
//     served, and names the unserved registry symbols;
//   * treasury_by_wallet has every LABELLED wallet on every chain (empty ones
//     with zeros), plus unlabelled wallets where they hold something (those
//     come from the FULL JOIN's data side and take their chain's as_of from
//     their own rows); label_source is the same constant on every row,
//     labelled or not;
//   * treasury_wallet_detail is ONE row for any address on a served chain —
//     zeros where it holds nothing, even outside the census;
//   * treasury_token_detail is EMPTY for a token not held on the as-of day.
//
// Shape (magnitudes near the live 2026-09-24 figures): Ethereum from 2020-11
// (71 months, hub NAV ~$115.6M, ex-Ltd ~$74.5M), Gnosis Chain from 2020-07
// (75 months, ~$128.3M). Every token class and spam reason; the Gnosis Ltd.
// wallet on both chains; 0x458c...5e6f on both chains (Ethereum holdings start
// 2026-07, a 'partial' month, then 08 and 09; Gnosis Chain since 2020-07); an
// unlabelled wallet that exists only on Gnosis Chain; blanked months (Gnosis
// Chain 2025-02 'gap', Ethereum 2023-02 'unpublished'); hub prices that start
// mid-history (COW 2022-03, SAFE 2024-04, osGNO 2024-10); a token sold before
// the as-of day (BAL).
//
// Wallet LABELS here are placeholders, not the real community list.

import type { DatasetDescriptor } from "../shared/miniAppTypes";

type ChainId = 1 | 100;
type TokenClass = "priced" | "listed" | "unverified" | "spam" | "retired_mirror";
type SpamReason = "" | "impersonation" | "lure" | "obfuscated" | "malformed" | "mass_airdrop";

export const FIXTURE_AS_OF = "2026-09-24";
export const FIXTURE_SPOT_AT = "2026-09-25T08:00:00Z";
const LABEL_SOURCE = "koeppelmann/GnosisDAO_treasury README (community list)";
const ANCHOR_BLOCK: Record<ChainId, number> = { 1: 23431775, 100: 42318904 };

/** Month index 0 = 2020-07; 74 = 2026-09 (the as-of month). */
const LAST_MONTH = 74;
/** First month each chain was indexed. */
const CHAIN_START: Record<ChainId, number> = { 1: 4, 100: 0 };
/** Months with nothing served (no history rows at all): Gnosis Chain 2025-02
 * ('gap'), Ethereum 2023-02 ('unpublished'). */
const BLANK_MONTHS: Record<ChainId, Map<number, "gap" | "unpublished">> = {
  1: new Map([[31, "unpublished"]]),
  100: new Map([[55, "gap"]]),
};

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

function yearMonth(mi: number): { year: number; month: number } {
  const abs = mi + 6;
  return { year: 2020 + Math.floor(abs / 12), month: (abs % 12) + 1 };
}

function monthBucket(mi: number): string {
  const { year, month } = yearMonth(mi);
  return `${year}-${pad2(month)}-01`;
}

/** The raw day each month-end resolves to: the last calendar day, except the
 * current month, which is the as-of day. */
function monthEnd(mi: number): string {
  if (mi === LAST_MONTH) return FIXTURE_AS_OF;
  const { year, month } = yearMonth(mi);
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${year}-${pad2(month)}-${pad2(days)}`;
}

const round2 = (value: number) => Math.round(value * 100) / 100;
const round4 = (value: number) => Math.round(value * 10000) / 10000;

// ---------------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------------

interface FixtureToken {
  chainId: ChainId;
  token: string;
  symbol: string | null;
  name: string | null;
  registrySymbol: string;
  assetKey: string;
  assetClass: string;
  decimals: number | null;
  metadataStatus: "resolved" | "failed";
  /** Registry role (or the classifier's verdict) — the class in any month the
   * hub HAS a price; a 'priced' role with no price that month reads 'listed'. */
  role: TokenClass;
  spamReason: SpamReason;
  /** Role in a given month (EURe v1 was the real EURe before 2024-08-25). */
  roleAt?: (mi: number) => TokenClass;
  /** dbt price hub USD for the month-end, or null before the hub has a price. */
  hubPrice?: (mi: number) => number | null;
  /** Hub series the registry prices it with ("WETH" for stETH). */
  priceSymbol?: string;
  /** Priced through a pegged asset's hub series. */
  proxy?: boolean;
  supply?: number;
}

const gnoPrice = (mi: number) => round2(140 + 110 * Math.sin((mi - 4) / 7) + 0.9 * mi);
const ethPrice = (mi: number) => round2(2200 + 1200 * Math.sin((mi - 10) / 9));
const cowPrice = (mi: number) => (mi < 20 ? null : round4(0.45 + 0.2 * Math.sin(mi / 5)));
const eurePrice = (mi: number) => round4(1.08 + 0.03 * Math.sin(mi / 8));

export const T = {
  GNO_1: "0x6810e776880c02933d47db1b9fc05908e5386b96",
  USDC_1: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
  WETH_1: "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
  COW_1: "0xdef1ca1fb7fbcdc777520aa7f396b4e015f497ab",
  SAFE_1: "0x5afe3855358e112b5647b952709e6165e1c1eeee",
  STETH_1: "0xae7ab96520de3a18e5e111b5eaab095312d7fe84",
  BAL_1: "0xba100000625a3754423978a60c9317c58a424e3d",
  LDO_1: "0x5a98fcbea516cf06857215779fd812ca3bef1b32",
  GRT_1: "0xc944e90c64b2c07662a292be6244bdf05cda44a7",
  RAID_1: "0x154e35c2b0024b3e079c5c5e4fc31c979c189cce",
  RAW_1: "0x2ec109a0cefec70661a242a8b54cae8f45630397",
  FAKE_USDC_1: "0x3a5906b5d3b1a0a3c0f0b9a9e8d7c6b5a4f3e2d1",
  LURE_1: "0x10ce5a6e1d6f1b0c9e8d7a6b5c4d3e2f1a0b9c8d",
  OBF_1: "0x0bf5c0de5d1e4a3b2c1d0e9f8a7b6c5d4e3f2a1b",
  DROP_1: "0xd70b0d70b0d70b0d70b0d70b0d70b0d70b0d70b0",
  GNO_100: "0x9c58bacc331c9aa871afd802db6379a98e80cedb",
  WXDAI_100: "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d",
  SDAI_100: "0xaf204776c7245bf4147c2612bf6e5972ee483701",
  USDCE_100: "0x2a22f9c3b484c3629090feed35f17ff8f88f76f0",
  EURE_100: "0x420ca0f9b9b604ce0fd9c18ef134c705e5fa3430",
  EURE_V1_100: "0xcb444e90d8198415266c6a2724b7900fb12fc56e",
  OSGNO_100: "0xf490c80aae5f2616d3e3bda2483e30c4cb21d1a0",
  COW_100: "0x177127622c4a00f3d409b75571e12cb3c8973d3c",
  XBZZ_100: "0xdbf3ea6f5bee45c02255b2c26a16f300502f68da",
  MALFORMED_100: "0x3a1f0e0d0c0b0a09080706050403020100ff00ff",
} as const;

const TOKENS: FixtureToken[] = [
  // Ethereum — reviewed registry, hub-priced.
  { chainId: 1, token: T.GNO_1, symbol: "GNO", name: "Gnosis Token", registrySymbol: "GNO", assetKey: "GNO", assetClass: "GNO", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: gnoPrice, priceSymbol: "GNO", supply: 3_000_000 },
  { chainId: 1, token: T.USDC_1, symbol: "USDC", name: "USD Coin", registrySymbol: "USDC", assetKey: "USDC", assetClass: "Stablecoins", decimals: 6, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: () => 1, priceSymbol: "USDC", supply: 30_000_000_000 },
  { chainId: 1, token: T.WETH_1, symbol: "WETH", name: "Wrapped Ether", registrySymbol: "WETH", assetKey: "ETH", assetClass: "ETH", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: ethPrice, priceSymbol: "WETH", supply: 3_000_000 },
  // stETH has no hub series of its own: priced with WETH's (1:1 staking peg).
  { chainId: 1, token: T.STETH_1, symbol: "stETH", name: "Liquid staked Ether 2.0", registrySymbol: "stETH", assetKey: "stETH", assetClass: "ETH", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: ethPrice, priceSymbol: "WETH", proxy: true, supply: 9_000_000 },
  { chainId: 1, token: T.COW_1, symbol: "COW", name: "CoW Protocol Token", registrySymbol: "COW", assetKey: "COW", assetClass: "Other", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: cowPrice, priceSymbol: "COW", supply: 1_000_000_000 },
  { chainId: 1, token: T.SAFE_1, symbol: "SAFE", name: "Safe Token", registrySymbol: "SAFE", assetKey: "SAFE", assetClass: "Other", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: (mi) => (mi < 45 ? null : round4(0.8 + 0.35 * Math.sin(mi / 4))), priceSymbol: "SAFE", supply: 1_000_000_000 },
  { chainId: 1, token: T.BAL_1, symbol: "BAL", name: "Balancer", registrySymbol: "BAL", assetKey: "BAL", assetClass: "Other", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: (mi) => round4(8 - 0.08 * mi), priceSymbol: "BAL", supply: 100_000_000 },
  // Ethereum — reviewed but outside the hub: CoinGecko spot fallback only.
  { chainId: 1, token: T.LDO_1, symbol: "LDO", name: "Lido DAO Token", registrySymbol: "LDO", assetKey: "LDO", assetClass: "Other", decimals: 18, metadataStatus: "resolved", role: "listed", spamReason: "" },
  { chainId: 1, token: T.GRT_1, symbol: "GRT", name: "Graph Token", registrySymbol: "GRT", assetKey: "GRT", assetClass: "Other", decimals: 18, metadataStatus: "resolved", role: "listed", spamReason: "" },
  // Ethereum — outside the registry: never valued, not even at spot.
  { chainId: 1, token: T.RAID_1, symbol: "RAID \u{2694}\u{FE0F}", name: "Raid Guild", registrySymbol: "", assetKey: "", assetClass: "", decimals: 18, metadataStatus: "resolved", role: "unverified", spamReason: "" },
  { chainId: 1, token: T.RAW_1, symbol: null, name: null, registrySymbol: "", assetKey: "", assetClass: "", decimals: null, metadataStatus: "failed", role: "unverified", spamReason: "" },
  // Ethereum — spam, hidden by default and never valued.
  { chainId: 1, token: T.FAKE_USDC_1, symbol: "USDC", name: "USD Coin", registrySymbol: "", assetKey: "", assetClass: "", decimals: 6, metadataStatus: "resolved", role: "spam", spamReason: "impersonation", supply: 1_000_000 },
  { chainId: 1, token: T.LURE_1, symbol: "aave-sr.xyz", name: "Visit aave-sr.xyz to claim", registrySymbol: "", assetKey: "", assetClass: "", decimals: 18, metadataStatus: "resolved", role: "spam", spamReason: "lure" },
  { chainId: 1, token: T.OBF_1, symbol: "U\u{034F}SDC", name: "U\u{034F}SD Coin", registrySymbol: "", assetKey: "", assetClass: "", decimals: 6, metadataStatus: "resolved", role: "spam", spamReason: "obfuscated" },
  { chainId: 1, token: T.DROP_1, symbol: "ZKDROP", name: "ZK Drop Rewards", registrySymbol: "", assetKey: "", assetClass: "", decimals: 18, metadataStatus: "resolved", role: "spam", spamReason: "mass_airdrop", supply: 1000 },
  // Gnosis Chain — hub-priced.
  { chainId: 100, token: T.GNO_100, symbol: "GNO", name: "Gnosis Token on xDai", registrySymbol: "GNO", assetKey: "GNO", assetClass: "GNO", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: gnoPrice, priceSymbol: "GNO", supply: 1_500_000 },
  { chainId: 100, token: T.WXDAI_100, symbol: "WXDAI", name: "Wrapped XDAI", registrySymbol: "WXDAI", assetKey: "DAI", assetClass: "Stablecoins", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: () => 1, priceSymbol: "WXDAI" },
  { chainId: 100, token: T.SDAI_100, symbol: "sDAI", name: "Savings xDAI", registrySymbol: "sDAI", assetKey: "sDAI", assetClass: "Stablecoins", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: (mi) => (mi < 40 ? null : round4(1.02 + 0.004 * (mi - 40))), priceSymbol: "SDAI" },
  { chainId: 100, token: T.USDCE_100, symbol: "USDC.e", name: "Bridged USDC (Gnosis)", registrySymbol: "USDC.e", assetKey: "USDC", assetClass: "Stablecoins", decimals: 6, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: () => 1, priceSymbol: "USDC" },
  { chainId: 100, token: T.EURE_100, symbol: "EURe", name: "Monerium EURe", registrySymbol: "EURe", assetKey: "EURe", assetClass: "Stablecoins", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: eurePrice, priceSymbol: "EURE" },
  { chainId: 100, token: T.EURE_V1_100, symbol: "EURe", name: "Monerium EUR emoney", registrySymbol: "EURe v1", assetKey: "EURe", assetClass: "Stablecoins", decimals: 18, metadataStatus: "resolved", role: "retired_mirror", spamReason: "", roleAt: (mi) => (mi < 50 ? "priced" : "retired_mirror"), hubPrice: eurePrice, priceSymbol: "EURE" },
  { chainId: 100, token: T.OSGNO_100, symbol: "osGNO", name: "Staked GNO", registrySymbol: "osGNO", assetKey: "OSGNO", assetClass: "GNO", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: (mi) => (mi < 51 ? null : round2(gnoPrice(mi) * 1.03)), priceSymbol: "OSGNO" },
  { chainId: 100, token: T.COW_100, symbol: "COW", name: "CoW Protocol Token from Mainnet", registrySymbol: "COW", assetKey: "COW", assetClass: "Other", decimals: 18, metadataStatus: "resolved", role: "priced", spamReason: "", hubPrice: cowPrice, priceSymbol: "COW" },
  // Gnosis Chain — listed (spot quote present).
  { chainId: 100, token: T.XBZZ_100, symbol: "xBZZ", name: "xBZZ", registrySymbol: "xBZZ", assetKey: "BZZ", assetClass: "Other", decimals: 16, metadataStatus: "resolved", role: "listed", spamReason: "" },
  // Gnosis Chain — malformed spam (control characters for a symbol).
  { chainId: 100, token: T.MALFORMED_100, symbol: "\u{0007}\u{0008}", name: "", registrySymbol: "", assetKey: "", assetClass: "", decimals: 18, metadataStatus: "resolved", role: "spam", spamReason: "malformed" },
];

const TOKEN_BY_KEY = new Map(TOKENS.map((token) => [`${token.chainId}:${token.token}`, token]));

function hubPriceAt(token: FixtureToken, mi: number): number | null {
  return token.hubPrice ? token.hubPrice(mi) : null;
}

/** The token's class in a month, as `_expr_treasury_token_class.sql` computes
 * it: a 'priced' registry token with no hub price that month reads 'listed'. */
function classAt(token: FixtureToken, mi: number): TokenClass {
  const role = token.roleAt ? token.roleAt(mi) : token.role;
  if (role === "priced" && hubPriceAt(token, mi) === null) return "listed";
  return role;
}

/** CoinGecko spot quotes — exist only for spot-eligible ('listed') rows. */
const SPOT_QUOTES: Record<ChainId, Record<string, number>> = {
  1: { [T.LDO_1]: 1.21, [T.GRT_1]: 0.18 },
  100: { [T.XBZZ_100]: 0.25 },
};
/** Quotes the server refused (units x spot beyond plausibility). */
const SPOT_REFUSED: Record<ChainId, string[]> = { 1: [T.LDO_1], 100: [] };

// ---------------------------------------------------------------------------
// Wallets
// ---------------------------------------------------------------------------

interface FixtureWallet {
  address: string;
  label: string;
  isLtd: boolean;
  chains: ChainId[];
}

export const W_MAIN = "0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f";
export const W_LTD = "0x604e4557e9020841f4e8eb98148de3d3cdea350c";

function syntheticWallet(index: number): string {
  return `0x${(`d0${index.toString(16).padStart(2, "0")}`).repeat(10)}`;
}

const SHARED_LABELS = [
  "DAO Main Safe", "Gnosis Ltd.", "Operations Safe", "Grants Safe", "Liquidity Safe",
  "Custody G", "Investments Safe", "Payroll Safe", "Validator Rewards", "Ecosystem Fund",
  "Treasury Reserve", "Bridge Buffer", "Legal Wrapper", "GIP Escrow", "Research Fund",
  "Security Council", "Market Ops", "Custody A", "Custody B", "Custody C", "Custody D",
  "Custody E", "Custody F",
];

/** The unlabelled wallet: not in the community list, holds only on Gnosis. */
export const W_GNOSIS_ONLY = `0x${"ab17".repeat(10)}`;
/** A labelled wallet with nothing visible on Ethereum (a zero row there). */
export const W_EMPTY_ETH = syntheticWallet(21);

const WALLETS: FixtureWallet[] = [
  ...SHARED_LABELS.map((label, index) => ({
    address: index === 0 ? W_MAIN : index === 1 ? W_LTD : syntheticWallet(index),
    label,
    isLtd: index === 1,
    chains: [1, 100] as ChainId[],
  })),
  { address: W_GNOSIS_ONLY, label: "", isLtd: false, chains: [100] },
];

export const FIXTURE_WALLETS: readonly string[] = WALLETS.map((wallet) => wallet.address);

// ---------------------------------------------------------------------------
// Positions: (wallet, chain, token) -> units per month.
// ---------------------------------------------------------------------------

interface Position {
  wallet: number;
  chainId: ChainId;
  token: string;
  from: number;
  /** Last month held (inclusive); held through the as-of month when absent. */
  to?: number;
  units: (mi: number) => number;
}

const flat = (value: number) => () => value;

const POSITIONS: Position[] = [
  // --- Ethereum (from 2020-11, month 4) ------------------------------------
  // 0x458c arrives on Ethereum in 2026-07 (a 'partial' month: SAFE was not
  // served) — real assets first, then USDC + WETH in August.
  { wallet: 0, chainId: 1, token: T.GNO_1, from: 72, units: flat(264_000) },
  { wallet: 0, chainId: 1, token: T.SAFE_1, from: 72, units: flat(1_250_000) },
  { wallet: 0, chainId: 1, token: T.COW_1, from: 72, units: flat(2_000_000) },
  { wallet: 0, chainId: 1, token: T.USDC_1, from: 73, units: flat(320_000) },
  { wallet: 0, chainId: 1, token: T.WETH_1, from: 73, units: flat(118.5) },
  { wallet: 1, chainId: 1, token: T.GNO_1, from: 4, units: flat(253_900) },
  { wallet: 1, chainId: 1, token: T.USDC_1, from: 20, units: (mi) => 6_000_000 - 40_000 * (mi - 20) },
  { wallet: 2, chainId: 1, token: T.GNO_1, from: 4, units: flat(60_000) },
  { wallet: 2, chainId: 1, token: T.WETH_1, from: 10, units: flat(800) },
  { wallet: 2, chainId: 1, token: T.STETH_1, from: 40, units: flat(500) },
  { wallet: 2, chainId: 1, token: T.LDO_1, from: 30, units: flat(200_000_000) },
  { wallet: 2, chainId: 1, token: T.BAL_1, from: 12, to: 50, units: flat(1_000_000) },
  { wallet: 2, chainId: 1, token: T.FAKE_USDC_1, from: 62, units: flat(1_000_000) },
  { wallet: 3, chainId: 1, token: T.COW_1, from: 16, units: flat(20_000_000) },
  { wallet: 3, chainId: 1, token: T.SAFE_1, from: 40, units: flat(5_000_000) },
  { wallet: 3, chainId: 1, token: T.FAKE_USDC_1, from: 64, units: flat(250_000) },
  { wallet: 4, chainId: 1, token: T.USDC_1, from: 8, units: flat(2_000_000) },
  { wallet: 4, chainId: 1, token: T.RAID_1, from: 50, units: flat(1000) },
  { wallet: 4, chainId: 1, token: T.RAW_1, from: 55, units: flat(10) },
  { wallet: 4, chainId: 1, token: T.FAKE_USDC_1, from: 66, units: flat(777) },
  { wallet: 4, chainId: 1, token: T.OBF_1, from: 67, units: flat(5000) },
  { wallet: 5, chainId: 1, token: T.GNO_1, from: 4, units: flat(5000) },
  { wallet: 6, chainId: 1, token: T.GNO_1, from: 6, units: flat(1500) },
  { wallet: 6, chainId: 1, token: T.GRT_1, from: 60, units: flat(2_000_000) },
  { wallet: 6, chainId: 1, token: T.LURE_1, from: 70, units: flat(1) },
  { wallet: 7, chainId: 1, token: T.GNO_1, from: 8, units: flat(800) },
  { wallet: 7, chainId: 1, token: T.OBF_1, from: 68, units: flat(12_000) },
  ...Array.from({ length: 15 }, (_, offset) => offset + 8)
    .filter((wallet) => wallet !== 21)
    .map((wallet): Position => ({
      wallet, chainId: 1, token: T.GNO_1, from: 4 + (wallet % 12), units: flat(100 * (wallet - 7)),
    })),
  // A mass airdrop: every Ethereum wallet holds it.
  ...Array.from({ length: 23 }, (_, wallet): Position => ({
    wallet, chainId: 1, token: T.DROP_1, from: 71, units: flat(1000),
  })),
  // --- Gnosis Chain (from 2020-07, month 0) --------------------------------
  { wallet: 0, chainId: 100, token: T.GNO_100, from: 0, units: (mi) => 480_000 + 2966 * mi },
  { wallet: 0, chainId: 100, token: T.SDAI_100, from: 40, units: flat(1_000_000) },
  { wallet: 0, chainId: 100, token: T.WXDAI_100, from: 0, units: (mi) => 200_000 - 1200 * mi },
  // EURe: v1 held since 2022-05; after the 2024-08-25 migration v2 MIRRORS
  // the same balance, so summing both would double-count.
  { wallet: 0, chainId: 100, token: T.EURE_V1_100, from: 22, units: flat(500_000) },
  { wallet: 0, chainId: 100, token: T.EURE_100, from: 50, units: flat(500_000) },
  { wallet: 1, chainId: 100, token: T.GNO_100, from: 0, units: flat(100_000) },
  { wallet: 1, chainId: 100, token: T.USDCE_100, from: 54, units: flat(1_000_000) },
  ...Array.from({ length: 21 }, (_, offset): Position => {
    const wallet = offset + 2;
    return { wallet, chainId: 100, token: T.GNO_100, from: wallet % 18, units: flat(50 * wallet) };
  }),
  { wallet: 9, chainId: 100, token: T.OSGNO_100, from: 44, units: flat(20_000) },
  { wallet: 10, chainId: 100, token: T.MALFORMED_100, from: 62, units: flat(42) },
  { wallet: 11, chainId: 100, token: T.COW_100, from: 16, units: flat(3_000_000) },
  { wallet: 23, chainId: 100, token: T.GNO_100, from: 12, units: flat(10_000) },
  { wallet: 23, chainId: 100, token: T.XBZZ_100, from: 48, units: flat(1_000_000) },
];

/** Tokens not served in a 'partial' month (drawn from what WAS served). */
const UNSERVED: Record<ChainId, Map<number, string[]>> = {
  1: new Map([[72, [T.SAFE_1]]]),
  100: new Map(),
};

function blankStatus(chainId: ChainId, mi: number): "gap" | "unpublished" | undefined {
  return BLANK_MONTHS[chainId].get(mi);
}

function isServed(chainId: ChainId, mi: number, token: string): boolean {
  if (blankStatus(chainId, mi)) return false;
  return !(UNSERVED[chainId].get(mi) ?? []).includes(token);
}

/** Units of every (wallet, token) held on `chainId` in month `mi`. */
function heldAt(chainId: ChainId, mi: number): Array<{ wallet: number; token: FixtureToken; units: number }> {
  const out: Array<{ wallet: number; token: FixtureToken; units: number }> = [];
  for (const position of POSITIONS) {
    if (position.chainId !== chainId || mi < position.from) continue;
    if (position.to !== undefined && mi > position.to) continue;
    const units = position.units(mi);
    if (!(units > 0)) continue;
    const token = TOKEN_BY_KEY.get(`${chainId}:${position.token}`);
    if (token) out.push({ wallet: position.wallet, token, units });
  }
  return out;
}

const VISIBLE: TokenClass[] = ["priced", "listed", "unverified"];

function rawOf(units: number, decimals: number | null): string {
  // Decimals never observed: the fixture still needs an exact integer.
  const scale = decimals ?? 18;
  if (scale >= 6) return (BigInt(Math.round(units * 1e6)) * 10n ** BigInt(scale - 6)).toString();
  return BigInt(Math.round(units * 10 ** scale)).toString();
}

function unitsOf(token: FixtureToken, units: number): number | null {
  return token.decimals === null ? null : units;
}

/** Fold used for `symbol_collisions`: case and invisible characters folded. */
function fold(symbol: string | null): string {
  return String(symbol ?? "")
    .normalize("NFC")
    .replace(/[\p{Cc}\p{Cf}\p{Default_Ignorable_Code_Point}\p{Mn}]/gu, "")
    .toUpperCase();
}

function collisions(token: FixtureToken): number {
  const mine = fold(token.symbol);
  if (!mine) return 0;
  return TOKENS.filter((other) => other.chainId === token.chainId
    && other.token !== token.token
    && fold(other.symbol) === mine
    && heldAt(other.chainId, LAST_MONTH).some((held) => held.token === other)).length;
}

// ---------------------------------------------------------------------------
// Row builders
// ---------------------------------------------------------------------------

type Obj = Record<string, unknown>;

/** Positional rows from name-keyed objects, STRICT both ways: an object key
 * the contract does not name, or a contract column the object omits, throws —
 * so the fixture cannot drift from the column list it declares. */
function rowsOf(key: string, columns: readonly string[], objects: Obj[]): unknown[][] {
  const known = new Set(columns);
  return objects.map((object) => {
    for (const name of Object.keys(object)) {
      if (!known.has(name)) throw new Error(`fixture ${key}: unknown column ${name}`);
    }
    return columns.map((name) => {
      if (!(name in object)) throw new Error(`fixture ${key}: missing column ${name}`);
      return object[name];
    });
  });
}

function descriptor(key: string, sqlFile: string, columns: readonly string[], objects: Obj[]): DatasetDescriptor {
  const rows = rowsOf(key, columns, objects);
  return {
    key,
    title: key.split("_").join(" "),
    sql: `-- development fixture mirroring queries/governance/${sqlFile}.sql`,
    database: "governance_db",
    columns: columns.map((name) => ({ name, type: "Unknown" })),
    stats: { row_count: rows.length, rows_returned: rows.length, mode: "exact_capped", source_rows: rows.length, row_cap: 10000, truncated: false, warnings: [] },
    preview_rows: rows,
    provenance: { coverage: { actual_start: "2020-07-01T00:00:00Z", actual_end: `${FIXTURE_AS_OF}T00:00:00Z`, mode: "exact_capped", warning_codes: [], source_kind: "treasury" } },
  };
}

// Column lists — declared literally, mirroring the SQL. treasuryRows.test.ts
// compares each against treasuryColumns.json, so a contract change fails there.
const SUMMARY_COLUMNS = [
  "chain_id", "as_of", "as_of_status", "anchor_block",
  "published_tokens", "served_tokens", "carried_tokens",
  "wallets_tracked", "wallets_active", "tokens_held", "positions",
  "priced_tokens", "listed_tokens", "unverified_tokens",
  "hidden_spam_tokens", "hidden_retired_tokens",
  "gno_units", "gno_units_ex_ltd", "nav_usd", "nav_usd_ex_ltd",
  "oldest_price_date", "hub_latest_date",
] as const;
const HOLDINGS_COLUMNS = [
  "chain_id", "token_address", "symbol", "name", "registry_symbol",
  "asset_key", "asset_class", "decimals", "metadata_status",
  "token_class", "spam_reason", "wallets_holding",
  "balance_total_raw", "balance_units", "balance_units_ex_ltd",
  "supply_share", "symbol_collisions",
  "price_usd", "price_date", "price_source",
  "value_usd", "value_usd_ex_ltd", "spot_eligible", "token_date", "as_of",
] as const;
const BY_WALLET_COLUMNS = [
  "chain_id", "wallet_address", "wallet_label", "label_source", "is_ltd",
  "tokens_held", "priced_positions", "unpriced_positions", "hidden_positions",
  "gno_units", "nav_usd", "as_of",
] as const;
const HISTORY_COLUMNS = [
  "grain", "chain_id", "bucket", "bucket_date",
  "wallet_address", "wallet_label", "is_ltd",
  "token_address", "registry_symbol", "asset_key", "asset_class", "token_class",
  "balance_units", "balance_units_ex_ltd", "price_usd", "price_date",
  "nav_usd", "nav_usd_ex_ltd", "gno_units", "gno_units_ex_ltd",
  "wallets_holding", "tokens_held", "positions",
  "priced_tokens", "listed_tokens", "unverified_tokens", "hidden_spam_tokens",
] as const;
const COVERAGE_COLUMNS = [
  "chain_id", "bucket", "raw_month_end", "bucket_date",
  "published_tokens", "served_tokens", "carried_tokens", "unserved_tokens",
  "unserved_registry_tokens", "unserved_registry_symbols", "status",
] as const;
const WALLET_DETAIL_COLUMNS = [
  "chain_id", "wallet_address", "wallet_label", "label_source", "is_ltd",
  "as_of", "as_of_status", "anchor_block",
  "tokens_held", "priced_positions", "unpriced_positions", "hidden_positions",
  "gno_units", "nav_usd",
] as const;
const WALLET_POSITIONS_COLUMNS = [
  "chain_id", "token_address", "symbol", "name", "registry_symbol",
  "asset_key", "asset_class", "decimals", "metadata_status",
  "token_class", "spam_reason", "wallets_holding", "symbol_collisions",
  "balance_total_raw", "balance_units", "treasury_share", "supply_share",
  "price_usd", "price_date", "price_source", "value_usd",
  "spot_eligible", "token_date", "as_of",
] as const;
const WALLET_SERIES_COLUMNS = [
  "chain_id", "bucket", "bucket_date", "token_address", "registry_symbol",
  "asset_key", "asset_class", "token_class",
  "balance_units", "price_usd", "price_date", "value_usd",
] as const;
const WALLET_CHAINS_COLUMNS = [
  "chain_id", "wallet_address", "tracked", "has_positions",
  "tokens_held", "nav_usd", "as_of",
] as const;
const TOKEN_DETAIL_COLUMNS = [
  "chain_id", "token_address", "symbol", "name", "registry_symbol",
  "asset_key", "asset_class", "decimals", "metadata_status",
  "token_class", "spam_reason", "wallets_holding", "symbol_collisions",
  "balance_total_raw", "balance_units", "supply_share",
  "price_usd", "price_date", "price_source", "value_usd",
  "spot_eligible", "token_date", "as_of", "anchor_block", "sibling_tokens",
] as const;
const TOKEN_HOLDERS_COLUMNS = [
  "chain_id", "wallet_address", "wallet_label", "label_source", "is_ltd",
  "balance_total_raw", "balance_units", "value_usd", "treasury_share",
] as const;
const HOLDER_SERIES_COLUMNS = [
  "chain_id", "bucket", "bucket_date", "wallet_address", "wallet_label",
  "is_ltd", "balance_units", "value_usd",
] as const;
const PRICE_HISTORY_COLUMNS = ["day", "price_symbol", "price_usd", "role"] as const;

// ---------------------------------------------------------------------------
// Derivations
// ---------------------------------------------------------------------------

/** Units x hub price, unrounded, so every grain sums to the same NAV. Only a
 * token PRICED that month is valued. */
function tokenValue(token: FixtureToken, mi: number, units: number): number | null {
  if (classAt(token, mi) !== "priced" || token.decimals === null) return null;
  const price = hubPriceAt(token, mi);
  return price === null ? null : units * price;
}

const sum = (values: Array<number | null>) => values.reduce<number>((acc, value) => acc + (value ?? 0), 0);

interface TokenAgg {
  token: FixtureToken;
  units: number;
  unitsExLtd: number;
  wallets: number[];
}

function tokenAggregates(chainId: ChainId, mi: number, servedOnly: boolean): TokenAgg[] {
  const byToken = new Map<string, TokenAgg>();
  for (const held of heldAt(chainId, mi)) {
    if (servedOnly && !isServed(chainId, mi, held.token.token)) continue;
    const agg = byToken.get(held.token.token) ?? { token: held.token, units: 0, unitsExLtd: 0, wallets: [] };
    agg.units += held.units;
    if (!WALLETS[held.wallet].isLtd) agg.unitsExLtd += held.units;
    agg.wallets.push(held.wallet);
    byToken.set(held.token.token, agg);
  }
  return [...byToken.values()];
}

function chainCounts(chainId: ChainId, mi: number, servedOnly: boolean) {
  const aggs = tokenAggregates(chainId, mi, servedOnly);
  const holds = heldAt(chainId, mi).filter((held) => !servedOnly || isServed(chainId, mi, held.token.token));
  const visibleHolds = holds.filter((held) => VISIBLE.includes(classAt(held.token, mi)));
  const ofClass = (cls: TokenClass) => aggs.filter((agg) => classAt(agg.token, mi) === cls).length;
  const gnoAggs = aggs.filter((agg) => agg.token.registrySymbol === "GNO");
  return {
    aggs,
    walletsHolding: new Set(holds.map((held) => held.wallet)).size,
    tokensHeld: aggs.filter((agg) => VISIBLE.includes(classAt(agg.token, mi))).length,
    positions: visibleHolds.length,
    priced: ofClass("priced"),
    listed: ofClass("listed"),
    unverified: ofClass("unverified"),
    spam: ofClass("spam"),
    retired: ofClass("retired_mirror"),
    nav: sum(aggs.map((agg) => tokenValue(agg.token, mi, agg.units))),
    navExLtd: sum(aggs.map((agg) => tokenValue(agg.token, mi, agg.unitsExLtd))),
    gno: round2(gnoAggs.reduce((acc, agg) => acc + agg.units, 0)),
    gnoExLtd: round2(gnoAggs.reduce((acc, agg) => acc + agg.unitsExLtd, 0)),
  };
}

function priceFields(token: FixtureToken, mi: number) {
  const price = classAt(token, mi) === "priced" ? hubPriceAt(token, mi) : null;
  return {
    price_usd: price,
    price_date: price === null ? "" : monthEnd(mi),
    price_source: price === null ? "" : token.proxy ? "hub_proxy" : "hub",
  };
}

/** `toUInt8(class = 'listed' AND decimals IS NOT NULL)` — the ONLY rows a
 * CoinGecko spot quote may value. */
function spotEligible(token: FixtureToken, mi: number): number {
  return classAt(token, mi) === "listed" && token.decimals !== null ? 1 : 0;
}

function tokenDate(token: FixtureToken): string {
  // One token is CARRIED from its own latest served day (counted in summary).
  return token.token === T.WETH_1 ? "2026-09-22" : FIXTURE_AS_OF;
}

function holdingObject(agg: TokenAgg): Obj {
  const token = agg.token;
  const units = unitsOf(token, agg.units);
  const unitsExLtd = unitsOf(token, agg.unitsExLtd);
  return {
    chain_id: token.chainId,
    token_address: token.token,
    symbol: token.symbol,
    name: token.name,
    registry_symbol: token.registrySymbol,
    asset_key: token.assetKey,
    asset_class: token.assetClass,
    decimals: token.decimals,
    metadata_status: token.metadataStatus,
    token_class: classAt(token, LAST_MONTH),
    spam_reason: token.spamReason,
    wallets_holding: agg.wallets.length,
    balance_total_raw: rawOf(agg.units, token.decimals),
    balance_units: units,
    balance_units_ex_ltd: unitsExLtd,
    supply_share: token.supply && units !== null ? round4(units / token.supply) : null,
    symbol_collisions: collisions(token),
    ...priceFields(token, LAST_MONTH),
    value_usd: units === null ? null : tokenValue(token, LAST_MONTH, units),
    value_usd_ex_ltd: unitsExLtd === null ? null : tokenValue(token, LAST_MONTH, unitsExLtd),
    spot_eligible: spotEligible(token, LAST_MONTH),
    token_date: tokenDate(token),
    as_of: FIXTURE_AS_OF,
  };
}

const CHAINS: ChainId[] = [1, 100];

function summaryObjects(): Obj[] {
  return CHAINS.map((chainId) => {
    const counts = chainCounts(chainId, LAST_MONTH, false);
    const tracked = WALLETS.filter((wallet) => wallet.chains.includes(chainId)).length;
    return {
      chain_id: chainId,
      as_of: FIXTURE_AS_OF,
      as_of_status: "complete",
      anchor_block: ANCHOR_BLOCK[chainId],
      published_tokens: counts.aggs.length,
      served_tokens: counts.aggs.length,
      carried_tokens: counts.aggs.filter((agg) => tokenDate(agg.token) !== FIXTURE_AS_OF).length,
      wallets_tracked: tracked,
      wallets_active: counts.walletsHolding,
      tokens_held: counts.tokensHeld,
      positions: counts.positions,
      priced_tokens: counts.priced,
      listed_tokens: counts.listed,
      unverified_tokens: counts.unverified,
      hidden_spam_tokens: counts.spam,
      hidden_retired_tokens: counts.retired,
      gno_units: counts.gno,
      gno_units_ex_ltd: counts.gnoExLtd,
      nav_usd: counts.nav,
      nav_usd_ex_ltd: counts.navExLtd,
      oldest_price_date: FIXTURE_AS_OF,
      hub_latest_date: FIXTURE_AS_OF,
    };
  });
}

function holdingsObjects(): Obj[] {
  return CHAINS.flatMap((chainId) => tokenAggregates(chainId, LAST_MONTH, false)
    .sort((a, b) => (tokenValue(b.token, LAST_MONTH, b.units) ?? -1) - (tokenValue(a.token, LAST_MONTH, a.units) ?? -1)
      || (a.token.token < b.token.token ? -1 : 1))
    .map(holdingObject));
}

interface WalletStats {
  tokensHeld: number;
  priced: number;
  unpriced: number;
  hidden: number;
  gno: number;
  nav: number;
}

function walletStats(chainId: ChainId, walletIndex: number, mi: number, servedOnly: boolean): WalletStats {
  const holds = heldAt(chainId, mi).filter((held) => held.wallet === walletIndex
    && (!servedOnly || isServed(chainId, mi, held.token.token)));
  let priced = 0;
  let unpriced = 0;
  let hidden = 0;
  let nav = 0;
  let gno = 0;
  for (const held of holds) {
    const cls = classAt(held.token, mi);
    const value = tokenValue(held.token, mi, held.units);
    if (cls === "spam" || cls === "retired_mirror") hidden += 1;
    else if (value !== null) priced += 1;
    else unpriced += 1;
    nav += value ?? 0;
    if (held.token.registrySymbol === "GNO") gno += held.units;
  }
  return { tokensHeld: priced + unpriced, priced, unpriced, hidden, gno: round2(gno), nav };
}

/** Every LABELLED wallet on every chain (zeros where empty — every labelled
 * wallet here is on both chains), plus unlabelled wallets on the chains they
 * hold something on. */
function walletsOn(chainId: ChainId): Array<{ wallet: FixtureWallet; index: number }> {
  return WALLETS.flatMap((wallet, index) => (wallet.chains.includes(chainId) ? [{ wallet, index }] : []));
}

function byWalletObjects(): Obj[] {
  return CHAINS.flatMap((chainId) => walletsOn(chainId).map(({ wallet, index }) => {
    const stats = walletStats(chainId, index, LAST_MONTH, false);
    return {
      chain_id: chainId,
      wallet_address: wallet.address,
      wallet_label: wallet.label,
      // The SQL passes the list's source as a constant on EVERY row, labelled
      // or not; the UI shows it only beside a label.
      label_source: LABEL_SOURCE,
      is_ltd: wallet.isLtd ? 1 : 0,
      tokens_held: stats.tokensHeld,
      priced_positions: stats.priced,
      unpriced_positions: stats.unpriced,
      hidden_positions: stats.hidden,
      gno_units: stats.gno,
      nav_usd: stats.nav,
      // A wallet outside the label list reaches the table through the FULL
      // JOIN's data side only; its as_of is its chain's, read from its rows.
      as_of: FIXTURE_AS_OF,
    };
  }));
}

const EMPTY_HISTORY: Obj = {
  wallet_address: "", wallet_label: "", is_ltd: 0,
  token_address: "", registry_symbol: "", asset_key: "", asset_class: "", token_class: "",
  balance_units: null, balance_units_ex_ltd: null, price_usd: null, price_date: null,
  nav_usd: 0, nav_usd_ex_ltd: 0, gno_units: 0, gno_units_ex_ltd: 0,
  wallets_holding: 0, tokens_held: 0, positions: 0,
  priced_tokens: 0, listed_tokens: 0, unverified_tokens: 0, hidden_spam_tokens: 0,
};

function historyObjects(): Obj[] {
  const out: Obj[] = [];
  for (const chainId of CHAINS) {
    for (let mi = CHAIN_START[chainId]; mi <= LAST_MONTH; mi += 1) {
      if (blankStatus(chainId, mi)) continue; // nothing served: no rows at all
      const counts = chainCounts(chainId, mi, true);
      const base = { chain_id: chainId, bucket: monthBucket(mi), bucket_date: monthEnd(mi) };
      out.push({
        ...EMPTY_HISTORY, ...base, grain: "chain",
        nav_usd: counts.nav, nav_usd_ex_ltd: counts.navExLtd,
        gno_units: counts.gno, gno_units_ex_ltd: counts.gnoExLtd,
        wallets_holding: counts.walletsHolding, tokens_held: counts.tokensHeld, positions: counts.positions,
        priced_tokens: counts.priced, listed_tokens: counts.listed,
        unverified_tokens: counts.unverified, hidden_spam_tokens: counts.spam,
      });
      WALLETS.forEach((wallet, index) => {
        if (!wallet.chains.includes(chainId)) return;
        const stats = walletStats(chainId, index, mi, true);
        if (stats.tokensHeld === 0) return; // wallet grain = visible tokens only
        out.push({
          ...EMPTY_HISTORY, ...base, grain: "wallet",
          wallet_address: wallet.address, wallet_label: wallet.label, is_ltd: wallet.isLtd ? 1 : 0,
          nav_usd: stats.nav, nav_usd_ex_ltd: wallet.isLtd ? 0 : stats.nav,
          gno_units: stats.gno, gno_units_ex_ltd: wallet.isLtd ? 0 : stats.gno,
          wallets_holding: 1, tokens_held: stats.tokensHeld, positions: stats.tokensHeld,
          priced_tokens: stats.priced,
        });
      });
      for (const agg of counts.aggs) {
        const token = agg.token;
        // Token grain: PRICED token-months only (priced = the hub has a price).
        if (classAt(token, mi) !== "priced") continue;
        const price = hubPriceAt(token, mi);
        out.push({
          ...EMPTY_HISTORY, ...base, grain: "token",
          token_address: token.token, registry_symbol: token.registrySymbol,
          asset_key: token.assetKey, asset_class: token.assetClass, token_class: "priced",
          balance_units: round4(agg.units), balance_units_ex_ltd: round4(agg.unitsExLtd),
          price_usd: price, price_date: monthEnd(mi),
          nav_usd: tokenValue(token, mi, agg.units) ?? 0, nav_usd_ex_ltd: tokenValue(token, mi, agg.unitsExLtd) ?? 0,
          gno_units: token.registrySymbol === "GNO" ? round2(agg.units) : 0,
          gno_units_ex_ltd: token.registrySymbol === "GNO" ? round2(agg.unitsExLtd) : 0,
          wallets_holding: agg.wallets.length, tokens_held: 1, positions: agg.wallets.length,
          priced_tokens: 1,
        });
      }
    }
  }
  return out;
}

/** The coverage calendar spine, from each chain's first month (146 rows). */
function coverageObjects(chainFilter?: ChainId): Obj[] {
  const out: Obj[] = [];
  for (const chainId of CHAINS) {
    if (chainFilter !== undefined && chainId !== chainFilter) continue;
    for (let mi = CHAIN_START[chainId]; mi <= LAST_MONTH; mi += 1) {
      const blank = blankStatus(chainId, mi);
      const all = blank === "unpublished" ? [] : tokenAggregates(chainId, mi, false);
      const unserved = all.filter((agg) => !isServed(chainId, mi, agg.token.token));
      const registry = unserved.filter((agg) => agg.token.registrySymbol !== ""
        && (agg.token.role === "priced" || agg.token.role === "listed"));
      const served = all.length - unserved.length;
      out.push({
        chain_id: chainId,
        bucket: monthBucket(mi),
        raw_month_end: blank === "unpublished" ? null : monthEnd(mi),
        bucket_date: served === 0 ? null : monthEnd(mi),
        published_tokens: all.length,
        served_tokens: served,
        carried_tokens: mi === LAST_MONTH && chainId === 1 ? 1 : 0,
        unserved_tokens: unserved.length,
        unserved_registry_tokens: registry.length,
        unserved_registry_symbols: registry.map((agg) => agg.token.registrySymbol).sort(),
        status: blank ?? (unserved.length > 0 ? "partial" : "complete"),
      });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Public: section datasets, entity bundles, overlays.
// ---------------------------------------------------------------------------

export function treasurySectionDatasets(): Record<string, DatasetDescriptor> {
  return {
    treasury_summary: descriptor("treasury_summary", "treasury_summary", SUMMARY_COLUMNS, summaryObjects()),
    treasury_holdings: descriptor("treasury_holdings", "treasury_holdings", HOLDINGS_COLUMNS, holdingsObjects()),
    treasury_by_wallet: descriptor("treasury_by_wallet", "treasury_by_wallet", BY_WALLET_COLUMNS, byWalletObjects()),
    treasury_history: descriptor("treasury_history", "treasury_history", HISTORY_COLUMNS, historyObjects()),
    treasury_history_coverage: descriptor(
      "treasury_history_coverage", "treasury_history_coverage", COVERAGE_COLUMNS, coverageObjects(),
    ),
  };
}

function parseIdentifier(identifier: string): { chainId: ChainId; address: string } | null {
  const sep = identifier.indexOf(":");
  if (sep <= 0) return null;
  const chainId = Number(identifier.slice(0, sep));
  if (chainId !== 1 && chainId !== 100) return null;
  return { chainId, address: identifier.slice(sep + 1).trim().toLowerCase() };
}

function walletBundle(chainId: ChainId, address: string): Record<string, DatasetDescriptor> {
  const index = WALLETS.findIndex((wallet) => wallet.address === address);
  const wallet = index >= 0 ? WALLETS[index] : null;
  const present = Boolean(wallet?.chains.includes(chainId));
  const stats = present ? walletStats(chainId, index, LAST_MONTH, false) : null;
  // wallet_detail.sql selects FROM the served chain's as-of and LEFT JOINs the
  // wallet's positions: ONE row for any address — zeros where it holds nothing,
  // even an address outside the census.
  const detail: Obj[] = [{
    chain_id: chainId,
    wallet_address: address,
    wallet_label: wallet?.label ?? "",
    label_source: LABEL_SOURCE,
    is_ltd: wallet?.isLtd ? 1 : 0,
    as_of: FIXTURE_AS_OF,
    as_of_status: "complete",
    anchor_block: ANCHOR_BLOCK[chainId],
    tokens_held: stats?.tokensHeld ?? 0,
    priced_positions: stats?.priced ?? 0,
    unpriced_positions: stats?.unpriced ?? 0,
    hidden_positions: stats?.hidden ?? 0,
    gno_units: stats?.gno ?? 0,
    nav_usd: stats?.nav ?? 0,
  }];
  const aggs = new Map(tokenAggregates(chainId, LAST_MONTH, false).map((agg) => [agg.token.token, agg]));
  const positions: Obj[] = present
    ? heldAt(chainId, LAST_MONTH).filter((held) => held.wallet === index).map((held) => {
      const token = held.token;
      const agg = aggs.get(token.token);
      const units = unitsOf(token, held.units);
      return {
        chain_id: chainId,
        token_address: token.token,
        symbol: token.symbol,
        name: token.name,
        registry_symbol: token.registrySymbol,
        asset_key: token.assetKey,
        asset_class: token.assetClass,
        decimals: token.decimals,
        metadata_status: token.metadataStatus,
        token_class: classAt(token, LAST_MONTH),
        spam_reason: token.spamReason,
        wallets_holding: agg?.wallets.length ?? 1,
        symbol_collisions: collisions(token),
        balance_total_raw: rawOf(held.units, token.decimals),
        balance_units: units,
        treasury_share: agg ? round4(held.units / agg.units) : null,
        supply_share: token.supply && units !== null ? round4(units / token.supply) : null,
        ...priceFields(token, LAST_MONTH),
        value_usd: units === null ? null : tokenValue(token, LAST_MONTH, units),
        spot_eligible: spotEligible(token, LAST_MONTH),
        token_date: tokenDate(token),
        as_of: FIXTURE_AS_OF,
      };
    })
    : [];
  const series: Obj[] = [];
  if (present) {
    for (let mi = CHAIN_START[chainId]; mi <= LAST_MONTH; mi += 1) {
      for (const held of heldAt(chainId, mi)) {
        if (held.wallet !== index || !isServed(chainId, mi, held.token.token)) continue;
        if (classAt(held.token, mi) !== "priced") continue; // wallet_series: x_class = 'priced'
        series.push({
          chain_id: chainId,
          bucket: monthBucket(mi),
          bucket_date: monthEnd(mi),
          token_address: held.token.token,
          registry_symbol: held.token.registrySymbol,
          asset_key: held.token.assetKey,
          asset_class: held.token.assetClass,
          token_class: "priced",
          balance_units: held.units,
          price_usd: hubPriceAt(held.token, mi),
          price_date: monthEnd(mi),
          value_usd: tokenValue(held.token, mi, held.units),
        });
      }
    }
  }
  // wallet_chains.sql: one row per served chain; tracked = labelled OR present.
  const chains: Obj[] = CHAINS.map((chain) => {
    const onChain = Boolean(wallet?.chains.includes(chain));
    const chainStats = onChain ? walletStats(chain, index, LAST_MONTH, false) : null;
    return {
      chain_id: chain,
      wallet_address: address,
      tracked: onChain || Boolean(wallet?.label) ? 1 : 0,
      has_positions: chainStats && chainStats.tokensHeld > 0 ? 1 : 0,
      tokens_held: chainStats?.tokensHeld ?? 0,
      nav_usd: chainStats ? chainStats.nav : 0,
      as_of: FIXTURE_AS_OF,
    };
  });
  return {
    treasury_wallet_detail: descriptor("treasury_wallet_detail", "wallet_detail", WALLET_DETAIL_COLUMNS, detail),
    treasury_wallet_positions: descriptor("treasury_wallet_positions", "wallet_positions", WALLET_POSITIONS_COLUMNS, positions),
    treasury_wallet_series: descriptor("treasury_wallet_series", "wallet_series", WALLET_SERIES_COLUMNS, series),
    treasury_wallet_chains: descriptor("treasury_wallet_chains", "wallet_chains", WALLET_CHAINS_COLUMNS, chains),
    treasury_wallet_months: descriptor("treasury_wallet_months", "treasury_history_coverage", COVERAGE_COLUMNS, coverageObjects(chainId)),
  };
}

function tokenBundle(chainId: ChainId, address: string): Record<string, DatasetDescriptor> {
  const token = TOKEN_BY_KEY.get(`${chainId}:${address}`) ?? null;
  // Not held on the as-of day -> token_detail and token_holders are EMPTY, while
  // its history and price history can still exist.
  const agg = token ? tokenAggregates(chainId, LAST_MONTH, false).find((entry) => entry.token === token) ?? null : null;
  const detail: Obj[] = [];
  if (token && agg) {
    const { balance_units_ex_ltd: _unitsEx, value_usd_ex_ltd: _valueEx, ...rest } = holdingObject(agg);
    void _unitsEx;
    void _valueEx;
    const siblings = token.assetKey && token.role !== "spam" && token.role !== "retired_mirror"
      ? TOKENS.filter((other) => other.assetKey === token.assetKey && other.chainId !== chainId
        && other.role !== "retired_mirror").map((other) => `${other.chainId}:${other.token}`)
      : [];
    detail.push({ ...rest, anchor_block: ANCHOR_BLOCK[chainId], sibling_tokens: siblings });
  }
  const holders: Obj[] = token && agg
    ? heldAt(chainId, LAST_MONTH).filter((held) => held.token === token).map((held) => {
      const wallet = WALLETS[held.wallet];
      const units = unitsOf(token, held.units);
      return {
        chain_id: chainId,
        wallet_address: wallet.address,
        wallet_label: wallet.label,
        label_source: LABEL_SOURCE,
        is_ltd: wallet.isLtd ? 1 : 0,
        balance_total_raw: rawOf(held.units, token.decimals),
        balance_units: units,
        value_usd: units === null ? null : tokenValue(token, LAST_MONTH, units),
        treasury_share: round4(held.units / agg.units),
      };
    }).sort((a, b) => Number(b.balance_units ?? 0) - Number(a.balance_units ?? 0))
    : [];
  const holderSeries: Obj[] = [];
  if (token) {
    for (let mi = CHAIN_START[chainId]; mi <= LAST_MONTH; mi += 1) {
      if (!isServed(chainId, mi, token.token)) continue;
      for (const held of heldAt(chainId, mi)) {
        if (held.token !== token) continue;
        const wallet = WALLETS[held.wallet];
        holderSeries.push({
          chain_id: chainId,
          bucket: monthBucket(mi),
          bucket_date: monthEnd(mi),
          wallet_address: wallet.address,
          wallet_label: wallet.label,
          is_ltd: wallet.isLtd ? 1 : 0,
          balance_units: unitsOf(token, held.units),
          value_usd: tokenValue(token, mi, held.units),
        });
      }
    }
  }
  // token_price_history.sql: the hub series of a registry-PRICED token over its
  // priced registry windows, from its first census publication (weekly points
  // here; the real query is daily). Empty for anything the registry does not
  // price.
  const prices: Obj[] = [];
  if (token?.hubPrice && token.priceSymbol) {
    const first = POSITIONS.filter((position) => position.chainId === chainId && position.token === token.token)
      .reduce((acc, position) => Math.min(acc, position.from), LAST_MONTH);
    const { year, month } = yearMonth(first);
    const start = Date.UTC(year, month - 1, 1);
    const end = Date.UTC(2026, 8, 24);
    for (let stamp = start; stamp <= end; stamp += 7 * 86_400_000) {
      const date = new Date(stamp);
      const mi = (date.getUTCFullYear() - 2020) * 12 + date.getUTCMonth() - 6;
      const role = token.roleAt ? token.roleAt(mi) : token.role;
      const price = token.hubPrice(mi);
      if (price === null || role !== "priced") continue;
      prices.push({
        day: date.toISOString().slice(0, 10),
        price_symbol: token.priceSymbol,
        price_usd: round4(price * (1 + 0.012 * Math.sin(stamp / (86_400_000 * 9)))),
        role: "priced",
      });
    }
  }
  return {
    treasury_token_detail: descriptor("treasury_token_detail", "token_detail", TOKEN_DETAIL_COLUMNS, detail),
    treasury_token_holders: descriptor("treasury_token_holders", "token_holders", TOKEN_HOLDERS_COLUMNS, holders),
    treasury_token_holder_series: descriptor("treasury_token_holder_series", "holder_series", HOLDER_SERIES_COLUMNS, holderSeries),
    treasury_token_price_history: descriptor("treasury_token_price_history", "token_price_history", PRICE_HISTORY_COLUMNS, prices),
    treasury_token_months: descriptor("treasury_token_months", "treasury_history_coverage", COVERAGE_COLUMNS, coverageObjects(chainId)),
  };
}

/** The entity bundle a `load_governance_entity` call would attach, for any
 * `<chain>:<address>` in the fixture (unknown ones come back empty, which is
 * the real server's shape for an address it holds nothing for). */
export function treasuryEntityDatasets(
  entityType: "treasury_wallet" | "treasury_token",
  identifier: string,
): Record<string, DatasetDescriptor> {
  const parsed = parseIdentifier(identifier);
  const chainId = parsed?.chainId ?? 1;
  const address = parsed?.address ?? "";
  return entityType === "treasury_wallet" ? walletBundle(chainId, address) : tokenBundle(chainId, address);
}

export const TREASURY_PRICE_OVERLAY = {
  kind: "spot" as const,
  role: "spot_fallback",
  by_chain: {
    "1": { ...SPOT_QUOTES[1] },
    "100": { ...SPOT_QUOTES[100] },
  },
  excluded_implausible: {
    "1": [...SPOT_REFUSED[1]],
    "100": [...SPOT_REFUSED[100]],
  },
};

export const TREASURY_ICON_OVERLAY: Record<string, Record<string, string>> = {
  "1": { [T.GNO_1]: "https://coin-images.coingecko.com/coins/images/662/thumb/logo_square_simple_300px.png" },
  "100": { [T.GNO_100]: "https://coin-images.coingecko.com/coins/images/662/thumb/logo_square_simple_300px.png" },
};
