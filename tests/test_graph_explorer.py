"""Tests for the Graph Explorer mini app."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.semantic import graph_profiles
from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools.visualization import mini_apps
from cerebro_mcp.tools.semantic.graph_explorer import register_graph_explorer_tools
from cerebro_mcp.tools.semantic.graph_explorer import constants
from cerebro_mcp.tools.semantic.graph_explorer.forensics import (
    reset_source_contract_cache_for_tests,
)


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
    # WS1 cached views — empty by default so the no-cache path (live scan of
    # `models`) is still exercised by most fixtures.
    graph_profiles: tuple[Any, ...] = ()
    profiles_by_id: dict[str, Any] = field(default_factory=dict)
    kind_to_profiles: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    graph_search_documents: tuple[dict[str, Any], ...] = ()
    graph_catalog_hash: str = ""


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
    time_end_column: str | None = None,
    temporal_semantics: str | None = None,
    weight_column: str | None = None,
    directed: bool = True,
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
                    "directed": directed,
                    **({"time_column": time_column} if time_column else {}),
                    **(
                        {"time_end_column": time_end_column}
                        if time_end_column
                        else {}
                    ),
                    **(
                        {"temporal_semantics": temporal_semantics}
                        if temporal_semantics
                        else {}
                    ),
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
    # Populate the WS1/WS5 cached views the way SemanticRuntime._build_snapshot
    # does, so the new graph-native tools exercise their real (cached) paths.
    from cerebro_mcp.semantic.graph_extraction import synthesize_search_documents

    profiles = tuple(graph_profiles.discover_profiles(models=snap.models))
    snap.graph_profiles = profiles
    snap.profiles_by_id = {p.profile: p for p in profiles}
    snap.kind_to_profiles = graph_profiles.build_kind_index(profiles)
    snap.graph_search_documents = tuple(synthesize_search_documents(profiles))
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
        if "FROM system.columns" in sql:
            required = list(params.get("required") or [])
            rows = [[name, "String"] for name in required]
            return ExecutedQuery(
                sql, sql, database, ["name", "type"], rows, len(rows), 0.0, "rows", []
            )
        if "AS source_horizon" in sql:
            rows = [["2026-07-18T00:00:00Z"]]
            return ExecutedQuery(
                sql,
                sql,
                database,
                ["source_horizon"],
                rows,
                1,
                0.0,
                "rows",
                [],
            )
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
    reset_source_contract_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    reset_source_contract_cache_for_tests()
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


def test_discover_profiles_prefers_cached_tuple(fake_snapshot):
    # WS1: when the snapshot carries a cached graph_profiles tuple, discover_profiles
    # returns it verbatim rather than rescanning models.
    live = tuple(graph_profiles.discover_profiles(models=fake_snapshot.models))
    fake_snapshot.graph_profiles = live
    returned = graph_profiles.discover_profiles()
    assert [p.profile for p in returned] == [p.profile for p in live]
    # Mutating models afterwards must NOT change the cached result.
    fake_snapshot.models = {}
    assert [p.profile for p in graph_profiles.discover_profiles()] == [p.profile for p in live]


def test_profile_by_id_uses_snapshot_index(fake_snapshot):
    # WS1: profile_by_id reads the precomputed profiles_by_id index when present.
    live = tuple(graph_profiles.discover_profiles(models=fake_snapshot.models))
    fake_snapshot.profiles_by_id = {p.profile: p for p in live}
    fake_snapshot.models = {}  # index lookup must not fall back to a model scan
    found = graph_profiles.profile_by_id("safe_ownership")
    assert found is not None and found.profile == "safe_ownership"
    assert graph_profiles.profile_by_id("does_not_exist") is None


def test_build_kind_index_groups_by_kind(fake_snapshot):
    # WS1: build_kind_index maps each node kind to the profiles touching it; a
    # self-referential profile (circles_avatar -> circles_avatar) appears once.
    profiles = graph_profiles.discover_profiles(models=fake_snapshot.models)
    index = graph_profiles.build_kind_index(profiles)
    assert {p.profile for p in index["address"]} == {"safe_ownership", "lp_in_pool"}
    assert [p.profile for p in index["circles_avatar"]] == ["circles_trust"]
    fake_snapshot.kind_to_profiles = index
    fake_snapshot.models = {}
    assert {p.profile for p in graph_profiles.profiles_for_kind("address")} == {
        "safe_ownership",
        "lp_in_pool",
    }


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
    assert sc["datasets"]["tx_raw_receipts"]["stats"]["row_count"] == 0
    assert [
        column["name"] for column in sc["datasets"]["tx_raw_receipts"]["columns"]
    ] == constants.TX_RAW_RECEIPTS_COLUMNS


def test_open_explicit_atlas_route_with_seed_keeps_catalog_mode(fake_snapshot):
    """Standalone ``?mode=atlas&seed=…`` must not launch Investigate.

    The web route filters query arguments against the open-tool signature. If
    ``mode`` is absent from that signature it is discarded while ``seed`` is
    forwarded, and the initial payload contradicts the public deep link before
    React has mounted.
    """
    ch = StubCH(
        edge_rows={
            "api_execution_circles_v2_trust_relations_current": [
                [AVATAR, TRUSTEE, 1.0, 1],
            ],
        }
    )
    server = _server(ch)
    result = _call_tool(
        server,
        "open_graph_explorer",
        {
            "seed_node_id": AVATAR,
            "seed_model": "circles_trust",
            "mode": "atlas",
        },
    )
    state = result.structuredContent["view_state"]

    assert state["mode"] == "atlas"
    assert state["mode_revision"] == 1
    assert state["investigate"]["seed"]["id"] == AVATAR
    assert state["investigate"]["active_profiles"] == ["circles_trust"]
    assert result.structuredContent["datasets"]["edges"]["stats"]["row_count"] == 1


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
            "request_id": 7,
        },
    )
    sc = result.structuredContent
    edges = sc["datasets"]["edges"]["preview_rows"]
    assert len(edges) == 1
    nodes = sc["datasets"]["nodes"]["preview_rows"]
    node_ids = {row[0] for row in nodes}
    assert AVATAR in node_ids
    assert TRUSTEE in node_ids
    # A direct seed data load no longer flips mode (owned by explicit mode
    # commands); the view stays in whatever mode it was opened (atlas).
    assert sc["view_state"]["mode"] == "atlas"
    assert sc["view_state"]["investigate"]["active_profiles"] == ["circles_trust"]
    scope = sc["view_state"]["investigate"]["scope"]
    assert scope["request_id"] == 7
    assert scope["status"] == "ready"
    assert scope["sources"][0]["name"] == (
        "dbt.api_execution_circles_v2_trust_relations_current"
    )
    assert scope["sources"][0]["horizon"] == "2026-07-18T00:00:00Z"
    assert scope["sources"][0]["fetched_at"]
    assert scope["data_horizon"] == "2026-07-18T00:00:00Z"
    for key in ("nodes", "edges", "graph_metrics"):
        assert sc["view_state"]["dataset_scopes"][key] == scope["scope_id"]
        assert sc["datasets"][key]["scope_id"] == scope["scope_id"]
        assert sc["datasets"][key]["provenance"]["scope_id"] == scope["scope_id"]
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
    selected = set(sc["view_state"]["investigate"]["active_profiles"])
    assert "safe_ownership" in selected
    assert "lp_in_pool" in selected
    # The explicitly supplied transfer fallback should NOT be in the selection
    # since roles matched specific profiles.


def test_expand_node_caps_at_max_hops(fake_snapshot, monkeypatch):
    """Expand calls beyond MAX_HOPS are rejected with a "Max X hops reached"
    error. Only expands that actually GREW the graph consume hops, so the
    stub's edge list is grown between calls. Patches MAX_HOPS to 2 so the
    test stays cheap; the production default is higher."""
    import cerebro_mcp.tools.semantic.graph_explorer as ge
    # Limits live in the constants submodule; tools read them attribute-style
    # precisely so this patch takes effect.
    monkeypatch.setattr(ge.constants, "MAX_HOPS", 2)

    trust_rows = [[AVATAR, TRUSTEE, 1.0, 1]]
    ch = StubCH(edge_rows={
        "api_execution_circles_v2_trust_relations_current": trust_rows,
    })
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": AVATAR, "seed_model": "circles_trust"},
    )
    # A new edge appears on chain — this expand gains a node, consuming a hop
    # and reaching the (patched) cap of 2.
    trust_rows.append([TRUSTEE, OWNER, 1.0, 1])
    grew = _call_tool(
        server,
        "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": TRUSTEE, "relation_types": ["circles_trust"]},
    )
    assert grew.isError is False
    assert grew.structuredContent["view_state"]["investigate"]["hops_used"] == 2
    result = _call_tool(
        server,
        "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": OWNER, "relation_types": ["circles_trust"]},
    )
    # Next call is rejected once MAX_HOPS is reached.
    assert result.isError is True
    text = result.content[0].text
    assert "Max 2 hops" in text or "Max" in text


def test_expand_zero_gain_keeps_hops_and_warns(fake_snapshot):
    """A no-op expand must not consume a hop, and must TELL the user nothing
    was found instead of silently reporting success."""
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
    result = _call_tool(
        server,
        "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": TRUSTEE, "relation_types": ["circles_trust"]},
    )
    assert result.isError is False
    vs = result.structuredContent["view_state"]
    assert vs["investigate"]["hops_used"] == 1  # unchanged
    assert any("no new nodes or edges" in w.lower() for w in vs["warnings"])
    assert "nothing new" in result.content[0].text


def test_expand_seed_advances_frontier_not_reload(fake_snapshot):
    """Expanding the SEED again is a frontier round: the canvas leaves (not
    the seed itself) are queried, so hop-2 neighborhoods actually load."""
    trust_rows = [[AVATAR, TRUSTEE, 1.0, 1]]
    ch = RecordingStubCH(edge_rows={
        "api_execution_circles_v2_trust_relations_current": trust_rows,
    })
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    view_id = opened.structuredContent["view_id"]
    _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": AVATAR, "seed_model": "circles_trust"},
    )
    trust_rows.append([TRUSTEE, OWNER, 1.0, 1])
    ch.calls.clear()
    result = _call_tool(
        server,
        "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": AVATAR, "relation_types": ["circles_trust"]},
    )
    assert result.isError is False
    vs = result.structuredContent["view_state"]
    # The frontier round queried the LEAF (trustee), not the already-expanded
    # seed, and the seed+leaf are now both recorded as expanded.
    seed_id_params = [c["seed_ids"] for c in ch.calls]
    assert any(TRUSTEE in ids for ids in seed_id_params)
    assert all(AVATAR not in ids for ids in seed_id_params)
    assert set(vs["investigate"]["expanded_ids"]) >= {AVATAR, TRUSTEE}
    assert vs["investigate"]["hops_used"] == 2
    node_ids = {r[0] for r in result.structuredContent["datasets"]["nodes"]["preview_rows"]}
    assert OWNER in node_ids


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
    assert sc["patch"]["view_state"]["investigate"]["max_neighbors"] == 50


def test_focus_evidence_is_subject_stamped_symmetric_and_monotonic(fake_snapshot):
    role_row = [
        True, False, False, "", False, "", False, False, False, "",
        False, False, False, False, "",
    ]
    ch = StubCH(
        edge_rows={TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]]},
        roles={AVATAR: role_row},
    )
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]

    node = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "selected_node_id": AVATAR, "request_id": 11},
    )
    node_rows = node.structuredContent["patch"]["datasets"]["node_evidence"][
        "preview_rows"
    ]
    assert node_rows
    assert all(row[0] == AVATAR and row[3:] == ["node", 11] for row in node_rows)
    assert node.structuredContent["patch"]["datasets"]["edge_evidence"][
        "preview_rows"
    ] == []

    edge_id = f"circles_trust:{AVATAR}->{TRUSTEE}"
    edge = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "selected_edge_id": edge_id, "request_id": 12},
    )
    edge_rows = edge.structuredContent["patch"]["datasets"]["edge_evidence"][
        "preview_rows"
    ]
    assert edge_rows
    assert all(row[0] == edge_id and row[3:] == ["edge", 12] for row in edge_rows)
    assert edge.structuredContent["patch"]["datasets"]["node_evidence"][
        "preview_rows"
    ] == []

    node_again = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "selected_node_id": AVATAR, "request_id": 13},
    )
    assert node_again.structuredContent["patch"]["datasets"]["node_evidence"][
        "preview_rows"
    ]
    assert node_again.structuredContent["patch"]["datasets"]["edge_evidence"][
        "preview_rows"
    ] == []

    stale = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "selected_node_id": TRUSTEE, "request_id": 11},
    )
    assert stale.isError is True
    stored = mini_apps.get_view(view_id)
    assert stored is not None
    assert stored.view_state["selection"] == {
        "node_id": AVATAR, "edge_id": "", "request_id": 13,
    }

    cleared = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "request_id": 14},
    )
    assert cleared.isError is not True
    for key in ("node_evidence", "edge_evidence"):
        assert cleared.structuredContent["patch"]["datasets"][key][
            "preview_rows"
        ] == []


def test_node_focus_query_failure_is_failed_unknown_not_verified_empty(
    fake_snapshot,
):
    class NodeEvidenceFailureCH(StubCH):
        def run_query(self, sql, database="dbt", **kwargs):
            if "FROM int_execution_address_roles_current" in sql:
                raise RuntimeError("role evidence source unavailable")
            return super().run_query(sql, database, **kwargs)

    server = _server(NodeEvidenceFailureCH())
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent[
        "view_id"
    ]
    result = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "selected_node_id": AVATAR, "request_id": 21},
    )

    assert result.isError is not True
    patch = result.structuredContent["patch"]
    assert patch["datasets"]["node_evidence"]["preview_rows"] == []
    assert patch["datasets"]["edge_evidence"]["preview_rows"] == []
    scope = patch["view_state"]["focus_scope"]
    assert scope["status"] == "failed"
    assert scope["verification"]["status"] == "unverified"
    assert scope["coverage"]["rows"] == {"shown": 0, "total": None}
    assert scope["coverage"]["nodes"] == {"shown": 0, "total": None}
    assert scope["data_horizon"] == "2026-07-18T00:00:00Z"
    assert scope["sources"][0]["status"] == "error"
    assert "role evidence source unavailable" in scope["sources"][0]["error"]
    assert patch["view_state"]["selection"] == {
        "node_id": AVATAR,
        "edge_id": "",
        "request_id": 21,
    }


def test_edge_focus_query_failure_is_failed_unknown_not_verified_empty(
    fake_snapshot,
):
    class EdgeEvidenceFailureCH(StubCH):
        def run_query(self, sql, database="dbt", **kwargs):
            if (
                "SELECT *" in sql
                and "api_execution_circles_v2_trust_relations_current" in sql
            ):
                raise RuntimeError("edge evidence source unavailable")
            return super().run_query(sql, database, **kwargs)

    server = _server(EdgeEvidenceFailureCH())
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent[
        "view_id"
    ]
    edge_id = f"circles_trust:{AVATAR}->{TRUSTEE}"
    result = _call_tool(
        server,
        "update_graph_explorer_focus",
        {"view_id": view_id, "selected_edge_id": edge_id, "request_id": 22},
    )

    assert result.isError is not True
    patch = result.structuredContent["patch"]
    assert patch["datasets"]["edge_evidence"]["preview_rows"] == []
    assert patch["datasets"]["node_evidence"]["preview_rows"] == []
    scope = patch["view_state"]["focus_scope"]
    assert scope["status"] == "failed"
    assert scope["verification"]["status"] == "unverified"
    assert scope["coverage"]["rows"] == {"shown": 0, "total": None}
    assert scope["coverage"]["edges"] == {"shown": 0, "total": None}
    assert scope["data_horizon"] == "2026-07-18T00:00:00Z"
    assert scope["sources"][0]["status"] == "error"
    assert "edge evidence source unavailable" in scope["sources"][0]["error"]
    assert patch["view_state"]["selection"] == {
        "node_id": "",
        "edge_id": edge_id,
        "request_id": 22,
    }


@pytest.mark.parametrize("subject_kind", ["node", "edge"])
def test_focus_verified_empty_requires_successful_exact_query(
    fake_snapshot,
    subject_kind,
):
    relation = "api_execution_circles_v2_trust_relations_current"
    server = _server(StubCH(edge_rows={relation: []}))
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent[
        "view_id"
    ]
    request = {"view_id": view_id, "request_id": 23}
    if subject_kind == "node":
        request["selected_node_id"] = AVATAR
    else:
        request["selected_edge_id"] = f"circles_trust:{AVATAR}->{TRUSTEE}"

    result = _call_tool(server, "update_graph_explorer_focus", request)

    assert result.isError is not True
    patch = result.structuredContent["patch"]
    assert patch["datasets"]["node_evidence"]["preview_rows"] == []
    assert patch["datasets"]["edge_evidence"]["preview_rows"] == []
    scope = patch["view_state"]["focus_scope"]
    assert scope["status"] == "ready"
    assert scope["verification"]["status"] == "verified"
    assert scope["coverage"]["rows"] == {"shown": 0, "total": 0}
    assert scope["data_horizon"] == "2026-07-18T00:00:00Z"
    assert scope["sources"][0]["status"] == "ok"
    assert scope["sources"][0]["horizon"] == "2026-07-18T00:00:00Z"


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
    scope = sc["view_state"]["investigate"]["scope"]
    assert scope["status"] == "failed"
    assert scope["coverage"]["rows"]["total"] is None
    assert scope["sources"][0]["status"] == "error"


def test_relationship_horizon_probe_failure_is_not_a_ready_empty_graph(
    fake_snapshot,
):
    class MissingFreshnessCH(StubCH):
        def run_query(self, sql, database="dbt", **kwargs):
            if "AS source_horizon" in sql:
                raise RuntimeError("freshness probe unavailable")
            return super().run_query(sql, database, **kwargs)

    ch = MissingFreshnessCH(
        edge_rows={
            "api_execution_circles_v2_trust_relations_current": [
                [AVATAR, TRUSTEE, 1.0, 1],
            ],
        }
    )
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent[
        "view_id"
    ]
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {
            "view_id": view_id,
            "seed_node_id": AVATAR,
            "seed_model": "circles_trust",
        },
    )
    scope = result.structuredContent["view_state"]["investigate"]["scope"]
    assert scope["status"] == "failed"
    assert scope["data_horizon"] is None
    assert scope["sources"][0]["status"] == "error"
    assert "freshness probe unavailable" in scope["sources"][0]["error"]
    assert scope["coverage"]["rows"]["total"] is None


# ---------------------------------------------------------------------------
# WS5/6/7 — graph-native tools
# ---------------------------------------------------------------------------


def test_search_graph_catalog_matches_profile(fake_snapshot):
    server = _server(StubCH())
    res = _call_tool(server, "search_graph_catalog", {"query": "circles trust"})
    ids = [r["id"] for r in res["results"]]
    assert "profile:circles_trust" in ids
    assert res["results_from_fallback"] is True  # no catalog hash on the fake


def test_search_graph_catalog_tier_gate(fake_snapshot):
    # safe_ownership is candidate-tier; default approved gate hides it, all shows it.
    server = _server(StubCH())
    approved = _call_tool(server, "search_graph_catalog", {"query": "safe ownership"})
    assert "profile:safe_ownership" not in [r["id"] for r in approved["results"]]
    assert approved["hidden_by_tier_count"] >= 1
    allt = _call_tool(server, "search_graph_catalog", {"query": "safe ownership", "min_quality_tier": "all"})
    assert "profile:safe_ownership" in [r["id"] for r in allt["results"]]


def test_search_graph_catalog_node_kind_filter(fake_snapshot):
    server = _server(StubCH())
    res = _call_tool(server, "search_graph_catalog", {"query": "pool", "node_kind": "pool"})
    ids = {r["id"] for r in res["results"]}
    # lp_in_pool has target_kind=pool; the node:pool doc is also in scope
    assert "profile:lp_in_pool" in ids


def test_search_graph_catalog_empty_query_browses(fake_snapshot):
    server = _server(StubCH())
    res = _call_tool(server, "search_graph_catalog", {"query": "", "min_quality_tier": "all"})
    assert res["browse"] is True
    assert res["count"] > 0


def test_explore_neighborhood_one_hop(fake_snapshot):
    ch = StubCH(edge_rows={
        "api_execution_circles_v2_trust_relations_current": [[AVATAR, TRUSTEE, 1.0, 1]],
    })
    server = _server(ch)
    res = _call_tool(
        server,
        "explore_neighborhood",
        {"seed_ids": [AVATAR], "profiles": ["circles_trust"], "hops": 1},
    )
    node_ids = {n["id"] for n in res["nodes"]}
    assert {AVATAR, TRUSTEE} <= node_ids
    assert res["edge_count"] == 1
    assert "circles_trust" in res["profiles_used"]
    assert res["truncated"] is False


def test_explore_neighborhood_respects_max_nodes(fake_snapshot):
    ch = StubCH(edge_rows={
        "api_execution_circles_v2_trust_relations_current": [
            [AVATAR, TRUSTEE, 1.0, 1],
            [AVATAR, "0xaaaa000000000000000000000000000000000003", 1.0, 1],
        ],
    })
    server = _server(ch)
    res = _call_tool(
        server,
        "explore_neighborhood",
        {"seed_ids": [AVATAR], "profiles": ["circles_trust"], "hops": 2, "max_nodes": 1},
    )
    assert res["truncated"] is True
    assert res["node_count"] <= 1


def test_explore_neighborhood_no_seeds(fake_snapshot):
    res = _call_tool(_server(StubCH()), "explore_neighborhood", {"seed_ids": []})
    assert res["nodes"] == [] and res["edges"] == []
    assert any("no seed_ids" in w for w in res["warnings"])


def test_explore_neighborhood_merges_node_profiles_across_profiles(fake_snapshot):
    # AVATAR is the source in both circles_trust and safe_ownership this walk;
    # its profiles list must carry BOTH, not just the first discovered.
    ch = StubCH(edge_rows={
        "api_execution_circles_v2_trust_relations_current": [[AVATAR, TRUSTEE, 1.0, 1]],
        "int_execution_safes_current_owners": [[AVATAR, SAFE, 1.0, 1]],
    })
    server = _server(ch)
    res = _call_tool(
        server,
        "explore_neighborhood",
        {"seed_ids": [AVATAR], "profiles": ["circles_trust", "safe_ownership"], "hops": 1},
    )
    avatar = next(n for n in res["nodes"] if n["id"] == AVATAR)
    assert {"circles_trust", "safe_ownership"} <= set(avatar["profiles"])


def test_calculate_flow_efficiency_basic(fake_snapshot):
    ch = StubCH(edge_rows={
        "int_execution_pools_dex_liquidity_events": [["0xprovider", 200.0, 100.0]],
    })
    server = _server(ch)
    res = _call_tool(
        server,
        "calculate_flow_efficiency",
        {"profile": "lp_in_pool", "node_ids": ["0xprovider"]},
    )
    assert res["weight_unit"] == "amount_usd"
    node = res["nodes"][0]
    assert node["efficiency"] == 2.0 and node["status"] == "ok"


def test_calculate_flow_efficiency_zero_inflow_is_null(fake_snapshot):
    ch = StubCH(edge_rows={
        "int_execution_pools_dex_liquidity_events": [["0xsink", 50.0, 0.0]],
    })
    server = _server(ch)
    res = _call_tool(
        server,
        "calculate_flow_efficiency",
        {"profile": "lp_in_pool", "node_ids": ["0xsink"]},
    )
    node = res["nodes"][0]
    assert node["efficiency"] is None and node["status"] == "no_inflow"


def test_build_node_flow_sql_casts_weight_to_float():
    # Regression: a UInt64 weight_column must be wrapped in toFloat64 so the
    # inflow/outflow UNION legs unify with the toFloat64(0) constant — otherwise
    # ClickHouse raises NO_COMMON_TYPE (Float64 vs UInt64). Only caught live.
    from cerebro_mcp.semantic.graph_profiles import GraphProfile, build_node_flow_sql

    prof = GraphProfile(
        profile="p", model_name="m", relation_name="m",
        source_column="a", target_column="b",
        source_kind="address", target_kind="address", weight_column="w",
    )
    sql, _ = build_node_flow_sql(prof, node_ids=["0xabc"])
    assert "toFloat64(sum(w))" in sql
    assert "toFloat64(0)" in sql


def test_profile_contract_extracts_physical_columns_from_endpoint_expression():
    from cerebro_mcp.semantic.graph_profiles import GraphProfile
    from cerebro_mcp.tools.semantic.graph_explorer.forensics import (
        physical_columns_from_expression,
    )
    from cerebro_mcp.tools.semantic.graph_explorer.ui_tools import (
        _profile_contract_columns,
    )

    withdrawal_address = (
        "concat('0x', substring(withdrawal_credentials, 27, 40))"
    )
    profile = GraphProfile(
        profile="deposit_to_validator",
        model_name="int_GBCDeposit_deposists_daily",
        relation_name="int_GBCDeposit_deposists_daily",
        source_column=withdrawal_address,
        target_column="validator_index",
        source_kind="address",
        target_kind="validator",
        time_column="date",
        weight_column="amount",
    )

    assert physical_columns_from_expression(withdrawal_address) == [
        "withdrawal_credentials"
    ]
    assert _profile_contract_columns(profile) == [
        "withdrawal_credentials",
        "validator_index",
        "date",
        "amount",
    ]


def test_null_weight_relationship_is_partial_unknown_not_zero(fake_snapshot):
    from cerebro_mcp.semantic.graph_extraction import synthesize_search_documents

    relation = "int_test_nullable_edge_weight"
    fake_snapshot.models = {
        relation: _graph_model(
            relation,
            profile="nullable_weight_relation",
            module="bridges",
            source_column="user_address",
            target_column="bridge_contract",
            source_kind="address",
            target_kind="bridge",
            time_column="date",
            weight_column="volume_usd",
            status="candidate",
        )
    }
    profiles = tuple(graph_profiles.discover_profiles(models=fake_snapshot.models))
    fake_snapshot.graph_profiles = profiles
    fake_snapshot.profiles_by_id = {profile.profile: profile for profile in profiles}
    fake_snapshot.kind_to_profiles = graph_profiles.build_kind_index(profiles)
    fake_snapshot.graph_search_documents = tuple(
        synthesize_search_documents(profiles)
    )

    server = _server(StubCH(edge_rows={relation: [[AVATAR, SAFE, None, 4]]}))
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent[
        "view_id"
    ]
    result = _call_tool(
        server,
        "load_graph_atlas_sample",
        {
            "view_id": view_id,
            "profiles": ["nullable_weight_relation"],
            "sample_size": 10,
        },
    )

    assert result.isError is not True
    edge = result.structuredContent["datasets"]["atlas_edges"]["preview_rows"][0]
    assert edge[4] is None
    scope = result.structuredContent["view_state"]["atlas"]["scope"]
    assert scope["status"] == "partial"
    assert scope["sources"][0]["status"] == "partial"
    assert scope["coverage"]["rows"] == {"shown": 1, "total": 1}
    assert scope["verification"]["status"] == "verified"
    assert any("weight unknown" in warning for warning in scope["warnings"])

    sql, _ = graph_profiles.build_sample_sql(profiles[0], limit=10)
    assert "count(volume_usd) = 0" in sql
    assert "CAST(NULL AS Nullable(Float64))" in sql


def test_unsafe_deployed_bridge_profile_is_suppressed(fake_snapshot):
    relation = "int_execution_bridges_address_flows_daily"
    fake_snapshot.models = {
        relation: _graph_model(
            relation,
            profile="bridge_user_flows",
            module="bridges",
            source_column="user_address",
            target_column="bridge_contract",
            source_kind="address",
            target_kind="bridge",
            time_column="date",
            weight_column="volume_usd",
            status="candidate",
        )
    }

    assert graph_profiles.discover_profiles(models=fake_snapshot.models) == []
    assert graph_profiles.profile_by_id("bridge_user_flows") is None


def test_calculate_flow_efficiency_unknown_profile(fake_snapshot):
    res = _call_tool(
        _server(StubCH()),
        "calculate_flow_efficiency",
        {"profile": "nope", "node_ids": ["0xabc"]},
    )
    assert res["nodes"] == []
    assert any("unknown profile" in w for w in res["warnings"])


def test_graph_usage_analytics_records_tool_calls(fake_snapshot):
    from cerebro_mcp.semantic import graph_telemetry

    graph_telemetry.reset()
    server = _server(StubCH())
    _call_tool(server, "search_graph_catalog", {"query": "circles trust", "min_quality_tier": "all"})
    _call_tool(server, "search_graph_catalog", {"query": "pool", "min_quality_tier": "all"})
    res = _call_tool(server, "graph_usage_analytics", {})
    assert res["tool_calls"].get("search_graph_catalog") == 2
    assert res["total_calls"] >= 2
    # coverage gaps = registered kinds never explored (we issued no node_kind)
    assert isinstance(res["coverage_gaps"], list)
    assert "search_graph_catalog" in res["latency_ms"]
    graph_telemetry.reset()


def test_canonical_edge_id_and_undirected_merge():
    from cerebro_mcp.tools.semantic.graph_explorer import _canonical_edge_id, _merge_graph

    e1, _, _ = _canonical_edge_id("p", "B", "A", False)
    e2, _, _ = _canonical_edge_id("p", "A", "B", False)
    assert e1 == e2 == "p:A|B"
    # Undirected reciprocal edges collapse to one row with summed weight (Q6).
    _, edges = _merge_graph([], [], [], [
        {"id": "p:A->B", "source": "A", "target": "B", "profile": "p", "weight": 1.0, "edge_count": 1, "directed": False},
        {"id": "p:B->A", "source": "B", "target": "A", "profile": "p", "weight": 2.0, "edge_count": 3, "directed": False},
    ])
    assert len(edges) == 1
    assert edges[0][4] == 3.0 and edges[0][5] == 4


def test_merge_graph_directed_keeps_both_directions():
    from cerebro_mcp.tools.semantic.graph_explorer import _merge_graph

    _, edges = _merge_graph([], [], [], [
        {"id": "p:A->B", "source": "A", "target": "B", "profile": "p", "weight": 1.0, "edge_count": 1, "directed": True},
        {"id": "p:B->A", "source": "B", "target": "A", "profile": "p", "weight": 2.0, "edge_count": 1, "directed": True},
    ])
    assert len(edges) == 2


# ---------------------------------------------------------------------------
# Characterization goldens for explore_neighborhood (G1b BFS-unification gate)
#
# These pin the CURRENT observable contract — output dict shape, node/edge
# ordering, canonical edge collapse, truncation, defaults, and the per-hop
# query pattern (one batched query per profile with the WHOLE frontier).
# The unified bfs_expand implementation must pass them UNCHANGED.
# ---------------------------------------------------------------------------


class RecordingStubCH(StubCH):
    """StubCH that logs every neighbors-query (relation, seed_ids, lim)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[dict[str, Any]] = []

    def run_query(self, sql, database="dbt", requested_max_rows=100,
                  audience="tool", fetch_mode="auto", parameters=None):
        params = parameters or {}
        if "seed_ids" in params:
            relation = next(
                (t for t in self.edge_rows if t in sql),
                next((t for t in (
                    "api_execution_circles_v2_trust_relations_current",
                    "int_execution_safes_current_owners",
                    "int_execution_pools_dex_liquidity_events",
                ) if t in sql), "?"),
            )
            self.calls.append(
                {
                    "relation": relation,
                    "seed_ids": sorted(params["seed_ids"]),
                    "lim": params.get("lim"),
                }
            )
        return super().run_query(
            sql, database, requested_max_rows, audience, fetch_mode, parameters
        )


