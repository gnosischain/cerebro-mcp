import importlib.resources

from cerebro_mcp import runtime_state


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
                'statistical_reviewer'), or an MMM agent
                ('mmm_analyst', 'mmm_causal_reviewer', 'mmm_simulator').
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
