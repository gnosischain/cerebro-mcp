"""Lint tests for agent-persona markdown files.

Live debugging on 2026-04-22 found that 5 specialist personas referenced
`dbt.*` tables that LOOK real (namespaced) but do not exist in the
catalog. Agents would happily copy the SQL and crash with UNKNOWN_TABLE.

The fix for each was to add a top-of-toolkit guard rule that tells the
agent "run search_models + describe_table first; names below are
illustrative." These tests lock that in so the warning can't silently
disappear in a future edit.
"""

from __future__ import annotations

import importlib.resources

import pytest


# Personas whose toolkit references namespaced dbt.* tables that may not
# exist — they MUST carry a verification warning.
PERSONAS_NEEDING_WARNING = [
    "growth_analyst",
    "tokenomics_analyst",
    "network_health_analyst",
    "bridge_security_analyst",
    "esg_analyst",
]

# Phrase the warning must contain, case-insensitively.
WARNING_PHRASES = [
    "search_models",
    "describe_table",
]


def _load_persona(role: str) -> str:
    return (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath(f"{role}.md")
        .read_text("utf-8")
    )


@pytest.mark.parametrize("role", PERSONAS_NEEDING_WARNING)
def test_persona_has_table_verification_warning(role):
    content = _load_persona(role)
    lower = content.lower()
    # Some variant of "illustrative" / "not guaranteed to exist" must appear
    warning_markers = (
        "illustrative",
        "not guaranteed",
        "verify",
    )
    assert any(marker in lower for marker in warning_markers), (
        f"persona '{role}' references dbt.* tables but is missing a "
        f"verification warning (one of {warning_markers})"
    )
    for phrase in WARNING_PHRASES:
        assert phrase in lower, (
            f"persona '{role}' warning must tell the agent to call `{phrase}` first"
        )


def test_growth_analyst_uses_uniqexactif_not_countif_distinct():
    """countIf(DISTINCT ...) is not valid ClickHouse syntax.

    Check only non-comment lines — the persona may still mention the
    broken pattern in a warning comment as context for the agent.
    """
    content = _load_persona("growth_analyst")
    offending = [
        line for line in content.splitlines()
        if "countIf(DISTINCT" in line and not line.lstrip().startswith("--")
    ]
    assert not offending, (
        "growth_analyst has non-comment line(s) still using countIf(DISTINCT ...):\n"
        + "\n".join(offending)
    )
    assert "uniqExactIf(" in content, (
        "growth_analyst must show the correct uniqExactIf pattern as a fix"
    )


def test_mmm_analyst_range_length_fix():
    """Bug #1 fixed during MMM live test: range(9) → range(length(arr))."""
    content = _load_persona("mmm_analyst")
    assert "range(length(" in content, (
        "mmm_analyst must use range(length(arr)) so adstock windows of "
        "variable size don't raise SIZES_OF_ARRAYS_DONT_MATCH at series start"
    )


def test_mmm_analyst_tuple_access_fix():
    """Bug #2-3 fixed during MMM live test: tuple .1/.2 access, correct (k,b) order."""
    content = _load_persona("mmm_analyst")
    # Must not contain the invalid tuple-destructure alias
    assert "AS (log_beta, r)" not in content
    assert "AS (r, log_beta)" not in content
    # Must document the correct mapping
    assert "(slope, intercept)" in content or "slope" in content.lower()
