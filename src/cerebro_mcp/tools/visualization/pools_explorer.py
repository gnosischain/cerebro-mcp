"""Pool Liquidity Explorer mini app.

Read-only analyst surface over the ``rpc_state_indexer`` ClickHouse database:
daily, publication-verified state for Gnosis Chain DEX pools. Two indexer jobs
feed it and the difference between them is the app's central fact:

* ``daily_cl_liquidity`` reads concentrated-liquidity pools (Uniswap v3 and
  Swapr v3 / Algebra) — slot0 price, in-range liquidity, fee-growth
  accumulators and, for pools above an activity threshold, every initialized
  tick. That tick set is what a liquidity profile is made of.
* ``daily_pool_reserves`` reads raw token balances for a strict superset of the
  same pools plus Balancer v2/v3, which have no ticks at all.

So a pool is in one of three states and the UI must never blur them: probed
(state + profile), state-only (below the tick threshold), or reserves-only
(Balancer). Roughly 427 of 2,519 CL pools are probed on a given day.

Frozen contract (mirrored byte-for-byte by the frontend, test-enforced on both
sides):

* ``SECTION_GROUPS`` — every dataset key lives in exactly one group, globally
  unique across sections. The ``pool`` and ``token`` sections are entity
  drill-downs loaded through ``load_pools_explorer_entity`` and then streamed
  by the same group loader as any tab, so a profile date change or the
  over-time heatmap is one additive group call rather than a reload.
* **Only ``rpc_state_indexer``.** No dbt model is joined anywhere. That is a
  product decision, not an oversight: the dbt pool models carry USD prices and
  whitelist symbols, and mixing them in would make it impossible to say which
  numbers are chain-verified at a pinned block and which are modelled.
* **Prices are raw by default.** ``price_raw`` is token1 atoms per token0 atom
  straight from ``sqrtPriceX96`` and always honest. ``price_adjusted`` needs
  BOTH tokens' decimals and is NULL otherwise — which is most pools here, since
  the metadata resolver has symbols for 34 of the 2,312 tokens in these pools.
* **FINAL has exactly one home on this plane.** ``config_registry`` is a
  ReplacingMergeTree and takes ``FINAL``. Every ``v_*`` view resolves dedup
  internally and must NOT be FINAL'd. The raw ``pool_*`` tables are never read
  at all: their sort key includes ``attempt_id``, so even FINAL leaves one row
  per retry.
* **Dates resolve from ``census_publications``**, never by aggregating a view,
  and every view scan carries a constant-folding ``IN`` prune beside its join
  (lesson: fat-view-join-never-prunes).
* **Every date column is an ISO string.** When the as-of predicate is
  unbounded ClickHouse folds the whole aggregate to a constant and the driver
  returns the raw day number, so the same column arrives as ``2026-09-16`` on
  one code path and ``20712`` on another. ``toDate()``, ``CAST`` and
  ``materialize()`` all still return the integer; only ``toString()`` survives
  the fold. Emitting every date as a string makes the wire contract uniform and
  round-trips exactly through the ``as_of`` deep link.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import (
    INTERACTIVE_QUERY_BUDGET,
    ClickHouseManager,
)
from cerebro_mcp.models.mini_app import MiniAppPayload, SummaryCard
from cerebro_mcp.runtime.mini_app_cache import CachedDataset, FailureCache
from cerebro_mcp.tools.visualization import (
    coingecko, mini_apps, sql_loader, token_rpc, web_apps,
)

logger = logging.getLogger(__name__)

POOLS_APP_ID = "pools_explorer"
POOLS_TITLE = "Pool Liquidity Explorer"
POOLS_URI = "ui://cerebro/pools_explorer"
POOLS_DB = "rpc_state_indexer"
#: Gnosis Chain. The indexer publishes pool jobs on this chain only; the
#: mainnet jobs in the same database are token censuses, not pools.
CHAIN_ID = 100

CL_JOB = "daily_cl_liquidity"
RESERVES_JOB = "daily_pool_reserves"
STATE_VIEW = "v_pool_cl_state_published"
TICK_VIEW = "v_pool_tick_liquidity_published"
BALANCES_VIEW = "v_pool_token_balances_published"
METADATA_VIEW = "v_token_metadata_current"
ANCHORS_VIEW = "v_day_anchors_canonical"
PUB_TABLE = "census_publications"
#: The check the indexer records when it decided a pool was too quiet to be
#: worth reading every initialized tick for. Its PRESENCE means the ticks were
#: NOT read, which is why every consumer negates it.
BELOW_THRESHOLD_CHECK = "cl_below_active_threshold"

#: Pool classes with tick-level data, and those with balances only. Derived
#: from the indexer's own ``pool_class`` vocabulary rather than guessed from a
#: name: it is what actually decides whether a profile can exist.
CL_CLASSES = ("uniswap_v3", "swapr_v3_algebra")
RESERVES_CLASSES = ("balancer_v2", "balancer_v3")
POOL_CLASSES = frozenset(CL_CLASSES + RESERVES_CLASSES)
POOL_FAMILIES = frozenset({"cl", "reserves_only"})

#: Earliest snapshot either pool job published. Used to reject an ``as_of``
#: before the plane existed rather than returning a silently empty app.
FIRST_DATE = date(2022, 12, 12)
#: A source whose newest publication is older than this reads as a missed run.
STALE_DAYS = 2

#: Fee-tier bands over the raw pips. Uniswap v3 uses four static tiers; Algebra
#: sets a dynamic fee that has taken 435 distinct values on this chain, so the
#: filter buckets rather than enumerates.
FEE_BAND_PREDICATES = {
    "b100": "st.fee <= 100",
    "b500": "st.fee > 100 AND st.fee <= 500",
    "b3000": "st.fee > 500 AND st.fee <= 3000",
    "b10000": "st.fee > 3000 AND st.fee <= 10000",
    "bhigh": "st.fee > 10000",
}
#: History windows, in days. 0 means every published day.
WINDOWS = {"90d": 90, "1y": 365, "all": 0}
DEFAULT_WINDOW = "1y"
HEATMAP_WINDOWS = ("90d", "1y", "all")
DEFAULT_HEATMAP_WINDOW = "1y"

#: Heatmap grid bounds. The product is the row ceiling for the single most
#: expensive dataset in the app and is asserted to sit inside ROW_CAP.
HEATMAP_MAX_DATES = 120
HEATMAP_TICK_BUCKETS = 80
#: ln(1.2) / ln(1.0001): the tick distance that is 20% away in price. The
#: heatmap axis pads the pool's own current-tick excursion by this much, so the
#: grid always shows the neighbourhood that can actually be traded into.
HEATMAP_AXIS_PAD_TICKS = 1823

#: Tick offsets equal to +-1%, +-5% and +-10% in price (ln(1+x)/ln(1.0001)).
BAND_TICKS = {"1pct": 100, "5pct": 488, "10pct": 953}
#: A position at or beyond this tick in both directions is full-range. The two
#: conventions in use here are +-887220 (tick spacing 60) and +-887270 (spacing
#: 10), so the threshold sits below both rather than matching either exactly.
FULL_RANGE_TICK = 887000

#: Columns on this plane that hold a token address — scalars and the arrays a
#: pool with more than two assets carries. Narrow on purpose: a loose pattern
#: would sweep pool addresses into a token lookup.
TOKEN_COLUMN_RE = re.compile(
    r"^(?:token0|token1|token_address|assets|counter_tokens|reserve_tokens)$"
)
#: Addresses resolved per overlay call. The directory pages 100 rows, so a page
#: is at most ~200 tokens and lands in one RPC round trip; the ceiling exists so
#: a wide view degrades into "more pending" rather than a long block.
TOKEN_OVERLAY_CAP = 400

ROW_CAP = 10_000
#: 4 list sections + the two entity sections: an entity drill-down never evicts
#: a section scope.
MAX_RETAINED_SECTIONS = 6
SEARCH_CANDIDATE_CAP = 20
MAX_QUERY_LENGTH = 200

VALID_SECTIONS = {"overview", "pools", "tokens", "coverage", "pool", "token"}
#: Sections that are entity drill-downs: reachable through
#: ``load_pools_explorer_entity``, never through the section tool.
ENTITY_SECTIONS = {"pool": "pool", "token": "token"}
ENTITY_TYPES = frozenset(ENTITY_SECTIONS)

ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
HEX_PREFIX_RE = re.compile(r"^0x[0-9a-f]{2,39}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Whitelisted ORDER BY fragments. Every one ends in a unique id so paging is
#: deterministic (lesson: ch-bare-limit-nondeterministic).
#: Sort ids carry their direction (``<field>_<dir>``) rather than pairing a
#: field with a separate order argument: one token means one whitelist lookup
#: and no way to ask for a direction the server did not intend. ``""`` is the
#: server default. Mirrored by POOL_SORTS / TOKEN_SORTS in the frontend's
#: state/toolArgs.ts.
DIRECTORY_SORTS = {
    "": "liquidity_float DESC NULLS LAST, pool_address",
    "liquidity_asc": "liquidity_float ASC NULLS LAST, pool_address",
    "tick_count_desc": "tick_count DESC NULLS LAST, pool_address",
    "days_published_desc": "days_published DESC, pool_address",
    "first_published_asc": "first_published ASC, pool_address",
    "first_published_desc": "first_published DESC, pool_address",
    "fee_asc": "fee ASC NULLS LAST, pool_address",
    "fee_desc": "fee DESC NULLS LAST, pool_address",
    "pool_name_asc": "pool_name ASC, pool_address",
}
TOKEN_SORTS = {
    "": "pools_count DESC, token_address",
    "live_pools_desc": "live_pools DESC, token_address",
    "probed_pools_desc": "probed_pools DESC, token_address",
    "symbol_asc": "symbol ASC NULLS LAST, token_address",
}

POOLS_APP_META = {
    "ui": {"resourceUri": POOLS_URI},
    "ui/resourceUri": POOLS_URI,
}

#: Provenance label. One plane, one label — every figure in this app is a
#: verified read at a pinned finalized block, and none of it is priced.
SOURCE_LABEL = (
    "rpc-state-indexer daily snapshots at pinned finalized blocks (Gnosis Chain); "
    "no USD valuation on this plane"
)

#: Datasets per section, split into load groups (FROZEN — the frontend mirrors
#: this map byte-for-byte). A section apply loads only ``core``; every other
#: group is streamed afterwards through ``load_pools_explorer_datasets``. Every
#: dataset key appears in exactly one group and keys are globally unique.
SECTION_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "overview": {
        "core": ("pools_summary", "source_freshness"),
        "mix": ("pools_by_class_fee", "probe_coverage_split"),
        # Alone in its group: the only dataset that scans the state view across
        # the whole window rather than one snapshot.
        "trend": ("live_pool_trend",),
        "concentration": ("concentration_summary", "range_width_distribution"),
    },
    "pools": {"core": ("pool_directory",)},
    "tokens": {"core": ("token_directory",)},
    "coverage": {
        "core": ("coverage_summary", "publication_calendar"),
        "gaps": ("missing_days", "metadata_gap"),
    },
    "pool": {
        "core": ("pool_detail", "pool_publication_facts"),
        # Date-scoped: a profile-date change reloads exactly this group.
        "profile": ("pool_profile_at", "pool_profile_concentration", "pool_ticks_at"),
        "history": ("pool_state_history", "pool_reserves_history"),
        "fees": ("pool_fee_growth",),
        # On demand only — the frontend loads it when the over-time view opens.
        "heatmap": ("pool_profile_heatmap",),
    },
    "token": {"core": ("token_detail", "token_pools")},
}
#: Keys that only exist for a concentrated-liquidity pool. A Balancer pool's
#: group load simply skips them, exactly as a group load skips any absent key.
CL_ONLY_KEYS = frozenset({
    "pool_profile_at", "pool_profile_concentration", "pool_ticks_at",
    "pool_state_history", "pool_fee_growth", "pool_profile_heatmap",
})


@dataclass(frozen=True)
class QuerySpec:
    key: str
    title: str
    sql: str
    parameters: dict[str, Any]
    basis: str
    cache_ttl_seconds: int = 1800
    #: False for the datasets that scan a history window rather than one
    #: snapshot: the loader's ``count() OVER ()`` envelope forces the whole
    #: result to materialize before LIMIT, and their row counts are already
    #: bounded by construction.
    exact_count: bool = True


_BUNDLE = mini_apps.StaticBundle(
    "pools_explorer.html",
    assets_dir="assets/pools_explorer",
    build_hint="make build-ui-pools-explorer",
)


def get_pools_explorer_html() -> str:
    return _BUNDLE.html()


def get_pools_explorer_diagnostics() -> dict[str, Any]:
    return _BUNDLE.diagnostics()


# ---------------------------------------------------------------------------
# Shared SQL composition
# ---------------------------------------------------------------------------

_PUB = f"{POOLS_DB}.{PUB_TABLE}"
_CL_CLASS_LIST = ", ".join(f"'{name}'" for name in CL_CLASSES)


def _asof_cte(as_of: str) -> str:
    """The as-of resolver. ``as_of`` is "" for the newest published day or an
    ISO date for the newest day on or before it — never an equality, so a day
    the indexer skipped resolves backwards instead of emptying the app."""
    bound = "1"
    if as_of:
        bound = sql_loader.load_sql("pools", "_pred_asof_upper_bound")
    return sql_loader.load_sql(
        "pools", "_cte_asof", pub=_PUB, job=CL_JOB, chain=CHAIN_ID, asof_bound=bound
    )


def _reserves_asof_cte() -> str:
    return sql_loader.load_sql(
        "pools", "_cte_reserves_asof", pub=_PUB, job=RESERVES_JOB, chain=CHAIN_ID
    )


def _cfg_cte(pool_sql: str = "1") -> str:
    return sql_loader.load_sql(
        "pools", "_cte_config", db=POOLS_DB, chain=CHAIN_ID, job=RESERVES_JOB,
        cl_classes=_CL_CLASS_LIST, pool_sql=pool_sql,
    )


def _meta_cte() -> str:
    return sql_loader.load_sql(
        "pools", "_cte_token_meta", db=POOLS_DB, meta_view=METADATA_VIEW,
        chain=CHAIN_ID,
    )


def _probe_cte() -> str:
    return sql_loader.load_sql(
        "pools", "_cte_probe_flags", pub=_PUB, job=CL_JOB, chain=CHAIN_ID,
        check=BELOW_THRESHOLD_CHECK,
    )


def _life_cte() -> str:
    return sql_loader.load_sql("pools", "_cte_pool_lifetimes", pub=_PUB, chain=CHAIN_ID)


def _st_cte(pool_sql: str = "1") -> str:
    return sql_loader.load_sql(
        "pools", "_cte_state_at", db=POOLS_DB, view=STATE_VIEW, chain=CHAIN_ID,
        job=CL_JOB, pool_sql=pool_sql,
    )


def _res_cte(pool_sql: str = "1") -> str:
    return sql_loader.load_sql(
        "pools", "_cte_reserves_at", db=POOLS_DB, view=BALANCES_VIEW,
        chain=CHAIN_ID, job=RESERVES_JOB, pool_sql=pool_sql,
    )


def _probe_days_cte() -> str:
    """Per-day tick-probe flag for one pool, so a history series can mark the
    days the indexer did not read its ticks."""
    return sql_loader.load_sql(
        "pools", "_cte_probe_days", pub=_PUB, job=CL_JOB, chain=CHAIN_ID,
        check=BELOW_THRESHOLD_CHECK,
    )

def _ranges_cte(pool_sql: str, date_sql: str) -> str:
    return sql_loader.load_sql(
        "pools", "_cte_profile_ranges", db=POOLS_DB, view=TICK_VIEW,
        chain=CHAIN_ID, job=CL_JOB, pool_sql=pool_sql, date_sql=date_sql,
    )


def _asof_prune(alias: str) -> str:
    """The prune every scan of a dated view carries beside its as-of join."""
    return sql_loader.load_sql("pools", "_pred_asof_prune", alias=alias)


def _heatmap_date_prune(alias: str) -> str:
    return sql_loader.load_sql("pools", "_pred_heatmap_dates", alias=alias)


def _price_raw(alias: str) -> str:
    return sql_loader.load_sql("pools", "_expr_price_raw", alias=alias)


def _price_adjusted(price_raw: str, dec0: str, dec1: str) -> str:
    return sql_loader.load_sql(
        "pools", "_expr_price_adjusted", price_raw=price_raw, dec0=dec0, dec1=dec1
    )


def _fee_band(alias: str) -> str:
    return sql_loader.load_sql("pools", "_expr_fee_band", alias=alias)


def _asset_symbols(assets: str) -> str:
    return sql_loader.load_sql("pools", "_expr_asset_symbols", assets=assets)


def _asset_decimals(assets: str) -> str:
    return sql_loader.load_sql("pools", "_expr_asset_decimals", assets=assets)


def _window_pred(alias: str, days: int) -> str:
    """Window predicate anchored to the resolved as-of, or ``1`` for all
    history. Anchoring to the as-of rather than to ``now()`` matters: the
    indexer runs behind wall clock, and a now()-anchored window silently loses
    the lag off the end of every series."""
    if days <= 0:
        return "1"
    return sql_loader.load_sql("pools", "_pred_window", alias=alias, days=days)


# ---------------------------------------------------------------------------
# Validation (all raise before any SQL)
# ---------------------------------------------------------------------------


def _default_filters() -> dict[str, Any]:
    return {
        "query": "",
        "pool_class": "",
        "pool_family": "",
        "fee_band": "",
        "fee": 0,
        "token": "",
        "live_only": False,
        "probed_only": False,
        "sort_by": "",
    }


def _validate_as_of(as_of: str) -> str:
    value = as_of.strip()
    if not value:
        return ""
    if not DATE_RE.fullmatch(value):
        raise ValueError("as_of must be an ISO date (YYYY-MM-DD) or empty")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("as_of must be a valid ISO date") from exc
    today = datetime.now(timezone.utc).date()
    if parsed < FIRST_DATE or parsed > today:
        raise ValueError(
            f"as_of must fall between {FIRST_DATE.isoformat()} and today"
        )
    return parsed.isoformat()


def _validate_window(window: str, *, allowed: tuple[str, ...] = ()) -> str:
    value = window.strip().lower()
    if not value:
        return DEFAULT_WINDOW
    choices = allowed or tuple(WINDOWS)
    if value not in choices:
        raise ValueError(f"window must be one of {sorted(choices)}")
    return value


def _validate_filters(
    section: str,
    query: str,
    pool_class: str,
    pool_family: str,
    fee_band: str,
    fee: int,
    token: str,
    live_only: bool,
    probed_only: bool,
    sort_by: str,
) -> dict[str, Any]:
    """Validate every filter and its per-section applicability. A filter that
    cannot apply to the section is an error, never a silent no-op."""
    text = query.strip()
    if len(text) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must be at most {MAX_QUERY_LENGTH} characters")
    klass = pool_class.strip().lower()
    if klass and klass not in POOL_CLASSES:
        raise ValueError(f"pool_class must be one of {sorted(POOL_CLASSES)}")
    family = pool_family.strip().lower()
    if family and family not in POOL_FAMILIES:
        raise ValueError(f"pool_family must be one of {sorted(POOL_FAMILIES)}")
    band = fee_band.strip().lower()
    if band and band not in FEE_BAND_PREDICATES:
        raise ValueError(f"fee_band must be one of {sorted(FEE_BAND_PREDICATES)}")
    pips = int(fee or 0)
    if pips < 0:
        raise ValueError("fee must be a non-negative integer (pips)")
    if pips and band:
        raise ValueError("pass either fee or fee_band, not both")
    address = token.strip().lower()
    if address and not ADDRESS_RE.fullmatch(address):
        raise ValueError("token must be a lowercase 0x-prefixed 20-byte address")
    sort = sort_by.strip().lower()
    sorts = DIRECTORY_SORTS if section == "pools" else TOKEN_SORTS
    if section not in {"pools", "tokens"}:
        if sort:
            raise ValueError("sort_by applies only to the pools and tokens sections")
    elif sort not in sorts:
        raise ValueError(
            f"sort_by must be one of {sorted(k for k in sorts if k)} "
            f"for section {section}"
        )
    if section != "pools" and (
        klass or family or band or pips or address or live_only or probed_only
    ):
        raise ValueError(
            "pool_class/pool_family/fee/fee_band/token/live_only/probed_only "
            "apply only to the pools section"
        )
    if text and section not in {"pools", "tokens"}:
        raise ValueError("query applies only to the pools and tokens sections")
    return {
        "query": text,
        "pool_class": klass,
        "pool_family": family,
        "fee_band": band,
        "fee": pips,
        "token": address,
        "live_only": bool(live_only),
        "probed_only": bool(probed_only),
        "sort_by": sort,
    }


def _validate_entity_identifier(entity_type: str, identifier: str) -> str:
    kind = entity_type.strip().lower()
    if kind not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    value = identifier.strip().lower()
    if not ADDRESS_RE.fullmatch(value):
        raise ValueError(f"{kind} identifier must be a 0x-prefixed EVM address")
    return value


# ---------------------------------------------------------------------------
# Spec builders
# ---------------------------------------------------------------------------


def _source_freshness_spec() -> QuerySpec:
    sql = sql_loader.load_sql(
        "pools", "source_freshness", pub=_PUB, cl_job=CL_JOB,
        reserves_job=RESERVES_JOB, chain=CHAIN_ID,
    )
    return QuerySpec(
        "source_freshness", "Source freshness", sql, {},
        "newest publication per indexer job", 300,
    )


def _directory_predicates(filters: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Runtime-shaped WHERE fragments for the pool directory.

    Every predicate names a TABLE-QUALIFIED column. The projection aliases
    ``fee``, ``is_live`` and friends over the same names, and an output alias
    shadows the source column in a WHERE — which returns nothing, with no error
    (lesson: ch-output-alias-shadows-column).
    """
    params: dict[str, Any] = {}
    parts = {
        "class_sql": "1", "family_sql": "1", "fee_sql": "1", "token_sql": "1",
        "live_sql": "1", "probed_sql": "1", "query_sql": "1",
    }
    if filters.get("pool_class"):
        parts["class_sql"] = "cfg.pool_class = {pool_class:String}"
        params["pool_class"] = filters["pool_class"]
    if filters.get("pool_family"):
        parts["family_sql"] = "cfg.pool_family = {pool_family:String}"
        params["pool_family"] = filters["pool_family"]
    if filters.get("fee"):
        parts["fee_sql"] = "st.fee = {fee:UInt32}"
        params["fee"] = int(filters["fee"])
    elif filters.get("fee_band"):
        parts["fee_sql"] = f"({FEE_BAND_PREDICATES[filters['fee_band']]})"
    if filters.get("token"):
        parts["token_sql"] = "has(cfg.assets, {token:String})"
        params["token"] = filters["token"]
    if filters.get("live_only"):
        parts["live_sql"] = (
            "(if(cfg.pool_family = 'cl', st.liquidity > 0, rs.r_any_positive))"
        )
    if filters.get("probed_only"):
        parts["probed_sql"] = "pr.ticks_probed"
    if filters.get("query"):
        parts["query_sql"] = (
            "(startsWith(cfg.pool_address, {q:String}) "
            "OR arrayExists(a -> startsWith(a, {q:String}), cfg.assets))"
        )
        params["q"] = filters["query"].lower()
    return parts, params


