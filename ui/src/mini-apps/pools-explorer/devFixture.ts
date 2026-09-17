// Vite dev-mode fixture (`make dev` → pools-explorer.html). Descriptor
// columns are the FROZEN projections in types.ts (DATASET_COLUMNS) — every
// row is built as a name-keyed object and projected through the column list,
// and a test pins fixture columns against the contract. A fixture column the
// server does not emit would hide real bugs (lesson learned on Governance).
//
// Dev-only overrides: `?section=pools|tokens|coverage` renders that section
// from the fixture and `?entity=pool:<addr>` / `?entity=token:<addr>` opens an
// entity (pure mock mode has no server to apply a switch through). Tests
// import MOCK_PAYLOAD under a default URL, so its section is "overview".
//
// Pools: P_FULL (Swapr CRC20/sDAI, unresolved token0, ONE full-range range,
// raw price), P_WETH (Uniswap WETH/WXDAI 0.3%, 20 inner ranges plus the two
// boundary segments, 90-day history/reserves/fees, heatmap), P_USDC
// (USDC.e/WXDAI 0.05%, spacing 10, decimals 6/18 adjusted price, ±887270 full
// range), P_ZERO (GNO/WXDAI 1%, liquidity 0, state-only), P_STATE (live but
// unprobed), P_BAL (Balancer v2 three-token pool, reserves only), plus twelve
// generated directory rows.

import type { DatasetDescriptor, MiniAppPayload } from "../shared/miniAppTypes";
import { SECTION_GROUPS } from "./model/datasetGroups";
import { isFullRange, shareWithinWindow, tickToPrice, type ProfileRange } from "./model/liquidityProfile";
import { EMPTY_DRAFT } from "./state/toolArgs";
import {
  DATASET_COLUMNS, type DatasetKey, type PlxEntityType, type PlxListSection, type PlxSection,
  type PoolsExplorerViewState,
} from "./types";

export const DEV_AS_OF = "2026-09-16";
export const DEV_ANCHOR_BLOCK = 43_210_450;
const DEV_ANCHOR_TS = "2026-09-16T23:59:55Z";
const HISTORY_DAYS = 90;

// ---- helpers ----------------------------------------------------------------

function isoDay(offsetDays: number, from = DEV_AS_OF): string {
  const base = new Date(`${from}T00:00:00Z`);
  base.setUTCDate(base.getUTCDate() + offsetDays);
  return base.toISOString().slice(0, 10);
}

/** Exact decimal string of an integer-valued double (never exponent form). */
function bigStr(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return BigInt(Math.round(Math.max(0, value))).toString();
}

/** Deterministic pseudo-noise in [-1, 1] — no Math.random, so renders repro. */
function noise(seed: number): number {
  const x = Math.sin(seed * 12.9898 + 78.233) * 43758.5453;
  return (x - Math.floor(x)) * 2 - 1;
}

function project(key: DatasetKey, row: Record<string, unknown>): unknown[] {
  const columns = DATASET_COLUMNS[key] as readonly string[];
  for (const name of Object.keys(row)) {
    if (!columns.includes(name)) throw new Error(`devFixture: ${key} has no column ${name}`);
  }
  return columns.map((name) => (name in row ? row[name] : null));
}

function descriptor(
  key: DatasetKey,
  rows: unknown[][],
  extra: { rowCount?: number; pageToken?: string; failure?: string; codes?: string[] } = {},
): DatasetDescriptor {
  const columns = DATASET_COLUMNS[key] as readonly string[];
  const rowCount = extra.rowCount ?? rows.length;
  return {
    key,
    title: key.split("_").join(" "),
    sql: "-- development fixture",
    database: "rpc_state_indexer",
    columns: columns.map((name) => ({ name, type: "Unknown" })),
    stats: {
      row_count: rowCount, rows_returned: rows.length, mode: "exact_capped", source_rows: rowCount,
      row_cap: 10000, truncated: false, warnings: [],
    },
    preview_rows: rows,
    ...(extra.pageToken ? { page_token: extra.pageToken } : {}),
    provenance: {
      coverage: {
        actual_start: "2022-12-12", actual_end: DEV_AS_OF, mode: "publication_verified",
        warning_codes: extra.codes ?? (extra.failure ? ["query_failed"] : []),
        ...(extra.failure ? { error: extra.failure } : {}),
      },
    },
  };
}

// ---- tokens -----------------------------------------------------------------

export const WETH = "0x6a023ccd1ff6f2045c3309768ead9e68f978f6e1";
export const WXDAI = "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d";
export const USDCE = "0x2a22f9c3b484c3629090feed35f17ff8f88f76f0";
export const SDAI = "0xaf204776c7245bf4147c2612bf6e5972ee483701";
export const GNO = "0x9c58bacc331c9aa871afd802db6379a98e80cedb";
export const COW = "0x177127622c4a00f3d409b75571e12cb3c8973d3c";
export const EURE = "0xcb444e90d8198415266c6a2724b7900fb12fc56e";
/** A Circles CRC20 token — UNRESOLVED metadata (no symbol, no decimals). */
export const CRC = "0x3ab2f8b8d9cb4f6a3b8e3d8f9a1c2b3d4e5f6a7b";
/** A second Circles CRC20 — unresolved by the indexer, but READABLE over
 * RPC, so the chain-state overlay labels it (and supplies its decimals). */
export const CRC2 = "0x51c0a3f6bd1e2a9d4b7c8e1f2a3b4c5d6e7f8091";

interface TokenMeta { symbol: string | null; name: string | null; decimals: number | null }
const TOKEN_META: Record<string, TokenMeta> = {
  [WETH]: { symbol: "WETH", name: "Wrapped Ether on xDai", decimals: 18 },
  [WXDAI]: { symbol: "WXDAI", name: "Wrapped XDAI", decimals: 18 },
  [USDCE]: { symbol: "USDC.e", name: "Bridged USDC (Gnosis)", decimals: 6 },
  [SDAI]: { symbol: "sDAI", name: "Savings xDAI", decimals: 18 },
  [GNO]: { symbol: "GNO", name: "Gnosis", decimals: 18 },
  [COW]: { symbol: "COW", name: "CoW Protocol Token", decimals: 18 },
  [EURE]: { symbol: "EURe", name: "Monerium EUR emoney", decimals: 18 },
  [CRC]: { symbol: null, name: null, decimals: null },
  [CRC2]: { symbol: null, name: null, decimals: null },
};

function meta(token: string): TokenMeta {
  return TOKEN_META[token] ?? { symbol: null, name: null, decimals: null };
}

/** Block the chain-state read happened at — AHEAD of the pinned publication
 * anchor, which is the whole point: the overlay is current state, the datasets
 * are a verified snapshot. */
export const DEV_OVERLAY_BLOCK = DEV_ANCHOR_BLOCK + 8_412;

