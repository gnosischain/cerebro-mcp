"""Governance Explorer mini app.

Read-only analyst surface over the ``governance_db`` ClickHouse database:
GnosisDAO Snapshot proposals/votes/followers plus the Discourse forum
(topics/posts/users/categories), the ``rpc_log_indexer`` DelegateRegistry
plane, and the ``rpc_state_indexer`` treasury plane. Snapshot content is
**off-chain signaling — never binding execution**, and there is still no
execution or spend-attribution data here: treasury coverage is token
*balances* at pinned finalized blocks, with no USD valuation.

Frozen contract (mirrored byte-for-byte by the frontend, test-enforced on
both sides):

* ``SECTION_GROUPS`` — every dataset key lives in exactly one group,
  globally unique across sections. Deliberate deviations from the original
  product spec: the overview concentration dataset is named
  ``voter_power_concentration`` (``voter_concentration`` belongs to
  voters.insights — dataset keys are global per view and LRU eviction removes
  by key, so sharing a key across two retained sections would corrupt
  retention), and ``forum_category_activity`` is added as a 5th
  overview-insights dataset (the Overview UI requires the chart the spec's
  dataset list omitted).
* ``start_at`` token encoding — ``load_governance_section`` has NO
  ``window_days`` parameter. ``start_at`` accepts ``""`` (with empty
  ``end_at``) = all history; ``"90d"`` / ``"1y"`` (``end_at`` must be empty)
  = relative presets anchored to ``now()`` UTC; an ISO-8601 pair = custom
  range (start-only/end-only rejected). Tokens keep the scope fingerprint
  deterministic so presets survive the zero-query tab return.
* Cross-source linking is two-tier: the author-declared ``discussion`` URL
  (exact topic id) is the PRIMARY proposal<->topic link (``link_source =
  'discussion'``); exact GIP-number equality is the secondary tier
  (``link_source = 'gip'``). No fuzzy/text joins, ever.
* **FINAL on every table read**: all eight ``governance_db`` tables are
  ``ReplacingMergeTree(ingested_at)`` and the daily ingesters re-insert rows
  routinely (all proposals, the whole user directory, every bumped topic
  with all its posts), so un-FINAL'd aggregates double-count until merges
  land. Every ``governance_db.<table>`` reference is followed by ``FINAL`` —
  no carve-outs (a lexical test enforces the rule).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import (
    INTERACTIVE_QUERY_BUDGET,
    ClickHouseManager,
)
from cerebro_mcp.models.mini_app import MiniAppPayload, SummaryCard
from cerebro_mcp.runtime.mini_app_cache import CachedDataset, FailureCache
from cerebro_mcp.tools.visualization import coingecko, mini_apps, sql_loader, web_apps

logger = logging.getLogger(__name__)

GOV_APP_ID = "governance"
GOV_TITLE = "Governance Explorer"
GOV_URI = "ui://cerebro/governance"
GOV_DB = "governance_db"
#: On-chain Snapshot DelegateRegistry plane (rpc-log-indexer output). The
#: ``v_delegate_events_gnosis`` view is already reorg-safe / checkpoint-bounded
#: (built on ``decoded_events_canonical``), so it is queried WITHOUT ``FINAL``.
#: Columns: environment, chain_id, action ('SetDelegate'|'ClearDelegate'),
#: delegator, id, delegate, block_timestamp, block_number, log_index, tx_hash.
#: The view now carries BOTH Ethereum mainnet (chain_id 1) and Gnosis Chain
#: (chain_id 100) events — the gnosis.eth space delegates on both. Delegation
#: is last-write-wins PER (chain_id, delegator): the same address can delegate
#: independently on each chain (Snapshot has one delegation strategy per net).
DELEGATE_DB = "rpc_log_indexer"
DELEGATE_VIEW = "v_delegate_events_gnosis"
#: Treasury plane (rpc-state-indexer output): verified ERC-20 balances for the
#: GnosisDAO wallet set, each pinned to an immutable finalized block. Every
#: figure carries ``anchor_block``/``anchor_hash`` — that attributability is the
#: whole point of this plane over a portfolio API.
#:
#: ``v_treasury_balances`` resolves ReplacingMergeTree dedup internally, so it is
#: queried WITHOUT ``FINAL`` (same as the delegate view).
#:
#: CRITICAL: the view is NOT job-scoped upstream — it spans every census job,
#: including the ``full_holders`` jobs whose universes contain the treasury
#: wallets (hundreds of thousands of rows per date, 185M+ overall). Every spec
#: here MUST pin ``job_name = TREASURY_JOB``; an unpinned read exhausts server
#: memory and double-counts any token measured by two jobs.
TREASURY_DB = "rpc_state_indexer"
TREASURY_VIEW = "v_treasury_balances"
TREASURY_SCALARS_VIEW = "v_token_scalars_published"
TREASURY_JOB = "daily_treasury"
#: Chains the treasury job publishes on. Labels only — the chain set actually
#: shown is derived from the data, never assumed.
TREASURY_CHAINS = {1: "Ethereum", 100: "Gnosis Chain"}
#: GNO per chain — the one holding with an unambiguous governance meaning.
#: Sourced from rpc-state-indexer's own catalog (config/ethereum/tokens.yaml,
#: config/gnosis/jobs.yaml), not from a symbol lookup: symbols are attacker
#: controlled, addresses are not.
GNO_TOKENS = {
    1: "0x6810e776880c02933d47db1b9fc05908e5386b96",
    100: "0x9c58bacc331c9aa871afd802db6379a98e80cedb",
}
#: Gnosis Ltd. — excluded from the DAO-scoped NAV convention. Identified in
#: rpc-state-indexer's .agents/memory/treasury-sweep-pipeline.md and present as
#: the last row of both chains' treasury_addresses.csv. Deliberately the ONLY
#: labelled wallet: no other name is verifiable from either repo, and inventing
#: labels for the remaining 22 would be fabricated provenance.
LTD_WALLETS = ("0x604e4557e9020841f4e8eb98148de3d3cdea350c",)
#: Columns the overlay resolver scans for token addresses. Narrow on purpose:
#: this plane names its token column exactly one way, and a loose pattern would
#: sweep unrelated columns into a CoinGecko lookup.
TREASURY_TOKEN_COLUMN_RE = re.compile(r"^token_address$")
#: History grain. Chain 1 publishes month-end snapshots natively; chain 100
#: published daily until it went stale. Sampling the LAST snapshot of each month
#: is the only grain both chains share, and it keeps every history dataset at
#: ~99 buckets total — small enough to hydrate fully and chart client-side.
TREASURY_HISTORY_UNIT = "month"
#: Candidate cap per chain for the token-history series set. NOT a display N:
#: USD ranking happens client-side (prices are a frontend overlay), so the
#: server ships a candidate pool and the UI picks its own top-N by value.
TREASURY_HISTORY_TOKENS = 24
#: bytes32 of "gnosis.eth" (right-padded ASCII) — the Snapshot space id.
GNOSIS_SPACE_ID = "0x676e6f7369732e65746800000000000000000000000000000000000000000000"
#: The Snapshot space slug as it appears in ``governance_db.*.space_id``. The
#: ingester only loads this one space today, but any query that reduces ACROSS
#: proposals (rather than filtering to one) must pin it: an unpinned argMax over
#: votes would silently follow a voter into a second space the day one is added.
SNAPSHOT_SPACE = "gnosis.eth"
#: Snapshot strategy names carrying delegated voting power. Matched as a
#: SUBSTRING, deliberately: gnosis.eth has used both ``delegation`` and
#: ``erc20-balance-of-delegation``, and Snapshot's delegation family keeps
#: growing. See ``_delegation_slot_cte`` for why the name — not the position —
#: is the identity.
DELEGATION_STRATEGY_MATCH = "delegation"
#: Row cap for ``delegation_power``. Set ABOVE the current delegate count (80)
#: on purpose: delegates with no realized figure sort last under NULLS LAST, so
#: a tight cap would truncate exactly the rows the UI needs in order to say how
#: many delegates it could not measure — and an undisclosed truncation reads as
#: "every delegate is here".
DELEGATE_POWER_CAP = 200
GOV_APP_META = {
    "ui": {"resourceUri": GOV_URI},
    "ui/resourceUri": GOV_URI,
}
ROW_CAP = 10_000
#: 4 sections + the ``entity`` pseudo-section: an entity drill-down never
#: evicts a section scope. Datasets are small; memory cost is negligible.
MAX_RETAINED_SECTIONS = 5
VALID_SECTIONS = {
    "overview", "proposals", "voters", "forum", "delegations", "treasury",
}
ENTITY_TYPES = {
    "proposal", "voter", "forum_topic", "forum_user",
    "treasury_token", "treasury_wallet",
}

PROPOSAL_ID_RE = re.compile(r"^0x[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
#: Treasury entities are ``<chain_id>:<address>``. The chain is part of the
#: identity because 23 of the 24 census wallets exist verbatim on BOTH chains —
#: a bare address is ambiguous 96% of the time — and because a bridged token
#: would collide on canonical addresses the day it lands.
TREASURY_ENTITY_ID_RE = re.compile(r"^([0-9]{1,6}):(0x[0-9a-f]{40})$")
FORUM_ID_RE = re.compile(r"^[0-9]{1,10}$")
GIP_QUERY_RE = re.compile(r"^gip[\s-]?0*([0-9]+)$", re.IGNORECASE)
#: Shared GIP regex, verbatim across SQL (RE2) and TS: no digit cap, no
#: trailing boundary; ``AGIP-5`` must NOT match (word boundary before GIP).
GIP_PATTERN = r"\bGIP[\s-]?0*([0-9]+)"
#: SQL fragment template — ``{col}`` is a trusted internal column reference,
#: never user input. The regex is a SQL string literal, hence ``\\b``.
GIP_SQL = r"toInt32OrNull(extract({col}, '(?i)\\bGIP[\\s-]?0*([0-9]+)'))"
#: PRIMARY link tier: topic id from the author-declared discussion URL.
#: Handles trailing post-number segments (``/t/slug/123/5`` -> 123) and is
#: NULL-safe while the column is empty pre-reingest.
DISCUSSION_TOPIC_SQL = (
    r"toUInt32OrNull(extract(discussion, 'forum\\.gnosis\\.io/t/[^/]+/([0-9]+)'))"
)
#: Quorum vocabulary is met/missed/unspecified — never pass/fail language.
QUORUM_STATUS_SQL = "multiIf(quorum <= 0, 'unspecified', scores_total >= quorum, 'met', 'missed')"
QUORUM_RATIO_SQL = "scores_total / nullIf(quorum, 0)"
SPACE_URL = "https://snapshot.org/#/gnosis.eth"
FORUM_BASE_URL = "https://forum.gnosis.io"

#: Datasets per section, split into load groups (FROZEN — the frontend
#: mirrors this map byte-for-byte). The section apply loads only ``core``
#: synchronously; every other group is fetched afterwards by the frontend
#: through ``load_governance_datasets``. Every dataset key of a section MUST
#: appear in exactly one group, and keys are globally unique (tested).
SECTION_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "overview": {
        "core": ("space_summary", "source_freshness", "governance_activity"),
        "insights": (
            "proposal_types",
            "quorum_distribution",
            "voter_power_concentration",
            "latest_activity",
            "forum_category_activity",
        ),
    },
    "proposals": {
        "core": ("proposal_summary", "proposals"),
        "charts": ("proposal_activity",),
    },
    "voters": {
        "core": ("voter_summary", "voter_leaderboard"),
        "insights": ("voter_concentration", "voter_activity"),
    },
    "forum": {
        "core": ("forum_summary", "forum_categories", "forum_topics"),
        "insights": ("forum_activity", "contributor_leaderboard"),
    },
    "delegations": {
        "core": ("delegation_summary", "top_delegates"),
        "insights": (
            "delegation_activity",
            "delegation_power",
            "delegation_concentration",
            "delegation_churn",
        ),
    },
    "treasury": {
        "core": ("treasury_summary", "treasury_holdings", "treasury_by_wallet"),
        "insights": ("treasury_coverage",),
        "history": (
            "treasury_chain_history",
            "treasury_token_history",
            "treasury_wallet_history",
        ),
    },
}
#: Entity drill-down bundles (FROZEN) — loaded by ``_apply_entity_load``
#: under the ``"entity"`` pseudo-section, never part of SECTION_GROUPS.
ENTITY_BUNDLES: dict[str, tuple[str, ...]] = {
    "proposal": (
        "proposal_detail", "proposal_choices", "proposal_vote_trend",
        "proposal_votes", "proposal_forum_links",
    ),
    "voter": ("voter_profile", "voter_votes", "voter_participation"),
    "forum_topic": ("topic_detail", "topic_posts", "topic_proposal_links"),
    "forum_user": (
        "contributor_profile", "contributor_posts", "contributor_activity",
    ),
    "treasury_token": (
        "treasury_token_detail", "treasury_token_holders",
        "treasury_token_holder_series",
    ),
    "treasury_wallet": (
        "treasury_wallet_detail", "treasury_wallet_positions",
        "treasury_wallet_series",
    ),
}

#: Provenance labels per source plane — every dataset discloses whether it is
#: Snapshot signaling, forum activity, or a cross-source composition.
SOURCE_LABELS = {
    "snapshot": "Snapshot off-chain signaling",
    "forum": "Forum activity",
    "cross": "Snapshot signaling + forum activity",
    #: Snapshot votes joined to the on-chain delegate registry. Distinct from
    #: "cross" (which is signaling + FORUM) — no forum data reaches these.
    "signaling_onchain": "Snapshot signaling + on-chain delegate registry",
    "delegation": "Snapshot delegate registry (on-chain: mainnet + Gnosis Chain)",
    "treasury": "Verified treasury balances at pinned finalized blocks",
}

PROPOSAL_STATES = {"", "active", "pending", "closed"}
#: Full Snapshot voting-system vocabulary (only basic/single-choice/
#: ranked-choice exist in the space today; the rest are valid filter values).
PROPOSAL_TYPES = {
    "", "basic", "single-choice", "approval", "ranked-choice", "quadratic",
    "weighted",
}
QUORUM_STATUSES = {"", "met", "missed", "unspecified"}
FORUM_STATUSES = {"", "open", "closed", "archived"}
#: forum_status values map to fixed flag predicates — the value string itself
#: never reaches SQL text.
FORUM_STATUS_PREDICATES = {
    "open": "closed = 0 AND archived = 0",
    "closed": "closed = 1 AND archived = 0",
    "archived": "archived = 1",
}
MAX_QUERY_LENGTH = 200

#: Per-section sort whitelists mapping to FIXED ORDER BY fragments with
#: unique-id tiebreakers. ``""`` is the section default.
PROPOSAL_SORTS = {
    "": "created_at DESC, id",
    "newest": "created_at DESC, id",
    "oldest": "created_at ASC, id",
    "most_votes": "votes_count DESC, id",
    "highest_participation": "scores_total DESC, id",
    "quorum_ratio": "quorum_ratio DESC NULLS LAST, id",
    "recently_ended": "end_at DESC, id",
}
VOTER_SORTS = {
    "": "total_vp DESC, voter_key",
    "total_vp": "total_vp DESC, voter_key",
    "vote_count": "vote_count DESC, voter_key",
    "avg_vp": "avg_vp DESC, voter_key",
    "first_vote": "first_vote_at ASC, voter_key",
    "latest_vote": "last_vote_at DESC, voter_key",
}
FORUM_SORTS = {
    "": "last_posted_at DESC, id",
    "recent_activity": "last_posted_at DESC, id",
    "newest": "created_at DESC, id",
    "most_posts": "posts_count DESC, id",
    "most_views": "views DESC, id",
    "most_likes": "like_count DESC, id",
}
DELEGATE_SORTS = {
    "": "delegator_count DESC, delegate",
    "delegator_count": "delegator_count DESC, delegate",
    "recently_active": "last_delegation_at DESC, delegate",
    "first_seen": "first_delegation_at ASC, delegate",
}
#: Treasury holdings ordering. The default is deliberate: without a price feed
#: there is NO value ranking, and the two obvious proxies both mislead — wallet
#: count ranks airdrop spam first (spam hits every wallet by construction), and
#: raw balance compares incomparable units. So the default surfaces what can be
#: displayed truthfully (resolved metadata) and ranks it by share of the token's
#: own supply, which is at least dimensionless. It is a display order, not a
#: claim about treasury importance — the UI says so.
#: ``supply_share > 1`` is arithmetically impossible for an honest token: the
#: holding cannot exceed the token's own supply. The classic spoofed-token shape
#: returns a constant balance to every caller, so N wallets each "hold" 100% and
#: the total lands near N x supply. Those are demoted rather than allowed to top
#: the list on a fabricated number.
_PLAUSIBLE_SHARE = "ifNull(supply_share <= 1, 1) DESC"
TREASURY_SORTS = {
    "": f"metadata_known DESC, {_PLAUSIBLE_SHARE}, supply_share DESC NULLS LAST, token_address",
    "supply_share": f"{_PLAUSIBLE_SHARE}, supply_share DESC NULLS LAST, token_address",
    "wallets_holding": "wallets_holding DESC, token_address",
    "symbol": "symbol ASC NULLS LAST, token_address",
}
SECTION_SORTS: dict[str, dict[str, str]] = {
    "overview": {"": ""},
    "proposals": PROPOSAL_SORTS,
    "voters": VOTER_SORTS,
    "forum": FORUM_SORTS,
    "delegations": DELEGATE_SORTS,
    "treasury": TREASURY_SORTS,
}


@dataclass(frozen=True)
class QuerySpec:
    key: str
    title: str
    sql: str
    parameters: dict[str, Any]
    basis: str
    #: Which data plane the dataset reads — feeds the provenance label.
    source: Literal[
        "snapshot", "forum", "cross", "delegation", "treasury", "signaling_onchain",
    ]
    cache_ttl_seconds: int = 1800
    exact_count: bool = True


_BUNDLE = mini_apps.StaticBundle(
    "governance.html",
    assets_dir="assets/governance",
    build_hint="make build-ui-governance",
)


def get_governance_html() -> str:
    return _BUNDLE.html()


def get_governance_diagnostics() -> dict[str, Any]:
    return _BUNDLE.diagnostics()


# ---------------------------------------------------------------------------
# Validation helpers (all raise before any SQL)
# ---------------------------------------------------------------------------


def _gip_sql(col: str) -> str:
    """GIP-number extraction over a trusted internal column reference."""
    return GIP_SQL.format(col=col)


def _range_state(start_at: str, end_at: str) -> dict[str, Any]:
    """Frozen ``start_at`` token encoding — see the module docstring.

    ``""``/``""`` = all history; ``"90d"``/``"1y"`` (end empty) = relative
    presets anchored to ``now()`` UTC; ISO pair = custom. The token values are
    stored verbatim so the scope fingerprint stays deterministic.
    """
    start = start_at.strip()
    end = end_at.strip()
    if not start and not end:
        return {
            "kind": "all", "window_days": 0, "anchor": "now",
            "start_at": "", "end_at": "",
        }
    if start in ("90d", "1y"):
        if end:
            raise ValueError(
                "end_at must be empty when start_at is a '90d'/'1y' preset token"
            )
        days = 90 if start == "90d" else 365
        return {
            "kind": "relative", "window_days": days, "anchor": "now",
            "start_at": start, "end_at": "",
        }
    if not start or not end:
        raise ValueError(
            "start_at and end_at must be provided together for a custom range"
        )
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "start_at must be '', '90d', '1y', or an ISO-8601 timestamp "
            "paired with end_at"
        ) from exc
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    if start_dt >= end_dt:
        raise ValueError("start_at must be earlier than end_at")
    return {
        "kind": "absolute", "window_days": None, "anchor": "explicit",
        "start_at": start_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end_at": end_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _range_days(range_state: dict[str, Any]) -> int:
    if range_state["kind"] == "relative":
        return int(range_state["window_days"] or 0)
    if range_state["kind"] == "absolute":
        start_dt = datetime.fromisoformat(str(range_state["start_at"]).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(range_state["end_at"]).replace("Z", "+00:00"))
        return max(1, int(((end_dt - start_dt).total_seconds() + 86_399) // 86_400))
    return 0


def _bucket(col: str, days: int) -> tuple[str, str]:
    """Adaptive bucket fragment + unit for an activity time series.

    ``col`` is a trusted internal column reference, never user input.
    ``days == 0`` means all history (monthly buckets).
    """
    if 0 < days <= 120:
        return f"toStartOfDay({col})", "day"
    if 0 < days <= 740:
        return f"toStartOfWeek({col}, 1)", "week"
    return f"toStartOfMonth({col})", "month"


def _time_params(range_state: dict[str, Any]) -> dict[str, Any]:
    if range_state["kind"] == "relative":
        return {"window_days": int(range_state["window_days"])}
    if range_state["kind"] == "absolute":
        return {"start_at": range_state["start_at"], "end_at": range_state["end_at"]}
    return {}


def _point_predicate(col: str, range_state: dict[str, Any]) -> str:
    """Time predicate for a point-timestamp column (votes/topics/posts)."""
    if range_state["kind"] == "all":
        return "1"
    if range_state["kind"] == "absolute":
        return (
            f"{col} >= parseDateTime64BestEffort({{start_at:String}}) "
            f"AND {col} <= parseDateTime64BestEffort({{end_at:String}})"
        )
    return f"{col} >= now() - toIntervalDay({{window_days:UInt32}})"


def _overlap_predicate(range_state: dict[str, Any], alias: str = "") -> str:
    """Voting-window-overlap predicate for proposals (user-confirmed):
    a proposal is in range when its [start_at, end_at] window intersects it.
    """
    prefix = f"{alias}." if alias else ""
    if range_state["kind"] == "all":
        return "1"
    if range_state["kind"] == "absolute":
        return (
            f"{prefix}start_at <= parseDateTime64BestEffort({{end_at:String}}) "
            f"AND {prefix}end_at >= parseDateTime64BestEffort({{start_at:String}})"
        )
    return (
        f"{prefix}start_at <= now() "
        f"AND {prefix}end_at >= now() - toIntervalDay({{window_days:UInt32}})"
    )


def _default_filters() -> dict[str, Any]:
    return {
        "query": "",
        "proposal_state": "",
        "proposal_type": "",
        "quorum_status": "",
        "category_id": 0,
        "forum_status": "",
        "sort_by": "",
        "chain_id": 0,
        "asset": "",
        "exclude_ltd": False,
    }


def _validate_filters(
    section: str,
    query: str,
    proposal_state: str,
    proposal_type: str,
    quorum_status: str,
    category_id: int,
    forum_status: str,
    sort_by: str,
    chain_id: int = 0,
    asset: str = "",
    exclude_ltd: bool = False,
) -> dict[str, Any]:
    """Validate every filter and its per-section applicability. Raises before
    any SQL — a filter that cannot apply to the section is an error, never a
    silent no-op."""
    text = query.strip()
    if len(text) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must be at most {MAX_QUERY_LENGTH} characters")
    state = proposal_state.strip().lower()
    if state not in PROPOSAL_STATES:
        raise ValueError(f"proposal_state must be one of {sorted(PROPOSAL_STATES)}")
    ptype = proposal_type.strip().lower()
    if ptype not in PROPOSAL_TYPES:
        raise ValueError(f"proposal_type must be one of {sorted(PROPOSAL_TYPES)}")
    qstatus = quorum_status.strip().lower()
    if qstatus not in QUORUM_STATUSES:
        raise ValueError(f"quorum_status must be one of {sorted(QUORUM_STATUSES)}")
    fstatus = forum_status.strip().lower()
    if fstatus not in FORUM_STATUSES:
        raise ValueError(f"forum_status must be one of {sorted(FORUM_STATUSES)}")
    cid = int(category_id or 0)
    if cid < 0:
        raise ValueError("category_id must be a non-negative integer")
    if section != "proposals" and (state or ptype or qstatus):
        raise ValueError(
            "proposal_state/proposal_type/quorum_status apply only to the "
            "proposals section"
        )
    if section != "forum" and (cid or fstatus):
        raise ValueError("category_id/forum_status apply only to the forum section")
    if text and section not in {"proposals", "forum"}:
        raise ValueError("query applies only to the proposals and forum sections")
    chain = int(chain_id or 0)
    token = asset.strip().lower()
    ltd = bool(exclude_ltd)
    if section != "treasury" and (chain or token or ltd):
        raise ValueError(
            "chain_id/asset/exclude_ltd apply only to the treasury section"
        )
    if chain and chain not in TREASURY_CHAINS:
        raise ValueError(f"chain_id must be one of {sorted(TREASURY_CHAINS)}")
    if token and not ADDRESS_RE.match(token):
        raise ValueError("asset must be a lowercase 0x-prefixed 20-byte address")
    sort = sort_by.strip().lower()
    sorts = SECTION_SORTS[section]
    if sort not in sorts:
        raise ValueError(
            f"sort_by must be one of {sorted(k for k in sorts if k)} "
            f"for section {section}"
        )
    return {
        "query": text,
        "proposal_state": state,
        "proposal_type": ptype,
        "quorum_status": qstatus,
        "category_id": cid,
        "forum_status": fstatus,
        "sort_by": sort,
        "chain_id": chain,
        "asset": token,
        "exclude_ltd": ltd,
    }


def _validate_entity_identifier(entity_type: str, identifier: str) -> str:
    """Validate + normalize an entity identifier. Raises before any SQL."""
    kind = entity_type.strip().lower()
    if kind not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    value = identifier.strip()
    if kind == "proposal":
        value = value.lower()
        if not PROPOSAL_ID_RE.fullmatch(value):
            raise ValueError("proposal identifier must be a 0x-prefixed 64-hex id")
        return value
    if kind == "voter":
        value = value.lower()
        if not ADDRESS_RE.fullmatch(value):
            raise ValueError("voter identifier must be a 0x-prefixed EVM address")
        return value
    if kind in ("treasury_token", "treasury_wallet"):
        match = TREASURY_ENTITY_ID_RE.fullmatch(value.lower())
        if match is None:
            raise ValueError(
                f"{kind} identifier must be '<chain_id>:<0x address>'"
            )
        chain = int(match.group(1))
        if chain not in TREASURY_CHAINS:
            raise ValueError(f"chain_id must be one of {sorted(TREASURY_CHAINS)}")
        return f"{chain}:{match.group(2)}"
    if not FORUM_ID_RE.fullmatch(value) or int(value) <= 0:
        raise ValueError(f"{kind} identifier must be a positive integer id")
    return str(int(value))


# ---------------------------------------------------------------------------
# Choice helpers
# ---------------------------------------------------------------------------


def _classify_choice(choice_raw: Any, choice_count: int = 0) -> dict[str, Any]:
    """Classify a raw Snapshot vote ``choice`` value (pure helper).

    Int -> 1-based ``single`` (out-of-range flagged); int array -> ``ranked``
    (out-of-range/duplicate flagged); object/empty/unparseable ->
    ``unsupported``. Accepts a Python value or a JSON string.
    """
    value = choice_raw
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {"kind": "unsupported", "index": None, "indexes": [], "flagged": True}
        try:
            value = json.loads(text)
        except ValueError:
            return {"kind": "unsupported", "index": None, "indexes": [], "flagged": True}
    if isinstance(value, bool):
        return {"kind": "unsupported", "index": None, "indexes": [], "flagged": True}
    if isinstance(value, int):
        flagged = value < 1 or (choice_count > 0 and value > choice_count)
        return {"kind": "single", "index": int(value), "indexes": [], "flagged": flagged}
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        indexes = [int(item) for item in value]
        out_of_range = any(
            item < 1 or (choice_count > 0 and item > choice_count) for item in indexes
        )
        duplicates = len(set(indexes)) != len(indexes)
        return {
            "kind": "ranked", "index": None, "indexes": indexes,
            "flagged": out_of_range or duplicates,
        }
    return {"kind": "unsupported", "index": None, "indexes": [], "flagged": True}


def _choice_warning_scan(dataset: CachedDataset) -> bool:
    """True when a votes dataset carries an unsupported/invalid choice shape."""
    columns = {name: idx for idx, name in enumerate(dataset.columns)}
    kind_idx = columns.get("choice_kind")
    if kind_idx is None:
        return False
    index_idx = columns.get("choice_index")
    indexes_idx = columns.get("choice_indexes")
    for row in dataset.rows:
        if kind_idx >= len(row):
            continue
        kind = str(row[kind_idx] or "")
        if kind == "unsupported":
            return True
        if kind == "single" and index_idx is not None and index_idx < len(row):
            index = row[index_idx]
            try:
                if index is not None and int(index) < 1:
                    return True
            except (TypeError, ValueError):
                return True
        if kind == "ranked" and indexes_idx is not None and indexes_idx < len(row):
            sequence = row[indexes_idx] or []
            try:
                values = [int(item) for item in sequence]
            except (TypeError, ValueError):
                return True
            if any(item < 1 for item in values) or len(set(values)) != len(values):
                return True
    return False


# ---------------------------------------------------------------------------
# Spec builders — every spec targets governance_db, FINAL after every table
# reference, {name:Type} binds for every user value, explicit ORDER BY.
# ---------------------------------------------------------------------------


def _source_freshness_spec() -> QuerySpec:
    """Two independent freshness clocks per source: the ingestion clock
    (``ingested_at``) and the activity clock (created/last_posted)."""
    sql = sql_loader.load_sql("governance", "source_freshness")
    return QuerySpec(
        "source_freshness", "Source freshness", sql, {},
        "ingested_at + activity clocks", "cross", 300,
    )


def _proposal_filter(filters: dict[str, Any], alias: str = "") -> tuple[str, dict[str, Any]]:
    prefix = f"{alias}." if alias else ""
    parts: list[str] = []
    params: dict[str, Any] = {}
    if filters.get("proposal_state"):
        parts.append(f"{prefix}state = {{proposal_state:String}}")
        params["proposal_state"] = filters["proposal_state"]
    if filters.get("proposal_type"):
        parts.append(f"{prefix}type = {{proposal_type:String}}")
        params["proposal_type"] = filters["proposal_type"]
    if filters.get("quorum_status"):
        parts.append(f"{QUORUM_STATUS_SQL} = {{quorum_status:String}}")
        params["quorum_status"] = filters["quorum_status"]
    if filters.get("query"):
        parts.append(f"positionCaseInsensitive({prefix}title, {{query:String}}) > 0")
        params["query"] = filters["query"]
    return (" AND ".join(parts) if parts else "1"), params


def _forum_filter(filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    parts: list[str] = []
    params: dict[str, Any] = {}
    if filters.get("category_id"):
        parts.append("category_id = {category_id:Int32}")
        params["category_id"] = int(filters["category_id"])
    if filters.get("forum_status"):
        parts.append(FORUM_STATUS_PREDICATES[filters["forum_status"]])
    if filters.get("query"):
        parts.append("positionCaseInsensitive(title, {query:String}) > 0")
        params["query"] = filters["query"]
    return (" AND ".join(parts) if parts else "1"), params


def _overview_specs(range_state: dict[str, Any]) -> list[QuerySpec]:
    time_params = _time_params(range_state)
    overlap = _overlap_predicate(range_state)
    votes_time = _point_predicate("created_at", range_state)
    follows_time = _point_predicate("created_at", range_state)
    topics_time = _point_predicate("created_at", range_state)
    posts_time = _point_predicate("created_at", range_state)
    activity_time = _point_predicate("last_posted_at", range_state)
    days = _range_days(range_state)

    space_summary = sql_loader.load_sql("governance", "space_summary", overlap=overlap, votes_time=votes_time, follows_time=follows_time, topics_time=topics_time, posts_time=posts_time)

    proposal_bucket, unit = _bucket("created_at", days)
    vote_bucket, _ = _bucket("created_at", days)
    topic_bucket, _ = _bucket("created_at", days)
    post_bucket, _ = _bucket("created_at", days)
    governance_activity = sql_loader.load_sql("governance", "governance_activity", unit=unit, proposal_bucket=proposal_bucket, vote_bucket=vote_bucket, topic_bucket=topic_bucket, post_bucket=post_bucket, proposals_time=_point_predicate("created_at", range_state), votes_time=votes_time, topics_time=topics_time, posts_time=posts_time)

    proposal_types = sql_loader.load_sql("governance", "proposal_types", overlap=overlap)

    quorum_distribution = sql_loader.load_sql("governance", "quorum_distribution", quorum_status_sql=QUORUM_STATUS_SQL, quorum_ratio_sql=QUORUM_RATIO_SQL, overlap=overlap)

    voter_power_concentration = sql_loader.load_sql("governance", "voter_power_concentration", votes_time=votes_time)

    latest_activity = sql_loader.load_sql("governance", "latest_activity")

    forum_category_activity = sql_loader.load_sql("governance", "forum_category_activity", activity_time=activity_time)

    return [
        QuerySpec("space_summary", "Space summary", space_summary, dict(time_params),
                  "date-scoped counts; forum_user_count is all-time (no created_at)",
                  "cross"),
        _source_freshness_spec(),
        QuerySpec("governance_activity", "Governance activity", governance_activity,
                  dict(time_params), "created_at", "cross"),
        QuerySpec("proposal_types", "Proposal types", proposal_types,
                  dict(time_params), "voting-window overlap", "snapshot"),
        QuerySpec("quorum_distribution", "Quorum attainment", quorum_distribution,
                  dict(time_params), "voting-window overlap", "snapshot"),
        QuerySpec("voter_power_concentration", "Voting-power concentration",
                  voter_power_concentration, dict(time_params), "created_at",
                  "snapshot"),
        QuerySpec("latest_activity", "Latest activity", latest_activity, {},
                  "newest proposals + most recently bumped topics", "cross", 300),
        QuerySpec("forum_category_activity", "Forum category activity",
                  forum_category_activity, dict(time_params), "last_posted_at",
                  "forum"),
    ]


def _proposals_specs(
    range_state: dict[str, Any], filters: dict[str, Any]
) -> list[QuerySpec]:
    overlap = _overlap_predicate(range_state)
    filter_sql, filter_params = _proposal_filter(filters)
    params = {**_time_params(range_state), **filter_params}
    where = f"{overlap} AND {filter_sql}"
    sort_fragment = PROPOSAL_SORTS.get(filters.get("sort_by", ""), PROPOSAL_SORTS[""])
    days = _range_days(range_state)

    proposal_summary = sql_loader.load_sql("governance", "proposal_summary", where=where)

    proposals = sql_loader.load_sql("governance", "proposals", where=where, sort_fragment=sort_fragment, quorum_ratio_sql=QUORUM_RATIO_SQL, quorum_status_sql=QUORUM_STATUS_SQL, gip_title=_gip_sql("title"), discussion_topic_sql=DISCUSSION_TOPIC_SQL)

    start_bucket, unit = _bucket("start_at", days)
    vote_bucket, _ = _bucket("created_at", days)
    proposal_activity = sql_loader.load_sql("governance", "proposal_activity", where=where, unit=unit, start_bucket=start_bucket, vote_bucket=vote_bucket)

    return [
        QuerySpec("proposal_summary", "Proposal summary", proposal_summary,
                  dict(params), "voting-window overlap", "snapshot"),
        QuerySpec("proposals", "Proposals", proposals, dict(params),
                  "voting-window overlap", "snapshot"),
        QuerySpec("proposal_activity", "Proposal activity", proposal_activity,
                  dict(params), "start_at / vote created_at", "snapshot"),
    ]


def _voters_specs(
    range_state: dict[str, Any], filters: dict[str, Any]
) -> list[QuerySpec]:
    votes_time = _point_predicate("created_at", range_state)
    params = _time_params(range_state)
    sort_fragment = VOTER_SORTS.get(filters.get("sort_by", ""), VOTER_SORTS[""])
    days = _range_days(range_state)

    # Inner column names deliberately differ from the outer aliases —
    # ClickHouse substitutes same-name aliases back into the aggregate
    # (ILLEGAL_AGGREGATION: sum(vote_count) AS vote_count).
    voter_summary = sql_loader.load_sql("governance", "voter_summary", votes_time=votes_time)

    # any(voter) must not be aliased back to "voter" in the grouping SELECT —
    # the alias would substitute into lower(voter) (ILLEGAL_AGGREGATION).
    voter_leaderboard = sql_loader.load_sql("governance", "voter_leaderboard", votes_time=votes_time, sort_fragment=sort_fragment)

    voter_concentration = sql_loader.load_sql("governance", "voter_concentration", votes_time=votes_time)

    bucket_sql, unit = _bucket("created_at", days)
    voter_activity = sql_loader.load_sql("governance", "voter_activity", bucket_sql=bucket_sql, unit=unit, votes_time=votes_time)

    return [
        QuerySpec("voter_summary", "Voter summary", voter_summary, dict(params),
                  "vote created_at; voters grouped on lower(voter)", "snapshot"),
        QuerySpec("voter_leaderboard", "Voter leaderboard", voter_leaderboard,
                  dict(params), "vote created_at", "snapshot"),
        QuerySpec("voter_concentration", "Voter concentration", voter_concentration,
                  dict(params), "vote created_at", "snapshot"),
        QuerySpec("voter_activity", "Voter activity", voter_activity, dict(params),
                  "vote created_at", "snapshot"),
    ]


def _delegations_specs(
    range_state: dict[str, Any], filters: dict[str, Any]
) -> list[QuerySpec]:
    """Snapshot delegate-registry analytics over the on-chain
    ``rpc_log_indexer.v_delegate_events_gnosis`` view (Ethereum mainnet AND
    Gnosis Chain).

    The view is reorg-safe / checkpoint-bounded (built on
    ``decoded_events_canonical``) so it is queried WITHOUT ``FINAL``.
    Delegation is last-write-wins PER ``(chain_id, delegator)``: the same
    address can delegate independently on each chain, so every reduction groups
    by ``(chain_id, delegator)`` and delegator counts are ``uniqExact`` over
    addresses (a person active on both chains counts once per delegate).
    "Active" specs read all history (state as of now); the activity/churn time
    series honour the range.
    """
    src = f"{DELEGATE_DB}.{DELEGATE_VIEW}"
    time_params = _time_params(range_state)
    time_pred = _point_predicate("block_timestamp", range_state)
    days = _range_days(range_state)
    bucket_sql, unit = _bucket("block_timestamp", days)
    sort_fragment = DELEGATE_SORTS.get(filters.get("sort_by", ""), DELEGATE_SORTS[""])

    delegation_summary = sql_loader.load_sql("governance", "delegation_summary", src=src)

    top_delegates = sql_loader.load_sql("governance", "top_delegates", src=src, sort_fragment=sort_fragment)

    delegation_activity = sql_loader.load_sql("governance", "delegation_activity", src=src, time_pred=time_pred, bucket_sql=bucket_sql, unit=unit)

    # Delegated voting power, resolved by strategy NAME + NETWORK rather than by
    # array position.
    #
    # `vp_by_strategy` is positional against the proposal's OWN strategy list,
    # and gnosis.eth has rewritten that list three times:
    #
    #   2020-11..2022-03  erc20-balance-of(1), delegation(1)                        -> delegation at [2]
    #   2022-03..2025-10  gno(1), delegation(1), gno(100), delegation(100)          -> [2]=mainnet, [4]=gnosis
    #   2025-11..now      contract-call(100), beacon-chain(100), contract-call(1),
    #                     delegation(100), delegation(1)                            -> [4]=gnosis, [5]=mainnet
    #
    # Two traps live in that table. The obvious one: this query used to read
    # `vps[4]`/`vps[5]` guarded on `length(vps) = 5`, so every delegate whose
    # latest final vote predated 2025-11-16 reported 0 — 20 of 80 delegates and
    # 112,491 of 426,575 VP (26%) invisible. The subtle one: the chain order
    # INVERTS between the 4- and 5-strategy layouts, so "just take the last two"
    # would have silently swapped mainnet and Gnosis Chain for 44,635 votes.
    # Only the name+network pair is stable, so that is what we resolve on, and
    # it survives the next strategy rewrite without a code change.
    delegation_power = sql_loader.load_sql(
        "governance", "delegation_power",
        src=src, gov_db=GOV_DB, space=SNAPSHOT_SPACE,
        delegation_match=DELEGATION_STRATEGY_MATCH, cap=DELEGATE_POWER_CAP,
    )

    delegation_concentration = sql_loader.load_sql("governance", "delegation_concentration", src=src)

    delegation_churn = sql_loader.load_sql("governance", "delegation_churn", src=src, time_pred=time_pred, bucket_sql=bucket_sql, unit=unit)

    return [
        QuerySpec("delegation_summary", "Delegation summary", delegation_summary, {},
                  "current active delegations (last-write-wins per chain) + all-time events; "
                  "mainnet + Gnosis Chain", "delegation", 900),
        QuerySpec("top_delegates", "Top delegates", top_delegates, {},
                  "distinct active delegators per delegate; mainnet + Gnosis Chain", "delegation"),
        QuerySpec("delegation_activity", "Delegation activity", delegation_activity,
                  dict(time_params),
                  "Set/Clear per period + cumulative net within window; block_timestamp",
                  "delegation"),
        QuerySpec("delegation_power", "Delegated voting power", delegation_power, {},
                  "realized vp_by_strategy delegation share at each delegate's latest final "
                  "vote, with the delegation strategies resolved by name+network from that "
                  "proposal's own strategy list (gnosis.eth has rewritten it three times, and "
                  "the chain order is not stable across them); voted delegates only, "
                  "snapshot-time. NULL — never 0 — where no realized figure exists: a delegate "
                  "who never voted, or a chain the proposal carried no delegation strategy for",
                  "signaling_onchain"),
        QuerySpec("delegation_concentration", "Delegation concentration",
                  delegation_concentration, {},
                  "top-N delegate share of active delegators (headcount)", "delegation"),
        QuerySpec("delegation_churn", "Delegation churn", delegation_churn,
                  dict(time_params), "new/re-pointed/cleared delegators per period; block_timestamp",
                  "delegation"),
    ]


def _treasury_predicates(filters: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Chain / asset predicates for the treasury plane, plus their binds.

    The job pin is NOT optional and is applied by every caller: the upstream
    view spans all census jobs (see TREASURY_DB notes).
    """
    params: dict[str, Any] = {}
    chain_sql = "1"
    if filters.get("chain_id"):
        chain_sql = "t.chain_id = {chain_id:UInt64}"
        params["chain_id"] = int(filters["chain_id"])
    asset_sql = "1"
    if filters.get("asset"):
        asset_sql = "t.token_address = {asset:String}"
        params["asset"] = str(filters["asset"])
    return chain_sql, asset_sql, params


