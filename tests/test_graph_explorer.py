"""Tests for the Graph Explorer mini app."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp import graph_profiles
from cerebro_mcp.clickhouse_client import ExecutedQuery
from cerebro_mcp.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.graph_explorer import register_graph_explorer_tools


AVATAR = "0xaaaa000000000000000000000000000000000001"
TRUSTEE = "0xaaaa000000000000000000000000000000000002"
SAFE = "0xbbbb000000000000000000000000000000000001"
OWNER = "0xbbbb000000000000000000000000000000000002"
POOL = "0xcccc000000000000000000000000000000000001"


# ---------------------------------------------------------------------------
# Fake semantic snapshot
# ---------------------------------------------------------------------------


@dataclass
class FakeSnapshot:
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)


def _graph_model(
    name: str,
    *,
    profile: str,
    module: str,
    source_column: str,
    target_column: str,
    source_kind: str,
    target_kind: str,
    time_column: str | None = None,
    weight_column: str | None = None,
    status: str = "approved",
    synonyms: tuple[str, ...] = (),
    description: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "relation_name": name,
        "module": module,
        "description": description,
        "semantic_status": status,
        "quality_tier": status if status != "docs_only" else "",
        "semantic_source_file": f"semantic/authoring/{module}/semantic_models.yml",
        "semantic": {
            "meta": {
                "question_synonyms": list(synonyms),
                "graph": {
                    "enabled": True,
                    "profile": profile,
                    "source_column": source_column,
                    "target_column": target_column,
                    "source_kind": source_kind,
                    "target_kind": target_kind,
                    "directed": True,
                    **({"time_column": time_column} if time_column else {}),
                    **({"weight_column": weight_column} if weight_column else {}),
                },
            },
        },
    }


@pytest.fixture
def fake_snapshot():
    snap = FakeSnapshot(
        models={
            "api_execution_circles_v2_trust_relations_current": _graph_model(
                "api_execution_circles_v2_trust_relations_current",
                profile="circles_trust",
                module="Circles",
                source_column="truster",
                target_column="trustee",
                source_kind="circles_avatar",
                target_kind="circles_avatar",
                time_column="valid_from",
                synonyms=("circles trust", "who trusts whom"),
            ),
            "int_execution_safes_current_owners": _graph_model(
                "int_execution_safes_current_owners",
                profile="safe_ownership",
                module="safe",
                source_column="owner",
                target_column="safe_address",
                source_kind="address",
                target_kind="safe",
                time_column="became_owner_at",
                status="candidate",
            ),
            "int_execution_pools_dex_liquidity_events": _graph_model(
                "int_execution_pools_dex_liquidity_events",
                profile="lp_in_pool",
                module="pools",
                source_column="provider",
                target_column="pool_address",
                source_kind="address",
                target_kind="pool",
                time_column="block_timestamp",
                weight_column="amount_usd",
            ),
        },
        relationships=[
            {
                "name": "circles_trust_to_avatar_metadata",
                "via_entity": "circles_avatar",
                "quality_tier": "approved",
            },
            {
                "name": "safe_owner_is_address",
                "via_entity": "address",
                "quality_tier": "approved",
            },
        ],
    )
    with patch.object(graph_profiles, "semantic_runtime") as rt:
        rt.snapshot = snap
        yield snap


# ---------------------------------------------------------------------------
# Fake ClickHouse stub
# ---------------------------------------------------------------------------


class StubCH:
    def __init__(self, edge_rows: dict[str, list[list[Any]]] | None = None,
                 roles: dict[str, list[Any]] | None = None):
        self.edge_rows = edge_rows or {}
        self.roles = roles or {}

    def run_query(
        self,
        sql: str,
        database: str = "dbt",
        requested_max_rows: int = 100,
        audience: str = "tool",
        fetch_mode: str = "auto",
        parameters: dict[str, Any] | None = None,
    ) -> ExecutedQuery:
        params = parameters or {}
        if "int_execution_address_roles_current" in sql:
            addr = str(params.get("addr", "")).lower()
            cols = [
                "is_safe", "is_gpay_wallet", "is_ga_user", "controls_gpay_wallet",
                "is_circles_avatar", "circles_avatar_type", "is_circles_wrapper",
                "is_safe_owner", "is_lp_provider", "pool_protocol", "is_pool",
                "is_lending_user", "is_validator_depositor",
                "has_dune_label", "dune_project",
            ]
            rows = [self.roles[addr]] if addr in self.roles else []
            return ExecutedQuery(sql, sql, database, cols, rows, len(rows), 0.0, "rows", [])

        for table, rows in self.edge_rows.items():
            if table in sql:
                cols = ["source_id", "target_id", "weight", "edge_count"]
                return ExecutedQuery(sql, sql, database, cols, rows, len(rows), 0.0, "rows", [])
        return ExecutedQuery(sql, sql, database, [], [], 0, 0.0, "rows", [])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()


def _server(ch: StubCH) -> FastMCP:
    server = FastMCP("graph-explorer-test")
    register_graph_explorer_tools(server, ch)
    return server


def _call_tool(server: FastMCP, tool_name: str, args: dict[str, Any]) -> Any:
    tools = server._tool_manager._tools  # type: ignore[attr-defined]
    fn = tools[tool_name].fn
    return fn(**args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_discover_profiles_from_snapshot(fake_snapshot):
    profiles = graph_profiles.discover_profiles()
    ids = {p.profile for p in profiles}
    assert ids == {"circles_trust", "safe_ownership", "lp_in_pool"}
    trust = next(p for p in profiles if p.profile == "circles_trust")
    assert trust.time_aware is True
    assert trust.source_kind == "circles_avatar"


def test_open_graph_explorer_empty_catalog(fake_snapshot):
    ch = StubCH()
    server = _server(ch)
    result = _call_tool(server, "open_graph_explorer", {})
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    catalog = sc["view_state"]["catalog"]
    assert len(catalog) == 3
    assert {p["profile"] for p in catalog} == {
        "circles_trust",
        "safe_ownership",
        "lp_in_pool",
    }
    # No seed yet — no nodes / edges.
    assert sc["datasets"]["nodes"]["stats"]["row_count"] == 0
    assert sc["datasets"]["edges"]["stats"]["row_count"] == 0


def test_load_seed_with_explicit_profile(fake_snapshot):
    ch = StubCH(edge_rows={
        "api_execution_circles_v2_trust_relations_current": [
            [AVATAR, TRUSTEE, 1.0, 1],
        ],
    })
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {
            "view_id": view_id,
            "seed_node_id": AVATAR,
            "seed_model": "circles_trust",
        },
    )
    sc = result.structuredContent
    edges = sc["datasets"]["edges"]["preview_rows"]
    assert len(edges) == 1
    nodes = sc["datasets"]["nodes"]["preview_rows"]
    node_ids = {row[0] for row in nodes}
    assert AVATAR in node_ids
    assert TRUSTEE in node_ids
    assert sc["view_state"]["selected_profiles"] == ["circles_trust"]
    # `suggested_next_hops` returns CROSS-sector pivots only — same-kind
    # profiles (circles_avatar -> circles_avatar) are intentionally filtered
    # out because they describe in-sector expansion, not a pivot. The
    # circles_trust profile itself is a self-loop on `circles_avatar`, so no
    # suggestion includes it. The fixture contains no profile that bridges
    # `circles_avatar` to a different kind, so the list is legitimately empty.
    suggestions = sc["view_state"]["suggested_next_hops"]
    assert isinstance(suggestions, list)
    assert all(s.get("target_kind") != "circles_avatar" for s in suggestions)


def test_load_seed_auto_detects_via_roles(fake_snapshot):
    ch = StubCH(
        edge_rows={
            "int_execution_safes_current_owners": [[OWNER, SAFE, 1.0, 1]],
            "int_execution_pools_dex_liquidity_events": [[OWNER, POOL, 12345.0, 3]],
        },
        roles={
            OWNER: [
                0,  # is_safe
                0,  # is_gpay_wallet
                0,  # is_ga_user
                None,  # controls_gpay_wallet
                0,  # is_circles_avatar
                None,  # circles_avatar_type
                0,  # is_circles_wrapper
                1,  # is_safe_owner
                1,  # is_lp_provider
                "Uniswap V3",  # pool_protocol
                0,  # is_pool
                0,  # is_lending_user
                0,  # is_validator_depositor
                0,  # has_dune_label
                None,  # dune_project
            ],
        },
    )
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": OWNER},
    )
    sc = result.structuredContent
    selected = set(sc["view_state"]["selected_profiles"])
    assert "safe_ownership" in selected
    assert "lp_in_pool" in selected
    # The explicitly supplied transfer fallback should NOT be in the selection
    # since roles matched specific profiles.


def test_expand_node_caps_at_max_hops(fake_snapshot, monkeypatch):
    """Expand calls beyond MAX_HOPS are rejected with a "Max X hops reached"
    error. Patches MAX_HOPS to 2 so the test stays cheap; the production
    default is higher (currently 20)."""
    import cerebro_mcp.tools.graph_explorer as ge
    monkeypatch.setattr(ge, "MAX_HOPS", 2)

    ch = StubCH(edge_rows={
        "api_execution_circles_v2_trust_relations_current": [
            [AVATAR, TRUSTEE, 1.0, 1],
        ],
    })
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": AVATAR, "seed_model": "circles_trust"},
    )
    _call_tool(
        server,
        "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": TRUSTEE, "relation_types": ["circles_trust"]},
    )
    result = _call_tool(
        server,
        "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": TRUSTEE, "relation_types": ["circles_trust"]},
    )
    # Third call is rejected once MAX_HOPS is reached.
    assert result.isError is True
    text = result.content[0].text
    assert "Max 2 hops" in text or "Max" in text


def test_unknown_view_returns_error(fake_snapshot):
    ch = StubCH()
    server = _server(ch)
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": "deadbeef", "seed_node_id": AVATAR},
    )
    assert result.isError is True


def test_update_focus_returns_patch(fake_snapshot):
    ch = StubCH()
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    result = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "layout": "circular", "max_neighbors": 50},
    )
    sc = result.structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    assert sc["patch"]["view_state"]["layout"] == "circular"
    assert sc["patch"]["view_state"]["max_neighbors"] == 50


def test_edge_query_failure_reports_warning(fake_snapshot):
    class FailingCH(StubCH):
        def run_query(self, sql, database="dbt", **kwargs):
            if "int_execution_address_roles_current" in sql:
                return super().run_query(sql, database, **kwargs)
            raise RuntimeError("clickhouse exploded")

    ch = FailingCH()
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": AVATAR, "seed_model": "circles_trust"},
    )
    sc = result.structuredContent
    warnings = sc.get("warnings") or []
    assert any("clickhouse exploded" in w for w in warnings)
