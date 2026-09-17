"""Lint tests for the domain data-plane personas.

cow_analyst, dao_governance_analyst and pool_liquidity_analyst work over
curated raw ClickHouse databases (cow_db / governance_db /
rpc_state_indexer) that have NO dbt models or semantic coverage;
chain_state_analyst is the lean point-in-time RPC reader.
These tests lock in the load-bearing content of each persona: the
anti-discovery instruction, the domain safety rules (FINAL, dedup keys,
quorum vocabulary, raw units / no dbt joins), and the forensics
escalation path.
"""

from __future__ import annotations

import importlib.resources
import re

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.tools.governance.agents import _VALID_ROLES, register_agent_tools


DOMAIN_ROLES = [
    "cow_analyst",
    "dao_governance_analyst",
    "chain_state_analyst",
    "pool_liquidity_analyst",
]

# Personas over curated raw DBs must tell the agent NOT to run dbt
# discovery — the semantic registry has zero coverage for their domain.
CURATED_DB_ROLES = ["cow_analyst", "dao_governance_analyst", "pool_liquidity_analyst"]


def _load_persona(role: str) -> str:
    return (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath(f"{role}.md")
        .read_text("utf-8")
    )


# ── registration ─────────────────────────────────────────────────


@pytest.mark.parametrize("role", DOMAIN_ROLES)
def test_role_registered(role):
    assert role in _VALID_ROLES


@pytest.mark.parametrize("role", DOMAIN_ROLES)
def test_persona_loads_via_get_agent_persona(role):
    mcp = FastMCP("test-domain-personas")
    register_agent_tools(mcp)
    fn = mcp._tool_manager._tools["get_agent_persona"].fn
    content = fn(role=role)
    assert len(content) > 500, f"persona '{role}' looks empty"
    assert not content.startswith("Unknown role")


# ── anti-discovery instruction (curated raw DBs) ─────────────────


@pytest.mark.parametrize("role", CURATED_DB_ROLES)
def test_curated_db_persona_forbids_dbt_discovery(role):
    """The persona must tell the agent NOT to run search_models for its
    domain (no semantic coverage) and to use describe_table instead."""
    content = _load_persona(role).lower()
    assert "search_models" in content
    assert "describe_table" in content
    # A "do not" within a few lines of search_models
    assert re.search(r"do\s+\*{0,2}not\*{0,2}[^.]{0,200}search_models", content) or re.search(
        r"search_models[^.]{0,200}do\s+\*{0,2}not\*{0,2}", content
    ), f"persona '{role}' must forbid dbt discovery for its domain"


# ── cow_analyst content ──────────────────────────────────────────


def test_cow_analyst_load_bearing_content():
    content = _load_persona("cow_analyst")
    lower = content.lower()
    assert "uniqExact" in content, "must state the dedup discipline"
    assert "BNB" in content, "must carry the chain-56 NULL-timestamp caveat"
    assert "open_cow_explorer" in content, "mini-app is the default visual path"
    assert "indexing_checkpoints" in content, "completeness is judged vs checkpoints"
    assert "observed snapshot" in lower or "not a complete" in lower, (
        "must caveat that open intents are a partial orderbook snapshot"
    )
    assert "chain_state_analyst" in content, "live-state handoff must be named"


def test_cow_analyst_never_uses_countif_distinct():
    content = _load_persona("cow_analyst")
    assert "countIf(DISTINCT" not in content, (
        "countIf(DISTINCT ...) is not valid ClickHouse — use uniqExactIf"
    )


# ── dao_governance_analyst content ───────────────────────────────


def test_dao_governance_analyst_load_bearing_content():
    content = _load_persona("dao_governance_analyst")
    lower = content.lower()
    assert "FINAL" in content, "every governance_db read must use FINAL"
    for word in ("met", "missed", "unspecified"):
        assert word in lower, f"quorum vocabulary must include '{word}'"
    assert "open_governance" in content, "mini-app is the default visual path"
    assert "lower(voter)" in content, "voter identity must be lowercased"
    assert "off-chain signaling" in lower, "scope guard must be stated"
    assert "passed" in lower and "never" in lower, (
        "must forbid pass/fail language for Snapshot outcomes"
    )


# ── chain_state_analyst content ──────────────────────────────────


def test_chain_state_analyst_load_bearing_content():
    content = _load_persona("chain_state_analyst")
    assert "contract_call_function" in content
    assert "rpc_batch_call" in content
    assert "chain_forensics" in content, "escalation path must be named"
    assert "block" in content.lower(), "values must be block-attributed"


def test_chain_state_analyst_stays_lean():
    """The lean state persona must NOT import the forensics reconciliation
    ceremony — that is exactly what chain_forensics is for."""
    content = _load_persona("chain_state_analyst").lower()
    assert "reconcile two independent ways" not in content
    assert "residual-bucket ledger" not in content or "no residual-bucket" in content


# ── pool_liquidity_analyst content ───────────────────────────────


def test_pool_liquidity_analyst_load_bearing_content():
    content = _load_persona("pool_liquidity_analyst")
    lower = content.lower()
    assert "config_registry" in content, "the pool universe comes from config_registry"
    assert "FINAL" in content, "config_registry is the one FINAL in the plane"
    assert "v_pool_cl_state_published" in content, (
        "state must be read from the published view, never the raw table"
    )
    assert "census_publications" in content, "as-of dates resolve from census_publications"
    assert "cl_below_active_threshold" in content, "state-only pools must be disclosed"
    assert "open_pools_explorer" in content, "mini-app is the default visual path"
    assert "raw units" in lower, "prices default to raw units"
    assert "uniqExact" in content, "pools are counted with uniqExact"
    # A "never" within 120 chars of "dbt": the persona must forbid dbt joins.
    assert re.search(r"never[^\n]{0,120}dbt", lower) or re.search(
        r"dbt[^\n]{0,120}never", lower
    ), "must forbid joining dbt models"


def test_pool_liquidity_analyst_never_finals_a_view():
    """FINAL belongs to config_registry alone in this plane: the published
    v_* views resolve dedup internally and the raw pool_* tables (keyed by
    attempt_id, so FINAL still duplicates) must not be read at all."""
    content = _load_persona("pool_liquidity_analyst")
    sql = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("--")
    )
    assert not re.search(r"\bv_\w+(\s+AS\s+\w+)?\s+FINAL\b", sql), (
        "a v_* view must never be read with FINAL"
    )
    assert re.search(r"config_registry\s+AS\s+\w+\s+FINAL", sql), (
        "config_registry must be read with FINAL (alias before FINAL)"
    )
    for raw in ("pool_cl_state", "pool_tick_liquidity", "pool_token_balances"):
        assert not re.search(rf"rpc_state_indexer\.{raw}\b", sql), (
            f"the raw table {raw} must never appear in a query"
        )
