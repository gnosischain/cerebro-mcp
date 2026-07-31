import importlib.resources

from cerebro_mcp.runtime import runtime_state

#: Shared contracts that personas reference by relative markdown link.
#:
#: 30 personas open with "you MUST apply every rule in
#: `_shared_quality_rules.md`", and the report gate's own rejection message
#: tells the model to "follow the rules in `_shared_quality_rules.md`" — while
#: nothing delivered that file. `get_agent_persona` read exactly `{role}.md`,
#: the file is not a valid role, and a relative link is not resolvable by a
#: client with no filesystem access to the installed package. So every persona
#: declared a mandatory dependency that could not be obtained, and the four SQL
#: discipline rules the gate enforces were stated only there.
#:
#: Resolution is driven by the persona's OWN reference rather than a hardcoded
#: role list: a new persona that links the contract gets it automatically, and
#: one that drops the link stops paying for it.
_SHARED_CONTRACTS = ("_shared_quality_rules.md", "_forensic_standards.md")


def _read_prompt(filename: str) -> str:
    return (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath(filename)
        .read_text("utf-8")
    )


def load_persona(role: str) -> str:
    """Persona text with every shared contract it references inlined.

    Shared by `get_agent_persona` and the `@mcp.prompt()` persona loaders in
    `prompts/templates.py`, so both front doors deliver the same thing.
    """
    content = _read_prompt(f"{role}.md")
    for contract in _SHARED_CONTRACTS:
        if contract not in content:
            continue
        try:
            body = _read_prompt(contract)
        except (FileNotFoundError, OSError):  # pragma: no cover - packaging
            continue
        content += (
            f"\n\n---\n\n"
            f"# Inlined: {contract}\n\n"
            f"This persona references `{contract}` as mandatory. It is "
            f"included below because a relative link is not resolvable by an "
            f"MCP client.\n\n"
            f"{body}"
        )
    return content


_VALID_ROLES = {
    "analytics_reporter",
    "ui_designer",
    "reality_checker",
    # Storyteller mode personas (opt-in; standard mode is unaffected)
    "storyteller_orchestrator",
    "storyteller_context",
    "storyteller_narrative",
    "storyteller_visual_designer",
    "storyteller_writer",
    "storyteller_critic",
    "storyteller_accessibility",
    # Specialist consultants (called by analytics_reporter for domain expertise)
    "forecasting_analyst",
    "growth_analyst",
    "tokenomics_analyst",
    "defi_analyst",
    "network_health_analyst",
    "bridge_security_analyst",
    "marketing_analyst",
    "esg_analyst",
    "statistical_reviewer",
    # Marketing Mix Modeling agents (sector contribution / ROI analysis)
    "mmm_analyst",
    "mmm_causal_reviewer",
    "mmm_simulator",
    # Multi-Touch Attribution + unified MMM/MTA reconciliation
    "mta_analyst",
    "unified_causal_reviewer",
    "unified_allocator",
    # Cerebro Dispatcher — top-level intent triage + gated routing
    "cerebro_dispatcher",
    # Grafana dashboard architect (mixed-audience dashboard composition)
    "grafana_architect",
    # On-chain incident forensics via the bulk RPC scan toolkit
    "chain_forensics",
    # Transaction/pattern forensics. These share the accuracy contract in
    # prompts/agents/_forensic_standards.md (evidence tiers, calibrated
    # confidence, mandatory alternative hypothesis, coverage disclosure).
    "transaction_forensics",
    "pattern_forensics",
    "forensic_reviewer",
    # Deep-research workflow lead (multi-phase research projects + peer review)
    "gnosis_research_analyst",
    # Domain specialists over curated raw databases (cow_db / governance_db —
    # no dbt models or semantic coverage; describe_table is their discovery)
    "cow_analyst",
    "dao_governance_analyst",
    # Lean point-in-time on-chain reads (no forensic ceremony; escalates to
    # chain_forensics for incident/historical/reconciliation work)
    "chain_state_analyst",
}


def register_agent_tools(mcp):
    """Register agent persona tools."""

    @mcp.tool()
    def get_agent_persona(role: str) -> str:
        """Fetch strict operational rules for a specific agent persona.

        Call this before executing a phase to adopt the agent's identity,
        critical rules, and success metrics.

        Args:
            role: One of the standard roles ('analytics_reporter',
                'ui_designer', 'reality_checker'), a storyteller role
                ('storyteller_orchestrator', 'storyteller_context',
                'storyteller_narrative', 'storyteller_visual_designer',
                'storyteller_writer', 'storyteller_critic',
                'storyteller_accessibility'), or a specialist consultant
                ('forecasting_analyst', 'growth_analyst',
                'tokenomics_analyst', 'defi_analyst',
                'network_health_analyst', 'bridge_security_analyst',
                'marketing_analyst', 'esg_analyst',
                'statistical_reviewer'), an MMM agent
                ('mmm_analyst', 'mmm_causal_reviewer', 'mmm_simulator'),
                a unified MMM+MTA measurement agent
                ('mta_analyst', 'unified_causal_reviewer',
                'unified_allocator'), the deep-research lead
                ('gnosis_research_analyst'), the Grafana dashboard
                architect ('grafana_architect'), the on-chain forensics
                analyst ('chain_forensics'), the transaction/pattern
                forensic specialists ('transaction_forensics',
                'pattern_forensics') and their accuracy gate
                ('forensic_reviewer'), the curated-raw-database domain
                specialists ('cow_analyst' for CoW Protocol internals over
                cow_db, 'dao_governance_analyst' for Snapshot + forum
                analytics over governance_db), the point-in-time chain
                reader ('chain_state_analyst'),
                or the top-level dispatcher ('cerebro_dispatcher').
        """
        if role not in _VALID_ROLES:
            return (
                f"Unknown role: {role}. "
                f"Valid roles: {', '.join(sorted(_VALID_ROLES))}"
            )
        from cerebro_mcp.tools.tool_policy import (
            CONNECTOR_PERSONAS_ALLOWED,
            persona_allowed,
        )

        if not persona_allowed(role):
            # Frozen connector allowlist (tool_policy): a persona whose
            # rendered rules direct the model at excluded tools would
            # produce a workflow the wire rejects at every step.
            return (
                f"Persona '{role}' is not available on this profile — its "
                "workflow depends on tools outside the connector surface. "
                f"Available: {', '.join(sorted(CONNECTOR_PERSONAS_ALLOWED))}"
            )
        runtime_state.current_agent_role = role
        return load_persona(role)
