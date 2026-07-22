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


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
PROPOSAL_ID = "0x" + "ab" * 32
VOTER = "0x" + "cd" * 20
SENTINEL_TEXT = "zz_sentinel_zz"
GOV_TOOLS = (
    "open_governance", "load_governance_section", "load_governance_datasets",
    "search_governance", "load_governance_entity",
)
APP_ONLY_TOOLS = frozenset(GOV_TOOLS) - {"open_governance"}


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
    for kind, identifier in (
        ("proposal", PROPOSAL_ID), ("voter", VOTER),
        ("forum_topic", "987654"), ("forum_user", "987654"),
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
    specs = _all_specs()
    assert specs
    for spec in specs:
        sql = spec.sql
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


GIP_FIXTURES = [
    ("GIP-151: Should GnosisDAO fund X", 151),
    ("GIP 152 - Treasury topup", 152),
    ("discussing gip-128 here", 128),
    ("GIP-0042 legacy numbering", 42),
    ("AGIP-5 is another DAO's numbering", None),
    ("no token here", None),
]


def test_gip_extraction_exact_patterns_only():
    pattern = re.compile(governance_explorer.GIP_PATTERN, re.IGNORECASE)
    for text, expected in GIP_FIXTURES:
        match = pattern.search(text)
        got = int(match.group(1)) if match else None
        assert got == expected, text
    # The SQL side carries the verbatim shared regex literal.
    sql_literal = r"(?i)\\bGIP[\\s-]?0*([0-9]+)"
    proposals = {s.key: s for s in governance_explorer._proposals_specs(
        governance_explorer._range_state("", ""),
        governance_explorer._default_filters(),
    )}["proposals"]
    assert sql_literal in proposals.sql
    # Link specs use exact GIP equality joins — never fuzzy text joins.
    links = {s.key: s for s in governance_explorer._entity_specs("proposal", PROPOSAL_ID)}
    link_sql = links["proposal_forum_links"].sql
    assert sql_literal in link_sql
    assert "positionCaseInsensitive" not in link_sql
    assert "LIKE" not in link_sql.upper()
    reverse = {s.key: s for s in governance_explorer._entity_specs("forum_topic", "12131")}
    reverse_sql = reverse["topic_proposal_links"].sql
    assert sql_literal in reverse_sql
    assert "positionCaseInsensitive" not in reverse_sql


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