TRUST_TABLE = "api_execution_circles_v2_trust_relations_current"
SAFES_TABLE = "int_execution_safes_current_owners"
POOLS_TABLE = "int_execution_pools_dex_liquidity_events"


def test_characterization_explore_two_hops_full_output(fake_snapshot):
    """Golden: full output dict of a 2-hop walk over the whole catalog."""
    ch = RecordingStubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        }
    )
    server = _server(ch)
    result = _call_tool(
        server, "explore_neighborhood", {"seed_ids": [AVATAR], "hops": 2}
    )

    assert result == {
        "seed_ids": [AVATAR],
        "nodes": [
            # Seed first (kind stays "" — profile provenance merged in).
            {"id": AVATAR, "kind": "", "label": f"{AVATAR[:6]}…{AVATAR[-4:]}",
             "profiles": ["circles_trust"]},
            {"id": TRUSTEE, "kind": "circles_avatar",
             "label": f"{TRUSTEE[:6]}…{TRUSTEE[-4:]}", "profiles": ["circles_trust"]},
            {"id": OWNER, "kind": "address", "label": f"{OWNER[:6]}…{OWNER[-4:]}",
             "profiles": ["safe_ownership"]},
            {"id": SAFE, "kind": "safe", "label": f"{SAFE[:6]}…{SAFE[-4:]}",
             "profiles": ["safe_ownership"]},
        ],
        "edges": [
            {"id": f"circles_trust:{AVATAR}->{TRUSTEE}", "source": AVATAR,
             "target": TRUSTEE, "profile": "circles_trust", "weight": 1.0,
             "edge_count": 1, "directed": True},
            {"id": f"safe_ownership:{OWNER}->{SAFE}", "source": OWNER,
             "target": SAFE, "profile": "safe_ownership", "weight": 2.0,
             "edge_count": 2, "directed": True},
        ],
        "profiles_used": ["circles_trust", "safe_ownership"],
        "hops_requested": 2,
        "hops_completed": 2,
        "node_count": 4,
        "edge_count": 2,
        "truncated": False,
        "max_nodes": 250,
        "warnings": [],
    }


