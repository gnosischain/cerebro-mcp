"""Global-state classification registry (R10 §6.4, R9-audit blocker 9).

Every module-level MUTABLE object in the modules that carry cross-request
state must have an explicit, reviewed disposition. A lint against imports
cannot prove coverage — this test WALKS the modules and fails on any
mutable global that nobody classified, so adding shared state without a
decision is a red build, not a silent leak.

Dispositions:
    cycle-keyed   resolved per (owner, analysis_handle) via a proxy
    owner-keyed   partitioned by owner (cache keys carry identity)
    disabled-http guarded off under the connector profile
    safe-shared   locks, registries swapped atomically, or append-only
                  telemetry reviewed as identity-free
"""

from __future__ import annotations

import importlib

import pytest

#: module -> {global name -> disposition}. UNLISTED mutable globals fail.
#: Locks / queues / paths are not data state and are ignored by the walker.
#: "const" = a module constant that happens to use a mutable container —
#: reviewed as never mutated after import.
CLASSIFICATION: dict[str, dict[str, str]] = {
    "cerebro_mcp.tools.governance.session_state": {
        "state": "cycle-keyed",          # _SessionStateProxy
        "_default_state": "safe-shared", # the off-profile singleton itself
    },
    "cerebro_mcp.tools.visualization.charts": {
        "_chart_registry": "cycle-keyed",      # _ChartRegistryProxy
        "_default_chart_registry": "safe-shared",
        "_LAST_VISUAL": "disabled-http",       # write-guarded under profile
        "_REPORT_CACHE": "safe-shared",  # keyed by report_id; connector
                                         # reads re-authorize via the authz
                                         # store (owner recheck on hit)
        "state": "cycle-keyed",                # by-value import of the proxy
        "CHART_BUILDERS": "const",
        "CEREBRO_CHART_PALETTE_DARK": "const",
        "CEREBRO_CHART_PALETTE_LIGHT": "const",
        "ECHARTS_PALETTE_DARK": "const",
        "ECHARTS_PALETTE_LIGHT": "const",
        "_SEMANTIC_DIMENSION_ALIASES": "const",
    },
    "cerebro_mcp.tools.governance.reasoning": {
        "_current_session": "disabled-http",   # _record_step profile guard
        "_BATCH_CHART_ACTIONS": "const",
        "_SINGLE_CHART_ACTIONS": "const",
        "_RAW_EXECUTION_ACTIONS": "const",
        "_SEMANTIC_TOOL_NAMES": "const",
        "_SENSITIVE_KEY_MARKERS": "const",
        "_EXCLUDED_AUTO_TRACE_TOOLS": "const",
    },
    "cerebro_mcp.tools.visualization.mini_apps": {
        "_app_only_tool_names": "safe-shared",      # additive name registry
        "_force_visible_tool_names": "disabled-http",  # load_tools denial
        "APP_ONLY_META": "const",
        "_apps": "safe-shared",   # mini-app definitions: write-at-register,
                                  # read-only afterwards
        "_views": "disabled-http",  # view store has NO owner check
                                    # (mini_apps.py get_view) — unreachable
                                    # on the connector profile because every
                                    # mini-app tool is profile-excluded; an
                                    # owner stamp is required before any
                                    # multi-user surface exposes it
    },
    "cerebro_mcp.runtime.analysis_registry": {
        "_cycles": "cycle-keyed",
    },
}

#: Types treated as mutable state when found at module level.
_MUTABLE_TYPES = (dict, list, set)


def _mutable_globals(module) -> set[str]:
    found = set()
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        if isinstance(value, _MUTABLE_TYPES):
            found.add(name)
        # our two proxies are mutable state carriers too
        if type(value).__name__ in ("_SessionStateProxy", "_ChartRegistryProxy"):
            found.add(name)
        if type(value).__name__ == "SessionState":
            found.add(name)
    return found


@pytest.mark.parametrize("module_name", sorted(CLASSIFICATION))
def test_every_mutable_global_is_classified(module_name):
    module = importlib.import_module(module_name)
    found = _mutable_globals(module)
    classified = set(CLASSIFICATION[module_name])
    unclassified = found - classified
    assert not unclassified, (
        f"{module_name}: unclassified mutable global(s) {sorted(unclassified)} "
        "— add each to CLASSIFICATION with a reviewed disposition "
        "(cycle-keyed / owner-keyed / disabled-http / safe-shared). "
        "Shared state without a decision is how cross-user leaks ship."
    )
    # Stale-pruning applies only to names the walker can DETECT. State that
    # is `None` until first use (e.g. reasoning._current_session) is
    # legitimately classified yet undetectable at import time.
    sentinel = object()
    stale = {
        n
        for n in classified - found
        if getattr(module, n, sentinel) is sentinel
    }
    assert not stale, (
        f"{module_name}: CLASSIFICATION lists {sorted(stale)} which no "
        "longer exist — prune the registry so it stays authoritative."
    )
