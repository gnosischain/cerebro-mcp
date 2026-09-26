"""Contract tests for the read-only Governance Explorer miniapp."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import INTERACTIVE_QUERY_BUDGET, ExecutedQuery
from cerebro_mcp.models.mini_app import DatasetStats
from cerebro_mcp.runtime.mini_app_cache import CachedDataset, reset_cache_for_tests
from cerebro_mcp.security import RiskClass, TOOL_RISK_REGISTRY
from cerebro_mcp.tools.tool_meta import TOOL_META
from cerebro_mcp.tools.visualization import governance_explorer, mini_apps, web_apps
from tests.gip_fixtures import GIP_TITLE_FIXTURES
from tests.sql_text import sql_code


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
PROPOSAL_ID = "0x" + "ab" * 32
VOTER = "0x" + "cd" * 20
ASSET = "0x" + "ef" * 20
SENTINEL_TEXT = "zz_sentinel_zz"
GOV_TOOLS = (
    "open_governance", "load_governance_section", "load_governance_datasets",
    "search_governance", "load_governance_entity", "load_governance_overlays",
)
APP_ONLY_TOOLS = frozenset(GOV_TOOLS) - {"open_governance"}


def test_sql_code_strips_comments_and_keeps_code():
    """The helper above is load-bearing for several guards, so it is itself
    tested — an assertion helper that quietly stops working takes every guard
    that depends on it down with it, silently."""
    assert sql_code("SELECT 1 -- FINAL\n-- months months\nFROM t") == (
        "SELECT 1 \n\nFROM t"
    )
    assert "FINAL" not in sql_code("-- and in the FINAL select")
    # A string literal containing `--` would be mangled; assert none exists on
    # this plane, so the simple splitter stays correct.
    for spec in governance_explorer._treasury_specs(
        governance_explorer._range_state("", ""),
        governance_explorer._default_filters(),
    ):
        for line in spec.sql.splitlines():
            head, _, tail = line.partition("--")
            assert head.count("'") % 2 == 0, f"{spec.key}: `--` inside a literal"


class StubCH:
    """ClickHouse stub used by the one-pass exact-capped dataset loader.

    Records ``(sql, database, max_rows, parameters, query_budget)`` per call
    and echoes ``__source_rows`` for exact-capped fetches. Freshness-shaped
    queries return the two-source clock rows so ``_freshness_state`` parses.
    """

    def __init__(self, *, total: int = 2, fail_marker: str = ""):
        self.total = total
        self.fail_marker = fail_marker
        self.calls: list[tuple[str, str, int, dict | None, object]] = []

    def run_query(
        self,
        sql,
        database="dbt",
        requested_max_rows=100,
        audience="tool",
        fetch_mode="auto",
        parameters=None,
        query_budget=None,
    ):
        self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
        if self.fail_marker and self.fail_marker in sql:
            raise RuntimeError("planned dataset failure")
        exact_capped = "__source_rows" in sql
        if "AS latest_ingested_at" in sql:
            columns = ["source", "latest_ingested_at", "latest_activity_at"]
            rows = [["snapshot", NOW, NOW], ["forum", NOW, NOW]]
            if exact_capped:
                columns = [*columns, "__source_rows"]
                rows = [[*row, 2] for row in rows]
            return self._result(sql, database, columns, rows)
        n = min(self.total, requested_max_rows)
        columns = ["id", "created_at"]
        rows = [[index, NOW] for index in range(n)]
        if exact_capped:
            columns = [*columns, "__source_rows"]
            rows = [[*row, self.total] for row in rows]
        return self._result(sql, database, columns, rows)

    @staticmethod
    def _result(sql, database, columns, rows):
        return ExecutedQuery(
            sql=sql,
            executed_sql=sql,
            database=database,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            elapsed_seconds=0.001,
            fetch_mode="rows",
            warnings=[],
        )


SEARCH_COLUMNS = ["entity_type", "identifier", "label", "role", "evidence_count", "match_rank"]


class SearchCH(StubCH):
    """Branches on the classifier arms' distinctive SQL markers."""

    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None, query_budget=None):
        if "'proposal' AS role" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            return self._result(sql, database, SEARCH_COLUMNS,
                                [["proposal", PROPOSAL_ID, "GIP-151: Example", "proposal", 250, 0]])
        if "'follower'" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            return self._result(sql, database, SEARCH_COLUMNS,
                                [["voter", VOTER, VOTER, "voter", 42, 0],
                                 ["voter", VOTER, VOTER, "follower", 1, 0]])
        if "id = {n:UInt32}" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            return self._result(sql, database, SEARCH_COLUMNS,
                                [["forum_topic", "12131", "GIP-149 discussion", "forum_topic", 55, 0],
                                 ["forum_user", "12131", "someone", "forum_user", 3, 0]])
        if "'gip_proposal'" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            return self._result(sql, database, SEARCH_COLUMNS,
                                [["proposal", PROPOSAL_ID, "GIP-151: Example", "gip_proposal", 500, 0],
                                 ["forum_topic", "9001", "GIP-151 discussion", "gip_topic", 30, 0]])
        if "'proposal_title'" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            return self._result(sql, database, SEARCH_COLUMNS,
                                [["proposal", PROPOSAL_ID, "Treasury diversification", "proposal_title", 120, 1],
                                 ["forum_topic", "77", "Treasury talk", "topic_title", 900, 2]])
        return super().run_query(sql, database, requested_max_rows, audience, fetch_mode, parameters, query_budget)


class SingleHitSearchCH(SearchCH):
    """Every classifier arm resolves to exactly ONE candidate (auto-load)."""

    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None, query_budget=None):
        if "'proposal' AS role" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            return self._result(sql, database, SEARCH_COLUMNS,
                                [["proposal", PROPOSAL_ID, "GIP-151: Example", "proposal", 250, 0]])
        return StubCH.run_query(self, sql, database, requested_max_rows, audience, fetch_mode, parameters, query_budget)


class FreshCH(StubCH):
    """Snapshot ingestion clock lagging >24h behind now (stale)."""

    def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None, query_budget=None):
        if "AS latest_ingested_at" in sql:
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            stale = datetime.now(timezone.utc) - timedelta(days=3)
            fresh = datetime.now(timezone.utc) - timedelta(hours=1)
            columns = ["source", "latest_ingested_at", "latest_activity_at"]
            rows = [["snapshot", stale, stale], ["forum", fresh, fresh]]
            if "__source_rows" in sql:
                columns = [*columns, "__source_rows"]
                rows = [[*row, 2] for row in rows]
            return self._result(sql, database, columns, rows)
        return super().run_query(sql, database, requested_max_rows, audience, fetch_mode, parameters, query_budget)


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    governance_explorer.reset_failure_cache_for_tests()
    mini_apps.reset_views_for_tests()
    web_apps.WEB_APP_CONFIGS.pop(governance_explorer.GOV_APP_ID, None)
    for name in GOV_TOOLS:
        web_apps.MINI_APP_TOOL_REGISTRY.pop(name, None)
    yield
    reset_cache_for_tests()
    governance_explorer.reset_failure_cache_for_tests()
    mini_apps.reset_views_for_tests()


def _server(ch=None):
    server = FastMCP("governance-test")
    ch = ch or StubCH()
    mini_apps.register_mini_app_infra(server, ch)
    governance_explorer.register_governance_tools(server, ch)
    return server, ch


def _tool(server, name):
    return next(t.fn for t in server._tool_manager._tools.values() if t.name == name)


def _all_section_group_keys() -> set[str]:
    return {
        key
        for groups in governance_explorer.SECTION_GROUPS.values()
        for keys in groups.values()
        for key in keys
    }


def _all_specs() -> list[governance_explorer.QuerySpec]:
    """Every spec builder with all applicable filters set to bindable
    sentinel values (an absolute custom range so date binds exist too)."""
    range_state = governance_explorer._range_state(
        "2025-01-01T00:00:00Z", "2025-06-30T00:00:00Z"
    )
    defaults = governance_explorer._default_filters()
    specs: list[governance_explorer.QuerySpec] = []
    specs += governance_explorer._overview_specs(range_state)
    specs += governance_explorer._proposals_specs(range_state, {
        **defaults, "query": SENTINEL_TEXT, "proposal_state": "closed",
        "proposal_type": "basic", "quorum_status": "met",
        "sort_by": "quorum_ratio",
    })
    specs += governance_explorer._voters_specs(range_state, {
        **defaults, "sort_by": "vote_count",
    })
    specs += governance_explorer._forum_specs(range_state, {
        **defaults, "query": SENTINEL_TEXT, "category_id": 424242,
        "forum_status": "archived", "sort_by": "most_posts",
    })
    specs += governance_explorer._delegations_specs(range_state, {
        **defaults, "sort_by": "recently_active",
    })
    specs += governance_explorer._graph_specs(range_state)
    specs += governance_explorer._treasury_specs(range_state, {
        **defaults, "chain_id": 100, "exclude_ltd": True, "sort_by": "supply_share",
    })
    for kind, identifier in (
        ("proposal", PROPOSAL_ID), ("voter", VOTER),
        ("forum_topic", "987654"), ("forum_user", "987654"),
        ("treasury_wallet", "100:0x0000000000000000000000000000000000000001"),
        ("treasury_token", "100:0x0000000000000000000000000000000000000002"),
    ):
        specs += governance_explorer._entity_specs(kind, identifier)
    return specs


# ---------------------------------------------------------------------------
# Launch / flow
# ---------------------------------------------------------------------------


def test_launcher_opens_with_zero_clickhouse_round_trips():
    server, ch = _server()
    result = _tool(server, "open_governance")()
    payload = result.structuredContent
    assert payload["type"] == "INITIAL_LOAD"
    assert payload["app_id"] == "governance"
    assert payload["view_state"]["section"] == "overview"
    assert payload["view_state"]["date_range"]["kind"] == "all"
    # v2 contract: the open path never touches ClickHouse — all datasets defer.
    assert payload["datasets"] == {}
    assert ch.calls == []
    groups = payload["view_state"]["loaded_groups"]
    assert groups["overview.core"] is False
    assert groups["overview.insights"] is False
    assert set(groups) == {
        f"{section}.{group}"
        for section, section_groups in governance_explorer.SECTION_GROUPS.items()
        for group in section_groups
    }


def test_open_with_entity_args_loads_entity_bundle():
    server, ch = _server()
    result = _tool(server, "open_governance")(
        entity_type="proposal", identifier=PROPOSAL_ID
    )
    payload = result.structuredContent
    assert payload["view_state"]["section"] == "entity"
    assert payload["view_state"]["selected_entity"]["entity_type"] == "proposal"
    assert payload["view_state"]["selected_entity"]["identifier"] == PROPOSAL_ID
    assert set(payload["datasets"]) == set(governance_explorer.ENTITY_BUNDLES["proposal"])
    assert ch.calls  # entity bundles load eagerly
    assert all(call[1] == "governance_db" for call in ch.calls)