def test_characterization_explore_query_pattern(fake_snapshot):
    """Golden: ONE batched query per profile per hop with the WHOLE frontier
    (never per-node), and the caller's direction/limit pass straight through."""
    ch = RecordingStubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        }
    )
    server = _server(ch)
    _call_tool(
        server,
        "explore_neighborhood",
        {"seed_ids": [AVATAR], "hops": 2, "max_nodes": 50},
    )
    # Hop 1: 3 profiles x frontier [AVATAR]. Hop 2: 3 profiles x the whole
    # new frontier (TRUSTEE/OWNER/SAFE) — one call each, batched.
    assert len(ch.calls) == 6
    hop1, hop2 = ch.calls[:3], ch.calls[3:]
    assert all(c["seed_ids"] == [AVATAR] for c in hop1)
    expected_frontier = sorted([TRUSTEE, OWNER, SAFE])
    assert all(c["seed_ids"] == expected_frontier for c in hop2)
    assert all(c["lim"] == 50 for c in ch.calls)
    # Each hop touches every selected profile exactly once (order is the
    # catalog's discovery order — pin the SET, not the sequence).
    assert sorted(c["relation"] for c in hop1) == sorted(
        [TRUST_TABLE, SAFES_TABLE, POOLS_TABLE]
    )


def test_characterization_explore_truncation_and_flags(fake_snapshot):
    """Golden: max_nodes cap sets truncated=True and stops the walk."""
    ch = RecordingStubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        }
    )
    server = _server(ch)
    result = _call_tool(
        server,
        "explore_neighborhood",
        {"seed_ids": [AVATAR], "hops": 3, "max_nodes": 3},
    )
    assert result["truncated"] is True
    assert result["node_count"] == 3
    assert result["hops_completed"] == 1  # cap hit ends the walk after hop 1


