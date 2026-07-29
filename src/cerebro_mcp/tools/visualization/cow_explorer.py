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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from mcp.types import CallToolResult

from cerebro_mcp.chains import CHAINS, NATIVE_ICON_URLS, ChainInfo, ExplorerInfo
from cerebro_mcp.clients.clickhouse import (
    INTERACTIVE_QUERY_BUDGET,
    ClickHouseManager,
)
from cerebro_mcp.models.mini_app import MiniAppPayload, SummaryCard
from cerebro_mcp.runtime.mini_app_cache import CachedDataset, FailureCache
from cerebro_mcp.tools.visualization import coingecko, mini_apps, sql_loader, web_apps

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
#: CoinGecko access moved to ``visualization.coingecko`` when the governance
#: Treasury tab needed the same address-keyed lookups. These aliases keep the
#: original names — they are referenced throughout this module and mirrored
#: client-side — while the implementation lives in one place.
COINGECKO_PLATFORM_IDS = coingecko.PLATFORM_IDS
COINGECKO_TOKEN_LIST_URL = coingecko.TOKEN_LIST_URL
#: Alias of the shared registry map — kept under the original name because it
#: is referenced throughout this module and mirrored client-side.
COINGECKO_NATIVE_ICON_URLS = NATIVE_ICON_URLS
COINGECKO_ICON_CACHE_TTL_SECONDS = coingecko.ICON_CACHE_TTL_SECONDS
_TOKEN_COLUMN_RE = re.compile(r"^(?:token|token[01]|(?:base|quote|sell|buy|fee)_token)$")
ORDER_UID_RE = re.compile(r"^0x[0-9a-f]{112}$")
HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
INTEGER_RE = re.compile(r"^[0-9]+$")


#: Chains CoW Protocol is deployed on, in deliberate priority order — NOT
#: numeric. The order is observable: it drives the mini-app's chain dropdown
#: and the `chain_id IN (...)` literals built by `_scope_predicate` /
#: `_search_scope`, so reordering churns generated SQL (and the ClickHouse
#: query cache). Membership is asserted by tests/test_cow_explorer.py.
#: Chains present in the shared registry but NOT here (e.g. Celo) are
#: deliberately excluded — CoW does not settle there.
COW_CHAIN_IDS: tuple[int, ...] = (
    1, 100, 42161, 8453, 56, 137, 43114, 59144, 57073, 9745, 11155111,
)

COW_CHAINS: dict[int, ChainInfo] = {cid: CHAINS[cid] for cid in COW_CHAIN_IDS}


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


def _dataset_token_addresses(
    datasets: dict[str, CachedDataset],
    cap_per_chain: int = 500,
) -> dict[int, set[str]]:
    """Collect distinct token addresses per chain from attached datasets.

    Thin binding of the shared collector to CoW's own column vocabulary —
    ``_TOKEN_COLUMN_RE`` and ``NATIVE_TOKEN`` are this app's schema, not
    CoinGecko's, so they stay here.
    """
    return coingecko.dataset_token_addresses(
        datasets,
        token_columns=_TOKEN_COLUMN_RE,
        native_token=NATIVE_TOKEN,
        cap_per_chain=cap_per_chain,
    )