/**
 * `token_overlay`: chain-state metadata patched in by
 * `load_pools_token_metadata`, keyed by lowercase address. Covers the three
 * cases the UI has to keep apart:
 *
 *   USDCE — the indexer ALSO knows this token. The overlay carries the live
 *           symbol ("USDCe", as the contract actually returns it) which
 *           differs from the snapshot's "USDC.e"; the indexer value is the one
 *           that must render, unmarked.
 *   CRC2  — only the overlay knows it, WITH decimals, so its reserves become
 *           scalable — and the resulting figure must carry the chain marker
 *           rather than reading as a verified amount.
 *   CRC   — neither knows it. ABSENT from the map (never present with a null
 *           symbol), so it keeps rendering as a short address + "unresolved".
 */
export const TOKEN_OVERLAY: Record<string, {
  symbol: string | null; name: string | null; decimals: number | null;
  block_number: number; encoding: string; source: "rpc";
}> = {
  [USDCE]: {
    symbol: "USDCe", name: "USD//C on xDai", decimals: 6,
    block_number: DEV_OVERLAY_BLOCK, encoding: "string", source: "rpc",
  },
  [CRC2]: {
    symbol: "CRC", name: "Circles", decimals: 18,
    block_number: DEV_OVERLAY_BLOCK, encoding: "bytes32", source: "rpc",
  },
};

/** `token_overlay_stats` for the same read: one token answered from cache, one
 * was fetched, one (CRC) could not be read at all. */
export const TOKEN_OVERLAY_STATS = {
  requested: 3,
  resolved: 2,
  from_cache: 1,
  fetched: 1,
  unreadable: 1,
  truncated: false,
  block_number: DEV_OVERLAY_BLOCK,
  error: null,
  source: "rpc",
};

function shortLabel(token: string): string {
  return `${token.slice(0, 6)}…${token.slice(-4)}`;
}

function tokenLabelOf(token: string): string {
  return meta(token).symbol ?? shortLabel(token);
}

function adjusted(raw: number | null, t0: string, t1: string): number | null {
  const d0 = meta(t0).decimals;
  const d1 = meta(t1).decimals;
  if (raw === null || d0 === null || d1 === null) return null;
  return raw * 10 ** (d0 - d1);
}

// ---- pools ------------------------------------------------------------------

export const P_WETH = "0x0cf44132a7df09ba82d5c4010e73e151d31a42ae";
export const P_USDC = "0x8f31db2ff2b7b1e2c3d4e5f6a7b8c9d0e1f2a3b4";
export const P_FULL = "0xa1b2c3d4e5f60718293a4b5c6d7e8f9012345678";
export const P_ZERO = "0xb2c3d4e5f60718293a4b5c6d7e8f90123456789a";
export const P_STATE = "0xc3d4e5f60718293a4b5c6d7e8f90123456789ab1";
export const P_BAL = "0xd4e5f60718293a4b5c6d7e8f90123456789ab1c2";

interface PoolSeed {
  address: string;
  name: string;
  cls: "uniswap_v3" | "swapr_v3_algebra" | "balancer_v2" | "balancer_v3";
  fee: number | null;
  spacing: number | null;
  assets: string[];
  tick: number | null;
  /** Liquidity (float) — 0 for a dead pool; null for reserves-only. */
  liquidity: number | null;
  tickCount: number | null;
  probed: boolean;
  /** Raw reserves per asset. */
  reserves: number[];
  firstPublished: string;
  deploymentBlock: number;
  poolId?: string;
  /** Configured CL pool with NO published state row at as_of — the case
   * `has_state` exists to distinguish from "liquidity is zero". */
  noState?: boolean;
}

function fullRangeTick(spacing: number | null): number {
  return spacing === 10 ? 887_270 : 887_220;
}

export const POOL_SEEDS: PoolSeed[] = [
  {
    address: P_WETH, name: "Uniswap V3 WETH/WXDAI 0.3%", cls: "uniswap_v3", fee: 3000, spacing: 60,
    assets: [WETH, WXDAI], tick: 78_244, liquidity: 7.4e19, tickCount: 23, probed: true,
    reserves: [412.5e18, 1.032e24], firstPublished: "2023-09-25", deploymentBlock: 28_100_310,
  },
  {
    address: P_USDC, name: "Uniswap V3 USDC.e/WXDAI 0.05%", cls: "uniswap_v3", fee: 500, spacing: 10,
    assets: [USDCE, WXDAI], tick: 276_324, liquidity: 2.9e15, tickCount: 12, probed: true,
    reserves: [1.84e12, 1.79e24], firstPublished: "2023-09-25", deploymentBlock: 28_400_120,
  },
  {
    address: P_FULL, name: "Swapr V3 CRC/sDAI", cls: "swapr_v3_algebra", fee: 100, spacing: 60,
    assets: [CRC, SDAI], tick: -1_625, liquidity: 5.0e15, tickCount: 2, probed: true,
    reserves: [8.1e21, 6.4e21], firstPublished: "2025-02-11", deploymentBlock: 38_500_000,
  },
  {
    address: P_ZERO, name: "Uniswap V3 GNO/WXDAI 1%", cls: "uniswap_v3", fee: 10_000, spacing: 200,
    assets: [GNO, WXDAI], tick: 51_080, liquidity: 0, tickCount: 0, probed: false,
    reserves: [0, 0], firstPublished: "2023-09-25", deploymentBlock: 28_100_900,
  },
  {
    address: P_STATE, name: "Uniswap V3 WETH/GNO 0.3%", cls: "uniswap_v3", fee: 3000, spacing: 60,
    assets: [WETH, GNO], tick: -27_450, liquidity: 9.1e12, tickCount: null, probed: false,
    reserves: [0.21e18, 3.3e18], firstPublished: "2024-01-08", deploymentBlock: 31_700_400,
  },
  {
    address: P_BAL, name: "Balancer 33WETH-33GNO-33WXDAI", cls: "balancer_v2", fee: null, spacing: null,
    assets: [WETH, GNO, WXDAI], tick: null, liquidity: null, tickCount: null, probed: false,
    reserves: [61.2e18, 1_240e18, 152_000e18], firstPublished: "2022-12-12", deploymentBlock: 25_400_010,
    poolId: "0x66f33ae36dd80327744207a48122f874634b3ada000100000000000000000013",
  },
];