def _overview_specs(as_of: str, window: str) -> list[QuerySpec]:
    asof_cte = _asof_cte(as_of)
    params: dict[str, Any] = {"as_of": as_of} if as_of else {}
    days = WINDOWS[window]

    summary = sql_loader.load_sql(
        "pools", "pools_summary", asof_cte=asof_cte,
        reserves_asof_cte=_reserves_asof_cte(), cfg_cte=_cfg_cte(),
        st_cte=_st_cte(), probe_cte=_probe_cte(), res_cte=_res_cte(),
        db=POOLS_DB, anchors_view=ANCHORS_VIEW, chain=CHAIN_ID,
    )
    by_class = sql_loader.load_sql(
        "pools", "pools_by_class_fee", asof_cte=asof_cte,
        reserves_asof_cte=_reserves_asof_cte(), cfg_cte=_cfg_cte(),
        st_cte=_st_cte(), probe_cte=_probe_cte(), res_cte=_res_cte(),
        fee_band_sql=_fee_band("if(st.st_pool != '', toNullable(st.fee), NULL)"),
    )
    probe_split = sql_loader.load_sql(
        "pools", "probe_coverage_split", asof_cte=asof_cte, cfg_cte=_cfg_cte(),
        st_cte=_st_cte(), probe_cte=_probe_cte(),
    )
    trend = sql_loader.load_sql(
        "pools", "live_pool_trend", asof_cte=asof_cte, pub=_PUB, db=POOLS_DB,
        view=STATE_VIEW, chain=CHAIN_ID, cl_job=CL_JOB, reserves_job=RESERVES_JOB,
        check=BELOW_THRESHOLD_CHECK, window_pub=_window_pred("p", days),
        window_state=_window_pred("s", days),
    )
    ranges = _ranges_cte("1", _asof_prune("t"))
    concentration = sql_loader.load_sql(
        "pools", "concentration_summary", asof_cte=asof_cte, st_cte=_st_cte(),
        ranges_cte=ranges, band1=BAND_TICKS["1pct"], band5=BAND_TICKS["5pct"],
        band10=BAND_TICKS["10pct"], full_tick=FULL_RANGE_TICK,
    )
    widths = sql_loader.load_sql(
        "pools", "range_width_distribution", asof_cte=asof_cte, ranges_cte=ranges,
        full_tick=FULL_RANGE_TICK,
    )
    window_note = "all published days" if days <= 0 else f"latest {days} days"
    return [
        QuerySpec("pools_summary", "Pool universe", summary, dict(params),
                  "configured pools vs what published on the resolved as-of"),
        _source_freshness_spec(),
        QuerySpec("pools_by_class_fee", "Pools by class and fee", by_class,
                  dict(params),
                  "counts only — liquidity L is per-pair and is never summed "
                  "across pools"),
        QuerySpec("probe_coverage_split", "Tick coverage", probe_split, dict(params),
                  "CL pools split by whether their ticks were probed and whether "
                  "they hold liquidity"),
        QuerySpec("live_pool_trend", "Universe over time", trend, dict(params),
                  f"{window_note}; publication counts from the publications "
                  "table, live count from the state view", 3600,
                  exact_count=False),
        QuerySpec("concentration_summary", "Liquidity concentration", concentration,
                  dict(params),
                  "tick-weighted share of each probed pool's liquidity within "
                  "+-1%/5%/10% of its own price; a full-range position covers "
                  "every band by construction and is reported separately"),
        QuerySpec("range_width_distribution", "Position widths", widths, dict(params),
                  "ranges carrying active liquidity at the as-of; empty ranges "
                  "are gaps between positions and are excluded"),
    ]