def test_open_with_query_autoloads_single_candidate():
    server, _ = _server(SingleHitSearchCH())
    result = _tool(server, "open_governance")(query=PROPOSAL_ID)
    payload = result.structuredContent
    assert payload["view_state"]["section"] == "entity"
    assert payload["view_state"]["selected_entity"]["identifier"] == PROPOSAL_ID


def test_section_apply_loads_core_and_datasets_tool_streams_the_rest():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent
    assert applied["type"] == "INITIAL_LOAD"
    assert set(applied["datasets"]) == {
        "space_summary", "source_freshness", "governance_activity"
    }
    assert applied["view_state"]["loaded_groups"]["overview.core"] is True
    assert applied["view_state"]["loaded_groups"]["overview.insights"] is False
    assert all(call[1] == "governance_db" for call in ch.calls)
    scope_id = applied["view_state"]["scope_id"]
    grouped = _tool(server, "load_governance_datasets")(
        view_id=view_id, request_id=0, section="overview", group="insights",
        scope_id=scope_id,
    ).structuredContent
    assert grouped["type"] == "PATCH_VIEW_STATE"
    # Group loads refresh source_freshness too (300s cache keeps it cheap).
    assert set(grouped["datasets"]) == {
        "proposal_types", "quorum_distribution", "voter_power_concentration",
        "latest_activity", "forum_category_activity", "source_freshness",
    }
    assert grouped["patch"]["loaded_groups"] == {"overview.insights": True}
    assert set(grouped["patch"]["dataset_revisions"]) == set(grouped["datasets"])
    record = mini_apps.get_view(view_id)
    assert record is not None
    assert set(record.datasets) >= _all_section_group_keys() & {
        "space_summary", "source_freshness", "governance_activity",
        "proposal_types", "quorum_distribution", "voter_power_concentration",
        "latest_activity", "forum_category_activity",
    }


def test_group_load_with_stale_scope_id_is_a_noop():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    )
    call_count = len(ch.calls)
    stale = _tool(server, "load_governance_datasets")(
        view_id=view_id, request_id=0, section="overview", group="insights",
        scope_id="overview:999",
    ).structuredContent
    assert stale["type"] == "PATCH_VIEW_STATE"
    assert "stale_scope" in stale["warnings"]
    assert stale.get("datasets") in (None, {})
    assert len(ch.calls) == call_count


def test_section_transition_retains_scopes_and_fingerprint_short_circuits():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    )
    loaded = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=2, section="proposals"
    ).structuredContent
    assert loaded["view_state"]["applied_request_id"] == 2
    # Proposals core loads; the overview core datasets are RETAINED.
    assert {"proposal_summary", "proposals"} <= set(loaded["datasets"])
    assert {"space_summary", "governance_activity"} <= set(loaded["datasets"])
    assert "proposal_activity" not in loaded["datasets"]  # charts group defers
    # Tab return with an unchanged scope: zero ClickHouse round trips.
    call_count = len(ch.calls)
    restored = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=3, section="overview"
    ).structuredContent
    assert restored["view_state"]["section"] == "overview"
    assert len(ch.calls) == call_count


def test_lru_evicts_beyond_five_retained_scopes():
    server, _ = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    for request_id, section in enumerate(
        ("overview", "proposals", "voters", "forum"), start=1
    ):
        _tool(server, "load_governance_section")(
            view_id=view_id, request_id=request_id, section=section
        )
    _tool(server, "load_governance_entity")(
        view_id=view_id, request_id=5, entity_type="voter", identifier=VOTER
    )
    record = mini_apps.get_view(view_id)
    assert record is not None
    state = dict(record.view_state)
    # 4 sections + the entity pseudo-section fit exactly — nothing evicted.
    assert state["section_lru"] == ["overview", "proposals", "voters", "forum", "entity"]
    assert "space_summary" in record.datasets
    assert "voter_profile" in record.datasets
    # A 6th retained scope evicts the LRU victim (overview) — but the
    # source_freshness dataset survives eviction by contract.
    governance_explorer._touch_section_lru(view_id, state, "synthetic_extra")
    assert "overview" not in state["section_lru"]
    assert state["loaded_groups"]["overview.core"] is False
    record = mini_apps.get_view(view_id)
    assert record is not None
    assert "space_summary" not in record.datasets
    assert "governance_activity" not in record.datasets
    assert "source_freshness" in record.datasets


def test_force_refresh_bypasses_fingerprint_and_cache_and_repopulates():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent
    first = applied["view_state"]["dataset_revisions"]["space_summary"]
    call_count = len(ch.calls)
    refreshed = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=2, section="overview", force_refresh=True
    ).structuredContent
    assert len(ch.calls) > call_count
    assert refreshed["view_state"]["dataset_revisions"]["space_summary"] > first
    assert refreshed["datasets"]["space_summary"]["stats"]["fetched_at"]


def test_partial_dataset_failure_keeps_successful_datasets():
    server, _ = _server(StubCH(fail_marker="topics_in_range DESC"))
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent
    grouped = _tool(server, "load_governance_datasets")(
        view_id=view_id, request_id=0, section="overview", group="insights",
        scope_id=applied["view_state"]["scope_id"],
    ).structuredContent
    # Failure contract: the failed dataset stays VISIBLE as a zero-row stub
    # whose provenance carries the error, and the group is "partial".
    assert "forum_category_activity" in grouped["datasets"]
    stub = grouped["datasets"]["forum_category_activity"]
    assert stub["preview_rows"] == []
    assert stub["provenance"]["coverage"]["error"]
    assert "proposal_types" in grouped["datasets"]
    assert "query_failed" in grouped["warnings"]
    assert grouped["patch"]["coverage"]["forum_category_activity"]["warning_codes"] == ["query_failed"]
    assert grouped["patch"]["loaded_groups"]["overview.insights"] == "partial"
    record = mini_apps.get_view(view_id)
    assert record is not None
    assert record.datasets["forum_category_activity"].rows == []


def test_negative_cache_replays_failure_without_requerying():
    """A dataset that just failed must NOT re-run its query on retry."""
    server, ch = _server(StubCH(fail_marker="topics_in_range DESC"))
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent
    scope_id = applied["view_state"]["scope_id"]
    first = _tool(server, "load_governance_datasets")(
        view_id=view_id, request_id=0, section="overview", group="insights",
        scope_id=scope_id,
    ).structuredContent
    assert "query_failed" in first["warnings"]
    failing_after_first = sum(1 for call in ch.calls if "topics_in_range DESC" in call[0])
    second = _tool(server, "load_governance_datasets")(
        view_id=view_id, request_id=0, section="overview", group="insights",
        scope_id=scope_id,
    ).structuredContent
    failing_after_second = sum(1 for call in ch.calls if "topics_in_range DESC" in call[0])
    # The failing query ran once; the retry replayed the cached failure.
    assert failing_after_second == failing_after_first
    assert "cached failure" in " ".join(second["warnings"])
    # An explicit force refresh IS allowed to re-run it.
    _tool(server, "load_governance_datasets")(
        view_id=view_id, request_id=0, section="overview", group="insights",
        scope_id=scope_id, force_refresh=True,
    )
    assert sum(1 for call in ch.calls if "topics_in_range DESC" in call[0]) > failing_after_second


def test_dataset_revisions_monotonic_across_reloads():
    server, _ = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    first = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="voters"
    ).structuredContent["view_state"]["dataset_revisions"]["voter_summary"]
    second = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=2, section="voters", force_refresh=True
    ).structuredContent["view_state"]["dataset_revisions"]["voter_summary"]
    third = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=3, section="voters", force_refresh=True
    ).structuredContent["view_state"]["dataset_revisions"]["voter_summary"]
    assert first < second < third


def test_unknown_view_id_errors():
    server, ch = _server()
    result = _tool(server, "load_governance_section")(
        view_id="missing", request_id=1, section="overview"
    )
    assert result.isError
    assert ch.calls == []


def test_stale_request_id_ignored():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_governance_section")(
        view_id=view_id, request_id=2, section="proposals"
    )
    call_count = len(ch.calls)
    stale = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="forum"
    ).structuredContent
    assert stale["view_state"]["applied_request_id"] == 2
    assert stale["view_state"]["section"] == "proposals"
    assert len(ch.calls) == call_count


# ---------------------------------------------------------------------------
# Query contract
# ---------------------------------------------------------------------------


def test_every_spec_targets_governance_db_with_final_order_by_and_binds():
    # External planes whose views resolve dedup internally: reading them is
    # allowed without governance_db and without FINAL. Fully qualified on
    # purpose — a bare "db." prefix would let any spec that merely mentions the
    # string (even in a comment) skip the governance_db existence check.
    external_plane_refs = (
        f"{governance_explorer.DELEGATE_DB}.{governance_explorer.DELEGATE_VIEW}",
        # Treasury: the served two-step read (lesson: published-is-not-served).
        f"{governance_explorer.TREASURY_DB}.{governance_explorer.TREASURY_BALANCES_TABLE}",
        f"{governance_explorer.TREASURY_DB}.{governance_explorer.TREASURY_SERVED_VIEW}",
        f"{governance_explorer.TREASURY_DB}.{governance_explorer.TREASURY_PUB_TABLE}",
        governance_explorer.TREASURY_PRICE_HUB,
    )
    specs = _all_specs()
    assert specs
    for spec in specs:
        sql = spec.sql
        # Any governance_db.<table> still present (e.g. the cross power spec's
        # snapshot_votes) MUST still carry FINAL, external plane or not.
        reads_external_plane = any(ref in sql for ref in external_plane_refs)
        if not reads_external_plane:
            assert "governance_db." in sql, spec.key
        # Every governance_db.<table> reference is followed by FINAL
        # (optionally through an alias) — no carve-outs.
        for match in re.finditer(r"governance_db\.[a-z_]+", sql):
            tail = sql[match.end():]
            assert re.match(r"\s+(AS\s+\w+\s+)?FINAL\b", tail), (
                f"{spec.key}: {match.group(0)} not followed by FINAL"
            )
        assert "ORDER BY" in sql.upper(), spec.key
        assert "SETTINGS" not in sql.upper(), spec.key
        assert len(sql) <= 9_900, spec.key
        # User values are bound server-side, never interpolated into SQL.
        assert SENTINEL_TEXT not in sql, spec.key
        assert "424242" not in sql, spec.key
        assert "987654" not in sql, spec.key
        assert PROPOSAL_ID not in sql, spec.key
        assert VOTER not in sql, spec.key
        assert ASSET not in sql, spec.key
        assert "2025-01-01" not in sql, spec.key
        for value in (spec.parameters or {}).values():
            assert str(value) not in ("", None)
    # The sentinel values DID reach the parameters of the filtered specs.
    by_key = {spec.key: spec for spec in specs}
    assert by_key["proposals"].parameters["query"] == SENTINEL_TEXT
    assert by_key["proposals"].parameters["proposal_state"] == "closed"
    assert by_key["proposals"].parameters["quorum_status"] == "met"
    assert by_key["forum_topics"].parameters["category_id"] == 424242
    assert by_key["proposal_detail"].parameters["proposal_id"] == PROPOSAL_ID
    assert by_key["voter_profile"].parameters["voter"] == VOTER
    assert by_key["topic_detail"].parameters["topic_id"] == 987654
    assert by_key["contributor_profile"].parameters["user_id"] == 987654
    assert by_key["proposals"].parameters["start_at"] == "2025-01-01T00:00:00Z"