// Twelve generated rows: a mix of classes, probe states and Circles pools.
const GENERATED_SEEDS: PoolSeed[] = Array.from({ length: 12 }, (_, i) => {
  const kinds: PoolSeed["cls"][] = ["uniswap_v3", "swapr_v3_algebra", "swapr_v3_algebra", "balancer_v3"];
  const cls = kinds[i % kinds.length];
  const address = `0x${(0x10 + i).toString(16).padStart(2, "0")}${"e".repeat(38)}`.slice(0, 42);
  const circles = cls === "swapr_v3_algebra";
  const balancer = cls === "balancer_v3";
  const probed = !balancer && i % 3 === 0;
  // One configured CL pool has NO state row at all (the `has_state` case).
  const noState = i === 5;
  const liquidity = balancer || noState ? null : probed ? 2e19 * (1 + i) : i % 4 === 1 ? 0 : 4e12 * (1 + i);
  return {
    address,
    name: balancer ? `Balancer v3 pool #${i}` : circles ? `Swapr V3 CRC/sDAI #${i}` : `Uniswap V3 COW/WXDAI #${i}`,
    cls,
    fee: balancer ? null : circles ? 100 + i * 37 : 3000,
    spacing: balancer ? null : 60,
    assets: balancer ? [COW, GNO, WXDAI] : circles ? [i % 2 ? CRC : CRC2, SDAI] : [COW, WXDAI],
    tick: balancer ? null : circles ? -1400 - i * 90 : -23_000 + i * 40,
    liquidity,
    tickCount: balancer ? null : probed ? 6 + i : liquidity === 0 ? 0 : null,
    probed,
    reserves: balancer ? [1e21, 2e20, 3e22] : [1e21 * (1 + i), 2e21],
    firstPublished: i % 2 ? "2024-06-01" : "2025-03-15",
    deploymentBlock: 30_000_000 + i * 12_345,
    noState,
  };
});

const ALL_SEEDS = [...POOL_SEEDS, ...GENERATED_SEEDS];

/** The configured CL pool with NO published state row (`has_state = 0`). */
export const P_NOSTATE = GENERATED_SEEDS.find((seed) => seed.noState)!.address;

function feeBandOf(fee: number | null): string | null {
  if (fee === null) return null;
  if (fee <= 100) return "b100";
  if (fee <= 500) return "b500";
  if (fee <= 3000) return "b3000";
  if (fee <= 10_000) return "b10000";
  return "bhigh";
}

function familyOf(cls: PoolSeed["cls"]): "cl" | "reserves_only" {
  return cls === "uniswap_v3" || cls === "swapr_v3_algebra" ? "cl" : "reserves_only";
}

function unitsOf(raw: number, token: string): number | null {
  const decimals = meta(token).decimals;
  return decimals === null ? null : raw / 10 ** decimals;
}

function isLive(seed: PoolSeed): boolean {
  if (familyOf(seed.cls) === "cl") return (seed.liquidity ?? 0) > 0;
  return seed.reserves.some((value) => value > 0);
}

function directoryObject(seed: PoolSeed): Record<string, unknown> {
  const hasState = familyOf(seed.cls) === "cl" && !seed.noState;
  const t0 = seed.assets[0];
  const t1 = seed.assets[1];
  const priceRaw = seed.tick === null ? null : tickToPrice(seed.tick);
  const daysPublished = Math.round((Date.parse(`${DEV_AS_OF}T00:00:00Z`) - Date.parse(`${seed.firstPublished}T00:00:00Z`)) / 86_400_000) + 1;
  return {
    pool_address: seed.address,
    pool_name: seed.name,
    pool_class: seed.cls,
    pool_family: familyOf(seed.cls),
    n_assets: seed.assets.length,
    token0: seed.assets.length > 2 ? null : t0,
    token0_symbol: seed.assets.length > 2 ? null : meta(t0).symbol,
    token0_decimals: seed.assets.length > 2 ? null : meta(t0).decimals,
    token0_resolved: seed.assets.length > 2 ? 0 : meta(t0).symbol !== null ? 1 : 0,
    token0_label: seed.assets.length > 2 ? "" : tokenLabelOf(t0),
    token1: seed.assets.length > 2 ? null : t1,
    token1_symbol: seed.assets.length > 2 ? null : meta(t1).symbol,
    token1_decimals: seed.assets.length > 2 ? null : meta(t1).decimals,
    token1_resolved: seed.assets.length > 2 ? 0 : meta(t1).symbol !== null ? 1 : 0,
    token1_label: seed.assets.length > 2 ? "" : tokenLabelOf(t1),
    assets: seed.assets,
    // '' = symbol not observed, -1 = decimals not observed (never a real 0).
    asset_symbols: seed.assets.map((token) => meta(token).symbol ?? ""),
    asset_decimals: seed.assets.map((token) => meta(token).decimals ?? -1),
    as_of: hasState ? DEV_AS_OF : null,
    has_state: hasState ? 1 : 0,
    current_tick: hasState ? seed.tick : null,
    price_raw: hasState ? priceRaw : null,
    price_adjusted: hasState ? adjusted(priceRaw, t0, t1) : null,
    liquidity_raw: hasState && seed.liquidity !== null ? bigStr(seed.liquidity) : null,
    liquidity_float: hasState ? seed.liquidity : null,
    is_live: isLive(seed) ? 1 : 0,
    tick_count: hasState ? seed.tickCount : null,
    tick_spacing: seed.spacing,
    fee: seed.fee,
    fee_band: feeBandOf(seed.fee),
    ticks_probed: seed.probed ? 1 : 0,
    reserves_as_of: DEV_AS_OF,
    reserve0_raw: seed.assets.length > 2 ? null : bigStr(seed.reserves[0]),
    reserve1_raw: seed.assets.length > 2 ? null : bigStr(seed.reserves[1]),
    reserve0_units: seed.assets.length > 2 ? null : unitsOf(seed.reserves[0], t0),
    reserve1_units: seed.assets.length > 2 ? null : unitsOf(seed.reserves[1], t1),
    first_published: seed.firstPublished,
    last_published: DEV_AS_OF,
    days_published: daysPublished,
    deployment_block: seed.deploymentBlock,
    anchor_block: DEV_ANCHOR_BLOCK,
  };
}

function detailObject(seed: PoolSeed): Record<string, unknown> {
  const directory = directoryObject(seed);
  return {
    ...directory,
    entity_label: `${seed.cls} · ${shortLabel(seed.address)}`,
    days_live: isLive(seed) ? directory.days_published : 0,
    pool_id: seed.poolId ?? null,
    profile_available_from: seed.probed ? (seed.address === P_FULL ? "2025-02-11" : "2023-10-02") : null,
    // Paired arrays: the only reserve surface an N-asset Balancer pool has.
    reserve_tokens: seed.assets,
    reserve_raw: seed.reserves.map(bigStr),
  };
}

// ---- profiles ---------------------------------------------------------------

interface ProfileSpec { boundaries: number[]; liquidity: number[] }

const WETH_PROFILE: ProfileSpec = {
  boundaries: [
    -887_220, 77_400, 77_520, 77_640, 77_760, 77_880, 78_000, 78_060, 78_120, 78_180, 78_240,
    78_300, 78_360, 78_420, 78_480, 78_600, 78_720, 78_840, 78_960, 79_080, 79_200, 79_320, 887_220,
  ],
  liquidity: [
    1.2e18, 2.5e18, 4.1e18, 6.8e18, 1.1e19, 1.9e19, 2.6e19, 3.4e19, 4.8e19, 6.1e19, 7.4e19,
    6.9e19, 5.7e19, 4.2e19, 3.1e19, 2.2e19, 1.5e19, 9.0e18, 5.2e18, 3.0e18, 1.8e18, 1.2e18,
  ],
};

