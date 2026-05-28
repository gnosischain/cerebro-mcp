"""Tests for the MTA + unified-measurement agent personas.

Three agents extend the MMM stack into journey-grain attribution:
- mta_analyst: discovery-first journey attribution (rule-based + Markov + Shapley proxy)
- unified_causal_reviewer: gate that bounds MTA credit by MMM-estimated lift
- unified_allocator: bounded micro / tactical allocation after both reviewers PASS

These tests verify the persona files exist, are registered in _VALID_ROLES,
load via get_agent_persona, and contain the rules the design depends on
(discovery-first, incrementality bound, ±30% cap, coverage disclosure).
"""

from __future__ import annotations

import importlib.resources

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.tools.governance.agents import _VALID_ROLES, register_agent_tools


MTA_ROLES = [
    "mta_analyst",
    "unified_causal_reviewer",
    "unified_allocator",
]


def _persona(role: str) -> str:
    return (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath(f"{role}.md")
        .read_text("utf-8")
    )


@pytest.mark.parametrize("role", MTA_ROLES)
def test_mta_persona_file_exists(role):
    content = _persona(role)
    assert len(content) > 300, f"persona {role} looks empty"


@pytest.mark.parametrize("role", MTA_ROLES)
def test_mta_role_registered(role):
    assert role in _VALID_ROLES


@pytest.mark.parametrize("role", MTA_ROLES)
def test_get_agent_persona_returns_mta_roles(role):
    mcp = FastMCP(f"test-{role}")
    register_agent_tools(mcp)
    fn = mcp._tool_manager._tools["get_agent_persona"].fn
    content = fn(role=role)
    assert "Unknown role" not in content
    assert len(content) > 300


def test_mta_analyst_requires_runtime_discovery():
    content = _persona("mta_analyst")
    lower = content.lower()
    # Must instruct the agent to rediscover and verify columns at runtime
    assert "search_models" in content
    assert "describe_table" in content
    # Must explicitly disclaim the example model names as not-a-contract
    assert "do not assume" in lower or "not guaranteed" in lower
    # Must ship a runtime mapping contract
    assert "runtime mapping" in lower


def test_mta_analyst_contains_methods():
    content = _persona("mta_analyst")
    # Funnel + sequence diagnostics
    assert "windowFunnel" in content
    assert "sequenceMatch" in content
    # Algorithmic methods
    assert "Markov" in content
    assert "Shapley" in content
    # Lookback discipline
    assert "lookback" in content.lower()


def test_mta_analyst_contains_volume_gates():
    content = _persona("mta_analyst")
    # Volume rules: <30 / 30-499 / 500+
    assert "30" in content
    assert "500" in content


def test_unified_reviewer_blocks_overclaiming():
    content = _persona("unified_causal_reviewer")
    lower = content.lower()
    # The point of the reviewer
    assert "incrementality" in lower or "incremental lift" in lower
    assert "coverage" in lower
    assert "selection bias" in lower
    assert "leakage" in lower
    # Verdict format
    assert "VERDICT: PASS" in content or "verdict: pass" in lower
    assert "BLOCK" in content
    # Eight checks
    assert "Check 3" in content or "Incrementality bound" in content


def test_unified_reviewer_documents_calibration_formula():
    content = _persona("unified_causal_reviewer")
    # The calibration math must appear so downstream agents apply it
    assert "calibrated" in content.lower()
    assert "mmm_incremental_lift" in content or "MMM_lift" in content
    assert "unexplained" in content.lower() or "untracked" in content.lower()


def test_unified_allocator_respects_mmm_bounds():
    content = _persona("unified_allocator")
    lower = content.lower()
    # Composes MMM lift with MTA shares
    assert "mmm" in lower
    assert "mta" in lower
    # ±30% per-period cap inherited from mmm_simulator
    assert "30%" in content
    # Refuses without unified reviewer PASS
    assert "unified_causal_reviewer" in content
    assert "PASS" in content or "pass" in lower
    # Bounds Σ allocation by MMM lift
    assert "incremental lift" in lower


def test_unified_allocator_calls_out_residual():
    content = _persona("unified_allocator")
    lower = content.lower()
    # Unexplained / untracked must be disclosed, not allocated
    assert "unexplained" in lower or "untracked" in lower


def test_unknown_role_still_rejected():
    """Regression: adding the new roles did not loosen unknown-role handling."""
    mcp = FastMCP("test-unknown")
    register_agent_tools(mcp)
    fn = mcp._tool_manager._tools["get_agent_persona"].fn
    out = fn(role="not_a_real_role_xyz")
    assert out.startswith("Unknown role")


def test_existing_mmm_roles_still_registered():
    """Regression: MTA registration did not drop the MMM trio."""
    for r in ("mmm_analyst", "mmm_causal_reviewer", "mmm_simulator"):
        assert r in _VALID_ROLES