def test_poll_specs_respect_option_grain():
    """forum_polls is poll-OPTION grain: ``voters`` is a poll-level total
    REPEATED per option row (max per poll, never summed across options), and
    ``option_votes = -1`` is Discourse's hidden-results sentinel (NULL, never
    summed raw). Assertions run on rendered spec SQL — comments are already
    stripped by the loader."""
    specs = {spec.key: spec for spec in _all_specs()}
    poll_readers = {key for key, spec in specs.items() if "governance_db.forum_polls" in spec.sql}
    # Growing this set means a new forum_polls reader — it must adopt the
    # grain guards below (or, like source_freshness, read ingested_at only).
    assert poll_readers == {
        "poll_summary", "forum_polls", "poll_activity", "topic_polls",
        "source_freshness",
    }
    for key in ("poll_summary", "forum_polls", "poll_activity"):
        sql = specs[key].sql
        assert "max(p.voters)" in sql, key
        assert "sum(p.option_votes" not in sql, key
        assert "sum(option_votes" not in sql, key
    # The list spec guards the sentinel, positive-score ties, and the
    # zero-vote case (an all-zero poll has no leader).
    list_sql = specs["forum_polls"].sql
    assert "min(p.option_votes) < 0" in list_sql
    assert "arrayMax" in list_sql
    assert "max(p.option_votes) > 0" in list_sql
    # The entity spec is per-option BY DESIGN (exempt from the voters-max
    # rule) but must neutralize the sentinel.
    assert "nullIf(p.option_votes, -1)" in specs["topic_polls"].sql
    # The activity spec has no business reading option votes at all.
    assert "option_votes" not in specs["poll_activity"].sql
    # Freshness reads ingested_at only.
    assert "option_votes" not in specs["source_freshness"].sql


def test_like_specs_apply_the_eligibility_contract():
    """Every analytical forum_likes read is ACTIVE (hidden/deleted excluded),
    MAPPED (referenced topic and post still exist), and scoped by the
    filters-only topic subquery — never by topic_where, whose last_posted_at
    window silently drops valid in-range likes on quiet topics."""
    specs = {spec.key: spec for spec in _all_specs()}
    like_readers = {key for key, spec in specs.items() if "governance_db.forum_likes" in spec.sql}
    assert like_readers == {
        "forum_summary", "contributor_leaderboard", "likes_activity",
        "likes_by_category", "most_liked_topics", "topic_likes_activity",
        "source_freshness",
    }
    for key in sorted(like_readers):
        sql = specs[key].sql
        if key == "source_freshness":
            # Freshness deliberately skips the contract: it reads ingested_at
            # only, and filtering there would let a purge fake freshness.
            assert "hidden" not in sql
            continue
        assert "hidden = 0" in sql, key
        assert "deleted = 0" in sql, key
        # Topic scope: section specs filter via the topics subquery; entity
        # specs pin one topic by bind. Both count as scoped+mapped.
        assert (
            "topic_id IN (" in sql
            or "topic_id = {topic_id:UInt32}" in sql
        ), key
        assert (
            "post_id IN (SELECT id FROM governance_db.forum_posts" in sql
            or "ON fp.id = l.post_id" in sql
        ), key
    # Like predicates never window on last_posted_at.
    for key in ("likes_activity", "most_liked_topics", "contributor_leaderboard"):
        assert "last_posted_at" not in specs[key].sql, key
    # forum_summary legitimately uses topic_where (last_posted_at) for its
    # TOPIC aggregates — but its like subselects must scope by filters only.
    summary_sql = specs["forum_summary"].sql
    likes_chunk = summary_sql.split(" AS likes_in_range")[0].rsplit("(SELECT count()", 1)[1]
    assert "last_posted_at" not in likes_chunk
    assert "hidden = 0 AND deleted = 0" in likes_chunk


def test_proposal_votes_share_is_null_safe():
    """vp_share divides by the proposal's scores_total through nullIf — a
    pending/zero total yields NULL, never a fabricated 0 or a div-by-zero."""
    specs = {spec.key: spec for spec in _all_specs()}
    sql = specs["proposal_votes"].sql
    assert "vp_share" in sql
    assert "nullIf" in sql
    assert "scores_total" in sql


def test_forum_summary_counts_like_exclusions():
    """Ineligible likes are COUNTED, never silently dropped, and the
    attribution share ships as a live figure for the UI caption (NULL, never
    a division by zero, when the counter denominator is empty)."""
    specs = {spec.key: spec for spec in _all_specs()}
    sql = specs["forum_summary"].sql
    assert "likes_hidden_or_deleted" in sql
    assert "likes_unmapped" in sql
    assert "like_attribution_pct" in sql
    assert "nullIf" in sql


def test_source_freshness_forum_clock_is_weakest_link():
    """The forum ingestion clock is the min across every forum table's own
    max(ingested_at) — a stalled polls/likes ingest must not be masked by a
    fresh posts/topics run. Snapshot keeps max() (one ingest run)."""
    sql = governance_explorer._source_freshness_spec().sql
    for table in (
        "forum_topics", "forum_posts", "forum_users", "forum_categories",
        "forum_polls", "forum_likes",
    ):
        assert f"governance_db.{table}" in sql, table
    assert (
        "if(source = 'forum', min(latest_ingested_at), max(latest_ingested_at))"
        in sql
    )


def test_specs_carry_interactive_query_budget():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    )
    assert ch.calls
    assert all(call[4] is INTERACTIVE_QUERY_BUDGET for call in ch.calls)


def test_row_cap_truncation_reports_result_truncated():
    server, _ = _server(StubCH(total=10_050))
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="proposals"
    ).structuredContent
    coverage = applied["view_state"]["coverage"]["proposals"]
    assert coverage["truncated"] is True
    assert coverage["source_rows"] == 10_050
    assert "result_truncated" in coverage["warning_codes"]
    assert "result_truncated" in applied["view_state"]["warnings"]


def test_exact_capped_rejects_unordered_sql_before_querying():
    ch = StubCH()
    with pytest.raises(mini_apps.MiniAppQueryError, match="ORDER BY"):
        mini_apps.load_exact_capped_dataset(
            ch, "SELECT id FROM governance_db.snapshot_proposals FINAL",
            database="governance_db",
        )
    assert ch.calls == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_date_range_presets_and_custom_validation():
    all_history = governance_explorer._range_state("", "")
    assert all_history["kind"] == "all"
    assert all_history["start_at"] == "" and all_history["end_at"] == ""
    ninety = governance_explorer._range_state("90d", "")
    assert ninety == {
        "kind": "relative", "window_days": 90, "anchor": "now",
        "start_at": "90d", "end_at": "",
    }
    year = governance_explorer._range_state("1y", "")
    assert year["window_days"] == 365 and year["start_at"] == "1y"
    custom = governance_explorer._range_state(
        "2025-01-01T00:00:00Z", "2025-06-30T12:30:00Z"
    )
    assert custom["kind"] == "absolute"
    assert custom["start_at"] == "2025-01-01T00:00:00Z"
    assert custom["end_at"] == "2025-06-30T12:30:00Z"
    for start, end in (
        ("90d", "2025-06-30T00:00:00Z"),   # preset + end_at
        ("1y", "2025-06-30T00:00:00Z"),
        ("2025-01-01T00:00:00Z", ""),      # start-only
        ("", "2025-06-30T00:00:00Z"),      # end-only
        ("not-a-date", "2025-06-30T00:00:00Z"),
        ("2025-06-30T00:00:00Z", "2025-01-01T00:00:00Z"),  # start >= end
    ):
        with pytest.raises(ValueError):
            governance_explorer._range_state(start, end)


def test_filter_enums_and_section_applicability_validated_before_sql():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    assert ch.calls == []
    invalid_calls = (
        {"section": "proposals", "proposal_state": "bogus"},
        {"section": "proposals", "proposal_type": "mystery"},
        {"section": "proposals", "quorum_status": "sideways"},
        {"section": "forum", "forum_status": "hidden"},
        {"section": "forum", "category_id": -1},
        # Strict per-section applicability.
        {"section": "voters", "proposal_state": "closed"},
        {"section": "proposals", "category_id": 21},
        {"section": "overview", "forum_status": "open"},
        {"section": "voters", "query": "hello"},
        {"section": "overview", "query": "hello"},
        # Over-long query text.
        {"section": "proposals", "query": "x" * 201},
    )
    for kwargs in invalid_calls:
        result = _tool(server, "load_governance_section")(
            view_id=view_id, request_id=1, **kwargs
        )
        assert result.isError, kwargs
        assert ch.calls == [], kwargs


def test_sort_by_whitelist_per_section_maps_to_fixed_order_by():
    range_state = governance_explorer._range_state("", "")
    defaults = governance_explorer._default_filters()
    for sort_by, fragment in governance_explorer.PROPOSAL_SORTS.items():
        spec = {s.key: s for s in governance_explorer._proposals_specs(
            range_state, {**defaults, "sort_by": sort_by}
        )}["proposals"]
        assert spec.sql.rstrip().endswith(f"ORDER BY {fragment}"), sort_by
    for sort_by, fragment in governance_explorer.VOTER_SORTS.items():
        spec = {s.key: s for s in governance_explorer._voters_specs(
            range_state, {**defaults, "sort_by": sort_by}
        )}["voter_leaderboard"]
        assert spec.sql.rstrip().endswith(f"ORDER BY {fragment}"), sort_by
    for sort_by, fragment in governance_explorer.FORUM_SORTS.items():
        spec = {s.key: s for s in governance_explorer._forum_specs(
            range_state, {**defaults, "sort_by": sort_by}
        )}["forum_topics"]
        assert spec.sql.rstrip().endswith(f"ORDER BY {fragment}"), sort_by
    for sort_by, fragment in governance_explorer.DELEGATE_SORTS.items():
        spec = {s.key: s for s in governance_explorer._delegations_specs(
            range_state, {**defaults, "sort_by": sort_by}
        )}["top_delegates"]
        assert spec.sql.rstrip().endswith(f"ORDER BY {fragment}"), sort_by
    # A cross-section sort name is rejected at validation time.
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    result = _tool(server, "load_governance_section")(
        view_id=opened.structuredContent["view_id"], request_id=1,
        section="proposals", sort_by="most_posts",
    )
    assert result.isError
    assert ch.calls == []


