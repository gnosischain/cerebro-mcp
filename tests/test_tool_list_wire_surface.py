"""Wire-surface tests: what `tools/list` and `tools/call` ACTUALLY serve.

These tests drive the low-level request handlers
(``mcp._mcp_server.request_handlers[types.ListToolsRequest]``) — the path a
real client hits — never the ``mcp.list_tools`` instance attribute.

Why that distinction is load-bearing: FastMCP binds ``self.list_tools`` into
the low-level handler eagerly in ``__init__`` (``_setup_handlers``), so a
later ``mcp.list_tools = wrapper`` assignment changes only the attribute and
never the wire. The original ``install_app_only_filter`` did exactly that —
it passed every test that called the attribute while all 187 tools (including
the 27 app-only hydration tools) shipped to every real client. See lesson
``wire-handler-binds-at-init`` in ``src/cerebro_mcp/prompts/lessons/``.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp import types
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.tools.visualization import mini_apps


def _wire_tool_names(mcp) -> set[str]:
    """Tool names as served by the LOW-LEVEL tools/list handler (the wire)."""
    request = types.ListToolsRequest(method="tools/list")
    handler = mcp._mcp_server.request_handlers[types.ListToolsRequest]
    result = asyncio.run(handler(request))
    return {t.name for t in result.root.tools}


def _wire_call(mcp, name: str, arguments: dict) -> types.CallToolResult:
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    return asyncio.run(handler(request)).root


def _build_server() -> FastMCP:
    """A server the way cerebro builds one: subclass + app-only marking."""
    from cerebro_mcp.runtime.mcp_server import CerebroFastMCP

    server = CerebroFastMCP("wire-surface-test")

    @server.tool()
    def visible_tool() -> str:
        return "ok"

    @server.tool(meta=mini_apps.APP_ONLY_META)
    def hidden_hydration_tool() -> str:
        return "app-only"

    mini_apps.install_app_only_filter(server)
    mini_apps.mark_app_only("hidden_hydration_tool")
    return server


def test_app_only_tools_absent_from_wire():
    """The app-only drop must reach the WIRE, not just the attribute."""
    server = _build_server()

    # The attribute path (what the old tests asserted) — kept for contrast.
    attr_names = {t.name for t in asyncio.run(server.list_tools())}
    assert "hidden_hydration_tool" not in attr_names

    # The wire path — this is what a real client receives.
    wire_names = _wire_tool_names(server)
    assert "hidden_hydration_tool" not in wire_names, (
        "app-only tool leaked onto the wire: the low-level tools/list "
        "handler is not seeing the visibility filter"
    )
    assert "visible_tool" in wire_names


def test_app_only_tools_stay_callable():
    """App-only hides from listing only — the ext-apps callTool path stays."""
    server = _build_server()
    result = _wire_call(server, "hidden_hydration_tool", {})
    assert not result.isError


# ---------------------------------------------------------------------------
# Connector profile (team_analytics_v1)
# ---------------------------------------------------------------------------


from cerebro_mcp.config import settings  # noqa: E402
from cerebro_mcp.tools import tool_policy  # noqa: E402


@pytest.fixture
def connector_profile(monkeypatch):
    monkeypatch.setattr(
        settings, "MCP_SURFACE_PROFILE", tool_policy.PROFILE_TEAM_ANALYTICS_V1
    )


def _profile_server() -> FastMCP:
    from cerebro_mcp.runtime.mcp_server import CerebroFastMCP

    server = CerebroFastMCP("profile-test")

    @server.tool()
    def execute_query(sql: str, research_project_id: str = "", persist_result: bool = False) -> str:
        return "rows"

    @server.tool()
    def verify_numbers(claims_json: str = "") -> str:
        return "verified"

    @server.tool()
    def rpc_scan_logs() -> str:  # NOT in the profile
        return "scan"

    return server


def test_profile_hides_excluded_from_wire(connector_profile):
    server = _profile_server()
    wire = _wire_tool_names(server)
    assert "execute_query" in wire
    assert "rpc_scan_logs" not in wire, (
        "a tool outside TOOL_POLICY leaked onto the wire under the "
        "connector profile"
    )


def test_profile_blocks_excluded_invocation(connector_profile):
    """Hiding a name is not a capability boundary — call must fail too."""
    server = _profile_server()
    result = _wire_call(server, "rpc_scan_logs", {})
    assert result.isError, (
        "excluded tool was CALLABLE under the connector profile: listing "
        "and invocation must be enforced separately"
    )


def test_profile_denies_argument_values_not_keys(connector_profile):
    # truthy values denied
    with pytest.raises(tool_policy.ToolPolicyViolation):
        tool_policy.check_call_allowed(
            "execute_query", {"sql": "SELECT 1", "research_project_id": "p1"}
        )
    with pytest.raises(tool_policy.ToolPolicyViolation):
        tool_policy.check_call_allowed(
            "query_metrics", {"metrics": ["x"], "allow_candidate": True}
        )
    with pytest.raises(tool_policy.ToolPolicyViolation):
        tool_policy.check_call_allowed(
            "explain_metric_query", {"metrics": ["x"], "allow_candidate": True}
        )
    # safe values pass (value-level rules, not key presence)
    tool_policy.check_call_allowed(
        "execute_query",
        {"sql": "SELECT 1", "research_project_id": "", "persist_result": False},
    )
    tool_policy.check_call_allowed(
        "query_metrics", {"metrics": ["x"], "allow_candidate": False}
    )


def test_profile_rejects_nested_check_query(connector_profile):
    """`check_query` rides INSIDE claims_json — key filtering can't see it."""
    claims = '[{"claim": "tvl is 5", "check_query": "SELECT 1"}]'
    with pytest.raises(tool_policy.ToolPolicyViolation):
        tool_policy.check_call_allowed("verify_numbers", {"claims_json": claims})
    # arithmetic-only claims pass
    tool_policy.check_call_allowed(
        "verify_numbers", {"claims_json": '[{"claim": "2+2=4"}]'}
    )
    # malformed JSON passes through — the tool itself reports the parse error
    tool_policy.check_call_allowed("verify_numbers", {"claims_json": "{not json"})


