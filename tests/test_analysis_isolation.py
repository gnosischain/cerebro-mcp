"""Analysis-handle registry + SessionState proxy isolation (R10 §4.1/P0-9).

The property under test is the INVERSE of what the old visualization tests
asserted: not "tests are isolated from each other", but "two concurrent
OWNERS are isolated from each other" — the missing coverage the audits
called out three times.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from mcp import types

from cerebro_mcp.config import settings
from cerebro_mcp.runtime import analysis_registry as registry
from cerebro_mcp.runtime.identity import (
    reset_current_owner,
    set_current_owner_prehashed,
)
from cerebro_mcp.runtime.mcp_server import CerebroFastMCP
from cerebro_mcp.tools import tool_policy
from cerebro_mcp.tools.governance import session_state


@pytest.fixture(autouse=True)
def _clean():
    registry.reset_registry_for_tests()
    yield
    registry.reset_registry_for_tests()


@pytest.fixture
def connector_profile(monkeypatch):
    monkeypatch.setattr(
        settings, "MCP_SURFACE_PROFILE", tool_policy.PROFILE_TEAM_ANALYTICS_V1
    )


# ---------------------------------------------------------------------------
# Registry semantics
# ---------------------------------------------------------------------------


def test_mint_reuse_and_owner_binding():
    h, reused = registry.mint_or_reuse("v1:alice", None)
    assert not reused and len(h) == 32
    h2, reused2 = registry.mint_or_reuse("v1:alice", h)
    assert reused2 and h2 == h
    # possession is not authentication: bob cannot present alice's handle
    with pytest.raises(registry.AnalysisHandleError, match="unknown"):
        registry.mint_or_reuse("v1:bob", h)
    with pytest.raises(registry.AnalysisHandleError):
        registry.acquire("v1:bob", h)


def test_expired_handle_rejected_not_reminted():
    h, _ = registry.mint_or_reuse("v1:alice", None)
    cycle = registry._cycles[("v1:alice", h)]
    cycle.last_used = time.time() - registry.IDLE_EXPIRY_S - 1
    with pytest.raises(registry.AnalysisHandleError, match="expired"):
        registry.mint_or_reuse("v1:alice", h)
    # reject-don't-mint: the id did NOT silently become a fresh cycle
    assert ("v1:alice", h) not in registry._cycles


def test_capacity_evicts_only_idle_and_errors_when_all_active():
    handles = [registry.mint_or_reuse("v1:alice", None)[0] for _ in range(8)]
    for h in handles:
        registry.acquire("v1:alice", h)  # all ACTIVE (refcount 1)
    with pytest.raises(registry.AnalysisCapacityError, match="active"):
        registry.mint_or_reuse("v1:alice", None)
    # releasing one makes it the idle victim; minting now succeeds
    registry.release("v1:alice", handles[0])
    h_new, _ = registry.mint_or_reuse("v1:alice", None)
    assert ("v1:alice", handles[0]) not in registry._cycles
    assert ("v1:alice", h_new) in registry._cycles


def test_release_in_finally_even_on_error():
    h, _ = registry.mint_or_reuse("v1:alice", None)
    registry.acquire("v1:alice", h)
    try:
        raise RuntimeError("tool blew up")
    except RuntimeError:
        pass
    finally:
        registry.release("v1:alice", h)
    assert registry._cycles[("v1:alice", h)].refcount == 0


# ---------------------------------------------------------------------------
# The proxy: two-owner SessionState isolation
# ---------------------------------------------------------------------------


def _in_cycle(owner: str, handle: str, fn):
    otoken = set_current_owner_prehashed(owner)
    htoken = registry.set_current_handle(handle)
    try:
        return fn()
    finally:
        registry.reset_current_handle(htoken)
        reset_current_owner(otoken)


def test_two_owners_do_not_share_session_state(connector_profile):
    """begin_analysis_cycle for alice must not clear bob's discovery —
    the exact production incident recorded at charts.py:1078-1098."""
    ha, _ = registry.mint_or_reuse("v1:alice", None)
    hb, _ = registry.mint_or_reuse("v1:bob", None)

    _in_cycle("v1:bob", hb, lambda: session_state.state.discovered_models.update(
        {"fct_bob_model"}
    ))
    _in_cycle("v1:alice", ha, session_state.state.begin_analysis_cycle)

    bob_models = _in_cycle(
        "v1:bob", hb, lambda: set(session_state.state.discovered_models)
    )
    assert bob_models == {"fct_bob_model"}, (
        "alice's begin_analysis_cycle cleared bob's discovery — cross-user "
        "clobber is back"
    )


def test_proxy_falls_back_to_singleton_off_profile():
    session_state.state.discovered_models.add("legacy_model")
    assert "legacy_model" in session_state._default_state.discovered_models
    session_state._default_state.discovered_models.discard("legacy_model")


def test_proxy_setattr_delegates(connector_profile):
    h, _ = registry.mint_or_reuse("v1:alice", None)

    def _set():
        session_state.state.generate_chart_count = 7
        return session_state.state.generate_chart_count

    assert _in_cycle("v1:alice", h, _set) == 7
    # the singleton was untouched
    assert session_state._default_state.generate_chart_count != 7


# ---------------------------------------------------------------------------
# Wire: schema injection + handle enforcement through call_tool
# ---------------------------------------------------------------------------


def _server() -> CerebroFastMCP:
    server = CerebroFastMCP("handles-test")

    @server.tool()
    def find(query: str) -> dict:
        return {"route": "ok"}

    @server.tool()
    def execute_query(sql: str) -> str:
        return "rows"

    @server.tool()
    def list_reports() -> str:
        return "reports"

    return server


def _wire_tools(server) -> dict[str, types.Tool]:
    req = types.ListToolsRequest(method="tools/list")
    handler = server._mcp_server.request_handlers[types.ListToolsRequest]
    result = asyncio.run(handler(req))
    return {t.name: t for t in result.root.tools}


def _wire_call(server, name, arguments) -> types.CallToolResult:
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    handler = server._mcp_server.request_handlers[types.CallToolRequest]
    return asyncio.run(handler(req)).root


def test_schema_injection_matches_handle_rule(connector_profile):
    tools = _wire_tools(_server())
    # MINTS: optional analysis_id
    find_schema = tools["find"].inputSchema
    assert "analysis_id" in find_schema["properties"]
    assert "analysis_id" not in (find_schema.get("required") or [])
    # REQUIRED: required analysis_id
    eq_schema = tools["execute_query"].inputSchema
    assert "analysis_id" in eq_schema["properties"]
    assert "analysis_id" in eq_schema["required"]
    # NONE (durable owner-keyed): untouched
    assert "analysis_id" not in (
        tools["list_reports"].inputSchema.get("properties") or {}
    )


def test_mint_annotate_require_roundtrip(connector_profile):
    server = _server()
    otoken = set_current_owner_prehashed("v1:alice")
    try:
        # REQUIRED without a handle: rejected with guidance
        r = _wire_call(server, "execute_query", {"sql": "SELECT 1"})
        assert r.isError
        assert "find or preflight" in r.content[0].text

        # MINTS returns the id in the result
        r = _wire_call(server, "find", {"query": "tvl"})
        assert not r.isError
        handle = (r.structuredContent or {}).get("analysis_id")
        if handle is None:  # annotated as a trailing text block otherwise
            handle = next(
                c.text.split(":", 1)[1].strip()
                for c in r.content
                if getattr(c, "text", "").startswith("analysis_id:")
            )
        assert len(handle) == 32

        # REQUIRED with the minted handle: runs, handle stripped from args
        r = _wire_call(
            server, "execute_query", {"sql": "SELECT 1", "analysis_id": handle}
        )
        assert not r.isError

        # reuse does NOT mint a second cycle
        before = registry.registry_size()
        r = _wire_call(server, "find", {"query": "tvl", "analysis_id": handle})
        assert not r.isError
        assert registry.registry_size() == before

        # list_reports (Handle.NONE) works with no handle at all
        assert not _wire_call(server, "list_reports", {}).isError
    finally:
        reset_current_owner(otoken)


def test_annotate_preserves_sdk_return_shapes():
    """The SDK dispatches on the RETURN TYPE of call_tool (2-tuple ->
    structured+unstructured, dict -> structured-only, iterable -> content).
    Annotating must stay INSIDE the shape.

    Regression: an earlier version did `list(result) + [TextContent(...)]`,
    which flattened the 2-tuple into a 3-element list — the structured half
    was lost and every outputSchema-bearing tool (find included) failed with
    "outputSchema defined but no structured output returned".
    """
    from mcp.types import TextContent

    from cerebro_mcp.runtime.mcp_server import CerebroFastMCP

    ann = CerebroFastMCP._annotate_analysis_id

    # 2-tuple: shape preserved, structured half annotated, content untouched
    content = [TextContent(type="text", text="body")]
    out = ann((content, {"route": "semantic"}), "h" * 32)
    assert isinstance(out, tuple) and len(out) == 2
    assert out[0] is content
    assert out[1] == {"route": "semantic", "analysis_id": "h" * 32}

    # dict: still a dict (NOT wrapped in a list)
    out = ann({"route": "raw"}, "h" * 32)
    assert isinstance(out, dict)
    assert out["analysis_id"] == "h" * 32

    # bare content list: id appended as a text block
    out = ann(content, "h" * 32)
    assert isinstance(out, list) and len(out) == 2
    assert "analysis_id" in out[-1].text


def test_off_profile_behavior_unchanged():
    server = _server()
    tools = _wire_tools(server)
    assert "analysis_id" not in (
        tools["execute_query"].inputSchema.get("properties") or {}
    )
    assert not _wire_call(server, "execute_query", {"sql": "SELECT 1"}).isError