def test_entity_identifier_validation_short_circuits_before_sql():
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    for entity_type, identifier in (
        ("proposal", "0xdead"),
        ("proposal", VOTER),           # address-length hex is not a proposal id
        ("voter", "not-an-address"),
        ("voter", PROPOSAL_ID),        # proposal-length hex is not an address
        ("forum_topic", "0"),
        ("forum_topic", "abc"),
        ("forum_user", "-5"),
        ("mystery", "1"),
    ):
        result = _tool(server, "load_governance_entity")(
            view_id=view_id, request_id=1,
            entity_type=entity_type, identifier=identifier,
        )
        assert result.isError, (entity_type, identifier)
        assert ch.calls == [], (entity_type, identifier)
    # Addresses normalize to lowercase before hitting SQL.
    assert governance_explorer._validate_entity_identifier(
        "voter", VOTER.upper().replace("0X", "0x")
    ) == VOTER


# ---------------------------------------------------------------------------
# Semantics
# ---------------------------------------------------------------------------


def test_quorum_sql_contract_never_passed_failed():
    assert "nullIf" in governance_explorer.QUORUM_RATIO_SQL
    assert "multiIf" in governance_explorer.QUORUM_STATUS_SQL
    for banned in ("passed", "failed", "winner"):
        assert banned not in governance_explorer.QUORUM_STATUS_SQL.lower()
    quorum = {s.key: s for s in governance_explorer._overview_specs(
        governance_explorer._range_state("", "")
    )}["quorum_distribution"]
    assert "multiIf(quorum <= 0, 'unspecified'" in quorum.sql
    assert "nullIf(quorum, 0)" in quorum.sql
    for spec in _all_specs():
        haystack = f"{spec.key} {spec.title} {spec.sql}".lower()
        for banned in ("passed", "failed", "winner"):
            assert banned not in haystack, (spec.key, banned)


def test_leading_choice_derivation_guards_shape():
    spec = {s.key: s for s in governance_explorer._proposals_specs(
        governance_explorer._range_state("", ""),
        governance_explorer._default_filters(),
    )}["proposals"]
    assert "length(choices) = length(scores)" in spec.sql
    assert "indexOf(scores, arrayMax(scores))" in spec.sql
    assert "leading_choice_share" in spec.sql
    assert "choice_shape_flagged" in spec.sql


def test_choice_classification_columns_and_unsupported_warning():
    votes = {s.key: s for s in governance_explorer._entity_specs(
        "proposal", PROPOSAL_ID
    )}["proposal_votes"]
    assert "JSONType" in votes.sql
    assert "'unsupported'" in votes.sql
    for column in ("choice_kind", "choice_index", "choice_indexes", "reason"):
        assert f"AS {column}" in votes.sql
    flagged = CachedDataset(
        columns=["choice_kind", "choice_index", "choice_indexes"],
        column_types=["str", "int", "list"],
        rows=[["single", 1, []], ["unsupported", None, []]],
        stats=DatasetStats(row_count=2, rows_returned=2, mode="exact_capped"),
        sql="--", database="governance_db",
    )
    assert governance_explorer._choice_warning_scan(flagged) is True
    clean = CachedDataset(
        columns=["choice_kind", "choice_index", "choice_indexes"],
        column_types=["str", "int", "list"],
        rows=[["single", 2, []], ["ranked", None, [2, 1, 3]]],
        stats=DatasetStats(row_count=2, rows_returned=2, mode="exact_capped"),
        sql="--", database="governance_db",
    )
    assert governance_explorer._choice_warning_scan(clean) is False
    coverage, codes = governance_explorer._coverage_from_dataset(
        flagged, votes, governance_explorer._range_state("", "")
    )
    assert "unsupported_choice_shape" in codes
    assert "unsupported_choice_shape" in coverage["warning_codes"]


@pytest.mark.parametrize(
    ("choice_raw", "choice_count", "kind", "flagged"),
    [
        (1, 3, "single", False),           # basic yes/no vote
        (2, 3, "single", False),           # single-choice vote
        (5, 3, "single", True),            # out of range
        (0, 3, "single", True),            # 1-based: zero is invalid
        ([2, 1, 3], 3, "ranked", False),   # ranked-choice ballot
        ([1, 1], 3, "ranked", True),       # duplicate ranks
        ([4], 3, "ranked", True),          # rank out of range
        ("[1,2]", 3, "ranked", False),     # JSON string input
        ("1", 3, "single", False),
        ({}, 3, "unsupported", True),      # object shape
        ([], 3, "unsupported", True),      # empty array
        ("", 3, "unsupported", True),
        (None, 0, "unsupported", True),
        (True, 3, "unsupported", True),    # bool is not an index
        ("not json", 3, "unsupported", True),
    ],
)
def test_classify_choice_python_helper_edge_cases(choice_raw, choice_count, kind, flagged):
    result = governance_explorer._classify_choice(choice_raw, choice_count)
    assert result["kind"] == kind
    assert result["flagged"] is flagged
    # Zero-quorum / pending scores never make a vote's SHAPE invalid — shape
    # classification depends only on the choice payload itself.
    if kind == "single":
        assert result["index"] >= 0
    if kind == "ranked":
        assert result["indexes"]


def _py_gip(text: str) -> int | None:
    """The Python-dialect evaluation of the canonical title identity, with
    the gip_number > 0 phantom guard the SQL side bakes into _gip_sql()."""
    match = re.compile(governance_explorer.GIP_PATTERN, re.IGNORECASE).match(text)
    if not match:
        return None
    n = int(match.group(1))
    return n if n > 0 else None


def test_gip_extraction_exact_patterns_only():
    for text, expected in GIP_TITLE_FIXTURES:
        assert _py_gip(text) == expected, text
    # The SQL side carries the canonical anchored literal (comment-stripped —
    # a prose mention must not satisfy this; see sql_code()).
    sql_literal = governance_explorer.GIP_PATTERN_SQL
    proposals = {s.key: s for s in governance_explorer._proposals_specs(
        governance_explorer._range_state("", ""),
        governance_explorer._default_filters(),
    )}["proposals"]
    assert sql_literal in sql_code(proposals.sql)
    # Link specs use exact GIP equality joins — never fuzzy text joins.
    links = {s.key: s for s in governance_explorer._entity_specs("proposal", PROPOSAL_ID)}
    link_sql = links["proposal_forum_links"].sql
    assert sql_literal in sql_code(link_sql)
    assert "positionCaseInsensitive" not in link_sql
    assert "LIKE" not in link_sql.upper()
    reverse = {s.key: s for s in governance_explorer._entity_specs("forum_topic", "12131")}
    reverse_sql = reverse["topic_proposal_links"].sql
    assert sql_literal in sql_code(reverse_sql)
    assert "positionCaseInsensitive" not in reverse_sql


def test_mention_pattern_diverges_only_in_graph_edges():
    """The unanchored mention pattern is a DELIBERATE divergence scoped to the
    citation graph's body scan — everywhere else the anchored canonical
    pattern is the only GIP extraction."""
    mention = governance_explorer.GIP_MENTION_PATTERN_SQL
    carriers = {s.key for s in _all_specs() if mention in sql_code(s.sql)}
    assert carriers == {"graph_edges"}
    edges = {s.key: s for s in _all_specs()}["graph_edges"]
    # The src (title) side of the same query uses the canonical pattern.
    assert governance_explorer.GIP_PATTERN_SQL in sql_code(edges.sql)


def test_concentration_tiers_are_the_canonical_10_20_50_everywhere():
    """Canonical space-level tiers (dbt api_governance_concentration_latest).
    delegation_concentration was the one outlier at 5/10/20 — WL-039 aligned
    it; this pins ALL concentration surfaces to the one scale."""
    tiers = "[toUInt32(10), toUInt32(20), toUInt32(50)]"
    specs = {s.key: s for s in _all_specs()}
    for key in ("delegation_concentration", "voter_concentration",
                "voter_power_concentration"):
        assert tiers in sql_code(specs[key].sql), key
    for spec in specs.values():
        assert "toUInt32(5)" not in sql_code(spec.sql), spec.key


_T_BALANCES = f"{governance_explorer.TREASURY_DB}.{governance_explorer.TREASURY_BALANCES_TABLE}"
_T_SERVED = f"{governance_explorer.TREASURY_DB}.{governance_explorer.TREASURY_SERVED_VIEW}"


def _treasury_read_problems(code: str) -> list[str]:
    """Problems with a treasury statement's read path; empty means compliant.

    Shared by the guard and its negative fixture, so the guard is proven to
    reject the pattern it exists to reject."""
    problems: list[str] = []
    job = governance_explorer.TREASURY_JOB
    if _T_BALANCES in code:
        if "argMax(b.balance_raw, b.insert_version)" not in code:
            problems.append("balances read without the argMax dedup")
        if "b.attempt_id)" not in code:
            problems.append("balances read not pinned to the served attempt")
        if f"b.job_name = '{job}'" not in code:
            problems.append("balances read without the job pin")
        if ("b.snapshot_date >= today() -" not in code
                and "b.snapshot_date IN (SELECT c_date FROM cand)" not in code):
            problems.append("balances read without a constant date bound")
        if _T_SERVED not in code:
            problems.append("balances read without served publications")
    if re.search(r"max\(snapshot_date\)\s+AS\s+(as_of|month_end)\b", code):
        problems.append("dates resolved from raw publications")
    if governance_explorer.TREASURY_CANONICAL_VIEW in code:
        problems.append("reads the canonical view (18s for full history)")
    return problems


def _every_treasury_spec() -> list[governance_explorer.QuerySpec]:
    specs = governance_explorer._treasury_specs(
        governance_explorer._range_state("", ""), governance_explorer._default_filters()
    )
    for kind, ident in (("treasury_token", f"1:{ASSET}"), ("treasury_wallet", f"100:{VOTER}")):
        specs += governance_explorer._entity_specs(kind, ident)
    return specs


def test_treasury_dates_resolve_from_served_publications_and_every_scan_is_pruned():
    """A raw census_publications row is not a served snapshot: 2026-07-28..08-15 on
    Ethereum were published under an interim config hash and served nothing, and
    resolving month-ends from raw publications emptied the July bucket. Every
    treasury read resolves dates from v_publications_current and reads
    token_balances for exactly the served attempts, bounded three ways."""
    specs = _every_treasury_spec()
    assert len(specs) >= 15
    reads = 0
    for spec in specs:
        code = sql_code(spec.sql)
        assert _treasury_read_problems(code) == [], spec.key
        reads += _T_BALANCES in code
    assert reads >= 11, "the served two-step read vanished from the treasury specs"

    # Negative fixture: the pre-2026-09 shape (raw as-of, no attempt pin) MUST fail.
    old_shape = (
        "WITH asof AS (SELECT chain_id, max(snapshot_date) AS as_of "
        "FROM rpc_state_indexer.census_publications GROUP BY chain_id)\n"
        "SELECT sum(b.balance_raw) FROM rpc_state_indexer.token_balances AS b "
        "WHERE b.snapshot_date IN (SELECT as_of FROM asof)"
    )
    assert len(_treasury_read_problems(old_shape)) >= 4


