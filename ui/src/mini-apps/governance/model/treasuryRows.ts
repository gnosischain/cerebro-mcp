// Treasury dataset parsers — the ONLY module that knows treasury column names.
//
// The contract lives in ./treasuryColumns.json (shared with the backend SQL and
// pinned by treasuryRows.test.ts). Everything downstream of this file reads
// typed rows, never `row.some_column`, so a renamed or reordered column breaks
// exactly one place, and loudly.
//
// Conventions, all load-bearing:
//   * Rows are read BY COLUMN NAME (rowsToObjects), never by position.
//   * NULL means "not measured / not priced" and stays null — `finite()` never
//     turns a missing number into 0, which would fabricate a zero balance or a
//     worthless token.
//   * Addresses are lowercased: the overlay and the entity identifiers are
//     lowercase-keyed, and a checksummed row would otherwise split an identity.
//   * On-chain `symbol` / `name` are ATTACKER-AUTHORED (this treasury holds
//     dozens of fake "USDC" contracts and phishing lures) and are sanitized
//     here, once. `registry_symbol` / `asset_key` / `asset_class` / wallet
//     labels come from a reviewed registry and are trusted — they are still
//     passed through the same sanitizer, which costs nothing on clean text.
//   * Dates are calendar days ('YYYY-MM-DD'); buckets are month starts
//     ('YYYY-MM-01'). Both are derived with integer arithmetic, never through
//     `Date` parsing, so the viewer's timezone can never move a month boundary.

import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import { sanitizeSymbol, sanitizeText } from "../../shared/TokenIdentity";

// ---------------------------------------------------------------------------
// Vocabularies (mirrors treasuryColumns.json `vocab`; a test pins equality).
// ---------------------------------------------------------------------------

export const TOKEN_CLASSES = ["priced", "listed", "unverified", "spam", "retired_mirror"] as const;
export type TokenClass = (typeof TOKEN_CLASSES)[number];

export const SPAM_REASONS = ["", "impersonation", "lure", "obfuscated", "malformed", "mass_airdrop"] as const;
export type SpamReason = (typeof SPAM_REASONS)[number];

export const AS_OF_STATUSES = ["complete", "partial", "no_served_snapshot"] as const;
export type AsOfStatus = (typeof AS_OF_STATUSES)[number];

export const COVERAGE_STATUSES = ["complete", "partial", "gap", "unpublished"] as const;
export type CoverageStatus = (typeof COVERAGE_STATUSES)[number];

export const HISTORY_GRAINS = ["chain", "wallet", "token"] as const;
export type HistoryGrain = (typeof HISTORY_GRAINS)[number];

/** "hub" = the token's own dbt price hub series; "hub_proxy" = a pegged
 * asset's hub series (stETH with WETH, DAI with xDAI, supply tokens with their
 * reserve) — hub-priced in totals AND history, shown with a peg-proxy marker. */
export const PRICE_SOURCES = ["hub", "hub_proxy", ""] as const;
export type PriceSource = (typeof PRICE_SOURCES)[number];

export const ASSET_CLASSES = ["GNO", "ETH", "Stablecoins", "BTC", "RWA", "Other", ""] as const;
export type AssetClass = (typeof ASSET_CLASSES)[number];

/** Display caps for untrusted/registry text. A wallet label is a short name;
 * anything longer is either a mistake or someone smuggling a sentence in. */
export const LABEL_MAX = 40;
export const NAME_MAX = 48;
const REGISTRY_TEXT_MAX = 24;
const SOURCE_MAX = 80;

// ---------------------------------------------------------------------------
// Scalar readers
// ---------------------------------------------------------------------------

type Row = Record<string, unknown>;

/** Strict number: null/undefined/""/booleans/NaN stay null. */
export const num = finite;

/** A flag that may arrive as 0/1, "0"/"1", true/false or "true"/"false". */
export function flag(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    return text === "true" || text === "1";
  }
  return (finite(value) ?? 0) > 0;
}

/** Trimmed text, "" for null/undefined. */
export function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

/** A lowercase 0x address, or "" when the value is not address-shaped. */
export function address(value: unknown): string {
  const raw = text(value).toLowerCase();
  return /^0x[0-9a-f]+$/.test(raw) ? raw : "";
}

/** Big on-chain integers travel as strings; keep them exact. */
function rawInteger(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "bigint") return value.toString();
  return text(value);
}

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

