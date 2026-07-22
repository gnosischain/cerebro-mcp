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

import hashlib
import importlib.resources
import os
from pathlib import Path
import subprocess
import threading
from datetime import datetime, timezone
from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.loaders.manifest import manifest
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
from .flows import register_flows_tools
from .transactions import register_transaction_tools
from .timeline import register_timeline_tools
from .traverse import canonical_edge_id as _canonical_edge_id
from .traverse import merge_graph as _merge_graph
from .ui_tools import register_ui_tools

_BUNDLED_HTML: str | None = None
_BUNDLED_HTML_SIGNATURE: tuple[int, int] | None = None
_BUNDLED_HTML_SHA256: str | None = None
_BUNDLED_HTML_MTIME: str | None = None
_BUNDLE_LOCK = threading.Lock()
_WEB_BUNDLED_HTML: str | None = None
_WEB_BUNDLED_HTML_SIGNATURE: tuple[int, int] | None = None
_WEB_BUNDLED_HTML_SHA256: str | None = None
_WEB_BUNDLED_HTML_MTIME: str | None = None
_WEB_BUNDLE_LOCK = threading.Lock()
_APP_COMMIT: str | None = None


def _bundle_resource():
    return importlib.resources.files("cerebro_mcp").joinpath(
        "static/graph_explorer.html"
    )


def _web_bundle_resource():
    return importlib.resources.files("cerebro_mcp").joinpath(
        "static/graph_explorer_web.html"
    )


def _iso_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _app_commit() -> str:
    """Best-effort build identity without requiring a Git checkout in prod."""
    global _APP_COMMIT
    configured = os.environ.get("CEREBRO_BUILD_COMMIT", "").strip()
    if configured:
        return configured
    if _APP_COMMIT is not None:
        return _APP_COMMIT
    package_path = Path(__file__).resolve()
    repo = next((p for p in package_path.parents if (p / ".git").exists()), None)
    if repo is None:
        _APP_COMMIT = "unknown"
        return _APP_COMMIT
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        _APP_COMMIT = result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        _APP_COMMIT = "unknown"
    return _APP_COMMIT


def get_graph_explorer_html() -> str:
    """Load the Vite bundle, invalidating the cache when the file changes."""
    global _BUNDLED_HTML, _BUNDLED_HTML_SIGNATURE
    global _BUNDLED_HTML_SHA256, _BUNDLED_HTML_MTIME
    with _BUNDLE_LOCK:
        try:
            resource = _bundle_resource()
            with importlib.resources.as_file(resource) as path:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if _BUNDLED_HTML is not None and signature == _BUNDLED_HTML_SIGNATURE:
                    return _BUNDLED_HTML
                raw = path.read_bytes()
                _BUNDLED_HTML = raw.decode("utf-8")
                _BUNDLED_HTML_SIGNATURE = signature
                _BUNDLED_HTML_SHA256 = hashlib.sha256(raw).hexdigest()
                _BUNDLED_HTML_MTIME = _iso_mtime(stat.st_mtime)
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>graph_explorer.html not built</div>"
                "</body></html>"
            )
            _BUNDLED_HTML_SIGNATURE = None
            _BUNDLED_HTML_SHA256 = hashlib.sha256(
                _BUNDLED_HTML.encode("utf-8")
            ).hexdigest()
            _BUNDLED_HTML_MTIME = None
        return _BUNDLED_HTML


def get_graph_explorer_web_html() -> str:
    """Load the split standalone shell while keeping MCP HTML self-contained.

    Source checkouts that have not run the new dual build yet fall back to the
    inline artifact, preserving the existing developer workflow.
    """
    global _WEB_BUNDLED_HTML, _WEB_BUNDLED_HTML_SIGNATURE
    global _WEB_BUNDLED_HTML_SHA256, _WEB_BUNDLED_HTML_MTIME
    missing = False
    with _WEB_BUNDLE_LOCK:
        try:
            resource = _web_bundle_resource()
            with importlib.resources.as_file(resource) as path:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if (
                    _WEB_BUNDLED_HTML is not None
                    and signature == _WEB_BUNDLED_HTML_SIGNATURE
                ):
                    return _WEB_BUNDLED_HTML
                raw = path.read_bytes()
                _WEB_BUNDLED_HTML = raw.decode("utf-8")
                _WEB_BUNDLED_HTML_SIGNATURE = signature
                _WEB_BUNDLED_HTML_SHA256 = hashlib.sha256(raw).hexdigest()
                _WEB_BUNDLED_HTML_MTIME = _iso_mtime(stat.st_mtime)
                return _WEB_BUNDLED_HTML
        except (FileNotFoundError, ModuleNotFoundError):
            missing = True

    if missing:
        inline = get_graph_explorer_html()
        with _WEB_BUNDLE_LOCK:
            _WEB_BUNDLED_HTML = inline
            _WEB_BUNDLED_HTML_SIGNATURE = None
            _WEB_BUNDLED_HTML_SHA256 = hashlib.sha256(
                inline.encode("utf-8")
            ).hexdigest()
            _WEB_BUNDLED_HTML_MTIME = _BUNDLED_HTML_MTIME
        return inline
    raise RuntimeError("Graph Explorer standalone bundle could not be loaded")


def get_graph_explorer_diagnostics() -> dict[str, Any]:
    """Identity injected into the app and exposed on its health route."""
    get_graph_explorer_html()
    get_graph_explorer_web_html()
    return {
        "app_commit": _app_commit(),
        # Standalone diagnostics identify the exact shell served by /app.
        "bundle_sha256": _WEB_BUNDLED_HTML_SHA256,
        "bundle_mtime": _WEB_BUNDLED_HTML_MTIME,
        "bundle_kind": (
            "split_web"
            if _WEB_BUNDLED_HTML_SIGNATURE is not None
            else "inline_fallback"
        ),
        "inline_bundle_sha256": _BUNDLED_HTML_SHA256,
        "web_bundle_sha256": _WEB_BUNDLED_HTML_SHA256,
        "dbt_manifest_sha256": manifest.content_hash,
    }


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
        **register_timeline_tools(mcp, ch),
        **register_flows_tools(mcp, ch),
        **register_transaction_tools(mcp, ch),
        **register_data_tools(mcp, ch),
    }
    web_apps.register_web_app(
        app_id=constants.GRAPH_EXPLORER_APP_ID,
        open_tool="open_graph_explorer",
        html_loader=get_graph_explorer_web_html,
        tools=tools,
        title="Graph Explorer",
        description=(
            "Forensic graph of on-chain relationships and fund flows. Trace value hop by hop from a seed address, play a subgraph across time, or browse the semantic relationship catalog."
        ),
        icon="◈",
        diagnostics_loader=get_graph_explorer_diagnostics,
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
    "get_graph_explorer_web_html",
    "get_graph_explorer_diagnostics",
    "register_graph_explorer_tools",
    "_canonical_edge_id",
    "_merge_graph",
]