def test_re_delegations_uses_the_canonical_repointed_definition():
    """re_delegations must be dbt's 'repointed': sets whose row_number over
    ALL events (sets and clears) is > 1 — not sets beyond the first set,
    which undercounts delegators whose first event was a clear."""
    summary = {s.key: s for s in _all_specs()}["delegation_summary"]
    code = sql_code(summary.sql)
    assert "row_number() OVER (PARTITION BY chain_id, delegator" in code
    assert "rn > 1" in code
    assert "uniqExactIf((chain_id, delegator)" not in code


DISCUSSION_FIXTURES = [
    ("https://forum.gnosis.io/t/gip-149-fund-thing/12131", 12131),
    ("https://forum.gnosis.io/t/gip-149-fund-thing/12131/5", 12131),
    ("https://example.com/t/something/555", None),
    ("", None),
]


def test_discussion_topic_id_extraction():
    pattern = re.compile(r"forum\.gnosis\.io/t/[^/]+/([0-9]+)")
    for url, expected in DISCUSSION_FIXTURES:
        match = pattern.search(url)
        got = int(match.group(1)) if match else None
        assert got == expected, url
    detail = {s.key: s for s in governance_explorer._entity_specs("proposal", PROPOSAL_ID)}
    sql = detail["proposal_detail"].sql
    assert r"forum\\.gnosis\\.io/t/[^/]+/([0-9]+)" in sql
    assert "toUInt32OrNull" in sql  # NULL-safe while discussion is empty
    assert "AS discussion_topic_id" in sql


def test_link_specs_rank_discussion_over_gip():
    links = {s.key: s for s in governance_explorer._entity_specs("proposal", PROPOSAL_ID)}
    sql = links["proposal_forum_links"].sql
    assert "'discussion' AS link_source" in sql
    assert "'gip'" in sql
    # A topic linked both ways appears once, as 'discussion': the GIP arm
    # excludes the discussion topic id.
    assert "NOT IN (SELECT discussion_topic_id" in sql
    # 'discussion' < 'gip' — ORDER BY link_source ranks the primary tier first.
    assert "ORDER BY link_source" in sql
    # Pre-reingest (empty discussion column) the extraction is NULL-safe and
    # the GIP tier alone remains active — no code change needed.
    assert "toUInt32OrNull" in sql
    reverse = {s.key: s for s in governance_explorer._entity_specs("forum_topic", "12131")}
    reverse_sql = reverse["topic_proposal_links"].sql
    assert "'discussion' AS link_source" in reverse_sql
    assert "ORDER BY link_source" in reverse_sql
    assert "!= {topic_id:UInt32}" in reverse_sql


def test_gip_links_return_all_candidates_and_flag_missing():
    for kind, identifier, key in (
        ("proposal", PROPOSAL_ID, "proposal_forum_links"),
        ("forum_topic", "12131", "topic_proposal_links"),
    ):
        spec = {s.key: s for s in governance_explorer._entity_specs(kind, identifier)}[key]
        # ALL candidates: the GIP relation is not 1:1 — no LIMIT anywhere.
        assert "LIMIT" not in spec.sql.upper(), key
        empty = CachedDataset(
            columns=[], column_types=[], rows=[],
            stats=DatasetStats(row_count=0, rows_returned=0, mode="exact_capped"),
            sql=spec.sql, database="governance_db",
        )
        coverage, codes = governance_explorer._coverage_from_dataset(
            empty, spec, governance_explorer._range_state("", "")
        )
        assert "no_data" in codes
        assert coverage["source_label"]


def test_freshness_two_clocks_and_source_stale_over_24h():
    stale_at = datetime.now(timezone.utc) - timedelta(days=3)
    fresh_at = datetime.now(timezone.utc) - timedelta(hours=2)
    dataset = CachedDataset(
        columns=["source", "latest_ingested_at", "latest_activity_at"],
        column_types=["str", "datetime", "datetime"],
        rows=[["snapshot", stale_at, stale_at], ["forum", fresh_at, fresh_at]],
        stats=DatasetStats(row_count=2, rows_returned=2, mode="exact_capped"),
        sql="--", database="governance_db",
    )
    freshness, warnings = governance_explorer._freshness_state(
        {"source_freshness": dataset}
    )
    assert freshness["snapshot"]["stale"] is True
    assert freshness["forum"]["stale"] is False
    # Two independent clocks per source.
    assert freshness["snapshot"]["latest_ingested_at"]
    assert freshness["snapshot"]["latest_activity_at"]
    assert warnings == ["source_stale"]
    # Stale flag propagates into section-load warnings.
    server, _ = _server(FreshCH())
    opened = _tool(server, "open_governance")()
    applied = _tool(server, "load_governance_section")(
        view_id=opened.structuredContent["view_id"], request_id=1,
        section="overview",
    ).structuredContent
    assert applied["view_state"]["freshness"]["snapshot"]["stale"] is True
    assert applied["view_state"]["freshness"]["forum"]["stale"] is False
    assert "source_stale" in applied["view_state"]["warnings"]


def test_fingerprint_short_circuit_serves_retained_freshness():
    """The zero-query guarantee is absolute: a tab return serves the RETAINED
    freshness state without touching ClickHouse (pinned frozen behavior)."""
    server, ch = _server()
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    applied = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=1, section="overview"
    ).structuredContent
    assert applied["view_state"]["freshness"]["snapshot"]["latest_ingested_at"]
    call_count = len(ch.calls)
    restored = _tool(server, "load_governance_section")(
        view_id=view_id, request_id=2, section="overview"
    ).structuredContent
    assert len(ch.calls) == call_count
    assert restored["view_state"]["freshness"] == applied["view_state"]["freshness"]
    assert "source_freshness" in restored["datasets"]


def test_every_spec_carries_source_provenance_label():
    for spec in _all_specs():
        assert spec.source in governance_explorer.SOURCE_LABELS, spec.key
        dataset = CachedDataset(
            columns=["id"], column_types=["int"], rows=[[1]],
            stats=DatasetStats(row_count=1, rows_returned=1, mode="exact_capped"),
            sql=spec.sql, database="governance_db",
        )
        coverage, _ = governance_explorer._coverage_from_dataset(
            dataset, spec, governance_explorer._range_state("", "")
        )
        assert coverage["source_kind"] == spec.source
        assert coverage["source_label"] == governance_explorer.SOURCE_LABELS[spec.source]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "entity_type", "identifier"),
    [
        (PROPOSAL_ID, "proposal", PROPOSAL_ID),
        (VOTER, "voter", VOTER),
        ("GIP-151", "proposal", PROPOSAL_ID),
        ("gip 151", "proposal", PROPOSAL_ID),
        ("12131", "forum_topic", "12131"),
        ("Treasury", "proposal", PROPOSAL_ID),
    ],
)
def test_search_classifier_formats(query, entity_type, identifier):
    candidates = governance_explorer._search_candidates(SearchCH(), query)
    assert candidates
    assert candidates[0]["entity_type"] == entity_type
    assert candidates[0]["identifier"] == identifier
    for candidate in candidates:
        assert set(candidate) == {
            "entity_type", "identifier", "label", "role", "evidence_count"
        }


def test_search_oversized_gip_number_skips_gip_arm():
    # "GIP-99999999999" parses as a GIP query but overflows Int32 — the GIP
    # arm must be skipped (no {gip:Int32} bind), falling through to the text
    # arm, which still returns candidates without raising.
    ch = SearchCH()
    candidates = governance_explorer._search_candidates(ch, "GIP-99999999999")
    assert candidates
    assert all("'gip_proposal'" not in sql for (sql, *_rest) in ch.calls)
    assert any("'proposal_title'" in sql for (sql, *_rest) in ch.calls)
    for (_sql, _db, _rows, params, _budget) in ch.calls:
        for value in (params or {}).values():
            if isinstance(value, int):
                assert value <= 0x7FFFFFFF


def test_search_ranking_exact_prefix_before_text_and_cap_20():
    class RankCH(StubCH):
        def run_query(self, sql, database="dbt", requested_max_rows=100, audience="tool", fetch_mode="auto", parameters=None, query_budget=None):
            self.calls.append((sql, database, requested_max_rows, parameters, query_budget))
            rows = []
            for index in range(25):
                rank = index % 3  # exact / prefix / substring mixed
                rows.append(["proposal", f"0x{index:064x}", f"title {index}",
                             "proposal_title", 1000 - index, rank])
            return self._result(sql, database, SEARCH_COLUMNS, rows)

    candidates = governance_explorer._search_candidates(RankCH(), "governance dao")
    assert len(candidates) == governance_explorer.SEARCH_CANDIDATE_CAP == 20
    # Rank-major merge: exact (0) before prefix (1) before substring (2)...
    ranks = []
    for candidate in candidates:
        index = int(candidate["identifier"], 16)
        ranks.append(index % 3)
    assert ranks == sorted(ranks)
    # ...and evidence-descending within a rank tier.
    for tier in (0, 1, 2):
        tier_evidence = [c["evidence_count"] for c, r in zip(candidates, ranks) if r == tier]
        assert tier_evidence == sorted(tier_evidence, reverse=True)
    with pytest.raises(ValueError):
        governance_explorer._search_candidates(StubCH(), "x" * 201)


def test_stale_search_ignored_without_sql():
    server, ch = _server(SearchCH())
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    _tool(server, "load_governance_section")(
        view_id=view_id, request_id=2, section="overview"
    )
    call_count = len(ch.calls)
    stale = _tool(server, "search_governance")(
        view_id=view_id, request_id=1, query="Treasury"
    )
    assert stale.structuredContent["view_state"]["applied_request_id"] == 2
    assert len(ch.calls) == call_count


def test_search_single_candidate_autoloads_entity():
    server, _ = _server(SingleHitSearchCH())
    opened = _tool(server, "open_governance")()
    view_id = opened.structuredContent["view_id"]
    result = _tool(server, "search_governance")(
        view_id=view_id, request_id=1, query=PROPOSAL_ID
    ).structuredContent
    assert result["view_state"]["section"] == "entity"
    assert result["view_state"]["selected_entity"]["identifier"] == PROPOSAL_ID
    assert set(result["datasets"]) >= set(governance_explorer.ENTITY_BUNDLES["proposal"])
    # Multi-candidate searches patch the candidate strip instead.
    server2, _ = _server(SearchCH())
    opened2 = _tool(server2, "open_governance")()
    view_id2 = opened2.structuredContent["view_id"]
    multi = _tool(server2, "search_governance")(
        view_id=view_id2, request_id=1, query="GIP-151"
    ).structuredContent
    assert multi["type"] == "PATCH_VIEW_STATE"
    assert len(multi["patch"]["search"]["candidates"]) == 2


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_visibility_web_registry_and_security_metadata():
    server, _ = _server()
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "open_governance" in names
    assert APP_ONLY_TOOLS.isdisjoint(names)
    assert APP_ONLY_TOOLS <= mini_apps.get_app_only_tool_names()
    config = web_apps.WEB_APP_CONFIGS["governance"]
    assert config.open_tool == "open_governance"
    assert config.diagnostics_loader is not None
    assert set(GOV_TOOLS) <= config.allowed_tools
    assert TOOL_RISK_REGISTRY["open_governance"] == frozenset({RiskClass.READ_ONLY})
    for name in APP_ONLY_TOOLS:
        assert TOOL_RISK_REGISTRY[name] == frozenset({RiskClass.APP_ONLY})
    meta = TOOL_META["open_governance"]
    assert meta["domain"] == "visualization"
    assert "governance" in meta["tags"]
    resource_uris = {
        str(template.uri_template) if hasattr(template, "uri_template") else str(template)
        for template in server._resource_manager._resources
    }
    assert governance_explorer.GOV_URI in resource_uris


