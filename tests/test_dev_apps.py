"""Dev-only miniapp gating (portfolio + model lineage).

Default deployment: FULLY absent — tools unregistered, ui:// resources
absent, /app routes 404, and the injected __MINI_APP_APPS__ list omits them
(so the chrome hides their tabs). DEV_MINI_APPS_ENABLED=true restores all.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.config import Settings, settings
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.tools.visualization import mini_apps, web_apps
from cerebro_mcp.tools.visualization.dev_apps import register_dev_mini_apps

PORTFOLIO_TOOLS = {
    "open_portfolio",
    "load_portfolio_address",
    "navigate_portfolio_relation",
    "load_portfolio_section",
    "update_portfolio_focus",
}
LINEAGE_TOOLS = {
    "open_model_lineage",
    "expand_model_lineage_node",
    "set_model_lineage_filters",
    "load_column_lineage",
}
DEV_TOOLS = PORTFOLIO_TOOLS | LINEAGE_TOOLS
DEV_APP_IDS = {"portfolio", "model_lineage"}
DEV_RESOURCE_URIS = {"ui://cerebro/portfolio", "ui://cerebro/model_lineage"}


class FakeRequest:
    def __init__(self, *, path_params=None, query=None, headers=None):
        self.path_params = path_params or {}
        self.query_params = query or {}
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def clean_registries():
    reset_cache_for_tests()
    mini_apps.reset_views_for_tests()
    web_apps.WEB_APP_CONFIGS.clear()
    web_apps.MINI_APP_TOOL_REGISTRY.clear()
    yield
    web_apps.WEB_APP_CONFIGS.clear()
    web_apps.MINI_APP_TOOL_REGISTRY.clear()
    mini_apps.reset_views_for_tests()


def _tool_names(server: FastMCP) -> set[str]:
    return {t.name for t in asyncio.run(server.list_tools())}


def _resource_uris(server: FastMCP) -> set[str]:
    return {str(r.uri) for r in asyncio.run(server.list_resources())}


def test_flag_default_is_off():
    assert settings.DEV_MINI_APPS_ENABLED is False


def test_env_override_enables(monkeypatch):
    monkeypatch.setenv("DEV_MINI_APPS_ENABLED", "true")
    assert Settings().DEV_MINI_APPS_ENABLED is True


def test_default_registers_nothing():
    server = FastMCP("test-gate-off")
    register_dev_mini_apps(server, None)

    assert _tool_names(server) & DEV_TOOLS == set()
    assert _resource_uris(server) & DEV_RESOURCE_URIS == set()
    assert set(web_apps.WEB_APP_CONFIGS) & DEV_APP_IDS == set()
    assert set(web_apps.MINI_APP_TOOL_REGISTRY) & DEV_TOOLS == set()


@pytest.mark.asyncio
async def test_default_routes_404():
    server = FastMCP("test-gate-off")
    register_dev_mini_apps(server, None)
    for app_id in sorted(DEV_APP_IDS):
        resp = await web_apps.serve_app(FakeRequest(path_params={"app_id": app_id}))
        assert resp.status_code == 404, app_id
        resp = await web_apps.dispatch_app_tool(
            FakeRequest(path_params={"app_id": app_id, "tool_name": "open_portfolio"})
        )
        assert resp.status_code == 404, app_id


def test_default_injected_app_list_omits_dev_apps():
    server = FastMCP("test-gate-off")
    register_dev_mini_apps(server, None)
    html = web_apps._inject_payload("<html><script></script></html>", "{}", "x")
    marker = "window.__MINI_APP_APPS__="
    assert marker in html
    apps_json = html.split(marker, 1)[1].split(";", 1)[0]
    apps = json.loads(apps_json)
    assert set(apps) & DEV_APP_IDS == set()


def test_enabled_registers_everything(monkeypatch):
    monkeypatch.setattr(settings, "DEV_MINI_APPS_ENABLED", True)
    server = FastMCP("test-gate-on")
    register_dev_mini_apps(server, None)

    assert DEV_TOOLS <= _tool_names(server)
    assert DEV_RESOURCE_URIS <= _resource_uris(server)
    assert DEV_APP_IDS <= set(web_apps.WEB_APP_CONFIGS)
    assert DEV_TOOLS <= set(web_apps.MINI_APP_TOOL_REGISTRY)

    html = web_apps._inject_payload("<html><script></script></html>", "{}", "x")
    apps = json.loads(html.split("window.__MINI_APP_APPS__=", 1)[1].split(";", 1)[0])
    assert DEV_APP_IDS <= set(apps)
