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
                'ui_designer', 'reality_checker') or a storyteller role
                ('storyteller_orchestrator', 'storyteller_context',
                'storyteller_narrative', 'storyteller_visual_designer',
                'storyteller_writer', 'storyteller_critic',
                'storyteller_accessibility').
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