def _ltd_predicate(filters: dict[str, Any]) -> str:
    """Ltd-wallet exclusion. LTD_WALLETS is a module constant, never user input.

    Returns ``1`` when the toggle is off so the exclusion is always visible in
    the SQL the UI shows, rather than being applied invisibly elsewhere.
    """
    if not filters.get("exclude_ltd") or not LTD_WALLETS:
        return "1"
    listed = ", ".join(f"'{address}'" for address in LTD_WALLETS)
    return f"t.wallet_address NOT IN ({listed})"


def _treasury_specs(
    range_state: dict[str, Any], filters: dict[str, Any]
) -> list[QuerySpec]:
    """Verified treasury balances from the rpc-state-indexer plane.

    Three invariants hold in every spec here:

    * ``job_name = TREASURY_JOB`` is pinned. The upstream view is not
      job-scoped; without this a read spans the full_holders jobs (185M+ rows),
      exhausts memory, and double-counts any token measured by two jobs.
    * The as-of date is resolved PER CHAIN. Chains publish independently and
      are months apart, so a global ``max(snapshot_date)`` would blend one
      chain's current snapshot with another's stale one.
    * ``decimals`` is Nullable and NULL means "not observed" — never 0. A
      balance whose decimals are unknown is emitted raw with its status, and is
      never scaled into a plausible-looking wrong number.

    The date-range control does not apply: these are stock measures read at a
    point in time, not flows over a window.
    """
    src = f"{TREASURY_DB}.{TREASURY_VIEW}"
    scalars = f"{TREASURY_DB}.{TREASURY_SCALARS_VIEW}"
    chain_sql, asset_sql, params = _treasury_predicates(filters)
    ltd_sql = _ltd_predicate(filters)
    sort_fragment = TREASURY_SORTS.get(filters.get("sort_by", ""), TREASURY_SORTS[""])
    gno_sql = "multiIf(" + ", ".join(
        f"t.chain_id = {chain}, '{address}'" for chain, address in sorted(GNO_TOKENS.items())
    ) + ", '')"
    ltd_list = ", ".join(f"'{address}'" for address in LTD_WALLETS) or "''"
    # Per-chain as-of. Repeated per spec rather than materialized: the frozen
    # QuerySpec contract is one self-contained statement per dataset.
    asof_cte = sql_loader.load_sql("governance", "_cte_treasury_asof_per_chain", src=src, job=TREASURY_JOB)
    # Per-chain month-end sampling for the history datasets. Same per-chain
    # discipline as asof: a global month-end would pin one chain's date onto
    # the other's rows.
    months_cte = sql_loader.load_sql("governance", "_cte_treasury_months_per_chain", src=src, job=TREASURY_JOB)
    months_join = (
        "INNER JOIN months AS m "
        "ON t.chain_id = m.chain_id AND t.snapshot_date = m.month_end"
    )
    #: The focus token for the per-wallet history: the selected asset when one
    #: is focused, otherwise GNO — the holding with an unambiguous governance
    #: meaning on both chains.
    focus_sql = f"{{asset:String}}" if filters.get("asset") else gno_sql

    treasury_summary = sql_loader.load_sql("governance", "treasury_summary", asof_cte=asof_cte, gno_sql=gno_sql, ltd_list=ltd_list, src=src, job=TREASURY_JOB, chain_sql=chain_sql, ltd_sql=ltd_sql)

    treasury_holdings = sql_loader.load_sql("governance", "treasury_holdings", asof_cte=asof_cte, scalars=scalars, job=TREASURY_JOB, src=src, chain_sql=chain_sql, asset_sql=asset_sql, ltd_sql=ltd_sql, sort_fragment=sort_fragment)

    treasury_by_wallet = sql_loader.load_sql("governance", "treasury_by_wallet", asof_cte=asof_cte, ltd_list=ltd_list, gno_sql=gno_sql, src=src, job=TREASURY_JOB, chain_sql=chain_sql, ltd_sql=ltd_sql)

    # The trust panel. Every dimension the tab cannot yet display is counted
    # here rather than silently rendered as absent.
    # Coverage counts every dimension in ONE pass over `held`, then pivots with
    # ARRAY JOIN. The obvious shape — four `SELECT ... FROM held` arms glued by
    # UNION ALL — reads better but ClickHouse INLINES a CTE per reference, so it
    # re-scanned and re-joined the whole snapshot four times and blew the 2 GiB
    # per-query cap once the treasury passed ~390 held tokens. Aggregate once,
    # reshape after.
    treasury_coverage = sql_loader.load_sql("governance", "treasury_coverage", asof_cte=asof_cte, src=src, job=TREASURY_JOB, chain_sql=chain_sql, ltd_sql=ltd_sql)

    # Whole-treasury shape per month: counts (always comparable) plus GNO in
    # units. Chains stay in separate rows via the chain_id column — the client
    # splits panels on it and never overlays two chains on one axis.
    treasury_chain_history = sql_loader.load_sql("governance", "treasury_chain_history", months_cte=months_cte, gno_sql=gno_sql, ltd_list=ltd_list, src=src, months_join=months_join, job=TREASURY_JOB, chain_sql=chain_sql, ltd_sql=ltd_sql)

    # Per-token monthly series for a CANDIDATE pool, ranked by how often the
    # position actually changed.
    #
    # Ranking by longevity or wallet count both fail here, and fail toward
    # spam: an airdropped token is held by every wallet from the moment it
    # lands and never moves again, so it beats a real position on both. Counting
    # distinct balances over the series inverts that — a dusted token has ONE
    # value forever, while a managed position has many. GNO is pinned first.
    #
    # The client picks its own top-N by USD out of this pool, which also filters
    # the residual spam for free: none of it is priced.
    treasury_token_history = sql_loader.load_sql("governance", "treasury_token_history", months_cte=months_cte, asof_cte_body=asof_cte[5:], src=src, months_join=months_join, job=TREASURY_JOB, chain_sql=chain_sql, asset_sql=asset_sql, ltd_sql=ltd_sql, gno_picked_sql=gno_sql.replace("t.chain_id", "p_chain"), history_tokens=TREASURY_HISTORY_TOKENS)

    # Per-wallet monthly series for ONE token — the focused asset, else GNO.
    # Same unit throughout, so the stack total is meaningful and folding the
    # tail into 'other' is a legitimate sum (unlike stacking across tokens).
    treasury_wallet_history = sql_loader.load_sql("governance", "treasury_wallet_history", months_cte=months_cte, src=src, job=TREASURY_JOB, chain_sql=chain_sql, ltd_sql=ltd_sql, focus_sql=focus_sql, ltd_list=ltd_list, months_join=months_join)

    scope = "; Ltd wallets excluded" if filters.get("exclude_ltd") else "; all treasury wallets"
    history_basis = (
        f"month-end snapshot per chain; chains never blended{scope}"
    )
    return [
        QuerySpec(
            "treasury_summary", "Treasury summary", treasury_summary, dict(params),
            f"latest published snapshot per chain, pinned to its finalized anchor{scope}",
            "treasury", 900,
        ),
        QuerySpec(
            "treasury_holdings", "Holdings by token", treasury_holdings, dict(params),
            "non-zero balances at each chain's latest snapshot; share of the token's own "
            f"total supply. NOT a value ranking — no price feed{scope}",
            "treasury",
        ),
        QuerySpec(
            "treasury_by_wallet", "Holdings by wallet", treasury_by_wallet, dict(params),
            f"per-wallet token counts and GNO at each chain's latest snapshot{scope}",
            "treasury",
        ),
        QuerySpec(
            "treasury_coverage", "Data coverage", treasury_coverage, dict(params),
            "what the plane can and cannot display for the held token set",
            "treasury", 900,
        ),
        QuerySpec(
            "treasury_chain_history", "Treasury over time", treasury_chain_history,
            dict(params), history_basis, "treasury",
        ),
        QuerySpec(
            "treasury_token_history", "Holdings over time", treasury_token_history,
            dict(params),
            "candidate pool ranked by how often the position changed (airdropped "
            f"dust never moves); {history_basis}",
            "treasury",
        ),
        QuerySpec(
            "treasury_wallet_history", "Wallet split over time",
            treasury_wallet_history, dict(params),
            f"one token (focused asset, else GNO), top 5 wallets + other; {history_basis}",
            "treasury",
        ),
    ]


