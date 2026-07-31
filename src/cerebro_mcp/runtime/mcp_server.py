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


def _inject_analysis_id_schema(tool: MCPTool) -> MCPTool:
    """Advertise the analysis-handle contract WITHOUT touching real
    signatures (R9-audit P0-6: current tools do not accept analysis_id, so
    forwarding it would be a signature error — the call layer strips it).

    REQUIRED tools gain a required `analysis_id` property; MINTS tools an
    optional one (supply to REUSE a cycle, omit to start fresh)."""
    import copy

    policy = tool_policy.TOOL_POLICY.get(tool.name)
    if policy is None or policy.handle is tool_policy.Handle.NONE:
        return tool
    schema = copy.deepcopy(tool.inputSchema or {"type": "object", "properties": {}})
    props = schema.setdefault("properties", {})
    if "analysis_id" in props:
        return tool
    if policy.handle is tool_policy.Handle.REQUIRED:
        props["analysis_id"] = {
            "type": "string",
            "description": (
                "The analysis-cycle id returned by find or "
                "preflight_analytics_request. Required."
            ),
        }
        required = list(schema.get("required", []) or [])
        if "analysis_id" not in required:
            required.append("analysis_id")
        schema["required"] = required
    else:  # MINTS
        props["analysis_id"] = {
            "type": "string",
            "description": (
                "Optional: pass a previously returned analysis id to REUSE "
                "that cycle; omit to start a fresh one."
            ),
        }
    return tool.model_copy(update={"inputSchema": schema})


class CerebroFastMCP(FastMCP):
    """FastMCP whose visibility and policy enforcement reach the wire."""

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        is_app_only, lean_core_hides = _visibility_filters()
        profile_active = tool_policy.connector_profile_active()
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
            if profile_active:
                tool = _inject_analysis_id_schema(tool)
            out.append(tool)
        return out

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        # Raises ToolPolicyViolation for excluded tools / denied argument
        # values; the SDK converts the exception into an MCP error result.
        tool_policy.check_call_allowed(name, arguments)
        if not tool_policy.connector_profile_active():
            return await super().call_tool(name, arguments)
        return await self._call_with_handle(name, arguments)

    async def _call_with_handle(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Analysis-handle contract (R10 §4.1): the advertised schemas carry
        ``analysis_id`` (injected in list_tools); it is validated and
        STRIPPED here, bound to the request context, and the unmodified
        underlying tool runs — real signatures never change. MINTS tools
        (find/preflight) mint-or-reuse and ANNOTATE their result with the
        id; REQUIRED tools acquire/release with the refcount decrement in
        ``finally`` so cancellation can never pin a cycle."""
        from cerebro_mcp.runtime import analysis_registry as registry
        from cerebro_mcp.runtime.identity import get_current_owner

        policy = tool_policy.TOOL_POLICY.get(name)
        handle_rule = policy.handle if policy else tool_policy.Handle.NONE
        args = dict(arguments or {})
        supplied = args.pop("analysis_id", None)
        if supplied is not None and not isinstance(supplied, str):
            raise registry.AnalysisHandleError("analysis_id must be a string")
        owner = get_current_owner()

        if handle_rule is tool_policy.Handle.MINTS:
            handle, _reused = registry.mint_or_reuse(owner, supplied or None)
            token = registry.set_current_handle(handle)
            try:
                result = await super().call_tool(name, args)
            finally:
                registry.reset_current_handle(token)
            return self._annotate_analysis_id(result, handle)

        if handle_rule is tool_policy.Handle.REQUIRED:
            if not supplied:
                raise registry.AnalysisHandleError(
                    f"{name} requires analysis_id — call find or "
                    "preflight_analytics_request first and pass the id it "
                    "returns"
                )
            registry.acquire(owner, supplied)
            token = registry.set_current_handle(supplied)
            try:
                return await super().call_tool(name, args)
            finally:
                registry.reset_current_handle(token)
                registry.release(owner, supplied)

        # Handle.NONE: durable owner-keyed reads — handle-free by design
        # (they must survive handle expiry, R10 D8).
        return await super().call_tool(name, args)

    @staticmethod
    def _annotate_analysis_id(result, handle: str):
        """Attach the minted/reused id to a find/preflight result.

        MUST preserve the SDK's return SHAPE. ``FastMCP.call_tool`` returns
        one of three things and the low-level handler dispatches on type
        (``lowlevel/server.py``): a 2-tuple ``(unstructured, structured)``
        when the tool declares an outputSchema, a plain dict for
        structured-only, or an iterable of content blocks. An earlier
        version did ``list(result) + [TextContent(...)]``, which flattened
        the 2-tuple into a 3-element list — the structured half was lost and
        every outputSchema-bearing tool (``find`` included) failed with
        "outputSchema defined but no structured output returned". Annotate
        WITHIN the shape, never across it.
        """
        from mcp.types import TextContent

        if isinstance(result, tuple) and len(result) == 2:
            unstructured, structured = result
            if isinstance(structured, dict):
                structured = {**structured, "analysis_id": handle}
            return unstructured, structured
        if isinstance(result, dict):
            return {**result, "analysis_id": handle}
        try:
            return list(result) + [
                TextContent(type="text", text=f"analysis_id: {handle}")
            ]
        except TypeError:  # pragma: no cover — non-iterable: leave untouched
            return result

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