def _pools_specs(as_of: str, filters: dict[str, Any]) -> list[QuerySpec]:
    parts, params = _directory_predicates(filters)
    if as_of:
        params["as_of"] = as_of
    price_raw = _price_raw("st")
    sql = sql_loader.load_sql(
        "pools", "pool_directory", asof_cte=_asof_cte(as_of),
        reserves_asof_cte=_reserves_asof_cte(), cfg_cte=_cfg_cte(),
        meta_cte=_meta_cte(), st_cte=_st_cte(), probe_cte=_probe_cte(),
        res_cte=_res_cte(), life_cte=_life_cte(),
        asset_symbols_sql=_asset_symbols("cfg.assets"),
        asset_decimals_sql=_asset_decimals("cfg.assets"),
        price_raw_sql=price_raw,
        price_adjusted_sql=_price_adjusted(
            "price_raw", "token0_decimals", "token1_decimals"
        ),
        fee_band_sql=_fee_band("fee"),
        sort_fragment=DIRECTORY_SORTS[filters.get("sort_by", "")],
        **parts,
    )
    return [QuerySpec(
        "pool_directory", "Pools", sql, params,
        "every configured pool; concentrated-liquidity columns are NULL for "
        "reserves-only (Balancer) pools, and prices are raw token1-per-token0 "
        "unless both decimals resolved",
    )]


def _tokens_specs(as_of: str, filters: dict[str, Any]) -> list[QuerySpec]:
    params: dict[str, Any] = {"as_of": as_of} if as_of else {}
    query_sql = "1"
    if filters.get("query"):
        query_sql = (
            "(startsWith(a.token_address, {q:String}) "
            "OR positionCaseInsensitive(ifNull(m.symbol, ''), {q:String}) > 0)"
        )
        params["q"] = filters["query"]
    sql = sql_loader.load_sql(
        "pools", "token_directory", asof_cte=_asof_cte(as_of), cfg_cte=_cfg_cte(),
        st_cte=_st_cte(), probe_cte=_probe_cte(), db=POOLS_DB,
        meta_view=METADATA_VIEW, chain=CHAIN_ID, token_query_sql=query_sql,
        sort_fragment=TOKEN_SORTS[filters.get("sort_by", "")],
    )
    return [QuerySpec(
        "token_directory", "Tokens", sql, params,
        "tokens held by the configured pool set, with how far metadata "
        "resolution got; an unresolved token is a chain fact, not a gap",
    )]