const USDC_PROFILE: ProfileSpec = {
  boundaries: [-887_270, 276_200, 276_250, 276_280, 276_300, 276_320, 276_330, 276_340, 276_360, 276_400, 276_450, 887_270],
  liquidity: [3.0e13, 1.1e14, 4.2e14, 9.8e14, 2.1e15, 2.9e15, 2.4e15, 1.3e15, 5.0e14, 1.4e14, 3.0e13],
};

const FULL_PROFILE: ProfileSpec = { boundaries: [-887_220, 887_220], liquidity: [5.0e15] };

const PROFILES: Record<string, ProfileSpec> = {
  [P_WETH]: WETH_PROFILE,
  [P_USDC]: USDC_PROFILE,
  [P_FULL]: FULL_PROFILE,
};

function profileRows(seed: PoolSeed, spec: ProfileSpec): unknown[][] {
  const current = seed.tick ?? 0;
  const t0 = seed.assets[0];
  const t1 = seed.assets[1];
  return spec.liquidity.map((liquidity, index) => {
    const lower = spec.boundaries[index];
    const upper = spec.boundaries[index + 1];
    const contains = current >= lower && current < upper;
    return project("pool_profile_at", {
      pool_address: seed.address,
      as_of: DEV_AS_OF,
      tick_lower: lower,
      tick_upper: upper,
      width_ticks: upper - lower,
      price_lower_raw: tickToPrice(lower),
      price_upper_raw: tickToPrice(upper),
      price_lower_adjusted: adjusted(tickToPrice(lower), t0, t1),
      price_upper_adjusted: adjusted(tickToPrice(upper), t0, t1),
      active_liquidity_raw: bigStr(liquidity),
      active_liquidity_float: liquidity,
      is_gap: liquidity > 0 ? 0 : 1,
      contains_current_tick: contains ? 1 : 0,
      is_full_range: isFullRange(lower, upper) ? 1 : 0,
      current_tick: current,
      distance_ticks: contains ? 0 : Math.min(Math.abs(current - lower), Math.abs(current - upper)),
      matches_state_liquidity: contains ? 1 : null,
      profile_source: "tick_window_recompute",
    });
  });
}

function concentrationRows(seed: PoolSeed, spec: ProfileSpec): unknown[][] {
  const current = seed.tick ?? 0;
  const ranges: ProfileRange[] = spec.liquidity.map((liquidity, index) => ({
    lower: spec.boundaries[index],
    upper: spec.boundaries[index + 1],
    liquidity,
    isGap: liquidity <= 0,
    containsCurrent: current >= spec.boundaries[index] && current < spec.boundaries[index + 1],
  }));
  const atCurrent = ranges.find((range) => range.containsCurrent)?.liquidity ?? null;
  const band = (name: string, ticks: number | null) => {
    const window = ticks === null
      ? { lo: -fullRangeTick(seed.spacing), hi: fullRangeTick(seed.spacing) }
      : { lo: current - ticks, hi: current + ticks };
    const inBand = ranges.filter((range) => range.upper > window.lo && range.lower < window.hi).length;
    return project("pool_profile_concentration", {
      band: name,
      band_ticks: ticks,
      tick_weighted_share: shareWithinWindow(ranges, window),
      ranges_in_band: inBand,
      liquidity_at_current_tick_float: atCurrent,
    });
  };
  return [band("1pct", 100), band("5pct", 488), band("10pct", 953), band("full_range", null)];
}

function tickRows(seed: PoolSeed, spec: ProfileSpec): unknown[][] {
  const current = seed.tick ?? 0;
  let previous = 0;
  const rows: unknown[][] = [];
  spec.boundaries.forEach((tick, index) => {
    const liquidity = index < spec.liquidity.length ? spec.liquidity[index] : 0;
    const net = liquidity - previous;
    previous = liquidity;
    const gross = Math.abs(net) + (index % 2 ? 2.5e17 : 0);
    rows.push(project("pool_ticks_at", {
      tick,
      liquidity_gross_raw: bigStr(gross),
      liquidity_gross_float: gross,
      liquidity_net_raw: net < 0 ? `-${bigStr(-net)}` : bigStr(net),
      liquidity_net_float: net,
      fee_growth_outside_0_raw: bigStr(1.4e30 + index * 3.7e27),
      fee_growth_outside_1_raw: bigStr(2.2e33 + index * 5.1e30),
      price_raw_at_tick: tickToPrice(tick),
      is_below_current: tick < current ? 1 : 0,
    }));
  });
  return rows;
}

// ---- histories --------------------------------------------------------------

function tickAt(seed: PoolSeed, daysAgo: number): number {
  const base = seed.tick ?? 0;
  return Math.round(base + 320 * Math.sin(daysAgo / 9) + 40 * noise(daysAgo + 3));
}

function liquidityAt(seed: PoolSeed, daysAgo: number): number {
  const base = seed.liquidity ?? 0;
  return base * (1 + 0.15 * Math.sin(daysAgo / 5) + 0.04 * noise(daysAgo));
}

function stateHistoryRows(seed: PoolSeed, days = HISTORY_DAYS): unknown[][] {
  const t0 = seed.assets[0];
  const t1 = seed.assets[1];
  const rows: unknown[][] = [];
  for (let daysAgo = days - 1; daysAgo >= 0; daysAgo -= 1) {
    const tick = tickAt(seed, daysAgo);
    const liquidity = liquidityAt(seed, daysAgo);
    const priceRaw = tickToPrice(tick);
    const fg0 = 1.4e30 + (days - daysAgo) * 3.7e27;
    const fg1 = 2.2e33 + (days - daysAgo) * 5.1e30;
    rows.push(project("pool_state_history", {
      snapshot_date: isoDay(-daysAgo),
      anchor_block: DEV_ANCHOR_BLOCK - daysAgo * 17_280,
      current_tick: tick,
      price_raw: priceRaw,
      price_adjusted: adjusted(priceRaw, t0, t1),
      liquidity_raw: bigStr(liquidity),
      liquidity_float: liquidity,
      is_live: liquidity > 0 ? 1 : 0,
      tick_count: seed.tickCount,
      fee: seed.fee,
      tick_spacing: seed.spacing,
      ticks_probed: seed.probed ? 1 : 0,
      fee_growth_global_0_raw: bigStr(fg0),
      fee_growth_global_1_raw: bigStr(fg1),
    }));
  }
  return rows;
}

