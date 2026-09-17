// Chain-state token overlay: symbol / decimals / name read live over RPC for
// the tokens the state indexer never catalogued (68 of ~3,400), patched in by
// `load_pools_token_metadata`.
//
// THE POINT OF THIS MODULE IS PROVENANCE, not convenience. An indexer value is
// verified at a pinned finalized block with a publication record behind it; an
// overlay value is current chain state with neither. So:
//
//   - the indexer ALWAYS wins. An overlay value may only fill a hole.
//   - anything sourced from the overlay carries a marker and says which block
//     it was read at, so it can never be mistaken for a verified figure.
//   - a token that could not be read is ABSENT from the map — never present
//     with a null symbol — and keeps rendering as its short address.
//
// Mirrors `_build_token_overlay` / `TokenMeta.as_overlay` in
// src/cerebro_mcp/tools/visualization/pools_explorer.py + token_rpc.py.

import { sanitizeSymbol } from "../../shared/TokenIdentity";
import { shortAddr } from "../../../utils/format";

/** One token's chain-state metadata. `symbol` / `name` are null (never "")
 * when the call reverted or returned something undecodable. */
export interface TokenOverlayEntry {
  symbol: string | null;
  name: string | null;
  decimals: number | null;
  block_number: number;
  /** "string" (standard ERC-20), "bytes32" (the older fixed-width variant) or
   * "absent" — recorded because a bytes32 decode went through a fallback. */
  encoding: string;
  source: "rpc";
}

/** Counters for one `load_pools_token_metadata` call. Every field is optional:
 * the initial view state carries `{}`, and the no-tokens path carries only
 * `requested` / `resolved`. */
export interface TokenOverlayStats {
  requested?: number;
  resolved?: number;
  from_cache?: number;
  fetched?: number;
  unreadable?: number;
  truncated?: boolean;
  block_number?: number | null;
  error?: string | null;
  source?: string;
}

export type TokenOverlay = Record<string, TokenOverlayEntry>;

/** Where a rendered label or figure came from. */
export type ValueSource = "indexer" | "overlay" | "none";