def test_section_groups_cover_every_dataset_key_exactly_once():
    seen_global: dict[str, str] = {}
    for section, groups in governance_explorer.SECTION_GROUPS.items():
        assert "core" in groups, f"{section} must define a core group"
        for group, keys in groups.items():
            assert keys, f"{section}.{group} must not be empty"
            for key in keys:
                owner = f"{section}.{group}"
                assert key not in seen_global, (
                    f"dataset {key} appears in {seen_global[key]} and {owner}"
                )
                seen_global[key] = owner
    # Entity bundle keys are globally unique and never appear in groups.
    entity_seen: set[str] = set()
    for kind, keys in governance_explorer.ENTITY_BUNDLES.items():
        for key in keys:
            assert key not in seen_global, f"entity key {key} is in SECTION_GROUPS"
            assert key not in entity_seen, f"entity key {key} duplicated"
            entity_seen.add(key)
    # Spec builders produce exactly the frozen keys.
    range_state = governance_explorer._range_state("", "")
    defaults = governance_explorer._default_filters()
    for section, groups in governance_explorer.SECTION_GROUPS.items():
        expected = {key for keys in groups.values() for key in keys}
        produced = {
            spec.key
            for spec in governance_explorer._section_specs(section, range_state, defaults)
        }
        if section != "overview":
            expected.discard("source_freshness")
        assert produced == expected, section
    for kind, keys in governance_explorer.ENTITY_BUNDLES.items():
        identifier = {
            "proposal": PROPOSAL_ID, "voter": VOTER,
            "forum_topic": "12131", "forum_user": "42",
            "treasury_token": f"1:{ASSET}", "treasury_wallet": f"100:{VOTER}",
        }[kind]
        produced = {spec.key for spec in governance_explorer._entity_specs(kind, identifier)}
        assert produced == set(keys), kind


class FakeRequest:
    """Minimal Starlette-Request stand-in for the web-app route handlers."""

    def __init__(self, *, path_params=None, query=None, headers=None, body=None):
        self.path_params = path_params or {}
        self.query_params = query or {}
        self.headers = headers or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def test_governance_web_routes_health_asset_and_dispatch():
    _server()

    # GET /app/governance — serves the shell with the payload injected.
    response = asyncio.run(web_apps.serve_app(
        FakeRequest(path_params={"app_id": "governance"})
    ))
    assert response.status_code == 200
    html = response.body.decode()
    assert 'id="mini-app-data"' in html
    assert "/app/governance/api/tool" in html

    # GET /app/governance/health — bundle identity for deploy verification.
    health = asyncio.run(web_apps.serve_app_health(
        FakeRequest(path_params={"app_id": "governance"})
    ))
    assert health.status_code == 200
    data = json.loads(health.body.decode())
    assert data["status"] == "ok"
    assert data["bundle_sha256"]

    # Asset namespace is scoped: traversal rejected, unknown asset 404s.
    traversal = asyncio.run(web_apps.serve_app_asset(
        FakeRequest(path_params={"app_id": "governance", "path": "../secret"})
    ))
    assert traversal.status_code == 400
    missing = asyncio.run(web_apps.serve_app_asset(
        FakeRequest(path_params={"app_id": "governance", "path": "nope.js"})
    ))
    assert missing.status_code == 404

    # POSITIVE dispatch: open + section load through the HTTP tool route.
    opened = asyncio.run(web_apps.dispatch_app_tool(FakeRequest(
        path_params={"app_id": "governance", "tool_name": "open_governance"},
        body={"arguments": {}},
    )))
    assert opened.status_code == 200
    opened_data = json.loads(opened.body.decode())
    assert opened_data["isError"] is False
    view_id = opened_data["structuredContent"]["view_id"]
    loaded = asyncio.run(web_apps.dispatch_app_tool(FakeRequest(
        path_params={"app_id": "governance", "tool_name": "load_governance_section"},
        body={"arguments": {"view_id": view_id, "request_id": 1, "section": "overview"}},
    )))
    assert loaded.status_code == 200
    loaded_data = json.loads(loaded.body.decode())
    assert loaded_data["isError"] is False
    assert loaded_data["structuredContent"]["view_state"]["loaded_groups"]["overview.core"] is True

    # NEGATIVE dispatch: a non-allowlisted tool name is rejected for this app.
    denied = asyncio.run(web_apps.dispatch_app_tool(FakeRequest(
        path_params={"app_id": "governance", "tool_name": "execute_query"},
        body={"arguments": {"sql": "SELECT 1"}},
    )))
    assert denied.status_code == 404
    assert "not available" in json.loads(denied.body.decode())["error"]


# ---------------------------------------------------------------------------
# Treasury plane
# ---------------------------------------------------------------------------


def _treasury_section(filters: dict | None = None) -> dict[str, governance_explorer.QuerySpec]:
    return {
        spec.key: spec
        for spec in governance_explorer._treasury_specs(
            governance_explorer._range_state("", ""),
            filters or governance_explorer._default_filters(),
        )
    }


def test_treasury_specs_pin_the_job_never_use_final_and_keep_chains_apart():
    """token_balances spans every census job (the full_holders jobs included), so
    the job pin is load-bearing; FINAL is forbidden on a billions-row raw table
    (argMax replaces it); and chains publish independently, so every window and
    served lookup partitions on the chain."""
    job_pin = f"job_name = '{governance_explorer.TREASURY_JOB}'"
    for spec in _every_treasury_spec():
        code = sql_code(spec.sql)
        assert job_pin in code, spec.key
        assert not re.search(r"\bFINAL\b", code), spec.key
    code = sql_code(_treasury_section()["treasury_summary"].sql)
    assert "OVER (PARTITION BY chain_id, snapshot_date)" in code
    assert "OVER (PARTITION BY w_chain)" in code
    history = sql_code(_treasury_section()["treasury_history"].sql)
    assert "(chain_id, snapshot_date) IN (SELECT c_chain, c_date FROM cand)" in history


def test_treasury_usd_comes_from_the_hub_through_the_registry_symbol():
    """The on-chain symbol is attacker-authored (18 contracts here claim USDC), so
    the price join keys on the REVIEWED registry symbol only, and only the
    priced class carries a value — never a fabricated 0."""
    for spec in _every_treasury_spec():
        code = sql_code(spec.sql)
        if "classified AS (" not in code:
            continue
        assert "ASOF LEFT JOIN hubp AS h ON h.h_sym = q.x_psym" in code, spec.key
        assert "if(e.x_date < r.reg_to, r.reg_psym, '') AS x_psym" in code, spec.key
        assert "if(x_class = 'priced', x_units * x_price, NULL) AS x_value" in code, spec.key
        assert not re.search(r"h_sym\s*=\s*[\w.]*x_symbol", code), spec.key
        assert "WHERE upper(symbol) IN {hub_syms:Array(String)}" in code, spec.key
        assert spec.parameters["hub_syms"], spec.key


def test_treasury_filters_are_client_side_view_hints():
    """chain_id / exclude_ltd stay validated and section-scoped, but they never
    change the SQL: every dataset carries both chains and all wallets, so one
    cached load serves every toggle. The dead `asset` filter is gone."""
    for kwargs in ({"chain_id": 1}, {"exclude_ltd": True}):
        with pytest.raises(ValueError, match="only to the treasury section"):
            governance_explorer._validate_filters(
                "proposals", "", "", "", "", 0, "", "", **kwargs
            )
    with pytest.raises(ValueError, match="chain_id must be one of"):
        governance_explorer._validate_filters("treasury", "", "", "", "", 0, "", "", 42)
    with pytest.raises(TypeError):
        governance_explorer._validate_filters(
            "treasury", "", "", "", "", 0, "", "", 0, False, ASSET
        )
    base = {key: (spec.sql, spec.parameters) for key, spec in _treasury_section().items()}
    for hint in ({"chain_id": 1}, {"chain_id": 100}, {"exclude_ltd": True}):
        variant = _treasury_section({**governance_explorer._default_filters(), **hint})
        assert {key: (spec.sql, spec.parameters) for key, spec in variant.items()} == base, hint


def test_treasury_ltd_is_companion_columns_never_a_filter():
    """The Ltd wallet holds ~46% of Ethereum GNO — the toggle roughly halves the
    headline — so it is exposed as explicit *_ex_ltd columns the UI switches
    between, never as a hidden server-side WHERE."""
    specs = _treasury_section()
    for key in ("treasury_summary", "treasury_holdings", "treasury_history"):
        code = sql_code(specs[key].sql)
        assert "_ex_ltd" in code, key
        assert "NOT c.x_is_ltd" in code, key
        assert specs[key].parameters["ltd"] == list(governance_explorer.LTD_WALLETS), key
    assert "has({ltd:Array(String)}, wallet_address) AS is_ltd" in sql_code(
        specs["treasury_by_wallet"].sql
    )


def test_treasury_history_is_full_one_scan_and_never_blends_chains():
    """History is FULL (no month-count bound) and ONE fan-out scan, so every chart
    reads the same snapshot and a stacked total equals the NAV line."""
    specs = _treasury_section()
    assert {key for key in specs if "history" in key} == {
        "treasury_history", "treasury_history_coverage",
    }
    history = specs["treasury_history"]
    code = sql_code(history.sql)
    assert "GROUP BY grain, chain_id, bucket, wallet_address, token_address" in code
    assert "ARRAY JOIN [('chain', '', ''), ('wallet', c.x_wallet, ''), ('token', '', c.x_token)]" in code
    # The only LIMIT is the per-month candidate-day window, never a month count.
    assert re.findall(r"LIMIT\s+\d+\s+BY\s+([^\n]+)", code) == [
        "c_chain, toStartOfMonth(c_date)"
    ]
    assert "FULL history" in history.basis and "24" not in history.basis
    assert history.cache_ttl_seconds == governance_explorer.TREASURY_HISTORY_TTL
    assert history.exact_count is False
    coverage = sql_code(specs["treasury_history_coverage"].sql)
    for status in ("'complete'", "'partial'", "'gap'", "'unpublished'"):
        assert status in coverage