def _forum_specs(
    range_state: dict[str, Any], filters: dict[str, Any]
) -> list[QuerySpec]:
    activity_time = _point_predicate("last_posted_at", range_state)
    posts_time = _point_predicate("created_at", range_state)
    filter_sql, filter_params = _forum_filter(filters)
    params = {**_time_params(range_state), **filter_params}
    topic_where = f"{activity_time} AND {filter_sql}"
    sort_fragment = FORUM_SORTS.get(filters.get("sort_by", ""), FORUM_SORTS[""])
    days = _range_days(range_state)

    forum_summary = sql_loader.load_sql("governance", "forum_summary", posts_time=posts_time, topic_where=topic_where)

    forum_categories = sql_loader.load_sql("governance", "forum_categories")

    forum_topics = sql_loader.load_sql("governance", "forum_topics", gip_sql=_gip_sql("title"), topic_where=topic_where, sort_fragment=sort_fragment)

    topic_bucket, unit = _bucket("created_at", days)
    post_bucket, _ = _bucket("created_at", days)
    forum_activity = sql_loader.load_sql("governance", "forum_activity", filter_sql=filter_sql, unit=unit, topic_bucket=topic_bucket, post_bucket=post_bucket, posts_time=posts_time)

    contributor_leaderboard = sql_loader.load_sql("governance", "contributor_leaderboard", posts_time=posts_time)

    return [
        QuerySpec("forum_summary", "Forum summary", forum_summary, dict(params),
                  "last_posted_at", "forum"),
        QuerySpec("forum_categories", "Forum categories", forum_categories, {},
                  "all-time category directory", "forum"),
        QuerySpec("forum_topics", "Forum topics", forum_topics, dict(params),
                  "last_posted_at", "forum"),
        QuerySpec("forum_activity", "Forum activity", forum_activity, dict(params),
                  "created_at", "forum"),
        QuerySpec("contributor_leaderboard", "Contributor leaderboard",
                  contributor_leaderboard, dict(params), "post created_at",
                  "forum"),
    ]