/** Civil date from a day number (days since 1970-01-01), integer arithmetic
 * only (Howard Hinnant's `civil_from_days`). ClickHouse `Date` may serialize
 * as this number. */
export function civilFromDays(days: number): { year: number; month: number; day: number } {
  const z = Math.trunc(days) + 719468;
  const era = Math.floor(z / 146097);
  const doe = z - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const day = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const month = mp < 10 ? mp + 3 : mp - 9;
  return { year: yoe + era * 400 + (month <= 2 ? 1 : 0), month, day };
}

/** A calendar day 'YYYY-MM-DD' from a 'YYYY-MM-DD' string, a datetime
 * ('YYYY-MM-DD HH:MM:SS' or ISO — the calendar day AS WRITTEN, no timezone
 * conversion), or a day-number integer. "" when unparseable. */
export function day(value: unknown): string {
  if (value === null || value === undefined || typeof value === "boolean") return "";
  if (typeof value === "number" || (typeof value === "string" && /^\s*-?\d{1,6}\s*$/.test(value))) {
    const days = Number(value);
    if (!Number.isInteger(days)) return "";
    const civil = civilFromDays(days);
    return `${civil.year}-${pad2(civil.month)}-${pad2(civil.day)}`;
  }
  const match = /^\s*(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  if (!match) return "";
  const month = Number(match[2]);
  const dom = Number(match[3]);
  if (month < 1 || month > 12 || dom < 1 || dom > 31) return "";
  return `${match[1]}-${match[2]}-${match[3]}`;
}

/** A month bucket 'YYYY-MM-01' from anything `day()` accepts, or 'YYYY-MM'. */
export function bucket(value: unknown): string {
  if (typeof value === "string") {
    const month = /^\s*(\d{4})-(\d{2})\s*$/.exec(value);
    if (month) {
      const m = Number(month[2]);
      return m >= 1 && m <= 12 ? `${month[1]}-${month[2]}-01` : "";
    }
  }
  const d = day(value);
  return d ? `${d.slice(0, 7)}-01` : "";
}

function oneOf<T extends string>(values: readonly T[], value: unknown, fallback: T): T {
  const raw = text(value);
  return (values as readonly string[]).includes(raw) ? (raw as T) : fallback;
}

/** Unknown or empty class -> "unverified": a token the registry cannot vouch
 * for is shown as unverified, never silently promoted to priced. */
export function tokenClassOf(value: unknown): TokenClass {
  return oneOf(TOKEN_CLASSES, value, "unverified");
}

export function spamReasonOf(value: unknown): SpamReason {
  return oneOf(SPAM_REASONS, value, "");
}

export function assetClassOf(value: unknown): AssetClass {
  return oneOf(ASSET_CLASSES, value, "");
}

function priceSourceOf(value: unknown): PriceSource {
  return oneOf(PRICE_SOURCES, value, "");
}

export function coverageStatusOf(value: unknown): CoverageStatus {
  // An unrecognised status is a month we cannot vouch for: treat it like a
  // gap, never like a complete month.
  return oneOf(COVERAGE_STATUSES, value, "gap");
}

function asOfStatusOf(value: unknown): AsOfStatus | "" {
  return oneOf<AsOfStatus | "">([...AS_OF_STATUSES, ""], value, "");
}

/** Registry-trusted short text (symbols, asset keys). */
function registryText(value: unknown): string {
  return sanitizeText(value, REGISTRY_TEXT_MAX);
}

/** An Array(String) column that may also arrive JSON-encoded or comma-joined. */
function stringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((entry) => text(entry)).filter(Boolean);
  const raw = text(value);
  if (!raw) return [];
  if (raw.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.map((entry) => text(entry)).filter(Boolean);
    } catch {
      // Fall through to the comma split: a malformed list is still readable.
    }
  }
  return raw.replace(/^\[|\]$/g, "").split(",").map((entry) => entry.trim().replace(/^'|'$/g, "")).filter(Boolean);
}

// ---------------------------------------------------------------------------
// treasury_summary
// ---------------------------------------------------------------------------