export function normalizeAddress(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

/** Overlay entry for an address, keyed case-insensitively. */
export function overlayEntry(
  overlay: TokenOverlay | undefined,
  address: unknown,
): TokenOverlayEntry | undefined {
  if (!overlay) return undefined;
  const key = normalizeAddress(address);
  if (!key) return undefined;
  return overlay[key] ?? overlay[String(address ?? "")] ?? undefined;
}

export interface LabelResolution {
  /** Display text: symbol, or the short address when nothing is known. */
  text: string;
  /** "indexer" | "overlay" | "none" — "none" means the short address. */
  source: ValueSource;
  /** Block the overlay value was read at; null for any other source. */
  blockNumber: number | null;
}

/**
 * Three-way label precedence: indexer symbol > overlay symbol > short address.
 *
 * `indexerSymbol` is whatever the dataset column carries (sanitized here, as
 * symbols are attacker-authored display text). An overlay symbol can only be
 * reached when the indexer has none — it never overwrites one.
 */
export function resolveTokenLabel(
  address: unknown,
  indexerSymbol: unknown,
  overlay?: TokenOverlay,
): LabelResolution {
  const indexerClean = sanitizeSymbol(indexerSymbol);
  if (indexerClean) return { text: indexerClean, source: "indexer", blockNumber: null };
  const entry = overlayEntry(overlay, address);
  const overlayClean = sanitizeSymbol(entry?.symbol);
  if (entry && overlayClean) {
    return { text: overlayClean, source: "overlay", blockNumber: entry.block_number ?? null };
  }
  return { text: shortAddr(String(address ?? "")) || "—", source: "none", blockNumber: null };
}

export interface DecimalsResolution {
  decimals: number | null;
  source: ValueSource;
  blockNumber: number | null;
}

/**
 * Indexer decimals win; an overlay value may only fill a hole. `-1` is the
 * indexer's "not observed" sentinel in the `asset_decimals` array and is NOT a
 * real scale factor.
 */
export function resolveDecimals(
  indexerDecimals: unknown,
  address: unknown,
  overlay?: TokenOverlay,
): DecimalsResolution {
  const indexer = finiteDecimals(indexerDecimals);
  if (indexer !== null) return { decimals: indexer, source: "indexer", blockNumber: null };
  const entry = overlayEntry(overlay, address);
  const fromChain = finiteDecimals(entry?.decimals);
  if (entry && fromChain !== null) {
    return { decimals: fromChain, source: "overlay", blockNumber: entry.block_number ?? null };
  }
  return { decimals: null, source: "none", blockNumber: null };
}

/** A usable ERC-20 scale exponent: an integer in 0..77 (10**78 overflows a
 * uint256, so anything beyond cannot be a real scaling factor). */
function finiteDecimals(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
  return n >= 0 && n <= 77 ? n : null;
}

/** Tooltip for a label read off the chain. */
export function overlayLabelTitle(blockNumber: number | null): string {
  const at = blockNumber ? ` at block ${blockNumber.toLocaleString("en-US")}` : "";
  return `Symbol read from chain state${at}, not from a verified snapshot`;
}

/** Tooltip for a figure scaled by chain-state decimals. */
export function overlayAmountTitle(blockNumber: number | null): string {
  const at = blockNumber ? ` at block ${blockNumber.toLocaleString("en-US")}` : "";
  return `Adjusted with decimals read from chain state${at}, not from a verified snapshot`;
}

// ---------------------------------------------------------------------------
// Warning codes
// ---------------------------------------------------------------------------

/** `token_rpc_unavailable` when the read could not happen at all;
 * `token_overlay_pending` when more tokens than the per-call cap were offered
 * and a retry resolves more. Mirrors `_build_token_overlay`'s own branch — the
 * codes travel on the tool PAYLOAD, not in `view_state.warnings`, so the app
 * derives them from the stats it does keep. */
export function overlayWarningCodes(stats?: TokenOverlayStats | null): string[] {
  if (!stats) return [];
  if (stats.error) return ["token_rpc_unavailable"];
  if (stats.truncated) return ["token_overlay_pending"];
  return [];
}

// ---------------------------------------------------------------------------
// Visible-token accounting
// ---------------------------------------------------------------------------

/** Dataset columns that hold a token address, paired with the column carrying
 * the INDEXER's symbol for it. Mirrors the backend TOKEN_COLUMN_RE; narrow on
 * purpose, so a pool address can never be swept into a token lookup. */
const TOKEN_COLUMNS: ReadonlyArray<{ address: string; symbol: string | null }> = [
  { address: "token0", symbol: "token0_symbol" },
  { address: "token1", symbol: "token1_symbol" },
  { address: "token_address", symbol: "symbol" },
  { address: "assets", symbol: "asset_symbols" },
  { address: "reserve_tokens", symbol: "asset_symbols" },
  { address: "counter_tokens", symbol: "counter_labels" },
];

/** A `counter_labels` entry falls back to a short address server-side, so an
 * elision mark means "no symbol", not a symbol. Same rule cells.tsx applies. */
const SHORT_ADDRESS_LABEL_RE = /…|\.\.\./;

/** Rows scanned per dataset. The directory pages 100 rows at a time and the
 * biggest attached preview is a few hundred; the cap only exists so a future
 * wide dataset cannot turn a status line into a scan of 10k rows. */
const ROW_SCAN_CAP = 2_000;

export interface VisibleTokens {
  /** Distinct lowercase addresses, sorted — the overlay's request set. */
  addresses: string[];
  /** Addresses the INDEXER labels (a non-empty symbol in some visible row). */
  indexerLabelled: Set<string>;
}

interface ScanDataset {
  columns: string[];
  rows: unknown[][];
}

/**
 * Distinct token addresses across the datasets on screen, and which of them
 * the indexer itself labels. Feeds both the coverage note and the auto-load
 * dedupe key — the key must be derived from what is VISIBLE, never from the
 * overlay itself, or applying a patch would re-trigger the call that made it.
 */
export function collectVisibleTokens(datasets: ScanDataset[]): VisibleTokens {
  const addresses = new Set<string>();
  const indexerLabelled = new Set<string>();
  for (const dataset of datasets) {
    const index = new Map(dataset.columns.map((name, position) => [name, position]));
    const pairs = TOKEN_COLUMNS
      .filter((pair) => index.has(pair.address))
      .map((pair) => ({
        address: index.get(pair.address)!,
        symbol: pair.symbol !== null && index.has(pair.symbol) ? index.get(pair.symbol)! : -1,
      }));
    if (pairs.length === 0) continue;
    const rows = dataset.rows.length > ROW_SCAN_CAP ? dataset.rows.slice(0, ROW_SCAN_CAP) : dataset.rows;
    for (const row of rows) {
      for (const pair of pairs) {
        const rawAddresses = toArray(row[pair.address]);
        const rawSymbols = pair.symbol >= 0 ? toArray(row[pair.symbol]) : [];
        rawAddresses.forEach((value, position) => {
          const address = normalizeAddress(value);
          if (!/^0x[0-9a-f]{40}$/.test(address)) return;
          addresses.add(address);
          const symbol = sanitizeSymbol(rawSymbols[position]);
          if (symbol && !SHORT_ADDRESS_LABEL_RE.test(symbol)) indexerLabelled.add(address);
        });
      }
    }
  }
  return { addresses: [...addresses].sort(), indexerLabelled };
}

function toArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined || value === "") return [];
  return [value];
}