def _proposal_entity_specs(identifier: str) -> list[QuerySpec]:
    params = {"proposal_id": identifier}

    proposal_detail = sql_loader.load_sql("governance", "proposal_detail", quorum_ratio_sql=QUORUM_RATIO_SQL, quorum_status_sql=QUORUM_STATUS_SQL, gip_title=_gip_sql("title"), discussion_topic_sql=DISCUSSION_TOPIC_SQL)

    proposal_choices = sql_loader.load_sql("governance", "proposal_choices")

    # Vote trend: how votes + voting power accumulated over the proposal's
    # voting window (cgov-style turnout curve). Hourly buckets; the frontend
    # draws per-bucket votes as bars and cumulative VP as a line.
    proposal_vote_trend = sql_loader.load_sql("governance", "proposal_vote_trend")

    proposal_votes = sql_loader.load_sql("governance", "proposal_votes")

    # Two-tier links: the author-declared discussion topic ranks above exact
    # GIP-number matches; a topic linked both ways appears once as
    # 'discussion'. ALL candidates returned — the GIP relation is not 1:1.
    proposal_forum_links = sql_loader.load_sql("governance", "proposal_forum_links", gip_title=_gip_sql("title"), discussion_topic_sql=DISCUSSION_TOPIC_SQL, gip_topic_title=_gip_sql("t.title"), gip_proposal_title=_gip_sql("s.title"))

    return [
        QuerySpec("proposal_detail", "Proposal detail", proposal_detail,
                  dict(params), "all history", "snapshot"),
        QuerySpec("proposal_choices", "Proposal choices", proposal_choices,
                  dict(params), "all history; NULL score while pending",
                  "snapshot"),
        QuerySpec("proposal_vote_trend", "Vote trend", proposal_vote_trend,
                  dict(params), "cumulative votes + VP over the voting window (hourly)",
                  "snapshot"),
        QuerySpec("proposal_votes", "Proposal votes", proposal_votes,
                  dict(params), "all history", "snapshot"),
        QuerySpec("proposal_forum_links", "Linked forum discussion",
                  proposal_forum_links, dict(params),
                  "discussion URL (primary) + exact GIP number (secondary)",
                  "cross"),
    ]