def _coverage_specs(as_of: str, window: str) -> list[QuerySpec]:
    asof_cte = _asof_cte(as_of)
    params: dict[str, Any] = {"as_of": as_of} if as_of else {}
    days = WINDOWS[window]
    summary = sql_loader.load_sql(
        "pools", "coverage_summary", asof_cte=asof_cte, cfg_cte=_cfg_cte(),
        pub=_PUB, chain=CHAIN_ID, cl_job=CL_JOB, reserves_job=RESERVES_JOB,
        check=BELOW_THRESHOLD_CHECK,
    )
    calendar = sql_loader.load_sql(
        "pools", "publication_calendar", asof_cte=asof_cte, cfg_cte=_cfg_cte(),
        pub=_PUB, chain=CHAIN_ID, cl_job=CL_JOB, reserves_job=RESERVES_JOB,
        check=BELOW_THRESHOLD_CHECK, window_pub=_window_pred("p", days),
    )
    missing = sql_loader.load_sql(
        "pools", "missing_days", asof_cte=asof_cte, pub=_PUB, chain=CHAIN_ID,
        cl_job=CL_JOB, reserves_job=RESERVES_JOB,
    )
    gaps = sql_loader.load_sql(
        "pools", "metadata_gap", asof_cte=asof_cte, cfg_cte=_cfg_cte(),
        meta_cte=_meta_cte(), db=POOLS_DB, meta_view=METADATA_VIEW,
        chain=CHAIN_ID,
    )
    window_note = "all published days" if days <= 0 else f"latest {days} days"
    return [
        QuerySpec("coverage_summary", "Job coverage", summary, dict(params),
                  "per indexer job, all history"),
        QuerySpec("publication_calendar", "Publications by day", calendar,
                  dict(params), window_note),
        QuerySpec("missing_days", "Gaps", missing, dict(params),
                  "days absent from a job's calendar, plus days that published "
                  "under 90% of the prior week's peak"),
        QuerySpec("metadata_gap", "Metadata coverage", gaps, dict(params),
                  "what the plane can label; this is why prices are raw"),
    ]


def _pool_entity_specs(
    identifier: str, as_of: str, window: str, heatmap_window: str, is_cl: bool
) -> list[QuerySpec]:
    """Datasets for one pool.

    A reserves-only (Balancer) pool has no ticks, no slot0 price and no
    fee-growth accumulators, so the concentrated-liquidity datasets are not
    BUILT for it rather than being built and returning nothing: an empty chart
    reads as "no liquidity", which would be false.
    """
    asof_cte = _asof_cte(as_of)
    params: dict[str, Any] = {"pool": identifier}
    if as_of:
        params["as_of"] = as_of
    days = WINDOWS[window]

    detail = sql_loader.load_sql(
        "pools", "pool_detail", asof_cte=asof_cte,
        reserves_asof_cte=_reserves_asof_cte(),
        cfg_cte=_cfg_cte("c.target_address = {pool:String}"), meta_cte=_meta_cte(),
        st_cte=_st_cte("s.pool_address = {pool:String}"), probe_cte=_probe_cte(),
        res_cte=_res_cte("b.pool_address = {pool:String}"), life_cte=_life_cte(),
        asset_symbols_sql=_asset_symbols("cfg.assets"),
        asset_decimals_sql=_asset_decimals("cfg.assets"),
        price_raw_sql=_price_raw("st"),
        price_adjusted_sql=_price_adjusted(
            "price_raw", "token0_decimals", "token1_decimals"
        ),
        fee_band_sql=_fee_band("fee"), db=POOLS_DB, view=STATE_VIEW,
        chain=CHAIN_ID, cl_job=CL_JOB, pub=_PUB, check=BELOW_THRESHOLD_CHECK,
    )
    facts = sql_loader.load_sql(
        "pools", "pool_publication_facts", asof_cte=asof_cte,
        reserves_asof_cte=_reserves_asof_cte(), pub=_PUB, db=POOLS_DB,
        anchors_view=ANCHORS_VIEW, chain=CHAIN_ID, cl_job=CL_JOB,
        reserves_job=RESERVES_JOB, check=BELOW_THRESHOLD_CHECK,
    )
    reserves_history = sql_loader.load_sql(
        "pools", "pool_reserves_history", asof_cte=asof_cte, db=POOLS_DB,
        view=BALANCES_VIEW, meta_view=METADATA_VIEW, chain=CHAIN_ID,
        job=RESERVES_JOB, window_state=_window_pred("b", days),
    )
    as_of_note = f"as of {as_of}" if as_of else "as of the newest published day"
    specs = [
        QuerySpec("pool_detail", "Pool", detail, dict(params), as_of_note),
        QuerySpec("pool_publication_facts", "Provenance", facts, dict(params),
                  "the publication behind this pool-day, per job"),
        QuerySpec("pool_reserves_history", "Reserves over time", reserves_history,
                  dict(params),
                  "raw balances; scaled to units only where decimals resolved",
                  exact_count=False),
    ]
    if not is_cl:
        return specs

    ranges = _ranges_cte("t.pool_address = {pool:String}", _asof_prune("t"))
    st_one = _st_cte("s.pool_address = {pool:String}")
    profile = sql_loader.load_sql(
        "pools", "pool_profile_at", asof_cte=asof_cte,
        cfg_cte=_cfg_cte("c.target_address = {pool:String}"), meta_cte=_meta_cte(),
        st_cte=st_one, ranges_cte=ranges,
        asset_decimals_sql=_asset_decimals("cfg.assets"),
        full_tick=FULL_RANGE_TICK,
    )
    concentration = sql_loader.load_sql(
        "pools", "pool_profile_concentration", asof_cte=asof_cte, st_cte=st_one,
        ranges_cte=ranges, band1=BAND_TICKS["1pct"], band5=BAND_TICKS["5pct"],
        band10=BAND_TICKS["10pct"], full_tick=FULL_RANGE_TICK,
    )
    ticks = sql_loader.load_sql(
        "pools", "pool_ticks_at", asof_cte=asof_cte, st_cte=st_one, db=POOLS_DB,
        view=TICK_VIEW, chain=CHAIN_ID, job=CL_JOB,
    )
    state_history = sql_loader.load_sql(
        "pools", "pool_state_history", asof_cte=asof_cte,
        cfg_cte=_cfg_cte("c.target_address = {pool:String}"), meta_cte=_meta_cte(),
        probe_days_cte=_probe_days_cte(),
        asset_decimals_sql=_asset_decimals("cfg.assets"),
        price_raw_sql=_price_raw("s"), db=POOLS_DB, view=STATE_VIEW,
        chain=CHAIN_ID, job=CL_JOB, window_state=_window_pred("s", days),
    )
    fee_growth = sql_loader.load_sql(
        "pools", "pool_fee_growth", asof_cte=asof_cte,
        cfg_cte=_cfg_cte("c.target_address = {pool:String}"), meta_cte=_meta_cte(),
        probe_days_cte=_probe_days_cte(),
        asset_decimals_sql=_asset_decimals("cfg.assets"), db=POOLS_DB,
        view=STATE_VIEW, chain=CHAIN_ID, job=CL_JOB,
        window_state=_window_pred("s", days),
    )
    heat_days = WINDOWS[heatmap_window]
    heatmap = sql_loader.load_sql(
        "pools", "pool_profile_heatmap", asof_cte=asof_cte,
        ranges_cte=_ranges_cte(
            "t.pool_address = {pool:String}", _heatmap_date_prune("t")
        ),
        pub=_PUB, db=POOLS_DB, view=STATE_VIEW, chain=CHAIN_ID, job=CL_JOB,
        check=BELOW_THRESHOLD_CHECK, window_pub=_window_pred("p", heat_days),
        max_dates=HEATMAP_MAX_DATES, tick_buckets=HEATMAP_TICK_BUCKETS,
        axis_pad=HEATMAP_AXIS_PAD_TICKS,
    )
    window_note = "all published days" if days <= 0 else f"latest {days} days"
    heat_note = (
        "all probed days" if heat_days <= 0 else f"latest {heat_days} days"
    )
    specs.extend([
        QuerySpec("pool_profile_at", "Liquidity profile", profile, dict(params),
                  f"recomputed from initialized ticks, {as_of_note}"),
        QuerySpec("pool_profile_concentration", "Concentration",
                  concentration, dict(params),
                  "tick-weighted share of this pool's liquidity per price band"),
        QuerySpec("pool_ticks_at", "Initialized ticks", ticks, dict(params),
                  as_of_note),
        QuerySpec("pool_state_history", "State over time", state_history,
                  dict(params), window_note, exact_count=False),
        QuerySpec("pool_fee_growth", "Fee accrual", fee_growth, dict(params),
                  f"{window_note}; estimated from fee-growth deltas times "
                  "in-range liquidity, NULL where nothing can be measured",
                  exact_count=False),
        QuerySpec("pool_profile_heatmap", "Profile over time", heatmap,
                  dict(params),
                  f"{heat_note}, sampled to at most {HEATMAP_MAX_DATES} dates "
                  f"and {HEATMAP_TICK_BUCKETS} tick buckets", 3600,
                  exact_count=False),
    ])
    return specs


def _token_entity_specs(identifier: str, as_of: str) -> list[QuerySpec]:
    asof_cte = _asof_cte(as_of)
    params: dict[str, Any] = {"token": identifier}
    if as_of:
        params["as_of"] = as_of
    detail = sql_loader.load_sql(
        "pools", "token_detail", asof_cte=asof_cte,
        reserves_asof_cte=_reserves_asof_cte(), cfg_cte=_cfg_cte(),
        st_cte=_st_cte(), probe_cte=_probe_cte(), res_cte=_res_cte(),
        db=POOLS_DB, meta_view=METADATA_VIEW, chain=CHAIN_ID,
    )
    pools = sql_loader.load_sql(
        "pools", "token_pools", asof_cte=asof_cte,
        reserves_asof_cte=_reserves_asof_cte(), cfg_cte=_cfg_cte(),
        meta_cte=_meta_cte(), st_cte=_st_cte(), probe_cte=_probe_cte(),
        res_cte=_res_cte(), asset_symbols_sql=_asset_symbols("cfg.assets"),
        asset_decimals_sql=_asset_decimals("cfg.assets"),
        price_raw_sql=_price_raw("st"), fee_band_sql=_fee_band("fee"),
    )
    return [
        QuerySpec("token_detail", "Token", detail, dict(params),
                  "every configured pool holding this token"),
        QuerySpec("token_pools", "Pools holding it", pools, dict(params),
                  "ranked by share of this token's own observed reserves — one "
                  "unit throughout, and NOT a value ranking"),
    ]