def test_profile_off_is_a_noop():
    assert tool_policy.tool_visible("rpc_scan_logs")
    tool_policy.check_call_allowed(
        "query_metrics", {"metrics": ["x"], "allow_candidate": True}
    )


def test_exact_surface_assertion_fires_on_missing_tool(connector_profile):
    registered = set(tool_policy.CONNECTOR_TOOL_NAMES)
    tool_policy.assert_exact_surface(registered)  # all 44 -> ok
    registered.discard("query_metrics")
    with pytest.raises(RuntimeError, match="query_metrics"):
        tool_policy.assert_exact_surface(registered)


def test_policy_table_is_exactly_44():
    assert set(tool_policy.TOOL_POLICY) == tool_policy.CONNECTOR_TOOL_NAMES
    assert len(tool_policy.TOOL_POLICY) == 44


def test_non_tool_surface_frozen(connector_profile):
    assert tool_policy.resource_uri_allowed("gnosis://platform-overview")
    assert tool_policy.resource_uri_allowed("gnosis://dbt-modules/bridges")
    assert tool_policy.resource_uri_allowed("gnosis://semantic-model/fct_x")
    # dropped template, ui:// contracts, and unknown URIs all denied
    assert not tool_policy.resource_uri_allowed("gnosis://source-tables/dbt")
    assert not tool_policy.resource_uri_allowed("ui://cerebro/report")
    assert not tool_policy.resource_uri_allowed("ui://cerebro/visualization")
    assert not tool_policy.template_allowed("gnosis://source-tables/{database}")
    assert tool_policy.template_allowed("gnosis://dbt-modules/{module_name}")
    assert not tool_policy.prompt_allowed("analysis_sop")


def test_surface_profile_boot_validation(monkeypatch):
    from cerebro_mcp.runtime.bootstrap import validate_surface_profile

    # stdio: exempt
    monkeypatch.setattr(settings, "MCP_SURFACE_PROFILE", "")
    validate_surface_profile("stdio")

    # HTTP without a profile: fail closed
    with pytest.raises(RuntimeError, match="MCP_SURFACE_PROFILE"):
        validate_surface_profile("streamable-http")

    # internal_full: today's surface, chosen by name
    monkeypatch.setattr(settings, "MCP_SURFACE_PROFILE", "internal_full")
    validate_surface_profile("streamable-http")

    # connector profile: LEAN_CORE conflict
    monkeypatch.setattr(
        settings, "MCP_SURFACE_PROFILE", tool_policy.PROFILE_TEAM_ANALYTICS_V1
    )
    monkeypatch.setattr(settings, "LEAN_CORE_ENABLED", True)
    with pytest.raises(RuntimeError, match="LEAN_CORE_ENABLED"):
        validate_surface_profile("streamable-http")

    # connector profile: gated-tool flags required
    monkeypatch.setattr(settings, "LEAN_CORE_ENABLED", False)
    monkeypatch.setattr(settings, "CUSTOM_TOOLS_ENABLED", False)
    with pytest.raises(RuntimeError, match="CUSTOM_TOOLS_ENABLED"):
        validate_surface_profile("streamable-http")
    monkeypatch.setattr(settings, "CUSTOM_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "CUSTOM_TOOLS_PATH", "custom_tools.yaml")
    monkeypatch.setattr(settings, "SEMANTIC_ENABLED", False)
    with pytest.raises(RuntimeError, match="SEMANTIC_ENABLED"):
        validate_surface_profile("streamable-http")
    monkeypatch.setattr(settings, "SEMANTIC_ENABLED", True)
    validate_surface_profile("streamable-http")


def test_plain_fastmcp_refused_by_filter_install():
    """install_app_only_filter must refuse a plain FastMCP loudly — on one,
    the visibility filter can never reach the wire."""
    plain = FastMCP("plain")
    with pytest.raises(TypeError, match="wire"):
        mini_apps.install_app_only_filter(plain)