def _voter_entity_specs(identifier: str) -> list[QuerySpec]:
    params = {"voter": identifier}

    voter_profile = sql_loader.load_sql("governance", "voter_profile")

    voter_votes = sql_loader.load_sql("governance", "voter_votes")

    voter_participation = sql_loader.load_sql("governance", "voter_participation")

    return [
        QuerySpec("voter_profile", "Voter profile", voter_profile, dict(params),
                  "all history", "snapshot"),
        QuerySpec("voter_votes", "Vote history", voter_votes, dict(params),
                  "all history", "snapshot"),
        QuerySpec("voter_participation", "Participation over time",
                  voter_participation, dict(params), "all history", "snapshot"),
    ]


def _topic_entity_specs(identifier: str) -> list[QuerySpec]:
    params = {"topic_id": int(identifier)}

    topic_detail = sql_loader.load_sql("governance", "topic_detail", gip_topic_title=_gip_sql("t.title"), forum_base_url=FORUM_BASE_URL)

    topic_posts = sql_loader.load_sql("governance", "topic_posts")

    topic_proposal_links = sql_loader.load_sql("governance", "topic_proposal_links", gip_title=_gip_sql("title"), gip_proposal_title=_gip_sql("p.title"), discussion_topic_sql=DISCUSSION_TOPIC_SQL)

    return [
        QuerySpec("topic_detail", "Topic detail", topic_detail, dict(params),
                  "all history", "forum"),
        QuerySpec("topic_posts", "Topic posts", topic_posts, dict(params),
                  "all history", "forum"),
        QuerySpec("topic_proposal_links", "Linked Snapshot proposals",
                  topic_proposal_links, dict(params),
                  "discussion URL (primary) + exact GIP number (secondary)",
                  "cross"),
    ]


def _contributor_entity_specs(identifier: str) -> list[QuerySpec]:
    params = {"user_id": int(identifier)}

    contributor_profile = sql_loader.load_sql("governance", "contributor_profile")

    contributor_posts = sql_loader.load_sql("governance", "contributor_posts")

    contributor_activity = sql_loader.load_sql("governance", "contributor_activity")

    return [
        QuerySpec("contributor_profile", "Contributor profile",
                  contributor_profile, dict(params), "all history", "forum"),
        QuerySpec("contributor_posts", "Contributor posts", contributor_posts,
                  dict(params), "all history", "forum"),
        QuerySpec("contributor_activity", "Contributor activity",
                  contributor_activity, dict(params), "all history", "forum"),
    ]


def _treasury_entity_parts(identifier: str) -> tuple[int, str, dict[str, Any]]:
    """Split a validated ``<chain>:<address>`` id into its SQL inputs.

    The chain is interpolated (it is an int already checked against
    TREASURY_CHAINS) so the month/as-of CTEs can be chain-pinned rather than
    grouped per chain; the address is always a bound parameter.
    """
    chain_text, address = identifier.split(":", 1)
    return int(chain_text), address, {"addr": address}


def _treasury_entity_ctes(chain: int) -> tuple[str, str]:
    """Chain-pinned ``asof`` and ``months`` CTEs.

    The section specs group these per chain because they serve both chains at
    once. An entity is one chain by construction, so pinning is both cheaper
    and strictly stronger — there is no second chain to blend in.
    """
    src = f"{TREASURY_DB}.{TREASURY_VIEW}"
    asof = sql_loader.load_sql("governance", "_cte_treasury_asof_one_chain", src=src, job=TREASURY_JOB, chain=chain)
    months = sql_loader.load_sql("governance", "_cte_treasury_months_one_chain", src=src, job=TREASURY_JOB, chain=chain)
    return asof, months