def _section_specs(
    section: str,
    as_of: str,
    window: str,
    filters: dict[str, Any],
) -> list[QuerySpec]:
    if section == "overview":
        return _overview_specs(as_of, window)
    if section == "pools":
        return _pools_specs(as_of, filters)
    if section == "tokens":
        return _tokens_specs(as_of, filters)
    if section == "coverage":
        return _coverage_specs(as_of, window)
    raise ValueError(f"Unsupported section: {section}")


def _entity_specs(
    kind: str,
    identifier: str,
    as_of: str,
    window: str,
    heatmap_window: str,
    is_cl: bool = True,
) -> list[QuerySpec]:
    if kind == "pool":
        return _pool_entity_specs(identifier, as_of, window, heatmap_window, is_cl)
    if kind == "token":
        return _token_entity_specs(identifier, as_of)
    raise ValueError(f"Unsupported entity type: {kind}")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _iso_value(value: Any) -> Any:
    if isinstance(value, datetime):
        anchored = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return anchored.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _coverage_from_dataset(
    dataset: CachedDataset,
    spec: QuerySpec,
    range_state: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    warning_codes: list[str] = []
    if not dataset.rows:
        warning_codes.append("no_data")
    if dataset.stats.truncated:
        warning_codes.append("result_truncated")
    resolved = _dataset_as_of(dataset)
    requested = str(range_state.get("as_of") or "")
    if requested and resolved and resolved != requested:
        warning_codes.append("as_of_shifted")
    coverage = {
        "basis": spec.basis,
        "source_kind": "rpc_state_indexer",
        "source_label": SOURCE_LABEL,
        "requested_as_of": requested or None,
        "resolved_as_of": resolved or None,
        "window": range_state.get("window"),
        "fetched_at": dataset.stats.fetched_at,
        "returned_rows": dataset.stats.rows_returned,
        "source_rows": dataset.stats.source_rows,
        "row_cap": dataset.stats.row_cap,
        "truncated": bool(dataset.stats.truncated),
        "warning_codes": warning_codes,
    }
    return coverage, warning_codes


def _dataset_as_of(dataset: CachedDataset) -> str:
    """The as-of a dataset actually resolved to, when it carries one."""
    columns = {name: idx for idx, name in enumerate(dataset.columns)}
    idx = columns.get("as_of")
    if idx is None or not dataset.rows or idx >= len(dataset.rows[0]):
        return ""
    return str(_iso_value(dataset.rows[0][idx]) or "")


_FAILURE_CACHE = FailureCache(POOLS_DB)


def reset_failure_cache_for_tests() -> None:
    _FAILURE_CACHE.reset()


def _log_dataset_loaded(
    spec: QuerySpec,
    dataset: CachedDataset,
    range_state: dict[str, Any],
    elapsed: float,
) -> None:
    logger.info(
        "pools_dataset key=%s as_of=%s window=%s rows=%s truncated=%s elapsed=%.3f",
        spec.key, range_state.get("as_of") or "latest", range_state.get("window"),
        dataset.stats.rows_returned, dataset.stats.truncated, elapsed,
    )


def _log_dataset_failed(spec: QuerySpec, error: Exception) -> None:
    logger.warning("pools dataset %s unavailable: %s", spec.key, error)


def _failure_coverage(
    spec: QuerySpec,
    range_state: dict[str, Any],
    fetched_at: str,
    error: str,
) -> dict[str, Any]:
    return {
        "basis": spec.basis,
        "source_kind": "rpc_state_indexer",
        "source_label": SOURCE_LABEL,
        "requested_as_of": range_state.get("as_of") or None,
        "resolved_as_of": None,
        "window": range_state.get("window"),
        "fetched_at": fetched_at,
        "returned_rows": 0,
        "source_rows": None,
        "row_cap": ROW_CAP,
        "truncated": False,
        "warning_codes": ["query_failed"],
        # The frontend renders an explicit error card from this — a failed
        # dataset must stay VISIBLE, never silently vanish.
        "error": error[:400],
    }


def _load_specs_safe(
    ch: ClickHouseManager,
    specs: list[QuerySpec],
    range_state: dict[str, Any],
    *,
    force_refresh: bool,
) -> tuple[dict[str, CachedDataset], dict[str, Any], list[str]]:
    return mini_apps.load_specs_safe(
        ch,
        specs,
        range_state,
        force_refresh=force_refresh,
        database=POOLS_DB,
        row_cap=ROW_CAP,
        failure_cache=_FAILURE_CACHE,
        coverage_fn=_coverage_from_dataset,
        failure_coverage_fn=_failure_coverage,
        worker_limit=3,
        thread_name_prefix="pools-data",
        log_success=_log_dataset_loaded,
        log_failure=_log_dataset_failed,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date | None:
    """Parse a snapshot date out of a dataset cell.

    Every date column on this plane is emitted as an ISO string (see the module
    docstring), but the driver also hands back real dates and, for a
    constant-folded projection, raw day numbers — so all three are accepted
    rather than assumed away.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return date(1970, 1, 1) + timedelta(days=value)
    if isinstance(value, str) and DATE_RE.fullmatch(value.strip()):
        return date.fromisoformat(value.strip())
    return None

def _empty_loaded_groups() -> dict[str, Any]:
    return {
        f"{section}.{group}": False
        for section, groups in SECTION_GROUPS.items()
        for group in groups
    }


def _empty_freshness() -> dict[str, Any]:
    return {
        "cl_state": {"latest_snapshot_date": None, "anchor_block": None,
                     "pools_published": None, "stale": False},
        "reserves": {"latest_snapshot_date": None, "anchor_block": None,
                     "pools_published": None, "stale": False},
    }


def _empty_state(section: str = "overview") -> dict[str, Any]:
    return {
        "section": section,
        "title": POOLS_TITLE,
        "as_of": "",
        "window": DEFAULT_WINDOW,
        "heatmap_window": DEFAULT_HEATMAP_WINDOW,
        "filters": _default_filters(),
        "selected_entity": None,
        "breadcrumbs": [],
        "search": {"query": "", "candidates": []},
        "applied_request_id": 0,
        "scope_id": f"{section}:0",
        "coverage": {},
        "coverage_warnings": [],
        "warnings": [],
        "dataset_revisions": {},
        "loaded_groups": _empty_loaded_groups(),
        "section_fingerprints": {},
        "section_datasets": {},
        "section_lru": [],
        "freshness": _empty_freshness(),
        # Metadata the indexer never catalogued, read live over RPC and patched
        # in by load_pools_token_metadata. Kept SEPARATE from the dataset
        # columns on purpose: an indexer value is verified at a pinned finalized
        # block with a publication behind it, an RPC value is current chain
        # state with neither, and the UI has to be able to say which is which.
        "token_overlay": {},
        "token_overlay_stats": {},
    }


def _section_fingerprint(
    section: str,
    as_of: str,
    window: str,
    filters: dict[str, Any],
    identifier: str = "",
) -> str:
    """Deterministic identity of a load scope, so a tab return with unchanged
    filters short-circuits with zero ClickHouse round trips."""
    return ":".join([
        section, as_of, window, identifier,
        str(filters.get("query", "")),
        str(filters.get("pool_class", "")),
        str(filters.get("pool_family", "")),
        str(filters.get("fee_band", "")),
        str(filters.get("fee", 0)),
        str(filters.get("token", "")),
        str(filters.get("live_only", False)),
        str(filters.get("probed_only", False)),
        str(filters.get("sort_by", "")),
    ])


def _touch_section_lru(view_id: str, state: dict[str, Any], keep_section: str) -> None:
    mini_apps.touch_section_lru(
        view_id, state, keep_section,
        section_groups=SECTION_GROUPS,
        max_retained=MAX_RETAINED_SECTIONS,
        protected_keys=("source_freshness",),
    )


def _freshness_state(
    datasets: dict[str, CachedDataset],
) -> tuple[dict[str, Any], list[str]]:
    """Parse the per-job freshness rows into view state. ``stale`` means the
    newest publication is more than STALE_DAYS old, which on a daily cadence
    reads as a missed run rather than normal lag."""
    freshness = _empty_freshness()
    warnings: list[str] = []
    dataset = datasets.get("source_freshness")
    if dataset is None or not dataset.rows:
        return freshness, warnings
    columns = {name: idx for idx, name in enumerate(dataset.columns)}
    if "source" not in columns:
        return freshness, warnings
    today = datetime.now(timezone.utc).date()
    for row in dataset.rows:
        source = str(row[columns["source"]] or "")
        if source not in freshness:
            continue

        def cell(name: str) -> Any:
            idx = columns.get(name)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        latest = cell("latest_snapshot_date")
        stale = False
        parsed = _as_date(latest)
        if parsed is not None:
            stale = (today - parsed).days > STALE_DAYS
        freshness[source] = {
            "latest_snapshot_date": _iso_value(latest),
            "anchor_block": cell("latest_anchor_block"),
            "pools_published": cell("pools_published"),
            "stale": stale,
        }
        if stale:
            warnings.append("source_stale")
    return freshness, list(dict.fromkeys(warnings))


_dataset_titles = mini_apps.dataset_titles


def _summary_cards(record: mini_apps.ViewRecord) -> list[SummaryCard]:
    state = record.view_state
    cards = [
        SummaryCard(label="Section",
                    value=str(state.get("section") or "overview").title()),
        SummaryCard(label="As of", value=str(state.get("as_of") or "Latest")),
    ]
    for key, label in (
        ("pool_directory", "Pools"), ("token_directory", "Tokens"),
        ("pool_profile_at", "Ranges"),
    ):
        dataset = record.datasets.get(key)
        if dataset is not None:
            cards.append(SummaryCard(
                label=label,
                value=f"{dataset.stats.source_rows or dataset.stats.row_count:,}",
            ))
    return cards[:5]


def _payload_from_record(
    record: mini_apps.ViewRecord,
    titles: dict[str, str] | None = None,
) -> MiniAppPayload:
    return mini_apps.payload_from_record(
        record, app_id=POOLS_APP_ID, database=POOLS_DB,
        summary_cards=_summary_cards, titles=titles,
    )


def _entity_warnings(kind: str, datasets: dict[str, CachedDataset]) -> list[str]:
    """Coverage facts about the entity itself, surfaced as codes the UI turns
    into badges: a pool whose ticks were never probed has no profile, and a
    pool whose token decimals are unknown has no adjusted price. Neither is an
    error, and neither may be left for the reader to infer from an empty panel.
    """
    codes: list[str] = []
    if kind != "pool":
        return codes
    dataset = datasets.get("pool_detail")
    if dataset is None or not dataset.rows:
        return codes
    columns = {name: idx for idx, name in enumerate(dataset.columns)}
    row = dataset.rows[0]

    def cell(name: str) -> Any:
        idx = columns.get(name)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    if str(cell("pool_family") or "") == "reserves_only":
        codes.append("reserves_only_pool")
    elif not cell("ticks_probed"):
        codes.append("pool_below_active_threshold")
    if cell("price_adjusted") is None:
        codes.append("metadata_unresolved")
    return codes


# ---------------------------------------------------------------------------
# Appliers
# ---------------------------------------------------------------------------


def _visible_datasets(record: mini_apps.ViewRecord) -> dict[str, CachedDataset]:
    """The datasets of the section being LOOKED AT, not every dataset retained.

    The view keeps several sections cached, so a pool drill-down opened after
    the directory still holds 4,022 directory rows. Feeding all of them to a
    capped token sweep spends the whole budget on rows nobody is looking at and
    leaves the open pool's own two tokens unresolved — which is exactly what it
    did before this narrowed.
    """
    state = record.view_state or {}
    section = str(state.get("section") or "")
    keys = (state.get("section_datasets") or {}).get(section) or []
    scoped = {key: record.datasets[key] for key in keys if key in record.datasets}
    return scoped or record.datasets


def _build_token_overlay(
    record: mini_apps.ViewRecord, *, force_refresh: bool = False
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Resolve the tokens visible in the view that the indexer has no metadata
    for, and return ``(overlay, stats, warnings)``.

    Every token in the current section is offered to the resolver, not just the
    ones its datasets show as unresolved: the resolver is cache-first, so an
    already-known token costs nothing, and deciding "unresolved" here would mean
    re-deriving it from whichever columns each dataset happens to carry.

    A token that does not answer is absent from the overlay rather than present
    with an empty symbol, so the client keeps rendering a short address for it.
    """
    addresses = coingecko.dataset_token_addresses(
        _visible_datasets(record), token_columns=TOKEN_COLUMN_RE,
        cap_per_chain=TOKEN_OVERLAY_CAP, default_chain_id=CHAIN_ID,
    ).get(CHAIN_ID, set())
    if not addresses:
        return {}, {"requested": 0, "resolved": 0}, []
    resolved, stats = token_rpc.resolve_tokens(
        CHAIN_ID, addresses, force_refresh=force_refresh
    )
    overlay = {address: meta.as_overlay() for address, meta in resolved.items()}
    warnings: list[str] = []
    if stats.error:
        warnings.append("token_rpc_unavailable")
    elif stats.truncated:
        warnings.append("token_overlay_pending")
    return overlay, {
        "requested": stats.requested,
        "resolved": len(overlay),
        "from_cache": stats.from_cache,
        "fetched": stats.fetched,
        "unreadable": stats.unreadable,
        "truncated": stats.truncated,
        "block_number": stats.block_number or None,
        "error": stats.error or None,
        "source": "rpc",
    }, warnings

def _apply_section_load(
    ch: ClickHouseManager,
    view_id: str,
    request_id: int,
    section: str,
    query: str,
    pool_class: str,
    pool_family: str,
    fee_band: str,
    fee: int,
    token: str,
    live_only: bool,
    probed_only: bool,
    sort_by: str,
    as_of: str,
    window: str,
    force_refresh: bool,
) -> tuple[MiniAppPayload, str]:
    """Apply a section scope: validate, evict stale data, load the CORE group.

    Non-core groups are deliberately NOT loaded here — the frontend streams
    them afterwards while skeletons show. A fingerprint match returns the
    retained datasets with zero queries.
    """
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    current = dict(record.view_state)
    if request_id < int(current.get("applied_request_id") or 0):
        return _payload_from_record(record), "Ignored stale Pool Explorer request."
    section_key = section.strip().lower()
    if section_key in ENTITY_SECTIONS:
        raise ValueError(
            f"section '{section_key}' is an entity drill-down — use "
            "load_pools_explorer_entity"
        )
    if section_key not in VALID_SECTIONS:
        raise ValueError(f"section must be one of {sorted(VALID_SECTIONS)}")
    resolved_as_of = _validate_as_of(as_of)
    resolved_window = _validate_window(window)
    filters = _validate_filters(
        section_key, query, pool_class, pool_family, fee_band, fee, token,
        live_only, probed_only, sort_by,
    )
    fingerprint = _section_fingerprint(
        section_key, resolved_as_of, resolved_window, filters
    )
    stored_fingerprints = dict(current.get("section_fingerprints") or {})
    core_loaded = bool((current.get("loaded_groups") or {}).get(f"{section_key}.core"))
    if (
        not force_refresh
        and stored_fingerprints.get(section_key) == fingerprint
        and core_loaded
    ):
        next_state = {
            **current,
            "section": section_key,
            "as_of": resolved_as_of,
            "window": resolved_window,
            "filters": filters,
            "selected_entity": None,
            "applied_request_id": int(request_id),
        }
        _touch_section_lru(view_id, next_state, section_key)
        mini_apps.set_view_state(view_id, next_state)
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return (
            _payload_from_record(updated),
            f"Pools {section_key} restored from retained datasets.",
        )
    range_state = {"as_of": resolved_as_of, "window": resolved_window}
    specs = _section_specs(section_key, resolved_as_of, resolved_window, filters)
    core_keys = SECTION_GROUPS[section_key]["core"]
    core_specs = [spec for spec in specs if spec.key in core_keys]
    if "source_freshness" not in core_keys:
        core_specs.append(_source_freshness_spec())
    datasets, coverage, load_warnings = _load_specs_safe(
        ch, core_specs, range_state, force_refresh=force_refresh
    )
    freshness, freshness_warnings = _freshness_state(datasets)
    warnings = list(dict.fromkeys([*load_warnings, *freshness_warnings]))
    scope_id = f"{section_key}:{request_id}"
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
    titles.update({spec.key: spec.title for spec in core_specs})
    next_state = {
        **current,
        "section": section_key,
        "as_of": resolved_as_of,
        "window": resolved_window,
        "filters": filters,
        "selected_entity": None,
        "breadcrumbs": [],
        "applied_request_id": int(request_id),
        "scope_id": scope_id,
        "coverage": {**(current.get("coverage") or {}), **coverage},
        "coverage_warnings": [w for w in warnings if " " not in w],
        "warnings": warnings,
        "loaded_groups": loaded_groups,
        "dataset_titles": titles,
        "freshness": freshness,
    }
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
    return _payload_from_record(updated, titles), f"Pools {section_key} loaded."


def _pool_family_of(state: dict[str, Any], record: mini_apps.ViewRecord) -> bool:
    """Whether the selected pool carries concentrated-liquidity data.

    Read from the loaded detail row rather than re-queried: the entity load
    already knows, and asking again would cost a round trip to learn something
    the view is holding.
    """
    dataset = record.datasets.get("pool_detail")
    if dataset is None or not dataset.rows:
        return True
    columns = {name: idx for idx, name in enumerate(dataset.columns)}
    idx = columns.get("pool_family")
    if idx is None or idx >= len(dataset.rows[0]):
        return True
    return str(dataset.rows[0][idx] or "") != "reserves_only"


def _apply_group_load(
    ch: ClickHouseManager,
    view_id: str,
    section: str,
    group: str,
    scope_id: str,
    as_of: str,
    heatmap_window: str,
    force_refresh: bool,
) -> tuple[MiniAppPayload, str]:
    """Load ONE deferred dataset group additively and return a PATCH payload.

    Group loads never bump ``applied_request_id`` — they are additive and
    order-independent. The ``scope_id`` guard makes a late-arriving load for a
    superseded scope a harmless no-op instead of a corruption.

    ``as_of`` re-dates the pool profile group in place: changing the profile
    date is one additive call, not a reload of the whole entity.
    """
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    state = dict(record.view_state)
    current_scope_id = str(state.get("scope_id") or "")
    if scope_id and scope_id != current_scope_id:
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE", view_id=view_id, app_id=POOLS_APP_ID,
            title=record.title, patch={}, warnings=["stale_scope"],
        )
        return payload, "Ignored stale pools group request."
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
    resolved_as_of = _validate_as_of(as_of) if as_of.strip() else str(
        state.get("as_of") or ""
    )
    resolved_window = str(state.get("window") or DEFAULT_WINDOW)
    resolved_heatmap = (
        _validate_window(heatmap_window, allowed=HEATMAP_WINDOWS)
        if heatmap_window.strip()
        else str(state.get("heatmap_window") or DEFAULT_HEATMAP_WINDOW)
    )
    filters = {**_default_filters(), **(state.get("filters") or {})}
    if section_key in ENTITY_SECTIONS:
        entity = state.get("selected_entity") or {}
        identifier = str(entity.get("identifier") or "")
        if not identifier:
            raise ValueError("no entity selected — load the entity first")
        specs = _entity_specs(
            section_key, identifier, resolved_as_of, resolved_window,
            resolved_heatmap, _pool_family_of(state, record),
        )
    else:
        specs = _section_specs(section_key, resolved_as_of, resolved_window, filters)
    group_specs = [spec for spec in specs if spec.key in group_keys]
    if "source_freshness" not in group_keys:
        group_specs.append(_source_freshness_spec())
    range_state = {"as_of": resolved_as_of, "window": resolved_window}
    datasets, coverage, load_warnings = _load_specs_safe(
        ch, group_specs, range_state, force_refresh=force_refresh
    )
    freshness, freshness_warnings = _freshness_state(datasets)
    load_warnings = list(dict.fromkeys([*load_warnings, *freshness_warnings]))
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
        *(state.get("warnings") or []), *load_warnings,
    ]))
    group_failed = any(
        "query_failed" in (coverage.get(k, {}).get("warning_codes") or [])
        for k in group_keys
    )
    patch: dict[str, Any] = {
        "loaded_groups": {
            f"{section_key}.{group_key}": "partial" if group_failed else True
        },
        "coverage": coverage,
        "dataset_revisions": {
            key: updated.dataset_revisions.get(key, 0) for key in datasets
        },
        "section_datasets": {section_key: tracked},
        "dataset_titles": {spec.key: spec.title for spec in group_specs},
        "warnings": combined_warnings,
        "coverage_warnings": [w for w in combined_warnings if " " not in w],
        "freshness": freshness,
        "as_of": resolved_as_of,
        "heatmap_window": resolved_heatmap,
    }
    mini_apps.patch_view_state(view_id, patch)
    descriptors = {
        key: mini_apps.build_dataset_descriptor(
            key=key, dataset=dataset,
            title=titles.get(key, key.replace("_", " ").title()),
            scope_id=current_scope_id,
            provenance={"source": POOLS_DB, "coverage": coverage.get(key, {})},
        )
        for key, dataset in datasets.items()
    }
    payload = MiniAppPayload(
        type="PATCH_VIEW_STATE", view_id=view_id, app_id=POOLS_APP_ID,
        title=record.title, datasets=descriptors, patch=patch,
        warnings=load_warnings,
    )
    return payload, f"Pools {section_key}.{group_key} loaded."


