"""CoW Data Explorer mini app.

Read-only analyst surface over the ``cow_db`` ClickHouse database.  The app
intentionally distinguishes settled execution data, auction/native reference
prices, and the indexer's observed open-order snapshot. Blockscout remains an
outbound-link provider only; CoinGecko token lists are the optional, cached
source for decorative token imagery.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import requests
from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import (
    INTERACTIVE_QUERY_BUDGET,
    ClickHouseManager,
)
from cerebro_mcp.models.mini_app import MiniAppPayload, SummaryCard
from cerebro_mcp.runtime.mini_app_cache import CachedDataset, FailureCache
from cerebro_mcp.tools.visualization import mini_apps, web_apps

logger = logging.getLogger(__name__)

COW_APP_ID = "cow_explorer"
COW_TITLE = "CoW Data Explorer"
COW_URI = "ui://cerebro/cow_explorer"
COW_DB = "cow_db"
COW_APP_META = {
    "ui": {"resourceUri": COW_URI},
    "ui/resourceUri": COW_URI,
}
ROW_CAP = 10_000
NATIVE_TOKEN = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
DEFAULT_CHAIN_ID = 1
VALID_SCOPES = {"production", "testnet"}
VALID_SECTIONS = {
    "overview", "markets", "trades", "orders", "auctions", "solvers",
    "traders", "patterns", "live",
}
#: Sections that support the all-networks scope (chain_id=0). Every other
#: section coerces to a concrete chain WITH an explicit warning.
#: orders: status/type analytics are multi-chain; the pair-scoped intents and
#: the trades-join quality groups stay single-chain (skipped at chain=0).
#: live: feeds merge every in-scope chain (the 1h bound keeps the dedup hash
#: tiny at ten chains — same safety mechanism as single-chain).
ALL_NETWORK_SECTIONS = {
    "overview", "trades", "solvers", "traders", "auctions", "orders", "live",
}
#: Trader growth accounting (new/returning/reactivated/churned) displays this
#: many trailing months; scans bound to N+1 months for the warm-up period.
#: Sizing (live-verified 2026-07-22): uniq(owner) all-time = 719,470 (~90 MB
#: min-state hash for the first-seen CTE) and (owner, month) pairs over 13
#: months = 696,213 (~80 MB grouped-scan hash; full classification query ran
#: 3.2s). Each dynamics dataset is the SOLE member of its load group, so the
#: two hashes never stack with sibling queries. Re-verify yearly.
TRADER_DYNAMICS_MONTHS = 12
#: Per-arm over-fetch for top-N tape arms: 3x the row cap absorbs the <0.1%
#: ReplacingMergeTree duplicate rate so the post-dedup global top-ROW_CAP is
#: correct. Each arm is a bounded heap sort — memory-safe at any window.
TAPE_ARM_LIMIT = 3 * ROW_CAP
#: Per-fill execution surplus vs the order's limit price, in basis points.
#: (exec_buy x limit_sell)/(exec_sell x limit_buy) - 1 is KIND-INDEPENDENT:
#: for sell orders it is exec_rate/limit_rate - 1, for buy orders
#: limit_rate/exec_rate - 1 - positive always means better than limit.
#: Ratios are decimals-free within a pair, so no token metadata is needed.
SURPLUS_BPS = (
    "((toFloat64({eb})*toFloat64({ls}))"
    "/nullIf(toFloat64({es})*toFloat64({lb}),0)-1)*1e4"
)
#: Default chain for the Live section. Gnosis currently has the freshest
#: indexing (checkpoint lag ~minutes); the pulse panel shows every chain's lag
#: so users can switch when another chain catches up.
LIVE_DEFAULT_CHAIN_ID = 100
#: Live feed window — the base tables are NOT time-sorted, so live queries
#: must stay tightly bounded. Never widen beyond one hour.
LIVE_WINDOW_SQL = "now() - INTERVAL 1 HOUR"
#: NOTE: block_number is NOT in the trades/settlements sort key
#: (ORDER BY (environment, chain_id, tx_hash, log_index, …)), so a block_number
#: floor does NOT prune those tables — only `chain_blocks` has block_number in
#: its key. Live feeds stay memory-safe via the 1h block_timestamp bound, which
#: keeps the GROUP BY hash to an hour of rows; the scan is full either way.
ENTITY_TYPES = {"order", "transaction", "address", "token", "auction", "solver"}
SECTION_DEFAULT_DAYS = {
    "overview": 30,
    "markets": 30,
    "trades": 7,
    "orders": 30,
    "auctions": 30,
    "solvers": 30,
    "traders": 30,
    "patterns": 30,
    "live": 1,
}
#: Datasets per section, split into load groups. The section apply loads only
#: ``core`` synchronously; every other group is fetched afterwards by the
#: frontend through ``load_cow_explorer_datasets`` — this is what makes the
#: open path and section switches fast. Every dataset key of a section MUST
#: appear in exactly one group (tested).
SECTION_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "overview": {
        "core": ("network_summary", "coverage_matrix"),
        "breakdown": ("network_activity", "top_pairs", "fee_policy_counts"),
        "protocol": ("protocol_kpis", "alltime_chain_totals"),
        "share": ("chain_share_trend",),
    },
    "markets": {
        "core": ("market_summary", "pair_options"),
        "charts": ("price_candles", "auction_reference_prices", "native_reference_prices"),
        "depth": ("pair_depth", "depth_horizon", "open_intent_pairs"),
        # Depth-over-time heatmap — a SEPARATE deferred group from `depth` so it
        # loads only when the Heatmap tab is opened, never on a history-slider tick.
        "depth_heatmap": ("pair_depth_heatmap",),
        "tape": ("recent_market_trades",),
    },
    "trades": {
        "core": ("trade_activity", "trade_pair_breakdown"),
        "tape": ("trades",),
    },
    "orders": {
        "core": ("order_status_summary", "order_activity"),
        # intents (pair-scoped) and the trades-join quality groups exist only
        # single-chain; types/programmatic are dual-mode — a group load simply
        # skips absent keys (execution_flow precedent).
        "intents": ("known_orders", "known_intents", "intent_depth"),
        "quality": ("order_quality_summary", "fill_latency_distribution", "surplus_distribution"),
        "types": ("order_type_summary", "order_flavor_mix", "order_type_trend"),
        "programmatic": ("conditional_order_activity", "appdata_order_classes"),
        "class_quality": ("surplus_by_class",),
    },
    "auctions": {
        "core": ("auction_activity",),
        "list": ("auctions",),
    },
    "solvers": {
        "core": ("solver_stats", "solver_activity"),
        # execution_flow exists only single-chain; solver_cross_chain only in
        # the all-networks rollup — a group load simply skips absent keys.
        "detail": ("ranking_distribution", "execution_flow", "solver_cross_chain"),
        "directory": ("solver_directory",),
        "quality": ("solver_score_gaps",),
    },
    "traders": {
        "core": ("trader_leaderboard", "trader_activity"),
        # Growth accounting and retention are each the SOLE member of their
        # group: their all-time first-seen hash (~90 MB) must never run
        # concurrently with a sibling scan (see TRADER_DYNAMICS_MONTHS).
        "dynamics": ("trader_dynamics",),
        "retention": ("trader_retention",),
    },
    "patterns": {
        "core": ("solver_pair_matrix",),
        "affinity": ("trader_solver_affinity",),
        "quality": ("fee_policy_quality", "quote_delta_quality"),
    },
    "live": {
        "core": ("live_pulse",),
        "feed": ("live_trades", "live_settlements", "live_minute_activity"),
        "intents": ("live_open_orders", "live_order_events"),
    },
}
#: Retain at most this many sections' datasets on a view before evicting the
#: least recently used one (keeps tab-return instant without unbounded memory).
MAX_RETAINED_SECTIONS = 4
CANDLE_BUCKETS = {
    "5m": "toStartOfInterval(block_timestamp, INTERVAL 5 MINUTE)",
    "15m": "toStartOfInterval(block_timestamp, INTERVAL 15 MINUTE)",
    "30m": "toStartOfInterval(block_timestamp, INTERVAL 30 MINUTE)",
    "1h": "toStartOfInterval(block_timestamp, INTERVAL 1 HOUR)",
    "2h": "toStartOfInterval(block_timestamp, INTERVAL 2 HOUR)",
    "4h": "toStartOfInterval(block_timestamp, INTERVAL 4 HOUR)",
    "12h": "toStartOfInterval(block_timestamp, INTERVAL 12 HOUR)",
    "1d": "toStartOfDay(block_timestamp)",
    "1w": "toStartOfWeek(block_timestamp)",
}
COINGECKO_PLATFORM_IDS = {
    1: "ethereum",
    56: "binance-smart-chain",
    100: "xdai",
    137: "polygon-pos",
    8453: "base",
    9745: "plasma",
    42161: "arbitrum-one",
    43114: "avalanche",
    57073: "ink",
    59144: "linea",
}
COINGECKO_TOKEN_LIST_URL = "https://tokens.coingecko.com/{platform}/all.json"
COINGECKO_NATIVE_ICON_URLS = {
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
}
COINGECKO_ICON_CACHE_TTL_SECONDS = 30 * 60
_COINGECKO_IMAGE_HOSTS = {"assets.coingecko.com", "coin-images.coingecko.com"}
_COINGECKO_ICON_CACHE: dict[int, tuple[float, dict[str, str]]] = {}
_COINGECKO_ICON_LOCK = threading.RLock()
_TOKEN_COLUMN_RE = re.compile(r"^(?:token|token[01]|(?:base|quote|sell|buy|fee)_token)$")
ORDER_UID_RE = re.compile(r"^0x[0-9a-f]{112}$")
HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
INTEGER_RE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class ExplorerInfo:
    provider: Literal["blockscout", "bscscan", "avalanche", "plasmascan"]
    brand: str
    base_url: str
    transaction_url_template: str
    address_url_template: str
    token_url_template: str


@dataclass(frozen=True)
class ChainInfo:
    chain_id: int
    name: str
    native_symbol: str
    environment: Literal["production", "testnet"]
    explorer: ExplorerInfo


def _explorer(
    provider: Literal["blockscout", "bscscan", "avalanche", "plasmascan"],
    brand: str,
    base: str,
    *,
    token_as_address: bool = False,
) -> ExplorerInfo:
    base = base.rstrip("/")
    return ExplorerInfo(
        provider=provider,
        brand=brand,
        base_url=base,
        transaction_url_template=f"{base}/tx/{{hash}}",
        address_url_template=f"{base}/address/{{address}}",
        token_url_template=(
            f"{base}/address/{{address}}" if token_as_address else f"{base}/token/{{address}}"
        ),
    )


COW_CHAINS: dict[int, ChainInfo] = {
    1: ChainInfo(1, "Ethereum", "ETH", "production", _explorer("blockscout", "Blockscout", "https://eth.blockscout.com")),
    100: ChainInfo(100, "Gnosis", "xDAI", "production", _explorer("blockscout", "Blockscout", "https://gnosis.blockscout.com")),
    42161: ChainInfo(42161, "Arbitrum One", "ETH", "production", _explorer("blockscout", "Blockscout", "https://arbitrum.blockscout.com")),
    8453: ChainInfo(8453, "Base", "ETH", "production", _explorer("blockscout", "Blockscout", "https://base.blockscout.com")),
    56: ChainInfo(56, "BNB Smart Chain", "BNB", "production", _explorer("bscscan", "BscScan", "https://bscscan.com")),
    137: ChainInfo(137, "Polygon PoS", "POL", "production", _explorer("blockscout", "Blockscout", "https://polygon.blockscout.com")),
    43114: ChainInfo(43114, "Avalanche C-Chain", "AVAX", "production", _explorer("avalanche", "Avalanche Explorer", "https://subnets.avax.network/c-chain", token_as_address=True)),
    59144: ChainInfo(59144, "Linea", "ETH", "production", _explorer("blockscout", "Blockscout", "https://explorer.linea.build")),
    57073: ChainInfo(57073, "Ink", "ETH", "production", _explorer("blockscout", "Blockscout", "https://explorer.inkonchain.com")),
    9745: ChainInfo(9745, "Plasma", "XPL", "production", _explorer("plasmascan", "Plasmascan", "https://plasmascan.to")),
    11155111: ChainInfo(11155111, "Ethereum Sepolia", "ETH", "testnet", _explorer("blockscout", "Blockscout", "https://eth-sepolia.blockscout.com")),
}


@dataclass(frozen=True)
class QuerySpec:
    key: str
    title: str
    sql: str
    parameters: dict[str, Any]
    basis: str
    coverage_mode: str
    cache_ttl_seconds: int = 300
    #: False for heavy row-tapes that carry their own inner LIMIT: skips the
    #: loader's ``count() OVER ()`` wrapper, whose empty-frame window forces
    #: full materialization of the inner result before LIMIT (the proven OOM
    #: mechanism on unbounded scans). Aggregate specs keep the exact count —
    #: their result cardinality is inherently bounded by the GROUP BY.
    exact_count: bool = True


_BUNDLE = mini_apps.StaticBundle(
    "cow_explorer.html",
    assets_dir="assets/cow_explorer",
    build_hint="make build-ui-cow-explorer",
)


def get_cow_explorer_html() -> str:
    return _BUNDLE.html()


def get_cow_explorer_diagnostics() -> dict[str, Any]:
    return _BUNDLE.diagnostics()


#: Static per-chain data caveats surfaced in the chain selector and coverage
#: matrix. BSC's indexed trades carry NULL block timestamps (verified live),
#: so every time-bounded view silently excludes them — say so explicitly.
CHAIN_DATA_NOTES: dict[int, str] = {
    56: (
        "Indexed BNB Chain trades have no block timestamps; time-bounded "
        "views exclude them — use entity lookups (ordered by block number) "
        "or all-history aggregates."
    ),
}


def _chain_dict(chain: ChainInfo) -> dict[str, Any]:
    return {
        "chain_id": chain.chain_id,
        "name": chain.name,
        "native_symbol": chain.native_symbol,
        "environment": chain.environment,
        "explorer": asdict(chain.explorer),
        # CoinGecko asset-platform image (static registry; monogram fallback
        # client-side for chains without one, e.g. Sepolia).
        "icon_url": COINGECKO_NATIVE_ICON_URLS.get(chain.chain_id, ""),
        "data_note": CHAIN_DATA_NOTES.get(chain.chain_id, ""),
    }


def _chains_for_scope(scope: str) -> list[ChainInfo]:
    return [c for c in COW_CHAINS.values() if c.environment == scope]


def _normalize_hex(value: str) -> str:
    return value.strip().lower()


def _safe_coingecko_logo_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _COINGECKO_IMAGE_HOSTS:
        return ""
    return url


def _fetch_coingecko_icon_map(chain_id: int) -> dict[str, str]:
    platform = COINGECKO_PLATFORM_IDS.get(chain_id)
    if not platform:
        return {}
    response = requests.get(
        COINGECKO_TOKEN_LIST_URL.format(platform=platform),
        timeout=(2, 8),
        headers={"Accept": "application/json", "User-Agent": "cerebro-cow-explorer/1"},
    )
    response.raise_for_status()
    payload = response.json()
    tokens = payload.get("tokens", []) if isinstance(payload, dict) else []
    icons: dict[str, str] = {}
    for item in tokens:
        if not isinstance(item, dict):
            continue
        address = _normalize_hex(str(item.get("address") or ""))
        logo_url = _safe_coingecko_logo_url(item.get("logoURI"))
        if ADDRESS_RE.fullmatch(address) and logo_url:
            icons[address] = logo_url
    return icons


#: Background fetcher for CoinGecko token lists. Two workers are plenty — a
#: fetch per chain runs at most once per cache TTL, and NOTHING ever waits on
#: it: data loads read the cache as-is and the frontend patches icons in later.
_COINGECKO_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cow-icons")
_COINGECKO_PENDING: set[int] = set()


def _coingecko_icon_map_nowait(chain_id: int) -> tuple[dict[str, str], bool]:
    """Return ``(cached icon map, pending)`` without ever blocking.

    On a cache miss the fetch is submitted to the background executor and
    ``pending=True`` signals the caller (the icon-overlay tool) that a retry
    will find more icons. Data-loading paths never call this.
    """
    now = time.monotonic()
    with _COINGECKO_ICON_LOCK:
        cached = _COINGECKO_ICON_CACHE.get(chain_id)
        if cached and now - cached[0] < COINGECKO_ICON_CACHE_TTL_SECONDS:
            return cached[1], False
        if chain_id not in COINGECKO_PLATFORM_IDS:
            return {}, False
        if chain_id in _COINGECKO_PENDING:
            return (cached[1] if cached else {}), True
        _COINGECKO_PENDING.add(chain_id)

    def fetch() -> None:
        icons: dict[str, str] = {}
        try:
            icons = _fetch_coingecko_icon_map(chain_id)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning(
                "CoinGecko token icons unavailable for chain %s: %s", chain_id, exc
            )
        finally:
            with _COINGECKO_ICON_LOCK:
                _COINGECKO_ICON_CACHE[chain_id] = (time.monotonic(), icons)
                _COINGECKO_PENDING.discard(chain_id)

    try:
        _COINGECKO_EXECUTOR.submit(fetch)
    except RuntimeError:  # interpreter shutdown
        with _COINGECKO_ICON_LOCK:
            _COINGECKO_PENDING.discard(chain_id)
        return (cached[1] if cached else {}), False
    return (cached[1] if cached else {}), True


def _dataset_token_addresses(
    datasets: dict[str, CachedDataset],
    cap_per_chain: int = 500,
) -> dict[int, set[str]]:
    """Collect distinct token addresses per chain from attached datasets."""
    per_chain: dict[int, set[str]] = {}
    for dataset in datasets.values():
        token_indexes = [
            index for index, name in enumerate(dataset.columns)
            if _TOKEN_COLUMN_RE.fullmatch(name)
        ]
        if not token_indexes:
            continue
        chain_index = (
            dataset.columns.index("chain_id") if "chain_id" in dataset.columns else -1
        )
        fallback_chain = int(dataset.parameters.get("chain_id") or 0) if dataset.parameters else 0
        for row in dataset.rows:
            chain_id = fallback_chain
            if 0 <= chain_index < len(row) and row[chain_index] is not None:
                try:
                    chain_id = int(row[chain_index])
                except (TypeError, ValueError):
                    chain_id = fallback_chain
            if chain_id <= 0:
                continue
            bucket = per_chain.setdefault(chain_id, set())
            if len(bucket) >= cap_per_chain:
                continue
            for index in token_indexes:
                if index < len(row):
                    value = _normalize_hex(str(row[index] or ""))
                    if value == NATIVE_TOKEN or ADDRESS_RE.fullmatch(value):
                        bucket.add(value)
    return per_chain


def _build_icon_overlay(
    datasets: dict[str, CachedDataset],
) -> tuple[dict[str, dict[str, str]], bool]:
    """Resolve icon URLs for every token visible in the attached datasets.

    Returns ``(overlay, pending)`` where overlay is ``{chain_id: {token: url}}``
    and ``pending`` means at least one chain's CoinGecko list is still being
    fetched in the background (the frontend retries once shortly after).
    """
    overlay: dict[str, dict[str, str]] = {}
    any_pending = False
    for chain_id, tokens in _dataset_token_addresses(datasets).items():
        icon_map, pending = _coingecko_icon_map_nowait(chain_id)
        any_pending = any_pending or pending
        chain_icons: dict[str, str] = {}
        for token in tokens:
            if token == NATIVE_TOKEN:
                url = COINGECKO_NATIVE_ICON_URLS.get(chain_id, "")
            else:
                url = icon_map.get(token, "")
            if url:
                chain_icons[token] = url
        if chain_icons:
            overlay[str(chain_id)] = chain_icons
    return overlay, any_pending


def _validate_scope(scope: str) -> str:
    value = scope.strip().lower() or "production"
    if value not in VALID_SCOPES:
        raise ValueError("environment_scope must be 'production' or 'testnet'")
    return value


def _resolve_chain(scope: str, chain_id: int, section: str) -> ChainInfo | None:
    if chain_id == 0 and section in ALL_NETWORK_SECTIONS:
        return None
    if chain_id == 0:
        if section == "live":
            chain_id = LIVE_DEFAULT_CHAIN_ID if scope == "production" else 11155111
        else:
            chain_id = DEFAULT_CHAIN_ID if scope == "production" else 11155111
    chain = COW_CHAINS.get(int(chain_id))
    if chain is None:
        raise ValueError(f"Unsupported CoW chain_id: {chain_id}")
    if chain.environment != scope:
        raise ValueError(f"chain_id {chain_id} is not in the {scope} scope")
    return chain


def _resolve_interval(interval: str, window_days: int) -> tuple[str, list[str]]:
    value = interval.strip().lower() or "1h"
    warnings: list[str] = []
    if value not in CANDLE_BUCKETS:
        value = "1h"
        warnings.append("coarsened_interval")
    if value == "5m" and (window_days == 0 or window_days > 7):
        value = "1h" if window_days <= 365 and window_days != 0 else "1d"
        warnings.append("coarsened_interval")
    if value == "1h" and (window_days == 0 or window_days > 365):
        value = "1d"
        warnings.append("coarsened_interval")
    return value, warnings


def _range_state(
    section: str,
    window_days: int,
    start_at: str,
    end_at: str,
) -> dict[str, Any]:
    if bool(start_at.strip()) != bool(end_at.strip()):
        raise ValueError("start_at and end_at must be provided together")
    if start_at and end_at:
        try:
            start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("start_at/end_at must be ISO-8601 timestamps") from exc
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start >= end:
            raise ValueError("start_at must be earlier than end_at")
        return {
            "kind": "absolute",
            "anchor": "explicit",
            "window_days": None,
            "start_at": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end_at": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    days = SECTION_DEFAULT_DAYS[section] if window_days < 0 else int(window_days)
    if days < 0:
        raise ValueError("window_days must be -1, 0, or a positive integer")
    return {
        "kind": "all" if days == 0 else "relative",
        "anchor": "latest_indexed",
        "window_days": days,
        "start_at": "",
        "end_at": "",
    }


def _scope_parameters(scope: str, chain: ChainInfo | None) -> dict[str, Any]:
    return {
        "env": scope,
        "chain_id": chain.chain_id if chain else 0,
        "native_symbol": chain.native_symbol if chain else "",
    }


def _scope_predicate(chain: ChainInfo | None, alias: str = "", scope: str = "production") -> str:
    prefix = f"{alias}." if alias else ""
    if chain is not None:
        return f"{prefix}environment={{env:String}} AND {prefix}chain_id={{chain_id:UInt64}}"
    ids = ",".join(str(c.chain_id) for c in COW_CHAINS.values() if c.environment == scope)
    return f"{prefix}environment={{env:String}} AND {prefix}chain_id IN ({ids})"


def _time_predicate(
    column: str,
    range_state: dict[str, Any],
    anchor_sql: str,
) -> tuple[str, dict[str, Any]]:
    if range_state["kind"] == "all":
        return "1", {}
    if range_state["kind"] == "absolute":
        return (
            f"{column} >= parseDateTime64BestEffort({{start_at:String}}) "
            f"AND {column} <= parseDateTime64BestEffort({{end_at:String}})",
            {"start_at": range_state["start_at"], "end_at": range_state["end_at"]},
        )
    return (
        f"{column} >= ({anchor_sql}) - toIntervalDay({{window_days:UInt32}})",
        {"window_days": int(range_state["window_days"])},
    )


def _token_metadata_cte() -> str:
    return """
tm AS (
    SELECT token,
           argMax(symbol, observed_at) AS symbol,
           argMax(name, observed_at) AS name,
           argMax(decimals, observed_at) AS decimals,
           max(observed_at) AS metadata_observed_at
    FROM cow_db.token_metadata
    WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    GROUP BY token
    UNION ALL
    SELECT '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
           {native_symbol:String}, {native_symbol:String}, toUInt8(18),
           toDateTime64(0, 3, 'UTC')
)"""


def _token_metadata_cte_multi(scope: str, chain: ChainInfo | None = None) -> str:
    """Multi-chain sibling of ``_token_metadata_cte`` at (chain_id, token) grain.

    Native-symbol rows come from the static chain registry (no user input is
    interpolated). Join with ``ON tmx.chain_id=<alias>.chain_id AND tmx.token=…``.
    """
    chains = [chain] if chain is not None else _chains_for_scope(scope)
    ids = ",".join(str(c.chain_id) for c in chains)
    native_tuples = ",".join(
        f"(toUInt64({c.chain_id}),'{c.native_symbol}')" for c in chains
    )
    return f"""
tmx AS (
    SELECT chain_id, token,
           argMax(symbol, observed_at) AS symbol,
           argMax(name, observed_at) AS name,
           argMax(decimals, observed_at) AS decimals,
           max(observed_at) AS metadata_observed_at
    FROM cow_db.token_metadata
    WHERE environment={{env:String}} AND chain_id IN ({ids})
    GROUP BY chain_id, token
    UNION ALL
    SELECT nt.1 AS chain_id,'{NATIVE_TOKEN}' AS token,nt.2 AS symbol,
           nt.2 AS name,toUInt8(18) AS decimals,
           toDateTime64(0,3,'UTC') AS metadata_observed_at
    FROM (SELECT arrayJoin([{native_tuples}]) AS nt)
)"""


def _trade_anchor(chain: ChainInfo | None) -> str:
    return (
        "SELECT max(block_timestamp) FROM cow_db.trades "
        f"WHERE {_scope_predicate(chain)} AND block_timestamp IS NOT NULL"
    )


#: Correlation/flow queries JOIN two big tables (trades ⋈ settlements), so
#: their peak memory is the hash-join build of the SMALLER side over the
#: window — unbounded at all-history that build is ~2.6M rows (~320 MB) and
#: OOMs the shared ClickHouse instance when it is already near its ceiling.
#: These specific analytical matrices are capped to a rolling window (90d hash
#: ≈ 27 MB on the busiest chain). This is NOT the history-tape clamp the user
#: rejected — the Trades/Markets tapes stay fully unclamped; only the
#: solver/trader CORRELATION views (which are meaningless over "all time"
#: anyway) are bounded, and the cap is disclosed in each dataset's (i) note.
CORRELATION_MAX_WINDOW_DAYS = 90


def _capped_analytical_range(range_state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Clamp a correlation query's window to CORRELATION_MAX_WINDOW_DAYS.

    Returns ``(range_state, was_capped)``. Absolute ranges are left as-is
    (the user picked explicit bounds); only ``all`` and long relative windows
    are pulled back to the rolling cap.
    """
    kind = range_state.get("kind")
    if kind == "all" or (
        kind == "relative"
        and int(range_state.get("window_days") or 0) > CORRELATION_MAX_WINDOW_DAYS
    ):
        return (
            {
                "kind": "relative",
                "anchor": "latest_indexed",
                "window_days": CORRELATION_MAX_WINDOW_DAYS,
                "start_at": "",
                "end_at": "",
            },
            True,
        )
    return range_state, False


def _settlement_time_bound(range_state: dict[str, Any]) -> str:
    """Time predicate for a settlements CTE, anchored on the settlements table.

    The ``exec`` CTE (settlement executor per tx) and similar joins previously
    aggregated the ENTIRE per-chain settlements table (~2M rows) as the
    hash-join build side — two of those concurrent tipped the shared
    ClickHouse instance over its 10.8 GiB ceiling (code 241). Bounding to the
    query window shrinks the build to the window's settlements (e.g. ~44k for
    30 days on Gnosis). Reuses the caller's already-bound window_days/start_at/
    end_at params (settlements share block_timestamp semantics with trades)."""
    anchor = (
        "SELECT max(block_timestamp) FROM cow_db.settlements "
        "WHERE environment={env:String} AND chain_id={chain_id:UInt64}"
    )
    pred, _ = _time_predicate("block_timestamp", range_state, anchor)
    return pred


def _grouped_time(column: str, anchor_alias: str, range_state: dict[str, Any]) -> str:
    """Time predicate for grouped multi-chain scans.

    Relative windows anchor per chain via an ``anchors``-style CTE joined on
    chain_id (``anchor_alias.anchor``) instead of one scalar subquery per chain.
    """
    if range_state["kind"] == "all":
        return "1"
    if range_state["kind"] == "absolute":
        return (
            f"{column}>=parseDateTime64BestEffort({{start_at:String}}) AND "
            f"{column}<=parseDateTime64BestEffort({{end_at:String}})"
        )
    return f"{column}>={anchor_alias}.anchor-toIntervalDay({{window_days:UInt32}})"


def _time_params(range_state: dict[str, Any]) -> dict[str, Any]:
    if range_state["kind"] == "all":
        return {}
    if range_state["kind"] == "absolute":
        return {"start_at": range_state["start_at"], "end_at": range_state["end_at"]}
    return {"window_days": int(range_state["window_days"])}


def _per_chain_time(range_state: dict[str, Any]):
    """Per-chain-arm time predicate factory (scalar anchor subqueries).

    Multi-chain scans over ``trades_canonical`` MUST stay per-chain-bounded:
    expanding the reorg-safe view (FINAL + chain_blocks join + checkpoint
    subquery) for ten chains in one pass exceeds ClickHouse's server memory
    (observed ~11 GiB). UNION arms keep each expansion single-chain.
    """
    def predicate(column: str, anchor: str) -> str:
        if range_state["kind"] == "all":
            return "1"
        if range_state["kind"] == "absolute":
            return (
                f"{column}>=parseDateTime64BestEffort({{start_at:String}}) AND "
                f"{column}<=parseDateTime64BestEffort({{end_at:String}})"
            )
        return f"{column}>={anchor}-toIntervalDay({{window_days:UInt32}})"

    return predicate