export interface SummaryRow {
  chainId: number;
  asOf: string;
  asOfStatus: AsOfStatus | "";
  anchorBlock: number | null;
  publishedTokens: number | null;
  servedTokens: number | null;
  carriedTokens: number | null;
  walletsTracked: number | null;
  walletsActive: number | null;
  tokensHeld: number | null;
  positions: number | null;
  pricedTokens: number | null;
  listedTokens: number | null;
  unverifiedTokens: number | null;
  hiddenSpamTokens: number | null;
  hiddenRetiredTokens: number | null;
  gnoUnits: number | null;
  gnoUnitsExLtd: number | null;
  navUsd: number | null;
  navUsdExLtd: number | null;
  oldestPriceDate: string;
  hubLatestDate: string;
}

export function parseSummary(ds?: RowDataset): SummaryRow[] {
  return rowsToObjects(ds).flatMap<SummaryRow>((row) => {
    const chainId = num(row.chain_id);
    if (chainId === null) return [];
    return [{
      chainId,
      asOf: day(row.as_of),
      asOfStatus: asOfStatusOf(row.as_of_status),
      anchorBlock: num(row.anchor_block),
      publishedTokens: num(row.published_tokens),
      servedTokens: num(row.served_tokens),
      carriedTokens: num(row.carried_tokens),
      walletsTracked: num(row.wallets_tracked),
      walletsActive: num(row.wallets_active),
      tokensHeld: num(row.tokens_held),
      positions: num(row.positions),
      pricedTokens: num(row.priced_tokens),
      listedTokens: num(row.listed_tokens),
      unverifiedTokens: num(row.unverified_tokens),
      hiddenSpamTokens: num(row.hidden_spam_tokens),
      hiddenRetiredTokens: num(row.hidden_retired_tokens),
      gnoUnits: num(row.gno_units),
      gnoUnitsExLtd: num(row.gno_units_ex_ltd),
      navUsd: num(row.nav_usd),
      navUsdExLtd: num(row.nav_usd_ex_ltd),
      oldestPriceDate: day(row.oldest_price_date),
      hubLatestDate: day(row.hub_latest_date),
    }];
  }).sort((a, b) => a.chainId - b.chainId);
}

// ---------------------------------------------------------------------------
// Token positions: treasury_holdings, treasury_wallet_positions,
// treasury_token_detail. One shape; columns a dataset lacks read as null.
// ---------------------------------------------------------------------------

export interface HoldingRow {
  /** `${chainId}:${token}` — stable identity across renders. */
  key: string;
  chainId: number;
  token: string;
  /** Sanitized on-chain symbol (UNTRUSTED). "" when none is legible. */
  symbol: string;
  /** Sanitized on-chain name (UNTRUSTED), capped at NAME_MAX. */
  name: string;
  /** Reviewed registry symbol (trusted). "" for tokens outside the registry. */
  registrySymbol: string;
  assetKey: string;
  assetClass: AssetClass;
  decimals: number | null;
  metadataStatus: string;
  tokenClass: TokenClass;
  spamReason: SpamReason;
  walletsHolding: number | null;
  balanceRaw: string;
  units: number | null;
  unitsExLtd: number | null;
  supplyShare: number | null;
  symbolCollisions: number | null;
  /** Share of the treasury's own position (wallet positions only). */
  treasuryShare: number | null;
  priceUsd: number | null;
  priceDate: string;
  priceSource: PriceSource;
  valueUsd: number | null;
  valueUsdExLtd: number | null;
  /** Whether the dataset carries the `*_ex_ltd` companions at all. Wallet-level
   * datasets do not — there the Ltd filter drops whole wallets instead. */
  hasExLtd: boolean;
  spotEligible: boolean;
  tokenDate: string;
  asOf: string;
}

function holdingFrom(row: Row, hasExLtd: boolean): HoldingRow | null {
  const chainId = num(row.chain_id);
  const token = address(row.token_address);
  // The address IS the identity: a row without one cannot be valued, linked
  // or deduplicated. Both are grain keys upstream, so this only fires on a
  // malformed dataset.
  if (chainId === null || !token) return null;
  return {
    key: `${chainId}:${token}`,
    chainId,
    token,
    symbol: sanitizeSymbol(row.symbol),
    name: sanitizeText(row.name, NAME_MAX),
    registrySymbol: registryText(row.registry_symbol),
    assetKey: registryText(row.asset_key),
    assetClass: assetClassOf(row.asset_class),
    decimals: num(row.decimals),
    metadataStatus: text(row.metadata_status),
    tokenClass: tokenClassOf(row.token_class),
    spamReason: spamReasonOf(row.spam_reason),
    walletsHolding: num(row.wallets_holding),
    balanceRaw: rawInteger(row.balance_total_raw),
    units: num(row.balance_units),
    unitsExLtd: hasExLtd ? num(row.balance_units_ex_ltd) : null,
    supplyShare: num(row.supply_share),
    symbolCollisions: num(row.symbol_collisions),
    treasuryShare: num(row.treasury_share),
    priceUsd: num(row.price_usd),
    priceDate: day(row.price_date),
    priceSource: priceSourceOf(row.price_source),
    valueUsd: num(row.value_usd),
    valueUsdExLtd: hasExLtd ? num(row.value_usd_ex_ltd) : null,
    hasExLtd,
    spotEligible: flag(row.spot_eligible),
    tokenDate: day(row.token_date),
    asOf: day(row.as_of),
  };
}