def test_characterization_explore_profile_filter_and_unknown(fake_snapshot):
    """Golden: explicit profiles restrict the walk; unknown ids are skipped."""
    ch = RecordingStubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        }
    )
    server = _server(ch)
    result = _call_tool(
        server,
        "explore_neighborhood",
        {"seed_ids": [AVATAR], "profiles": ["circles_trust", "nope"], "hops": 1},
    )
    assert result["profiles_used"] == ["circles_trust"]
    assert {n["id"] for n in result["nodes"]} == {AVATAR, TRUSTEE}
    assert [c["relation"] for c in ch.calls] == [TRUST_TABLE]  # only the chosen profile


def test_characterization_explore_mcp_schema_defaults(fake_snapshot):
    """Golden: the registered MCP schema keeps the PUBLIC defaults
    (window_days=365, max_nodes=250, hops=1, direction='both')."""
    import asyncio

    server = _server(StubCH())
    tool = next(
        t for t in asyncio.run(server.list_tools()) if t.name == "explore_neighborhood"
    )
    props = tool.inputSchema["properties"]
    assert props["window_days"]["default"] == 365
    assert props["max_nodes"]["default"] == 250
    assert props["hops"]["default"] == 1
    assert props["direction"]["default"] == "both"
    assert tool.inputSchema.get("required") == ["seed_ids"]