_ENTITY_DETAIL_KEY = {"pool": "pool_detail", "token": "token_detail"}


def _entity_label(kind: str, identifier: str, datasets: dict[str, CachedDataset]) -> str:
    """The breadcrumb label. Composed server-side from the protocol class and
    the address, NEVER from a token symbol: breadcrumbs render raw and symbols
    on this chain are attacker-authored."""
    dataset = datasets.get(_ENTITY_DETAIL_KEY[kind])
    if dataset is not None and dataset.rows:
        columns = {name: idx for idx, name in enumerate(dataset.columns)}
        idx = columns.get("entity_label")
        if idx is not None and idx < len(dataset.rows[0]):
            value = str(dataset.rows[0][idx] or "").strip()
            if value:
                return value
    return identifier


def _apply_entity_load(
    ch: ClickHouseManager,
    view_id: str,
    request_id: int,
    entity_type: str,
    identifier: str,
    as_of: str = "",
    window: str = "",
    force_refresh: bool = False,
) -> tuple[MiniAppPayload, str]:
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    current = dict(record.view_state)
    if request_id < int(current.get("applied_request_id") or 0):
        return _payload_from_record(record), "Ignored stale pools entity request."
    kind = entity_type.strip().lower()
    normalized_id = _validate_entity_identifier(kind, identifier)
    resolved_as_of = _validate_as_of(as_of)
    resolved_window = _validate_window(window)
    heatmap_window = str(current.get("heatmap_window") or DEFAULT_HEATMAP_WINDOW)
    # The pool family is unknown until the detail row lands, so the CORE group
    # is built for a CL pool and the deferred groups then follow whatever the
    # detail said. Core carries no CL-only key, so nothing is wasted.
    specs = _entity_specs(
        kind, normalized_id, resolved_as_of, resolved_window, heatmap_window, True
    )
    core_keys = SECTION_GROUPS[kind]["core"]
    core_specs = [spec for spec in specs if spec.key in core_keys]
    core_specs.append(_source_freshness_spec())
    range_state = {"as_of": resolved_as_of, "window": resolved_window}
    datasets, coverage, load_warnings = _load_specs_safe(
        ch, core_specs, range_state, force_refresh=force_refresh
    )
    freshness, freshness_warnings = _freshness_state(datasets)
    warnings = list(dict.fromkeys([
        *load_warnings, *freshness_warnings, *_entity_warnings(kind, datasets),
    ]))
    scope_id = f"{kind}:{normalized_id}:{request_id}"
    label = _entity_label(kind, normalized_id, datasets)
    breadcrumb = {
        "label": label[:80], "entity_type": kind, "identifier": normalized_id,
    }
    breadcrumbs = (
        list(current.get("breadcrumbs") or [])
        if current.get("section") in ENTITY_SECTIONS
        else []
    )
    existing_index = next(
        (
            index for index, item in enumerate(breadcrumbs)
            if item.get("entity_type") == kind
            and item.get("identifier") == normalized_id
        ),
        None,
    )
    if existing_index is None:
        breadcrumbs.append(breadcrumb)
    else:
        breadcrumbs = breadcrumbs[: existing_index + 1]
    breadcrumbs = breadcrumbs[-8:]
    core_failed = any(
        "query_failed" in (coverage.get(k, {}).get("warning_codes") or [])
        for k in core_keys
    )
    loaded_groups = dict(current.get("loaded_groups") or _empty_loaded_groups())
    for group in SECTION_GROUPS[kind]:
        if group == "core":
            loaded_groups[f"{kind}.{group}"] = "partial" if core_failed else True
        else:
            loaded_groups[f"{kind}.{group}"] = False
    titles = dict(current.get("dataset_titles") or {})
    titles.update(_dataset_titles(specs))
    next_state = {
        **current,
        "section": kind,
        "as_of": resolved_as_of,
        "window": resolved_window,
        "selected_entity": {
            "entity_type": kind, "identifier": normalized_id, "label": label,
        },
        "breadcrumbs": breadcrumbs,
        "search": {"query": normalized_id, "candidates": []},
        "applied_request_id": int(request_id),
        "scope_id": scope_id,
        "coverage": {**(current.get("coverage") or {}), **coverage},
        "coverage_warnings": [w for w in warnings if " " not in w],
        "warnings": warnings,
        "loaded_groups": loaded_groups,
        "dataset_titles": titles,
        "freshness": freshness,
    }
    previous_keys = list((current.get("section_datasets") or {}).get(kind, []) or [])
    stale = [key for key in previous_keys if key not in datasets]
    if stale:
        mini_apps.remove_view_datasets(view_id, stale)
    for key, dataset in datasets.items():
        mini_apps.attach_dataset(view_id, key, dataset)
    section_datasets = dict(current.get("section_datasets") or {})
    section_datasets[kind] = sorted(datasets)
    next_state["section_datasets"] = section_datasets
    fingerprints = dict(current.get("section_fingerprints") or {})
    fingerprints[kind] = _section_fingerprint(
        kind, resolved_as_of, resolved_window, _default_filters(), normalized_id
    )
    next_state["section_fingerprints"] = fingerprints
    _touch_section_lru(view_id, next_state, kind)
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    next_state["dataset_revisions"] = dict(updated.dataset_revisions)
    mini_apps.set_view_state(view_id, next_state)
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    return _payload_from_record(updated, titles), f"Loaded {kind} detail."


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _search_candidates(ch: ClickHouseManager, query: str) -> list[dict[str, Any]]:
    """Classify a query into typed arms and return ranked candidates.

    Every arm reads the config registry and the metadata view only — never a
    pool view — so a search costs one small scan however much history the plane
    holds.
    """
    q = query.strip()
    if len(q) > MAX_QUERY_LENGTH:
        raise ValueError(f"Search query must be at most {MAX_QUERY_LENGTH} characters")
    if not q:
        return []
    lowered = q.lower()
    params: dict[str, Any] = {}
    cfg = _cfg_cte()
    if ADDRESS_RE.fullmatch(lowered):
        params["q"] = lowered
        sql = sql_loader.load_sql("pools", "search_address", cfg_cte=cfg)
    elif HEX_PREFIX_RE.fullmatch(lowered):
        params["q"] = lowered
        sql = sql_loader.load_sql("pools", "search_prefix", cfg_cte=cfg)
    else:
        params["q"] = q
        sql = sql_loader.load_sql(
            "pools", "search_text", cfg_cte=cfg, db=POOLS_DB,
            meta_view=METADATA_VIEW, chain=CHAIN_ID,
        )
    result = mini_apps.run_structured_query(
        ch, sql, POOLS_DB, params, requested_max_rows=100,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )
    columns = {name: idx for idx, name in enumerate(result.columns)}
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for row in result.rows:
        rank = int(row[columns["match_rank"]]) if "match_rank" in columns else 0
        evidence = (
            int(row[columns["evidence_count"]]) if "evidence_count" in columns else 0
        )
        ranked.append((rank, -evidence, {
            "entity_type": str(row[columns["entity_type"]]),
            "identifier": str(row[columns["identifier"]]),
            "label": str(row[columns["label"]]) if "label" in columns else "",
            "role": str(row[columns["role"]]) if "role" in columns else "",
            "evidence_count": evidence,
        }))
    ranked.sort(
        key=lambda item: (
            item[0], item[1], item[2]["entity_type"], item[2]["identifier"]
        )
    )
    return [candidate for _, _, candidate in ranked[:SEARCH_CANDIDATE_CAP]]