export interface OverlayCoverage {
  /** Distinct token addresses on screen. */
  visible: number;
  /** Labelled from a verified indexer snapshot. */
  fromIndexer: number;
  /** Labelled from current chain state (the overlay filled a hole). */
  fromChain: number;
  /** Neither: still rendered as a short address. */
  unlabelled: number;
}

/** How the tokens on screen are labelled. Indexer first — an overlay entry for
 * a token the indexer already names is not "labelled from chain state", since
 * the indexer value is the one that renders. */
export function overlayCoverage(
  visible: VisibleTokens,
  overlay?: TokenOverlay,
): OverlayCoverage {
  let fromIndexer = 0;
  let fromChain = 0;
  for (const address of visible.addresses) {
    if (visible.indexerLabelled.has(address)) {
      fromIndexer += 1;
      continue;
    }
    if (sanitizeSymbol(overlayEntry(overlay, address)?.symbol)) fromChain += 1;
  }
  return {
    visible: visible.addresses.length,
    fromIndexer,
    fromChain,
    unlabelled: visible.addresses.length - fromIndexer - fromChain,
  };
}

// ---------------------------------------------------------------------------
// Auto-load dedupe key
// ---------------------------------------------------------------------------

/** FNV-1a over the joined address set. Only needs to be stable and cheap. */
export function hashAddresses(addresses: readonly string[]): string {
  let hash = 0x811c9dc5;
  const text = addresses.join(",");
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${addresses.length}-${hash.toString(36)}`;
}

/**
 * Identity of one overlay request: the load scope plus a hash of the token set
 * it would resolve, plus the table-page epoch (a "Load more" reveals rows the
 * preview never carried). Re-rendering does not change it; a section switch, an
 * entity change, a deferred group landing or a page append does.
 *
 * Deliberately independent of `token_overlay` / `token_overlay_stats`: keying
 * on the answer would make every patch trigger the next call.
 */
export function overlayScopeKey(
  scopeId: string,
  addresses: readonly string[],
  pageEpoch: number,
): string {
  return `${scopeId}|${hashAddresses(addresses)}|${pageEpoch}`;
}
