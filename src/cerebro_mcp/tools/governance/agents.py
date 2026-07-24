import importlib.resources

from cerebro_mcp.runtime import runtime_state


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
        runtime_state.current_agent_role = role
        content = (
            importlib.resources.files("cerebro_mcp.prompts.agents")
            .joinpath(f"{role}.md")
            .read_text("utf-8")
        )
        return content