def _treasury_token_entity_specs(identifier: str) -> list[QuerySpec]:
    """One token, on one chain: what it is, who holds it, how that moved.

    Two anti-spoof measures are computed here rather than left to the client,
    because both need the whole chain's held set and the client only ever sees
    one token's rows:

    * ``symbol_collisions`` — how many OTHER held tokens claim this symbol. 19
      distinct tokens in this treasury claim "USDC"; the real one and each fake
      one all report 18, which is the honest answer: the symbol identifies
      nothing, and the number says so instead of implying the high count means
      "legitimate".
    * ``treasury_share`` — a wallet's share of the treasury's whole position.
      A contract that returns a constant balance to every caller yields exactly
      1/n for all n wallets, which is a spoof signature no balance alone shows.
    """
    chain, address, params = _treasury_entity_parts(identifier)
    src = f"{TREASURY_DB}.{TREASURY_VIEW}"
    scalars = f"{TREASURY_DB}.{TREASURY_SCALARS_VIEW}"
    asof_cte, months_cte = _treasury_entity_ctes(chain)
    ltd_list = ", ".join(f"'{address_}'" for address_ in LTD_WALLETS) or "''"
    label = f"{TREASURY_CHAINS[chain]} {address[:6]}…{address[-4:]}"

    # Identity + the collision count, which needs the chain's whole held set.
    # Held tokens aggregate to a few hundred rows before the window runs.
    token_detail = sql_loader.load_sql("governance", "token_detail", asof_cte=asof_cte, src=src, scalars=scalars, job=TREASURY_JOB, chain=chain, label=label)

    token_holders = sql_loader.load_sql("governance", "token_holders", asof_cte=asof_cte, src=src, job=TREASURY_JOB, chain=chain, ltd_list=ltd_list)

    # Monthly per-wallet split for this token. Same shape as
    # treasury_wallet_history so `walletSeries()` reads it unchanged.
    holder_series = sql_loader.load_sql("governance", "holder_series", months_cte=months_cte, src=src, job=TREASURY_JOB, chain=chain, ltd_list=ltd_list)

    chain_label = TREASURY_CHAINS[chain]
    return [
        QuerySpec(
            "treasury_token_detail", "Token", token_detail, dict(params),
            f"{chain_label}, latest published snapshot; symbol collisions counted "
            "over the whole held set",
            "treasury", 900,
        ),
        QuerySpec(
            "treasury_token_holders", "Wallets holding it", token_holders,
            dict(params),
            f"{chain_label}, latest published snapshot; share is of the treasury's "
            "own position, not of supply",
            "treasury",
        ),
        QuerySpec(
            "treasury_token_holder_series", "Wallet split over time",
            holder_series, dict(params),
            f"{chain_label}, month-end snapshots; top 6 wallets + other",
            "treasury",
        ),
    ]


def _treasury_wallet_entity_specs(identifier: str) -> list[QuerySpec]:
    """One wallet, on one chain: what it holds now and how that moved.

    This is where a wallet's full composition lives — and therefore the only
    place its total can be valued. The Wallets board can only price GNO, because
    ``treasury_by_wallet`` carries no per-token breakdown; it says so rather
    than labelling a partial figure "wallet value".
    """
    chain, address, params = _treasury_entity_parts(identifier)
    src = f"{TREASURY_DB}.{TREASURY_VIEW}"
    asof_cte, months_cte = _treasury_entity_ctes(chain)
    ltd_list = ", ".join(f"'{address_}'" for address_ in LTD_WALLETS) or "''"
    gno = GNO_TOKENS.get(chain, "")
    label = f"{TREASURY_CHAINS[chain]} {address[:6]}…{address[-4:]}"

    wallet_detail = sql_loader.load_sql("governance", "wallet_detail", asof_cte=asof_cte, chain=chain, label=label, ltd_list=ltd_list, gno=gno, src=src, job=TREASURY_JOB)

    # Positions carry the same two anti-spoof measures as the token page, so a
    # wallet's list is readable without opening every token.
    wallet_positions = sql_loader.load_sql("governance", "wallet_positions", asof_cte=asof_cte, chain=chain, src=src, job=TREASURY_JOB)

    # Monthly per-token series for this wallet, ranked the same way as
    # treasury_token_history: by how often the position actually moved, because
    # airdropped dust is held from the moment it lands and never changes again.
    wallet_series = sql_loader.load_sql("governance", "wallet_series", months_cte=months_cte, chain=chain, gno=gno, src=src, job=TREASURY_JOB, history_tokens=TREASURY_HISTORY_TOKENS)

    chain_label = TREASURY_CHAINS[chain]
    return [
        QuerySpec(
            "treasury_wallet_detail", "Wallet", wallet_detail, dict(params),
            f"{chain_label}, latest published snapshot", "treasury", 900,
        ),
        QuerySpec(
            "treasury_wallet_positions", "Positions", wallet_positions,
            dict(params),
            f"{chain_label}, non-zero balances at the latest snapshot; share is of "
            "the treasury's own position in each token",
            "treasury",
        ),
        QuerySpec(
            "treasury_wallet_series", "Positions over time", wallet_series,
            dict(params),
            f"{chain_label}, month-end snapshots; candidate pool ranked by how "
            "often the position changed",
            "treasury",
        ),
    ]


def _entity_specs(kind: str, identifier: str) -> list[QuerySpec]:
    if kind == "proposal":
        return _proposal_entity_specs(identifier)
    if kind == "voter":
        return _voter_entity_specs(identifier)
    if kind == "forum_topic":
        return _topic_entity_specs(identifier)
    if kind == "forum_user":
        return _contributor_entity_specs(identifier)
    if kind == "treasury_token":
        return _treasury_token_entity_specs(identifier)
    if kind == "treasury_wallet":
        return _treasury_wallet_entity_specs(identifier)
    raise ValueError(f"Unsupported entity type: {kind}")


def _section_specs(
    section: str,
    range_state: dict[str, Any],
    filters: dict[str, Any],
) -> list[QuerySpec]:
    if section == "overview":
        return _overview_specs(range_state)
    if section == "proposals":
        return _proposals_specs(range_state, filters)
    if section == "voters":
        return _voters_specs(range_state, filters)
    if section == "forum":
        return _forum_specs(range_state, filters)
    if section == "delegations":
        return _delegations_specs(range_state, filters)
    if section == "treasury":
        return _treasury_specs(range_state, filters)
    raise ValueError(f"Unsupported section: {section}")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _iso_value(value: Any) -> Any:
    if isinstance(value, datetime):
        anchored = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return anchored.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
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
    if spec.key in {"proposal_votes", "voter_votes"} and _choice_warning_scan(dataset):
        warning_codes.append("unsupported_choice_shape")
    coverage = {
        "basis": spec.basis,
        "source_kind": spec.source,
        "source_label": SOURCE_LABELS[spec.source],
        "requested_start": range_state.get("start_at") or None,
        "requested_end": range_state.get("end_at") or None,
        "range_kind": range_state.get("kind"),
        "window_days": range_state.get("window_days"),
        "fetched_at": dataset.stats.fetched_at,
        "returned_rows": dataset.stats.rows_returned,
        "source_rows": dataset.stats.source_rows,
        "row_cap": dataset.stats.row_cap,
        "truncated": bool(dataset.stats.truncated),
        "warning_codes": warning_codes,
    }
    return coverage, warning_codes


#: Negative-result cache: a failed dataset query is remembered so a manual
#: Retry within the TTL returns the cached failure INSTANTLY instead of
#: re-running a query known to blow up or time out. force_refresh bypasses
#: the read (an explicit refresh may genuinely retry).
_FAILURE_CACHE = FailureCache(GOV_DB)


def reset_failure_cache_for_tests() -> None:
    _FAILURE_CACHE.reset()


def _log_dataset_loaded(
    spec: QuerySpec,
    dataset: CachedDataset,
    range_state: dict[str, Any],
    elapsed: float,
) -> None:
    logger.info(
        "governance_dataset key=%s window=%s rows=%s source_rows=%s truncated=%s elapsed=%.3f",
        spec.key,
        range_state.get("window_days", range_state.get("kind")),
        dataset.stats.rows_returned,
        dataset.stats.source_rows,
        dataset.stats.truncated,
        elapsed,
    )


def _log_dataset_failed(spec: QuerySpec, error: Exception) -> None:
    logger.warning("governance dataset %s unavailable: %s", spec.key, error)