def test_treasury_warning_scan_discloses_partial_snapshots_and_gaps():
    def dataset(columns, rows):
        return CachedDataset(
            columns=columns, column_types=["str"] * len(columns), rows=rows,
            stats=DatasetStats(row_count=len(rows), rows_returned=len(rows), mode="exact_capped"),
            sql="--", database="governance_db", parameters={},
        )

    summary = dataset(
        ["chain_id", "as_of", "as_of_status", "carried_tokens", "hub_latest_date"],
        [[1, "2026-09-24", "partial", 3, "2026-09-20"], [100, None, "no_served_snapshot", 0, None]],
    )
    assert governance_explorer._treasury_warning_scan(summary, "treasury_summary") == [
        "treasury_asof_partial", "treasury_chain_unserved", "treasury_price_hub_stale",
        "treasury_tokens_carried",
    ]
    months = dataset(["chain_id", "status"], [[1, "complete"], [1, "gap"], [100, "partial"]])
    for key in ("treasury_history_coverage", "treasury_wallet_months", "treasury_token_months"):
        assert governance_explorer._treasury_warning_scan(months, key) == [
            "treasury_history_gap", "treasury_history_partial",
        ]


def test_every_treasury_warning_code_has_frontend_copy():
    """The warning strip passes an unknown string through unchanged, so a code
    the server emits without frontend copy reaches the page RAW —
    "treasury_history_partial" sat in a yellow chip above the treasury. Every code
    _treasury_warning_scan can produce must be a key of the frontend's map."""
    import inspect
    from pathlib import Path

    source = inspect.getsource(governance_explorer._treasury_warning_scan)
    codes = set(re.findall(r'codes\.add\("(treasury_[a-z_]+)"\)', source))
    statuses = re.search(r"for status in \(([^)]*)\)", source)
    assert statuses and 'f"treasury_history_{status}"' in source
    codes |= {f"treasury_history_{s}" for s in re.findall(r'"(\w+)"', statuses.group(1))}
    assert len(codes) == 7, codes  # the sweep found the vocabulary, not nothing

    ui = (Path(__file__).resolve().parents[1]
          / "ui/src/mini-apps/governance/state/warnings.ts").read_text(encoding="utf-8")
    copy = ui[ui.index("export const WARNING_COPY"):ui.index("export const QUIET_WARNINGS")]
    assert sorted(c for c in codes if f"\n  {c}:" not in copy) == []


def test_governance_resource_declares_the_coingecko_image_hosts():
    """An MCP-UI host blocks every remote image unless the resource names its
    hosts, so without this meta the treasury table silently renders monograms
    for everything. api.coingecko.com must NOT be listed: the browser never
    calls it — the server does, over the MCP tool channel."""
    server, _ = _server()
    resources = server._resource_manager._resources
    entry = next(
        resource for uri, resource in resources.items()
        if str(uri) == governance_explorer.GOV_URI
    )
    domains = entry.meta["ui"]["csp"]["resourceDomains"]
    assert set(domains) == {
        "https://assets.coingecko.com", "https://coin-images.coingecko.com",
    }
    assert not any("api.coingecko.com" in d for d in domains)


def test_overlay_prices_only_spot_eligible_rows_and_never_fabricates(monkeypatch):
    """The overlay is the SPOT FALLBACK only: a reviewed token the hub cannot price
    (spot_eligible) may get today's CoinGecko quote; hub-priced tokens and spam
    never reach the price endpoint, spam gets no icon, an unlisted token is absent
    (never 0), and a quote that would dominate its chain's hub total is dropped."""
    from cerebro_mcp.tools.visualization import coingecko

    coingecko.reset_caches_for_tests()
    monkeypatch.setattr(coingecko, "_EXECUTOR", type("E", (), {
        "submit": staticmethod(lambda fn, *a: fn(*a)),
    })())
    listed, spam, hub, unlisted, whale = ("0x" + c * 20 for c in ("11", "22", "33", "44", "55"))
    requested: list[set[str]] = []
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {"ethereum": {
        listed: "listed-coin", spam: "spam-coin", hub: "hub-coin", whale: "whale-coin",
    }})

    def fake_prices(ids):
        requested.append(set(ids))
        return {"listed-coin": 4.0, "spam-coin": 9.0, "hub-coin": 7.0, "whale-coin": 5.0}

    monkeypatch.setattr(coingecko, "fetch_prices", fake_prices)
    monkeypatch.setattr(coingecko, "fetch_icon_map", lambda chain: {
        listed: "https://assets.coingecko.com/l.png", spam: "https://assets.coingecko.com/s.png",
    })

    server, _ = _server()
    view_id = _tool(server, "open_governance")().structuredContent["view_id"]
    _tool(server, "load_governance_section")(view_id=view_id, request_id=1, section="treasury")

    def dataset(columns, rows):
        return CachedDataset(
            columns=columns, column_types=["str"] * len(columns), rows=rows,
            stats=DatasetStats(row_count=len(rows), rows_returned=len(rows), mode="exact_capped"),
            sql="--", database="governance_db", parameters={},
        )

    mini_apps.attach_dataset(view_id, "treasury_holdings", dataset(
        ["chain_id", "token_address", "token_class", "balance_units", "spot_eligible"],
        [[1, listed, "listed", 10.0, 1], [1, spam, "spam", 1e12, 0], [1, hub, "priced", 5.0, 0],
         [1, unlisted, "listed", 3.0, 1], [1, whale, "listed", 1e9, 1]],
    ))
    mini_apps.attach_dataset(view_id, "treasury_summary", dataset(
        ["chain_id", "nav_usd"], [[1, 1_000_000.0]],
    ))
    for _ in range(2):  # first call warms the background pass
        result = _tool(server, "load_governance_overlays")(view_id=view_id)
    patch = result.structuredContent["patch"]
    overlay = patch["price_overlay"]
    assert overlay["kind"] == "spot" and overlay["role"] == "spot_fallback"
    assert overlay["by_chain"]["1"] == {listed: 4.0}
    assert overlay["excluded_implausible"] == {"1": [whale]}
    assert all(spam not in ids and "hub-coin" not in ids for ids in requested)
    assert spam not in patch["icon_overlay"].get("1", {})
    assert listed in patch["icon_overlay"]["1"]
    assert patch["price_overlay_at"].endswith("Z")
    coingecko.reset_caches_for_tests()


#: Declared multi-reference allowances. ClickHouse inlines a CTE per reference, so
#: every extra reference re-runs its source; these two read small sources and each
#: extra reference is an IN-prune or a per-chain roll-up of that small result:
#:   picked — the served as-of window (v_publications_current, ~0.3s): the
#:            positions IN-prune, the attribute join, and a per-chain as-of roll-up;
#:   cand   — candidate days from raw census_publications (~0.3s): the served
#:            lookup's two IN-prunes, the balance read's date prune, and (coverage
#:            only) the raw lookup's two prunes and the calendar spine.
_TREASURY_CTE_ALLOWANCE = {"picked": 3, "cand": 6}


def test_treasury_specs_reference_each_cte_once():
    """Every treasury CTE is referenced once, except the declared cheap sources.

    Measured history of this trap: a four-arm UNION ALL over `held` blew the 2 GiB
    cap at ~390 tokens; `per_bucket` referenced twice timed out at 6 scans. The
    served two-step read keeps every fat source (balances, eligibility views)
    single-reference; only the small date-resolution CTEs are shared.
    """
    seen = 0
    for spec in _every_treasury_spec():
        code = sql_code(spec.sql)
        names = re.findall(r"(?:^|\bWITH\s+)(\w+) AS \(", code, re.MULTILINE)
        for name in names:
            uses = len(re.findall(rf"\b{name}\b", code)) - 1
            limit = _TREASURY_CTE_ALLOWANCE.get(name, 1)
            assert uses <= limit, (
                f"{spec.key}: CTE `{name}` is referenced {uses}x (allowed {limit}) — "
                "ClickHouse inlines it per reference"
            )
            seen += 1
    assert seen >= 150, f"CTE guard matched only {seen} CTEs — regex has drifted"


# ---------------------------------------------------------------------------
# Treasury entity drill-downs
# ---------------------------------------------------------------------------


def _treasury_entity_specs():
    """Every treasury entity spec, both kinds and both chains."""
    out = []
    for kind, ident in (
        ("treasury_token", f"1:{ASSET}"),
        ("treasury_token", f"100:{ASSET}"),
        ("treasury_wallet", f"1:{VOTER}"),
        ("treasury_wallet", f"100:{VOTER}"),
    ):
        normalized = governance_explorer._validate_entity_identifier(kind, ident)
        out.extend(governance_explorer._entity_specs(kind, normalized))
    return out


def test_treasury_entity_identifier_carries_the_chain():
    """A bare address is not an identity here: every census wallet exists verbatim
    on BOTH chains, so an address alone is always ambiguous."""

    for kind in ("treasury_token", "treasury_wallet"):
        assert governance_explorer._validate_entity_identifier(
            kind, f" 1:{ASSET.upper()} "
        ) == f"1:{ASSET}"
        for bad in (ASSET, f"5:{ASSET}", "1:0xnothex", "1:", ":" + ASSET, f"1:{ASSET}extra"):
            with pytest.raises(ValueError):
                governance_explorer._validate_entity_identifier(kind, bad)


#: Entity specs that deliberately read BOTH chains or no chain-scoped source first.
_CHAIN_PIN_EXEMPT = {
    "treasury_wallet_chains",        # the same address on every chain (switcher)
    "treasury_token_price_history",  # registry + hub; the chain pin is in the WHERE
}


def test_treasury_entity_specs_pin_the_job_the_chain_and_order_rows():
    specs = _treasury_entity_specs()
    assert len(specs) == 20
    job_pin = f"job_name = '{governance_explorer.TREASURY_JOB}'"
    for spec in specs:
        code = sql_code(spec.sql)
        assert job_pin in code, spec.key
        assert not re.search(r"\bFINAL\b", code), spec.key
        assert "ORDER BY" in code, spec.key
        if spec.key in _CHAIN_PIN_EXEMPT:
            continue
        # The leading CTE must be chain-pinned, or the entity resolves its dates
        # against the OTHER chain.
        head = code.split("\n),", 1)[0]
        assert re.search(r"chain_id = (1|100)\b", head), f"{spec.key}: leading CTE not chain-pinned"


def test_treasury_entity_addresses_are_bound_parameters_never_interpolated():
    """The chain is an int already checked against TREASURY_CHAINS, so it is
    interpolated. The address never is — it reaches SQL only as {addr:String}."""

    for kind, ident in (("treasury_token", f"1:{ASSET}"), ("treasury_wallet", f"1:{VOTER}")):
        normalized = governance_explorer._validate_entity_identifier(kind, ident)
        address = normalized.split(":", 1)[1]
        for spec in governance_explorer._entity_specs(kind, normalized):
            assert address not in spec.sql, spec.key
            if spec.key.endswith("_months"):
                # Month completeness is chain-level: it names no address at all.
                assert "addr" not in spec.parameters, spec.key
                continue
            assert spec.parameters["addr"] == address, spec.key
            assert "{addr:String}" in spec.sql, spec.key
            # Every bind the SQL names is present, and nothing else is passed.
            named = set(re.findall(r"\{([a-z_][a-z0-9_]*):", spec.sql))
            assert named == set(spec.parameters), spec.key