function reservesHistoryRows(seed: PoolSeed, days = HISTORY_DAYS): unknown[][] {
  const rows: unknown[][] = [];
  for (let daysAgo = days - 1; daysAgo >= 0; daysAgo -= 1) {
    seed.assets.forEach((token, index) => {
      const base = seed.reserves[index] ?? 0;
      const raw = base * (1 + 0.12 * Math.sin((daysAgo + index * 7) / 6) + 0.03 * noise(daysAgo * 3 + index));
      rows.push(project("pool_reserves_history", {
        snapshot_date: isoDay(-daysAgo),
        token_address: token,
        token_index: index,
        symbol: meta(token).symbol,
        decimals: meta(token).decimals,
        balance_raw: bigStr(raw),
        balance_float: raw,
        balance_units: unitsOf(raw, token),
        anchor_block: DEV_ANCHOR_BLOCK - daysAgo * 17_280,
      }));
    });
  }
  return rows;
}

function feeGrowthRows(seed: PoolSeed, days = HISTORY_DAYS): unknown[][] {
  const t0 = seed.assets[0];
  const t1 = seed.assets[1];
  const rows: unknown[][] = [];
  for (let daysAgo = days - 1; daysAgo >= 0; daysAgo -= 1) {
    const first = daysAgo === days - 1;
    const liquidity = liquidityAt(seed, daysAgo);
    const delta0 = 3.7e27 * (1 + 0.5 * noise(daysAgo + 11));
    const delta1 = 5.1e30 * (1 + 0.5 * noise(daysAgo + 17));
    // Fees ≈ Δfg × L / 2^128 — a negative delta (one day in the window) is null.
    const negative = daysAgo === 40;
    const fees0 = first || negative || liquidity <= 0 ? null : (delta0 * liquidity) / 2 ** 128;
    const fees1 = first || negative || liquidity <= 0 ? null : (delta1 * liquidity) / 2 ** 128;
    rows.push(project("pool_fee_growth", {
      snapshot_date: isoDay(-daysAgo),
      prev_snapshot_date: first ? null : isoDay(-daysAgo - 1),
      gap_days: first ? null : 1,
      liquidity_float: liquidity,
      fee_growth_global_0_raw: bigStr(1.4e30 + (days - daysAgo) * 3.7e27),
      fee_growth_global_1_raw: bigStr(2.2e33 + (days - daysAgo) * 5.1e30),
      delta_fg0_raw: first ? null : negative ? `-${bigStr(delta0)}` : bigStr(delta0),
      delta_fg1_raw: first ? null : negative ? `-${bigStr(delta1)}` : bigStr(delta1),
      fees0_raw_est: fees0 === null ? null : bigStr(fees0),
      fees1_raw_est: fees1 === null ? null : bigStr(fees1),
      fees0_units_est: fees0 === null ? null : unitsOf(fees0, t0),
      fees1_units_est: fees1 === null ? null : unitsOf(fees1, t1),
      ticks_probed: seed.probed ? 1 : 0,
    }));
  }
  return rows;
}

/** ≤120 sampled dates × ≤80 tick buckets — mirrors the server binning. */
function heatmapRows(seed: PoolSeed): unknown[][] {
  const datesTotal = 365;
  const step = 4;
  const sampled: number[] = [];
  for (let daysAgo = datesTotal - 1; daysAgo >= 0; daysAgo -= step) sampled.push(daysAgo);
  const currents = sampled.map((daysAgo) => tickAt(seed, daysAgo));
  const axisLo = Math.min(...currents) - 1823;
  const axisHi = Math.max(...currents) + 1823;
  const tickStep = Math.max(seed.spacing ?? 60, Math.ceil((axisHi - axisLo) / 80));
  const rows: unknown[][] = [];
  sampled.forEach((daysAgo, index) => {
    const current = currents[index];
    const peak = liquidityAt(seed, daysAgo);
    for (let lo = axisLo; lo < axisHi; lo += tickStep) {
      const hi = Math.min(axisHi, lo + tickStep);
      const centre = (lo + hi) / 2 - current;
      // A bell around the current tick plus the full-range floor.
      const liquidity = 1.2e18 + peak * Math.exp(-(centre * centre) / (2 * 420 * 420));
      if (liquidity < 1.25e18 && Math.abs(centre) > 1500) continue;
      rows.push(project("pool_profile_heatmap", {
        bucket_date: isoDay(-daysAgo),
        tick_bucket_lo: lo,
        tick_bucket_hi: hi,
        liquidity_float: liquidity,
        current_tick: current,
        axis_lo: axisLo,
        axis_hi: axisHi,
        tick_step: tickStep,
        date_step_days: step,
        dates_total: datesTotal,
        dates_sampled: sampled.length,
      }));
    }
  });
  return rows;
}

function publicationFactRows(seed: PoolSeed): unknown[][] {
  const cl = familyOf(seed.cls) === "cl";
  const rows: unknown[][] = [];
  if (cl) {
    rows.push(project("pool_publication_facts", {
      snapshot_date: DEV_AS_OF,
      job_name: "daily_cl_liquidity",
      anchor_block: DEV_ANCHOR_BLOCK,
      anchor_hash: `0x${"7c".repeat(32)}`,
      block_timestamp: DEV_ANCHOR_TS,
      publication_id: `pub-cl-${DEV_AS_OF}-${seed.address.slice(2, 10)}`,
      attempt_id: 2,
      published_at: "2026-09-17T01:12:44Z",
      integrity_mode: "verified",
      block_reference_kind: "canonical_day_anchor",
      executor_kind: "cl_liquidity_probe",
      observations_total: seed.probed ? 23 : 1,
      checks_passed: seed.probed
        ? ["net_sum_zero", "reconciles_state_liquidity", "anchor_canonical"]
        : ["cl_below_active_threshold", "anchor_canonical"],
      ticks_probed: seed.probed ? 1 : 0,
      net_sum_zero_passed: seed.probed ? 1 : null,
      reconciles_passed: seed.probed ? 1 : null,
    }));
  }
  rows.push(project("pool_publication_facts", {
    snapshot_date: DEV_AS_OF,
    job_name: "daily_pool_reserves",
    anchor_block: DEV_ANCHOR_BLOCK,
    anchor_hash: `0x${"7c".repeat(32)}`,
    block_timestamp: DEV_ANCHOR_TS,
    publication_id: `pub-res-${DEV_AS_OF}-${seed.address.slice(2, 10)}`,
    attempt_id: 1,
    published_at: "2026-09-17T00:41:09Z",
    integrity_mode: "verified",
    block_reference_kind: "canonical_day_anchor",
    executor_kind: "pool_reserves_reader",
    observations_total: seed.assets.length,
    checks_passed: ["anchor_canonical", "balances_non_negative"],
    ticks_probed: 0,
    net_sum_zero_passed: null,
    reconciles_passed: null,
  }));
  return rows;
}

// ---- list sections ----------------------------------------------------------