def _chain_trade_anchor(cid: int) -> str:
    # Anchor on the BASE trades table: max() over duplicate RMT versions is
    # identical to max() over the deduped view, and skipping the canonical
    # view's chain_blocks join keeps the scalar subquery cheap.
    return (
        "(SELECT max(block_timestamp) FROM cow_db.trades "
        f"WHERE environment={{env:String}} AND chain_id={cid})"
    )


def _chain_checkpoint(cid: int) -> str:
    """Committed-checkpoint scalar for one chain (bounds base-table scans)."""
    return (
        "(SELECT argMax(block_number,updated_at) FROM cow_db.indexing_checkpoints "
        f"WHERE environment={{env:String}} AND chain_id={cid} AND source='rpc')"
    )


#: Aggregate scans over many chains use the BASE trades table with
#: dedup-invariant aggregates instead of the reorg-safe canonical view:
#: ``uniq((tx_hash,log_index,order_uid))`` deduplicates ReplacingMergeTree
#: versions in CONSTANT memory (HLL, ~0.8% max error; the RMT is >99.9%
#: merged anyway) without FINAL, and skipping the view's chain_blocks join
#: (millions of rows per chain) is what keeps ten-chain aggregates inside the
#: ClickHouse instance's memory/time budget — uniqExact retains every distinct
#: key and was a memory risk at all-history. Small bounded tables
#: (competition_*, orders) keep uniqExact. Rows from orphaned (reorged)
#: blocks may be counted — a marginal overcount the coverage mode discloses.
TRADE_KEY = "(tx_hash,log_index,order_uid)"
BASE_DEDUP_MODE = "checkpoint_bounded_base_dedup"


def _shared_arm_ctes(ids: str) -> str:
    """Shared per-chain scalar lookups for multi-arm statements.

    ``cp`` = committed RPC checkpoint block, ``ta`` = latest trade timestamp
    (window anchor). One grouped scan each instead of a scalar subquery per
    arm — smaller SQL (the assembled statement must stay under the length
    cap) and fewer scans.
    """
    return f"""
cp AS (
  SELECT chain_id, argMax(block_number,updated_at) AS b
  FROM cow_db.indexing_checkpoints
  WHERE environment={{env:String}} AND chain_id IN ({ids}) AND source='rpc'
  GROUP BY chain_id
), ta AS (
  SELECT chain_id, max(block_timestamp) AS a
  FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id
)"""


def _arm_checkpoint(cid: int) -> str:
    return f"t.block_number<=(SELECT b FROM cp WHERE cp.chain_id={cid})"


def _arm_window(cid: int, range_state: dict[str, Any]) -> str:
    if range_state["kind"] == "all":
        return "1"
    if range_state["kind"] == "absolute":
        return (
            "t.block_timestamp>=parseDateTime64BestEffort({start_at:String}) AND "
            "t.block_timestamp<=parseDateTime64BestEffort({end_at:String})"
        )
    # GLOBAL anchor (max over every in-scope chain): a stale chain (e.g.
    # mainnet, months behind) must NOT render its own final days inside an
    # all-networks "last N days" view as if it were current — with the global
    # anchor its rows predate the window and fall out naturally, and the
    # exclusion self-heals the moment its indexer catches up. Single-chain
    # sections pass exactly one chain, where max(a) == that chain's own
    # anchor, so a stopped indexer still renders its trailing window there.
    del cid  # anchor is deliberately scope-global, not per-arm
    return (
        "t.block_timestamp>=(SELECT max(a) FROM ta)"
        "-toIntervalDay({window_days:UInt32})"
    )


def _overview_specs(
    scope: str,
    range_state: dict[str, Any],
    chain: ChainInfo | None = None,
) -> list[QuerySpec]:
    params = _scope_parameters(scope, None)
    chain_ids = [chain.chain_id] if chain else [c.chain_id for c in _chains_for_scope(scope)]
    ids = ",".join(str(chain_id) for chain_id in chain_ids)
    scope_pred = f"environment={{env:String}} AND chain_id IN ({ids})"
    p = {**params, **_time_params(range_state)}
    per_chain_time = _per_chain_time(range_state)

    network_columns = (
        "chain_id", "trade_count", "settlement_transactions", "order_count",
        "observed_open_orders", "competition_count_all_indexed", "indexed_from",
        "indexed_to", "source_observed_at", "order_indexed_from",
        "order_indexed_to", "order_observed_at", "competition_observed_at",
    )
    shared_ctes = _shared_arm_ctes(ids)
    order_anchor_cte = f"""
oa AS (
  SELECT chain_id, max(creation_date) AS a
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id
)"""

    def order_window(cid: int) -> str:
        if range_state["kind"] == "all":
            return "1"
        if range_state["kind"] == "absolute":
            return (
                "creation_date>=parseDateTime64BestEffort({start_at:String}) AND "
                "creation_date<=parseDateTime64BestEffort({end_at:String})"
            )
        # Global anchor — same stale-chain semantics as _arm_window.
        del cid
        return (
            "creation_date>=(SELECT max(a) FROM oa)"
            "-toIntervalDay({window_days:UInt32})"
        )

    competitions_cte = f"""
cc AS (
  SELECT chain_id, count() AS a, maxOrNull(observed_at) AS b
  FROM cow_db.solver_competitions FINAL
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id
)"""
    # Grouped single-pass shape: one trades scan and one orders scan, each
    # GROUP BY chain_id, joined onto an arrayJoin chain spine — replaces the
    # per-chain cross-join arms (10x the scans AND over the SQL length cap).
    # orders: argMax dedup grouped on the sort-key prefix streams, replacing
    # FINAL, whose k-way merge was the memory-heavy part of the all-network
    # summary; status/creation_date are latest-version exact.
    trades_cte = f"""
tr AS (
  SELECT t.chain_id AS chain_id,uniq({TRADE_KEY}) AS a,uniq(tx_hash) AS b,
         minOrNull(block_timestamp) AS c,maxOrNull(block_timestamp) AS d,
         maxOrNull(observed_at) AS e
  FROM cow_db.trades AS t
  INNER JOIN cp ON cp.chain_id=t.chain_id
  WHERE t.environment={{env:String}} AND t.chain_id IN ({ids})
    AND t.block_number<=cp.b AND {_arm_window(0, range_state)}
  GROUP BY t.chain_id
)"""
    # Counts + open-count are split so NEITHER deduplicates the whole orders
    # table. Once the historical backfill grew `orders` past ~4M rows, the old
    # `argMax(...) GROUP BY (chain_id, order_uid)` dedup over EVERY order built a
    # multi-million-entry hash and blew the 2 GiB per-query budget (code 241).
    # og:  creation_date is IMMUTABLE per order_uid, so counts/date-range read
    #      the raw window-filtered scan directly — uniq(order_uid) is HLL
    #      (constant memory) and the GROUP BY chain_id hash is tiny.
    # ogopen: the open count needs the LATEST status, so it must dedup — but a
    #      currently-open order MUST be unexpired (valid_to > now, immutable), a
    #      tiny live set, so the argMax runs over that only; expired backfilled
    #      history drops out and never bloats the hash.
    orders_cte = f"""
og AS (
  SELECT chain_id,uniq(order_uid) AS a,
         minOrNull(creation_date) AS c,maxOrNull(creation_date) AS d,
         maxOrNull(observed_at) AS e
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id IN ({ids}) AND {order_window(0)}
  GROUP BY chain_id
),
ogopen AS (
  SELECT chain_id,countIf(status='open') AS b
  FROM (
    SELECT chain_id,order_uid,argMax(status,observed_at) AS status
    FROM cow_db.orders
    WHERE environment={{env:String}} AND chain_id IN ({ids})
      AND valid_to>toUnixTimestamp(now())
    GROUP BY chain_id,order_uid
  )
  GROUP BY chain_id
)"""
    network_summary = (
        f"WITH {shared_ctes},{order_anchor_cte},{competitions_cte},"
        f"{trades_cte},{orders_cte}\n"
        f"SELECT spine.chain_id AS chain_id,coalesce(tr.a,0) AS trade_count,"
        "coalesce(tr.b,0) AS settlement_transactions,coalesce(og.a,0) AS order_count,"
        "coalesce(ogopen.b,0) AS observed_open_orders,"
        "coalesce(cc.a,0) AS competition_count_all_indexed,"
        "tr.c AS indexed_from,tr.d AS indexed_to,"
        "tr.e AS source_observed_at,og.c AS order_indexed_from,"
        "og.d AS order_indexed_to,og.e AS order_observed_at,"
        "cc.b AS competition_observed_at\n"
        f"FROM (SELECT arrayJoin([{ids}]) AS chain_id) AS spine\n"
        "LEFT JOIN tr ON tr.chain_id=spine.chain_id\n"
        "LEFT JOIN og ON og.chain_id=spine.chain_id\n"
        "LEFT JOIN ogopen ON ogopen.chain_id=spine.chain_id\n"
        "LEFT JOIN cc ON cc.chain_id=spine.chain_id\n"
        "ORDER BY spine.chain_id"
    )
    coverage = f"""
WITH cp AS (
  SELECT chain_id, argMax(block_number, updated_at) AS checkpoint_block,
         max(updated_at) AS checkpoint_updated_at
  FROM cow_db.indexing_checkpoints
  WHERE {scope_pred} AND source='rpc'
  GROUP BY chain_id
), blocks AS (
  -- block_number IS the sort key → this IN-set prunes chain_blocks from the
  -- whole ~9.2M-row table to the ~10 checkpoint blocks. Without it the JOIN
  -- condition alone forces a full-table scan + hash. (No FINAL: argMax dedups.)
  SELECT b.chain_id,b.block_number,
         argMax(b.block_timestamp,b.observed_at) AS checkpoint_timestamp
  FROM cow_db.chain_blocks AS b
  INNER JOIN cp
    ON b.chain_id=cp.chain_id AND b.block_number=cp.checkpoint_block
  WHERE b.environment={{env:String}} AND b.chain_id IN ({ids})
    AND b.block_number IN (SELECT checkpoint_block FROM cp)
  GROUP BY b.chain_id,b.block_number
), obs AS (
  -- max(observed_at) is dedup-invariant; the base table avoids expanding the
  -- canonical view (FINAL + chain_blocks join) once per chain.
  SELECT chain_id, max(observed_at) AS trade_observed_at
  FROM cow_db.trades WHERE {scope_pred} GROUP BY chain_id
), ord AS (
  -- max(observed_at) is FINAL-invariant on a ReplacingMergeTree(observed_at);
  -- skipping FINAL avoids the merge cost on the largest per-chain table.
  SELECT chain_id, max(observed_at) AS order_observed_at
  FROM cow_db.orders WHERE {scope_pred} GROUP BY chain_id
), comp AS (
  SELECT chain_id, max(auction_block) AS max_competition_block,
         max(observed_at) AS competition_observed_at
  FROM cow_db.solver_competitions FINAL WHERE {scope_pred} GROUP BY chain_id
), np AS (
  -- native_prices keeps observed_at in its sort key (time series); FINAL does
  -- not collapse snapshots there, so a plain max() is the correct read.
  SELECT chain_id, max(observed_at) AS native_price_observed_at
  FROM cow_db.native_prices WHERE {scope_pred} GROUP BY chain_id
)
SELECT n.chain_id AS chain_id, cp.checkpoint_block,
       nullIf(blocks.checkpoint_timestamp,toDateTime(0)) AS checkpoint_timestamp,
       cp.checkpoint_updated_at, obs.trade_observed_at, ord.order_observed_at,
       comp.max_competition_block,comp.competition_observed_at,
       np.native_price_observed_at,
       greatest(obs.trade_observed_at,ord.order_observed_at,
                comp.competition_observed_at,np.native_price_observed_at) AS source_observed_at
FROM (SELECT arrayJoin([{ids}]) AS chain_id) AS n
LEFT JOIN cp ON n.chain_id=cp.chain_id
LEFT JOIN blocks ON cp.chain_id=blocks.chain_id AND cp.checkpoint_block=blocks.block_number
LEFT JOIN obs ON n.chain_id=obs.chain_id
LEFT JOIN ord ON n.chain_id=ord.chain_id
LEFT JOIN comp ON n.chain_id=comp.chain_id
LEFT JOIN np ON n.chain_id=np.chain_id
ORDER BY n.chain_id"""
    tmx = _token_metadata_cte_multi(scope, chain)
    activity_parts: list[str] = []
    pair_parts: list[str] = []
    fee_parts: list[str] = []
    for cid in chain_ids:
        base_where = (
            f"t.environment={{env:String}} AND t.chain_id={cid}"
            f" AND {_arm_checkpoint(cid)}"
            f" AND t.block_timestamp IS NOT NULL AND {_arm_window(cid, range_state)}"
        )
        activity_parts.append(f"""
SELECT toStartOfDay(t.block_timestamp) AS bucket,{cid} AS chain_id,
       uniq({TRADE_KEY}) AS trade_count,uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE {base_where}
GROUP BY bucket""")
        pair_parts.append(f"""
SELECT {cid} AS chain_id,least(t.sell_token,t.buy_token) AS token0,
       greatest(t.sell_token,t.buy_token) AS token1,
       uniq({TRADE_KEY}) AS fill_count,
       uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE {base_where}
GROUP BY token0,token1""")
        # Fees stand alone on protocol_fees (small, API-enriched): joining the
        # trades view only supplied block timestamps and was the memory/time
        # hog; observed_at is the honest basis for API-sourced fee rows.
        fee_window = per_chain_time(
            "f.observed_at",
            "(SELECT max(observed_at) FROM cow_db.protocol_fees "
            f"WHERE environment={{env:String}} AND chain_id={cid})",
        )
        fee_parts.append(f"""
SELECT {cid} AS chain_id,f.token AS token,f.policy AS policy_raw,
       count() AS fee_entries,uniqExact(f.order_uid) AS orders,
       sum(f.amount) AS amount_sum,
       min(f.observed_at) AS indexed_from,max(f.observed_at) AS indexed_to,
       max(f.observed_at) AS source_observed_at
FROM cow_db.protocol_fees AS f FINAL
WHERE f.environment={{env:String}} AND f.chain_id={cid} AND {fee_window}
GROUP BY f.token,f.policy""")
    activity = (
        f"WITH {shared_ctes}\n"
        "SELECT * FROM (\n" + "\nUNION ALL\n".join(activity_parts)
        + "\n) ORDER BY bucket,chain_id"
    )
    pair_union = "\nUNION ALL\n".join(pair_parts)
    top_pairs = f"""WITH {shared_ctes},{tmx}
SELECT p.chain_id AS chain_id, p.token0 AS token0, p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       if(m0.token='',NULL,m0.decimals) AS token0_decimals,
       if(m1.token='',NULL,m1.decimals) AS token1_decimals,
       p.fill_count AS fill_count, p.settlement_transactions AS settlement_transactions,
       p.indexed_from AS indexed_from, p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM ({pair_union}) AS p
LEFT JOIN tmx AS m0 ON m0.chain_id=p.chain_id AND m0.token=p.token0
LEFT JOIN tmx AS m1 ON m1.chain_id=p.chain_id AND m1.token=p.token1
ORDER BY p.fill_count DESC, p.chain_id, p.token0, p.token1
LIMIT 500"""
    fee_union = "\nUNION ALL\n".join(fee_parts)
    fees = f"""WITH {tmx}
SELECT u.chain_id AS chain_id, u.token AS token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       u.policy_raw AS policy_raw,
       multiIf(positionCaseInsensitive(u.policy_raw,'priceImprovement')>0,'price_improvement',
               positionCaseInsensitive(u.policy_raw,'surplus')>0,'surplus',
               positionCaseInsensitive(u.policy_raw,'volume')>0,'volume','other') AS policy_family,
       u.fee_entries AS fee_entries, u.orders AS orders,
       toString(u.amount_sum) AS amount_raw,
       if(tm.token='',NULL,tm.decimals) AS token_decimals,
       if(tm.token='',NULL,toFloat64(u.amount_sum)/pow(10,toFloat64(tm.decimals))) AS amount,
       u.indexed_from AS indexed_from, u.indexed_to AS indexed_to,
       u.source_observed_at AS source_observed_at
FROM ({fee_union}) AS u
LEFT JOIN tmx AS tm ON tm.chain_id=u.chain_id AND tm.token=u.token
ORDER BY u.fee_entries DESC, u.chain_id, u.token, u.policy_raw"""
    # ---- Protocol-wide aggregates (Dune-style KPI tiles, pies, share) ----
    # Volume valuation: cow_db has NO historical price source (native_prices
    # is a live snapshot; auction_prices is patchy), so protocol KPIs are
    # counts-first. An approximate native-denominated volume is attached ONLY
    # for short relative windows (<= 7 days), valued at the CURRENT
    # native_prices snapshot (atoms x price / 1e18 = native wei), and is NULL
    # otherwise — the estimate label is a frontend/disclosure concern.
    volume_ok = (
        range_state["kind"] == "relative"
        and int(range_state.get("window_days") or 0) <= 7
    )
    if volume_ok:
        np_cte = f""",
np AS (
  SELECT chain_id, token, argMax(native_price, observed_at) AS native_price
  FROM cow_db.native_prices
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id, token
)"""
        np_join = "  LEFT JOIN np ON np.chain_id=t.chain_id AND np.token=t.sell_token\n"
        vol_expr = (
            "sumIf(toFloat64(t.sell_amount)*toFloat64OrZero(np.native_price)/1e36,"
            "np.token!='') AS approx_native_volume"
        )
    else:
        np_cte = ""
        np_join = ""
        vol_expr = "CAST(NULL AS Nullable(Float64)) AS approx_native_volume"
    kpi_where = (
        f"t.environment={{env:String}} AND t.chain_id IN ({ids})"
        f" AND t.block_number<=cp.b AND t.block_timestamp IS NOT NULL"
        f" AND {_arm_window(0, range_state)}"
    )
    kpi_select = f"""
  SELECT uniq({TRADE_KEY}) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
         uniq(t.owner) AS unique_traders,
         uniq(tuple(least(t.sell_token,t.buy_token),greatest(t.sell_token,t.buy_token))) AS unique_pairs,
         {vol_expr},
         minOrNull(t.block_timestamp) AS indexed_from,
         maxOrNull(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN cp ON cp.chain_id=t.chain_id
{np_join}  WHERE {kpi_where}"""
    protocol_kpis = f"""WITH {shared_ctes}{np_cte}
SELECT * FROM (
  SELECT t.chain_id AS chain_id,{kpi_select.replace("SELECT ", "", 1)}
  GROUP BY t.chain_id
UNION ALL
  SELECT toUInt64(0) AS chain_id,{kpi_select.replace("SELECT ", "", 1)}
) ORDER BY chain_id"""
    # All-time totals feed the distribution pies. Deliberately ignores the
    # global window (always all indexed history — disclosed) and deliberately
    # KEEPS NULL-timestamp rows (BNB) in the counts: an all-time count needs
    # no time axis, and excluding BNB here would silently understate it.
    alltime_totals = f"""WITH {shared_ctes}
SELECT t.chain_id AS chain_id,
       uniq({TRADE_KEY}) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       uniq(t.owner) AS unique_traders,
       minOrNull(t.block_timestamp) AS first_trade_at,
       maxOrNull(t.block_timestamp) AS last_trade_at,
       minOrNull(t.block_timestamp) AS indexed_from,
       maxOrNull(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
INNER JOIN cp ON cp.chain_id=t.chain_id
WHERE t.environment={{env:String}} AND t.chain_id IN ({ids}) AND t.block_number<=cp.b
GROUP BY t.chain_id
ORDER BY t.chain_id"""
    # Share-over-time: ONE grouped scan (bucket x chain hash stays tiny even
    # at all-history — weeks x 10 chains), NOT ten UNION arms. The frontend
    # normalizes per bucket for the 100%-share view.
    share_bucket = (
        "toStartOfWeek(t.block_timestamp)"
        if range_state["kind"] == "all"
        or int(range_state.get("window_days") or 0) > 180
        else "toStartOfDay(t.block_timestamp)"
    )
    chain_share_trend = f"""WITH {shared_ctes}
SELECT {share_bucket} AS bucket,t.chain_id AS chain_id,
       uniq({TRADE_KEY}) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
INNER JOIN cp ON cp.chain_id=t.chain_id
WHERE t.environment={{env:String}} AND t.chain_id IN ({ids})
  AND t.block_number<=cp.b AND t.block_timestamp IS NOT NULL
  AND {_arm_window(0, range_state)}
GROUP BY bucket,t.chain_id
ORDER BY bucket,t.chain_id"""
    return [
        QuerySpec("network_summary", "Indexed network summary", network_summary, p, "block_timestamp", BASE_DEDUP_MODE),
        QuerySpec("coverage_matrix", "Coverage matrix", coverage, params, "observed_at", "observed_series", 60),
        QuerySpec("network_activity", "Execution activity", activity, p, "block_timestamp", BASE_DEDUP_MODE),
        QuerySpec("top_pairs", "Top token pairs", top_pairs, p, "block_timestamp", BASE_DEDUP_MODE, 900),
        QuerySpec("fee_policy_counts", "Indexed fee-policy counts", fees, p, "observed_at", "observed_series", 900),
        QuerySpec("protocol_kpis", "Protocol KPIs by network", protocol_kpis, p, "block_timestamp", BASE_DEDUP_MODE, 900),
        QuerySpec("alltime_chain_totals", "All-time totals by network", alltime_totals, dict(params), "block_timestamp", BASE_DEDUP_MODE, 3600),
        QuerySpec("chain_share_trend", "Network share of activity over time", chain_share_trend, p, "block_timestamp", BASE_DEDUP_MODE, 900),
    ]


def _validate_token(value: str, label: str) -> str:
    token = _normalize_hex(value)
    if not ADDRESS_RE.fullmatch(token):
        raise ValueError(f"{label} must be a 0x-prefixed EVM token address")
    return token


def _resolve_pair(
    ch: ClickHouseManager,
    chain: ChainInfo,
    base_token: str,
    quote_token: str,
) -> tuple[str, str]:
    if bool(base_token.strip()) != bool(quote_token.strip()):
        raise ValueError("base_token and quote_token must be provided together")
    if base_token and quote_token:
        base = _validate_token(base_token, "base_token")
        quote = _validate_token(quote_token, "quote_token")
        if base == quote:
            raise ValueError("base_token and quote_token must differ")
        return base, quote
    # Default-pair probe: the busiest pair of the last 30 indexed days is more
    # than enough signal — an unbounded probe through the canonical view paid
    # a FINAL + chain_blocks join on EVERY markets/orders/solvers load. Falls
    # back to all-history (still base-table, dedup-free counts) only when the
    # recent window is empty (e.g. a stale chain).
    recent_sql = """
SELECT least(sell_token,buy_token) AS token0,
       greatest(sell_token,buy_token) AS token1, count() AS fills
FROM cow_db.trades
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  AND sell_token != buy_token
  AND block_timestamp >= now() - INTERVAL 30 DAY
GROUP BY token0, token1
ORDER BY fills DESC, token0, token1
LIMIT 1"""
    fallback_sql = """
SELECT least(sell_token,buy_token) AS token0,
       greatest(sell_token,buy_token) AS token1, count() AS fills
FROM cow_db.trades
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  AND sell_token != buy_token
GROUP BY token0, token1
ORDER BY fills DESC, token0, token1
LIMIT 1"""
    params = {"env": chain.environment, "chain_id": chain.chain_id}
    result = mini_apps.run_structured_query(
        ch, recent_sql, COW_DB, params, requested_max_rows=1,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )
    if not result.rows:
        result = mini_apps.run_structured_query(
            ch, fallback_sql, COW_DB, params, requested_max_rows=1,
            query_budget=INTERACTIVE_QUERY_BUDGET,
        )
    if not result.rows:
        return "", ""
    return str(result.rows[0][0]).lower(), str(result.rows[0][1]).lower()