def test_treasury_entity_label_comes_from_trusted_sources_only():
    """Breadcrumbs render their label raw and a token symbol is attacker-authored:
    labels come from the reviewed registry or the attributed wallet list, else the
    chain name and a short address — never from a dataset row."""
    assert "treasury_token" not in governance_explorer._ENTITY_LABEL_COLUMN
    label = governance_explorer._treasury_entity_label
    assert label("treasury_wallet", "1:0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f") == (
        "GNO Main Treasury \u00b7 Ethereum 0x458c\u20265e6f"
    )
    spoof = "1:0x357eb8dc76920a7a00d8e3059cdb0249aceb2df7"  # on-chain symbol "USDC"
    assert label("treasury_token", spoof) == "Ethereum 0x357e\u20262df7"
    assert label("treasury_token", "1:0x6810e776880c02933d47db1b9fc05908e5386b96").startswith("GNO ")


# ---------------------------------------------------------------------------
# Delegated voting power — strategy-era resolution
# ---------------------------------------------------------------------------


def _delegation_power_sql() -> str:
    specs = {
        spec.key: spec
        for spec in governance_explorer._delegations_specs(
            governance_explorer._range_state("", ""),
            governance_explorer._default_filters(),
        )
    }
    return specs["delegation_power"].sql


def test_delegation_power_never_indexes_vp_by_strategy_by_fixed_position():
    """The bug this replaces: `if(length(lv.vps) = 5, lv.vps[4], 0)`.

    `vp_by_strategy` is positional against the proposal's OWN strategy list, and
    gnosis.eth has rewritten that list three times (lengths 2, 4, 5). The fixed
    index read 0 for every delegate whose latest final vote predated 2025-11-16
    — 26.4% of all delegated voting power. A length guard is not a schema check.
    """
    sql = _delegation_power_sql()
    # No literal subscript on the vp array, and no length guard on it.
    assert not re.search(r"\bvps\s*\[\s*\d+\s*\]", sql)
    assert not re.search(r"length\s*\(\s*[\w.]*vps\s*\)\s*=\s*\d+", sql)
    # Slots come from the proposal's own strategy list instead.
    assert "snapshot_proposals FINAL" in sql
    assert "'strategies'" in sql
    assert "arrayEnumerate" in sql


def test_delegation_power_resolves_the_chain_from_network_not_position():
    """THE TRAP. The delegation strategies appear in OPPOSITE chain order in the
    two most recent layouts:

        len 4  gno(1), delegation(1), gno(100), delegation(100)   -> [2]=eth, [4]=gno-chain
        len 5  cc(100), beacon(100), cc(1), delegation(100), delegation(1)
                                                                  -> [4]=gno-chain, [5]=eth

    So "take the delegation entries in order" swaps mainnet and Gnosis Chain for
    44,635 votes (7.99M VP) without changing a single total — the failure would
    be invisible in every aggregate. Only the strategy's own `network` is stable.
    """
    sql = _delegation_power_sql()
    assert "'network'" in sql
    # Each chain's slots are selected by network literal, not by offset.
    assert re.search(r"networks\[i\]\s*=\s*'1'", sql)
    assert re.search(r"networks\[i\]\s*=\s*'100'", sql)


def test_delegation_power_matches_the_strategy_name_as_a_substring():
    """The 2020-12 layout names it `erc20-balance-of-delegation`, carrying
    199,139 VP. An exact `name = 'delegation'` filter drops it silently."""
    sql = _delegation_power_sql()
    assert governance_explorer.DELEGATION_STRATEGY_MATCH == "delegation"
    assert re.search(
        rf"position\(names\[i\], '{governance_explorer.DELEGATION_STRATEGY_MATCH}'\) > 0",
        sql,
    )
    assert "names[i] = 'delegation'" not in sql


def test_delegation_power_emits_null_not_zero_where_nothing_was_measured():
    """29 of 80 delegates have never voted. Zero is a measurement; absence is
    not. The epoch guard matters for the same reason — a LEFT JOIN miss on a
    DateTime defaults to 1970-01-01, which reads as a real vote date."""
    sql = _delegation_power_sql()
    assert "nullIf(lv.last_vote_at, toDateTime(0))" in sql
    # Every VP column returns NULL when its slot set is empty.
    assert sql.count("NULL,") >= 3
    assert "NULLS LAST" in sql


def test_delegation_power_pins_the_snapshot_space():
    """Both new CTEs reduce ACROSS proposals rather than filtering to one, so an
    unpinned argMax would follow a voter into a second space the day one lands."""
    sql = _delegation_power_sql()
    assert sql.count(f"space_id = '{governance_explorer.SNAPSHOT_SPACE}'") == 2


def test_delegation_power_cap_exceeds_the_delegate_universe():
    """Unmeasurable delegates sort last under NULLS LAST, so a tight cap would
    truncate exactly the rows the UI counts to say what it could not measure."""
    assert governance_explorer.DELEGATE_POWER_CAP >= 200
    assert f"LIMIT {governance_explorer.DELEGATE_POWER_CAP}" in _delegation_power_sql()


def _gip_pipeline_sql() -> str:
    specs = {
        spec.key: spec
        for spec in governance_explorer._overview_specs(
            governance_explorer._range_state("", "")
        )
    }
    return specs["gip_pipeline"].sql


def test_gip_pipeline_lists_only_the_pre_vote_stage():
    """"Moving toward a GIP" must mean phase-2, the pre-vote signalling stage.

    It used to list phase-1 too. phase-1 is the IDEA stage — upstream of a vote
    rather than moving toward one — and the only two phase-1 rows that ever
    qualified were the weakest in the panel (one had a single participant, five
    posts, and had been idle four months). They read as noise beside real GIPs.
    """
    sql = _gip_pipeline_sql()
    # The row filter selects phase-2 only; phase-1 survives solely as a count.
    assert re.search(r"WHERE\s+phase\s*=\s*'phase-2'", sql)
    assert "ideas_hidden" in sql


def test_gip_pipeline_window_is_tight_enough_to_mean_moving():
    """A 180-day window listed threads idle four months, which is not "moving".

    45 days is taken from the measured distribution, not picked: 3 of 157 open
    topics were touched within 30 days, 5 within 45, then nothing new until 104.
    """
    assert governance_explorer.GIP_PIPELINE_IDLE_DAYS == 45
    assert f"days_idle <= {governance_explorer.GIP_PIPELINE_IDLE_DAYS}" in _gip_pipeline_sql()


def test_gip_pipeline_exclusion_counts_leave_no_undisclosed_rows():
    """The two counts must PARTITION every pending row the list omits.

    Scoping `dormant_hidden` to phase-2 (the first attempt) left a phase-1 topic
    idle past the window in neither bucket: excluded and undisclosed, which is
    exactly the failure the counts exist to prevent. Verified against the live
    DB on 2026-07-30: 93 pending = 2 listed + 1 idea + 90 dormant, gap 0.
    """
    sql = _gip_pipeline_sql()
    idle = governance_explorer.GIP_PIPELINE_IDLE_DAYS
    # dormant counts BOTH phases past the window...
    assert re.search(
        r"SELECT count\(\) FROM pending\s*\n\s*WHERE days_idle > %d\) AS dormant_hidden" % idle,
        sql,
    )
    # ...so the ideas count only has to cover phase-1 INSIDE the window.
    assert re.search(
        r"WHERE phase = 'phase-1' AND days_idle <= %d\) AS ideas_hidden" % idle,
        sql,
    )
    # Both counts read from the not-yet-voted population the list is drawn from,
    # so they describe the same universe rather than a wider one.
    assert sql.count("FROM pending") == 3


# ---------------------------------------------------------------------------
# GIP forum phase vocabulary
# ---------------------------------------------------------------------------

#: The forum's phase tags, exhaustively. Measured against governance_db on
#: 2026-07-30: `SELECT extract(lower(tags),'phase-[0-9]+') ... GROUP BY` returns
#: exactly these three, with 74 / 84 / 33 topics.
GIP_PHASES = ("phase-1", "phase-2", "phase-3")


def test_the_gip_phase_vocabulary_is_exactly_three_values():
    """There is NO `phase-0`.

    It is a plausible-sounding tag that does not exist, and filtering on it
    would return nothing — silently, since an empty pipeline list looks the same
    as a quiet week. This pins the vocabulary so that assumption fails loudly
    instead.

    `phase-3` is the other half of the trap: it reads like a late/defunct stage,
    but it means the proposal is ALREADY at the vote (100% of phase-3 topics
    reached one), which is precisely why "moving toward a GIP" excludes it.
    """
    sql = sql_code(
        (
            governance_explorer.sql_loader.QUERIES_DIR
            / "governance" / "gip_pipeline.sql"
        ).read_text(encoding="utf-8")
    )
    referenced = set(re.findall(r"phase-\d+", sql))
    assert referenced, "gip_pipeline.sql references no phase at all"
    assert referenced <= set(GIP_PHASES), (
        f"gip_pipeline.sql filters on {sorted(referenced - set(GIP_PHASES))}, "
        f"which is not in the forum's vocabulary {GIP_PHASES}"
    )
    assert "phase-0" not in sql, "there is no phase-0"


def test_the_pipeline_lists_phase_2_and_discloses_the_other_two():
    """phase-1 and phase-3 are both EXCLUDED, for opposite reasons, and neither
    may vanish: phase-1 is counted into ideas_hidden, and phase-3 is already at
    a vote so it does not belong in a list of things approaching one."""
    sql = sql_code(
        (
            governance_explorer.sql_loader.QUERIES_DIR
            / "governance" / "gip_pipeline.sql"
        ).read_text(encoding="utf-8")
    )
    assert "phase = 'phase-2'" in sql, "the listed rows must be phase-2"
    assert "ideas_hidden" in sql, "phase-1 must be counted, not dropped"
    assert "dormant_hidden" in sql, "idle phase-2 topics must be counted"


def test_the_phase_vocabulary_is_written_down_where_it_is_used():
    """The persona and the spec description both state the vocabulary, because
    the analyst reads one and the tool caller reads the other. A rule recorded in
    only one of them is a rule half the callers never see."""
    persona = (
        governance_explorer.__file__.rsplit("/tools/", 1)[0]
        + "/prompts/agents/dao_governance_analyst.md"
    )
    with open(persona, encoding="utf-8") as fh:
        text = fh.read()
    assert "no `phase-0`" in text.lower() or "NO `phase-0`" in text
    for phase in GIP_PHASES:
        assert phase in text, f"{phase} missing from the persona"

    spec = next(
        s for s in governance_explorer._overview_specs(
            governance_explorer._range_state("", ""),
        )
        if s.key == "gip_pipeline"
    )
    assert "no phase-0" in spec.basis.lower()
    assert "phase-3" in spec.basis