function overviewDatasets(): Record<string, DatasetDescriptor> {
  const trend: unknown[][] = [];
  for (let daysAgo = 364; daysAgo >= 0; daysAgo -= 1) {
    const partial = daysAgo === 24; // the 2026-08-23 gap
    trend.push(project("live_pool_trend", {
      bucket: isoDay(-daysAgo),
      pools_published_cl: partial ? 1082 : 2380 + Math.round((364 - daysAgo) * 0.38),
      pools_live_cl: partial ? 540 : 1160 + Math.round((364 - daysAgo) * 0.21 + 12 * noise(daysAgo)),
      pools_probed: partial ? 200 : 398 + Math.round((364 - daysAgo) * 0.08),
      pools_published_reserves: 3900 + Math.round((364 - daysAgo) * 0.33),
    }));
  }
  return {
    pools_summary: descriptor("pools_summary", [project("pools_summary", {
      as_of: DEV_AS_OF, anchor_block: DEV_ANCHOR_BLOCK, anchor_timestamp: DEV_ANCHOR_TS,
      pools_configured: 4022, pools_configured_cl: 2521, pools_configured_reserves_only: 1501,
      pools_published_cl: 2519, pools_live_cl: 1237, pools_probed: 427, pools_live_unprobed: 810,
      reserves_as_of: DEV_AS_OF, pools_with_reserves: 3410, pools_live_reserves_only: 1104,
    })]),
    source_freshness: descriptor("source_freshness", [
      project("source_freshness", { source: "cl_state", latest_snapshot_date: DEV_AS_OF, latest_anchor_block: DEV_ANCHOR_BLOCK, pools_published: 2519, latest_published_at: "2026-09-17T01:12:44Z" }),
      project("source_freshness", { source: "reserves", latest_snapshot_date: DEV_AS_OF, latest_anchor_block: DEV_ANCHOR_BLOCK, pools_published: 4022, latest_published_at: "2026-09-17T00:41:09Z" }),
    ]),
    pools_by_class_fee: descriptor("pools_by_class_fee", [
      ["uniswap_v3", "cl", 100, "b100", 61, 34, 18],
      ["uniswap_v3", "cl", 500, "b500", 142, 98, 71],
      ["uniswap_v3", "cl", 3000, "b3000", 388, 240, 160],
      ["uniswap_v3", "cl", 10000, "b10000", 96, 41, 22],
      ["swapr_v3_algebra", "cl", 100, "b100", 1521, 702, 121],
      ["swapr_v3_algebra", "cl", 500, "b500", 214, 88, 24],
      ["swapr_v3_algebra", "cl", 3000, "b3000", 74, 26, 9],
      ["swapr_v3_algebra", "cl", 12000, "bhigh", 25, 8, 2],
      ["balancer_v2", "reserves_only", null, null, 1418, 1041, 0],
      ["balancer_v3", "reserves_only", null, null, 83, 63, 0],
    ].map((row) => project("pools_by_class_fee", {
      pool_class: row[0], pool_family: row[1], fee: row[2], fee_band: row[3], pools: row[4], live_pools: row[5], probed_pools: row[6],
    }))),
    probe_coverage_split: descriptor("probe_coverage_split", [
      project("probe_coverage_split", { ticks_probed: 1, is_live: 1, pools: 427, median_liquidity_float: 7.0e19, p90_liquidity_float: 9.4e21 }),
      project("probe_coverage_split", { ticks_probed: 1, is_live: 0, pools: 0, median_liquidity_float: null, p90_liquidity_float: null }),
      project("probe_coverage_split", { ticks_probed: 0, is_live: 1, pools: 810, median_liquidity_float: 9.0e12, p90_liquidity_float: 4.1e15 }),
      project("probe_coverage_split", { ticks_probed: 0, is_live: 0, pools: 1282, median_liquidity_float: 0, p90_liquidity_float: 0 }),
    ]),
    live_pool_trend: descriptor("live_pool_trend", trend),
    concentration_summary: descriptor("concentration_summary", [
      project("concentration_summary", { metric: "share_1pct", pools_measured: 427, q25: 0.004, median: 0.031, q75: 0.22, pools_true: null }),
      project("concentration_summary", { metric: "share_5pct", pools_measured: 427, q25: 0.018, median: 0.12, q75: 0.61, pools_true: null }),
      project("concentration_summary", { metric: "share_10pct", pools_measured: 427, q25: 0.03, median: 0.24, q75: 0.83, pools_true: null }),
      project("concentration_summary", { metric: "ranges_per_pool", pools_measured: 427, q25: 1, median: 3, q75: 9, pools_true: null }),
      project("concentration_summary", { metric: "has_full_range", pools_measured: 427, q25: null, median: null, q75: null, pools_true: 311 }),
    ]),
    range_width_distribution: descriptor("range_width_distribution", [
      [1, "≤10", 42, 18, 0.024], [2, "≤100", 210, 71, 0.118], [3, "≤1k", 512, 140, 0.288],
      [4, "≤10k", 466, 162, 0.262], [5, "≤100k", 191, 96, 0.107], [6, "wide", 47, 33, 0.026], [7, "full_range", 311, 311, 0.175],
    ].map((row) => project("range_width_distribution", {
      bucket_order: row[0], width_bucket: row[1], ranges: row[2], pools: row[3], share_of_ranges: row[4],
    }))),
  };
}

function poolsDatasets(): Record<string, DatasetDescriptor> {
  return {
    pool_directory: descriptor(
      "pool_directory",
      ALL_SEEDS.map((seed) => project("pool_directory", directoryObject(seed))),
      { rowCount: 4022, pageToken: `offset:${ALL_SEEDS.length}` },
    ),
  };
}

function tokenDirectoryObject(token: string): Record<string, unknown> {
  const pools = ALL_SEEDS.filter((seed) => seed.assets.includes(token));
  const m = meta(token);
  return {
    token_address: token,
    symbol: m.symbol,
    token_name: m.name,
    decimals: m.decimals,
    resolution_status: m.symbol === null ? "unresolved" : m.decimals === null ? "symbol_only" : "resolved",
    is_resolved: m.symbol !== null && m.decimals !== null ? 1 : 0,
    pools_count: pools.length,
    cl_pools: pools.filter((seed) => familyOf(seed.cls) === "cl").length,
    reserves_only_pools: pools.filter((seed) => familyOf(seed.cls) === "reserves_only").length,
    live_pools: pools.filter(isLive).length,
    probed_pools: pools.filter((seed) => seed.probed).length,
    as_of: DEV_AS_OF,
  };
}

const TOKENS = [WXDAI, WETH, SDAI, USDCE, GNO, COW, EURE, CRC, CRC2];

function tokensDatasets(): Record<string, DatasetDescriptor> {
  return {
    token_directory: descriptor(
      "token_directory",
      TOKENS.map((token) => project("token_directory", tokenDirectoryObject(token))),
      { rowCount: 2312, pageToken: `offset:${TOKENS.length}` },
    ),
  };
}