# ---------------------------------------------------------------------------
# G1b: unified bfs_expand — kind-partitioned batching (expand mode)
# ---------------------------------------------------------------------------


def test_bfs_expand_kind_partitioned_batching(fake_snapshot):
    """Expand mode: ONE batched query per (kind group, compatible profile) —
    and unknown-kind entries query all chosen profiles."""
    from cerebro_mcp.tools.semantic.graph_explorer.traverse import bfs_expand

    ch = RecordingStubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        }
    )
    profiles = list(graph_profiles.discover_profiles())

    # Known kind: only kind-compatible profiles queried for that group.
    result = bfs_expand(
        ch,
        frontier=[(AVATAR, "circles_avatar")],
        chosen_profiles=profiles,
        auto_direction=True,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=25,
        node_cap=1000,
        per_hop_budget=100,
    )
    assert [c["relation"] for c in ch.calls] == [TRUST_TABLE]
    assert ch.calls[0]["seed_ids"] == [AVATAR]
    assert result.profiles_used == {"circles_trust"}
    assert {n for n in result.nodes} == {AVATAR, TRUSTEE}

    # Unknown kind: ALL chosen profiles queried for the "" group.
    ch2 = RecordingStubCH(edge_rows={TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]]})
    bfs_expand(
        ch2,
        frontier=[("0x0000000000000000000000000000000000000abc", "")],
        chosen_profiles=profiles,
        auto_direction=True,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=25,
        node_cap=1000,
        per_hop_budget=100,
    )
    assert sorted(c["relation"] for c in ch2.calls) == sorted(
        [TRUST_TABLE, SAFES_TABLE, POOLS_TABLE]
    )