/** treasury_holdings / treasury_wallet_positions. */
export function parseHoldings(ds?: RowDataset): HoldingRow[] {
  const hasExLtd = Boolean(ds?.columns.includes("value_usd_ex_ltd"));
  return rowsToObjects(ds).flatMap((row) => {
    const parsed = holdingFrom(row, hasExLtd);
    return parsed ? [parsed] : [];
  });
}

export interface SiblingToken {
  chainId: number;
  token: string;
}

export interface TokenDetailRow extends HoldingRow {
  anchorBlock: number | null;
  /** The same registry asset on other chains ("chain:address" on the wire). */
  siblings: SiblingToken[];
}

/** treasury_token_detail (single row). */
export function parseTokenDetail(ds?: RowDataset): TokenDetailRow | null {
  const [row] = rowsToObjects(ds);
  if (!row) return null;
  const base = holdingFrom(row, false);
  if (!base) return null;
  const siblings = stringList(row.sibling_tokens).flatMap<SiblingToken>((entry) => {
    const sep = entry.indexOf(":");
    if (sep <= 0) return [];
    const chainId = num(entry.slice(0, sep));
    const token = address(entry.slice(sep + 1));
    if (chainId === null || !token) return [];
    if (chainId === base.chainId && token === base.token) return [];
    return [{ chainId, token }];
  });
  return { ...base, anchorBlock: num(row.anchor_block), siblings };
}

// ---------------------------------------------------------------------------
// Wallets: treasury_by_wallet and treasury_wallet_detail.
// ---------------------------------------------------------------------------

export interface WalletRow {
  chainId: number;
  wallet: string;
  /** Community label (registry-trusted, still sanitized). "" when unlabelled. */
  label: string;
  /** Attribution for the label; shown in a tooltip, never instead of it. */
  labelSource: string;
  isLtd: boolean;
  tokensHeld: number | null;
  pricedPositions: number | null;
  unpricedPositions: number | null;
  hiddenPositions: number | null;
  gnoUnits: number | null;
  navUsd: number | null;
  asOf: string;
  asOfStatus: AsOfStatus | "";
  anchorBlock: number | null;
}

export function parseWallets(ds?: RowDataset): WalletRow[] {
  return rowsToObjects(ds).flatMap<WalletRow>((row) => {
    const chainId = num(row.chain_id);
    const wallet = address(row.wallet_address);
    if (chainId === null || !wallet) return [];
    return [{
      chainId,
      wallet,
      label: sanitizeText(row.wallet_label, LABEL_MAX),
      labelSource: sanitizeText(row.label_source, SOURCE_MAX),
      isLtd: flag(row.is_ltd),
      tokensHeld: num(row.tokens_held),
      pricedPositions: num(row.priced_positions),
      unpricedPositions: num(row.unpriced_positions),
      hiddenPositions: num(row.hidden_positions),
      gnoUnits: num(row.gno_units),
      navUsd: num(row.nav_usd),
      asOf: day(row.as_of),
      asOfStatus: asOfStatusOf(row.as_of_status),
      anchorBlock: num(row.anchor_block),
    }];
  });
}

// ---------------------------------------------------------------------------
// treasury_history — ONE fan-out dataset, `grain` in chain | wallet | token.
// ---------------------------------------------------------------------------