def _pair_time_predicate(
    range_state: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    # max() needs no dedup and no reorg filtering — read the base table
    # directly instead of paying the canonical view's FINAL + chain_blocks
    # join on every market query.
    anchor = """SELECT max(block_timestamp) FROM cow_db.trades
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  AND ((sell_token={base:String} AND buy_token={quote:String})
       OR (sell_token={quote:String} AND buy_token={base:String}))
  AND block_timestamp IS NOT NULL"""
    return _time_predicate("t.block_timestamp", range_state, anchor)


def _market_specs(
    chain: ChainInfo,
    pair: tuple[str, str],
    interval: str,
    range_state: dict[str, Any],
    depth_at: str = "",
    heatmap_window: str = "7d",
) -> list[QuerySpec]:
    base, quote = pair
    # Pair picker options: the 50 busiest pairs of the last 30 days with
    # symbols AND addresses — feeds the base/quote dropdowns so users are not
    # typing raw token addresses. Cheap streaming aggregate; exists even when
    # no pair could be resolved so the picker can still offer choices.
    options_params = _scope_parameters(chain.environment, chain)
    pair_options = f"""
WITH {_token_metadata_cte()},
p AS (
  SELECT least(sell_token,buy_token) AS token0,greatest(sell_token,buy_token) AS token1,
         uniq((tx_hash,log_index,order_uid)) AS fill_count,
         min(block_timestamp) AS indexed_from,max(block_timestamp) AS indexed_to,
         max(observed_at) AS source_observed_at
  FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND sell_token != buy_token AND block_timestamp IS NOT NULL
    AND block_timestamp >= now() - INTERVAL 30 DAY
  GROUP BY token0,token1
  ORDER BY fill_count DESC,token0,token1
  LIMIT 50
)
SELECT p.token0 AS token0,p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.fill_count AS fill_count,
       p.indexed_from AS indexed_from,p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM p
LEFT JOIN tm AS m0 ON m0.token=p.token0
LEFT JOIN tm AS m1 ON m1.token=p.token1
ORDER BY p.fill_count DESC,p.token0,p.token1"""
    pair_options_spec = QuerySpec(
        "pair_options", "Pair picker options", pair_options, options_params,
        "block_timestamp", BASE_DEDUP_MODE, 900,
    )
    if not base or not quote:
        # The horizon row is pair-independent — return it so the depth group
        # still loads (coverage disclosure) when no pair could be resolved.
        return [pair_options_spec, *_pair_depth_specs(chain, ("", ""), depth_at)]
    params = {
        **_scope_parameters(chain.environment, chain),
        "base": base,
        "quote": quote,
    }
    time_pred, time_params = _pair_time_predicate(range_state)
    params.update(time_params)
    token_cte = _token_metadata_cte()
    pair_filter = """((t.sell_token={base:String} AND t.buy_token={quote:String})
                    OR (t.sell_token={quote:String} AND t.buy_token={base:String}))"""
    market_summary = f"""
WITH {token_cte}
SELECT {{base:String}} AS base_token, {{quote:String}} AS quote_token,
       (SELECT anyOrNull(symbol) FROM tm WHERE token={{base:String}}) AS base_symbol,
       (SELECT anyOrNull(symbol) FROM tm WHERE token={{quote:String}}) AS quote_symbol,
       (SELECT anyOrNull(decimals) FROM tm WHERE token={{base:String}}) AS base_decimals,
       (SELECT anyOrNull(decimals) FROM tm WHERE token={{quote:String}}) AS quote_decimals,
       uniq((t.tx_hash,t.log_index,t.order_uid)) AS fill_count,
       uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from, max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE {_scope_predicate(chain, 't')} AND {pair_filter}
  AND t.block_timestamp IS NOT NULL AND {time_pred}
ORDER BY base_token,quote_token"""
    bucket = CANDLE_BUCKETS[interval]
    # The dedup subquery matters for VOLUME correctness: recent fills sit in
    # unmerged ReplacingMergeTree parts (and API+RPC dual-source rows), so a
    # raw read double-counts sums. Pair-filtered sets are small enough that
    # the argMax GROUP BY streams cheaply.
    candles = f"""
WITH {token_cte}, dedup AS (
  SELECT t.tx_hash AS tx_hash, t.log_index AS log_index, t.order_uid AS order_uid,
         argMax(t.block_timestamp,t.observed_at) AS block_timestamp,
         argMax(t.sell_token,t.observed_at) AS sell_token,
         argMax(t.sell_amount,t.observed_at) AS sell_amount,
         argMax(t.buy_amount,t.observed_at) AS buy_amount,
         max(t.observed_at) AS observed_at
  FROM cow_db.trades AS t
  WHERE {_scope_predicate(chain, 't')} AND {pair_filter}
    AND t.block_timestamp IS NOT NULL AND {time_pred}
  GROUP BY t.tx_hash, t.log_index, t.order_uid
), fills AS (
  SELECT d.block_timestamp, d.log_index, d.tx_hash, d.order_uid,
         if(d.sell_token={{base:String}},
            toFloat64(d.sell_amount)/pow(10,toFloat64(b.decimals)),
            toFloat64(d.buy_amount)/pow(10,toFloat64(b.decimals))) AS base_qty,
         if(d.sell_token={{base:String}},
            toFloat64(d.buy_amount)/pow(10,toFloat64(q.decimals)),
            toFloat64(d.sell_amount)/pow(10,toFloat64(q.decimals))) AS quote_qty,
         d.observed_at
  FROM dedup AS d
  INNER JOIN tm AS b ON b.token={{base:String}}
  INNER JOIN tm AS q ON q.token={{quote:String}}
), priced AS (
  SELECT *, quote_qty/nullIf(base_qty,0) AS price
  FROM fills WHERE base_qty>0 AND quote_qty>=0
)
SELECT {bucket} AS bucket,
       argMin(price, tuple(block_timestamp,log_index,tx_hash,order_uid)) AS open,
       max(price) AS high, min(price) AS low,
       argMax(price, tuple(block_timestamp,log_index,tx_hash,order_uid)) AS close,
       sum(quote_qty)/nullIf(sum(base_qty),0) AS vwap,
       sum(base_qty) AS base_volume, sum(quote_qty) AS quote_volume,
       count() AS fill_count, min(block_timestamp) AS indexed_from,
       max(block_timestamp) AS indexed_to, max(observed_at) AS source_observed_at
FROM priced
GROUP BY bucket
ORDER BY bucket"""
    # Top-N-first tape (see _trade_specs): a plain ORDER BY … LIMIT over the
    # base table is a bounded heap sort, memory-safe at any window; dedup
    # happens over the selected set only, and metadata joins only the capped
    # rows. The checkpoint CTE replaces the canonical view's chain_blocks join.
    recent = f"""
WITH {token_cte}, cp AS (
  SELECT argMax(block_number,updated_at) AS b
  FROM cow_db.indexing_checkpoints
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND source='rpc'
)
SELECT u.block_timestamp AS block_timestamp, u.tx_hash AS tx_hash, u.order_uid AS order_uid,
       u.log_index AS log_index, u.owner AS owner,
       u.sell_token AS sell_token, if(s.token='',u.sell_token,s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       u.buy_token AS buy_token, if(b.token='',u.buy_token,b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(u.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(u.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       u.source AS source,
       u.obs_at AS source_observed_at
FROM (
  SELECT tx_hash,log_index,order_uid,
         argMax(block_timestamp,observed_at) AS block_timestamp,
         argMax(owner,observed_at) AS owner,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         argMax(fee_amount,observed_at) AS fee_amount,
         argMax(source,observed_at) AS source,
         max(observed_at) AS obs_at
  FROM (
    SELECT t.tx_hash,t.log_index,t.order_uid,t.block_timestamp,t.owner,
           t.sell_token,t.buy_token,t.sell_amount,t.buy_amount,t.fee_amount,
           t.source,t.observed_at
    FROM cow_db.trades AS t
    WHERE {_scope_predicate(chain, 't')} AND {pair_filter}
      AND t.block_number<=(SELECT b FROM cp)
      AND t.block_timestamp IS NOT NULL AND {time_pred}
    ORDER BY t.block_timestamp DESC
    LIMIT {TAPE_ARM_LIMIT}
  )
  GROUP BY tx_hash,log_index,order_uid
  ORDER BY block_timestamp DESC
  LIMIT {ROW_CAP}
) AS u
LEFT JOIN tm AS s ON s.token=u.sell_token
LEFT JOIN tm AS b ON b.token=u.buy_token
ORDER BY u.block_timestamp DESC, u.log_index DESC, u.tx_hash DESC, u.order_uid DESC"""
    # Window anchor without a chain_blocks-FINAL triple join: max block time
    # over the (few thousand) competition auction blocks, index-looked-up.
    auction_anchor = """SELECT max(block_timestamp) FROM cow_db.chain_blocks
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  AND block_number IN (
    SELECT argMax(auction_block, observed_at) FROM cow_db.solver_competitions FINAL
    WHERE environment={env:String} AND chain_id={chain_id:UInt64} GROUP BY auction_id)"""
    auction_time, _ = _time_predicate(
        "blocks.auction_timestamp", range_state, auction_anchor
    )
    auction_reference = f"""
WITH {token_cte}, bp AS (
  SELECT auction_id, argMax(price,observed_at) AS base_price,
         max(observed_at) AS base_observed_at
  FROM cow_db.auction_prices
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND token={{base:String}}
  GROUP BY auction_id
), qp AS (
  SELECT auction_id, argMax(price,observed_at) AS quote_price,
         max(observed_at) AS quote_observed_at
  FROM cow_db.auction_prices
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND token={{quote:String}}
  GROUP BY auction_id
), comp AS (
  SELECT auction_id, argMax(auction_block,observed_at) AS auction_block,
         max(observed_at) AS competition_observed_at
  FROM cow_db.solver_competitions FINAL
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
  GROUP BY auction_id
), blocks AS (
  -- Only the auction blocks (thousands, index-lookup) instead of the whole
  -- chain_blocks table with FINAL (millions of rows — a prior OOM source).
  SELECT block_number,argMax(block_timestamp,observed_at) AS auction_timestamp
  FROM cow_db.chain_blocks
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND block_number IN (SELECT auction_block FROM comp)
  GROUP BY block_number
)
SELECT bp.auction_id, blocks.auction_timestamp,
       toFloat64(bp.base_price)/nullIf(toFloat64(qp.quote_price),0)
         * pow(10,toFloat64((SELECT anyOrNull(decimals) FROM tm WHERE token={{base:String}}))
                  -toFloat64((SELECT anyOrNull(decimals) FROM tm WHERE token={{quote:String}}))) AS price,
       greatest(bp.base_observed_at,qp.quote_observed_at,comp.competition_observed_at) AS source_observed_at,
       blocks.auction_timestamp AS indexed_from, blocks.auction_timestamp AS indexed_to
FROM bp INNER JOIN qp USING auction_id
LEFT JOIN comp USING auction_id
LEFT JOIN blocks ON comp.auction_block=blocks.block_number
WHERE blocks.block_number!=0 AND {auction_time}
ORDER BY blocks.auction_timestamp"""
    native_reference = _native_reference_sql(chain, base, quote, range_state)
    return [
        pair_options_spec,
        QuerySpec("market_summary", "Market summary", market_summary, params, "block_timestamp", "checkpoint_bounded"),
        QuerySpec("price_candles", "Execution prices (settled fills)", candles, params, "block_timestamp", "checkpoint_bounded"),
        QuerySpec("recent_market_trades", "Recent settled fills", recent, params, "block_timestamp", "checkpoint_bounded", 60, exact_count=False),
        QuerySpec("auction_reference_prices", "Auction reference prices", auction_reference, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("native_reference_prices", "Native-price API observations", native_reference, params, "observed_at", "observed_series", 60),
        *_pair_depth_specs(chain, pair, depth_at),
        *_pair_depth_heatmap_specs(chain, pair, heatmap_window),
    ]


def _validate_depth_at(value: str) -> str:
    """Normalize a historical-book timestamp to a UTC ISO string.

    Only format and future bounds are enforced here — how far BACK
    reconstruction is honest is a data property surfaced by ``depth_horizon``
    (per-chain min(observed_at)) plus the empty-book state, never a
    hard-coded date.
    """
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("depth_at must be an ISO-8601 timestamp or 'live'") from exc
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    at = at.astimezone(timezone.utc)
    if at > datetime.now(timezone.utc):
        raise ValueError("depth_at cannot be in the future")
    if at.year < 2020:
        raise ValueError("depth_at predates CoW Protocol")
    return at.isoformat().replace("+00:00", "Z")


def _pair_depth_specs(
    chain: ChainInfo,
    pair: tuple[str, str],
    depth_at: str = "",
) -> list[QuerySpec]:
    """Order-book depth ladder for one pair — live or reconstructed at T.

    ONE per-order dataset serves the depth chart, the order list, and the
    count line (books are tiny — busiest observed pair ~91 open orders);
    cumulative sums, Flip, and range zoom are client-side re-projections.
    ``price`` is ALWAYS quote-per-base for BOTH sides: asks are orders
    selling the BASE token, bids are orders selling the QUOTE token.

    The historical shape reconstructs open-at-T from the captured orders
    subset: created<=T<valid_to, minus fills before T (trades, pruned by
    order_uid IN cand — order_uid IS in the orders sort key and the candidate
    set is tiny) and terminal events before T (order_events; API-observed
    cancel times carry minutes of jitter — disclosed). status:fulfilled is
    the fallback terminal for BNB fills whose trade rows lack timestamps.
    Live-verified 2026-07-23 (0.09s at T-1d on the busiest BNB pair; the cand
    CTE must NOT self-alias argMax to filtered column names — code 184).
    """
    horizon = """
SELECT min(observed_at) AS earliest_supported_at,
       max(observed_at) AS latest_observed_at,
       uniqExact(order_uid) AS captured_orders,
       min(creation_date) AS earliest_creation_seen,
       max(observed_at) AS source_observed_at
FROM cow_db.orders
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
ORDER BY earliest_supported_at"""
    # Pairs that HAVE a standing book right now (chain-scoped, pair-agnostic).
    # Some chains (Gnosis) run almost entirely on short-lived market orders and
    # hold ZERO open intents at any given moment — without this list the depth
    # panel dead-ends on an empty book with no path to data. Orders table is
    # tiny; argMax-dedup subquery, projected-column WHERE (no alias-in-WHERE
    # shadowing: the status/valid_to filters sit a level ABOVE the argMax).
    open_pairs = f"""
WITH {_token_metadata_cte()}
SELECT p.token0 AS token0,p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.open_orders AS open_orders,
       p.obs AS source_observed_at
FROM (
  SELECT least(sell_token,buy_token) AS token0,
         greatest(sell_token,buy_token) AS token1,
         count() AS open_orders,max(obs_at) AS obs
  FROM (
    SELECT order_uid,
           argMax(sell_token,observed_at) AS sell_token,
           argMax(buy_token,observed_at) AS buy_token,
           argMax(status,observed_at) AS status,
           argMax(valid_to,observed_at) AS valid_to,
           max(observed_at) AS obs_at
    FROM cow_db.orders
    WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    GROUP BY order_uid
  )
  WHERE status='open' AND valid_to>toUnixTimestamp(now())
  GROUP BY token0,token1
) AS p
LEFT JOIN tm AS m0 ON m0.token=p.token0
LEFT JOIN tm AS m1 ON m1.token=p.token1
ORDER BY p.open_orders DESC,p.token0,p.token1
LIMIT 30"""
    specs = [
        QuerySpec(
            "depth_horizon", "Depth reconstruction horizon", horizon,
            _scope_parameters(chain.environment, chain),
            "observed_at", "observed_series", 300,
        ),
        QuerySpec(
            "open_intent_pairs", "Pairs with open intents", open_pairs,
            _scope_parameters(chain.environment, chain),
            "observed_at", "observed_snapshot", 60,
        ),
    ]
    base, quote = pair
    if not base or not quote:
        return specs
    token_cte = _token_metadata_cte()
    ladder_projection = """
SELECT order_uid,owner,kind,side,order_class,partially_fillable,
       creation_date,valid_to,sell_token,buy_token,sell_symbol,buy_symbol,
       sell_decimals,buy_decimals,price,amount_base,amount_quote,
       sell_amount_raw,buy_amount_raw,
       creation_date AS indexed_from,creation_date AS indexed_to,
       source_observed_at
FROM priced
WHERE isFinite(price) AND price>0
ORDER BY side,price,order_uid"""
    if not depth_at:
        server_as_of = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        live_params = {
            **_scope_parameters(chain.environment, chain),
            "base": base,
            "quote": quote,
            "server_as_of": server_as_of.isoformat().replace("+00:00", "Z"),
        }
        live_sql = f"""
WITH {token_cte}, open_orders AS (
 SELECT o.*,
   if(o.executed_sell_amount<o.sell_amount,
      toUInt256(o.sell_amount-o.executed_sell_amount),toUInt256(0)) AS residual_sell_raw,
   if(o.executed_buy_amount<o.buy_amount,
      toUInt256(o.buy_amount-o.executed_buy_amount),toUInt256(0)) AS residual_buy_raw,
   if(o.kind='buy',
      toFloat64(o.sell_amount)*toFloat64(residual_buy_raw)
        /nullIf(toFloat64(o.buy_amount),0),
      toFloat64(residual_sell_raw)) AS remaining_sell_float,
   if(o.kind='buy',
      toFloat64(residual_buy_raw),
      toFloat64(o.buy_amount)*toFloat64(residual_sell_raw)
        /nullIf(toFloat64(o.sell_amount),0)) AS remaining_buy_float
 FROM cow_db.orders AS o FINAL
 WHERE o.environment={{env:String}} AND o.chain_id={{chain_id:UInt64}}
   AND o.status='open'
   AND o.valid_to>toUnixTimestamp(parseDateTime64BestEffort({{server_as_of:String}}))
   AND ((o.sell_token={{base:String}} AND o.buy_token={{quote:String}})
        OR (o.sell_token={{quote:String}} AND o.buy_token={{base:String}}))
), enriched AS (
 SELECT o.*,
   if(s.token='','',s.symbol) AS sell_symbol,
   if(b.token='','',b.symbol) AS buy_symbol,
   if(s.token='',NULL,s.decimals) AS sell_decimals,
   if(b.token='',NULL,b.decimals) AS buy_decimals,
   if(s.token='',NULL,remaining_sell_float/pow(10,toFloat64(s.decimals))) AS remaining_sell,
   if(b.token='',NULL,remaining_buy_float/pow(10,toFloat64(b.decimals))) AS remaining_buy,
   if(o.sell_token={{base:String}},'ask','bid') AS side
 FROM open_orders o
 LEFT JOIN tm s ON s.token=o.sell_token
 LEFT JOIN tm b ON b.token=o.buy_token
 WHERE remaining_sell_float>0 AND remaining_buy_float>0
), priced AS (
 SELECT *, class AS order_class,
   if(side='ask',remaining_buy/nullIf(remaining_sell,0),
                 remaining_sell/nullIf(remaining_buy,0)) AS price,
   if(side='ask',remaining_sell,remaining_buy) AS amount_base,
   if(side='ask',remaining_buy,remaining_sell) AS amount_quote,
   toString(sell_amount) AS sell_amount_raw,
   toString(buy_amount) AS buy_amount_raw,
   observed_at AS source_observed_at
 FROM enriched
 WHERE sell_decimals IS NOT NULL AND buy_decimals IS NOT NULL
)
{ladder_projection}"""
        specs.append(QuerySpec(
            "pair_depth", "Order-book depth (known open intents)", live_sql,
            live_params, "creation_date", "observed_snapshot", 60,
        ))
        return specs
    at_ts = _validate_depth_at(depth_at)
    hist_params = {
        **_scope_parameters(chain.environment, chain),
        "base": base,
        "quote": quote,
        "at_ts": at_ts,
    }
    hist_sql = f"""
WITH {token_cte}, cand AS (
  SELECT order_uid,
         argMax(owner,observed_at) AS owner_l,
         argMax(kind,observed_at) AS kind_l,
         argMax(class,observed_at) AS class_l,
         argMax(partially_fillable,observed_at) AS pf_l,
         argMax(sell_token,observed_at) AS st,
         argMax(buy_token,observed_at) AS bt,
         argMax(sell_amount,observed_at) AS sa,
         argMax(buy_amount,observed_at) AS ba,
         argMax(creation_date,observed_at) AS created,
         argMax(valid_to,observed_at) AS vt,
         max(observed_at) AS obs
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND ((sell_token={{base:String}} AND buy_token={{quote:String}})
         OR (sell_token={{quote:String}} AND buy_token={{base:String}}))
  GROUP BY order_uid
  HAVING created<=parseDateTime64BestEffort({{at_ts:String}})
     AND toDateTime(vt)>parseDateTime64BestEffort({{at_ts:String}})
), fills AS (
  SELECT order_uid,sum(fsa) AS filled_sell,sum(fba) AS filled_buy
  FROM (
    SELECT t.order_uid AS order_uid,t.tx_hash,t.log_index,
           argMax(t.sell_amount,t.observed_at) AS fsa,
           argMax(t.buy_amount,t.observed_at) AS fba
    FROM cow_db.trades AS t
    WHERE t.environment={{env:String}} AND t.chain_id={{chain_id:UInt64}}
      AND t.order_uid IN (SELECT order_uid FROM cand)
      AND t.block_timestamp IS NOT NULL
      AND t.block_timestamp<=parseDateTime64BestEffort({{at_ts:String}})
    GROUP BY t.order_uid,t.tx_hash,t.log_index
  ) GROUP BY order_uid
), term AS (
  SELECT order_uid,min(event_timestamp) AS terminated_at
  FROM cow_db.order_events
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND order_uid IN (SELECT order_uid FROM cand)
    AND event_type IN ('OrderInvalidated','OrderInvalidation','status:cancelled','status:fulfilled')
    AND event_timestamp IS NOT NULL
    AND event_timestamp<=parseDateTime64BestEffort({{at_ts:String}})
  GROUP BY order_uid
), book AS (
  -- Explicit column list: a qualified asterisk (c.*) through this joined CTE
  -- does NOT preserve plain column names on the server (code 47 downstream).
  SELECT c.order_uid AS order_uid,c.owner_l AS owner_l,c.kind_l AS kind_l,
    c.class_l AS class_l,c.pf_l AS pf_l,c.st AS st,c.bt AS bt,
    c.sa AS sa,c.ba AS ba,c.created AS created,c.vt AS vt,c.obs AS obs,
    if(f.filled_sell<c.sa,toUInt256(c.sa-f.filled_sell),toUInt256(0)) AS residual_sell_raw,
    if(f.filled_buy<c.ba,toUInt256(c.ba-f.filled_buy),toUInt256(0)) AS residual_buy_raw,
    if(c.kind_l='buy',
       toFloat64(c.sa)*toFloat64(residual_buy_raw)/nullIf(toFloat64(c.ba),0),
       toFloat64(residual_sell_raw)) AS remaining_sell_float,
    if(c.kind_l='buy',
       toFloat64(residual_buy_raw),
       toFloat64(c.ba)*toFloat64(residual_sell_raw)/nullIf(toFloat64(c.sa),0)) AS remaining_buy_float
  FROM cand AS c
  LEFT JOIN fills AS f ON f.order_uid=c.order_uid
  LEFT JOIN term AS x ON x.order_uid=c.order_uid
  WHERE x.order_uid=''
), enriched AS (
  SELECT bk.*,
    if(s.token='','',s.symbol) AS sell_symbol,
    if(b.token='','',b.symbol) AS buy_symbol,
    if(s.token='',NULL,s.decimals) AS sell_decimals,
    if(b.token='',NULL,b.decimals) AS buy_decimals,
    if(s.token='',NULL,remaining_sell_float/pow(10,toFloat64(s.decimals))) AS remaining_sell,
    if(b.token='',NULL,remaining_buy_float/pow(10,toFloat64(b.decimals))) AS remaining_buy,
    if(bk.st={{base:String}},'ask','bid') AS side
  FROM book bk
  LEFT JOIN tm s ON s.token=bk.st
  LEFT JOIN tm b ON b.token=bk.bt
  WHERE remaining_sell_float>0 AND remaining_buy_float>0
), priced AS (
  SELECT order_uid,owner_l AS owner,kind_l AS kind,side,class_l AS order_class,
    pf_l AS partially_fillable,created AS creation_date,vt AS valid_to,
    st AS sell_token,bt AS buy_token,sell_symbol,buy_symbol,
    sell_decimals,buy_decimals,
    if(side='ask',remaining_buy/nullIf(remaining_sell,0),
                  remaining_sell/nullIf(remaining_buy,0)) AS price,
    if(side='ask',remaining_sell,remaining_buy) AS amount_base,
    if(side='ask',remaining_buy,remaining_sell) AS amount_quote,
    toString(sa) AS sell_amount_raw,toString(ba) AS buy_amount_raw,
    obs AS source_observed_at
  FROM enriched
  WHERE sell_decimals IS NOT NULL AND buy_decimals IS NOT NULL
)
{ladder_projection}"""
    specs.append(QuerySpec(
        "pair_depth", "Order-book depth (reconstructed)", hist_sql,
        hist_params, "creation_date", "reconstructed_point_in_time", 3600,
    ))
    return specs


#: Time spans the depth heatmap can grid over. "all" spans the whole
#: order-capture horizon (min(observed_at)); 24h/7d are clamped to it.
_HEATMAP_WINDOWS = ("24h", "7d", "all")


def _validate_heatmap_window(value: str) -> str:
    """Normalize the depth-heatmap window to one of ``_HEATMAP_WINDOWS``."""
    window = (value or "").strip().lower()
    if window not in _HEATMAP_WINDOWS:
        raise ValueError(f"heatmap_window must be one of {list(_HEATMAP_WINDOWS)}")
    return window


def _pair_depth_heatmap_specs(
    chain: ChainInfo,
    pair: tuple[str, str],
    window: str = "7d",
) -> list[QuerySpec]:
    """Depth-over-time heatmap source for one pair.

    Reconstructs the *shape* of the book across a grid of ~60 timestamps in ONE
    query — the ``depth`` group's ``hist_sql`` returns a single instant, this
    returns many. Per (time bucket, order) it emits the order's resting price,
    side, and base-normalized size while the order is alive at that bucket:
    ``created <= t < valid_to`` and no terminal event (fill / cancel) has landed
    by ``t``. This is a deliberate lower-fidelity model than the 2-D ladder: an
    order rests at its FULL captured size until a terminal event removes it, so
    intra-window partial fills are not decremented gradually (disclosed via the
    ``depth_heatmap_reconstructed`` warning). Terminal events — the dominant
    effect, since a filled order leaves the book entirely — ARE honored. The
    client bins price into levels and sums ``depth_base`` per (bucket, level,
    side); ``amount_base`` is used for BOTH sides so one magnitude scale works.

    Row count is bounded by (<=60 buckets) x (open orders for the pair, tens),
    so the result stays small and memory-safe per the depth-panel budget.
    """
    base, quote = pair
    if not base or not quote:
        return []
    win = _validate_heatmap_window(window)
    token_cte = _token_metadata_cte()
    params = {
        **_scope_parameters(chain.environment, chain),
        "base": base,
        "quote": quote,
        "window": win,
    }
    # Grid derivation is split across CTEs so each expression references only a
    # PRIOR CTE alias (ClickHouse same-SELECT sibling-alias refs are fragile).
    heatmap_sql = f"""
WITH {token_cte},
bounds AS (
  SELECT now() AS t_now,
         ifNull(
           (SELECT min(observed_at) FROM cow_db.orders
              WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}),
           now() - INTERVAL 30 DAY) AS cap_start
),
win AS (
  SELECT t_now, cap_start,
         greatest(cap_start,
           multiIf({{window:String}}='24h', t_now - INTERVAL 24 HOUR,
                   {{window:String}}='7d',  t_now - INTERVAL 7 DAY,
                   cap_start)) AS w_start
  FROM bounds
),
grid AS (
  SELECT w_start,
         greatest(1, toUInt32(dateDiff('second', w_start, t_now))) AS span_s
  FROM win
),
grid_step AS (
  SELECT w_start, span_s, greatest(300, intDiv(span_s, 60)) AS step_s FROM grid
),
grid_n AS (
  SELECT w_start, step_s, least(72, toUInt32(intDiv(span_s, step_s)) + 1) AS n_buckets
  FROM grid_step
),
buckets AS (
  SELECT arrayJoin(arrayMap(i -> w_start + toUInt32(i) * step_s, range(n_buckets))) AS bucket_ts,
         step_s
  FROM grid_n
),
dims AS (
  SELECT (SELECT anyOrNull(decimals) FROM tm WHERE token={{base:String}}) AS base_dec,
         (SELECT anyOrNull(decimals) FROM tm WHERE token={{quote:String}}) AS quote_dec
),
cand AS (
  SELECT order_uid,
         argMax(sell_token,observed_at) AS st,
         argMax(sell_amount,observed_at) AS sa,
         argMax(buy_amount,observed_at) AS ba,
         argMax(creation_date,observed_at) AS created,
         argMax(valid_to,observed_at) AS vt
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND ((sell_token={{base:String}} AND buy_token={{quote:String}})
         OR (sell_token={{quote:String}} AND buy_token={{base:String}}))
  GROUP BY order_uid
),
term AS (
  SELECT order_uid, min(event_timestamp) AS terminated_at
  FROM cow_db.order_events
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND order_uid IN (SELECT order_uid FROM cand)
    AND event_type IN ('OrderInvalidated','OrderInvalidation','status:cancelled','status:fulfilled')
    AND event_timestamp IS NOT NULL
  GROUP BY order_uid
),
fill AS (
  -- Order_events does NOT reliably carry a terminal row for every filled order
  -- (most fills are trades, not status events), so without this an order that
  -- was filled but never got a status:fulfilled event would rest forever and
  -- the cross-join explodes. Use the LAST fill as the completion proxy: a
  -- fully-filled order leaves the book at its (only) fill; a partially-fillable
  -- order keeps resting until its final slice. Intra-fill size decay is not
  -- modeled (disclosed via depth_heatmap_reconstructed).
  SELECT order_uid, max(block_timestamp) AS filled_out_ts
  FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND order_uid IN (SELECT order_uid FROM cand)
    AND block_timestamp IS NOT NULL
  GROUP BY order_uid
),
priced AS (
  SELECT c.order_uid AS order_uid,
    if(c.st={{base:String}},'ask','bid') AS side,
    if(c.st={{base:String}},
       toFloat64(c.sa)/pow(10,toFloat64(d.base_dec)),
       toFloat64(c.ba)/pow(10,toFloat64(d.base_dec))) AS base_amt,
    if(c.st={{base:String}},
       toFloat64(c.ba)/pow(10,toFloat64(d.quote_dec)),
       toFloat64(c.sa)/pow(10,toFloat64(d.quote_dec))) AS quote_amt,
    c.created AS created,
    -- The book removes an order at the EARLIEST of: expiry, terminal event,
    -- and completing fill. Far-future sentinel keeps never-terminated resting
    -- orders alive across the window.
    least(toDateTime(c.vt),
          ifNull(t.terminated_at, toDateTime('2099-01-01 00:00:00')),
          ifNull(f.filled_out_ts, toDateTime('2099-01-01 00:00:00'))) AS alive_until
  FROM cand AS c
  CROSS JOIN dims AS d
  LEFT JOIN term AS t ON t.order_uid=c.order_uid
  LEFT JOIN fill AS f ON f.order_uid=c.order_uid
  WHERE d.base_dec IS NOT NULL AND d.quote_dec IS NOT NULL
)
SELECT
  formatDateTime(b.bucket_ts, '%Y-%m-%dT%H:%i:%SZ') AS bucket,
  p.quote_amt / nullIf(p.base_amt, 0) AS price,
  p.side AS side,
  p.base_amt AS depth_base,
  b.bucket_ts AS indexed_from,
  b.bucket_ts AS indexed_to
FROM buckets AS b
CROSS JOIN priced AS p
-- Interval overlap, not point-in-time: an order lights a bucket if its resting
-- span [created, alive_until) touches the bucket interval [bucket_ts, +step).
-- CoW books are transient (orders often rest minutes, buckets are ~an hour), so
-- a boundary snapshot would miss most orders and leave the heatmap near-empty.
WHERE p.created < (b.bucket_ts + b.step_s)
  AND p.alive_until > b.bucket_ts
  AND isFinite(price) AND price > 0 AND p.base_amt > 0
ORDER BY b.bucket_ts, p.side, price"""
    return [
        QuerySpec(
            "pair_depth_heatmap", "Order-book depth over time", heatmap_sql,
            params, "creation_date", "reconstructed_point_in_time", 3600,
            exact_count=False,
        ),
    ]


def _native_reference_sql(
    chain: ChainInfo,
    base: str,
    quote: str,
    range_state: dict[str, Any],
) -> str:
    token_cte = _token_metadata_cte()
    decimal_factor = """pow(10,toFloat64((SELECT anyOrNull(decimals) FROM tm WHERE token={base:String}))
                                  -toFloat64((SELECT anyOrNull(decimals) FROM tm WHERE token={quote:String})))"""
    if range_state["kind"] == "all":
        native_time = "1"
    elif range_state["kind"] == "absolute":
        native_time = (
            "observed_at>=parseDateTime64BestEffort({start_at:String}) AND "
            "observed_at<=parseDateTime64BestEffort({end_at:String})"
        )
    else:
        native_time = """observed_at>=(
          SELECT max(observed_at) FROM cow_db.native_prices FINAL
          WHERE environment={env:String} AND chain_id={chain_id:UInt64}
            AND token IN ({base:String},{quote:String})
        )-toIntervalDay({window_days:UInt32})"""
    if base == NATIVE_TOKEN:
        return f"""
WITH {token_cte}
SELECT observed_at AS bucket, 1/nullIf(toFloat64OrNull(native_price),0)*{decimal_factor} AS price,
       observed_at AS indexed_from, observed_at AS indexed_to, observed_at AS source_observed_at
FROM cow_db.native_prices FINAL
WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND token={{quote:String}}
  AND {native_time}
ORDER BY observed_at"""
    if quote == NATIVE_TOKEN:
        return f"""
WITH {token_cte}
SELECT observed_at AS bucket, toFloat64OrNull(native_price)*{decimal_factor} AS price,
       observed_at AS indexed_from, observed_at AS indexed_to, observed_at AS source_observed_at
FROM cow_db.native_prices FINAL
WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND token={{base:String}}
  AND {native_time}
ORDER BY observed_at"""
    return f"""
WITH {token_cte}, bp AS (
  SELECT toStartOfMinute(observed_at) AS bucket,
         argMax(toFloat64OrNull(native_price),observed_at) AS base_native_price,
         max(observed_at) AS base_observed_at
  FROM cow_db.native_prices FINAL
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND token={{base:String}}
    AND {native_time}
  GROUP BY bucket
), qp AS (
  SELECT toStartOfMinute(observed_at) AS bucket,
         argMax(toFloat64OrNull(native_price),observed_at) AS quote_native_price,
         max(observed_at) AS quote_observed_at
  FROM cow_db.native_prices FINAL
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND token={{quote:String}}
    AND {native_time}
  GROUP BY bucket
)
SELECT bp.bucket, bp.base_native_price/nullIf(qp.quote_native_price,0)*{decimal_factor} AS price,
       least(bp.base_observed_at,qp.quote_observed_at) AS indexed_from,
       greatest(bp.base_observed_at,qp.quote_observed_at) AS indexed_to,
       greatest(bp.base_observed_at,qp.quote_observed_at) AS source_observed_at
FROM bp INNER JOIN qp USING bucket
ORDER BY bucket"""


def _trade_specs(
    scope: str,
    chain: ChainInfo | None,
    range_state: dict[str, Any],
    filters: dict[str, str],
) -> list[QuerySpec]:
    """Trades section — single-chain, or all-networks via per-chain arms.

    Multi-chain scans stay per-chain-bounded UNION arms (see
    ``_per_chain_time``); token-symbol enrichment joins the multi-chain
    metadata CTE once around the assembled arms.
    """
    params = _scope_parameters(scope, None)
    chains = [chain] if chain is not None else _chains_for_scope(scope)
    ids = ",".join(str(c.chain_id) for c in chains)
    params.update(_time_params(range_state))
    shared_ctes = _shared_arm_ctes(ids)
    owner = _normalize_hex(filters.get("owner", ""))
    token = _normalize_hex(filters.get("token", ""))
    extra_predicates: list[str] = []
    if owner:
        if not ADDRESS_RE.fullmatch(owner):
            raise ValueError("owner filter must be an EVM address")
        params["owner"] = owner
        extra_predicates.append("t.owner={owner:String}")
    if token:
        token = _validate_token(token, "token filter")
        params["token"] = token
        extra_predicates.append("(t.sell_token={token:String} OR t.buy_token={token:String})")
    extra = "".join(f" AND {predicate}" for predicate in extra_predicates)
    tmx = _token_metadata_cte_multi(scope, chain)

    activity_parts: list[str] = []
    pair_parts: list[str] = []
    for c in chains:
        cid = c.chain_id
        arm_where = (
            f"t.environment={{env:String}} AND t.chain_id={cid} "
            f"AND {_arm_checkpoint(cid)} "
            f"AND t.block_timestamp IS NOT NULL AND {_arm_window(cid, range_state)}{extra}"
        )
        activity_parts.append(f"""
SELECT toStartOfDay(t.block_timestamp) AS bucket,{cid} AS chain_id,
       uniq({TRADE_KEY}) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       uniq(t.owner) AS owners,min(t.block_timestamp) AS indexed_from,
       max(t.block_timestamp) AS indexed_to,max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE {arm_where}
GROUP BY bucket""")
        pair_parts.append(f"""
SELECT {cid} AS chain_id,least(t.sell_token,t.buy_token) AS token0,
       greatest(t.sell_token,t.buy_token) AS token1,
       uniq({TRADE_KEY}) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE {arm_where}
GROUP BY token0,token1""")
    activity = (
        f"WITH {shared_ctes}\n"
        "SELECT * FROM (\n" + "\nUNION ALL\n".join(activity_parts)
        + "\n) ORDER BY bucket,chain_id"
    )
    pair_union = "\nUNION ALL\n".join(pair_parts)
    breakdown = f"""WITH {shared_ctes},{tmx}
SELECT p.chain_id AS chain_id, p.token0 AS token0, p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.fill_count AS fill_count, p.settlement_transactions AS settlement_transactions,
       p.indexed_from AS indexed_from, p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM ({pair_union}) AS p
LEFT JOIN tmx AS m0 ON m0.chain_id=p.chain_id AND m0.token=p.token0
LEFT JOIN tmx AS m1 ON m1.chain_id=p.chain_id AND m1.token=p.token1
ORDER BY p.fill_count DESC,p.chain_id,p.token0,p.token1
LIMIT 500"""
    # Top-N-first tape: ONE plain scan over the base table with a bounded
    # heap sort (PartialSorting) — constant memory even at all-history across
    # all networks (proven live: 6.3s where the previous shape OOMed at
    # 10.8 GiB; a per-arm UNION variant measured 7.9s and blew the SQL
    # length cap). No dedup, no join, no window function inside the heap.
    # The checkpoint filter runs on the SELECTED set (uncommitted-tail rows
    # are only the newest blocks; the 3x over-fetch absorbs them along with
    # the <0.1% ReplacingMergeTree duplicate rate), then argMax dedups and
    # the global top-ROW_CAP is taken.
    time_window = _arm_window(0, range_state)
    deduped_tape = f"""
SELECT u.chain_id AS chain_id,u.tx_hash AS tx_hash,u.log_index AS log_index,
       u.order_uid AS order_uid,
       argMax(u.block_timestamp,u.observed_at) AS block_timestamp,
       argMax(u.owner,u.observed_at) AS owner,
       argMax(u.sell_token,u.observed_at) AS sell_token,
       argMax(u.buy_token,u.observed_at) AS buy_token,
       argMax(u.sell_amount,u.observed_at) AS sell_amount,
       argMax(u.buy_amount,u.observed_at) AS buy_amount,
       argMax(u.fee_amount,u.observed_at) AS fee_amount,
       argMax(u.source,u.observed_at) AS source,
       max(u.observed_at) AS obs_at
FROM (
  SELECT chain_id,tx_hash,log_index,order_uid,block_timestamp,block_number,
         owner,sell_token,buy_token,sell_amount,buy_amount,fee_amount,
         source,observed_at
  FROM cow_db.trades AS t
  WHERE environment={{env:String}} AND chain_id IN ({ids})
    AND block_timestamp IS NOT NULL AND {time_window}{extra}
  ORDER BY block_timestamp DESC
  LIMIT {TAPE_ARM_LIMIT}
) AS u
INNER JOIN cp ON cp.chain_id=u.chain_id
WHERE u.block_number<=cp.b
GROUP BY u.chain_id,u.tx_hash,u.log_index,u.order_uid
ORDER BY block_timestamp DESC
LIMIT {ROW_CAP}"""
    trades = f"""WITH {shared_ctes},{tmx}
SELECT u.block_timestamp AS block_timestamp,u.chain_id AS chain_id,u.tx_hash AS tx_hash,
       u.log_index AS log_index,u.order_uid AS order_uid,u.owner AS owner,
       u.sell_token AS sell_token,if(s.symbol='',u.sell_token,s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.symbol='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       u.buy_token AS buy_token,if(b.symbol='',u.buy_token,b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.symbol='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(u.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(u.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       u.source AS source,u.obs_at AS source_observed_at
FROM ({deduped_tape}) AS u
LEFT JOIN tmx AS s ON s.chain_id=u.chain_id AND s.token=u.sell_token
LEFT JOIN tmx AS b ON b.chain_id=u.chain_id AND b.token=u.buy_token
ORDER BY u.block_timestamp DESC,u.log_index DESC,u.tx_hash DESC,u.order_uid DESC"""
    return [
        QuerySpec("trade_activity", "Settled fill activity", activity, params, "block_timestamp", "checkpoint_bounded"),
        QuerySpec("trade_pair_breakdown", "Settled fills by pair", breakdown, params, "block_timestamp", "checkpoint_bounded", 900),
        QuerySpec("trades", "Settled fills", trades, params, "block_timestamp", "checkpoint_bounded", 60, exact_count=False),
    ]


def _trader_dynamics_ctes(ids: str) -> str:
    """Shared CTE text for the trader growth-accounting datasets.

    ``om`` = (owner, month) activity over the trailing TRADER_DYNAMICS_MONTHS
    + 1 warm-up month, anchored on the scope-global latest trade (``ta`` from
    ``_shared_arm_ctes`` must precede this text). ``fsall`` = ALL-TIME
    first-seen per owner — a deliberate, documented departure from the
    90d-capped ``fs`` used by trader_activity: here the hash is built ONCE in
    a query that is the sole member of its load group (no arm concurrency),
    and true first-seen is what makes "new" honest. Sizing at
    TRADER_DYNAMICS_MONTHS (live 2026-07-22): om ~696K entries / fsall ~719K.
    """
    months = TRADER_DYNAMICS_MONTHS + 1
    return f"""
om AS (
  SELECT owner,toStartOfMonth(block_timestamp) AS period,max(observed_at) AS obs_at
  FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id IN ({ids})
    AND block_timestamp IS NOT NULL
    AND block_timestamp>=toStartOfMonth((SELECT max(a) FROM ta))-toIntervalMonth({months})
  GROUP BY owner,period
), fsall AS (
  SELECT owner,min(block_timestamp) AS first_seen
  FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id IN ({ids})
    AND block_timestamp IS NOT NULL
  GROUP BY owner
)"""


def _traders_specs(
    scope: str,
    chain: ChainInfo | None,
    range_state: dict[str, Any],
) -> list[QuerySpec]:
    """Traders section — per-owner stats, single-chain or cross-chain.

    Cross-chain mode assembles per-chain arms (memory-bounded view expansion)
    and re-aggregates per trader: fills/settlements sum exactly;
    ``distinct_pairs`` is the SUM of per-chain distinct pairs;
    ``chains_active`` counts the chains a trader appears on.
    """
    params = _scope_parameters(scope, None)
    chains = [chain] if chain is not None else _chains_for_scope(scope)
    ids = ",".join(str(c.chain_id) for c in chains)
    params.update(_time_params(range_state))
    shared_ctes = _shared_arm_ctes(ids)
    # Cap the first-seen build to the correlation window: unbounded, it is a
    # hash of EVERY trader ever (all-history) used as the INNER-JOIN build side
    # for each arm — an OOM driver. `ta` (latest-trade anchor) is emitted by
    # _shared_arm_ctes and precedes this CTE. "new_traders" then means "first
    # seen within the window" — the same disclosed tradeoff as the other caps.
    firsts_cte = f"""
fs AS (
  SELECT chain_id, owner, min(block_timestamp) AS first_seen
  FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id IN ({ids})
    AND block_timestamp IS NOT NULL
    AND block_timestamp >= (SELECT max(a) FROM ta) - toIntervalDay({CORRELATION_MAX_WINDOW_DAYS})
  GROUP BY chain_id, owner
)"""
    leader_parts: list[str] = []
    activity_parts: list[str] = []
    for c in chains:
        cid = c.chain_id
        arm_where = (
            f"t.environment={{env:String}} AND t.chain_id={cid} "
            f"AND {_arm_checkpoint(cid)} "
            f"AND t.block_timestamp IS NOT NULL AND {_arm_window(cid, range_state)}"
        )
        leader_parts.append(f"""
SELECT {cid} AS chain_id,t.owner AS trader,
       uniq({TRADE_KEY}) AS fill_count,
       uniq(t.tx_hash) AS settlement_transactions,
       uniq(tuple(least(t.sell_token,t.buy_token),greatest(t.sell_token,t.buy_token))) AS distinct_pairs,
       min(t.block_timestamp) AS fs_arm,
       max(t.block_timestamp) AS ls_arm,
       max(t.observed_at) AS obs_arm
FROM cow_db.trades AS t
WHERE {arm_where}
GROUP BY trader""")
        activity_parts.append(f"""
SELECT toStartOfDay(t.block_timestamp) AS bucket,{cid} AS chain_id,
       uniq(t.owner) AS active_traders,
       uniqIf(t.owner, toStartOfDay(f.first_seen)=toStartOfDay(t.block_timestamp)) AS new_traders,
       uniq({TRADE_KEY}) AS fill_count,
       min(t.block_timestamp) AS indexed_from,
       max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
INNER JOIN fs AS f ON f.chain_id={cid} AND f.owner=t.owner
WHERE {arm_where}
GROUP BY bucket""")
    leader_union = "\nUNION ALL\n".join(leader_parts)
    leaderboard = f"""
WITH {shared_ctes}
SELECT trader,
       sum(fill_count) AS fill_count,
       sum(settlement_transactions) AS settlement_transactions,
       count() AS chains_active,
       sum(distinct_pairs) AS distinct_pairs,
       min(fs_arm) AS first_seen,
       max(ls_arm) AS last_seen,
       min(fs_arm) AS indexed_from,
       max(ls_arm) AS indexed_to,
       max(obs_arm) AS source_observed_at
FROM (
{leader_union}
) GROUP BY trader
ORDER BY fill_count DESC, trader
LIMIT 200"""
    activity = (
        f"WITH {shared_ctes},{firsts_cte}\n"
        "SELECT * FROM (\n" + "\nUNION ALL\n".join(activity_parts)
        + "\n) ORDER BY bucket,chain_id"
    )
    # ---- Growth accounting + retention (fixed trailing window) ----
    # Deliberately IGNORES the global window (the _capped_analytical_range
    # pattern in the other direction): monthly growth accounting needs exactly
    # TRADER_DYNAMICS_MONTHS trailing periods, disclosed in the dataset docs.
    # Classification shape live-verified 2026-07-23 (3.2s, sane accounting:
    # new + returning + reactivated == active on every row).
    dynamics_ctes = _trader_dynamics_ctes(ids)
    dyn_params = _scope_parameters(scope, None)
    dynamics = f"""
WITH {shared_ctes},{dynamics_ctes},
monthly AS (
  SELECT o.period AS period,
         count() AS active_traders,
         countIf(toStartOfMonth(f.first_seen)=o.period) AS new_traders,
         countIf(p.owner!='') AS returning_traders,
         countIf(p.owner='' AND toStartOfMonth(f.first_seen)<o.period) AS reactivated_traders,
         max(o.obs_at) AS obs_at
  FROM om AS o
  INNER JOIN fsall AS f ON f.owner=o.owner
  LEFT JOIN (SELECT owner,period+toIntervalMonth(1) AS period FROM om) AS p
    ON p.owner=o.owner AND p.period=o.period
  GROUP BY period
)
SELECT period,active_traders,new_traders,returning_traders,reactivated_traders,
       prev_active-returning_traders AS churned_traders,
       (new_traders+reactivated_traders)/nullIf(prev_active-returning_traders,0) AS quick_ratio,
       returning_traders/nullIf(prev_active,0) AS retention_rate,
       period AS indexed_from,period AS indexed_to,
       obs_at AS source_observed_at
FROM (
  SELECT *,lagInFrame(active_traders,1) OVER (ORDER BY period ASC
           ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS prev_active
  FROM monthly
)
WHERE prev_active>0
ORDER BY period"""
    retention = f"""
WITH {shared_ctes},{dynamics_ctes},
coh AS (
  SELECT owner,toStartOfMonth(first_seen) AS cohort_month
  FROM fsall
  WHERE first_seen>=toStartOfMonth((SELECT max(a) FROM ta))-toIntervalMonth({TRADER_DYNAMICS_MONTHS})
), csize AS (
  SELECT cohort_month,uniqExact(owner) AS cohort_size FROM coh GROUP BY cohort_month
)
SELECT c.cohort_month AS cohort_month,
       dateDiff('month',c.cohort_month,om.period) AS month_index,
       any(cs.cohort_size) AS cohort_size,
       uniqExact(om.owner) AS active_traders,
       uniqExact(om.owner)/nullIf(any(cs.cohort_size),0) AS retention_share,
       c.cohort_month AS indexed_from,max(om.period) AS indexed_to,
       max(om.obs_at) AS source_observed_at
FROM om
INNER JOIN coh AS c ON c.owner=om.owner
INNER JOIN csize AS cs ON cs.cohort_month=c.cohort_month
WHERE om.period>=c.cohort_month
GROUP BY cohort_month,month_index
ORDER BY cohort_month,month_index"""
    return [
        QuerySpec("trader_leaderboard", "Trader leaderboard", leaderboard, dict(params), "block_timestamp", BASE_DEDUP_MODE, 900),
        QuerySpec("trader_activity", "Active and new traders", activity, dict(params), "block_timestamp", BASE_DEDUP_MODE, 900),
        QuerySpec("trader_dynamics", "Trader growth accounting (12 months)", dynamics, dict(dyn_params), "block_timestamp", BASE_DEDUP_MODE, 1800),
        QuerySpec("trader_retention", "Cohort retention (12 months)", retention, dict(dyn_params), "block_timestamp", BASE_DEDUP_MODE, 1800),
    ]


def _order_multi_core_specs(
    scope: str,
    chains: list[ChainInfo],
    range_state: dict[str, Any],
    filters: dict[str, str],
) -> list[QuerySpec]:
    """All-networks order lifecycle core (status summary + creation activity).

    Same output columns as the single-chain shapes so the frontend renders
    them unchanged; the argMax-dedup grouped subquery replaces FINAL in
    multi-chain mode (the ``og`` CTE precedent in the overview).
    """
    ids = ",".join(str(c.chain_id) for c in chains)
    params = {**_scope_parameters(scope, None), **_time_params(range_state)}
    extra: list[str] = []
    status = filters.get("status", "").strip()
    owner = _normalize_hex(filters.get("owner", ""))
    if status:
        params["status"] = status
        extra.append("status={status:String}")
    if owner:
        if not ADDRESS_RE.fullmatch(owner):
            raise ValueError("owner filter must be an EVM address")
        params["owner"] = owner
        extra.append("owner={owner:String}")
    oa_cte = f"""
oa AS (
  SELECT max(creation_date) AS a FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id IN ({ids})
)"""
    if range_state["kind"] == "all":
        window = "1"
    elif range_state["kind"] == "absolute":
        window = (
            "creation_date>=parseDateTime64BestEffort({start_at:String}) AND "
            "creation_date<=parseDateTime64BestEffort({end_at:String})"
        )
    else:
        window = "creation_date>=(SELECT a FROM oa)-toIntervalDay({window_days:UInt32})"
    # creation_date window pushed INTO the dedup (immutable → filtering raw ==
    # filtering deduped), bounding the argMax hash to the window rather than the
    # whole backfilled orders table. status/owner filters stay OUTER: they match
    # the LATEST (deduped) status/owner, so they cannot move inside the argMax.
    outer = " AND ".join(extra) if extra else "1"
    o_dedup = f"""
  SELECT chain_id,order_uid,argMax(status,observed_at) AS status,
         argMax(owner,observed_at) AS owner,
         argMax(creation_date,observed_at) AS creation_date,
         max(observed_at) AS obs_at
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id IN ({ids}) AND {window}
  GROUP BY chain_id,order_uid"""
    summary = f"""WITH {oa_cte}
SELECT status,count() AS order_count,uniqExact(owner) AS owners,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM ({o_dedup})
WHERE {outer}
GROUP BY status
ORDER BY order_count DESC,status"""
    activity = f"""WITH {oa_cte}
SELECT toStartOfDay(creation_date) AS bucket,count() AS order_count,
       countIf(status='open') AS currently_open,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM ({o_dedup})
WHERE {outer}
GROUP BY bucket
ORDER BY bucket"""
    return [
        QuerySpec("order_status_summary", "Observed order lifecycle", summary, dict(params), "creation_date", "observed_snapshot", 60),
        QuerySpec("order_activity", "Order creation activity", activity, dict(params), "creation_date", "observed_snapshot", 60),
    ]


def _order_type_specs(
    scope: str,
    chains: list[ChainInfo],
    range_state: dict[str, Any],
) -> list[QuerySpec]:
    """Order-type analytics (dual-mode: single-chain or all-networks).

    Everything here runs on the SMALL orders (~100K rows) and order_events
    (~1M rows) tables — argMax-dedup grouped scans with tiny hashes. Coverage
    caveat baked into the docs: the orderbook sync is a PARTIAL subset (~78K
    orders, limit-heavy and recent), so class mixes describe the observed
    subset, never all CoW orders.
    """
    ids = ",".join(str(c.chain_id) for c in chains)
    params = {**_scope_parameters(scope, None), **_time_params(range_state)}
    oa_cte = f"""
oa AS (
  SELECT max(creation_date) AS a FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id IN ({ids})
)"""

    def order_window(column: str = "creation_date") -> str:
        if range_state["kind"] == "all":
            return "1"
        if range_state["kind"] == "absolute":
            return (
                f"{column}>=parseDateTime64BestEffort({{start_at:String}}) AND "
                f"{column}<=parseDateTime64BestEffort({{end_at:String}})"
            )
        return f"{column}>=(SELECT a FROM oa)-toIntervalDay({{window_days:UInt32}})"

    # The creation_date window is pushed INTO the dedup (creation_date is
    # immutable per order_uid, so filtering the raw rows is equivalent to
    # filtering deduped rows). This keeps the argMax GROUP BY hash bounded by
    # the WINDOW, not the whole (now multi-million-row, backfilled) orders
    # table: the default 30d view dedups ~300k rows / 0.7s instead of ~4M / 12s.
    # "All history" is unbounded by design and remains the heaviest selection.
    o_dedup = f"""
  SELECT chain_id,order_uid,argMax(class,observed_at) AS order_class,
         argMax(kind,observed_at) AS order_kind,
         argMax(signing_scheme,observed_at) AS signing_scheme,
         argMax(partially_fillable,observed_at) AS partially_fillable,
         argMax(status,observed_at) AS status,
         argMax(owner,observed_at) AS owner,
         argMax(creation_date,observed_at) AS creation_date,
         max(observed_at) AS obs_at
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id IN ({ids}) AND {order_window()}
  GROUP BY chain_id,order_uid"""
    type_summary = f"""WITH {oa_cte}
SELECT chain_id,order_class,count() AS order_count,uniqExact(owner) AS owners,
       countIf(status='fulfilled') AS fulfilled,countIf(status='expired') AS expired,
       countIf(status='cancelled') AS cancelled,countIf(status='open') AS open_now,
       countIf(status='fulfilled')/nullIf(count(),0) AS fulfilled_share,
       countIf(partially_fillable) AS partially_fillable_count,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM ({o_dedup})
GROUP BY chain_id,order_class
ORDER BY chain_id,order_count DESC"""
    flavor_mix = f"""WITH {oa_cte}
SELECT chain_id,order_kind,signing_scheme,partially_fillable,
       count() AS order_count,uniqExact(owner) AS owners,
       countIf(status='fulfilled')/nullIf(count(),0) AS fulfilled_share,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM ({o_dedup})
GROUP BY chain_id,order_kind,signing_scheme,partially_fillable
ORDER BY chain_id,order_count DESC"""
    type_trend = f"""WITH {oa_cte}
SELECT toStartOfDay(creation_date) AS bucket,chain_id,order_class,
       count() AS order_count,countIf(status='fulfilled') AS fulfilled_count,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM ({o_dedup})
GROUP BY bucket,chain_id,order_class
ORDER BY bucket,chain_id,order_class"""
    # ComposableCoW / programmatic footprint. event_timestamp is Nullable —
    # bucket on coalesce(event_timestamp, observed_at), disclosed in docs.
    oea_cte = f"""
oea AS (
  SELECT max(coalesce(event_timestamp,observed_at)) AS a FROM cow_db.order_events
  WHERE environment={{env:String}} AND chain_id IN ({ids})
)"""
    event_ts = "coalesce(event_timestamp,observed_at)"
    if range_state["kind"] == "all":
        event_window = "1"
    elif range_state["kind"] == "absolute":
        event_window = (
            f"{event_ts}>=parseDateTime64BestEffort({{start_at:String}}) AND "
            f"{event_ts}<=parseDateTime64BestEffort({{end_at:String}})"
        )
    else:
        event_window = (
            f"{event_ts}>=(SELECT a FROM oea)-toIntervalDay({{window_days:UInt32}})"
        )
    conditional_activity = f"""WITH {oea_cte}
SELECT toStartOfDay({event_ts}) AS bucket,chain_id,event_type,
       uniq(event_id) AS events,uniq(owner) AS creators,
       min({event_ts}) AS indexed_from,max({event_ts}) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM cow_db.order_events
WHERE environment={{env:String}} AND chain_id IN ({ids})
  AND event_type IN ('ConditionalOrderCreated','MerkleRootSet','OrderInvalidation','SwapGuardSet')
  AND {event_window}
GROUP BY bucket,chain_id,event_type
ORDER BY bucket,chain_id,event_type"""
    # App-data orderClass tags (the ONLY honest TWAP signal: TWAP children
    # land as class='limit', so orders.class never says twap). The doubly-
    # nested JSON path is live-verified (probe 2026-07-23). Buckets:
    # 'unresolved' = order's app_data_hash is not in the stored app_data set
    # (~55% of order rows — the coverage disclosure), 'untagged' = doc exists
    # but carries no orderClass. Snapshot over the whole observed subset.
    # This is an all-time snapshot (no window to push in), so instead of
    # deduping the whole backfilled orders table to order grain we GROUP the raw
    # scan by (chain_id, app_data_hash) — app_data_hash is IMMUTABLE per order,
    # so uniq(order_uid) per hash counts its distinct orders, and the GROUP BY is
    # bounded by the ~1.2k distinct app-data hashes (NOT the millions of orders).
    # owners stay exact via uniqExact state-merge across the hashes of a class.
    appdata_classes = f"""
WITH ad AS (
  SELECT app_data_hash,
         JSONExtractString(JSONExtractString(argMax(full_app_data,observed_at),'fullAppData'),
                           'metadata','orderClass','orderClass') AS order_class
  FROM cow_db.app_data
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY app_data_hash
),
od AS (
  SELECT chain_id,app_data_hash,uniq(order_uid) AS orders,
         uniqExactState(owner) AS owners_state,max(observed_at) AS obs_at
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id,app_data_hash
)
SELECT od.chain_id AS chain_id,
       multiIf(od.app_data_hash='','unresolved',
               ad.app_data_hash='','unresolved',
               ad.order_class='','untagged',ad.order_class) AS order_class,
       sum(od.orders) AS orders,uniqExactMerge(od.owners_state) AS owners,
       uniqExactIf(od.app_data_hash,ad.app_data_hash!='') AS appdata_hashes,
       max(od.obs_at) AS source_observed_at
FROM od
LEFT JOIN ad ON ad.app_data_hash=od.app_data_hash
GROUP BY od.chain_id,order_class
ORDER BY od.chain_id,orders DESC"""
    snapshot_params = _scope_parameters(scope, None)
    return [
        QuerySpec("order_type_summary", "Orders by class (observed subset)", type_summary, dict(params), "creation_date", "observed_snapshot", 300),
        QuerySpec("order_flavor_mix", "Order flavor mix (kind x scheme)", flavor_mix, dict(params), "creation_date", "observed_snapshot", 300),
        QuerySpec("order_type_trend", "Order classes over time", type_trend, dict(params), "creation_date", "observed_snapshot", 900),
        QuerySpec("conditional_order_activity", "Programmatic order activity (ComposableCoW)", conditional_activity, dict(params), "observed_at", "observed_series", 900),
        QuerySpec("appdata_order_classes", "App-data order classes (TWAP tags)", appdata_classes, dict(snapshot_params), "observed_at", "observed_snapshot", 900),
    ]


def _order_specs(
    scope: str,
    chain: ChainInfo | None,
    pair: tuple[str, str],
    range_state: dict[str, Any],
    filters: dict[str, str],
) -> list[QuerySpec]:
    chains = [chain] if chain is not None else _chains_for_scope(scope)
    type_specs = _order_type_specs(scope, chains, range_state)
    if chain is None:
        return [*_order_multi_core_specs(scope, chains, range_state, filters), *type_specs]
    base, quote = pair
    params = _scope_parameters(chain.environment, chain)
    anchor = (
        "SELECT max(creation_date) FROM cow_db.orders FINAL "
        "WHERE environment={env:String} AND chain_id={chain_id:UInt64}"
    )
    time_pred, time_params = _time_predicate("o.creation_date", range_state, anchor)
    params.update(time_params)
    predicates = [_scope_predicate(chain, "o"), time_pred]
    status = filters.get("status", "").strip()
    owner = _normalize_hex(filters.get("owner", ""))
    if status:
        params["status"] = status
        predicates.append("o.status={status:String}")
    if owner:
        if not ADDRESS_RE.fullmatch(owner):
            raise ValueError("owner filter must be an EVM address")
        params["owner"] = owner
        predicates.append("o.owner={owner:String}")
    where = " AND ".join(predicates)
    summary = f"""
SELECT o.status,count() AS order_count,uniqExact(o.owner) AS owners,
       min(o.creation_date) AS indexed_from,max(o.creation_date) AS indexed_to,
       max(o.observed_at) AS source_observed_at
FROM cow_db.orders AS o FINAL
WHERE {where}
GROUP BY o.status
ORDER BY order_count DESC,o.status"""
    activity = f"""
SELECT toStartOfDay(o.creation_date) AS bucket,count() AS order_count,
       countIf(o.status='open') AS currently_open,
       min(o.creation_date) AS indexed_from,max(o.creation_date) AS indexed_to,
       max(o.observed_at) AS source_observed_at
FROM cow_db.orders AS o FINAL
WHERE {where}
GROUP BY bucket
ORDER BY bucket"""
    surplus = SURPLUS_BPS.format(
        eb="t.buy_amount", ls="o.sell_amount", es="t.sell_amount", lb="o.buy_amount",
    )
    # Streaming shape: base trades (checkpoint-bounded, dedup-free — <0.1%
    # duplicate fills, disclosed) hash-joined against the SMALL deduped orders
    # set. The previous trades_canonical FINAL x orders FINAL double-merge was
    # the memory-heavy part; the argMax subquery streams on the sort key.
    quality_join = """
FROM cow_db.trades AS t
INNER JOIN (
  SELECT order_uid,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         argMax(creation_date,observed_at) AS creation_date
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY order_uid
) AS o ON o.order_uid=t.order_uid
WHERE t.environment={env:String} AND t.chain_id={chain_id:UInt64}
  AND t.block_number<=(
    SELECT argMax(block_number,updated_at) FROM cow_db.indexing_checkpoints
    WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND source='rpc')
  AND t.block_timestamp IS NOT NULL"""
    # Cap the window: the three quality distributions load CONCURRENTLY and
    # each streams the full trades partition through per-day quantile state at
    # kind='all' — three unbounded aggregations at once is a concurrent-OOM
    # contributor. Same 90d analytical cap the correlation views use.
    quality_range, _ = _capped_analytical_range(range_state)
    quality_time, quality_params = _time_predicate(
        "t.block_timestamp", quality_range, _trade_anchor(chain)
    )
    quality_source = f"""
SELECT toStartOfDay(t.block_timestamp) AS bucket,
       {surplus} AS surplus_bps,
       if(o.creation_date<=t.block_timestamp,
          dateDiff('second', o.creation_date, t.block_timestamp), NULL) AS latency_seconds,
       t.block_timestamp, t.observed_at
{quality_join} AND {quality_time}"""
    quality_summary = f"""
SELECT bucket, count() AS fills,
       avg(surplus_bps) AS avg_surplus_bps,
       quantile(0.5)(surplus_bps) AS median_surplus_bps,
       avgIf(latency_seconds, latency_seconds IS NOT NULL) AS avg_latency_seconds,
       quantileIf(0.5)(latency_seconds, latency_seconds IS NOT NULL) AS median_latency_seconds,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM ({quality_source})
GROUP BY bucket
ORDER BY bucket"""
    latency_distribution = f"""
SELECT multiIf(latency_seconds IS NULL,'unknown',
               latency_seconds<10,'<10s',
               latency_seconds<60,'10-60s',
               latency_seconds<300,'1-5m',
               latency_seconds<3600,'5-60m','>1h') AS latency_bucket,
       count() AS fills,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM ({quality_source})
GROUP BY latency_bucket
ORDER BY latency_bucket"""
    surplus_distribution = f"""
SELECT multiIf(surplus_bps IS NULL,'unknown',
               surplus_bps< -50,'< -50 bps',
               surplus_bps<0,'-50-0 bps',
               surplus_bps<10,'0-10 bps',
               surplus_bps<50,'10-50 bps',
               surplus_bps<200,'50-200 bps','> 200 bps') AS surplus_bucket,
       count() AS fills,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM ({quality_source})
GROUP BY surplus_bucket
ORDER BY surplus_bucket"""
    quality_full_params = {**params, **quality_params}
    # Surplus by order class: the quality_join shape with class carried
    # through the deduped orders build. Same 90d analytical cap; sole member
    # of its own load group so it never stacks with the quality trio.
    class_source = f"""
SELECT o.klass AS klass,
       {surplus} AS surplus_bps,
       t.block_timestamp, t.observed_at
FROM cow_db.trades AS t
INNER JOIN (
  SELECT order_uid,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         argMax(class,observed_at) AS klass
  FROM cow_db.orders
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
  GROUP BY order_uid
) AS o ON o.order_uid=t.order_uid
WHERE t.environment={{env:String}} AND t.chain_id={{chain_id:UInt64}}
  AND t.block_number<=(
    SELECT argMax(block_number,updated_at) FROM cow_db.indexing_checkpoints
    WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND source='rpc')
  AND t.block_timestamp IS NOT NULL AND {quality_time}"""
    surplus_by_class = f"""
SELECT klass AS order_class,
       multiIf(surplus_bps IS NULL,'unknown',
               surplus_bps< -50,'< -50 bps',
               surplus_bps<0,'-50-0 bps',
               surplus_bps<10,'0-10 bps',
               surplus_bps<50,'10-50 bps',
               surplus_bps<200,'50-200 bps','> 200 bps') AS surplus_bucket,
       count() AS fills,
       avgOrNull(surplus_bps) AS avg_surplus_bps,
       quantile(0.5)(surplus_bps) AS median_surplus_bps,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM ({class_source})
GROUP BY order_class, surplus_bucket
ORDER BY order_class, surplus_bucket"""
    specs = [
        QuerySpec("order_status_summary", "Observed order lifecycle", summary, dict(params), "creation_date", "observed_snapshot", 60),
        QuerySpec("order_activity", "Order creation activity", activity, dict(params), "creation_date", "observed_snapshot", 60),
        QuerySpec("order_quality_summary", "Execution quality (surplus vs limit)", quality_summary, dict(quality_full_params), "block_timestamp", "checkpoint_bounded", 900),
        QuerySpec("fill_latency_distribution", "Creation-to-fill latency", latency_distribution, dict(quality_full_params), "block_timestamp", "checkpoint_bounded", 900),
        QuerySpec("surplus_distribution", "Surplus distribution (bps vs limit)", surplus_distribution, dict(quality_full_params), "block_timestamp", "checkpoint_bounded", 900),
        QuerySpec("surplus_by_class", "Surplus by order class", surplus_by_class, dict(quality_full_params), "block_timestamp", "checkpoint_bounded", 900),
        *type_specs,
    ]
    if not base or not quote:
        return specs
    server_as_of = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    intent_params = {
        **_scope_parameters(chain.environment, chain),
        "base": base,
        "quote": quote,
        "server_as_of": server_as_of.isoformat().replace("+00:00", "Z"),
    }
    owner_predicate = ""
    if owner:
        intent_params["owner"] = owner
        owner_predicate = "AND o.owner={owner:String}"
    token_cte = _token_metadata_cte()
    remaining_cte = f"""
{token_cte}, open_orders AS (
 SELECT o.*,
   if(o.executed_sell_amount<o.sell_amount,
      toUInt256(o.sell_amount-o.executed_sell_amount),toUInt256(0)) AS residual_sell_raw,
   if(o.executed_buy_amount<o.buy_amount,
      toUInt256(o.buy_amount-o.executed_buy_amount),toUInt256(0)) AS residual_buy_raw,
   if(o.kind='buy',
      toFloat64(o.sell_amount)*toFloat64(residual_buy_raw)
        /nullIf(toFloat64(o.buy_amount),0),
      toFloat64(residual_sell_raw)) AS remaining_sell_float,
   if(o.kind='buy',
      toFloat64(residual_buy_raw),
      toFloat64(o.buy_amount)*toFloat64(residual_sell_raw)
        /nullIf(toFloat64(o.sell_amount),0)) AS remaining_buy_float
 FROM cow_db.orders AS o FINAL
 WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
   AND o.status='open' AND o.valid_to>toUnixTimestamp(parseDateTime64BestEffort({{server_as_of:String}}))
   AND o.status!='presignaturePending'
   {owner_predicate}
   AND ((o.sell_token={{base:String}} AND o.buy_token={{quote:String}})
        OR (o.sell_token={{quote:String}} AND o.buy_token={{base:String}}))
), enriched AS (
 SELECT o.*,
   if(s.token='','',s.symbol) AS sell_symbol,
   if(b.token='','',b.symbol) AS buy_symbol,
   if(s.token='',NULL,s.decimals) AS sell_decimals,
   if(b.token='',NULL,b.decimals) AS buy_decimals,
   if(s.token='',NULL,toFloat64(o.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount_normalized,
   if(b.token='',NULL,toFloat64(o.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount_normalized,
   if(s.token='',NULL,remaining_sell_float/pow(10,toFloat64(s.decimals))) AS remaining_sell,
   if(b.token='',NULL,remaining_buy_float/pow(10,toFloat64(b.decimals))) AS remaining_buy,
   if(o.sell_token={{base:String}},'ask','bid') AS side,
   if(s.token='' OR b.token='',NULL,if(o.sell_token={{base:String}},
      (remaining_buy_float/pow(10,toFloat64(b.decimals)))
        /nullIf(remaining_sell_float/pow(10,toFloat64(s.decimals)),0),
      (remaining_sell_float/pow(10,toFloat64(s.decimals)))
        /nullIf(remaining_buy_float/pow(10,toFloat64(b.decimals)),0))) AS limit_price,
   if(s.token='' OR b.token='',NULL,if(o.sell_token={{base:String}},
      remaining_sell_float/pow(10,toFloat64(s.decimals)),
      remaining_buy_float/pow(10,toFloat64(b.decimals)))) AS base_remaining
 FROM open_orders o
 LEFT JOIN tm s ON s.token=o.sell_token
 LEFT JOIN tm b ON b.token=o.buy_token
 WHERE remaining_sell_float>0 AND remaining_buy_float>0
), normalized AS (
 SELECT * FROM enriched
 WHERE sell_decimals IS NOT NULL AND buy_decimals IS NOT NULL
)"""
    known_orders = f"""
WITH {remaining_cte}
SELECT order_uid,owner,kind,side,status,creation_date,valid_to,sell_token,buy_token,
       sell_symbol,buy_symbol,
       toString(sell_amount) AS sell_amount_raw,toString(buy_amount) AS buy_amount_raw,
       sell_decimals,buy_decimals,sell_amount_normalized,buy_amount_normalized,
       toString(executed_sell_amount) AS executed_sell_amount_raw,
       toString(executed_buy_amount) AS executed_buy_amount_raw,
       toString(residual_sell_raw) AS residual_sell_amount_raw,
       toString(residual_buy_raw) AS residual_buy_amount_raw,
       remaining_sell,remaining_buy,limit_price,observed_at AS source_observed_at
FROM enriched
ORDER BY creation_date DESC,order_uid DESC"""
    intent_summary = f"""
WITH {remaining_cte}
SELECT side,count() AS intent_count,sum(base_remaining) AS base_remaining,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM normalized
GROUP BY side
ORDER BY side"""
    depth = f"""
WITH {remaining_cte}
SELECT side,limit_price,sum(base_remaining) AS base_quantity,count() AS intent_count,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM normalized
WHERE isFinite(limit_price) AND limit_price>0
GROUP BY side,limit_price
ORDER BY side,if(side='bid',-limit_price,limit_price)"""
    specs.extend([
        QuerySpec("known_orders", "Known open intents (observed snapshot)", known_orders, dict(intent_params), "creation_date", "observed_snapshot", 60),
        QuerySpec("known_intents", "Known intent summary", intent_summary, dict(intent_params), "creation_date", "observed_snapshot", 60),
        QuerySpec("intent_depth", "Known intents", depth, dict(intent_params), "creation_date", "observed_snapshot", 60),
    ])
    return specs


def _auction_specs(
    chain: ChainInfo | None,
    range_state: dict[str, Any],
    scope: str = "production",
) -> list[QuerySpec]:
    """Auctions section — single-chain or all-networks (competition tables are
    small, so grouped multi-chain scans are cheap; auction_id is only unique
    PER chain, so every join carries chain_id)."""
    if chain is not None:
        scope = chain.environment
    params = _scope_parameters(scope, chain)
    scope_pred_c = _scope_predicate(chain, "c", scope)
    scope_pred_bare = _scope_predicate(chain, "", scope)
    # Bounded chain_blocks: `block_number IN (auction blocks)` prunes the whole
    # ~9.2M-row table (block_number is the sort key) down to the ~15k auction
    # blocks, and argMax dedups so NO FINAL. Replaces the unbounded
    # `chain_blocks FINAL` LEFT-JOIN build side that OOMed at 639 MiB.
    blk_cte = f"""blk AS (
  SELECT chain_id, block_number, argMax(block_timestamp, observed_at) AS block_timestamp
  FROM cow_db.chain_blocks
  WHERE {scope_pred_bare}
    AND block_number IN (SELECT auction_block FROM cow_db.solver_competitions FINAL
                         WHERE {scope_pred_bare})
  GROUP BY chain_id, block_number
)"""
    anchor = f"""SELECT max(block_timestamp) FROM cow_db.chain_blocks
WHERE {scope_pred_bare}
  AND block_number IN (SELECT auction_block FROM cow_db.solver_competitions FINAL WHERE {scope_pred_bare})"""
    time_pred, time_params = _time_predicate("b.block_timestamp", range_state, anchor)
    params.update(time_params)
    common = f"""FROM cow_db.solver_competitions AS c FINAL
LEFT JOIN blk AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
WHERE {scope_pred_c} AND b.block_number!=0 AND {time_pred}"""
    activity = f"""
WITH {blk_cte}
SELECT toStartOfDay(b.block_timestamp) AS bucket,c.chain_id AS chain_id,
       count() AS competition_count,
       uniqExact(c.winner) AS winners,min(b.block_timestamp) AS indexed_from,
       max(b.block_timestamp) AS indexed_to,max(c.observed_at) AS source_observed_at
{common}
GROUP BY bucket,chain_id
ORDER BY bucket,chain_id"""
    auctions = f"""
WITH {blk_cte},
sols AS (
 SELECT chain_id,auction_id,count() AS solution_count,countIf(is_winner) AS winner_rows,
        min(ranking) AS best_ranking
 FROM cow_db.competition_solutions FINAL
 WHERE {scope_pred_bare}
 GROUP BY chain_id,auction_id
), txs AS (
 SELECT chain_id,auction_id,count() AS transaction_count,groupUniqArray(tx_hash) AS tx_hashes
 FROM cow_db.competition_transactions FINAL
 WHERE {scope_pred_bare}
 GROUP BY chain_id,auction_id
)
SELECT c.chain_id AS chain_id,c.auction_id,b.block_timestamp AS auction_timestamp,c.auction_block,
       c.winner AS competition_winner,c.reference_score,
       coalesce(sols.solution_count,0) AS solution_count,
       coalesce(txs.transaction_count,0) AS transaction_count,txs.tx_hashes,
       b.block_timestamp AS indexed_from,b.block_timestamp AS indexed_to,
       c.observed_at AS source_observed_at
FROM cow_db.solver_competitions AS c FINAL
LEFT JOIN blk AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
LEFT JOIN sols ON c.chain_id=sols.chain_id AND c.auction_id=sols.auction_id
LEFT JOIN txs ON c.chain_id=txs.chain_id AND c.auction_id=txs.auction_id
WHERE {scope_pred_c} AND b.block_number!=0 AND {time_pred}
ORDER BY b.block_timestamp DESC,c.auction_id DESC"""
    return [
        QuerySpec("auction_activity", "Indexed settled competitions", activity, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("auctions", "Indexed settled competitions", auctions, params, "auction_block_timestamp", "observed_series"),
    ]


def _solver_specs(
    scope: str,
    chain: ChainInfo | None,
    range_state: dict[str, Any],
    pair: tuple[str, str] = ("", ""),
    filters: dict[str, str] | None = None,
) -> list[QuerySpec]:
    """Solvers section — single-chain, or an all-networks rollup.

    All-networks mode adds ``chains_active``/``executed_settlements`` to the
    stats table and a ``solver_cross_chain`` (solver x chain) dataset the
    frontend pivots into the comparison matrix; the pair->executor flow
    remains single-chain only.
    """
    params = _scope_parameters(scope, chain)
    chains = [chain] if chain is not None else _chains_for_scope(scope)
    ids = ",".join(str(c.chain_id) for c in chains)
    filters = filters or {}
    competition_filter = ""
    flow_filters: list[str] = []
    solver = _normalize_hex(filters.get("solver", ""))
    if solver:
        if not ADDRESS_RE.fullmatch(solver):
            raise ValueError("solver filter must be an EVM address")
        params["solver"] = solver
        competition_filter = " AND s.solver={solver:String}"
        flow_filters.append("exec.settlement_executor={solver:String}")
    base, quote = pair
    if base and quote:
        params.update({"base": base, "quote": quote})
        flow_filters.append(
            "((t.sell_token={base:String} AND t.buy_token={quote:String}) OR "
            "(t.sell_token={quote:String} AND t.buy_token={base:String}))"
        )
    scope_s = f"s.environment={{env:String}} AND s.chain_id IN ({ids})"
    # Prefiltered block-time lookup: joining the full chain_blocks table (with
    # FINAL) builds a hash table of millions of rows per chain; restricting to
    # blocks that actually appear as auction blocks keeps the join tiny in
    # both single-chain and all-networks mode.
    blocks_cte = f"""
blk AS (
  SELECT chain_id, block_number,
         argMax(block_timestamp, observed_at) AS block_timestamp
  FROM cow_db.chain_blocks
  WHERE environment={{env:String}} AND chain_id IN ({ids})
    AND block_number IN (
      SELECT auction_block FROM cow_db.solver_competitions FINAL
      WHERE environment={{env:String}} AND chain_id IN ({ids})
    )
  GROUP BY chain_id, block_number
)"""
    anchor = f"""SELECT max(block_timestamp)
FROM cow_db.chain_blocks
WHERE environment={{env:String}} AND chain_id IN ({ids})
  AND block_number IN (
    SELECT max(auction_block) FROM cow_db.solver_competitions FINAL
    WHERE environment={{env:String}} AND chain_id IN ({ids})
    GROUP BY chain_id
  )"""
    time_pred, time_params = _time_predicate("b.block_timestamp", range_state, anchor)
    params.update(time_params)
    common_joins = """FROM cow_db.competition_solutions AS s FINAL
INNER JOIN cow_db.solver_competitions AS c FINAL
  ON s.environment=c.environment AND s.chain_id=c.chain_id AND s.auction_id=c.auction_id
LEFT JOIN blk AS b
  ON b.chain_id=c.chain_id AND b.block_number=c.auction_block"""
    common_where = f"WHERE {scope_s} AND b.block_number!=0 AND {time_pred}{competition_filter}"
    common = f"{common_joins}\n{common_where}"
    if range_state["kind"] == "relative":
        settlement_time = (
            "block_timestamp IS NOT NULL AND block_timestamp >= ("
            "SELECT max(block_timestamp) FROM cow_db.settlements "
            f"WHERE environment={{env:String}} AND chain_id IN ({ids})"
            ") - toIntervalDay({window_days:UInt32})"
        )
    elif range_state["kind"] == "absolute":
        settlement_time = (
            "block_timestamp>=parseDateTime64BestEffort({start_at:String}) "
            "AND block_timestamp<=parseDateTime64BestEffort({end_at:String})"
        )
    else:
        settlement_time = "block_timestamp IS NOT NULL"
    stats = f"""
WITH {blocks_cte},
exec AS (
  SELECT solver, uniq(tx_hash) AS executed_settlements
  FROM cow_db.settlements
  WHERE environment={{env:String}} AND chain_id IN ({ids}) AND {settlement_time}
  GROUP BY solver
)
SELECT s.solver AS competition_solver,count() AS solutions,
       uniqExact(s.auction_id) AS competitions,countIf(s.is_winner) AS wins,
       countIf(s.is_winner)/nullIf(uniqExact(s.auction_id),0) AS win_rate,
       uniqExact(s.chain_id) AS chains_active,
       any(exec.executed_settlements) AS executed_settlements,
       avg(toFloat64(s.ranking)) AS average_ranking,min(s.ranking) AS best_ranking,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
{common_joins}
LEFT JOIN exec ON exec.solver=s.solver
{common_where}
GROUP BY competition_solver
ORDER BY wins DESC,competitions DESC,competition_solver
LIMIT 200"""
    activity = f"""
WITH {blocks_cte}
SELECT toStartOfDay(b.block_timestamp) AS bucket,s.solver AS competition_solver,
       uniqExact(s.auction_id) AS competitions,countIf(s.is_winner) AS wins,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
{common}
GROUP BY bucket,competition_solver
ORDER BY bucket,competition_solver"""
    ranking = f"""
WITH {blocks_cte}
SELECT s.ranking,count() AS solution_count,countIf(s.is_winner) AS winners,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
{common}
GROUP BY s.ranking
ORDER BY s.ranking"""
    # ---- Solver directory (all-time presence, dual-mode) ----
    # ONE full settlements streaming scan into a ~558-entry (chain, solver)
    # hash (live-verified 1.6s) + the small competition tables. All-time by
    # design: presence/first-seen/last-seen need no window (disclosed). The
    # per-chain anchor ships in every row so the frontend computes activity
    # tiers against the CHAIN's own freshness — a stale indexer (BNB) must
    # not mark its solvers inactive. keys via UNION DISTINCT (not FULL OUTER
    # JOIN — ClickHouse empty-key default quirks).
    directory = f"""
WITH st AS (
  SELECT chain_id,solver,
         minOrNull(block_timestamp) AS first_settlement_at,
         maxOrNull(block_timestamp) AS last_settlement_at,
         uniq(tx_hash) AS settlements_all_time,
         max(observed_at) AS obs_st
  FROM cow_db.settlements
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id,solver
), cs AS (
  SELECT chain_id,solver,uniqExact(auction_id) AS competitions_all,
         countIf(is_winner) AS wins_all,
         max(observed_at) AS obs_cs
  FROM cow_db.competition_solutions FINAL
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id,solver
), sa AS (
  SELECT chain_id,maxOrNull(block_timestamp) AS chain_anchor_at
  FROM cow_db.settlements
  WHERE environment={{env:String}} AND chain_id IN ({ids})
  GROUP BY chain_id
), dkeys AS (
  SELECT chain_id,solver FROM st
  UNION DISTINCT
  SELECT chain_id,solver FROM cs
)
SELECT k.chain_id AS chain_id,k.solver AS solver,
       st.first_settlement_at AS first_settlement_at,
       st.last_settlement_at AS last_settlement_at,
       coalesce(st.settlements_all_time,0) AS settlements_all_time,
       coalesce(cs.competitions_all,0) AS competitions_all,
       coalesce(cs.wins_all,0) AS wins_all,
       sa.chain_anchor_at AS chain_anchor_at,
       st.first_settlement_at AS indexed_from,
       st.last_settlement_at AS indexed_to,
       greatest(coalesce(st.obs_st,toDateTime64(0,3,'UTC')),
                coalesce(cs.obs_cs,toDateTime64(0,3,'UTC'))) AS source_observed_at
FROM dkeys AS k
LEFT JOIN st ON st.chain_id=k.chain_id AND st.solver=k.solver
LEFT JOIN cs ON cs.chain_id=k.chain_id AND cs.solver=k.solver
LEFT JOIN sa ON sa.chain_id=k.chain_id
ORDER BY settlements_all_time DESC,k.chain_id,k.solver
LIMIT 3000"""
    # ---- Winner score gap vs reference (dual-mode) ----
    # reference_score is ALWAYS a JSON map keyed by solver address; scores are
    # opaque big-int strings — parse defensively, surface failures as a count
    # instead of dropping rows silently.
    gap_expr = (
        "toFloat64OrNull(s.score)"
        "-toFloat64OrNull(JSONExtractString(c.reference_score,s.solver))"
    )
    score_gaps = f"""
WITH {blocks_cte}
SELECT s.chain_id AS chain_id,s.solver AS competition_solver,
       count() AS wins_scored,
       countIf(({gap_expr}) IS NULL) AS parse_failures,
       avgOrNull({gap_expr}) AS avg_score_gap,
       quantile(0.5)({gap_expr}) AS median_score_gap,
       quantile(0.9)({gap_expr}) AS p90_score_gap,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
{common_joins}
{common_where} AND s.is_winner
GROUP BY chain_id,competition_solver
ORDER BY wins_scored DESC,chain_id,competition_solver
LIMIT 2000"""
    dir_params = _scope_parameters(scope, None)
    specs = [
        QuerySpec("solver_stats", "Competition solver statistics", stats, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("solver_activity", "Competition solver activity", activity, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("ranking_distribution", "Solution ranking distribution", ranking, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("solver_directory", "Solver directory (observed presence)", directory, dict(dir_params), "block_timestamp", BASE_DEDUP_MODE, 1800),
        QuerySpec("solver_score_gaps", "Winner score gap vs reference", score_gaps, dict(params), "auction_block_timestamp", "observed_series", 900),
    ]
    if chain is None:
        cross_chain = f"""
WITH {blocks_cte}
SELECT s.solver AS competition_solver, s.chain_id AS chain_id,
       count() AS solutions, uniqExact(s.auction_id) AS competitions,
       countIf(s.is_winner) AS wins,
       countIf(s.is_winner)/nullIf(uniqExact(s.auction_id),0) AS win_rate,
       min(s.ranking) AS best_ranking,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
{common}
GROUP BY competition_solver, chain_id
ORDER BY competition_solver, chain_id
LIMIT 2000"""
        specs.append(QuerySpec(
            "solver_cross_chain", "Solver cross-chain comparison", cross_chain,
            params, "auction_block_timestamp", "observed_series", 900,
        ))
        return specs
    # Same trades ⋈ settlements join as the patterns matrices — cap the
    # window so the executor hash-join build stays small at any selection.
    flow_range, _ = _capped_analytical_range(range_state)
    flow_time, flow_params = _time_predicate("t.block_timestamp", flow_range, _trade_anchor(chain))
    flow_params_all = {**params, **flow_params}
    flow_settle_time = _settlement_time_bound(flow_range)
    flow = f"""
WITH {_token_metadata_cte()},
exec AS (
 SELECT tx_hash,argMax(solver,tuple(block_timestamp,log_index)) AS settlement_executor
 FROM cow_db.settlements
 WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
   AND block_timestamp IS NOT NULL AND {flow_settle_time}
 GROUP BY tx_hash
),
flows AS (
  SELECT least(t.sell_token,t.buy_token) AS token0,
         greatest(t.sell_token,t.buy_token) AS token1,
         exec.settlement_executor,count() AS fill_count,
         min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN exec ON exec.tx_hash=t.tx_hash
  WHERE {_scope_predicate(chain, 't')} AND t.block_timestamp IS NOT NULL AND {flow_time}
    {''.join(f' AND {predicate}' for predicate in flow_filters)}
  GROUP BY token0,token1,settlement_executor
)
SELECT f.token0 AS token0,f.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       f.settlement_executor AS settlement_executor,f.fill_count AS fill_count,
       f.indexed_from AS indexed_from,f.indexed_to AS indexed_to,
       f.source_observed_at AS source_observed_at
FROM flows AS f
LEFT JOIN tm AS m0 ON m0.token=f.token0
LEFT JOIN tm AS m1 ON m1.token=f.token1
ORDER BY f.fill_count DESC,f.token0,f.token1,f.settlement_executor"""
    specs.append(QuerySpec(
        "execution_flow", "Pair to settlement executor flow", flow,
        flow_params_all, "block_timestamp", "checkpoint_bounded", 900,
    ))
    return specs


def _patterns_specs(
    scope: str,
    chain: ChainInfo,
    range_state: dict[str, Any],
) -> list[QuerySpec]:
    """Patterns section — correlations the official explorer does not show.

    Single-chain by design: solver-pair specialization, trader-solver
    affinity, and fee-policy impact on execution quality. The cross-chain
    solver comparison lives in the Solvers all-networks rollup.
    """
    params = _scope_parameters(scope, chain)
    # Correlation matrices join two big tables — cap the analytical window so
    # the hash-join build stays small at any global window selection (incl.
    # "all history"). Disclosed in each dataset's (i) note.
    corr_range, _ = _capped_analytical_range(range_state)
    time_pred, time_params = _time_predicate(
        "t.block_timestamp", corr_range, _trade_anchor(chain)
    )
    params.update(time_params)
    # Bound the settlement-executor hash-join build to the query window — an
    # unbounded per-chain aggregation (~2.6M rows) was the patterns OOM source.
    settle_time = _settlement_time_bound(corr_range)
    exec_cte = f"""
exec AS (
  SELECT tx_hash, argMax(solver,tuple(block_timestamp,log_index)) AS settlement_executor
  FROM cow_db.settlements
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    AND block_timestamp IS NOT NULL AND {settle_time}
  GROUP BY tx_hash
)"""
    trade_where = (
        "t.environment={env:String} AND t.chain_id={chain_id:UInt64} "
        f"AND t.block_timestamp IS NOT NULL AND {time_pred}"
    )
    pair_matrix = f"""
WITH {_token_metadata_cte()},{exec_cte},
pf AS (
  SELECT least(t.sell_token,t.buy_token) AS token0,
         greatest(t.sell_token,t.buy_token) AS token1,
         exec.settlement_executor AS settlement_executor,
         count() AS fill_count,
         min(t.block_timestamp) AS indexed_from, max(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN exec ON exec.tx_hash=t.tx_hash
  WHERE {trade_where}
  GROUP BY token0, token1, settlement_executor
),
tp AS (
  SELECT token0, token1 FROM pf GROUP BY token0, token1
  ORDER BY sum(fill_count) DESC LIMIT 30
)
SELECT p.token0 AS token0, p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.settlement_executor AS settlement_executor,
       p.fill_count AS fill_count,
       p.fill_count/sum(p.fill_count) OVER (PARTITION BY p.token0,p.token1) AS pair_share,
       p.indexed_from AS indexed_from, p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM pf AS p
INNER JOIN tp USING (token0, token1)
LEFT JOIN tm AS m0 ON m0.token=p.token0
LEFT JOIN tm AS m1 ON m1.token=p.token1
ORDER BY p.token0, p.token1, p.fill_count DESC
LIMIT 1000"""
    affinity = f"""
WITH {exec_cte},
tt AS (
  SELECT t.owner AS trader, exec.settlement_executor AS settlement_executor,
         count() AS fill_count,
         min(t.block_timestamp) AS indexed_from, max(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN exec ON exec.tx_hash=t.tx_hash
  WHERE {trade_where}
  GROUP BY trader, settlement_executor
),
topt AS (
  SELECT trader FROM tt GROUP BY trader
  ORDER BY sum(fill_count) DESC LIMIT 100
),
tot AS (SELECT sum(fill_count) AS all_fills FROM tt),
sol AS (
  SELECT settlement_executor, sum(fill_count) AS solver_fills
  FROM tt GROUP BY settlement_executor
)
SELECT tt.trader AS trader, tt.settlement_executor AS settlement_executor,
       tt.fill_count AS fill_count,
       tt.fill_count/sum(tt.fill_count) OVER (PARTITION BY tt.trader) AS trader_share,
       sol.solver_fills/(SELECT all_fills FROM tot) AS solver_global_share,
       tt.indexed_from AS indexed_from, tt.indexed_to AS indexed_to,
       tt.source_observed_at AS source_observed_at
FROM tt
INNER JOIN topt USING (trader)
INNER JOIN sol USING (settlement_executor)
ORDER BY tt.trader, tt.fill_count DESC
LIMIT 2000"""
    fill_surplus = SURPLUS_BPS.format(
        eb="f.buy_amount", ls="o.sell_amount", es="f.sell_amount", lb="o.buy_amount",
    )
    fee_quality = f"""
WITH pol AS (
  SELECT order_uid, tx_hash, log_index, any(policy) AS policy
  FROM cow_db.protocol_fees FINAL
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
  GROUP BY order_uid, tx_hash, log_index
)
SELECT multiIf(positionCaseInsensitive(q.policy,'priceImprovement')>0,'price_improvement',
               positionCaseInsensitive(q.policy,'surplus')>0,'surplus',
               positionCaseInsensitive(q.policy,'volume')>0,'volume','other') AS policy_family,
       count() AS fills, uniqExact(q.order_uid) AS orders,
       avg(q.surplus_bps) AS avg_surplus_bps,
       quantile(0.5)(q.surplus_bps) AS median_surplus_bps,
       quantile(0.9)(q.surplus_bps) AS p90_surplus_bps,
       min(q.block_timestamp) AS indexed_from, max(q.block_timestamp) AS indexed_to,
       max(q.observed_at) AS source_observed_at
FROM (
  SELECT f.order_uid AS order_uid, pol.policy AS policy,
         {fill_surplus} AS surplus_bps,
         f.block_timestamp AS block_timestamp, f.observed_at AS observed_at
  FROM cow_db.trades AS f
  INNER JOIN pol ON pol.order_uid=f.order_uid AND pol.tx_hash=f.tx_hash
   AND pol.log_index=f.log_index
  INNER JOIN (
    SELECT order_uid,
           argMax(sell_amount,observed_at) AS sell_amount,
           argMax(buy_amount,observed_at) AS buy_amount
    FROM cow_db.orders
    WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
    GROUP BY order_uid
  ) AS o ON o.order_uid=f.order_uid
  WHERE f.environment={{env:String}} AND f.chain_id={{chain_id:UInt64}}
    AND f.block_timestamp IS NOT NULL
    AND {time_pred.replace('t.block_timestamp', 'f.block_timestamp')}
) AS q
GROUP BY policy_family
ORDER BY fills DESC
LIMIT 20"""
    # Quote-vs-execution delta: priceImprovement fee policies EMBED a
    # reference quote — the ONLY quote source in cow_db (the quotes table is
    # empty). policy is a Python-repr string, NOT JSON (single quotes; fixed
    # keys and numeric values only), so a quote swap makes it JSON-parseable.
    # Rows without an embedded quote land in the 'unquoted' bucket instead of
    # being dropped. This is quote-vs-execution delta, not user slippage.
    policy_json = "replaceAll(q.policy,'\\'','\"')"
    quote_sell = (
        f"toFloat64OrNull(JSON_VALUE({policy_json},"
        "'$.priceImprovement.quote.sellAmount'))"
    )
    quote_buy = (
        f"toFloat64OrNull(JSON_VALUE({policy_json},"
        "'$.priceImprovement.quote.buyAmount'))"
    )
    quote_delta_expr = SURPLUS_BPS.format(
        eb="q.buy_amount", ls=quote_sell, es="q.sell_amount", lb=quote_buy,
    )
    quote_delta = f"""
WITH pol AS (
  SELECT order_uid, tx_hash, log_index, any(policy) AS policy
  FROM cow_db.protocol_fees FINAL
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
  GROUP BY order_uid, tx_hash, log_index
)
SELECT policy_family,
       multiIf(delta_bps IS NULL,'unquoted',
               delta_bps< -50,'< -50 bps',
               delta_bps<0,'-50-0 bps',
               delta_bps<10,'0-10 bps',
               delta_bps<50,'10-50 bps',
               delta_bps<200,'50-200 bps','> 200 bps') AS delta_bucket,
       count() AS fills, uniqExact(order_uid) AS orders,
       avgOrNull(delta_bps) AS avg_delta_bps,
       quantile(0.5)(delta_bps) AS median_delta_bps,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM (
  SELECT q.order_uid AS order_uid,
         multiIf(positionCaseInsensitive(q.policy,'priceImprovement')>0,'price_improvement',
                 positionCaseInsensitive(q.policy,'surplus')>0,'surplus',
                 positionCaseInsensitive(q.policy,'volume')>0,'volume','other') AS policy_family,
         {quote_delta_expr} AS delta_bps,
         q.block_timestamp AS block_timestamp, q.observed_at AS observed_at
  FROM (
    SELECT f.order_uid AS order_uid, pol.policy AS policy,
           f.buy_amount AS buy_amount, f.sell_amount AS sell_amount,
           f.block_timestamp AS block_timestamp, f.observed_at AS observed_at
    FROM cow_db.trades AS f
    INNER JOIN pol ON pol.order_uid=f.order_uid AND pol.tx_hash=f.tx_hash
     AND pol.log_index=f.log_index
    WHERE f.environment={{env:String}} AND f.chain_id={{chain_id:UInt64}}
      AND f.block_timestamp IS NOT NULL
      AND {time_pred.replace('t.block_timestamp', 'f.block_timestamp')}
  ) AS q
)
GROUP BY policy_family, delta_bucket
ORDER BY policy_family, delta_bucket"""
    return [
        QuerySpec("solver_pair_matrix", "Solver-pair specialization", pair_matrix, dict(params), "block_timestamp", "checkpoint_bounded", 900),
        QuerySpec("trader_solver_affinity", "Trader-solver affinity", affinity, dict(params), "block_timestamp", "checkpoint_bounded", 900),
        QuerySpec("fee_policy_quality", "Fee-policy impact on execution quality", fee_quality, dict(params), "block_timestamp", "checkpoint_bounded", 900),
        QuerySpec("quote_delta_quality", "Execution vs embedded quote (priceImprovement)", quote_delta, dict(params), "block_timestamp", "checkpoint_bounded", 900),
    ]


def _live_specs(scope: str, chain: ChainInfo | None) -> list[QuerySpec]:
    """Live section: tight, short-TTL queries the frontend polls.

    ``live_pulse`` reads checkpoints for EVERY chain in scope (cheap; powers
    the per-chain lag/catch-up bars). Feed datasets are chain-optional:
    ``chain=None`` merges every in-scope chain into one newest-first tape —
    still hard-bounded to the last hour + small LIMITs, which keeps the argMax
    dedup hash to an hour of rows across ten chains (thousands, not millions;
    live-verified 0.2s). The base tables are not time-sorted, so wide live
    scans remain unacceptable in EITHER mode.
    """
    params = _scope_parameters(scope, chain)
    ids = ",".join(str(c.chain_id) for c in _chains_for_scope(scope))
    feed_pred = _scope_predicate(chain, "", scope)
    tmx_cte = _token_metadata_cte_multi(scope, chain)
    pulse = f"""
WITH cp AS (
  SELECT chain_id, argMax(block_number, updated_at) AS checkpoint_block,
         max(updated_at) AS checkpoint_updated_at
  FROM cow_db.indexing_checkpoints
  WHERE environment={{env:String}} AND chain_id IN ({ids}) AND source='rpc'
  GROUP BY chain_id
), blocks AS (
  -- block_number IN prunes chain_blocks (sort key) from ~9.2M to ~10 rows;
  -- this runs every 10s, so the full-table join was constant instance load.
  SELECT b.chain_id, argMax(b.block_timestamp, b.observed_at) AS checkpoint_timestamp
  FROM cow_db.chain_blocks AS b
  INNER JOIN cp ON b.chain_id=cp.chain_id AND b.block_number=cp.checkpoint_block
  WHERE b.environment={{env:String}} AND b.chain_id IN ({ids})
    AND b.block_number IN (SELECT checkpoint_block FROM cp)
  GROUP BY b.chain_id
)
SELECT n.chain_id AS chain_id, cp.checkpoint_block,
       nullIf(blocks.checkpoint_timestamp,toDateTime(0)) AS checkpoint_timestamp,
       cp.checkpoint_updated_at,
       if(blocks.checkpoint_timestamp IS NULL OR blocks.checkpoint_timestamp=toDateTime(0),
          NULL,
          dateDiff('second', blocks.checkpoint_timestamp, now())) AS lag_seconds
FROM (SELECT arrayJoin([{ids}]) AS chain_id) AS n
LEFT JOIN cp ON n.chain_id=cp.chain_id
LEFT JOIN blocks ON n.chain_id=blocks.chain_id
ORDER BY n.chain_id"""
    # Live feeds MUST dedup indexer versions: the last hour is exactly where
    # ReplacingMergeTree parts are still unmerged (and API+RPC dual-source rows
    # coexist), so a raw base-table read shows every fresh fill twice. The
    # 1-hour bound keeps the argMax GROUP BY tiny.
    trades = f"""
WITH {tmx_cte}
SELECT u.block_ts AS block_timestamp,u.chain_id AS chain_id,
       u.tx_hash,u.log_index,u.order_uid,u.owner,
       u.sell_token,if(s.token='','',s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       u.buy_token,if(b.token='','',b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       u.obs_at AS source_observed_at
FROM (
  SELECT chain_id,tx_hash,log_index,order_uid,
         argMax(block_timestamp,observed_at) AS block_ts,
         argMax(owner,observed_at) AS owner,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         max(observed_at) AS obs_at
  FROM cow_db.trades
  WHERE {feed_pred}
    AND block_timestamp >= {LIVE_WINDOW_SQL}
  GROUP BY chain_id,tx_hash,log_index,order_uid
  ORDER BY block_ts DESC,log_index DESC
  LIMIT 50
) AS u
LEFT JOIN tmx AS s ON s.chain_id=u.chain_id AND s.token=u.sell_token
LEFT JOIN tmx AS b ON b.chain_id=u.chain_id AND b.token=u.buy_token
ORDER BY u.block_ts DESC,u.log_index DESC
LIMIT 50"""
    settlements = f"""
WITH fills AS (
  SELECT chain_id,tx_hash, uniq(tuple(log_index,order_uid)) AS fill_count
  FROM cow_db.trades
  WHERE {feed_pred}
    AND block_timestamp >= {LIVE_WINDOW_SQL}
  GROUP BY chain_id,tx_hash
)
SELECT u.block_ts AS block_timestamp,u.chain_id AS chain_id,
       u.tx_hash,u.block_num AS block_number,
       u.settlement_executor,
       coalesce(fills.fill_count,0) AS fill_count,
       u.obs_at AS source_observed_at
FROM (
  -- block_num, NOT block_number: aliasing an aggregate to a column name that
  -- also appears in a same-level WHERE makes ClickHouse bind the WHERE
  -- identifier to the aggregate → ILLEGAL_AGGREGATION (code 184). A distinct
  -- alias keeps this safe even if a block_number predicate is added later.
  SELECT chain_id,tx_hash,log_index,
         argMax(block_timestamp,observed_at) AS block_ts,
         argMax(block_number,observed_at) AS block_num,
         argMax(solver,observed_at) AS settlement_executor,
         max(observed_at) AS obs_at
  FROM cow_db.settlements
  WHERE {feed_pred}
    AND block_timestamp >= {LIVE_WINDOW_SQL}
  GROUP BY chain_id,tx_hash,log_index
  ORDER BY block_ts DESC,log_index DESC
  LIMIT 30
) AS u
LEFT JOIN fills ON fills.chain_id=u.chain_id AND fills.tx_hash=u.tx_hash
ORDER BY u.block_ts DESC,u.log_index DESC
LIMIT 30"""
    open_orders = f"""
WITH {tmx_cte}
SELECT o.order_uid,o.chain_id AS chain_id,o.owner,o.kind,o.status,o.creation_date,o.valid_to,
       o.partially_fillable,
       o.sell_token,if(s.token='','',s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(o.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(o.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       o.buy_token,if(b.token='','',b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(o.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(o.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       if(o.sell_amount>0,
          least(1,toFloat64(o.executed_sell_amount)/toFloat64(o.sell_amount)),0) AS fill_ratio,
       o.observed_at AS source_observed_at
FROM cow_db.orders AS o FINAL
LEFT JOIN tmx AS s ON s.chain_id=o.chain_id AND s.token=o.sell_token
LEFT JOIN tmx AS b ON b.chain_id=o.chain_id AND b.token=o.buy_token
WHERE {_scope_predicate(chain, 'o', scope)}
  AND o.status='open' AND o.valid_to>toUnixTimestamp(now())
ORDER BY o.creation_date DESC,o.order_uid DESC
LIMIT 100"""
    events = f"""
SELECT event_type,chain_id,order_uid,owner,block_number,transaction_hash,event_timestamp,
       observed_at AS source_observed_at
FROM cow_db.order_events FINAL
WHERE {feed_pred}
  AND observed_at >= now() - INTERVAL 1 HOUR
ORDER BY observed_at DESC,event_id DESC
LIMIT 50"""
    # Minute-bucketed heartbeat for the live band chart: 1h bound keeps the
    # (minute x chain) hash at <= 60 x 10 entries regardless of load.
    minute_activity = f"""
SELECT toStartOfMinute(block_timestamp) AS bucket,chain_id,
       uniq({TRADE_KEY}) AS fills,uniq(tx_hash) AS settlements,
       min(block_timestamp) AS indexed_from,max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM cow_db.trades
WHERE {feed_pred}
  AND block_timestamp >= {LIVE_WINDOW_SQL}
GROUP BY bucket,chain_id
ORDER BY bucket,chain_id"""
    return [
        QuerySpec("live_pulse", "Indexing pulse", pulse, {"env": scope}, "observed_at", "observed_series", 10),
        QuerySpec("live_trades", "Latest settled fills", trades, dict(params), "block_timestamp", "checkpoint_bounded", 15),
        QuerySpec("live_settlements", "Latest settlements", settlements, dict(params), "block_timestamp", "checkpoint_bounded", 15),
        QuerySpec("live_minute_activity", "Last-hour heartbeat", minute_activity, dict(params), "block_timestamp", BASE_DEDUP_MODE, 15),
        QuerySpec("live_open_orders", "Waiting to execute (observed open intents)", open_orders, dict(params), "creation_date", "observed_snapshot", 30),
        QuerySpec("live_order_events", "Order lifecycle stream", events, dict(params), "observed_at", "observed_series", 30),
    ]


def _entity_specs(
    entity_type: str,
    identifier: str,
    chain: ChainInfo,
) -> list[QuerySpec]:
    params = _scope_parameters(chain.environment, chain)
    if entity_type == "order":
        uid = _normalize_hex(identifier)
        if not ORDER_UID_RE.fullmatch(uid):
            raise ValueError("Order UID must contain 112 hexadecimal characters")
        params["id"] = uid
        return _order_entity_specs(params)
    if entity_type == "transaction":
        tx = _normalize_hex(identifier)
        if not HASH_RE.fullmatch(tx):
            raise ValueError("Transaction hash must contain 64 hexadecimal characters")
        params["id"] = tx
        return _transaction_entity_specs(params)
    if entity_type in {"address", "token", "solver"}:
        address = _validate_token(identifier, entity_type)
        params["id"] = address
        if entity_type == "address":
            return _address_entity_specs(params)
        if entity_type == "token":
            return _token_entity_specs(params)
        return _solver_entity_specs(params)
    if entity_type == "auction":
        if not INTEGER_RE.fullmatch(identifier.strip()):
            raise ValueError("Auction identifier must be an integer")
        params["id"] = int(identifier)
        return _auction_entity_specs(params)
    raise ValueError(f"Unsupported entity_type: {entity_type}")


def _order_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    detail = f"""
WITH {_token_metadata_cte()}
SELECT o.order_uid,o.owner,o.sell_token,o.buy_token,o.receiver,
       if(s.token='','',s.symbol) AS sell_symbol,
       if(b.token='','',b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(o.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(o.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(o.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(o.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(o.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(o.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       toString(o.executed_sell_amount) AS executed_sell_amount_raw,
       if(s.token='',NULL,toFloat64(o.executed_sell_amount)/pow(10,toFloat64(s.decimals))) AS executed_sell_amount,
       toString(o.executed_buy_amount) AS executed_buy_amount_raw,
       if(b.token='',NULL,toFloat64(o.executed_buy_amount)/pow(10,toFloat64(b.decimals))) AS executed_buy_amount,
       toString(o.executed_fee_amount) AS executed_fee_amount_raw,
       if(s.token='',NULL,toFloat64(o.executed_fee_amount)/pow(10,toFloat64(s.decimals))) AS executed_fee_amount,
       o.valid_to,o.kind,o.partially_fillable,o.signing_scheme,o.creation_date,o.status,o.class,
       o.app_data_hash,o.source,o.source_updated_at,o.observed_at AS source_observed_at
FROM cow_db.orders AS o FINAL
LEFT JOIN tm AS s ON s.token=o.sell_token
LEFT JOIN tm AS b ON b.token=o.buy_token
WHERE o.environment={{env:String}} AND o.chain_id={{chain_id:UInt64}} AND o.order_uid={{id:String}}
ORDER BY o.observed_at DESC"""
    trades = f"""
WITH {_token_metadata_cte()}
SELECT t.block_timestamp,t.tx_hash,t.log_index,t.owner,t.sell_token,t.buy_token,
       if(s.token='','',s.symbol) AS sell_symbol,
       if(b.token='','',b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(t.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(t.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(t.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(t.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(t.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(t.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       t.source,t.observed_at AS source_observed_at
FROM cow_db.trades AS t
LEFT JOIN tm AS s ON s.token=t.sell_token
LEFT JOIN tm AS b ON b.token=t.buy_token
WHERE t.environment={{env:String}} AND t.chain_id={{chain_id:UInt64}} AND t.order_uid={{id:String}}
ORDER BY t.block_timestamp DESC,t.log_index DESC,t.tx_hash DESC"""
    events = """
SELECT event_id,event_type,owner,block_number,transaction_hash,log_index,event_timestamp,
       payload,source,observed_at AS source_observed_at
FROM cow_db.order_events FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND order_uid={id:String}
ORDER BY coalesce(event_timestamp,observed_at),event_id"""
    fees = f"""
WITH {_token_metadata_cte()}
SELECT f.tx_hash,f.log_index,f.fee_index,f.token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       toString(f.amount) AS amount_raw,
       if(tm.token='',NULL,tm.decimals) AS token_decimals,
       if(tm.token='',NULL,toFloat64(f.amount)/pow(10,toFloat64(tm.decimals))) AS amount,
       f.policy,
       multiIf(positionCaseInsensitive(f.policy,'priceImprovement')>0,'price_improvement',
               positionCaseInsensitive(f.policy,'surplus')>0,'surplus',
               positionCaseInsensitive(f.policy,'volume')>0,'volume','other') AS policy_family,
       f.source,f.observed_at AS source_observed_at
FROM cow_db.protocol_fees AS f FINAL
LEFT JOIN tm ON tm.token=f.token
WHERE f.environment={{env:String}} AND f.chain_id={{chain_id:UInt64}} AND f.order_uid={{id:String}}
ORDER BY f.observed_at,f.tx_hash,f.log_index,f.fee_index"""
    app_data = """
WITH ord AS (
 SELECT app_data_hash FROM cow_db.orders FINAL
 WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND order_uid={id:String}
 LIMIT 1
)
SELECT a.app_data_hash,a.full_app_data,a.source,a.observed_at AS source_observed_at
FROM cow_db.app_data AS a FINAL
INNER JOIN ord ON a.app_data_hash=ord.app_data_hash
WHERE a.environment={env:String} AND a.chain_id={chain_id:UInt64}
ORDER BY a.observed_at DESC"""
    realized_surplus = SURPLUS_BPS.format(
        eb="any(o.executed_buy_amount)", ls="any(o.sell_amount)",
        es="any(o.executed_sell_amount)", lb="any(o.buy_amount)",
    )
    quality = f"""
WITH o AS (
  SELECT * FROM cow_db.orders FINAL
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND order_uid={{id:String}}
  LIMIT 1
)
SELECT
 (SELECT count() FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND order_uid={{id:String}}) AS fills,
 any(o.kind) AS kind,
 {realized_surplus} AS realized_surplus_bps,
 if(any(o.kind)='buy',
    toFloat64(any(o.executed_buy_amount))/nullIf(toFloat64(any(o.buy_amount)),0),
    toFloat64(any(o.executed_sell_amount))/nullIf(toFloat64(any(o.sell_amount)),0)) AS fill_ratio,
 any(o.creation_date) AS creation_date,
 (SELECT min(block_timestamp) FROM cow_db.trades
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND order_uid={{id:String}}) AS first_fill_at,
 max(o.observed_at) AS source_observed_at
FROM o
ORDER BY fills"""
    return _entity_query_specs([
        ("order_detail", "Order", detail, "creation_date"),
        ("order_quality", "Execution quality vs limit", quality, "observed_at"),
        ("order_trades", "Settled fills", trades, "block_timestamp"),
        ("order_events", "Observed order lifecycle", events, "observed_at"),
        ("order_fees", "Indexed fee-policy amounts", fees, "observed_at"),
        ("order_app_data", "App data", app_data, "observed_at"),
    ], params)


def _transaction_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    detail = """
WITH comp AS (
 SELECT auction_id FROM cow_db.competition_transactions FINAL
 WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND tx_hash={id:String}
)
SELECT s.tx_hash,s.block_number,s.block_hash,s.block_timestamp,
       s.solver AS settlement_executor,s.log_index,comp.auction_id,
       s.observed_at AS source_observed_at
FROM cow_db.settlements_canonical s
LEFT JOIN comp ON 1
WHERE s.environment={env:String} AND s.chain_id={chain_id:UInt64} AND s.tx_hash={id:String}
ORDER BY s.log_index"""
    trades = f"""
WITH {_token_metadata_cte()}
SELECT t.block_timestamp,t.log_index,t.order_uid,t.owner,t.sell_token,t.buy_token,
       if(s.symbol='',t.sell_token,s.symbol) AS sell_symbol,
       if(b.symbol='',t.buy_token,b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(t.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(t.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(t.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(t.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(t.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(t.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       t.source,t.observed_at AS source_observed_at
FROM cow_db.trades_canonical AS t
LEFT JOIN tm AS s ON s.token=t.sell_token
LEFT JOIN tm AS b ON b.token=t.buy_token
WHERE t.environment={{env:String}} AND t.chain_id={{chain_id:UInt64}} AND t.tx_hash={{id:String}}
ORDER BY t.log_index,t.order_uid"""
    interactions = """
SELECT block_timestamp,log_index,target,toString(value) AS value_raw,selector,
       observed_at AS source_observed_at
FROM cow_db.interactions_canonical
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND tx_hash={id:String}
ORDER BY log_index,target"""
    competition = """
SELECT ct.auction_id,ct.tx_index,c.winner AS competition_winner,c.reference_score,
       c.auction_block,ws.solver AS winning_solution_solver,
       ws.tx_hash AS solution_tx_hash,ws.solution_index,
       greatest(ct.observed_at,c.observed_at,ws.observed_at) AS source_observed_at
FROM cow_db.competition_transactions AS ct FINAL
LEFT JOIN cow_db.solver_competitions AS c FINAL
 ON ct.environment=c.environment AND ct.chain_id=c.chain_id AND ct.auction_id=c.auction_id
LEFT JOIN cow_db.competition_solutions AS ws FINAL
 ON ct.environment=ws.environment AND ct.chain_id=ws.chain_id
 AND ct.auction_id=ws.auction_id AND ws.is_winner
WHERE ct.environment={env:String} AND ct.chain_id={chain_id:UInt64} AND ct.tx_hash={id:String}
ORDER BY ct.auction_id,ct.tx_index,ws.solution_index"""
    return _entity_query_specs([
        ("transaction_detail", "Settlement transaction", detail, "block_timestamp"),
        ("transaction_trades", "Settled fills", trades, "block_timestamp"),
        ("transaction_interactions", "Settlement interactions", interactions, "block_timestamp"),
        ("transaction_competition", "Competition mapping", competition, "observed_at"),
    ], params)


def _address_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    # Counts read the base tables dedup-free (<0.1% RMT duplicate rate,
    # disclosed) — counting through the canonical views pays a FINAL +
    # chain_blocks join per subquery, which made entity opens multi-second.
    summary = """
SELECT
 (SELECT count() FROM cow_db.trades PREWHERE owner={id:String} WHERE environment={env:String} AND chain_id={chain_id:UInt64}) AS owned_fills,
 (SELECT count() FROM cow_db.orders FINAL WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND owner={id:String}) AS owned_orders,
 (SELECT count() FROM cow_db.settlements PREWHERE solver={id:String} WHERE environment={env:String} AND chain_id={chain_id:UInt64}) AS executed_settlements,
 (SELECT count() FROM cow_db.competition_solutions FINAL WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}) AS submitted_solutions,
 (SELECT max(observed_at) FROM cow_db.orders FINAL WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND owner={id:String}) AS source_observed_at
ORDER BY owned_fills"""
    # Top-N-first owner tape: PREWHERE prunes column reads before the wide
    # SELECT list materializes (owner is not in the sort key, so this is a
    # full-partition scan either way — PREWHERE + bounded heap keep it cheap
    # and memory-safe at the entity view's all-history default).
    trades = f"""
WITH {_token_metadata_cte()}, cp AS (
  SELECT argMax(block_number,updated_at) AS b
  FROM cow_db.indexing_checkpoints
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND source='rpc'
)
SELECT u.block_timestamp,u.tx_hash,u.log_index,u.order_uid,u.sell_token,u.buy_token,
       if(s.token='','',s.symbol) AS sell_symbol,
       if(b.token='','',b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       u.obs_at AS source_observed_at
FROM (
  SELECT tx_hash,log_index,order_uid,
         argMax(block_timestamp,observed_at) AS block_timestamp,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         max(observed_at) AS obs_at
  FROM (
    SELECT t.tx_hash,t.log_index,t.order_uid,t.block_timestamp,
           t.sell_token,t.buy_token,t.sell_amount,t.buy_amount,t.observed_at
    FROM cow_db.trades AS t
    PREWHERE t.owner={{id:String}}
    WHERE t.environment={{env:String}} AND t.chain_id={{chain_id:UInt64}}
      AND t.block_number<=(SELECT b FROM cp)
    ORDER BY t.block_timestamp DESC
    LIMIT {TAPE_ARM_LIMIT}
  )
  GROUP BY tx_hash,log_index,order_uid
  ORDER BY block_timestamp DESC
  LIMIT {ROW_CAP}
) AS u
LEFT JOIN tm AS s ON s.token=u.sell_token
LEFT JOIN tm AS b ON b.token=u.buy_token
ORDER BY u.block_timestamp DESC,u.log_index DESC"""
    orders = f"""
WITH {_token_metadata_cte()}
SELECT o.order_uid,o.creation_date,o.status,o.kind,o.sell_token,o.buy_token,
       if(s.token='','',s.symbol) AS sell_symbol,
       if(b.token='','',b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(o.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(o.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(o.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(o.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       o.valid_to,o.observed_at AS source_observed_at
FROM cow_db.orders AS o FINAL
LEFT JOIN tm AS s ON s.token=o.sell_token
LEFT JOIN tm AS b ON b.token=o.buy_token
WHERE o.environment={{env:String}} AND o.chain_id={{chain_id:UInt64}} AND o.owner={{id:String}}
ORDER BY o.creation_date DESC,o.order_uid DESC"""
    solver = f"""
SELECT * FROM (
  SELECT 'settlement_executor' AS role,tx_hash AS identifier,
         toNullable(block_timestamp) AS event_time,observed_at AS source_observed_at
  FROM cow_db.settlements
  PREWHERE solver={{id:String}}
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
  ORDER BY block_timestamp DESC
  LIMIT {ROW_CAP}
  UNION ALL
  SELECT 'competition_solver',toString(auction_id),CAST(NULL AS Nullable(DateTime64(3))),
         observed_at
  FROM cow_db.competition_solutions FINAL
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND solver={{id:String}}
)
ORDER BY event_time DESC,identifier DESC
LIMIT {ROW_CAP}"""
    return _entity_query_specs([
        ("address_summary", "Address activity summary", summary, "observed_at"),
        ("address_trades", "Owned settled fills", trades, "block_timestamp"),
        ("address_orders", "Owned orders", orders, "creation_date"),
        ("address_solver_activity", "Solver and executor roles", solver, "observed_at"),
    ], params)


def _token_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    detail = f"""
WITH {_token_metadata_cte()}
SELECT token,symbol,name,decimals,
       if(token='{NATIVE_TOKEN}','synthetic_native','token_metadata') AS source,
       metadata_observed_at AS source_observed_at
FROM tm
WHERE token={{id:String}}
ORDER BY token"""
    pairs = f"""
WITH {_token_metadata_cte()}, cp AS (
  SELECT argMax(block_number,updated_at) AS b
  FROM cow_db.indexing_checkpoints
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND source='rpc'
),
p AS (
  SELECT least(sell_token,buy_token) AS token0,greatest(sell_token,buy_token) AS token1,
         count() AS fill_count,uniq(tx_hash) AS settlement_transactions,
         min(block_timestamp) AS indexed_from,max(block_timestamp) AS indexed_to,
         max(observed_at) AS source_observed_at
  FROM cow_db.trades
  PREWHERE (sell_token={{id:String}} OR buy_token={{id:String}})
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
   AND block_number<=(SELECT b FROM cp) AND block_timestamp IS NOT NULL
  GROUP BY token0,token1
)
SELECT p.token0 AS token0,p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.fill_count AS fill_count,p.settlement_transactions AS settlement_transactions,
       p.indexed_from AS indexed_from,p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM p
LEFT JOIN tm AS m0 ON m0.token=p.token0
LEFT JOIN tm AS m1 ON m1.token=p.token1
ORDER BY p.fill_count DESC,p.token0,p.token1"""
    executions = f"""
WITH {_token_metadata_cte()}, fills AS (
 SELECT t.block_timestamp,t.tx_hash,t.observed_at,
        if(t.sell_token={{id:String}},t.buy_token,t.sell_token) AS quote_token,
        if(t.sell_token={{id:String}},qbuy.symbol,qsell.symbol) AS quote_symbol,
        if(t.sell_token={{id:String}},
           toFloat64(t.sell_amount)/pow(10,toFloat64(base.decimals)),
           toFloat64(t.buy_amount)/pow(10,toFloat64(base.decimals))) AS base_qty,
        if(t.sell_token={{id:String}},
           toFloat64(t.buy_amount)/pow(10,toFloat64(qbuy.decimals)),
           toFloat64(t.sell_amount)/pow(10,toFloat64(qsell.decimals))) AS quote_qty
 FROM cow_db.trades AS t
 INNER JOIN tm AS base ON base.token={{id:String}}
 INNER JOIN tm AS qbuy ON qbuy.token=t.buy_token
 INNER JOIN tm AS qsell ON qsell.token=t.sell_token
 PREWHERE (t.sell_token={{id:String}} OR t.buy_token={{id:String}})
 WHERE t.environment={{env:String}} AND t.chain_id={{chain_id:UInt64}}
  AND t.block_timestamp IS NOT NULL
  AND t.block_number<=(SELECT argMax(block_number,updated_at) FROM cow_db.indexing_checkpoints
                       WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}} AND source='rpc')
)
SELECT toStartOfDay(block_timestamp) AS bucket,quote_token,any(quote_symbol) AS quote_symbol,
       sum(quote_qty)/nullIf(sum(base_qty),0) AS vwap_quote_per_token,
       sum(base_qty) AS base_volume,count() AS fill_count,
       uniq(tx_hash) AS settlement_transactions,min(block_timestamp) AS indexed_from,
       max(block_timestamp) AS indexed_to,max(observed_at) AS source_observed_at
FROM fills
WHERE base_qty>0 AND quote_qty>=0
GROUP BY bucket,quote_token
ORDER BY bucket,quote_token"""
    native = """
SELECT observed_at,native_price,source,observed_at AS indexed_from,
       observed_at AS indexed_to,observed_at AS source_observed_at
FROM cow_db.native_prices FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND token={id:String}
ORDER BY observed_at"""
    return _entity_query_specs([
        ("token_detail", "Token metadata", detail, "observed_at"),
        ("token_pairs", "Indexed execution pairs", pairs, "block_timestamp"),
        ("token_execution_prices", "Execution prices (settled fills)", executions, "block_timestamp"),
        ("token_native_prices", "Native-price API observations", native, "observed_at"),
    ], params)


def _auction_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    # chain_blocks pruned to this auction's block (block_number IN, sort-key
    # pruned; argMax dedups → no FINAL). The prior `chain_blocks FINAL` LEFT
    # JOIN materialized the whole ~2.3M-row table for a single-auction lookup.
    detail = """
SELECT c.auction_id,c.winner AS competition_winner,c.reference_score,c.auction_block,
       nullIf(b.block_timestamp,toDateTime(0)) AS auction_timestamp,
       c.source,c.observed_at AS source_observed_at
FROM cow_db.solver_competitions AS c FINAL
LEFT JOIN (
  SELECT chain_id, block_number, argMax(block_timestamp, observed_at) AS block_timestamp
  FROM cow_db.chain_blocks
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_number IN (SELECT auction_block FROM cow_db.solver_competitions FINAL
                         WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64})
  GROUP BY chain_id, block_number
) AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
WHERE c.environment={env:String} AND c.chain_id={chain_id:UInt64} AND c.auction_id={id:UInt64}
ORDER BY c.observed_at DESC"""
    orders = """
SELECT order_uid,payload,observed_at AS source_observed_at
FROM cow_db.auction_orders FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64}
ORDER BY order_uid"""
    prices = f"""
WITH {_token_metadata_cte()}
SELECT p.token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       toString(p.price) AS price_raw,p.observed_at AS source_observed_at
FROM cow_db.auction_prices AS p FINAL
LEFT JOIN tm ON tm.token=p.token
WHERE p.environment={{env:String}} AND p.chain_id={{chain_id:UInt64}} AND p.auction_id={{id:UInt64}}
ORDER BY p.token"""
    solutions = """
SELECT solution_index,solver AS competition_solver,score,ranking,is_winner,tx_hash,
       payload,observed_at AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64}
ORDER BY ranking,solution_index"""
    transactions = """
SELECT tx_index,tx_hash,source,observed_at AS source_observed_at
FROM cow_db.competition_transactions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64}
ORDER BY tx_index,tx_hash"""
    return _entity_query_specs([
        ("auction_detail", "Auction", detail, "auction_block_timestamp"),
        ("auction_orders", "Auction orders", orders, "observed_at"),
        ("auction_prices", "Auction price vector", prices, "observed_at"),
        ("auction_solutions", "Competition solutions", solutions, "observed_at"),
        ("auction_transactions", "Settlement transactions", transactions, "observed_at"),
    ], params)


def _solver_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    summary = """
SELECT uniqExact(auction_id) AS competitions,
       count() AS solutions,
       countIf(is_winner) AS wins,
       countIf(is_winner)/nullIf(uniqExact(auction_id),0) AS win_rate,
       countIf(is_winner AND ranking!=1) AS multi_winner_solutions,
       countIf(is_winner AND ranking!=1)/nullIf(countIf(is_winner),0) AS multi_winner_share,
       countIf(toUInt256OrZero(score)=0 AND score NOT IN ('','0')) AS score_parse_failures,
       avg(toFloat64(ranking)) AS average_ranking,
       min(ranking) AS best_ranking,
       (SELECT uniq(tx_hash) FROM cow_db.settlements
        WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}) AS executed_settlements,
       max(observed_at) AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}
ORDER BY competitions"""
    competitions = """
SELECT auction_id,solution_index,score,ranking,is_winner,tx_hash,
       observed_at AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}
ORDER BY observed_at DESC,auction_id DESC,ranking"""
    solutions = """
SELECT ranking,count() AS solution_count,countIf(is_winner) AS wins,
       max(observed_at) AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}
GROUP BY ranking
ORDER BY ranking"""
    # Top-N-first executor tape on the base table (solver is not in the sort
    # key — full-partition scan either way; PREWHERE + bounded heap keep it
    # cheap and memory-safe at all-history).
    settlements = f"""
SELECT tx_hash,log_index,
       argMax(block_number,observed_at) AS block_number,
       argMax(block_timestamp,observed_at) AS block_timestamp,
       any({{id:String}}) AS settlement_executor,
       max(observed_at) AS source_observed_at
FROM (
  SELECT tx_hash,log_index,block_number,block_timestamp,observed_at
  FROM cow_db.settlements
  PREWHERE solver={{id:String}}
  WHERE environment={{env:String}} AND chain_id={{chain_id:UInt64}}
  ORDER BY block_timestamp DESC
  LIMIT {TAPE_ARM_LIMIT}
)
GROUP BY tx_hash,log_index
ORDER BY block_timestamp DESC,log_index DESC
LIMIT {ROW_CAP}"""
    # Shared accounting CTEs: this solver's settlements over the last 30
    # indexed days, and the per-token net flow between traders and the
    # settlement contract in each of those batches. This is ORDER-LEVEL,
    # TRADE-IMPLIED accounting — AMM leg amounts, plain ERC20 transfers, and
    # buffer balances are NOT in cow_db, so it shows what the solver had to
    # source externally (or what accrued), not audited buffer books.
    accounting_ctes = """
exec AS (
  -- Base settlements (NOT the settlements_canonical view, whose FINAL +
  -- chain_blocks materialization OOMed the box). The block_timestamp bound
  -- keeps the GROUP BY tx_hash hash to ~30d of txs (small); the scan streams.
  -- The resulting tx_hash set is this solver's settlement txs — and tx_hash IS
  -- in the trades sort key (environment, chain_id, tx_hash, log_index), so it
  -- PRUNES the trades_canonical scan in `flows` (block_number would NOT — it is
  -- not in the sort key).
  SELECT tx_hash, argMax(solver,tuple(block_timestamp,log_index)) AS s
  FROM cow_db.settlements
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_timestamp >= (
      SELECT max(block_timestamp) FROM cow_db.settlements
      WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    ) - toIntervalDay(30)
  GROUP BY tx_hash
  HAVING s={id:String}
),
fills_d AS (
  -- Deduped base trades for this solver's settlement txs. tx_hash IN (…) PRUNES
  -- (tx_hash is in the sort key) to ~125k rows; argMax over the RMT version key
  -- dedups WITHOUT touching trades_canonical (whose internal chain_blocks-FINAL
  -- reorg-join would scan ~2.2M chain_blocks regardless). Settlements 30d back
  -- are committed/final, so the reorg-safe view buys nothing here.
  SELECT tx_hash, log_index, order_uid,
         argMax(block_timestamp,observed_at) AS block_timestamp,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND tx_hash IN (SELECT tx_hash FROM exec)
  GROUP BY tx_hash, log_index, order_uid
),
flows AS (
  SELECT u.tx_hash AS tx_hash, any(u.block_timestamp) AS block_timestamp,
         u.token AS token, sum(u.amt) AS net_atoms
  FROM (
    SELECT tx_hash, block_timestamp, sell_token AS token, toInt256(sell_amount) AS amt FROM fills_d
    UNION ALL
    SELECT tx_hash, block_timestamp, buy_token, -toInt256(buy_amount) FROM fills_d
  ) AS u
  GROUP BY u.tx_hash, u.token
),
am AS (
  SELECT tx_hash, argMax(auction_id,observed_at) AS auction_id
  FROM cow_db.competition_transactions FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY tx_hash
),
pr AS (
  SELECT auction_id, token, argMax(price,observed_at) AS price
  FROM cow_db.auction_prices FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY auction_id, token
)"""
    imbalance_settlements = f"""
WITH {accounting_ctes}
SELECT f.tx_hash AS tx_hash, any(f.block_timestamp) AS block_timestamp,
       count() AS tokens_touched,
       countIf(toFloat64(pr.price)=0) AS unpriced_tokens,
       sum(if(toFloat64(pr.price)>0,
              toFloat64(f.net_atoms)*toFloat64(pr.price)/1e18, 0)) AS net_native_wei_known,
       max(f.block_timestamp) AS source_observed_at
FROM flows AS f
LEFT JOIN am ON am.tx_hash=f.tx_hash
LEFT JOIN pr ON pr.auction_id=am.auction_id AND pr.token=f.token
GROUP BY f.tx_hash
ORDER BY block_timestamp DESC, tx_hash
LIMIT 500"""
    imbalance_tokens = f"""
WITH {_token_metadata_cte()},{accounting_ctes}
SELECT f.token AS token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       if(any(tm.token)='',NULL,any(tm.decimals)) AS token_decimals,
       count() AS settlements,
       toString(sum(f.net_atoms)) AS net_amount_raw,
       if(any(tm.token)='',NULL,
          toFloat64(sum(f.net_atoms))/pow(10,toFloat64(any(tm.decimals)))) AS net_amount,
       sum(if(toFloat64(pr.price)>0,
              toFloat64(f.net_atoms)*toFloat64(pr.price)/1e18, 0)) AS net_native_wei_known,
       max(f.block_timestamp) AS source_observed_at
FROM flows AS f
LEFT JOIN am ON am.tx_hash=f.tx_hash
LEFT JOIN pr ON pr.auction_id=am.auction_id AND pr.token=f.token
LEFT JOIN tm ON tm.token=f.token
GROUP BY f.token, token_symbol
ORDER BY abs(sum(if(toFloat64(pr.price)>0,toFloat64(f.net_atoms)*toFloat64(pr.price)/1e18,0))) DESC, token
LIMIT 200"""
    # reference_score is ALWAYS a JSON map keyed by solver address (verified
    # live 2026-07-21): JSONExtractString is the only parse path.
    score_gap = """
WITH blk AS (
  SELECT chain_id, block_number,
         argMax(block_timestamp, observed_at) AS block_timestamp
  FROM cow_db.chain_blocks
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_number IN (
      SELECT auction_block FROM cow_db.solver_competitions FINAL
      WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    )
  GROUP BY chain_id, block_number
)
SELECT c.auction_id AS auction_id,
       b.block_timestamp AS auction_timestamp,
       toFloat64OrNull(s.score) AS winning_score,
       toFloat64OrNull(JSONExtractString(c.reference_score,{id:String})) AS reference_score,
       toFloat64OrNull(s.score)
         - toFloat64OrNull(JSONExtractString(c.reference_score,{id:String})) AS score_gap,
       (toFloat64OrNull(s.score) IS NOT NULL
        AND toFloat64OrNull(JSONExtractString(c.reference_score,{id:String})) IS NOT NULL) AS scores_parsed,
       s.observed_at AS source_observed_at
FROM cow_db.solver_competitions AS c FINAL
INNER JOIN cow_db.competition_solutions AS s FINAL
  ON s.environment=c.environment AND s.chain_id=c.chain_id
 AND s.auction_id=c.auction_id AND s.is_winner AND s.solver={id:String}
LEFT JOIN blk AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
WHERE c.environment={env:String} AND c.chain_id={chain_id:UInt64}
ORDER BY auction_timestamp DESC, auction_id DESC
LIMIT 500"""
    return _entity_query_specs([
        ("solver_summary", "Solver dashboard summary", summary, "observed_at"),
        ("solver_competitions", "Competition solver entries", competitions, "observed_at"),
        ("solver_solutions", "Ranking distribution", solutions, "observed_at"),
        ("solver_settlements", "Settlement executor transactions", settlements, "block_timestamp"),
        ("solver_imbalance_settlements", "Settlement imbalance (order-level, trade-implied, 30d)", imbalance_settlements, "block_timestamp"),
        ("solver_imbalance_tokens", "Token imbalance (order-level, trade-implied, 30d)", imbalance_tokens, "block_timestamp"),
        ("solver_score_gap", "Winning vs reference score (where parseable)", score_gap, "observed_at"),
    ], params)


def _entity_query_specs(
    rows: list[tuple[str, str, str, str]],
    parameters: dict[str, Any],
) -> list[QuerySpec]:
    return [
        QuerySpec(key, title, sql, dict(parameters), basis, "observed_series", 300)
        for key, title, sql, basis in rows
    ]


def _section_specs(
    section: str,
    scope: str,
    chain: ChainInfo | None,
    pair: tuple[str, str],
    interval: str,
    range_state: dict[str, Any],
    filters: dict[str, str],
    depth_at: str = "",
    heatmap_window: str = "7d",
) -> list[QuerySpec]:
    if section == "overview":
        return _overview_specs(scope, range_state, chain)
    if section == "trades":
        return _trade_specs(scope, chain, range_state, filters)
    if section == "solvers":
        return _solver_specs(scope, chain, range_state, pair, filters)
    if section == "traders":
        return _traders_specs(scope, chain, range_state)
    if section == "auctions":
        return _auction_specs(chain, range_state, scope)
    if section == "orders":
        return _order_specs(scope, chain, pair, range_state, filters)
    if section == "live":
        return _live_specs(scope, chain)
    assert chain is not None
    if section == "markets":
        return _market_specs(chain, pair, interval, range_state, depth_at, heatmap_window)
    if section == "patterns":
        return _patterns_specs(scope, chain, range_state)
    raise ValueError(f"Unsupported section: {section}")


def _iso_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _coverage_from_dataset(
    dataset: CachedDataset,
    spec: QuerySpec,
    range_state: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    columns = {name: idx for idx, name in enumerate(dataset.columns)}
    warning_codes: list[str] = []

    def values(name: str) -> list[Any]:
        idx = columns.get(name)
        if idx is None:
            return []
        return [row[idx] for row in dataset.rows if idx < len(row) and row[idx] is not None]

    starts = values("indexed_from")
    ends = values("indexed_to")
    if not starts or not ends:
        basis_columns = {
            "block_timestamp": ("block_timestamp",),
            "creation_date": ("creation_date",),
            "auction_block_timestamp": ("auction_timestamp", "block_timestamp"),
            "observed_at": ("observed_at", "source_observed_at"),
        }.get(spec.basis, ())
        basis_values: list[Any] = []
        for name in basis_columns:
            basis_values = values(name)
            if basis_values:
                break
        if not starts:
            starts = basis_values
        if not ends:
            ends = basis_values
    observed = values("source_observed_at")
    checkpoints = values("checkpoint_block")
    checkpoint_times = values("checkpoint_timestamp")
    if not dataset.rows:
        warning_codes.append("no_indexed_data")
    if dataset.stats.truncated:
        warning_codes.append("result_truncated")
    if spec.key in {"known_orders", "known_intents", "intent_depth", "pair_depth", "open_intent_pairs"}:
        warning_codes.append("known_intents_incomplete")
    if spec.key == "pair_depth" and spec.parameters.get("at_ts"):
        warning_codes.append("depth_reconstructed")
    if spec.key == "pair_depth_heatmap":
        warning_codes.append("depth_heatmap_reconstructed")
    if spec.key == "coverage_matrix":
        checkpoint_idx = columns.get("checkpoint_block")
        if checkpoint_idx is None or any(
            checkpoint_idx >= len(row) or row[checkpoint_idx] in (None, "")
            for row in dataset.rows
        ):
            warning_codes.append("missing_checkpoint")
        checkpoint_time_idx = columns.get("checkpoint_timestamp")
        if checkpoint_time_idx is None or any(
            checkpoint_time_idx >= len(row) or row[checkpoint_time_idx] in (None, "")
            for row in dataset.rows
        ):
            warning_codes.append("missing_block_timestamp")
        competition_idx = columns.get("max_competition_block")
        if checkpoint_idx is not None and competition_idx is not None and any(
            row[competition_idx] not in (None, "")
            and row[checkpoint_idx] not in (None, "")
            and int(row[competition_idx]) > int(row[checkpoint_idx])
            for row in dataset.rows
            if competition_idx < len(row) and checkpoint_idx < len(row)
        ):
            warning_codes.append("missing_block_timestamp")
            warning_codes.append("partial_backfill")
    if spec.key == "market_summary" and dataset.rows:
        for name in ("base_decimals", "quote_decimals"):
            idx = columns.get(name)
            if idx is None or dataset.rows[0][idx] is None:
                warning_codes.append("missing_token_metadata")
                break
    for name, decimals_idx in columns.items():
        if not (name == "token_decimals" or name.endswith("_decimals")):
            continue
        if any(
            decimals_idx >= len(row) or row[decimals_idx] is None
            for row in dataset.rows
        ):
            warning_codes.append("missing_token_metadata")
            break
    latest_observed = max(observed) if observed else None
    stale_threshold = 1800 if spec.key == "native_reference_prices" else 600
    if isinstance(latest_observed, datetime):
        if (datetime.now(timezone.utc) - latest_observed.astimezone(timezone.utc)).total_seconds() > stale_threshold:
            warning_codes.append("stale_source")
    warning_codes = list(dict.fromkeys(warning_codes))
    requested_start = range_state.get("start_at") or None
    requested_end = range_state.get("end_at") or None
    if range_state.get("kind") == "relative" and ends:
        anchor_value = max(ends)
        if isinstance(anchor_value, datetime):
            requested_end = _iso_value(anchor_value)
            requested_start = _iso_value(
                anchor_value - timedelta(days=int(range_state.get("window_days") or 0))
            )
    coverage = {
        "basis": spec.basis,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "actual_start": _iso_value(min(starts)) if starts else None,
        "actual_end": _iso_value(max(ends)) if ends else None,
        "anchor": range_state.get("anchor"),
        "latest_source_observation": _iso_value(latest_observed),
        "fetched_at": dataset.stats.fetched_at,
        "checkpoint_block": max(checkpoints) if checkpoints else None,
        "checkpoint_timestamp": _iso_value(max(checkpoint_times)) if checkpoint_times else None,
        "returned_rows": dataset.stats.rows_returned,
        "source_rows": dataset.stats.source_rows,
        "row_cap": dataset.stats.row_cap,
        "truncated": bool(dataset.stats.truncated),
        "mode": spec.coverage_mode,
        "warning_codes": warning_codes,
    }
    return coverage, warning_codes


#: Negative-result cache: a failed dataset query is remembered so a manual
#: Retry within the TTL returns the cached failure INSTANTLY instead of
#: re-running a query known to blow up or time out. force_refresh bypasses
#: the read (an explicit refresh may genuinely retry).
_FAILURE_CACHE = FailureCache(COW_DB)


def reset_failure_cache_for_tests() -> None:
    _FAILURE_CACHE.reset()


def _log_dataset_loaded(
    spec: QuerySpec,
    dataset: CachedDataset,
    range_state: dict[str, Any],
    elapsed: float,
) -> None:
    logger.info(
        "cow_explorer_dataset key=%s environment=%s chain=%s window=%s rows=%s source_rows=%s truncated=%s elapsed=%.3f",
        spec.key,
        spec.parameters.get("env", ""),
        spec.parameters.get("chain_id", 0),
        range_state.get("window_days", range_state.get("kind")),
        dataset.stats.rows_returned,
        dataset.stats.source_rows,
        dataset.stats.truncated,
        elapsed,
    )


def _log_dataset_failed(spec: QuerySpec, error: Exception) -> None:
    logger.warning("cow_explorer dataset %s failed: %s", spec.key, error)


def _failure_coverage(
    spec: QuerySpec,
    range_state: dict[str, Any],
    fetched_at: str,
    error: str,
) -> dict[str, Any]:
    return {
        "basis": spec.basis,
        "requested_start": range_state.get("start_at") or None,
        "requested_end": range_state.get("end_at") or None,
        "actual_start": None,
        "actual_end": None,
        "anchor": range_state.get("anchor"),
        "latest_source_observation": None,
        "fetched_at": fetched_at,
        "checkpoint_block": None,
        "checkpoint_timestamp": None,
        "returned_rows": 0,
        "source_rows": None,
        "row_cap": ROW_CAP,
        "truncated": False,
        "mode": spec.coverage_mode,
        "warning_codes": ["query_failed"],
        # The frontend renders an explicit error card from this —
        # a failed dataset must stay VISIBLE, never silently vanish.
        "error": error[:400],
    }


def _load_specs_safe(
    ch: ClickHouseManager,
    specs: list[QuerySpec],
    range_state: dict[str, Any],
    *,
    force_refresh: bool,
) -> tuple[dict[str, CachedDataset], dict[str, Any], list[str]]:
    # ClickHouseManager maintains thread-local clients. All-network bundles
    # stay at two workers: their per-chain UNION arms are individually
    # memory-bounded, but running three ten-arm expansions at once can still
    # push the ClickHouse server's TOTAL memory over its limit (observed
    # live: code 241 "(total) memory limit exceeded" at ~11 GiB).
    all_network = any(
        int((spec.parameters or {}).get("chain_id") or 0) == 0 for spec in specs
    )
    return mini_apps.load_specs_safe(
        ch,
        specs,
        range_state,
        force_refresh=force_refresh,
        database=COW_DB,
        row_cap=ROW_CAP,
        failure_cache=_FAILURE_CACHE,
        coverage_fn=_coverage_from_dataset,
        failure_coverage_fn=_failure_coverage,
        worker_limit=2 if all_network else 3,
        thread_name_prefix="cow-data",
        log_success=_log_dataset_loaded,
        log_failure=_log_dataset_failed,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )


def _empty_loaded_groups() -> dict[str, Any]:
    return {
        f"{section}.{group}": False
        for section, groups in SECTION_GROUPS.items()
        for group in groups
    }


def _empty_state(
    scope: str,
    chain: ChainInfo | None,
    title: str,
    section: str = "overview",
) -> dict[str, Any]:
    return {
        "section": section,
        "environment_scope": scope,
        "environment": chain.environment if chain else scope,
        "chain_id": chain.chain_id if chain else 0,
        "chain_name": chain.name if chain else "All networks",
        "chain_options": [_chain_dict(c) for c in _chains_for_scope(scope)],
        "explorer": asdict(chain.explorer) if chain else None,
        "pair": {"base": "", "quote": "", "base_symbol": "", "quote_symbol": ""},
        "interval": "1h",
        "date_range": _range_state(section if section in SECTION_DEFAULT_DAYS else "overview", -1, "", ""),
        "filters": {"status": "", "owner": "", "solver": "", "token": ""},
        "selected_entity": None,
        "breadcrumbs": [],
        "search": {"query": "", "candidates": []},
        "applied_request_id": 0,
        "scope_id": f"{scope}:{chain.chain_id if chain else 0}:{section}:0",
        "coverage": {},
        "coverage_warnings": [],
        "warnings": [],
        "dataset_revisions": {},
        # Deferred-load bookkeeping (v2): which section.group bundles are loaded
        # (False | True | "error"), the scope fingerprint each section's cached
        # datasets were loaded under, the keys each section currently retains,
        # LRU order for eviction, and the async token-icon overlay.
        "loaded_groups": _empty_loaded_groups(),
        "section_fingerprints": {},
        "section_datasets": {},
        "section_lru": [],
        "icon_overlay": {},
        "title": title,
    }


_dataset_titles = mini_apps.dataset_titles


def _summary_cards(record: mini_apps.ViewRecord) -> list[SummaryCard]:
    state = record.view_state
    cards = [
        SummaryCard(label="Scope", value=str(state.get("chain_name") or "All networks")),
        SummaryCard(label="Window", value=(
            "All indexed history" if (state.get("date_range") or {}).get("kind") == "all"
            else f"{(state.get('date_range') or {}).get('window_days') or 'Custom'} days"
        )),
    ]
    for key, label in (("network_summary", "Networks"), ("trades", "Fills"), ("known_orders", "Known intents"), ("auctions", "Competitions")):
        dataset = record.datasets.get(key)
        if dataset is not None:
            cards.append(SummaryCard(label=label, value=f"{dataset.stats.source_rows or dataset.stats.row_count:,}"))
    return cards[:5]


def _payload_from_record(
    record: mini_apps.ViewRecord,
    titles: dict[str, str] | None = None,
) -> MiniAppPayload:
    return mini_apps.payload_from_record(
        record,
        app_id=COW_APP_ID,
        database=COW_DB,
        summary_cards=_summary_cards,
        titles=titles,
    )


def _search_scope(scope: str, chain_id: int) -> tuple[str, dict[str, Any]]:
    if chain_id:
        chain = COW_CHAINS.get(chain_id)
        if chain is None or chain.environment != scope:
            raise ValueError("Search chain is not in the selected scope")
        return "environment={env:String} AND chain_id={chain_id:UInt64}", {"env": scope, "chain_id": chain_id}
    ids = ",".join(str(c.chain_id) for c in _chains_for_scope(scope))
    return f"environment={{env:String}} AND chain_id IN ({ids})", {"env": scope}


def _search_candidates(
    ch: ClickHouseManager,
    query: str,
    scope: str,
    chain_id: int,
) -> list[dict[str, Any]]:
    q = _normalize_hex(query)
    if len(q) > 114:
        raise ValueError("Search query is too long")
    where, params = _search_scope(scope, chain_id)
    params["q"] = q
    if ORDER_UID_RE.fullmatch(q):
        sql = f"""SELECT chain_id,'order' AS entity_type,'order' AS role,count() AS evidence_count
FROM cow_db.orders FINAL WHERE {where} AND order_uid={{q:String}}
GROUP BY chain_id ORDER BY chain_id"""
        identifier = q
    elif HASH_RE.fullmatch(q):
        sql = f"""SELECT chain_id,'transaction' AS entity_type,'transaction' AS role,sum(evidence_count) AS evidence_count
FROM (
 SELECT chain_id,count() AS evidence_count FROM cow_db.trades WHERE {where} AND tx_hash={{q:String}} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,count() FROM cow_db.settlements WHERE {where} AND tx_hash={{q:String}} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,count() FROM cow_db.competition_transactions FINAL WHERE {where} AND tx_hash={{q:String}} GROUP BY chain_id
) GROUP BY chain_id ORDER BY chain_id"""
        identifier = q
    elif ADDRESS_RE.fullmatch(q):
        sql = f"""SELECT chain_id,role,sum(evidence_count) AS evidence_count
FROM (
 SELECT chain_id,'owner' AS role,count() AS evidence_count FROM cow_db.orders FINAL WHERE {where} AND owner={{q:String}} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'token',count() FROM cow_db.token_metadata FINAL WHERE {where} AND token={{q:String}} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'settlement_executor',count() FROM cow_db.settlements WHERE {where} AND solver={{q:String}} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'competition_solver',count() FROM cow_db.competition_solutions FINAL WHERE {where} AND solver={{q:String}} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'competition_winner',count() FROM cow_db.solver_competitions FINAL WHERE {where} AND winner={{q:String}} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'interaction_target',count() FROM cow_db.interactions_canonical WHERE {where} AND target={{q:String}} GROUP BY chain_id
) GROUP BY chain_id,role HAVING evidence_count>0 ORDER BY chain_id,role"""
        identifier = q
    elif INTEGER_RE.fullmatch(q):
        params["auction_id"] = int(q)
        sql = f"""SELECT chain_id,'auction' AS entity_type,'auction' AS role,count() AS evidence_count
FROM cow_db.solver_competitions FINAL WHERE {where} AND auction_id={{auction_id:UInt64}}
GROUP BY chain_id ORDER BY chain_id"""
        identifier = q
    else:
        params["symbol"] = query.strip()
        sql = f"""SELECT chain_id,token AS identifier,'token' AS entity_type,'token_symbol' AS role,count() AS evidence_count
FROM cow_db.token_metadata FINAL
WHERE {where} AND lower(symbol)=lower({{symbol:String}})
GROUP BY chain_id,token ORDER BY chain_id,token"""
        identifier = ""
    result = mini_apps.run_structured_query(
        ch, sql, COW_DB, params, requested_max_rows=100,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )
    columns = {name: idx for idx, name in enumerate(result.columns)}
    candidates: list[dict[str, Any]] = []
    for row in result.rows:
        cid = int(row[columns["chain_id"]])
        role = str(row[columns["role"]])
        entity_type = (
            str(row[columns["entity_type"]])
            if "entity_type" in columns
            else ("token" if role == "token" else "solver" if role in {"settlement_executor", "competition_solver", "competition_winner"} else "address")
        )
        resolved_id = (
            str(row[columns["identifier"]]).lower()
            if "identifier" in columns else identifier
        )
        chain = COW_CHAINS[cid]
        candidates.append({
            "entity_type": entity_type,
            "identifier": resolved_id,
            "chain_id": cid,
            "chain_name": chain.name,
            "role": role,
            "evidence_count": int(row[columns["evidence_count"]]),
        })
    return candidates


def _display_token(token: str, chain: ChainInfo | None) -> str:
    if token == NATIVE_TOKEN and chain is not None:
        return chain.native_symbol
    return f"{token[:6]}…{token[-4:]}" if token else ""


def _pair_state(
    pair: tuple[str, str],
    chain: ChainInfo | None,
    datasets: dict[str, CachedDataset],
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "base": pair[0],
        "quote": pair[1],
        "base_symbol": _display_token(pair[0], chain),
        "quote_symbol": _display_token(pair[1], chain),
        "base_decimals": None,
        "quote_decimals": None,
    }
    summary = datasets.get("market_summary")
    if summary is None or not summary.rows:
        return state
    columns = {name: index for index, name in enumerate(summary.columns)}
    row = summary.rows[0]
    for key in ("base_symbol", "quote_symbol", "base_decimals", "quote_decimals"):
        index = columns.get(key)
        if index is not None and index < len(row) and row[index] not in (None, ""):
            state[key] = row[index]
    return state


def _section_fingerprint(
    section: str,
    scope: str,
    chain: ChainInfo | None,
    requested_base: str,
    requested_quote: str,
    interval: str,
    range_state: dict[str, Any],
    filters: dict[str, str],
) -> str:
    """Deterministic identity of a section's load scope.

    Built from REQUESTED inputs (pre pair-resolution) so a fingerprint match on
    a tab return can short-circuit with zero ClickHouse round trips.
    """
    return ":".join([
        scope,
        str(chain.chain_id if chain else 0),
        section,
        requested_base,
        requested_quote,
        interval,
        str(range_state.get("kind")),
        str(range_state.get("window_days")),
        str(range_state.get("start_at") or ""),
        str(range_state.get("end_at") or ""),
        filters.get("status", ""),
        filters.get("owner", ""),
        filters.get("solver", ""),
        filters.get("token", ""),
    ])


def _touch_section_lru(view_id: str, state: dict[str, Any], keep_section: str) -> None:
    mini_apps.touch_section_lru(
        view_id,
        state,
        keep_section,
        section_groups=SECTION_GROUPS,
        max_retained=MAX_RETAINED_SECTIONS,
    )


def _apply_section_load(
    ch: ClickHouseManager,
    view_id: str,
    request_id: int,
    section: str,
    environment_scope: str,
    chain_id: int,
    base_token: str,
    quote_token: str,
    interval: str,
    window_days: int,
    start_at: str,
    end_at: str,
    status: str,
    owner: str,
    solver: str,
    token: str,
    force_refresh: bool,
) -> tuple[MiniAppPayload, str]:
    """Apply a section scope: validate, evict stale data, load the CORE group.

    Non-core groups are deliberately NOT loaded here — the frontend fetches
    them afterwards through ``load_cow_explorer_datasets`` while skeletons
    show. A fingerprint match returns the retained datasets with zero queries.
    """
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    current = dict(record.view_state)
    if request_id < int(current.get("applied_request_id") or 0):
        return _payload_from_record(record), "Ignored stale CoW Explorer request."
    section_key = section.strip().lower()
    if section_key not in VALID_SECTIONS:
        raise ValueError(f"section must be one of {sorted(VALID_SECTIONS)}")
    scope = _validate_scope(environment_scope or str(current.get("environment_scope") or "production"))
    effective_chain_id = int(chain_id or 0)
    if not effective_chain_id and int(current.get("chain_id") or 0):
        current_chain = COW_CHAINS.get(int(current["chain_id"]))
        if current_chain and current_chain.environment == scope and section_key != "overview":
            effective_chain_id = current_chain.chain_id
    chain = _resolve_chain(scope, effective_chain_id, section_key)
    range_warnings: list[str] = []
    if effective_chain_id == 0 and chain is not None and section_key != "live":
        # The user asked for all networks but this section is single-chain;
        # a concrete chain was substituted — say so instead of silently lying.
        range_warnings.append("all_networks_unsupported")
    range_state = _range_state(section_key, int(window_days), start_at, end_at)
    # No window clamping: "All history" must return real full history (user
    # requirement). Memory safety comes from the query SHAPES instead —
    # top-N-first tapes, streaming uniq aggregates, no count() OVER () on
    # heavy specs — all proven live at window=0 across all networks.
    resolution_days = int(range_state.get("window_days") or 0)
    if range_state["kind"] == "absolute":
        start_dt = datetime.fromisoformat(str(range_state["start_at"]).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(range_state["end_at"]).replace("Z", "+00:00"))
        resolution_days = max(
            1,
            int(((end_dt - start_dt).total_seconds() + 86_399) // 86_400),
        )
    resolved_interval, interval_warnings = _resolve_interval(
        interval or str(current.get("interval") or "1h"),
        resolution_days,
    )
    current_pair = current.get("pair") or {}
    same_chain = chain is not None and int(current.get("chain_id") or 0) == chain.chain_id
    requested_base = ""
    requested_quote = ""
    if chain is not None and section_key in {"markets", "orders", "solvers"}:
        requested_base = base_token or (str(current_pair.get("base") or "") if same_chain else "")
        requested_quote = quote_token or (str(current_pair.get("quote") or "") if same_chain else "")
    filters = {
        "status": status.strip(),
        "owner": owner.strip(),
        "solver": solver.strip(),
        "token": token.strip(),
    }
    fingerprint = _section_fingerprint(
        section_key, scope, chain, requested_base, requested_quote,
        interval or str(current.get("interval") or "1h"), range_state, filters,
    )
    stored_fingerprints = dict(current.get("section_fingerprints") or {})
    core_loaded = bool((current.get("loaded_groups") or {}).get(f"{section_key}.core"))
    if (
        not force_refresh
        and stored_fingerprints.get(section_key) == fingerprint
        and core_loaded
    ):
        # Tab return with unchanged scope: retained datasets are still valid.
        next_state = {
            **current,
            "section": section_key,
            "selected_entity": None,
            "applied_request_id": int(request_id),
        }
        _touch_section_lru(view_id, next_state, section_key)
        mini_apps.set_view_state(view_id, next_state)
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return (
            _payload_from_record(updated),
            f"CoW Explorer {section_key} restored from retained datasets.",
        )
    pair = ("", "")
    if chain is not None and section_key in {"markets", "orders", "solvers"}:
        pair = _resolve_pair(ch, chain, requested_base, requested_quote)
    specs = _section_specs(
        section_key, scope, chain, pair, resolved_interval, range_state, filters
    )
    core_keys = SECTION_GROUPS[section_key]["core"]
    core_specs = [spec for spec in specs if spec.key in core_keys]
    datasets, coverage, load_warnings = _load_specs_safe(
        ch, core_specs, range_state, force_refresh=force_refresh
    )
    warnings = [*range_warnings, *interval_warnings, *load_warnings]
    if section_key in {"markets", "orders", "solvers"} and chain is not None and not all(pair):
        warnings.extend(["no_indexed_data", "No indexed token pair is available for this chain."])
    warnings = list(dict.fromkeys(warnings))
    scope_id = f"{scope}:{chain.chain_id if chain else 0}:{section_key}:{request_id}"
    core_failed = any(
        "query_failed" in (coverage.get(k, {}).get("warning_codes") or [])
        for k in core_keys
    )
    loaded_groups = dict(current.get("loaded_groups") or _empty_loaded_groups())
    for group in SECTION_GROUPS[section_key]:
        if group == "core":
            loaded_groups[f"{section_key}.{group}"] = "partial" if core_failed else True
        else:
            loaded_groups[f"{section_key}.{group}"] = False
    titles = dict(current.get("dataset_titles") or {})
    titles.update(_dataset_titles(specs))
    next_state = {
        **current,
        "section": section_key,
        "environment_scope": scope,
        "environment": chain.environment if chain else scope,
        "chain_id": chain.chain_id if chain else 0,
        "chain_name": chain.name if chain else "All networks",
        "chain_options": [_chain_dict(c) for c in _chains_for_scope(scope)],
        "explorer": asdict(chain.explorer) if chain else None,
        "pair": _pair_state(pair, chain, datasets),
        "interval": resolved_interval,
        "date_range": range_state,
        "filters": filters,
        "selected_entity": None,
        "breadcrumbs": [],
        "applied_request_id": int(request_id),
        # Scope changes always return the depth panel to the LIVE book; the
        # historical timestamp is a per-scope ephemeral, not a fingerprint key.
        "depth_at": "",
        # Depth-heatmap window resets to the default on every section apply.
        "heatmap_window": "7d",
        "scope_id": scope_id,
        "coverage": {**(current.get("coverage") or {}), **coverage},
        "coverage_warnings": [w for w in warnings if " " not in w],
        "warnings": warnings,
        "loaded_groups": loaded_groups,
        "dataset_titles": titles,
    }
    # Evict this section's previous datasets (scope changed), then attach the
    # fresh core bundle. Other retained sections stay untouched.
    previous_keys = list((current.get("section_datasets") or {}).get(section_key, []) or [])
    stale = [key for key in previous_keys if key not in datasets]
    if stale:
        mini_apps.remove_view_datasets(view_id, stale)
    for key, dataset in datasets.items():
        mini_apps.attach_dataset(view_id, key, dataset)
    section_datasets = dict(current.get("section_datasets") or {})
    section_datasets[section_key] = sorted(datasets)
    next_state["section_datasets"] = section_datasets
    fingerprints = dict(stored_fingerprints)
    fingerprints[section_key] = fingerprint
    next_state["section_fingerprints"] = fingerprints
    _touch_section_lru(view_id, next_state, section_key)
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    next_state["dataset_revisions"] = dict(updated.dataset_revisions)
    mini_apps.set_view_state(view_id, next_state)
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    return _payload_from_record(updated, titles), f"CoW Explorer {section_key} loaded."


def _apply_entity_load(
    ch: ClickHouseManager,
    view_id: str,
    request_id: int,
    entity_type: str,
    identifier: str,
    chain_id: int,
) -> tuple[MiniAppPayload, str]:
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    current = dict(record.view_state)
    if request_id < int(current.get("applied_request_id") or 0):
        return _payload_from_record(record), "Ignored stale CoW entity request."
    kind = entity_type.strip().lower()
    if kind not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    scope = _validate_scope(str(current.get("environment_scope") or "production"))
    effective_chain_id = int(chain_id or current.get("chain_id") or 0)
    if not effective_chain_id:
        raise ValueError("chain_id is required when loading an entity from all-network scope")
    chain = _resolve_chain(scope, effective_chain_id, "markets")
    assert chain is not None
    normalized_id = _normalize_hex(identifier) if kind != "auction" else identifier.strip()
    specs = _entity_specs(kind, normalized_id, chain)
    range_state = {
        "kind": "all",
        "anchor": "latest_indexed",
        "window_days": 0,
        "start_at": "",
        "end_at": "",
    }
    datasets, coverage, warnings = _load_specs_safe(
        ch, specs, range_state, force_refresh=False
    )
    scope_id = f"{scope}:{chain.chain_id}:entity:{request_id}"
    breadcrumb = {
        "label": f"{kind.title()} {normalized_id[:12]}",
        "entity_type": kind,
        "identifier": normalized_id,
        "chain_id": chain.chain_id,
    }
    breadcrumbs = list(current.get("breadcrumbs") or []) if current.get("section") == "entity" else []
    existing_index = next(
        (
            index for index, item in enumerate(breadcrumbs)
            if item.get("entity_type") == kind
            and item.get("identifier") == normalized_id
            and int(item.get("chain_id") or 0) == chain.chain_id
        ),
        None,
    )
    if existing_index is None:
        breadcrumbs.append(breadcrumb)
    else:
        breadcrumbs = breadcrumbs[:existing_index + 1]
    breadcrumbs = breadcrumbs[-8:]
    titles = dict(current.get("dataset_titles") or {})
    titles.update(_dataset_titles(specs))
    next_state = {
        **current,
        "section": "entity",
        "environment": chain.environment,
        "chain_id": chain.chain_id,
        "chain_name": chain.name,
        "explorer": asdict(chain.explorer),
        "selected_entity": {
            "entity_type": kind,
            "identifier": normalized_id,
            "chain_id": chain.chain_id,
            "chain_name": chain.name,
        },
        "breadcrumbs": breadcrumbs,
        "search": {"query": normalized_id, "candidates": []},
        "date_range": range_state,
        "applied_request_id": int(request_id),
        "scope_id": scope_id,
        "coverage": {**(current.get("coverage") or {}), **coverage},
        "coverage_warnings": [w for w in warnings if " " not in w],
        "warnings": warnings,
        "dataset_titles": titles,
    }
    # Entity bundles participate in the same per-section retention as tabs:
    # evict only the PREVIOUS entity's datasets, keep other sections cached.
    previous_keys = list((current.get("section_datasets") or {}).get("entity", []) or [])
    stale = [key for key in previous_keys if key not in datasets]
    if stale:
        mini_apps.remove_view_datasets(view_id, stale)
    for key, dataset in datasets.items():
        mini_apps.attach_dataset(view_id, key, dataset)
    section_datasets = dict(current.get("section_datasets") or {})
    section_datasets["entity"] = sorted(datasets)
    next_state["section_datasets"] = section_datasets
    fingerprints = dict(current.get("section_fingerprints") or {})
    fingerprints["entity"] = f"{kind}:{normalized_id}:{chain.chain_id}"
    next_state["section_fingerprints"] = fingerprints
    _touch_section_lru(view_id, next_state, "entity")
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    next_state["dataset_revisions"] = dict(updated.dataset_revisions)
    mini_apps.set_view_state(view_id, next_state)
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    return _payload_from_record(updated, titles), f"Loaded CoW {kind} detail."


def _apply_group_load(
    ch: ClickHouseManager,
    view_id: str,
    section: str,
    group: str,
    scope_id: str,
    force_refresh: bool,
    depth_at: str = "",
    heatmap_window: str = "",
) -> tuple[MiniAppPayload, str]:
    """Load ONE deferred dataset group additively and return a PATCH payload.

    Group loads never bump ``applied_request_id`` — they are additive and
    order-independent. The ``scope_id`` guard makes a late-arriving group load
    for a superseded scope a harmless no-op instead of a data corruption.
    """
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    state = dict(record.view_state)
    current_scope_id = str(state.get("scope_id") or "")
    if scope_id and scope_id != current_scope_id:
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE", view_id=view_id, app_id=COW_APP_ID,
            title=record.title, patch={}, warnings=["stale_scope"],
        )
        return payload, "Ignored stale CoW group request."
    section_key = section.strip().lower()
    groups = SECTION_GROUPS.get(section_key)
    if groups is None:
        raise ValueError(f"section must be one of {sorted(SECTION_GROUPS)}")
    group_key = group.strip().lower()
    if group_key not in groups:
        raise ValueError(
            f"group must be one of {sorted(groups)} for section {section_key}"
        )
    group_keys = groups[group_key]
    scope = _validate_scope(str(state.get("environment_scope") or "production"))
    state_chain_id = int(state.get("chain_id") or 0)
    chain = COW_CHAINS.get(state_chain_id) if state_chain_id else None
    if chain is None and section_key not in ALL_NETWORK_SECTIONS:
        raise ValueError("This section requires a selected chain")
    range_state = dict(state.get("date_range") or _range_state(section_key, -1, "", ""))
    pair_state = state.get("pair") or {}
    pair = (str(pair_state.get("base") or ""), str(pair_state.get("quote") or ""))
    interval = str(state.get("interval") or "1h")
    if interval not in CANDLE_BUCKETS:
        interval = "1h"
    filters = {
        key: str((state.get("filters") or {}).get(key) or "")
        for key in ("status", "owner", "solver", "token")
    }
    # depth_at contract: "" reuses the view's current value (so ordinary group
    # retries keep the historical book on screen), the literal "live" clears
    # it, and any other value is a validated ISO timestamp. Only the
    # markets.depth group consumes it.
    if depth_at == "":
        effective_depth_at = str(state.get("depth_at") or "")
    elif depth_at.strip().lower() == "live":
        effective_depth_at = ""
    else:
        effective_depth_at = _validate_depth_at(depth_at)
    # heatmap_window contract: "" reuses the view's current value (default "7d");
    # only the markets.depth_heatmap group consumes it.
    if heatmap_window == "":
        effective_heatmap_window = str(state.get("heatmap_window") or "7d")
    else:
        effective_heatmap_window = _validate_heatmap_window(heatmap_window)
    specs = _section_specs(
        section_key, scope, chain, pair, interval, range_state, filters,
        effective_depth_at, effective_heatmap_window,
    )
    group_specs = [spec for spec in specs if spec.key in group_keys]
    datasets, coverage, load_warnings = _load_specs_safe(
        ch, group_specs, range_state, force_refresh=force_refresh
    )
    for key, dataset in datasets.items():
        mini_apps.attach_dataset(view_id, key, dataset)
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    titles = dict(state.get("dataset_titles") or {})
    titles.update({spec.key: spec.title for spec in group_specs})
    tracked = sorted(
        set((state.get("section_datasets") or {}).get(section_key, []) or [])
        | set(datasets)
    )
    combined_warnings = list(dict.fromkeys([
        *(state.get("warnings") or []),
        *load_warnings,
    ]))
    # "partial" (truthy — no skeleton) marks a group where at least one
    # dataset failed: the frontend shows error cards + a retry affordance
    # and the group loader treats it as retryable.
    group_failed = any(
        "query_failed" in (coverage.get(k, {}).get("warning_codes") or [])
        for k in group_keys
    )
    patch: dict[str, Any] = {
        "loaded_groups": {f"{section_key}.{group_key}": "partial" if group_failed else True},
        "depth_at": effective_depth_at,
        "heatmap_window": effective_heatmap_window,
        "coverage": coverage,
        "dataset_revisions": {
            key: updated.dataset_revisions.get(key, 0) for key in datasets
        },
        "section_datasets": {section_key: tracked},
        "dataset_titles": {spec.key: spec.title for spec in group_specs},
        "warnings": combined_warnings,
        "coverage_warnings": [w for w in combined_warnings if " " not in w],
    }
    mini_apps.patch_view_state(view_id, patch)
    descriptors = {
        key: mini_apps.build_dataset_descriptor(
            key=key,
            dataset=dataset,
            title=titles.get(key, key.replace("_", " ").title()),
            scope_id=current_scope_id,
            provenance={"source": COW_DB, "coverage": coverage.get(key, {})},
        )
        for key, dataset in datasets.items()
    }
    payload = MiniAppPayload(
        type="PATCH_VIEW_STATE",
        view_id=view_id,
        app_id=COW_APP_ID,
        title=record.title,
        datasets=descriptors,
        patch=patch,
        warnings=load_warnings,
    )
    return payload, f"CoW Explorer {section_key}.{group_key} loaded."


def register_cow_explorer_tools(mcp, ch: ClickHouseManager) -> None:
    """Register the CoW Explorer resource, tools, and standalone web app."""
    mini_apps.register_app(COW_APP_ID, title=COW_TITLE, resource_uri=COW_URI)

    @mcp.resource(
        COW_URI,
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {
                "csp": {
                    "resourceDomains": [
                        "https://assets.coingecko.com",
                        "https://coin-images.coingecko.com",
                    ]
                }
            }
        },
    )
    def serve_cow_explorer_app() -> str:
        return get_cow_explorer_html()

    @mcp.tool(meta=COW_APP_META)
    def open_cow_explorer(
        environment_scope: str = "production",
        chain_id: int = 0,
        section: str = "overview",
        query: str = "",
        base_token: str = "",
        quote_token: str = "",
        interval: str = "",
        window_days: int = -1,
        start_at: str = "",
        end_at: str = "",
        entity_type: str = "",
        identifier: str = "",
    ) -> CallToolResult:
        """Open the read-only CoW Data Explorer over indexed ``cow_db`` data.

        Defaults to an all-production-network coverage overview. Use this for
        historical CoW fills/prices, observed order lifecycles and known open
        intents, settled competitions, solver analysis, or order/transaction/
        address/token/auction/solver drill-downs. The app discloses the indexed
        time window on every surface and does not claim a complete live book.
        """
        try:
            scope = _validate_scope(environment_scope)
            section_key = section.strip().lower() or "overview"
            if section_key not in VALID_SECTIONS:
                raise ValueError(f"section must be one of {sorted(VALID_SECTIONS)}")
            initial_chain = _resolve_chain(scope, int(chain_id), section_key)
            view_id = mini_apps.create_view(COW_APP_ID, COW_TITLE)
            state = _empty_state(scope, initial_chain, COW_TITLE, section_key)
            # Deep-link scope seeds: validated, stored, but NOT loaded here —
            # the frontend applies the section (core group) and then streams
            # the remaining groups. This keeps the open path free of any
            # ClickHouse round trip, which is what makes it fast.
            state["date_range"] = _range_state(section_key, int(window_days), start_at, end_at)
            requested_interval = interval.strip().lower()
            if requested_interval in CANDLE_BUCKETS:
                state["interval"] = requested_interval
            if base_token.strip() and quote_token.strip():
                state["pair"] = {
                    **state["pair"],
                    "base": _validate_token(base_token, "base_token"),
                    "quote": _validate_token(quote_token, "quote_token"),
                }
            mini_apps.set_view_state(view_id, state)
            if entity_type.strip() or identifier.strip():
                if not entity_type.strip() or not identifier.strip():
                    raise ValueError("entity_type and identifier must be provided together")
                payload, summary = _apply_entity_load(
                    ch, view_id, 0, entity_type, identifier, int(chain_id)
                )
            elif query.strip():
                candidates = _search_candidates(ch, query, scope, int(chain_id))
                if len(candidates) == 1:
                    candidate = candidates[0]
                    payload, summary = _apply_entity_load(
                        ch, view_id, 0, candidate["entity_type"],
                        candidate["identifier"], candidate["chain_id"]
                    )
                else:
                    record = mini_apps.get_view(view_id)
                    assert record is not None
                    state = {
                        **record.view_state,
                        "search": {"query": query.strip(), "candidates": candidates},
                        "warnings": ([] if candidates else ["no_indexed_data"]),
                    }
                    mini_apps.set_view_state(view_id, state)
                    record = mini_apps.get_view(view_id)
                    assert record is not None
                    payload = _payload_from_record(record)
                    summary = f"CoW search returned {len(candidates)} candidate(s)."
            else:
                record = mini_apps.get_view(view_id)
                assert record is not None
                payload = _payload_from_record(record)
                summary = (
                    f"CoW Explorer opened on {section_key} — datasets load in "
                    "the app (deferred groups)."
                )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_cow_explorer_section(
        view_id: str,
        request_id: int,
        section: str,
        environment_scope: str = "",
        chain_id: int = 0,
        base_token: str = "",
        quote_token: str = "",
        interval: str = "",
        window_days: int = -1,
        start_at: str = "",
        end_at: str = "",
        status: str = "",
        owner: str = "",
        solver: str = "",
        token: str = "",
        force_refresh: bool = False,
    ) -> CallToolResult:
        """[App-only] Atomically load one CoW Explorer section."""
        try:
            payload, summary = _apply_section_load(
                ch, view_id, request_id, section, environment_scope, chain_id,
                base_token, quote_token, interval, window_days, start_at, end_at,
                status, owner, solver, token, force_refresh,
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def search_cow_explorer(
        view_id: str,
        request_id: int,
        query: str,
        chain_id: int = 0,
    ) -> CallToolResult:
        """[App-only] Resolve a CoW order, transaction, address, auction, or token."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")
        if not query.strip():
            return mini_apps.error_call_tool_result("query is required")
        try:
            if request_id < int(record.view_state.get("applied_request_id") or 0):
                return mini_apps.payload_to_call_tool_result(
                    _payload_from_record(record), "Ignored stale CoW search request."
                )
            scope = _validate_scope(str(record.view_state.get("environment_scope") or "production"))
            candidates = _search_candidates(ch, query, scope, int(chain_id))
            if len(candidates) == 1:
                candidate = candidates[0]
                payload, summary = _apply_entity_load(
                    ch, view_id, request_id, candidate["entity_type"],
                    candidate["identifier"], candidate["chain_id"]
                )
                return mini_apps.payload_to_call_tool_result(payload, summary)
            patch = {
                "search": {"query": query.strip(), "candidates": candidates},
                "applied_request_id": int(request_id),
            }
            mini_apps.patch_view_state(view_id, patch)
            payload = MiniAppPayload(
                type="PATCH_VIEW_STATE", view_id=view_id, app_id=COW_APP_ID,
                title=record.title, patch=patch,
                warnings=[] if candidates else ["no_indexed_data"],
            )
            return mini_apps.payload_to_call_tool_result(
                payload, f"CoW search returned {len(candidates)} candidate(s)."
            )
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_cow_entity(
        view_id: str,
        request_id: int,
        entity_type: str,
        identifier: str,
        chain_id: int = 0,
    ) -> CallToolResult:
        """[App-only] Load a resolved CoW entity bundle."""
        try:
            payload, summary = _apply_entity_load(
                ch, view_id, request_id, entity_type, identifier, chain_id
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_cow_explorer_datasets(
        view_id: str,
        request_id: int,
        section: str,
        group: str,
        scope_id: str = "",
        force_refresh: bool = False,
        depth_at: str = "",
        heatmap_window: str = "",
    ) -> CallToolResult:
        """[App-only] Load one deferred CoW dataset group (additive).

        ``depth_at``: "" keeps the view's current depth timestamp, "live"
        returns the markets depth panel to the live book, and an ISO-8601
        timestamp reconstructs the pair's open book at that moment.

        ``heatmap_window``: "" keeps the view's current depth-heatmap window
        (default "7d"); "24h"/"7d"/"all" pick the span the markets.depth_heatmap
        group grids over.
        """
        try:
            payload, summary = _apply_group_load(
                ch, view_id, section, group, scope_id, force_refresh, depth_at,
                heatmap_window,
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_cow_icon_overlay(
        view_id: str,
        request_id: int = 0,
    ) -> CallToolResult:
        """[App-only] Resolve CoinGecko icons for tokens visible in the view.

        Never blocks on the network: returns whatever is cached and kicks off
        background fetches for missing chains; ``icon_overlay_pending`` in the
        warnings tells the frontend one retry (a few seconds later) will find
        more icons.
        """
        try:
            record = mini_apps.get_view(view_id)
            if record is None:
                return mini_apps.error_call_tool_result(
                    f"Unknown or expired view_id: {view_id}"
                )
            overlay, pending = _build_icon_overlay(record.datasets)
            patch = {"icon_overlay": overlay}
            mini_apps.patch_view_state(view_id, patch)
            payload = MiniAppPayload(
                type="PATCH_VIEW_STATE", view_id=view_id, app_id=COW_APP_ID,
                title=record.title, patch=patch,
                warnings=(["icon_overlay_pending"] if pending else []),
            )
            chains = len(overlay)
            icons = sum(len(v) for v in overlay.values())
            return mini_apps.payload_to_call_tool_result(
                payload,
                f"Icon overlay: {icons} icon(s) across {chains} chain(s)"
                + (" — more pending." if pending else "."),
            )
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    for name in (
        "load_cow_explorer_section", "search_cow_explorer", "load_cow_entity",
        "load_cow_explorer_datasets", "load_cow_icon_overlay",
    ):
        mini_apps.mark_app_only(name)

    web_apps.register_web_app(
        app_id=COW_APP_ID,
        open_tool="open_cow_explorer",
        html_loader=get_cow_explorer_html,
        title=COW_TITLE,
        description=(
            "Explore indexed CoW fills, execution and reference prices, known "
            "open intents, auctions, solver competitions, and entity history."
        ),
        icon="◒",
        diagnostics_loader=get_cow_explorer_diagnostics,
        tools={
            "open_cow_explorer": open_cow_explorer,
            "load_cow_explorer_section": load_cow_explorer_section,
            "search_cow_explorer": search_cow_explorer,
            "load_cow_entity": load_cow_entity,
            "load_cow_explorer_datasets": load_cow_explorer_datasets,
            "load_cow_icon_overlay": load_cow_icon_overlay,
        },
    )


__all__ = [
    "COW_APP_ID", "COW_TITLE", "COW_URI", "COW_CHAINS", "NATIVE_TOKEN",
    "get_cow_explorer_html", "get_cow_explorer_diagnostics",
    "register_cow_explorer_tools", "_search_candidates",
]