def _failure_coverage(
    spec: QuerySpec,
    range_state: dict[str, Any],
    fetched_at: str,
    error: str,
) -> dict[str, Any]:
    return {
        "basis": spec.basis,
        "source_kind": spec.source,
        "source_label": SOURCE_LABELS[spec.source],
        "requested_start": range_state.get("start_at") or None,
        "requested_end": range_state.get("end_at") or None,
        "range_kind": range_state.get("kind"),
        "window_days": range_state.get("window_days"),
        "fetched_at": fetched_at,
        "returned_rows": 0,
        "source_rows": None,
        "row_cap": ROW_CAP,
        "truncated": False,
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
    return mini_apps.load_specs_safe(
        ch,
        specs,
        range_state,
        force_refresh=force_refresh,
        database=GOV_DB,
        row_cap=ROW_CAP,
        failure_cache=_FAILURE_CACHE,
        coverage_fn=_coverage_from_dataset,
        failure_coverage_fn=_failure_coverage,
        worker_limit=3,
        thread_name_prefix="gov-data",
        log_success=_log_dataset_loaded,
        log_failure=_log_dataset_failed,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def _empty_loaded_groups() -> dict[str, Any]:
    return {
        f"{section}.{group}": False
        for section, groups in SECTION_GROUPS.items()
        for group in groups
    }


def _empty_freshness() -> dict[str, Any]:
    return {
        "snapshot": {"latest_ingested_at": None, "latest_activity_at": None, "stale": False},
        "forum": {"latest_ingested_at": None, "latest_activity_at": None, "stale": False},
    }


def _empty_state(section: str = "overview") -> dict[str, Any]:
    return {
        "section": section,
        "title": GOV_TITLE,
        "date_range": _range_state("", ""),
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
        # Deferred-load bookkeeping: which section.group bundles are loaded
        # (False | True | "error"/"partial"), the scope fingerprint each
        # section's cached datasets were loaded under, the keys each section
        # currently retains, and LRU order for eviction.
        "loaded_groups": _empty_loaded_groups(),
        "section_fingerprints": {},
        "section_datasets": {},
        "section_lru": [],
        "freshness": _empty_freshness(),
        # CoinGecko overlays, patched in later by load_governance_overlays.
        # Empty means "not resolved yet", never "worthless" — the UI renders an
        # em-dash from the absence and must never substitute 0.
        "icon_overlay": {},
        "price_overlay": {},
        "price_overlay_at": "",
    }


def _section_fingerprint(
    section: str,
    range_state: dict[str, Any],
    filters: dict[str, Any],
) -> str:
    """Deterministic identity of a section's load scope, built from the
    requested tokens/filters so a tab return can short-circuit with zero
    ClickHouse round trips."""
    return ":".join([
        section,
        str(range_state.get("kind")),
        str(range_state.get("window_days")),
        str(range_state.get("start_at") or ""),
        str(range_state.get("end_at") or ""),
        str(filters.get("query", "")),
        str(filters.get("proposal_state", "")),
        str(filters.get("proposal_type", "")),
        str(filters.get("quorum_status", "")),
        str(filters.get("category_id", 0)),
        str(filters.get("forum_status", "")),
        str(filters.get("sort_by", "")),
        str(filters.get("chain_id", 0)),
        str(filters.get("asset", "")),
        str(filters.get("exclude_ltd", False)),
    ])


def _touch_section_lru(view_id: str, state: dict[str, Any], keep_section: str) -> None:
    """``source_freshness`` is excluded from eviction — the freshness strip
    must survive every retention decision."""
    mini_apps.touch_section_lru(
        view_id,
        state,
        keep_section,
        section_groups=SECTION_GROUPS,
        max_retained=MAX_RETAINED_SECTIONS,
        protected_keys=("source_freshness",),
    )


def _freshness_state(
    datasets: dict[str, CachedDataset],
) -> tuple[dict[str, Any], list[str]]:
    """Parse the two per-source freshness rows into view state.

    ``stale`` means the ingestion clock is more than 24h behind ``now()`` —
    with the daily cron cadence that reads as "a daily run was missed/late".
    """
    freshness = _empty_freshness()
    warnings: list[str] = []
    dataset = datasets.get("source_freshness")
    if dataset is None or not dataset.rows:
        return freshness, warnings
    columns = {name: idx for idx, name in enumerate(dataset.columns)}
    if "source" not in columns:
        return freshness, warnings
    now = datetime.now(timezone.utc)
    for row in dataset.rows:
        source = str(row[columns["source"]] or "")
        if source not in freshness:
            continue
        ingested = None
        activity = None
        idx = columns.get("latest_ingested_at")
        if idx is not None and idx < len(row):
            ingested = row[idx]
        idx = columns.get("latest_activity_at")
        if idx is not None and idx < len(row):
            activity = row[idx]
        stale = False
        if isinstance(ingested, datetime):
            anchored = ingested if ingested.tzinfo else ingested.replace(tzinfo=timezone.utc)
            stale = (now - anchored) > timedelta(hours=24)
        freshness[source] = {
            "latest_ingested_at": _iso_value(ingested),
            "latest_activity_at": _iso_value(activity),
            "stale": stale,
        }
        if stale:
            warnings.append("source_stale")
    return freshness, list(dict.fromkeys(warnings))


_dataset_titles = mini_apps.dataset_titles


def _summary_cards(record: mini_apps.ViewRecord) -> list[SummaryCard]:
    state = record.view_state
    date_range = state.get("date_range") or {}
    cards = [
        SummaryCard(label="Section", value=str(state.get("section") or "overview").title()),
        SummaryCard(label="Window", value=(
            "All history" if date_range.get("kind") == "all"
            else f"{date_range.get('window_days') or 'Custom'} days"
        )),
    ]
    for key, label in (
        ("proposals", "Proposals"), ("voter_leaderboard", "Voters"),
        ("forum_topics", "Topics"),
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
        record,
        app_id=GOV_APP_ID,
        database=GOV_DB,
        summary_cards=_summary_cards,
        titles=titles,
    )


# ---------------------------------------------------------------------------
# Appliers
# ---------------------------------------------------------------------------


def _apply_section_load(
    ch: ClickHouseManager,
    view_id: str,
    request_id: int,
    section: str,
    query: str,
    start_at: str,
    end_at: str,
    proposal_state: str,
    proposal_type: str,
    quorum_status: str,
    category_id: int,
    forum_status: str,
    sort_by: str,
    force_refresh: bool,
    chain_id: int = 0,
    asset: str = "",
    exclude_ltd: bool = False,
) -> tuple[MiniAppPayload, str]:
    """Apply a section scope: validate, evict stale data, load the CORE group.

    Non-core groups are deliberately NOT loaded here — the frontend fetches
    them afterwards through ``load_governance_datasets`` while skeletons
    show. A fingerprint match returns the retained datasets with zero queries
    (including retained freshness — the zero-query guarantee is absolute).
    """
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    current = dict(record.view_state)
    if request_id < int(current.get("applied_request_id") or 0):
        return _payload_from_record(record), "Ignored stale Governance Explorer request."
    section_key = section.strip().lower()
    if section_key not in VALID_SECTIONS:
        raise ValueError(f"section must be one of {sorted(VALID_SECTIONS)}")
    range_state = _range_state(start_at, end_at)
    filters = _validate_filters(
        section_key, query, proposal_state, proposal_type, quorum_status,
        category_id, forum_status, sort_by, chain_id, asset, exclude_ltd,
    )
    fingerprint = _section_fingerprint(section_key, range_state, filters)
    stored_fingerprints = dict(current.get("section_fingerprints") or {})
    core_loaded = bool((current.get("loaded_groups") or {}).get(f"{section_key}.core"))
    if (
        not force_refresh
        and stored_fingerprints.get(section_key) == fingerprint
        and core_loaded
    ):
        # Tab return with unchanged scope: retained datasets are still valid,
        # and the retained freshness state is served as-is (zero queries).
        next_state = {
            **current,
            "section": section_key,
            "date_range": range_state,
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
            f"Governance {section_key} restored from retained datasets.",
        )
    specs = _section_specs(section_key, range_state, filters)
    core_keys = SECTION_GROUPS[section_key]["core"]
    core_specs = [spec for spec in specs if spec.key in core_keys]
    # source_freshness refreshes on every non-short-circuit section load
    # (300s cache TTL keeps this near-free); overview carries it in core.
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
        "date_range": range_state,
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
    return _payload_from_record(updated, titles), f"Governance {section_key} loaded."


def _apply_group_load(
    ch: ClickHouseManager,
    view_id: str,
    section: str,
    group: str,
    scope_id: str,
    force_refresh: bool,
) -> tuple[MiniAppPayload, str]:
    """Load ONE deferred dataset group additively and return a PATCH payload.

    Group loads never bump ``applied_request_id`` — they are additive and
    order-independent. The ``scope_id`` guard makes a late-arriving group
    load for a superseded scope a harmless no-op instead of a corruption.
    """
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    state = dict(record.view_state)
    current_scope_id = str(state.get("scope_id") or "")
    if scope_id and scope_id != current_scope_id:
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE", view_id=view_id, app_id=GOV_APP_ID,
            title=record.title, patch={}, warnings=["stale_scope"],
        )
        return payload, "Ignored stale governance group request."
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
    range_state = dict(state.get("date_range") or _range_state("", ""))
    filters = {**_default_filters(), **(state.get("filters") or {})}
    specs = _section_specs(section_key, range_state, filters)
    group_specs = [spec for spec in specs if spec.key in group_keys]
    # source_freshness refreshes on every non-short-circuit group load too
    # (300s cache TTL keeps this near-free between refreshes).
    if "source_freshness" not in group_keys:
        group_specs.append(_source_freshness_spec())
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
        *(state.get("warnings") or []),
        *load_warnings,
    ]))
    # "partial" (truthy — no skeleton) marks a group where at least one
    # dataset failed: the frontend shows error cards + a retry affordance.
    group_failed = any(
        "query_failed" in (coverage.get(k, {}).get("warning_codes") or [])
        for k in group_keys
    )
    patch: dict[str, Any] = {
        "loaded_groups": {f"{section_key}.{group_key}": "partial" if group_failed else True},
        "coverage": coverage,
        "dataset_revisions": {
            key: updated.dataset_revisions.get(key, 0) for key in datasets
        },
        "section_datasets": {section_key: tracked},
        "dataset_titles": {spec.key: spec.title for spec in group_specs},
        "warnings": combined_warnings,
        "coverage_warnings": [w for w in combined_warnings if " " not in w],
        "freshness": freshness,
    }
    mini_apps.patch_view_state(view_id, patch)
    descriptors = {
        key: mini_apps.build_dataset_descriptor(
            key=key,
            dataset=dataset,
            title=titles.get(key, key.replace("_", " ").title()),
            scope_id=current_scope_id,
            provenance={"source": GOV_DB, "coverage": coverage.get(key, {})},
        )
        for key, dataset in datasets.items()
    }
    payload = MiniAppPayload(
        type="PATCH_VIEW_STATE",
        view_id=view_id,
        app_id=GOV_APP_ID,
        title=record.title,
        datasets=descriptors,
        patch=patch,
        warnings=load_warnings,
    )
    return payload, f"Governance {section_key}.{group_key} loaded."


_ENTITY_DETAIL_KEY = {
    "proposal": "proposal_detail",
    "voter": "voter_profile",
    "forum_topic": "topic_detail",
    "forum_user": "contributor_profile",
    "treasury_token": "treasury_token_detail",
    "treasury_wallet": "treasury_wallet_detail",
}
_ENTITY_LABEL_COLUMN = {
    "proposal": "title",
    "voter": "voter_display",
    "forum_topic": "title",
    "forum_user": "username",
    # NOT `symbol`: breadcrumbs render their label raw, and a token symbol is
    # attacker-authored. The detail specs compose `entity_label` server-side
    # from the chain name and the address, which cannot be spoofed.
    "treasury_token": "entity_label",
    "treasury_wallet": "entity_label",
}


def _entity_label(kind: str, identifier: str, datasets: dict[str, CachedDataset]) -> str:
    dataset = datasets.get(_ENTITY_DETAIL_KEY[kind])
    if dataset is not None and dataset.rows:
        columns = {name: idx for idx, name in enumerate(dataset.columns)}
        idx = columns.get(_ENTITY_LABEL_COLUMN[kind])
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
) -> tuple[MiniAppPayload, str]:
    record = mini_apps.get_view(view_id)
    if record is None:
        raise KeyError(f"Unknown or expired view_id: {view_id}")
    current = dict(record.view_state)
    if request_id < int(current.get("applied_request_id") or 0):
        return _payload_from_record(record), "Ignored stale governance entity request."
    kind = entity_type.strip().lower()
    normalized_id = _validate_entity_identifier(kind, identifier)
    specs = _entity_specs(kind, normalized_id)
    # Entity bundles are all-history, never sampled.
    range_state = _range_state("", "")
    datasets, coverage, warnings = _load_specs_safe(
        ch, specs, range_state, force_refresh=False
    )
    scope_id = f"entity:{kind}:{normalized_id}:{request_id}"
    label = _entity_label(kind, normalized_id, datasets)
    breadcrumb = {
        "label": label[:80],
        "entity_type": kind,
        "identifier": normalized_id,
    }
    breadcrumbs = list(current.get("breadcrumbs") or []) if current.get("section") == "entity" else []
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
        breadcrumbs = breadcrumbs[:existing_index + 1]
    breadcrumbs = breadcrumbs[-8:]
    titles = dict(current.get("dataset_titles") or {})
    titles.update(_dataset_titles(specs))
    next_state = {
        **current,
        "section": "entity",
        "selected_entity": {
            "entity_type": kind,
            "identifier": normalized_id,
            "label": label,
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
    fingerprints["entity"] = f"{kind}:{normalized_id}"
    next_state["section_fingerprints"] = fingerprints
    _touch_section_lru(view_id, next_state, "entity")
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    next_state["dataset_revisions"] = dict(updated.dataset_revisions)
    mini_apps.set_view_state(view_id, next_state)
    updated = mini_apps.get_view(view_id)
    assert updated is not None
    return _payload_from_record(updated, titles), f"Loaded governance {kind} detail."


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

SEARCH_CANDIDATE_CAP = 20


def _search_candidates(ch: ClickHouseManager, query: str) -> list[dict[str, Any]]:
    """Classify a query into typed arms and return ranked candidates.

    Ranks merge as ``(match_rank, -evidence_count)`` and cap at 20. GIP
    queries return ALL matching proposals AND topics — the relation is not
    1:1.
    """
    q = query.strip()
    if len(q) > MAX_QUERY_LENGTH:
        raise ValueError(f"Search query must be at most {MAX_QUERY_LENGTH} characters")
    if not q:
        return []
    lowered = q.lower()
    params: dict[str, Any] = {}
    gip_match = GIP_QUERY_RE.fullmatch(q)
    gip_arms = sql_loader.load_sql("governance", "search_gip_arms", gip_title=_gip_sql("title"))
    if PROPOSAL_ID_RE.fullmatch(lowered):
        params["q"] = lowered
        sql = sql_loader.load_sql("governance", "search_proposal_id")
    elif ADDRESS_RE.fullmatch(lowered):
        params["q"] = lowered
        sql = sql_loader.load_sql("governance", "search_address")
    elif gip_match and int(gip_match.group(1)) <= 0x7FFFFFFF:
        # Same Int32 guard as the plain-numeric arm — an oversized "GIP-…"
        # number skips the GIP arm (falls through to text search) instead of
        # overflowing the {gip:Int32} bind.
        params["gip"] = int(gip_match.group(1))
        sql = sql_loader.load_sql("governance", "search_gip", gip_arms=gip_arms)
    elif FORUM_ID_RE.fullmatch(q) and int(q) <= 0x7FFFFFFF:
        params["n"] = int(q)
        params["gip"] = int(q)
        sql = sql_loader.load_sql("governance", "search_forum_id", gip_arms=gip_arms)
    else:
        params["q"] = q
        rank_sql = (
            "multiIf(lower({col}) = lower({{q:String}}), 0, "
            "positionCaseInsensitive({col}, {{q:String}}) = 1, 1, 2)"
        )
        sql = sql_loader.load_sql(
            "governance", "search_text",
            rank_title=rank_sql.format(col="title"),
            rank_username=rank_sql.format(col="username"),
        )
    result = mini_apps.run_structured_query(
        ch, sql, GOV_DB, params, requested_max_rows=100,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )
    columns = {name: idx for idx, name in enumerate(result.columns)}
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for row in result.rows:
        rank = int(row[columns["match_rank"]]) if "match_rank" in columns else 0
        evidence = int(row[columns["evidence_count"]]) if "evidence_count" in columns else 0
        ranked.append((rank, -evidence, {
            "entity_type": str(row[columns["entity_type"]]),
            "identifier": str(row[columns["identifier"]]),
            "label": str(row[columns["label"]]) if "label" in columns else "",
            "role": str(row[columns["role"]]) if "role" in columns else "",
            "evidence_count": evidence,
        }))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]["entity_type"], item[2]["identifier"]))
    return [candidate for _, _, candidate in ranked[:SEARCH_CANDIDATE_CAP]]


