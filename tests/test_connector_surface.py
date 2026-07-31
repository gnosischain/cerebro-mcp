"""Connector non-tool surface: personas, system_status redaction, resources.

The persona sweep re-derives the frozen CONNECTOR_PERSONAS_ALLOWED from the
RENDERED persona bodies (shared contracts inlined, agents.py:31-35) so a
persona edit that starts steering the model at an excluded tool turns the
build red instead of shipping a workflow the wire rejects at every step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.tools import tool_policy

AGENTS_DIR = Path("src/cerebro_mcp/prompts/agents")

#: Tool-shaped tokens that are ENGLISH in persona prose, not tool refs.
_PROSE_TOKENS = {"list"}

#: Name families that are excluded from the profile wholesale — a mention
#: of any of these in a rendered persona is a real dependency.
_EXCLUDED_FAMILY_PREFIXES = (
    "rpc_",
    "contract_",
    "storyteller_",
    "grafana_",
    "open_",           # mini-app entry points
    "load_graph",
    "load_metric_lab",
    "load_contract",
    "resolve_address",
)

_WORD = re.compile(r"[a-z][a-z0-9_]{3,}")


def _rendered(role: str) -> str:
    from cerebro_mcp.tools.governance.agents import load_persona

    return load_persona(role).lower()


def _excluded_mentions(text: str) -> set[str]:
    from cerebro_mcp.tools.tool_meta import TOOL_META

    known = set(TOOL_META) | set(tool_policy.TOOL_POLICY)
    tokens = set(_WORD.findall(text)) - _PROSE_TOKENS
    out = set()
    for t in tokens:
        if t in tool_policy.TOOL_POLICY:
            continue
        if t in known or t.startswith(_EXCLUDED_FAMILY_PREFIXES):
            if "_" in t:  # single bare words are prose
                out.add(t)
    return out


@pytest.mark.parametrize(
    "role", sorted(tool_policy.CONNECTOR_PERSONAS_ALLOWED)
)
def test_allowlisted_personas_stay_inside_the_profile(role):
    mentions = _excluded_mentions(_rendered(role))
    assert not mentions, (
        f"persona {role!r} is on CONNECTOR_PERSONAS_ALLOWED but its "
        f"rendered body references excluded tool(s) {sorted(mentions)[:6]} "
        "— either fix the persona or remove it from the frozen allowlist."
    )


def test_allowlist_names_are_real_personas():
    files = {f.stem for f in AGENTS_DIR.glob("*.md") if not f.stem.startswith("_")}
    ghost = tool_policy.CONNECTOR_PERSONAS_ALLOWED - files
    assert not ghost, f"allowlisted persona file(s) missing: {sorted(ghost)}"


def test_persona_gate_denies_off_profile_roles(monkeypatch):
    monkeypatch.setattr(
        settings, "MCP_SURFACE_PROFILE", tool_policy.PROFILE_TEAM_ANALYTICS_V1
    )
    assert not tool_policy.persona_allowed("cerebro_dispatcher")
    assert not tool_policy.persona_allowed("chain_forensics")
    assert not tool_policy.persona_allowed("storyteller_orchestrator")
    assert tool_policy.persona_allowed("mmm_analyst")
    monkeypatch.setattr(settings, "MCP_SURFACE_PROFILE", "")
    assert tool_policy.persona_allowed("cerebro_dispatcher")


def test_system_status_redacted_on_profile(monkeypatch):
    monkeypatch.setattr(
        settings, "MCP_SURFACE_PROFILE", tool_policy.PROFILE_TEAM_ANALYTICS_V1
    )
    from cerebro_mcp.runtime.mcp_server import CerebroFastMCP
    from cerebro_mcp.clients.clickhouse import ClickHouseManager
    from cerebro_mcp.tools.analytics.metadata import register_metadata_tools

    server = CerebroFastMCP("redaction-test")
    register_metadata_tools(server, ClickHouseManager())
    fn = next(
        t.fn
        for t in server._tool_manager._tools.values()
        if t.name == "system_status"
    )
    out = fn()
    # Operator detail must be absent: paths, URLs, hashes, db inventory.
    for leak in ("Content hash", "Source:", "governance_db", "://", "/Users/"):
        assert leak not in out, f"redacted system_status leaked {leak!r}"
    assert "# System Status" in out