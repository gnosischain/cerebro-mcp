"""CerebroFastMCP — the FastMCP subclass whose overrides actually reach the wire.

Why a subclass and not a wrapper: FastMCP binds ``self.list_tools`` /
``self.call_tool`` / the resource and prompt methods into the low-level
request handlers eagerly in ``__init__`` (``_setup_handlers``, SDK
``fastmcp/server.py:302``). Assigning ``mcp.list_tools = wrapper`` afterwards
changes only the instance attribute — the wire keeps serving the original
bound method. That is exactly how ``install_app_only_filter`` shipped a
filter that passed its tests and did nothing in production (lesson
``wire-handler-binds-at-init``). Overriding on a subclass means the methods
bound at ``__init__`` ARE the filtered ones.

Three concerns live here, all driven by ``tools/tool_policy.py``:

1. **Wire visibility** (``list_tools``): the app-only drop and lean-core drop
   (same helpers ``install_app_only_filter`` used), plus the connector-profile
   allowlist, plus policy-derived MCP annotations for profile tools.
2. **Invocation enforcement** (``call_tool``): hiding a name is not a
   capability boundary — excluded tools and denied argument VALUES are
   rejected at call time. App-only tools stay callable (the ext-apps
   ``callTool`` path depends on that); profile exclusion beats app-only.
3. **Non-tool surface** (resources / templates / prompts): frozen allowlists
   under the connector profile; enumeration hiding AND direct-read rejection.
"""

from __future__ import annotations

from typing import Any, Sequence

from mcp.server.fastmcp import FastMCP
from mcp.types import (
    ContentBlock,
    Prompt as MCPPrompt,
    Resource as MCPResource,
    ResourceTemplate as MCPResourceTemplate,
    Tool as MCPTool,
    ToolAnnotations,
)
from pydantic import AnyUrl

from cerebro_mcp.tools import tool_policy


def _visibility_filters():
    """The app-only / lean-core predicates (lazy import: mini_apps pulls in
    the whole visualization package, which must not import at module load)."""
    from cerebro_mcp.tools.visualization.mini_apps import (
        _is_app_only_tool,
        _lean_core_hides,
    )

    return _is_app_only_tool, _lean_core_hides


def _policy_annotations(name: str) -> ToolAnnotations | None:
    policy = tool_policy.TOOL_POLICY.get(name)
    if policy is None:
        return None
    return ToolAnnotations(
        readOnlyHint=policy.read_only,
        destructiveHint=policy.destructive,
        idempotentHint=policy.idempotent,
        openWorldHint=policy.open_world,
    )


class CerebroFastMCP(FastMCP):
    """FastMCP whose visibility and policy enforcement reach the wire."""

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        is_app_only, lean_core_hides = _visibility_filters()
        out: list[MCPTool] = []
        for tool in tools:
            if is_app_only(tool):
                continue
            if lean_core_hides(tool):
                continue
            if not tool_policy.tool_visible(tool.name):
                continue
            if tool.annotations is None:
                annotations = _policy_annotations(tool.name)
                if annotations is not None:
                    tool = tool.model_copy(update={"annotations": annotations})
            out.append(tool)
        return out

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        # Raises ToolPolicyViolation for excluded tools / denied argument
        # values; the SDK converts the exception into an MCP error result.
        tool_policy.check_call_allowed(name, arguments)
        return await super().call_tool(name, arguments)

    async def list_resources(self) -> list[MCPResource]:
        resources = await super().list_resources()
        if not tool_policy.connector_profile_active():
            return resources
        return [
            r
            for r in resources
            if tool_policy.resource_uri_allowed(str(r.uri))
        ]

    async def list_resource_templates(self) -> list[MCPResourceTemplate]:
        templates = await super().list_resource_templates()
        if not tool_policy.connector_profile_active():
            return templates
        return [
            t
            for t in templates
            if tool_policy.template_allowed(t.uriTemplate)
        ]

    async def read_resource(self, uri: AnyUrl | str):
        if not tool_policy.resource_uri_allowed(str(uri)):
            raise ValueError(f"resource {uri!s} is not part of this profile")
        return await super().read_resource(uri)

    async def list_prompts(self) -> list[MCPPrompt]:
        prompts = await super().list_prompts()
        if not tool_policy.connector_profile_active():
            return prompts
        return [p for p in prompts if tool_policy.prompt_allowed(p.name)]

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None):
        if not tool_policy.prompt_allowed(name):
            raise ValueError(f"prompt {name!r} is not part of this profile")
        return await super().get_prompt(name, arguments)