def test_bfs_expand_batches_same_kind_frontier(fake_snapshot):
    """Two same-kind frontier nodes land in ONE query (the old expand issued
    one query per node)."""
    from cerebro_mcp.tools.semantic.graph_explorer.traverse import bfs_expand

    ch = RecordingStubCH(edge_rows={TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]]})
    profiles = list(graph_profiles.discover_profiles())
    bfs_expand(
        ch,
        frontier=[(AVATAR, "circles_avatar"), (TRUSTEE, "circles_avatar")],
        chosen_profiles=profiles,
        auto_direction=True,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=25,
        node_cap=1000,
        per_hop_budget=100,
    )
    trust_calls = [c for c in ch.calls if c["relation"] == TRUST_TABLE]
    assert len(trust_calls) == 1
    assert trust_calls[0]["seed_ids"] == sorted([AVATAR, TRUSTEE])


def test_bfs_expand_budget_truncation(fake_snapshot):
    """Budget mode: hitting the per-hop budget with frontier remaining sets
    truncated_at_hop (drives the user-facing cap warning)."""
    from cerebro_mcp.tools.semantic.graph_explorer.traverse import bfs_expand

    # Many neighbours from one profile.
    rows = [[AVATAR, f"0x{i:040x}", 1.0, 1] for i in range(1, 30)]
    ch = StubCH(edge_rows={TRUST_TABLE: rows})
    profiles = list(graph_profiles.discover_profiles())
    result = bfs_expand(
        ch,
        frontier=[(AVATAR, "circles_avatar")],
        chosen_profiles=profiles,
        auto_direction=True,
        kind_partition=True,
        hops=3,
        window_days=90,
        per_query_limit=100,
        node_cap=1000,
        per_hop_budget=5,
    )
    assert result.truncated_at_hop == 1
    assert result.truncated is True
    # Progress was still made before declaring truncation.
    assert len(result.nodes) >= 5


def test_bfs_expand_seeds_from_existing_graph(fake_snapshot):
    """initial_nodes/initial_edges pre-seed the accumulators (the UI expand
    path feeds the datasets already on canvas) — undirected reciprocals sum
    into pre-existing canonical rows."""
    from cerebro_mcp.tools.semantic.graph_explorer.traverse import bfs_expand

    ch = StubCH(edge_rows={TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]]})
    profiles = list(graph_profiles.discover_profiles())
    existing_nodes = {
        AVATAR: {"id": AVATAR, "kind": "circles_avatar", "label": "a", "profiles": []}
    }
    result = bfs_expand(
        ch,
        frontier=[(AVATAR, "circles_avatar")],
        chosen_profiles=profiles,
        auto_direction=True,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=25,
        node_cap=1000,
        per_hop_budget=100,
        initial_nodes=existing_nodes,
    )
    # Existing node kept; its profile provenance merged in.
    assert result.nodes[AVATAR]["profiles"] == ["circles_trust"]
    assert TRUSTEE in result.nodes


# ---------------------------------------------------------------------------
# view_state v2: modes, limits, atlas sampling, bulk view patch
# ---------------------------------------------------------------------------


def test_open_emits_v2_state_and_limits(fake_snapshot):
    server = _server(StubCH())
    result = _call_tool(server, "open_graph_explorer", {})
    vs = result.structuredContent["view_state"]
    assert vs["mode"] == "atlas"
    assert vs["limits"]["max_hops"] == 50
    assert vs["limits"]["default_expand_depth"] == 1
    assert vs["limits"]["ui_default_window_days"] == 90
    assert vs["limits"]["ui_default_max_neighbors"] == 100
    assert vs["selection"] == {"node_id": "", "edge_id": "", "request_id": 0}
    assert "dataset_revisions" in vs
    # Both dataset pairs exist from the start.
    assert {
        "nodes",
        "edges",
        "atlas_nodes",
        "atlas_edges",
        "atlas_preview_nodes",
        "atlas_preview_edges",
    } <= set(
        result.structuredContent["datasets"]
    )


def test_atlas_sample_replace_semantics(fake_snapshot):
    """Deselecting a profile and re-requesting leaves NO stale edges."""
    ch = StubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        }
    )
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]

    both = _call_tool(
        server,
        "load_graph_atlas_sample",
        {
            "view_id": view_id,
            "profiles": ["circles_trust", "safe_ownership"],
            "request_id": 4,
        },
    )
    sc = both.structuredContent
    assert sc["view_state"]["mode"] == "atlas"
    assert sc["view_state"]["atlas"]["selected_profiles"] == [
        "circles_trust", "safe_ownership",
    ]
    assert len(sc["datasets"]["atlas_edges"]["preview_rows"]) == 2
    scope = sc["view_state"]["atlas"]["scope"]
    assert scope["request_id"] == 4
    assert scope["status"] == "ready"
    for key in ("atlas_nodes", "atlas_edges"):
        assert sc["view_state"]["dataset_scopes"][key] == scope["scope_id"]
        assert sc["datasets"][key]["scope_id"] == scope["scope_id"]

    # REPLACE: re-request with only one profile -> the other's edges gone.
    one = _call_tool(
        server,
        "load_graph_atlas_sample",
        {"view_id": view_id, "profiles": ["circles_trust"]},
    )
    rows = one.structuredContent["datasets"]["atlas_edges"]["preview_rows"]
    assert len(rows) == 1
    assert rows[0][3] == "circles_trust"

    unknown = _call_tool(
        server, "load_graph_atlas_sample", {"view_id": view_id, "profiles": ["nope"]}
    )
    assert unknown.isError