def _build_icon_overlay(
    datasets: dict[str, CachedDataset],
) -> tuple[dict[str, dict[str, str]], bool]:
    """Resolve icon URLs for every token visible in the attached datasets.

    Returns ``(overlay, pending)`` where overlay is ``{chain_id: {token: url}}``
    and ``pending`` means at least one chain's CoinGecko list is still being
    fetched in the background (the frontend retries once shortly after).
    """
    return coingecko.build_icon_overlay(
        datasets,
        token_columns=_TOKEN_COLUMN_RE,
        native_token=NATIVE_TOKEN,
        native_icon_urls=COINGECKO_NATIVE_ICON_URLS,
    )


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
    return sql_loader.load_sql("cow", "_cte_token_metadata")


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
    return sql_loader.load_sql("cow", "_cte_token_metadata_multichain", ids=ids, native_token=NATIVE_TOKEN, native_tuples=native_tuples)


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
    return sql_loader.load_sql("cow", "_cte_checkpoints", ids=ids)


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
    order_anchor_cte = sql_loader.load_sql("cow", "order_anchor_cte", ids=ids)

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

    competitions_cte = sql_loader.load_sql("cow", "competitions_cte", ids=ids)
    # Grouped single-pass shape: one trades scan and one orders scan, each
    # GROUP BY chain_id, joined onto an arrayJoin chain spine — replaces the
    # per-chain cross-join arms (10x the scans AND over the SQL length cap).
    # orders: argMax dedup grouped on the sort-key prefix streams, replacing
    # FINAL, whose k-way merge was the memory-heavy part of the all-network
    # summary; status/creation_date are latest-version exact.
    trades_cte = sql_loader.load_sql("cow", "trades_cte", trade_key=TRADE_KEY, ids=ids, arm_window=_arm_window(0, range_state))
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
    orders_cte = sql_loader.load_sql("cow", "orders_cte", ids=ids, order_window=order_window(0))
    network_summary = (
        sql_loader.load_sql("cow", "network_summary", shared_ctes=shared_ctes, order_anchor_cte=order_anchor_cte, competitions_cte=competitions_cte, trades_cte=trades_cte, orders_cte=orders_cte, ids=ids)
    )
    coverage = sql_loader.load_sql("cow", "coverage", scope_pred=scope_pred, ids=ids)
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
        activity_parts.append(sql_loader.load_sql("cow", "network_activity_arm", cid=cid, trade_key=TRADE_KEY, base_where=base_where))
        pair_parts.append(sql_loader.load_sql("cow", "top_pairs_arm", cid=cid, trade_key=TRADE_KEY, base_where=base_where))
        # Fees stand alone on protocol_fees (small, API-enriched): joining the
        # trades view only supplied block timestamps and was the memory/time
        # hog; observed_at is the honest basis for API-sourced fee rows.
        fee_window = per_chain_time(
            "f.observed_at",
            "(SELECT max(observed_at) FROM cow_db.protocol_fees "
            f"WHERE environment={{env:String}} AND chain_id={cid})",
        )
        fee_parts.append(sql_loader.load_sql("cow", "fee_policy_arm", cid=cid, fee_window=fee_window))
    activity = (
        f"WITH {shared_ctes}\n"
        "SELECT * FROM (\n" + "\nUNION ALL\n".join(activity_parts)
        + "\n) ORDER BY bucket,chain_id"
    )
    pair_union = "\nUNION ALL\n".join(pair_parts)
    top_pairs = sql_loader.load_sql("cow", "top_pairs", shared_ctes=shared_ctes, tmx=tmx, pair_union=pair_union)
    fee_union = "\nUNION ALL\n".join(fee_parts)
    fees = sql_loader.load_sql("cow", "fees", tmx=tmx, fee_union=fee_union)
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
        np_cte = sql_loader.load_sql("cow", "np_cte", ids=ids)
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
    kpi_select = sql_loader.load_sql("cow", "kpi_select", trade_key=TRADE_KEY, vol_expr=vol_expr, np_join=np_join, kpi_where=kpi_where)
    protocol_kpis = sql_loader.load_sql("cow", "protocol_kpis", shared_ctes=shared_ctes, np_cte=np_cte, kpi_body=kpi_select.replace("SELECT ", "", 1))
    # All-time totals feed the distribution pies. Deliberately ignores the
    # global window (always all indexed history — disclosed) and deliberately
    # KEEPS NULL-timestamp rows (BNB) in the counts: an all-time count needs
    # no time axis, and excluding BNB here would silently understate it.
    alltime_totals = sql_loader.load_sql("cow", "alltime_totals", shared_ctes=shared_ctes, trade_key=TRADE_KEY, ids=ids)
    # Share-over-time: ONE grouped scan (bucket x chain hash stays tiny even
    # at all-history — weeks x 10 chains), NOT ten UNION arms. The frontend
    # normalizes per bucket for the 100%-share view.
    share_bucket = (
        "toStartOfWeek(t.block_timestamp)"
        if range_state["kind"] == "all"
        or int(range_state.get("window_days") or 0) > 180
        else "toStartOfDay(t.block_timestamp)"
    )
    chain_share_trend = sql_loader.load_sql("cow", "chain_share_trend", shared_ctes=shared_ctes, share_bucket=share_bucket, trade_key=TRADE_KEY, ids=ids, arm_window=_arm_window(0, range_state))
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
    recent_sql = sql_loader.load_sql("cow", "pair_discovery_recent")
    fallback_sql = sql_loader.load_sql("cow", "pair_discovery_alltime")
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
    bucket_seconds: int = 0,
) -> list[QuerySpec]:
    base, quote = pair
    # Pair picker options: the 50 busiest pairs of the last 30 days with
    # symbols AND addresses — feeds the base/quote dropdowns so users are not
    # typing raw token addresses. Cheap streaming aggregate; exists even when
    # no pair could be resolved so the picker can still offer choices.
    options_params = _scope_parameters(chain.environment, chain)
    pair_options = sql_loader.load_sql("cow", "pair_options", token_metadata_cte=_token_metadata_cte())
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
    market_summary = sql_loader.load_sql("cow", "market_summary", token_metadata_cte=token_cte, scope_pred=_scope_predicate(chain, 't'), pair_filter=pair_filter, time_pred=time_pred)
    bucket = CANDLE_BUCKETS[interval]
    # The dedup subquery matters for VOLUME correctness: recent fills sit in
    # unmerged ReplacingMergeTree parts (and API+RPC dual-source rows), so a
    # raw read double-counts sums. Pair-filtered sets are small enough that
    # the argMax GROUP BY streams cheaply.
    candles = sql_loader.load_sql("cow", "candles", token_metadata_cte=token_cte, scope_pred=_scope_predicate(chain, 't'), pair_filter=pair_filter, time_pred=time_pred, bucket=bucket)
    # Top-N-first tape (see _trade_specs): a plain ORDER BY … LIMIT over the
    # base table is a bounded heap sort, memory-safe at any window; dedup
    # happens over the selected set only, and metadata joins only the capped
    # rows. The checkpoint CTE replaces the canonical view's chain_blocks join.
    recent = sql_loader.load_sql("cow", "recent", token_metadata_cte=token_cte, scope_pred=_scope_predicate(chain, 't'), pair_filter=pair_filter, time_pred=time_pred, tape_arm_limit=TAPE_ARM_LIMIT, row_cap=ROW_CAP)
    # Window anchor without a chain_blocks-FINAL triple join: max block time
    # over the (few thousand) competition auction blocks, index-looked-up.
    auction_anchor = sql_loader.load_sql("cow", "auction_block_anchor")
    auction_time, _ = _time_predicate(
        "blocks.auction_timestamp", range_state, auction_anchor
    )
    auction_reference = sql_loader.load_sql("cow", "auction_reference", token_metadata_cte=token_cte, auction_time=auction_time)
    native_reference = _native_reference_sql(chain, base, quote, range_state)
    return [
        pair_options_spec,
        QuerySpec("market_summary", "Market summary", market_summary, params, "block_timestamp", "checkpoint_bounded"),
        QuerySpec("price_candles", "Execution prices (settled fills)", candles, params, "block_timestamp", "checkpoint_bounded"),
        QuerySpec("recent_market_trades", "Recent settled fills", recent, params, "block_timestamp", "checkpoint_bounded", 60, exact_count=False),
        QuerySpec("auction_reference_prices", "Auction reference prices", auction_reference, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("native_reference_prices", "Native-price API observations", native_reference, params, "observed_at", "observed_series", 60),
        *_pair_depth_specs(chain, pair, depth_at),
        *_pair_depth_heatmap_specs(chain, pair, heatmap_window, bucket_seconds),
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
    Post-backfill this reaches ~2021-08 (creation_date floor, surfaced by
    depth_horizon.earliest_creation_seen); cancelled orders WITHOUT any
    timestamped cancel event (the backfill cancel-time gap) are excluded at
    every T — their resting span is unknowable (see term_any).
    Live-verified 2026-07-23 (0.09s at T-1d on the busiest BNB pair; the cand
    CTE must NOT self-alias argMax to filtered column names — code 184).
    """
    # uniq, not uniqExact: an exact hash set over millions of backfilled
    # 56-byte uids is a memory risk; the count is display-only (HLL ~0.8%).
    horizon = sql_loader.load_sql("cow", "depth_horizon")
    # Pairs that HAVE a standing book right now (chain-scoped, pair-agnostic).
    # Some chains (Gnosis) run almost entirely on short-lived market orders and
    # hold ZERO open intents at any given moment — without this list the depth
    # panel dead-ends on an empty book with no path to data. The backfilled
    # orders table is ~millions of rows per chain, so the unexpired-validity
    # prefilter (valid_to is IMMUTABLE per order_uid) bounds the argMax hash to
    # the small live set; valid_to joins the GROUP BY key rather than being
    # argMax'd so the same-level WHERE binds the raw column (alias-in-WHERE
    # trap, code 184). The mutable status filter sits a level ABOVE the argMax.
    open_pairs = sql_loader.load_sql("cow", "open_intent_pairs", token_cte=_token_metadata_cte())
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
    ladder_projection = sql_loader.load_sql("cow", "ladder_projection")
    if not depth_at:
        server_as_of = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        live_params = {
            **_scope_parameters(chain.environment, chain),
            "base": base,
            "quote": quote,
            "server_as_of": server_as_of.isoformat().replace("+00:00", "Z"),
        }
        live_sql = sql_loader.load_sql("cow", "pair_depth", token_cte=token_cte, ladder_projection=ladder_projection)
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
    hist_sql = sql_loader.load_sql("cow", "pair_depth_at", token_cte=token_cte, ladder_projection=ladder_projection)
    specs.append(QuerySpec(
        "pair_depth", "Order-book depth (reconstructed)", hist_sql,
        hist_params, "creation_date", "reconstructed_point_in_time", 3600,
    ))
    return specs


#: Time spans the depth footprint can grid over. "all" spans the whole
#: reconstructable history (min(creation_date), reaching the backfill).
_HEATMAP_WINDOWS = ("24h", "7d", "30d", "90d", "all")

#: Footprint price binning: rows carry price RELATIVE to the bucket's own
#: median (percent), binned to `_FOOTPRINT_REL_STEP` and clamped to
#: +-`_FOOTPRINT_REL_PCT`. A relative reference is what makes long windows
#: usable: the underlying price trends by multiples over years while a book
#: spans single-digit percent of it, so an absolute grid leaves the liquidity
#: in 1-2 rows of the plot.
#:
#: It is also a correctness fix. Retention measured over all 254,525 mainnet
#: USDC/WETH orders, worst case ("all", ~32-day buckets):
#:     +-30% of the WINDOW median (the old clamp)  53.3%
#:     +-10% of the BUCKET median                  78.3%
#:     +-20% of the BUCKET median  (chosen)        92.5%
#:     +-30% of the BUCKET median                  95.8%
#: Short windows are unaffected either way — 7d buckets are 2.8h wide, so the
#: whole book lands inside +-9% (live-probed).
#:
#: Width and step are chosen together against the row budget: 41 bins leaves
#: room for the full 120 time buckets (41 x 2 x 120 = 9,840 < 10k), and a 1.0
#: point bin over +-20% is EXACTLY the client's 40 display levels — the server
#: grid and the client grid coincide, so nothing is re-binned or aliased.
_FOOTPRINT_REL_PCT = 20.0
_FOOTPRINT_REL_STEP = 1.0
_FOOTPRINT_MAX_BUCKETS = 120
#: Finest bucket the grid will cut, and the coarsest a caller may request.
_FOOTPRINT_MIN_STEP_S = 300
_FOOTPRINT_MAX_STEP_S = 2_592_000


def _validate_heatmap_window(value: str) -> str:
    """Normalize the depth-footprint window to one of ``_HEATMAP_WINDOWS``."""
    window = (value or "").strip().lower()
    if window not in _HEATMAP_WINDOWS:
        raise ValueError(f"heatmap_window must be one of {list(_HEATMAP_WINDOWS)}")
    return window


def _validate_bucket_seconds(value: int | str) -> int:
    """Normalize the requested footprint resolution to whole seconds.

    0 means "auto" (the server picks span/60). Anything else is clamped by
    the SQL itself to >= ``_FOOTPRINT_MIN_STEP_S`` and coarsened further when
    the span would otherwise exceed ``_FOOTPRINT_MAX_BUCKETS`` columns — the
    row budget is a hard cap, so a too-fine request is honored as far as it
    fits rather than rejected. The response echoes the effective resolution
    in ``bucket_seconds`` so the UI can disclose the coarsening.
    """
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("bucket_seconds must be an integer number of seconds") from exc
    if seconds == 0:
        return 0
    if seconds < _FOOTPRINT_MIN_STEP_S or seconds > _FOOTPRINT_MAX_STEP_S:
        raise ValueError(
            f"bucket_seconds must be 0 (auto) or between {_FOOTPRINT_MIN_STEP_S} "
            f"and {_FOOTPRINT_MAX_STEP_S} seconds"
        )
    return seconds


def _pair_depth_heatmap_specs(
    chain: ChainInfo,
    pair: tuple[str, str],
    window: str = "7d",
    bucket_seconds: int = 0,
) -> list[QuerySpec]:
    """Depth-over-time FOOTPRINT source for one pair (one binned tier).

    Reconstructs the *shape* of the book across a grid of time buckets in ONE
    query — the ``depth`` group's ``hist_sql`` returns a single instant, this
    returns many. An order rests in a bucket when its span
    [created, alive_until) overlaps the bucket, where ``alive_until`` is the
    earliest of expiry, a timestamped terminal event, and its completing fill.

    Shape (one tier for EVERY window — the client bins to price levels either
    way, so per-order rows were only ever wasted bandwidth):

    - One row per (bucket, relative-price bin, side). ``depth_base`` is
      TIME-WEIGHTED — an order resting a third of a bucket contributes a third
      of its size — so a transient intent never reads like a standing one.
    - Price is carried RELATIVE to the bucket's own median (``rel_pct``,
      0.5-point bins, clamped to +-10%) with the reference itself emitted as
      ``bucket_mid``, so the client can render either axis mode from one
      payload (``abs_price = bucket_mid * (1 + rel_pct / 100)``). A per-bucket
      reference is both a readability fix (a book spans ~1.6% of price while a
      multi-year window spans multiples of it) and a correctness one: the old
      +-30%-of-WINDOW-median clamp dropped 44.3% of mainnet USDC/WETH orders.
    - Rows are bounded at ``_FOOTPRINT_MAX_BUCKETS`` x 41 bins x 2 sides.
    - ``orders`` counts the resting orders behind each cell (tooltip honesty:
      a cell can be two orders or two thousand).

    Resolution is caller-chosen: ``bucket_seconds`` 0 means auto (span/60),
    anything else is floored at ``_FOOTPRINT_MIN_STEP_S`` and coarsened until
    the grid fits the bucket cap. The effective value comes back as the
    ``bucket_seconds`` column so the UI can disclose a coarsening.

    Cancelled orders WITHOUT a timestamped cancel event (the backfilled
    cancel-time gap — the API only reports current status) are EXCLUDED: their
    resting span is unknowable and including them until valid_to painted
    phantom depth (median 7d, p95 ~1y validity). Live-captured cancels carry
    order_events timestamps and are unaffected.
    """
    base, quote = pair
    if not base or not quote:
        return []
    win = _validate_heatmap_window(window)
    step_request = _validate_bucket_seconds(bucket_seconds)
    token_cte = _token_metadata_cte()
    params = {
        **_scope_parameters(chain.environment, chain),
        "base": base,
        "quote": quote,
        "window": win,
        "bucket_seconds": step_request,
    }
    # Grid derivation is split across CTEs so each expression references only a
    # PRIOR CTE alias (ClickHouse same-SELECT sibling-alias refs are fragile).
    heatmap_sql = sql_loader.load_sql("cow", "pair_depth_heatmap", token_cte=token_cte, min_step_label=f"{_FOOTPRINT_MIN_STEP_S}s", min_step_s=_FOOTPRINT_MIN_STEP_S, max_buckets=_FOOTPRINT_MAX_BUCKETS, max_buckets_f=float(_FOOTPRINT_MAX_BUCKETS), rel_bin_scale=100.0 / _FOOTPRINT_REL_STEP, rel_bin_step=1.0 / _FOOTPRINT_REL_STEP, rel_clamp=_FOOTPRINT_REL_PCT / 100.0)
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
        native_time = sql_loader.load_sql("cow", "_pred_native_price_window")
    if base == NATIVE_TOKEN:
        return sql_loader.load_sql("cow", "native_reference_inverted", token_cte=token_cte, decimal_factor=decimal_factor, native_time=native_time)
    if quote == NATIVE_TOKEN:
        return sql_loader.load_sql("cow", "native_reference_direct", token_cte=token_cte, decimal_factor=decimal_factor, native_time=native_time)
    return sql_loader.load_sql("cow", "native_reference_cross", token_cte=token_cte, native_time=native_time, decimal_factor=decimal_factor)


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
        activity_parts.append(sql_loader.load_sql("cow", "trade_activity_arm", cid=cid, trade_key=TRADE_KEY, arm_where=arm_where))
        pair_parts.append(sql_loader.load_sql("cow", "trade_pair_arm", cid=cid, trade_key=TRADE_KEY, arm_where=arm_where))
    activity = (
        f"WITH {shared_ctes}\n"
        "SELECT * FROM (\n" + "\nUNION ALL\n".join(activity_parts)
        + "\n) ORDER BY bucket,chain_id"
    )
    pair_union = "\nUNION ALL\n".join(pair_parts)
    breakdown = sql_loader.load_sql("cow", "trade_pair_breakdown", shared_ctes=shared_ctes, tmx=tmx, pair_union=pair_union)
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
    deduped_tape = sql_loader.load_sql("cow", "deduped_tape", ids=ids, time_window=time_window, extra=extra, tape_arm_limit=TAPE_ARM_LIMIT, row_cap=ROW_CAP)
    trades = sql_loader.load_sql("cow", "trade_tape", shared_ctes=shared_ctes, tmx=tmx, deduped_tape=deduped_tape)
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
    return sql_loader.load_sql("cow", "_cte_owner_months", ids=ids, months=months)


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
    firsts_cte = sql_loader.load_sql("cow", "firsts_cte", ids=ids, correlation_window_days=CORRELATION_MAX_WINDOW_DAYS)
    leader_parts: list[str] = []
    activity_parts: list[str] = []
    for c in chains:
        cid = c.chain_id
        arm_where = (
            f"t.environment={{env:String}} AND t.chain_id={cid} "
            f"AND {_arm_checkpoint(cid)} "
            f"AND t.block_timestamp IS NOT NULL AND {_arm_window(cid, range_state)}"
        )
        leader_parts.append(sql_loader.load_sql("cow", "trader_leaderboard_arm", cid=cid, trade_key=TRADE_KEY, arm_where=arm_where))
        activity_parts.append(sql_loader.load_sql("cow", "trader_activity_arm", cid=cid, trade_key=TRADE_KEY, arm_where=arm_where))
    leader_union = "\nUNION ALL\n".join(leader_parts)
    leaderboard = sql_loader.load_sql("cow", "trader_leaderboard", shared_ctes=shared_ctes, leader_union=leader_union)
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
    dynamics = sql_loader.load_sql("cow", "trader_dynamics", shared_ctes=shared_ctes, dynamics_ctes=dynamics_ctes)
    retention = sql_loader.load_sql("cow", "trader_retention", shared_ctes=shared_ctes, dynamics_ctes=dynamics_ctes, dynamics_months=TRADER_DYNAMICS_MONTHS)
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
    oa_cte = sql_loader.load_sql("cow", "order_multi_anchor_cte", ids=ids)
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
    o_dedup = sql_loader.load_sql("cow", "order_multi_dedup", ids=ids, window=window)
    summary = sql_loader.load_sql("cow", "order_multi_status_summary", oa_cte=oa_cte, o_dedup=o_dedup, outer=outer)
    activity = sql_loader.load_sql("cow", "order_multi_activity", oa_cte=oa_cte, o_dedup=o_dedup, outer=outer)
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

    The historical backfill grew `orders` to ~12M rows (order_events grew
    similarly), so every dedup below bounds its argMax hash by the WINDOW
    (immutable creation_date pushed into the raw scan) or groups the raw scan
    directly — never a whole-table dedup. Coverage caveat baked into the docs:
    the orderbook capture recovers EXECUTED and owner-enumerated orders, so
    class mixes describe the observed subset, never all CoW orders.
    """
    ids = ",".join(str(c.chain_id) for c in chains)
    params = {**_scope_parameters(scope, None), **_time_params(range_state)}
    oa_cte = sql_loader.load_sql("cow", "order_type_anchor_cte", ids=ids)

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
    o_dedup = sql_loader.load_sql("cow", "order_type_dedup_cte", ids=ids, order_window=order_window())
    type_summary = sql_loader.load_sql("cow", "type_summary", oa_cte=oa_cte, o_dedup=o_dedup)
    flavor_mix = sql_loader.load_sql("cow", "flavor_mix", oa_cte=oa_cte, o_dedup=o_dedup)
    type_trend = sql_loader.load_sql("cow", "type_trend", oa_cte=oa_cte, o_dedup=o_dedup)
    # ComposableCoW / programmatic footprint. event_timestamp is Nullable —
    # bucket on coalesce(event_timestamp, observed_at), disclosed in docs.
    oea_cte = sql_loader.load_sql("cow", "order_type_events_cte", ids=ids)
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
    conditional_activity = sql_loader.load_sql("cow", "conditional_activity", oea_cte=oea_cte, event_ts=event_ts, ids=ids, event_window=event_window)
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
    appdata_classes = sql_loader.load_sql("cow", "appdata_classes", ids=ids)
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
    summary = sql_loader.load_sql("cow", "order_status_summary", where=where)
    activity = sql_loader.load_sql("cow", "order_activity", where=where)
    surplus = SURPLUS_BPS.format(
        eb="t.buy_amount", ls="o.sell_amount", es="t.sell_amount", lb="o.buy_amount",
    )
    # Streaming shape: base trades (checkpoint-bounded, dedup-free — <0.1%
    # duplicate fills, disclosed) hash-joined against the SMALL deduped orders
    # set. The previous trades_canonical FINAL x orders FINAL double-merge was
    # the memory-heavy part; the argMax subquery streams on the sort key.
    quality_join = sql_loader.load_sql("cow", "order_quality_join")
    # Cap the window: the three quality distributions load CONCURRENTLY and
    # each streams the full trades partition through per-day quantile state at
    # kind='all' — three unbounded aggregations at once is a concurrent-OOM
    # contributor. Same 90d analytical cap the correlation views use.
    quality_range, _ = _capped_analytical_range(range_state)
    quality_time, quality_params = _time_predicate(
        "t.block_timestamp", quality_range, _trade_anchor(chain)
    )
    quality_source = sql_loader.load_sql("cow", "order_quality_source", surplus=surplus, quality_join=quality_join, quality_time=quality_time)
    quality_summary = sql_loader.load_sql("cow", "order_quality_summary", quality_source=quality_source)
    latency_distribution = sql_loader.load_sql("cow", "fill_latency_distribution", quality_source=quality_source)
    surplus_distribution = sql_loader.load_sql("cow", "surplus_distribution", quality_source=quality_source)
    quality_full_params = {**params, **quality_params}
    # Surplus by order class: the quality_join shape with class carried
    # through the deduped orders build. Same 90d analytical cap; sole member
    # of its own load group so it never stacks with the quality trio.
    class_source = sql_loader.load_sql("cow", "order_class_source", surplus=surplus, quality_time=quality_time)
    surplus_by_class = sql_loader.load_sql("cow", "surplus_by_class", class_source=class_source)
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
    remaining_cte = sql_loader.load_sql("cow", "order_remaining_cte", token_cte=token_cte, owner_predicate=owner_predicate)
    known_orders = sql_loader.load_sql("cow", "known_orders", remaining_cte=remaining_cte)
    intent_summary = sql_loader.load_sql("cow", "known_intents", remaining_cte=remaining_cte)
    depth = sql_loader.load_sql("cow", "intent_depth", remaining_cte=remaining_cte)
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
    blk_cte = sql_loader.load_sql("cow", "auction_blk_cte", scope_pred_bare=scope_pred_bare)
    anchor = sql_loader.load_sql("cow", "auction_anchor", scope_pred_bare=scope_pred_bare)
    time_pred, time_params = _time_predicate("b.block_timestamp", range_state, anchor)
    params.update(time_params)
    common = sql_loader.load_sql("cow", "auction_common", scope_pred_c=scope_pred_c, time_pred=time_pred)
    activity = sql_loader.load_sql("cow", "auction_activity", blk_cte=blk_cte, common=common)
    auctions = sql_loader.load_sql("cow", "auctions", blk_cte=blk_cte, scope_pred_bare=scope_pred_bare, scope_pred_c=scope_pred_c, time_pred=time_pred)
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
    blocks_cte = sql_loader.load_sql("cow", "solver_blocks_cte", ids=ids)
    anchor = sql_loader.load_sql("cow", "solver_anchor", ids=ids)
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
    stats = sql_loader.load_sql("cow", "solver_stats", blocks_cte=blocks_cte, ids=ids, settlement_time=settlement_time, common_joins=common_joins, common_where=common_where)
    activity = sql_loader.load_sql("cow", "solver_activity", blocks_cte=blocks_cte, common=common)
    ranking = sql_loader.load_sql("cow", "ranking_distribution", blocks_cte=blocks_cte, common=common)
    # ---- Solver directory (all-time presence, dual-mode) ----
    # ONE full settlements streaming scan into a ~558-entry (chain, solver)
    # hash (live-verified 1.6s) + the small competition tables. All-time by
    # design: presence/first-seen/last-seen need no window (disclosed). The
    # per-chain anchor ships in every row so the frontend computes activity
    # tiers against the CHAIN's own freshness — a stale indexer (BNB) must
    # not mark its solvers inactive. keys via UNION DISTINCT (not FULL OUTER
    # JOIN — ClickHouse empty-key default quirks).
    directory = sql_loader.load_sql("cow", "solver_directory", ids=ids)
    # ---- Winner score gap vs reference (dual-mode) ----
    # reference_score is ALWAYS a JSON map keyed by solver address; scores are
    # opaque big-int strings — parse defensively, surface failures as a count
    # instead of dropping rows silently.
    gap_expr = (
        "toFloat64OrNull(s.score)"
        "-toFloat64OrNull(JSONExtractString(c.reference_score,s.solver))"
    )
    score_gaps = sql_loader.load_sql("cow", "solver_score_gaps", blocks_cte=blocks_cte, gap_expr=gap_expr, common_joins=common_joins, common_where=common_where)
    dir_params = _scope_parameters(scope, None)
    specs = [
        QuerySpec("solver_stats", "Competition solver statistics", stats, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("solver_activity", "Competition solver activity", activity, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("ranking_distribution", "Solution ranking distribution", ranking, params, "auction_block_timestamp", "observed_series"),
        QuerySpec("solver_directory", "Solver directory (observed presence)", directory, dict(dir_params), "block_timestamp", BASE_DEDUP_MODE, 1800),
        QuerySpec("solver_score_gaps", "Winner score gap vs reference", score_gaps, dict(params), "auction_block_timestamp", "observed_series", 900),
    ]
    if chain is None:
        cross_chain = sql_loader.load_sql("cow", "solver_cross_chain", blocks_cte=blocks_cte, common=common)
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
    flow = sql_loader.load_sql("cow", "execution_flow", token_metadata_cte=_token_metadata_cte(), flow_settle_time=flow_settle_time, scope_pred_t=_scope_predicate(chain, 't'), flow_time=flow_time, flow_filter_sql=''.join(f' AND {predicate}' for predicate in flow_filters))
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
    exec_cte = sql_loader.load_sql("cow", "exec_cte", settle_time=settle_time)
    trade_where = (
        "t.environment={env:String} AND t.chain_id={chain_id:UInt64} "
        f"AND t.block_timestamp IS NOT NULL AND {time_pred}"
    )
    pair_matrix = sql_loader.load_sql("cow", "pair_matrix", token_metadata_cte=_token_metadata_cte(), exec_cte=exec_cte, trade_where=trade_where)
    affinity = sql_loader.load_sql("cow", "affinity", exec_cte=exec_cte, trade_where=trade_where)
    fill_surplus = SURPLUS_BPS.format(
        eb="f.buy_amount", ls="o.sell_amount", es="f.sell_amount", lb="o.buy_amount",
    )
    fee_quality = sql_loader.load_sql("cow", "fee_quality", fill_surplus=fill_surplus, fill_time_pred=time_pred.replace('t.block_timestamp', 'f.block_timestamp'))
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
    quote_delta = sql_loader.load_sql("cow", "quote_delta", quote_delta_expr=quote_delta_expr, fill_time_pred=time_pred.replace('t.block_timestamp', 'f.block_timestamp'))
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
    pulse = sql_loader.load_sql("cow", "pulse", ids=ids)
    # Live feeds MUST dedup indexer versions: the last hour is exactly where
    # ReplacingMergeTree parts are still unmerged (and API+RPC dual-source rows
    # coexist), so a raw base-table read shows every fresh fill twice. The
    # 1-hour bound keeps the argMax GROUP BY tiny.
    trades = sql_loader.load_sql("cow", "trades", tmx=tmx_cte, feed_pred=feed_pred, live_window=LIVE_WINDOW_SQL)
    settlements = sql_loader.load_sql("cow", "settlements", feed_pred=feed_pred, live_window=LIVE_WINDOW_SQL)
    # The backfill grew `orders` to ~12M rows, so FINAL's whole-table k-way
    # merge blew the memory budget at all-networks scope (code 241). valid_to
    # is IMMUTABLE per order_uid, so prefiltering the raw scan to unexpired
    # validity bounds the argMax hash to the small live set (the ogopen
    # pattern in _overview_specs); only mutable columns (status,
    # executed_sell_amount) need latest-version dedup. creation_date/valid_to
    # join the GROUP BY key instead of being argMax'd — an aggregate alias on
    # valid_to beside the same-level WHERE is the alias-in-WHERE trap
    # (code 184). Token-metadata joins on the selected 100 rows only.
    open_orders = sql_loader.load_sql("cow", "open_orders", tmx=tmx_cte, feed_pred=feed_pred)
    # order_events likewise outgrew FINAL; the 1h observed_at bound keeps the
    # argMax hash to an hour of rows, and event_id is the unique event key.
    events = sql_loader.load_sql("cow", "events", feed_pred=feed_pred)
    # Minute-bucketed heartbeat for the live band chart: 1h bound keeps the
    # (minute x chain) hash at <= 60 x 10 entries regardless of load.
    minute_activity = sql_loader.load_sql("cow", "minute_activity", feed_pred=feed_pred, live_window=LIVE_WINDOW_SQL, trade_key=TRADE_KEY)
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
    detail = sql_loader.load_sql("cow", "order_detail", token_metadata_cte=_token_metadata_cte())
    trades = sql_loader.load_sql("cow", "order_trades", token_metadata_cte=_token_metadata_cte())
    events = sql_loader.load_sql("cow", "order_events")
    fees = sql_loader.load_sql("cow", "order_fees", token_metadata_cte=_token_metadata_cte())
    app_data = sql_loader.load_sql("cow", "order_app_data")
    realized_surplus = SURPLUS_BPS.format(
        eb="any(o.executed_buy_amount)", ls="any(o.sell_amount)",
        es="any(o.executed_sell_amount)", lb="any(o.buy_amount)",
    )
    quality = sql_loader.load_sql("cow", "order_quality", realized_surplus=realized_surplus)
    return _entity_query_specs([
        ("order_detail", "Order", detail, "creation_date"),
        ("order_quality", "Execution quality vs limit", quality, "observed_at"),
        ("order_trades", "Settled fills", trades, "block_timestamp"),
        ("order_events", "Observed order lifecycle", events, "observed_at"),
        ("order_fees", "Indexed fee-policy amounts", fees, "observed_at"),
        ("order_app_data", "App data", app_data, "observed_at"),
    ], params)


def _transaction_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    detail = sql_loader.load_sql("cow", "transaction_detail")
    trades = sql_loader.load_sql("cow", "transaction_trades", token_metadata_cte=_token_metadata_cte())
    interactions = sql_loader.load_sql("cow", "transaction_interactions")
    competition = sql_loader.load_sql("cow", "transaction_competition")
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
    summary = sql_loader.load_sql("cow", "address_summary")
    # Top-N-first owner tape: PREWHERE prunes column reads before the wide
    # SELECT list materializes (owner is not in the sort key, so this is a
    # full-partition scan either way — PREWHERE + bounded heap keep it cheap
    # and memory-safe at the entity view's all-history default).
    trades = sql_loader.load_sql("cow", "address_trades", token_metadata_cte=_token_metadata_cte(), tape_arm_limit=TAPE_ARM_LIMIT, row_cap=ROW_CAP)
    orders = sql_loader.load_sql("cow", "address_orders", token_metadata_cte=_token_metadata_cte())
    solver = sql_loader.load_sql("cow", "address_solver_activity", row_cap=ROW_CAP)
    return _entity_query_specs([
        ("address_summary", "Address activity summary", summary, "observed_at"),
        ("address_trades", "Owned settled fills", trades, "block_timestamp"),
        ("address_orders", "Owned orders", orders, "creation_date"),
        ("address_solver_activity", "Solver and executor roles", solver, "observed_at"),
    ], params)


def _token_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    detail = sql_loader.load_sql("cow", "token_detail", token_metadata_cte=_token_metadata_cte(), native_token=NATIVE_TOKEN)
    pairs = sql_loader.load_sql("cow", "token_pairs", token_metadata_cte=_token_metadata_cte())
    executions = sql_loader.load_sql("cow", "token_execution_prices", token_metadata_cte=_token_metadata_cte())
    native = sql_loader.load_sql("cow", "token_native_prices")
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
    detail = sql_loader.load_sql("cow", "auction_detail")
    orders = sql_loader.load_sql("cow", "auction_orders")
    prices = sql_loader.load_sql("cow", "auction_prices", token_metadata_cte=_token_metadata_cte())
    solutions = sql_loader.load_sql("cow", "auction_solutions")
    transactions = sql_loader.load_sql("cow", "auction_transactions")
    return _entity_query_specs([
        ("auction_detail", "Auction", detail, "auction_block_timestamp"),
        ("auction_orders", "Auction orders", orders, "observed_at"),
        ("auction_prices", "Auction price vector", prices, "observed_at"),
        ("auction_solutions", "Competition solutions", solutions, "observed_at"),
        ("auction_transactions", "Settlement transactions", transactions, "observed_at"),
    ], params)


def _solver_entity_specs(params: dict[str, Any]) -> list[QuerySpec]:
    summary = sql_loader.load_sql("cow", "solver_summary")
    competitions = sql_loader.load_sql("cow", "solver_competitions")
    solutions = sql_loader.load_sql("cow", "solver_solutions")
    # Top-N-first executor tape on the base table (solver is not in the sort
    # key — full-partition scan either way; PREWHERE + bounded heap keep it
    # cheap and memory-safe at all-history).
    settlements = sql_loader.load_sql("cow", "solver_settlements", tape_arm_limit=TAPE_ARM_LIMIT, row_cap=ROW_CAP)
    # Shared accounting CTEs: this solver's settlements over the last 30
    # indexed days, and the per-token net flow between traders and the
    # settlement contract in each of those batches. This is ORDER-LEVEL,
    # TRADE-IMPLIED accounting — AMM leg amounts, plain ERC20 transfers, and
    # buffer balances are NOT in cow_db, so it shows what the solver had to
    # source externally (or what accrued), not audited buffer books.
    accounting_ctes = sql_loader.load_sql("cow", "solver_accounting_ctes")
    imbalance_settlements = sql_loader.load_sql("cow", "solver_imbalance_settlements", accounting_ctes=accounting_ctes)
    imbalance_tokens = sql_loader.load_sql("cow", "solver_imbalance_tokens", token_metadata_cte=_token_metadata_cte(), accounting_ctes=accounting_ctes)
    # reference_score is ALWAYS a JSON map keyed by solver address (verified
    # live 2026-07-21): JSONExtractString is the only parse path.
    score_gap = sql_loader.load_sql("cow", "solver_score_gap")
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
    bucket_seconds: int = 0,
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
        return _market_specs(
            chain, pair, interval, range_state, depth_at, heatmap_window, bucket_seconds,
        )
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
        sql = sql_loader.load_sql("cow", "search_transaction_hash", where=where)
        identifier = q
    elif HASH_RE.fullmatch(q):
        sql = sql_loader.load_sql("cow", "search_transaction_arm", where=where)
        identifier = q
    elif ADDRESS_RE.fullmatch(q):
        sql = sql_loader.load_sql("cow", "search_address_or_token", where=where)
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
        "bucket_seconds": 0,
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
    bucket_seconds: int = -1,
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
    # bucket_seconds contract mirrors heatmap_window: -1 reuses the view's
    # current resolution, 0 is an explicit "auto", anything else is validated.
    if bucket_seconds < 0:
        effective_bucket_seconds = int(state.get("bucket_seconds") or 0)
    else:
        effective_bucket_seconds = _validate_bucket_seconds(bucket_seconds)
    specs = _section_specs(
        section_key, scope, chain, pair, interval, range_state, filters,
        effective_depth_at, effective_heatmap_window, effective_bucket_seconds,
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
        "bucket_seconds": effective_bucket_seconds,
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
        bucket_seconds: int = -1,
    ) -> CallToolResult:
        """[App-only] Load one deferred CoW dataset group (additive).

        ``depth_at``: "" keeps the view's current depth timestamp, "live"
        returns the markets depth panel to the live book, and an ISO-8601
        timestamp reconstructs the pair's open book at that moment.

        ``heatmap_window``: "" keeps the view's current depth-footprint
        window (default "7d"); "24h"/"7d"/"30d"/"90d"/"all" pick the span the
        markets.depth_heatmap group grids over.

        ``bucket_seconds``: -1 keeps the view's current footprint resolution,
        0 is auto (span/60), and any value in seconds picks the bucket width.
        Too-fine requests are coarsened to fit the row budget rather than
        rejected; the effective width comes back in the dataset's
        ``bucket_seconds`` column.
        """
        try:
            payload, summary = _apply_group_load(
                ch, view_id, section, group, scope_id, force_refresh, depth_at,
                heatmap_window, bucket_seconds,
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
