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

    @mcp.tool()
    def approve_analysis(notes: str = "") -> str:
        """Approve the current analysis for report generation.

        Call this after reviewing the charts and analysis quality.
        generate_report will be blocked until this approval is given.

        Args:
            notes: Optional review notes or quality assessment.
        """
        from cerebro_mcp.tools.session_state import state

        can_approve, rejection, warnings = (
            state.check_approval_preconditions()
        )

        if not can_approve:
            return f"**Approval rejected:** {rejection}"

        state.record_review_approval(role="reviewer", notes=notes)

        response = "Analysis approved for report generation."
        if warnings:
            response += "\n\n**Warnings:**\n"
            for w in warnings:
                response += f"- {w}\n"
        return response