def test_atlas_preview_is_real_but_does_not_change_applied_selection(fake_snapshot):
    ch = StubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        }
    )
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent[
        "view_id"
    ]
    _call_tool(
        server,
        "load_graph_atlas_sample",
        {"view_id": view_id, "profiles": ["safe_ownership"]},
    )

    result = _call_tool(
        server,
        "load_graph_atlas_preview",
        {
            "view_id": view_id,
            "profile": "circles_trust",
            "sample_size": 25,
            "request_id": 9,
        },
    )
    sc = result.structuredContent
    assert sc["view_state"]["atlas"]["selected_profiles"] == ["safe_ownership"]
    assert sc["view_state"]["atlas_preview"]["profile"] == "circles_trust"
    assert sc["datasets"]["atlas_edges"]["preview_rows"][0][3] == "safe_ownership"
    assert sc["datasets"]["atlas_preview_edges"]["preview_rows"][0][3] == (
        "circles_trust"
    )
    scope = sc["view_state"]["atlas_preview"]["scope"]
    assert scope["request_id"] == 9
    assert scope["sources"][0]["name"] == f"dbt.{TRUST_TABLE}"
    for key in ("atlas_preview_nodes", "atlas_preview_edges"):
        assert sc["view_state"]["dataset_scopes"][key] == scope["scope_id"]
        assert sc["datasets"][key]["provenance"]["scope_id"] == scope["scope_id"]


def test_investigate_load_preserves_atlas_datasets(fake_snapshot):
    ch = StubCH(
        edge_rows={
            TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]],
            SAFES_TABLE: [[OWNER, SAFE, 2.0, 2]],
        },
        roles={AVATAR: [0, 0, 0, "", 1, "human", 0, 0, 0, "", 0, 0, 0, 0, ""]},
    )
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]
    _call_tool(
        server,
        "load_graph_atlas_sample",
        {"view_id": view_id, "profiles": ["safe_ownership"]},
    )
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": AVATAR},
    )
    sc = result.structuredContent
    # Direct seed load is a data load — it doesn't flip mode (stays atlas).
    assert sc["view_state"]["mode"] == "atlas"
    # Atlas datasets survive the investigate load (per-key attach).
    assert len(sc["datasets"]["atlas_edges"]["preview_rows"]) == 1
    assert len(sc["datasets"]["edges"]["preview_rows"]) == 1
    # Revisions cover both pairs.
    revs = sc["view_state"]["dataset_revisions"]
    assert revs["atlas_edges"] >= 1 and revs["edges"] >= 1


def test_legacy_sample_mode_lands_in_atlas(fake_snapshot):
    """The agent-compat no-seed + seed_model form now loads the atlas pair."""
    ch = StubCH(edge_rows={TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]]})
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": "", "seed_model": "circles_trust"},
    )
    sc = result.structuredContent
    assert sc["view_state"]["mode"] == "atlas"
    assert len(sc["datasets"]["atlas_edges"]["preview_rows"]) == 1


def test_set_graph_explorer_view_schema(fake_snapshot):
    server = _server(StubCH())
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]
    set_fn = _call_tool

    ok = set_fn(
        server,
        "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"layout": "circular",
                                        "investigate": {"window_days": 30}}},
    )
    assert not ok.isError
    assert ok.structuredContent["patch"]["view_state"]["layout"] == "circular"

    bad_key = set_fn(
        server, "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"hops": 3}},
    )
    assert bad_key.isError and "Unknown view-state key" in bad_key.content[0].text

    bad_nested = set_fn(
        server, "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"investigate": {"seed": {}}}},
    )
    assert bad_nested.isError

    bad_mode = set_fn(
        server, "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"mode": "banana"}},
    )
    assert bad_mode.isError
    # The error enumerates all four accepted modes.
    assert "timeline" in bad_mode.content[0].text and "flows" in bad_mode.content[0].text

    # Mode switch clears selection AND bumps mode_revision (authorizes the
    # client to adopt the new mode over later data-load adoptions).
    _call_tool(
        server, "update_graph_explorer_focus",
        {"view_id": view_id, "selected_node_id": AVATAR},
    )
    switched = set_fn(
        server, "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"mode": "investigate"}},
    )
    switched_vs = switched.structuredContent["patch"]["view_state"]
    assert switched_vs["selection"] == {
        "node_id": "", "edge_id": "", "request_id": 1,
    }
    assert switched_vs["mode_revision"] >= 1
    # timeline + flows are accepted modes.
    for m in ("timeline", "flows"):
        ok = set_fn(
            server, "set_graph_explorer_view",
            {"view_id": view_id, "patch": {"mode": m}},
        )
        assert ok.isError is not True


def test_mode_switch_via_focus_clears_selection(fake_snapshot):
    server = _server(StubCH())
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]
    _call_tool(
        server, "update_graph_explorer_focus",
        {"view_id": view_id, "selected_node_id": AVATAR},
    )
    result = _call_tool(
        server, "update_graph_explorer_focus",
        {"view_id": view_id, "mode": "investigate"},
    )
    vs_patch = result.structuredContent["patch"]["view_state"]
    assert vs_patch["mode"] == "investigate"
    assert vs_patch["selection"] == {
        "node_id": "", "edge_id": "", "request_id": 1,
    }
    assert vs_patch["mode_revision"] >= 1  # explicit mode command bumps it

    # An invalid mode is rejected and the message enumerates all four modes.
    bad = _call_tool(
        server, "update_graph_explorer_focus",
        {"view_id": view_id, "mode": "banana"},
    )
    assert bad.isError
    assert "flows" in bad.content[0].text and "timeline" in bad.content[0].text


def test_mode_switch_and_target_selection_publish_as_one_focus_revision(
    fake_snapshot,
):
    role_row = [
        True, False, False, "", False, "", False, False, False, "",
        False, False, False, False, "",
    ]
    server = _server(StubCH(roles={AVATAR: role_row}))
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent[
        "view_id"
    ]

    result = _call_tool(
        server,
        "update_graph_explorer_focus",
        {
            "view_id": view_id,
            "mode": "investigate",
            "selected_node_id": AVATAR,
            "request_id": 31,
        },
    )

    assert result.isError is not True
    patch = result.structuredContent["patch"]
    state = patch["view_state"]
    assert state["mode"] == "investigate"
    assert state["mode_revision"] >= 1
    assert state["selection"] == {
        "node_id": AVATAR,
        "edge_id": "",
        "request_id": 31,
    }
    node_rows = patch["datasets"]["node_evidence"]["preview_rows"]
    assert node_rows
    assert all(row[0] == AVATAR and row[3:] == ["node", 31] for row in node_rows)
    assert patch["datasets"]["edge_evidence"]["preview_rows"] == []
    assert state["focus_scope"]["request_id"] == 31


