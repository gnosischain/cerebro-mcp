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
# exist — they MUST carry a verification warning. The curated-raw-DB
# personas (cow_db / governance_db) reference concrete table names too;
# their warning additionally tells the agent NOT to run search_models
# (no semantic coverage) and to verify with describe_table instead.
PERSONAS_NEEDING_WARNING = [
    "growth_analyst",
    "tokenomics_analyst",
    "network_health_analyst",
    "bridge_security_analyst",
    "esg_analyst",
    "mta_analyst",
    "cow_analyst",
    "dao_governance_analyst",
]

# Model names that appeared in the MTA persona's planning context. They
# illustrate the kind of models a live run looks for, but the persona
# must NOT treat them as a contract — every appearance must be inside a
# clearly-marked context block that tells the agent to rediscover.
CONTEXT_ONLY_MODEL_NAMES = [
    "int_execution_gnosis_app_user_events",
    "int_execution_gnosis_app_user_activity_daily",
    "int_execution_gnosis_app_swaps",
    "int_execution_gnosis_app_gpay_topups",
    "int_execution_gnosis_app_token_offer_claims",
    "int_execution_gnosis_app_marketplace_payments",
    "int_execution_gnosis_app_users_current",
    "fct_execution_gnosis_app_retention_monthly",
    "fct_execution_gnosis_app_churn_monthly",
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


def test_mta_context_models_are_not_required_contracts():
    """MTA persona may mention example models, but only inside a context block.

    The risk we're locking out: a future edit pastes one of these names into
    the SQL toolkit verbatim, making it look like a guaranteed table. Every
    appearance must sit near language that tells the agent these are
    examples and that runtime discovery is mandatory.
    """
    content = _load_persona("mta_analyst").lower()

    for model_name in CONTEXT_ONLY_MODEL_NAMES:
        if model_name not in content:
            continue
        idx = content.find(model_name)
        window = content[max(idx - 1500, 0): idx + 1500]
        assert "context" in window, (
            f"`{model_name}` must appear inside a 'context' block"
        )
        assert "rediscover" in window or "search_models" in window, (
            f"`{model_name}` block must instruct agent to rediscover / search_models"
        )
        assert "not guaranteed" in window or "do not assume" in window, (
            f"`{model_name}` block must disclaim that the name is not guaranteed"
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