function coverageDatasets(): Record<string, DatasetDescriptor> {
  const calendar: unknown[][] = [];
  for (let daysAgo = 119; daysAgo >= 0; daysAgo -= 1) {
    const date = isoDay(-daysAgo);
    const partial = daysAgo === 24;
    const absent = daysAgo === 61;
    if (!absent) {
      calendar.push(project("publication_calendar", {
        snapshot_date: date, job_name: "daily_cl_liquidity", anchor_block: DEV_ANCHOR_BLOCK - daysAgo * 17_280,
        pools_published: partial ? 1082 : 2519 - Math.round(daysAgo * 0.4),
        pools_below_threshold: partial ? 900 : 2092 - Math.round(daysAgo * 0.3),
        pools_probed: partial ? 182 : 427 - Math.round(daysAgo * 0.1),
        net_sum_zero_passed: partial ? 182 : 427 - Math.round(daysAgo * 0.1),
        reconciles_passed: partial ? 180 : 425 - Math.round(daysAgo * 0.1),
        pools_configured_now: 2521,
      }));
    }
    calendar.push(project("publication_calendar", {
      snapshot_date: date, job_name: "daily_pool_reserves", anchor_block: DEV_ANCHOR_BLOCK - daysAgo * 17_280,
      pools_published: 4022 - Math.round(daysAgo * 0.6), pools_below_threshold: null, pools_probed: null,
      net_sum_zero_passed: null, reconciles_passed: null, pools_configured_now: 4022,
    }));
  }
  return {
    coverage_summary: descriptor("coverage_summary", [
      project("coverage_summary", {
        job_name: "daily_cl_liquidity", first_snapshot_date: "2023-09-25", last_snapshot_date: DEV_AS_OF,
        days_published: 1085, pools_configured: 2521, pools_published_latest: 2519,
        pools_below_threshold_latest: 2092, publications_total: 2_612_400,
      }),
      project("coverage_summary", {
        job_name: "daily_pool_reserves", first_snapshot_date: "2022-12-12", last_snapshot_date: DEV_AS_OF,
        days_published: 1375, pools_configured: 4022, pools_published_latest: 4022,
        pools_below_threshold_latest: null, publications_total: 3_640_100,
      }),
    ]),
    publication_calendar: descriptor("publication_calendar", calendar),
    missing_days: descriptor("missing_days", [
      project("missing_days", { snapshot_date: isoDay(-24), job_name: "daily_cl_liquidity", gap_kind: "partial", pools_published: 1082, expected_pools: 2519 }),
      project("missing_days", { snapshot_date: isoDay(-61), job_name: "daily_cl_liquidity", gap_kind: "absent", pools_published: null, expected_pools: 2510 }),
      project("missing_days", { snapshot_date: "2024-03-02", job_name: "daily_pool_reserves", gap_kind: "absent", pools_published: null, expected_pools: 3101 }),
    ]),
    metadata_gap: descriptor("metadata_gap", [
      ["tokens_symbol · cl", 34, 2278, 0.0147], ["tokens_decimals · cl", 34, 2278, 0.0147],
      ["pools_price_adjustable · cl", 87, 2432, 0.0345], ["pools_fully_labelled · cl", 87, 2432, 0.0345],
      ["tokens_symbol · reserves_only", 87, 1093, 0.0737], ["tokens_decimals · reserves_only", 87, 1093, 0.0737],
      ["pools_fully_labelled · reserves_only", 61, 1440, 0.0406],
    ].map((row) => project("metadata_gap", { dimension: row[0], known: row[1], unknown: row[2], pct_known: row[3] }))),
  };
}

// ---- entities ---------------------------------------------------------------

export function poolEntityDatasets(address: string): Record<string, DatasetDescriptor> | null {
  const seed = ALL_SEEDS.find((entry) => entry.address === address);
  if (!seed) return null;
  const cl = familyOf(seed.cls) === "cl";
  // No state row: the CL-only keys are present but empty, never fabricated.
  const stateRows = seed.noState ? 0 : HISTORY_DAYS;
  const out: Record<string, DatasetDescriptor> = {
    pool_detail: descriptor("pool_detail", [project("pool_detail", detailObject(seed))]),
    pool_publication_facts: descriptor("pool_publication_facts", publicationFactRows(seed)),
    pool_reserves_history: descriptor("pool_reserves_history", reservesHistoryRows(seed, cl ? HISTORY_DAYS : 60)),
  };
  if (!cl) return out;
  const spec = PROFILES[address];
  const probed = seed.probed && spec !== undefined;
  out.pool_profile_at = descriptor("pool_profile_at", probed ? profileRows(seed, spec) : []);
  out.pool_profile_concentration = descriptor("pool_profile_concentration", probed ? concentrationRows(seed, spec) : []);
  out.pool_ticks_at = descriptor("pool_ticks_at", probed ? tickRows(seed, spec) : []);
  out.pool_state_history = descriptor("pool_state_history", stateRows ? stateHistoryRows(seed, stateRows) : []);
  out.pool_fee_growth = descriptor("pool_fee_growth", probed ? feeGrowthRows(seed) : []);
  if (address === P_WETH) out.pool_profile_heatmap = descriptor("pool_profile_heatmap", heatmapRows(seed));
  return out;
}

export function tokenEntityDatasets(address: string): Record<string, DatasetDescriptor> | null {
  if (!TOKENS.includes(address)) return null;
  const pools = ALL_SEEDS.filter((seed) => seed.assets.includes(address));
  const reserveOf = (seed: PoolSeed) => seed.reserves[seed.assets.indexOf(address)] ?? 0;
  const total = pools.reduce((acc, seed) => acc + reserveOf(seed), 0);
  const directory = tokenDirectoryObject(address);
  return {
    token_detail: descriptor("token_detail", [project("token_detail", {
      ...directory,
      entity_label: `token · ${shortLabel(address)}`,
      reserves_as_of: DEV_AS_OF,
      total_reserve_raw: bigStr(total),
      total_reserve_units: unitsOf(total, address),
    })]),
    token_pools: descriptor("token_pools", pools.map((seed) => {
      const cl = familyOf(seed.cls) === "cl" && !seed.noState;
      const index = seed.assets.indexOf(address);
      const counter = seed.assets.filter((token) => token !== address);
      const priceRaw = cl && seed.tick !== null ? tickToPrice(seed.tick) : null;
      const priceAdj = cl ? adjusted(priceRaw, seed.assets[0], seed.assets[1]) : null;
      const reserve = reserveOf(seed);
      return project("token_pools", {
        pool_address: seed.address,
        pool_name: seed.name,
        pool_class: seed.cls,
        pool_family: familyOf(seed.cls),
        has_state: cl ? 1 : 0,
        fee: seed.fee,
        fee_band: feeBandOf(seed.fee),
        token_is_token0: seed.assets.length > 2 ? null : index === 0 ? 1 : 0,
        counter_tokens: counter,
        counter_labels: counter.map(tokenLabelOf),
        current_tick: cl ? seed.tick : null,
        price_raw: priceRaw,
        price_adjusted: priceAdj,
        price_of_token_in_counter: priceAdj === null || seed.assets.length > 2 ? null : index === 0 ? priceAdj : 1 / priceAdj,
        liquidity_raw: cl && seed.liquidity !== null ? bigStr(seed.liquidity) : null,
        liquidity_float: cl ? seed.liquidity : null,
        is_live: isLive(seed) ? 1 : 0,
        ticks_probed: seed.probed ? 1 : 0,
        reserve_token_raw: bigStr(reserve),
        token_decimals: meta(address).decimals,
        reserve_token_units: unitsOf(reserve, address),
        reserve_share: total > 0 ? reserve / total : null,
      });
    }), { rowCount: pools.length }),
  };
}

