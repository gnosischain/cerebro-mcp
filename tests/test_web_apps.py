"""Tests for standalone web-app delivery of the mini-apps.

Exercises the registry-based dispatch in
``cerebro_mcp.tools.visualization.web_apps`` without touching FastMCP
internals or a live ClickHouse: the model-lineage ``open_*`` tool builds an
empty view with no DB access when called with an empty seed.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools.visualization import mini_apps, web_apps
from cerebro_mcp.tools.analytics.model_lineage_app import register_model_lineage_tools


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


@pytest.fixture
def registered():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    web_apps.WEB_APP_CONFIGS.clear()
    web_apps.MINI_APP_TOOL_REGISTRY.clear()
    mcp = FastMCP("test")
    register_model_lineage_tools(mcp, None)
    return mcp


def test_register_web_app_populates_registry(registered):
    assert "model_lineage" in web_apps.WEB_APP_CONFIGS
    cfg = web_apps.WEB_APP_CONFIGS["model_lineage"]
    assert cfg.open_tool == "open_model_lineage"
    assert "open_model_lineage" in web_apps.MINI_APP_TOOL_REGISTRY
    assert "expand_model_lineage_node" in web_apps.MINI_APP_TOOL_REGISTRY


@pytest.mark.asyncio
async def test_serve_app_returns_html_with_payload(registered):
    req = FakeRequest(path_params={"app_id": "model_lineage"})
    resp = await web_apps.serve_app(req)
    assert resp.status_code == 200
    html = resp.body.decode()
    assert 'id="mini-app-data"' in html
    assert "window.__MINI_APP_API__" in html
    assert "/app/model_lineage/api/tool" in html


@pytest.mark.asyncio
async def test_serve_app_unknown_app_404(registered):
    req = FakeRequest(path_params={"app_id": "nope"})
    resp = await web_apps.serve_app(req)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_open_tool_returns_payload(registered):
    req = FakeRequest(
        path_params={"app_id": "model_lineage", "tool_name": "open_model_lineage"},
        body={"arguments": {}},
    )
    resp = await web_apps.dispatch_app_tool(req)
    assert resp.status_code == 200
    data = json.loads(resp.body.decode())
    assert data["isError"] is False
    assert data["structuredContent"]["type"] == "INITIAL_LOAD"
    assert data["structuredContent"]["app_id"] == "model_lineage"


@pytest.mark.asyncio
async def test_dispatch_serializes_dates(registered):
    """A tool whose structuredContent carries date/datetime must not 500.

    Starlette's JSONResponse uses stdlib json.dumps, which cannot serialize
    date/datetime. The web-app path routes through Pydantic mode="json" so
    such values become ISO strings (mirroring the MCP bridge).
    """
    import datetime as dt

    from mcp.types import CallToolResult, TextContent

    def _date_tool():
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structuredContent={
                "as_of": dt.date(2026, 5, 30),
                "ts": dt.datetime(2026, 5, 30, 9, 20, 0),
                "type": "PATCH_VIEW_STATE",
            },
            isError=False,
        )

    web_apps.MINI_APP_TOOL_REGISTRY["_date_tool"] = _date_tool
    req = FakeRequest(
        path_params={"app_id": "model_lineage", "tool_name": "_date_tool"},
        body={"arguments": {}},
    )
    resp = await web_apps.dispatch_app_tool(req)
    assert resp.status_code == 200
    data = json.loads(resp.body.decode())
    assert data["isError"] is False
    assert data["structuredContent"]["as_of"] == "2026-05-30"
    assert data["structuredContent"]["ts"].startswith("2026-05-30T")


@pytest.mark.asyncio
async def test_dispatch_plain_dict_tool_is_wrapped(registered):
    """A mini-app tool may return a plain JSON-able dict (FastMCP auto-wraps it
    as structuredContent). The HTTP dispatch must do the same — not 500 on
    `.content`/`.model_dump`. Regression for the graph-native tools
    (search_graph_catalog / explore_neighborhood / calculate_flow_efficiency)
    served in browser mode."""
    def _plain_tool():
        return {"count": 2, "results": [{"id": "profile:circles_trust"}]}

    web_apps.MINI_APP_TOOL_REGISTRY["_plain_tool"] = _plain_tool
    req = FakeRequest(
        path_params={"app_id": "model_lineage", "tool_name": "_plain_tool"},
        body={"arguments": {}},
    )
    resp = await web_apps.dispatch_app_tool(req)
    assert resp.status_code == 200
    data = json.loads(resp.body.decode())
    assert data["isError"] is False
    assert data["structuredContent"]["count"] == 2
    assert data["content"] == []


@pytest.mark.asyncio
async def test_dispatch_plain_dict_with_datetime_is_serialized(registered):
    """A plain-dict tool whose values include date/datetime (e.g. ClickHouse
    rows from the observability tools) must not 500 — the plain-dict path
    coerces them to strings like the CallToolResult path does."""
    import datetime as dt

    def _dt_tool():
        return {"as_of": dt.datetime(2026, 5, 12, 23, 6, 48), "rows": [[dt.date(2026, 5, 12), 1]]}

    web_apps.MINI_APP_TOOL_REGISTRY["_dt_tool"] = _dt_tool
    req = FakeRequest(
        path_params={"app_id": "model_lineage", "tool_name": "_dt_tool"},
        body={"arguments": {}},
    )
    resp = await web_apps.dispatch_app_tool(req)
    assert resp.status_code == 200
    data = json.loads(resp.body.decode())
    assert data["isError"] is False
    assert data["structuredContent"]["as_of"].startswith("2026-05-12")


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_404(registered):
    req = FakeRequest(
        path_params={"app_id": "model_lineage", "tool_name": "nope"},
        body={"arguments": {}},
    )
    resp = await web_apps.dispatch_app_tool(req)
    assert resp.status_code == 404
