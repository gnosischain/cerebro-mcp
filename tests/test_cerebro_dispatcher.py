"""Tests for the Cerebro Dispatcher — top-level gating orchestrator.

The dispatcher sits above every specialist persona. It classifies intent,
runs preflight, picks the specialist chain, enforces gates, and emits a
dispatch manifest. These tests cover:

- Registration in _VALID_ROLES
- Persona markdown loads via get_agent_persona
- Routing table names every real specialist (no broken references)
- MCP prompt surface wraps the persona correctly
- Key gate-language appears (mandatory manifest, MMM reviewer PASS)
"""

from __future__ import annotations

import importlib.resources

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.tools.governance.agents import _VALID_ROLES, register_agent_tools


def _load_persona() -> str:
    return (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath("cerebro_dispatcher.md")
        .read_text("utf-8")
    )


# ── registration ─────────────────────────────────────────────────


def test_dispatcher_role_registered():
    assert "cerebro_dispatcher" in _VALID_ROLES


def test_dispatcher_persona_file_exists():
    content = _load_persona()
    assert len(content) > 500, "dispatcher persona looks empty"


def test_get_agent_persona_returns_dispatcher():
    mcp = FastMCP("test-dispatcher")
    register_agent_tools(mcp)
    fn = mcp._tool_manager._tools["get_agent_persona"].fn
    content = fn(role="cerebro_dispatcher")
    assert "Cerebro Dispatcher" in content
    assert "dispatch manifest" in content.lower()


# ── routing table integrity ──────────────────────────────────────


SPECIALISTS_DISPATCHER_MUST_NAME = [
    # topic-routing specialists
    "growth_analyst",
    "forecasting_analyst",
    "defi_analyst",
    "tokenomics_analyst",
    "network_health_analyst",
    "bridge_security_analyst",
    "marketing_analyst",
    "esg_analyst",
    "statistical_reviewer",
    # MMM chain
    "mmm_analyst",
    "mmm_causal_reviewer",
    "mmm_simulator",
    # MTA + unified-measurement chain
    "mta_analyst",
    "unified_causal_reviewer",
    "unified_allocator",
    # sub-orchestrators and core roles
    "storyteller_orchestrator",
    "analytics_reporter",
    "reality_checker",
    # domain data-plane specialists (curated raw DBs + point-in-time chain)
    "cow_analyst",
    "dao_governance_analyst",
    "chain_state_analyst",
]


@pytest.mark.parametrize("role", SPECIALISTS_DISPATCHER_MUST_NAME)
def test_dispatcher_references_real_specialist(role):
    """The routing table must name a specialist that actually exists."""
    content = _load_persona()
    assert role in content, (
        f"dispatcher persona must reference specialist `{role}` "
        "in its routing table"
    )
    # Also verify the specialist is actually registered
    assert role in _VALID_ROLES, (
        f"dispatcher names `{role}` but it's not in _VALID_ROLES"
    )


# ── gate language present ────────────────────────────────────────


def test_dispatcher_has_mta_intent_category():
    """The dispatcher must route MTA and unified-measurement intents."""
    content = _load_persona()
    lower = content.lower()
    # Both new intents must appear in the routing table
    assert "`mta`" in content or " mta " in lower
    assert "unified_measurement" in lower
    # The unified reviewer must be named in the chain
    assert "unified_causal_reviewer" in content
    # Allocator named for the prescription path
    assert "unified_allocator" in content


def test_dispatcher_enforces_unified_review_gate():
    """The unified-measurement chain has its own PASS gate stacked on MMM."""
    content = _load_persona()
    lower = content.lower()
    assert "unified_causal_review" in lower
    # Must mention the gate is binding (PASS / VERDICT language somewhere)
    assert "PASS" in content


def test_dispatcher_enforces_mmm_reviewer_gate():
    """The MMM causal-reviewer PASS gate is the most important hard block."""
    content = _load_persona()
    assert "mmm_causal_reviewer" in content
    # Must mention PASS as a gate condition
    assert "PASS" in content, (
        "dispatcher must cite the `mmm_causal_reviewer` PASS requirement"
    )


def test_dispatcher_requires_preflight_before_routing():
    content = _load_persona().lower()
    assert "preflight_analytics_request" in content
    # Must be positioned as a prerequisite, not optional
    assert "before" in content


def test_dispatcher_caps_clarifying_questions():
    content = _load_persona().lower()
    # At most one clarifying question — this is the rule
    assert "one" in content and "clarif" in content


# ── MCP prompt surface ───────────────────────────────────────────


def test_dispatcher_prompt_registered_in_templates():
    """adopt_persona_cerebro_dispatcher must be callable as an MCP prompt."""
    mcp = FastMCP("test-dispatcher-prompt")
    from cerebro_mcp.prompts.templates import register_prompts

    register_prompts(mcp)
    # FastMCP stores prompts keyed by function name
    assert "adopt_persona_cerebro_dispatcher" in mcp._prompt_manager._prompts


# ── regression: existing roles still registered ──────────────────


@pytest.mark.parametrize(
    "role",
    [
        "analytics_reporter",
        "reality_checker",
        "ui_designer",
        "forecasting_analyst",
        "mmm_analyst",
        "storyteller_orchestrator",
        "mta_analyst",
        "unified_causal_reviewer",
        "unified_allocator",
        "cow_analyst",
        "dao_governance_analyst",
        "chain_state_analyst",
    ],
)
def test_existing_roles_still_registered(role):
    assert role in _VALID_ROLES
