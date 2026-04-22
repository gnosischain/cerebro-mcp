"""Tests for the Marketing Mix Modeling (MMM) agent personas.

Three agents cooperate to deliver MMM-style sector contribution / ROI reports:
- mmm_analyst: orchestrator (spine-fill → multicollinearity → adstock/Hill → decompose)
- mmm_causal_reviewer: DAG gate (Chronological / Non-inclusion / Identifiability)
- mmm_simulator: budget reallocation with 30% shift cap

These tests verify the persona files exist, are registered in _VALID_ROLES,
load via get_agent_persona, and contain the statistical-safety rules surfaced
during plan review (continuous time spine, baseline extraction).
"""

from __future__ import annotations

import importlib.resources

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.tools.agents import _VALID_ROLES, register_agent_tools


MMM_ROLES = [
    "mmm_analyst",
    "mmm_causal_reviewer",
    "mmm_simulator",
]


@pytest.mark.parametrize("role", MMM_ROLES)
def test_mmm_persona_file_exists(role):
    content = (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath(f"{role}.md")
        .read_text("utf-8")
    )
    assert len(content) > 200, f"persona {role} looks empty"


@pytest.mark.parametrize("role", MMM_ROLES)
def test_mmm_role_registered(role):
    assert role in _VALID_ROLES


def _load(role: str) -> str:
    mcp = FastMCP(f"test-mmm-{role}")
    register_agent_tools(mcp)
    fn = mcp._tool_manager._tools["get_agent_persona"].fn
    return fn(role=role)


def test_get_agent_persona_returns_mmm_analyst():
    content = _load("mmm_analyst")
    assert "MMM Analyst" in content
    # Statistical-safety rules from the reviewed plan
    assert "Multicollinearity" in content
    assert "Continuous time spine" in content
    assert "Baseline extraction" in content


def test_get_agent_persona_returns_mmm_causal_reviewer():
    content = _load("mmm_causal_reviewer")
    assert "MMM Causal Reviewer" in content
    # Three-check gate structure
    assert "Chronological" in content
    assert "Non-inclusion" in content
    assert "Identifiability" in content
    assert "BLOCK" in content


def test_get_agent_persona_returns_mmm_simulator():
    content = _load("mmm_simulator")
    assert "MMM Simulator" in content
    # 30% cap rule from Guidebook p.80
    assert "30%" in content
    # Must refuse without a passing reviewer verdict
    assert "mmm_causal_reviewer" in content


def test_unknown_role_still_rejected():
    # Regression: adding MMM roles did not loosen unknown-role handling
    content = _load("not_a_real_role")
    assert content.startswith("Unknown role")


def test_existing_specialist_roles_unchanged():
    # Regression: MMM registration did not drop any prior role
    for r in (
        "analytics_reporter",
        "forecasting_analyst",
        "statistical_reviewer",
        "marketing_analyst",
        "defi_analyst",
    ):
        assert r in _VALID_ROLES
