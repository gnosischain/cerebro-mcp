"""Shared test configuration."""

import pytest

from cerebro_mcp.config import settings


@pytest.fixture(autouse=True)
def _no_browser_auto_open(monkeypatch):
    """Never pop a real browser during tests.

    ``create_report_artifact`` only auto-opens when REPORT_AUTO_OPEN is on
    (default off). A test that opts it back in monkeypatches ``webbrowser.open``;
    this guard keeps the suite default safe regardless.
    """
    monkeypatch.setattr(settings, "REPORT_AUTO_OPEN", False)


@pytest.fixture(autouse=True)
def _disable_tool_offload(monkeypatch):
    """Run offloaded tool bodies synchronously in tests.

    Heavy tools are wrapped with ``@_offloaded`` (runtime/offload.py), which
    makes the registered tool an async coroutine that runs the body on a worker
    thread. Tests call ``mcp._tool_manager._tools[name].fn(...)`` directly and
    expect a synchronous return. The wrapper is transparent (same body, same
    result — only the thread differs), so we replace it with identity at
    registration time; the off-loop behavior itself is covered by a dedicated
    freeze-regression test. Tools are nested defs re-decorated on every
    ``register_*`` call, so patching the module symbol before registration
    yields plain sync tools.
    """
    identity = lambda fn: fn  # noqa: E731
    for mod in (
        "cerebro_mcp.tools.visualization.charts",
        "cerebro_mcp.tools.analytics.dbt",
        "cerebro_mcp.tools.analytics.query",
        "cerebro_mcp.tools.analytics.schema",
    ):
        monkeypatch.setattr(f"{mod}._offloaded", identity, raising=False)