# ---------------------------------------------------------------------------
# Tools + registrar
# ---------------------------------------------------------------------------


def register_pools_explorer_tools(mcp, ch: ClickHouseManager) -> None:
    """Register the Pool Liquidity Explorer resource, tools, and web app."""
    mini_apps.register_app(POOLS_APP_ID, title=POOLS_TITLE, resource_uri=POOLS_URI)

    @mcp.resource(POOLS_URI, mime_type="text/html;profile=mcp-app")
    def serve_pools_explorer_app() -> str:
        return get_pools_explorer_html()

    @mcp.tool(meta=POOLS_APP_META)
    def open_pools_explorer(
        section: str = "overview",
        query: str = "",
        entity_type: str = "",
        identifier: str = "",
        pool_class: str = "",
        pool_family: str = "",
        fee_band: str = "",
        token: str = "",
        live_only: bool = False,
        probed_only: bool = False,
        as_of: str = "",
        window: str = "",
    ) -> CallToolResult:
        """Open the read-only Pool Liquidity Explorer over ``rpc_state_indexer``.

        Covers Gnosis Chain DEX pools as the indexer verified them daily at
        pinned finalized blocks: concentrated-liquidity state and tick-level
        liquidity profiles for Uniswap v3 / Swapr v3 pools, and raw token
        reserves for those plus Balancer v2/v3. Prices are raw token1-per-token0
        unless both tokens' decimals resolved, and there is NO USD valuation on
        this plane. Pass ``query`` to resolve a pool address, a token address or
        prefix, or a token symbol; or ``entity_type`` + ``identifier`` directly.
        """
        try:
            section_key = section.strip().lower() or "overview"
            if section_key not in VALID_SECTIONS:
                raise ValueError(f"section must be one of {sorted(VALID_SECTIONS)}")
            if section_key in ENTITY_SECTIONS and not identifier.strip():
                raise ValueError(
                    f"section '{section_key}' needs an identifier"
                )
            resolved_as_of = _validate_as_of(as_of)
            resolved_window = _validate_window(window)
            filter_section = section_key if section_key == "pools" else "pools"
            filters = _validate_filters(
                filter_section, "", pool_class, pool_family, fee_band, 0, token,
                live_only, probed_only, "",
            )
            view_id = mini_apps.create_view(POOLS_APP_ID, POOLS_TITLE)
            state = _empty_state(
                section_key if section_key not in ENTITY_SECTIONS else "overview"
            )
            state["as_of"] = resolved_as_of
            state["window"] = resolved_window
            state["filters"] = filters
            mini_apps.set_view_state(view_id, state)
            if entity_type.strip() or identifier.strip():
                kind = entity_type.strip() or section_key
                if kind not in ENTITY_TYPES or not identifier.strip():
                    raise ValueError(
                        "entity_type and identifier must be provided together"
                    )
                payload, summary = _apply_entity_load(
                    ch, view_id, 0, kind, identifier, resolved_as_of, resolved_window
                )
            elif query.strip():
                candidates = _search_candidates(ch, query)
                if len(candidates) == 1:
                    candidate = candidates[0]
                    payload, summary = _apply_entity_load(
                        ch, view_id, 0, candidate["entity_type"],
                        candidate["identifier"], resolved_as_of, resolved_window,
                    )
                else:
                    record = mini_apps.get_view(view_id)
                    assert record is not None
                    mini_apps.set_view_state(view_id, {
                        **record.view_state,
                        "search": {"query": query.strip(), "candidates": candidates},
                        "warnings": ([] if candidates else ["no_indexed_data"]),
                    })
                    record = mini_apps.get_view(view_id)
                    assert record is not None
                    payload = _payload_from_record(record)
                    summary = f"Pool search returned {len(candidates)} candidate(s)."
            else:
                # Default path: zero ClickHouse queries — every dataset defers.
                record = mini_apps.get_view(view_id)
                assert record is not None
                payload = _payload_from_record(record)
                summary = (
                    f"Pool Liquidity Explorer opened on {section_key} — datasets "
                    "load in the app (deferred groups)."
                )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_pools_explorer_section(
        view_id: str,
        request_id: int,
        section: str,
        query: str = "",
        pool_class: str = "",
        pool_family: str = "",
        fee_band: str = "",
        fee: int = 0,
        token: str = "",
        live_only: bool = False,
        probed_only: bool = False,
        sort_by: str = "",
        as_of: str = "",
        window: str = "",
        force_refresh: bool = False,
    ) -> CallToolResult:
        """[App-only] Atomically load one Pool Explorer section."""
        try:
            payload, summary = _apply_section_load(
                ch, view_id, request_id, section, query, pool_class, pool_family,
                fee_band, fee, token, live_only, probed_only, sort_by, as_of,
                window, force_refresh,
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_pools_explorer_datasets(
        view_id: str,
        request_id: int,
        section: str,
        group: str,
        scope_id: str = "",
        as_of: str = "",
        heatmap_window: str = "",
        force_refresh: bool = False,
    ) -> CallToolResult:
        """[App-only] Load one deferred pool dataset group (additive).

        ``as_of`` re-dates the group in place — this is how the profile date
        picker works, without reloading the rest of the entity.
        ``heatmap_window`` picks the span the profile-over-time grid covers.
        """
        try:
            payload, summary = _apply_group_load(
                ch, view_id, section, group, scope_id, as_of, heatmap_window,
                force_refresh,
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def search_pools_explorer(
        view_id: str,
        request_id: int,
        query: str,
    ) -> CallToolResult:
        """[App-only] Resolve a pool address, token address/prefix, or symbol."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        if not query.strip():
            return mini_apps.error_call_tool_result("query is required")
        try:
            if request_id < int(record.view_state.get("applied_request_id") or 0):
                return mini_apps.payload_to_call_tool_result(
                    _payload_from_record(record), "Ignored stale pool search request."
                )
            candidates = _search_candidates(ch, query)
            if len(candidates) == 1:
                candidate = candidates[0]
                payload, summary = _apply_entity_load(
                    ch, view_id, request_id, candidate["entity_type"],
                    candidate["identifier"],
                    str(record.view_state.get("as_of") or ""),
                    str(record.view_state.get("window") or DEFAULT_WINDOW),
                )
                return mini_apps.payload_to_call_tool_result(payload, summary)
            patch = {
                "search": {"query": query.strip(), "candidates": candidates},
                "applied_request_id": int(request_id),
            }
            mini_apps.patch_view_state(view_id, patch)
            payload = MiniAppPayload(
                type="PATCH_VIEW_STATE", view_id=view_id, app_id=POOLS_APP_ID,
                title=record.title, patch=patch,
                warnings=[] if candidates else ["no_indexed_data"],
            )
            return mini_apps.payload_to_call_tool_result(
                payload, f"Pool search returned {len(candidates)} candidate(s)."
            )
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_pools_explorer_entity(
        view_id: str,
        request_id: int,
        entity_type: str,
        identifier: str,
        as_of: str = "",
        window: str = "",
        force_refresh: bool = False,
    ) -> CallToolResult:
        """[App-only] Load a resolved pool or token drill-down."""
        try:
            payload, summary = _apply_entity_load(
                ch, view_id, request_id, entity_type, identifier, as_of, window,
                force_refresh,
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_pools_token_metadata(
        view_id: str,
        request_id: int = 0,
        force_refresh: bool = False,
    ) -> CallToolResult:
        """[App-only] Read symbol/decimals for the view's tokens over RPC.

        The state indexer only catalogues tokens inside a census job's own
        universe, and the pool jobs sweep pool addresses rather than their
        assets — so it has metadata for 68 of the 3,400 tokens these pools hold.
        This fills the rest in from the chain.

        The values arrive as a SEPARATE overlay keyed by address, each carrying
        its source and the block it was read at. They never overwrite a dataset
        column: an indexer value is verified at a pinned finalized block with a
        publication behind it, an RPC value is current chain state with neither,
        and the app has to keep saying which is which.

        Never blocks on the chain: whatever cannot be read comes back absent,
        with a warning, and the client keeps showing a short address.
        """
        try:
            record = mini_apps.get_view(view_id)
            if record is None:
                return mini_apps.error_call_tool_result(
                    f"Unknown or expired view_id: {view_id}"
                )
            overlay, stats, warnings = _build_token_overlay(
                record, force_refresh=force_refresh
            )
            patch = {"token_overlay": overlay, "token_overlay_stats": stats}
            mini_apps.patch_view_state(view_id, patch)
            payload = MiniAppPayload(
                type="PATCH_VIEW_STATE", view_id=view_id, app_id=POOLS_APP_ID,
                title=record.title, patch=patch, warnings=warnings,
            )
            summary = (
                f"Token metadata: {stats['resolved']} of {stats['requested']} "
                f"token(s) labelled from chain state"
            )
            if stats.get("block_number"):
                summary += f" at block {stats['block_number']}"
            return mini_apps.payload_to_call_tool_result(payload, summary + ".")
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    for name in (
        "load_pools_explorer_section", "load_pools_explorer_datasets",
        "search_pools_explorer", "load_pools_explorer_entity",
        "load_pools_token_metadata",
    ):
        mini_apps.mark_app_only(name)

    web_apps.register_web_app(
        app_id=POOLS_APP_ID,
        open_tool="open_pools_explorer",
        html_loader=get_pools_explorer_html,
        title=POOLS_TITLE,
        description=(
            "Explore Gnosis Chain DEX pool liquidity as the state indexer "
            "verified it daily: tick-level liquidity profiles for Uniswap v3 "
            "and Swapr v3 pools, raw reserves for those plus Balancer."
        ),
        icon="~",
        diagnostics_loader=get_pools_explorer_diagnostics,
        tools={
            "open_pools_explorer": open_pools_explorer,
            "load_pools_explorer_section": load_pools_explorer_section,
            "load_pools_explorer_datasets": load_pools_explorer_datasets,
            "search_pools_explorer": search_pools_explorer,
            "load_pools_explorer_entity": load_pools_explorer_entity,
            "load_pools_token_metadata": load_pools_token_metadata,
        },
    )


__all__ = [
    "POOLS_APP_ID", "POOLS_TITLE", "POOLS_URI", "POOLS_DB", "CHAIN_ID",
    "CL_JOB", "RESERVES_JOB", "SECTION_GROUPS", "CL_ONLY_KEYS", "QuerySpec",
    "get_pools_explorer_html", "get_pools_explorer_diagnostics",
    "register_pools_explorer_tools", "reset_failure_cache_for_tests",
    "_search_candidates",
]
