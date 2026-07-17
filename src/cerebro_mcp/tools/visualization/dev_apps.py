"""Dev-only mini apps — conditional registration.

Portfolio and Model Lineage are development/debug surfaces. In the default
deployment they are FULLY absent: their MCP tools are not registered, their
``ui://`` resources don't exist, their ``/app/{id}`` routes 404, and the
cross-app tabs hide (the chrome filters on the injected registered-app list).
``settings.DEV_MINI_APPS_ENABLED=true`` restores everything.

The gate lives HERE (a side-effect-free module) rather than in ``server.py``
— importing server.py performs every registration at import time, which makes
call-site logic there untestable. The underlying ``register_portfolio_tools``
/ ``register_model_lineage_tools`` stay ungated so their own test suites can
keep calling them directly.
"""

from __future__ import annotations

from cerebro_mcp.config import settings


def register_dev_mini_apps(mcp, ch) -> None:
    """Register the dev-only miniapps — no-op unless DEV_MINI_APPS_ENABLED."""
    if not settings.DEV_MINI_APPS_ENABLED:
        return
    from cerebro_mcp.tools.analytics.model_lineage_app import (
        register_model_lineage_tools,
    )
    from cerebro_mcp.tools.visualization.portfolio import register_portfolio_tools

    register_portfolio_tools(mcp, ch)       # 5 tools + ui://cerebro/portfolio
    register_model_lineage_tools(mcp, ch)   # 4 tools + ui://cerebro/model_lineage


__all__ = ["register_dev_mini_apps"]
