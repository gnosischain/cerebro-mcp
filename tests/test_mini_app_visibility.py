"""Verify the app-only tool visibility filter hides hydration tools from the model."""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.metric_lab import register_metric_lab_tools
from cerebro_mcp.tools.mini_apps import (
    get_app_only_tool_names,
    register_mini_app_infra,
)
from cerebro_mcp.tools.token_explorer import register_token_explorer_tools


class StubCH:
    """Sufficient to satisfy register_*_tools — no DB calls happen at registration."""


@pytest.fixture
def mcp_server():
    server = FastMCP("test-server")
    ch = StubCH()
    register_mini_app_infra(server, ch)
    register_token_explorer_tools(server, ch)
    register_metric_lab_tools(server, ch)
    return server


def test_app_only_tools_are_hidden_from_model(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())
    names = {t.name for t in tools}
    for hidden in ("get_mini_app_rows", "get_mini_app_state"):
        assert hidden not in names, (
            f"{hidden} must be hidden from the model-facing tool list"
        )


def test_launcher_and_delta_tools_are_visible_to_model(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())
    names = {t.name for t in tools}
    expected_visible = {
        "open_token_explorer",
        "open_metric_lab_from_sql",
        "open_metric_lab_from_metrics",
        "update_token_explorer_focus",
        "update_metric_lab_chart",
    }
    missing = expected_visible - names
    assert not missing, f"missing model-visible tools: {missing}"


def test_app_only_tool_registry_records_hidden_names(mcp_server):
    app_only = get_app_only_tool_names()
    assert "get_mini_app_rows" in app_only
    assert "get_mini_app_state" in app_only


def test_install_filter_is_idempotent():
    server = FastMCP("idempotent")
    register_mini_app_infra(server, StubCH())
    first_list_tools = server.list_tools
    # Calling install again should not double-wrap
    mini_apps.install_app_only_filter(server)
    assert server.list_tools is first_list_tools