# ---------------------------------------------------------------------------
# Tools + registrar
# ---------------------------------------------------------------------------


def register_governance_tools(mcp, ch: ClickHouseManager) -> None:
    """Register the Governance Explorer resource, tools, and web app."""
    mini_apps.register_app(GOV_APP_ID, title=GOV_TITLE, resource_uri=GOV_URI)

    @mcp.resource(
        GOV_URI,
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {
                "csp": {
                    # Token logos only. An MCP-UI host blocks every remote image
                    # unless the resource names its hosts, so without this the
                    # treasury table renders monograms for everything. NOT
                    # api.coingecko.com: the browser never calls it — the server
                    # does, and the result arrives over the MCP tool channel.
                    "resourceDomains": [
                        "https://assets.coingecko.com",
                        "https://coin-images.coingecko.com",
                    ]
                }
            }
        },
    )
    def serve_governance_app() -> str:
        return get_governance_html()

    @mcp.tool(meta=GOV_APP_META)
    def open_governance(
        section: str = "overview",
        query: str = "",
        entity_type: str = "",
        identifier: str = "",
    ) -> CallToolResult:
        """Open the read-only Governance Explorer over ``governance_db``.

        Covers GnosisDAO Snapshot proposals, votes, voters, and followers
        plus the Discourse forum (topics, posts, contributors, categories),
        with two-tier cross-linking (author-declared discussion URL, then
        exact GIP number). Everything shown is Snapshot off-chain signaling
        and forum activity — never binding on-chain execution. Use ``query``
        to resolve a proposal id, voter address, GIP number, topic/user id,
        or title text; or pass ``entity_type`` + ``identifier`` directly.
        """
        try:
            section_key = section.strip().lower() or "overview"
            if section_key not in VALID_SECTIONS:
                raise ValueError(f"section must be one of {sorted(VALID_SECTIONS)}")
            view_id = mini_apps.create_view(GOV_APP_ID, GOV_TITLE)
            mini_apps.set_view_state(view_id, _empty_state(section_key))
            if entity_type.strip() or identifier.strip():
                if not entity_type.strip() or not identifier.strip():
                    raise ValueError("entity_type and identifier must be provided together")
                payload, summary = _apply_entity_load(
                    ch, view_id, 0, entity_type, identifier
                )
            elif query.strip():
                candidates = _search_candidates(ch, query)
                if len(candidates) == 1:
                    candidate = candidates[0]
                    payload, summary = _apply_entity_load(
                        ch, view_id, 0, candidate["entity_type"],
                        candidate["identifier"],
                    )
                else:
                    record = mini_apps.get_view(view_id)
                    assert record is not None
                    state = {
                        **record.view_state,
                        "search": {"query": query.strip(), "candidates": candidates},
                        "warnings": ([] if candidates else ["no_data"]),
                    }
                    mini_apps.set_view_state(view_id, state)
                    record = mini_apps.get_view(view_id)
                    assert record is not None
                    payload = _payload_from_record(record)
                    summary = f"Governance search returned {len(candidates)} candidate(s)."
            else:
                # Default path: zero ClickHouse queries — all datasets defer.
                record = mini_apps.get_view(view_id)
                assert record is not None
                payload = _payload_from_record(record)
                summary = (
                    f"Governance Explorer opened on {section_key} — datasets "
                    "load in the app (deferred groups)."
                )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_governance_section(
        view_id: str,
        request_id: int,
        section: str,
        query: str = "",
        start_at: str = "",
        end_at: str = "",
        proposal_state: str = "",
        proposal_type: str = "",
        quorum_status: str = "",
        category_id: int = 0,
        forum_status: str = "",
        sort_by: str = "",
        force_refresh: bool = False,
        chain_id: int = 0,
        asset: str = "",
        exclude_ltd: bool = False,
    ) -> CallToolResult:
        """[App-only] Atomically load one Governance Explorer section."""
        try:
            payload, summary = _apply_section_load(
                ch, view_id, request_id, section, query, start_at, end_at,
                proposal_state, proposal_type, quorum_status, category_id,
                forum_status, sort_by, force_refresh, chain_id, asset,
                exclude_ltd,
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_governance_datasets(
        view_id: str,
        request_id: int,
        section: str,
        group: str,
        scope_id: str = "",
        force_refresh: bool = False,
    ) -> CallToolResult:
        """[App-only] Load one deferred governance dataset group (additive)."""
        try:
            payload, summary = _apply_group_load(
                ch, view_id, section, group, scope_id, force_refresh
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def search_governance(
        view_id: str,
        request_id: int,
        query: str,
    ) -> CallToolResult:
        """[App-only] Resolve a proposal, voter, GIP, topic, or contributor."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")
        if not query.strip():
            return mini_apps.error_call_tool_result("query is required")
        try:
            if request_id < int(record.view_state.get("applied_request_id") or 0):
                return mini_apps.payload_to_call_tool_result(
                    _payload_from_record(record),
                    "Ignored stale governance search request.",
                )
            candidates = _search_candidates(ch, query)
            if len(candidates) == 1:
                candidate = candidates[0]
                payload, summary = _apply_entity_load(
                    ch, view_id, request_id, candidate["entity_type"],
                    candidate["identifier"],
                )
                return mini_apps.payload_to_call_tool_result(payload, summary)
            patch = {
                "search": {"query": query.strip(), "candidates": candidates},
                "applied_request_id": int(request_id),
            }
            mini_apps.patch_view_state(view_id, patch)
            payload = MiniAppPayload(
                type="PATCH_VIEW_STATE", view_id=view_id, app_id=GOV_APP_ID,
                title=record.title, patch=patch,
                warnings=[] if candidates else ["no_data"],
            )
            return mini_apps.payload_to_call_tool_result(
                payload, f"Governance search returned {len(candidates)} candidate(s)."
            )
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_governance_entity(
        view_id: str,
        request_id: int,
        entity_type: str,
        identifier: str,
    ) -> CallToolResult:
        """[App-only] Load a resolved governance entity bundle."""
        try:
            payload, summary = _apply_entity_load(
                ch, view_id, request_id, entity_type, identifier
            )
            return mini_apps.payload_to_call_tool_result(payload, summary)
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_governance_overlays(
        view_id: str,
        request_id: int = 0,
    ) -> CallToolResult:
        """[App-only] Resolve CoinGecko icons + spot USD for the visible tokens.

        Never blocks on the network: returns whatever is cached and kicks off
        background fetches, with ``overlay_pending`` in the warnings telling the
        frontend that one retry a few seconds later will find more.

        Prices are CURRENT spot and are applied client-side. A historical series
        valued with them is a constant-price revaluation, NOT historical market
        value — the UI is required to say so. Tokens CoinGecko does not list are
        omitted rather than priced at 0: this treasury holds 19 distinct tokens
        spoofing the symbol ``USDC``, and a fabricated $0 would make them
        indistinguishable from the real one.
        """
        try:
            record = mini_apps.get_view(view_id)
            if record is None:
                return mini_apps.error_call_tool_result(
                    f"Unknown or expired view_id: {view_id}"
                )
            icons, icons_pending = coingecko.build_icon_overlay(
                record.datasets, token_columns=TREASURY_TOKEN_COLUMN_RE
            )
            prices, prices_pending = coingecko.build_price_overlay(
                record.datasets, token_columns=TREASURY_TOKEN_COLUMN_RE
            )
            patch = {
                "icon_overlay": icons,
                "price_overlay": prices,
                "price_overlay_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            mini_apps.patch_view_state(view_id, patch)
            payload = MiniAppPayload(
                type="PATCH_VIEW_STATE",
                view_id=view_id,
                app_id=GOV_APP_ID,
                title=record.title,
                patch=patch,
                warnings=(
                    ["overlay_pending"] if (icons_pending or prices_pending) else []
                ),
            )
            icon_count = sum(len(v) for v in icons.values())
            price_count = sum(len(v) for v in prices.get("by_chain", {}).values())
            return mini_apps.payload_to_call_tool_result(
                payload,
                f"Overlays: {icon_count} icon(s), {price_count} price(s)"
                + (" — more pending." if (icons_pending or prices_pending) else "."),
            )
        except Exception as exc:
            return mini_apps.error_call_tool_result(str(exc))

    for name in (
        "load_governance_section", "load_governance_datasets",
        "search_governance", "load_governance_entity",
        "load_governance_overlays",
    ):
        mini_apps.mark_app_only(name)

    web_apps.register_web_app(
        app_id=GOV_APP_ID,
        open_tool="open_governance",
        html_loader=get_governance_html,
        title=GOV_TITLE,
        description=(
            "Explore GnosisDAO Snapshot proposals, votes, and voters plus "
            "forum topics, posts, and contributors — off-chain signaling and "
            "forum activity, cross-linked by discussion URL and GIP number."
        ),
        icon="⚖",
        diagnostics_loader=get_governance_diagnostics,
        tools={
            "open_governance": open_governance,
            "load_governance_section": load_governance_section,
            "load_governance_datasets": load_governance_datasets,
            "search_governance": search_governance,
            "load_governance_entity": load_governance_entity,
            "load_governance_overlays": load_governance_overlays,
        },
    )


__all__ = [
    "GOV_APP_ID", "GOV_TITLE", "GOV_URI", "GOV_DB", "SECTION_GROUPS",
    "ENTITY_BUNDLES", "GIP_PATTERN", "get_governance_html",
    "get_governance_diagnostics", "register_governance_tools",
    "reset_failure_cache_for_tests", "_search_candidates",
]