// ---- payload ----------------------------------------------------------------

const LIST_DATASETS: Record<PlxListSection, () => Record<string, DatasetDescriptor>> = {
  overview: overviewDatasets,
  pools: poolsDatasets,
  tokens: tokensDatasets,
  coverage: coverageDatasets,
};

function loadedGroupsFor(section: PlxSection, extra: Record<string, boolean | "partial"> = {}): Record<string, boolean | "partial"> {
  const out: Record<string, boolean | "partial"> = {};
  for (const [name, groups] of Object.entries(SECTION_GROUPS)) {
    for (const group of Object.keys(groups)) out[`${name}.${group}`] = name === section;
  }
  // The heatmap is on demand — only the pool fixture that ships one has it.
  if (section === "pool") out["pool.heatmap"] = false;
  return { ...out, ...extra };
}

function freshnessFor(): PoolsExplorerViewState["freshness"] {
  return {
    cl_state: { latest_snapshot_date: DEV_AS_OF, latest_anchor_block: DEV_ANCHOR_BLOCK, pools_published: 2519, latest_published_at: "2026-09-17T01:12:44Z", stale: false },
    reserves: { latest_snapshot_date: DEV_AS_OF, latest_anchor_block: DEV_ANCHOR_BLOCK, pools_published: 4022, latest_published_at: "2026-09-17T00:41:09Z", stale: false },
  };
}

function baseState(section: PlxSection): PoolsExplorerViewState {
  return {
    section,
    title: "Pool Liquidity Explorer",
    as_of: "",
    window: "1y",
    heatmap_window: "1y",
    filters: { ...EMPTY_DRAFT },
    selected_entity: null,
    breadcrumbs: [],
    search: { query: "", candidates: [] },
    applied_request_id: 1,
    scope_id: `dev-${section}`,
    coverage: {},
    coverage_warnings: [],
    warnings: [],
    dataset_revisions: {},
    loaded_groups: loadedGroupsFor(section),
    section_fingerprints: {},
    section_datasets: {},
    section_lru: [section],
    freshness: freshnessFor(),
    // Mirrors the server: both keys always exist (empty on a fresh view) and
    // are patched wholesale by load_pools_token_metadata.
    token_overlay: { ...TOKEN_OVERLAY },
    token_overlay_stats: { ...TOKEN_OVERLAY_STATS },
  };
}

function withRevisions(state: PoolsExplorerViewState, datasets: Record<string, DatasetDescriptor>): PoolsExplorerViewState {
  return {
    ...state,
    dataset_revisions: Object.fromEntries(Object.keys(datasets).map((key) => [key, 1])),
    section_datasets: { [state.section]: Object.keys(datasets) },
  };
}

function payload(section: PlxSection, datasets: Record<string, DatasetDescriptor>, state: PoolsExplorerViewState): MiniAppPayload<PoolsExplorerViewState> {
  return {
    type: "INITIAL_LOAD",
    view_id: "pools-dev",
    app_id: "pools_explorer",
    title: "Pool Liquidity Explorer",
    status: "ready",
    datasets,
    view_state: withRevisions({ ...state, section }, datasets),
  };
}

function entityWarnings(entityType: PlxEntityType, identifier: string): string[] {
  if (entityType === "token") return TOKEN_META[identifier]?.symbol === null ? ["metadata_unresolved"] : [];
  const seed = ALL_SEEDS.find((entry) => entry.address === identifier);
  if (!seed) return ["no_indexed_data"];
  const codes: string[] = [];
  if (familyOf(seed.cls) === "reserves_only") codes.push("reserves_only_pool");
  else if (!seed.probed) codes.push("pool_below_active_threshold");
  if (seed.assets.some((token) => meta(token).symbol === null)) codes.push("metadata_unresolved");
  return codes;
}

/** Entity payload for `?entity=pool:<addr>` / `?entity=token:<addr>`. An
 * unknown address renders the zero-row entity (the honest "not configured"
 * state) rather than falling back to a known pool. */
export function entityPayload(entityType: PlxEntityType, identifier: string): MiniAppPayload<PoolsExplorerViewState> {
  const address = identifier.toLowerCase();
  const datasets = (entityType === "pool" ? poolEntityDatasets(address) : tokenEntityDatasets(address))
    ?? (entityType === "pool"
      ? { pool_detail: descriptor("pool_detail", []), pool_publication_facts: descriptor("pool_publication_facts", []) }
      : { token_detail: descriptor("token_detail", []), token_pools: descriptor("token_pools", []) });
  const seed = ALL_SEEDS.find((entry) => entry.address === address);
  const label = entityType === "pool"
    ? (seed ? `${seed.cls} · ${shortLabel(address)}` : shortLabel(address))
    : `token · ${shortLabel(address)}`;
  const state = baseState(entityType);
  state.selected_entity = { entity_type: entityType, identifier: address, label };
  state.breadcrumbs = [{ label, entity_type: entityType, identifier: address }];
  state.coverage_warnings = entityWarnings(entityType, address);
  state.scope_id = `dev-${entityType}-${address.slice(2, 10)}`;
  if (entityType === "pool") {
    state.loaded_groups = loadedGroupsFor("pool", { "pool.heatmap": "pool_profile_heatmap" in datasets });
  }
  return payload(entityType, datasets, state);
}

export function sectionPayload(section: PlxListSection): MiniAppPayload<PoolsExplorerViewState> {
  return payload(section, LIST_DATASETS[section](), baseState(section));
}

/** Dev-only: pick the fixture from the URL. Defaults to overview — which is
 * also what tests importing MOCK_PAYLOAD observe. */
export function devPayload(search: string): MiniAppPayload<PoolsExplorerViewState> {
  const params = new URLSearchParams(search);
  const entity = params.get("entity") ?? "";
  const match = entity.match(/^(pool|token):(0x[0-9a-fA-F]{40})$/);
  if (match) return entityPayload(match[1] as PlxEntityType, match[2]);
  const section = params.get("section") ?? "";
  if (section === "pools" || section === "tokens" || section === "coverage") return sectionPayload(section);
  return sectionPayload("overview");
}

export const MOCK_PAYLOAD: MiniAppPayload<PoolsExplorerViewState> = sectionPayload("overview");
