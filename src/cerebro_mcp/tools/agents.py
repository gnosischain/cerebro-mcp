import importlib.resources


_VALID_ROLES = {"analytics_reporter", "ui_designer", "reality_checker"}


def register_agent_tools(mcp):
    """Register agent persona tools."""

    @mcp.tool()
    def get_agent_persona(role: str) -> str:
        """Fetch strict operational rules for a specific agent persona.

        Call this before executing a phase to adopt the agent's identity,
        critical rules, and success metrics.

        Args:
            role: One of 'analytics_reporter', 'ui_designer', 'reality_checker'.
        """
        if role not in _VALID_ROLES:
            return (
                f"Unknown role: {role}. "
                f"Valid roles: {', '.join(sorted(_VALID_ROLES))}"
            )
        content = (
            importlib.resources.files("cerebro_mcp.prompts.agents")
            .joinpath(f"{role}.md")
            .read_text("utf-8")
        )
        return content