def test_new_graph_app_tools_hidden_from_model(fake_snapshot):
    import asyncio as _asyncio

    from cerebro_mcp.tools.visualization import web_apps as _web_apps

    server = FastMCP("graph-vis-test")
    mini_apps.register_mini_app_infra(server, None)
    register_graph_explorer_tools(server, StubCH())
    names = [t.name for t in _asyncio.run(server.list_tools())]
    assert "load_graph_atlas_sample" not in names
    assert "load_graph_atlas_preview" not in names
    assert "set_graph_explorer_view" not in names
    assert "open_graph_explorer" in names
    cfg = _web_apps.WEB_APP_CONFIGS["graph_explorer"]
    assert {
        "load_graph_atlas_sample",
        "load_graph_atlas_preview",
        "set_graph_explorer_view",
    } <= cfg.allowed_tools


def test_seed_normalizes_checksummed_address(fake_snapshot):
    """A checksummed (mixed-case) seed must match lowercase on-chain data —
    regression for 0x295bA5c… returning 1 node / 0 edges."""
    ch = RecordingStubCH(edge_rows={TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]]})
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]
    checksummed = AVATAR[:2] + AVATAR[2:].upper()
    result = _call_tool(
        server,
        "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": checksummed,
         "relation_types": ["circles_trust"]},
    )
    assert not result.isError
    sc = result.structuredContent
    # seed stored lowercase + the SQL received the lowercase id
    assert sc["view_state"]["investigate"]["seed"]["id"] == AVATAR
    assert all(c["seed_ids"] == [AVATAR] for c in ch.calls)
    assert len(sc["datasets"]["edges"]["preview_rows"]) == 1


def test_expand_normalizes_checksummed_node(fake_snapshot):
    ch = RecordingStubCH(edge_rows={TRUST_TABLE: [[AVATAR, TRUSTEE, 1.0, 1]]})
    server = _server(ch)
    view_id = _call_tool(server, "open_graph_explorer", {}).structuredContent["view_id"]
    _call_tool(
        server, "load_graph_explorer_seed",
        {"view_id": view_id, "seed_node_id": AVATAR,
         "relation_types": ["circles_trust"]},
    )
    ch.calls.clear()
    checksummed = TRUSTEE[:2] + TRUSTEE[2:].upper()
    result = _call_tool(
        server, "expand_graph_explorer_node",
        {"view_id": view_id, "node_id": checksummed},
    )
    assert not result.isError
    assert all(c["seed_ids"] == [TRUSTEE] for c in ch.calls if c["seed_ids"])


def test_budget_truncated_round_consumes_only_fetched_groups(fake_snapshot):
    """When the per-hop budget stops a frontier round mid-way, the SKIPPED
    groups must not be reported as expanded — otherwise the next Expand
    strands them forever (the 'Expand again to continue' promise)."""
    from cerebro_mcp.tools.semantic.graph_explorer.traverse import bfs_expand

    def fake_fetch(ch, profile, *, seed_ids, direction, window_days, limit):
        nodes, edges = [], []
        for sid in seed_ids:
            nid = f"{sid}_n"
            nodes.append({"id": nid, "kind": "address", "label": "",
                          "profiles": [profile.profile]})
            edges.append({"id": f"{profile.profile}:{sid}->{nid}", "source": sid,
                          "target": nid, "profile": profile.profile,
                          "weight": 1.0, "edge_count": 1, "directed": True})
        return nodes, edges, []

    profs = {p.profile: p for p in fake_snapshot.graph_profiles}
    res = bfs_expand(
        None,
        frontier=[("0xa1", "circles_avatar"), ("0xs1", "address")],
        chosen_profiles=[profs["circles_trust"], profs["safe_ownership"]],
        direction="both",
        auto_direction=True,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=10,
        node_cap=100,
        per_hop_budget=1,
        fetch=fake_fetch,
    )
    assert res.truncated_at_hop == 1
    # Only the first kind group's fetch ran before the budget tripped; the
    # second group stays expandable for the next round.
    assert res.expanded_frontier == {"0xa1"}


# ---------------------------------------------------------------------------
# Role → profile inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roles",
    [
        {"has_dune_label": 1, "dune_project": "ERC20"},
        {"is_lp_provider": 1},
        {"is_pool": 1},
        {"is_lending_user": 1},
        {"is_circles_avatar": 1},
        {"is_validator_depositor": 1},
        {"is_gpay_wallet": 1},
        {},
    ],
)
def test_token_transfers_always_offered(roles):
    """Value movement is the universal forensic baseline.

    Regression: a role match used to SUPPRESS the token_transfers fallback, so
    a labelled address (has_dune_label -> address_labeled_as, a time-less label
    relation) resolved to the label edge alone and rendered an empty graph —
    observed on the aGnoEURe reserve, which had ~49.5k transfers across ~4k
    counterparties inside the default 90d window. Labelled addresses are
    exchanges, tokens and protocols: exactly where an investigation begins.
    """
    assert "token_transfers" in graph_profiles.profiles_for_address_roles(roles)


def test_role_profiles_supplement_rather_than_replace_transfers():
    selected = graph_profiles.profiles_for_address_roles(
        {"has_dune_label": 1, "is_safe": 1}
    )
    assert "token_transfers" in selected
    assert "address_labeled_as" in selected
    assert "safe_ownership" in selected
    # No duplicates: is_safe already names token_transfers explicitly.
    assert len(selected) == len(set(selected))


# ---------------------------------------------------------------------------
# SQL determinism + evidence scoping
# ---------------------------------------------------------------------------


def _a_profile():
    """A time-aware profile for SQL-shape assertions (registry is not loaded
    under pytest, so build one directly)."""
    from cerebro_mcp.semantic.graph_profiles import GraphProfile

    return GraphProfile(
        profile="p", model_name="m", relation_name="m",
        source_column="a", target_column="b",
        source_kind="address", target_kind="address",
        weight_column="w", time_column="block_timestamp",
    )


def test_neighborhood_and_sample_sql_have_a_total_order():
    """`ORDER BY weight DESC` alone is a PARTIAL order.

    Profiles whose weight is constant (one row per pair) are entirely tied, so
    ClickHouse could return any subset for the LIMIT. Two identical Atlas calls
    returned completely disjoint 50-edge sets (0 of 50 shared) before the
    endpoint tiebreaker was added.
    """
    prof = _a_profile()
    neigh, _ = graph_profiles.build_neighbors_sql(
        prof, seed_ids=["0xa1"], direction="out", window_days=90, limit=50
    )
    sample, _ = graph_profiles.build_sample_sql(prof, limit=50, window_days=90)
    for sql in (neigh, sample):
        assert "ORDER BY weight DESC, source_id, target_id" in sql


def test_evidence_sql_is_ordered_and_window_bound():
    """The drill-down must describe the EDGE it hangs off.

    With no time predicate, an edge aggregated over 90 days returned rows from
    years earlier; with no ORDER BY, the same selection returned different rows
    each call (17 distinct sets over 20 calls).
    """
    prof = _a_profile()
    sql, params = graph_profiles.build_evidence_sql(
        prof, source_id="0xa1", target_id="0xb2", limit=25, window_days=90
    )
    assert "ORDER BY" in sql
    assert "tuple(*)" in sql
    assert "block_timestamp" in sql
    assert params["win"] == 90

    # Omitting the window must not silently drop the ordering.
    unbound_sql, unbound_params = graph_profiles.build_evidence_sql(
        prof, source_id="0xa1", target_id="0xb2", limit=25
    )
    assert "ORDER BY" in unbound_sql
    assert "win" not in unbound_params
