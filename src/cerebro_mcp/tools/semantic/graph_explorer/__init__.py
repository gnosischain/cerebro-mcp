"""Graph Explorer mini app.

A cross-sector graph explorer whose profile catalog is driven entirely by
`cerebro.graph` metadata authored on dbt-cerebro semantic models. No
per-domain wiring lives here — the UI assembles the visible subgraph from
whatever graph-enabled profiles the semantic registry exposes.

Package layout (behavior-neutral split of the former single module):
  constants.py  — caps/defaults/dataset schemas (monkeypatch via attributes)
  state.py      — view-state + dataset builders (pure)
  fetch.py      — role resolution, unified profile-edge fetch, evidence
  traverse.py   — canonical edge identity + graph merge
  ui_tools.py   — the 4 agent-facing UI tools
  data_tools.py — the 4 graph-native data tools (PUBLIC contract)
"""

from __future__ import annotations

import importlib.resources

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.tools.visualization import mini_apps, web_apps

from . import constants
from .constants import (  # re-exported for existing importers
    DEFAULT_MAX_NEIGHBORS,
    DEFAULT_TITLE,
    DEFAULT_WINDOW_DAYS,
    GRAPH_EXPLORER_APP_ID,
    GRAPH_EXPLORER_URI,
    MAX_HOPS,
)
from .data_tools import register_data_tools
from .traverse import canonical_edge_id as _canonical_edge_id
from .traverse import merge_graph as _merge_graph
from .ui_tools import register_ui_tools

_BUNDLED_HTML: str | None = None


def get_graph_explorer_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_HTML
    if _BUNDLED_HTML is None:
        try:
            _BUNDLED_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/graph_explorer.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>graph_explorer.html not built</div>"
                "</body></html>"
            )
    return _BUNDLED_HTML


def register_graph_explorer_tools(mcp, ch: ClickHouseManager) -> None:
    mini_apps.register_app(
        constants.GRAPH_EXPLORER_APP_ID,
        title=constants.DEFAULT_TITLE,
        resource_uri=constants.GRAPH_EXPLORER_URI,
    )

    @mcp.resource(
        constants.GRAPH_EXPLORER_URI, mime_type="text/html;profile=mcp-app"
    )
    def serve_graph_explorer_app() -> str:
        return get_graph_explorer_html()

    tools = {
        **register_ui_tools(mcp, ch),
        **register_data_tools(mcp, ch),
    }
    web_apps.register_web_app(
        app_id=constants.GRAPH_EXPLORER_APP_ID,
        open_tool="open_graph_explorer",
        html_loader=get_graph_explorer_html,
        tools=tools,
    )


__all__ = [
    "GRAPH_EXPLORER_APP_ID",
    "GRAPH_EXPLORER_URI",
    "DEFAULT_TITLE",
    "MAX_HOPS",
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_MAX_NEIGHBORS",
    "constants",
    "get_graph_explorer_html",
    "register_graph_explorer_tools",
    "_canonical_edge_id",
    "_merge_graph",
]