export interface HistoryRow {
  grain: HistoryGrain;
  chainId: number;
  bucket: string;
  bucketDate: string;
  wallet: string;
  walletLabel: string;
  isLtd: boolean;
  token: string;
  registrySymbol: string;
  assetKey: string;
  assetClass: AssetClass;
  tokenClass: TokenClass;
  units: number | null;
  unitsExLtd: number | null;
  priceUsd: number | null;
  priceDate: string;
  navUsd: number | null;
  navUsdExLtd: number | null;
  gnoUnits: number | null;
  gnoUnitsExLtd: number | null;
  walletsHolding: number | null;
  tokensHeld: number | null;
  positions: number | null;
  pricedTokens: number | null;
  listedTokens: number | null;
  unverifiedTokens: number | null;
  hiddenSpamTokens: number | null;
}

export function parseHistory(ds?: RowDataset): HistoryRow[] {
  return rowsToObjects(ds).flatMap<HistoryRow>((row) => {
    const grainRaw = text(row.grain);
    if (!(HISTORY_GRAINS as readonly string[]).includes(grainRaw)) return [];
    const chainId = num(row.chain_id);
    const month = bucket(row.bucket);
    if (chainId === null || !month) return [];
    const grain = grainRaw as HistoryGrain;
    const wallet = address(row.wallet_address);
    const token = address(row.token_address);
    // A wallet-grain row without its wallet, or a token-grain row without its
    // token, cannot be attributed; dropping it beats drawing it as "Other".
    if (grain === "wallet" && !wallet) return [];
    if (grain === "token" && !token) return [];
    return [{
      grain,
      chainId,
      bucket: month,
      bucketDate: day(row.bucket_date),
      wallet,
      walletLabel: sanitizeText(row.wallet_label, LABEL_MAX),
      isLtd: flag(row.is_ltd),
      token,
      registrySymbol: registryText(row.registry_symbol),
      assetKey: registryText(row.asset_key),
      assetClass: assetClassOf(row.asset_class),
      tokenClass: tokenClassOf(row.token_class),
      units: num(row.balance_units),
      unitsExLtd: num(row.balance_units_ex_ltd),
      priceUsd: num(row.price_usd),
      priceDate: day(row.price_date),
      navUsd: num(row.nav_usd),
      navUsdExLtd: num(row.nav_usd_ex_ltd),
      gnoUnits: num(row.gno_units),
      gnoUnitsExLtd: num(row.gno_units_ex_ltd),
      walletsHolding: num(row.wallets_holding),
      tokensHeld: num(row.tokens_held),
      positions: num(row.positions),
      pricedTokens: num(row.priced_tokens),
      listedTokens: num(row.listed_tokens),
      unverifiedTokens: num(row.unverified_tokens),
      hiddenSpamTokens: num(row.hidden_spam_tokens),
    }];
  });
}

// ---------------------------------------------------------------------------
// Month coverage: treasury_history_coverage, treasury_wallet_months,
// treasury_token_months (same columns; the entity ones are chain-pinned).
// ---------------------------------------------------------------------------

export interface CoverageRow {
  chainId: number;
  bucket: string;
  rawMonthEnd: string;
  bucketDate: string;
  publishedTokens: number | null;
  servedTokens: number | null;
  carriedTokens: number | null;
  unservedTokens: number | null;
  unservedRegistryTokens: number | null;
  /** Registry-trusted symbols of priced tokens that were not served. */
  unservedRegistrySymbols: string[];
  status: CoverageStatus;
}

export function parseCoverage(ds?: RowDataset): CoverageRow[] {
  return rowsToObjects(ds).flatMap<CoverageRow>((row) => {
    const chainId = num(row.chain_id);
    const month = bucket(row.bucket);
    if (chainId === null || !month) return [];
    return [{
      chainId,
      bucket: month,
      rawMonthEnd: day(row.raw_month_end),
      bucketDate: day(row.bucket_date),
      publishedTokens: num(row.published_tokens),
      servedTokens: num(row.served_tokens),
      carriedTokens: num(row.carried_tokens),
      unservedTokens: num(row.unserved_tokens),
      unservedRegistryTokens: num(row.unserved_registry_tokens),
      unservedRegistrySymbols: stringList(row.unserved_registry_symbols)
        .map(registryText)
        .filter(Boolean),
      status: coverageStatusOf(row.status),
    }];
  });
}

// ---------------------------------------------------------------------------
// Wallet entity bundle extras.
// ---------------------------------------------------------------------------

export interface WalletSeriesRow {
  chainId: number;
  bucket: string;
  bucketDate: string;
  token: string;
  registrySymbol: string;
  assetKey: string;
  assetClass: AssetClass;
  tokenClass: TokenClass;
  units: number | null;
  priceUsd: number | null;
  priceDate: string;
  valueUsd: number | null;
}

export function parseWalletSeries(ds?: RowDataset): WalletSeriesRow[] {
  return rowsToObjects(ds).flatMap<WalletSeriesRow>((row) => {
    const chainId = num(row.chain_id);
    const month = bucket(row.bucket);
    const token = address(row.token_address);
    if (chainId === null || !month || !token) return [];
    return [{
      chainId,
      bucket: month,
      bucketDate: day(row.bucket_date),
      token,
      registrySymbol: registryText(row.registry_symbol),
      assetKey: registryText(row.asset_key),
      assetClass: assetClassOf(row.asset_class),
      tokenClass: tokenClassOf(row.token_class),
      units: num(row.balance_units),
      priceUsd: num(row.price_usd),
      priceDate: day(row.price_date),
      valueUsd: num(row.value_usd),
    }];
  });
}

export interface WalletChainRow {
  chainId: number;
  wallet: string;
  /** The address is in the census for this chain at all. */
  tracked: boolean;
  hasPositions: boolean;
  tokensHeld: number | null;
  navUsd: number | null;
  asOf: string;
}

export function parseWalletChains(ds?: RowDataset): WalletChainRow[] {
  return rowsToObjects(ds).flatMap<WalletChainRow>((row) => {
    const chainId = num(row.chain_id);
    if (chainId === null) return [];
    return [{
      chainId,
      wallet: address(row.wallet_address),
      tracked: flag(row.tracked),
      hasPositions: flag(row.has_positions),
      tokensHeld: num(row.tokens_held),
      navUsd: num(row.nav_usd),
      asOf: day(row.as_of),
    }];
  }).sort((a, b) => a.chainId - b.chainId);
}

// ---------------------------------------------------------------------------
// Token entity bundle extras.
// ---------------------------------------------------------------------------

export interface HolderRow {
  chainId: number;
  wallet: string;
  label: string;
  labelSource: string;
  isLtd: boolean;
  balanceRaw: string;
  units: number | null;
  valueUsd: number | null;
  treasuryShare: number | null;
}

export function parseHolders(ds?: RowDataset): HolderRow[] {
  return rowsToObjects(ds).flatMap<HolderRow>((row) => {
    const chainId = num(row.chain_id);
    const wallet = address(row.wallet_address);
    if (chainId === null || !wallet) return [];
    return [{
      chainId,
      wallet,
      label: sanitizeText(row.wallet_label, LABEL_MAX),
      labelSource: sanitizeText(row.label_source, SOURCE_MAX),
      isLtd: flag(row.is_ltd),
      balanceRaw: rawInteger(row.balance_total_raw),
      units: num(row.balance_units),
      valueUsd: num(row.value_usd),
      treasuryShare: num(row.treasury_share),
    }];
  });
}

export interface HolderSeriesRow {
  chainId: number;
  bucket: string;
  bucketDate: string;
  wallet: string;
  label: string;
  isLtd: boolean;
  units: number | null;
  valueUsd: number | null;
}

export function parseHolderSeries(ds?: RowDataset): HolderSeriesRow[] {
  return rowsToObjects(ds).flatMap<HolderSeriesRow>((row) => {
    const chainId = num(row.chain_id);
    const month = bucket(row.bucket);
    const wallet = address(row.wallet_address);
    if (chainId === null || !month || !wallet) return [];
    return [{
      chainId,
      bucket: month,
      bucketDate: day(row.bucket_date),
      wallet,
      label: sanitizeText(row.wallet_label, LABEL_MAX),
      isLtd: flag(row.is_ltd),
      units: num(row.balance_units),
      valueUsd: num(row.value_usd),
    }];
  });
}

export interface PriceRow {
  day: string;
  priceSymbol: string;
  priceUsd: number | null;
  /** Registry role of the price window (e.g. "priced", "retired_mirror"). */
  role: string;
}

export function parsePriceHistory(ds?: RowDataset): PriceRow[] {
  return rowsToObjects(ds).flatMap<PriceRow>((row) => {
    const d = day(row.day);
    if (!d) return [];
    return [{
      day: d,
      priceSymbol: registryText(row.price_symbol),
      priceUsd: num(row.price_usd),
      role: sanitizeText(row.role, REGISTRY_TEXT_MAX),
    }];
  }).sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
}
