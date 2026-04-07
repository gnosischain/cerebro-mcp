from __future__ import annotations

import inspect
import logging
import yaml
from pathlib import Path
from typing import Any

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.custom_tool_models import (
    CustomToolDefinition,
    CustomToolsConfig,
    CH_TYPE_TO_PYTHON,
)
from cerebro_mcp.tool_output import build_query_summary

logger = logging.getLogger(__name__)


def register_custom_query_tools(mcp, ch: ClickHouseManager):
    """Load custom_tools.yaml and dynamically register parameterized MCP tools.

    Feature-gated by CUSTOM_TOOLS_ENABLED.
    """
    if not settings.CUSTOM_TOOLS_ENABLED or not settings.CUSTOM_TOOLS_PATH:
        return

    config_path = Path(settings.CUSTOM_TOOLS_PATH)
    if not config_path.exists():
        logger.warning("Custom tools config not found: %s", config_path)
        return

    config = _load_config(config_path)

    # Also register a listing tool
    @mcp.tool()
    def list_custom_tools() -> str:
        """List all available custom parameterized query tools.

        These are pre-built, peer-reviewed SQL templates that accept typed
        parameters. Always prefer these over raw execute_query for common
        domain questions.
        """
        lines = ["# Custom Query Tools\n"]
        lines.append("| Tool | Description | Parameters |")
        lines.append("|------|-------------|------------|")
        for t in config.tools:
            params = ", ".join(
                f"`{k}` ({v.type}{'*' if v.default is None else ''})"
                for k, v in t.parameters.items()
            )
            lines.append(f"| `{t.name}` | {t.description[:80]} | {params} |")
        lines.append(f"\nTotal: {len(config.tools)} custom tools")
        lines.append("\n*= required parameter")
        return "\n".join(lines)

    for tool_def in config.tools:
        _register_one_tool(mcp, ch, tool_def)

    logger.info("Registered %d custom query tools from %s", len(config.tools), config_path)


def _load_config(path: Path) -> CustomToolsConfig:
    """Load and validate custom tools YAML config."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return CustomToolsConfig(**raw)


def _register_one_tool(mcp, ch: ClickHouseManager, tool_def: CustomToolDefinition):
    """Register a single custom tool using closure factory + dynamic signature."""

    # Build the actual tool function via closure
    def _make_fn():
        # Capture tool_def in closure
        _tool = tool_def

        def tool_fn(**kwargs) -> str:
            # Resolve parameters: apply defaults and validate required
            params: dict[str, Any] = {}
            for pname, pspec in _tool.parameters.items():
                val = kwargs.get(pname)
                if val is None and pspec.default is not None:
                    val = pspec.default
                if val is None:
                    return f"Error: required parameter '{pname}' not provided"
                params[pname] = val

            try:
                # Track the query execution for session state
                from cerebro_mcp.tools.session_state import state

                state.record_execute_query(_tool.sql)

                executed = ch.run_query(
                    _tool.sql,
                    database=_tool.database,
                    parameters=params,
                    requested_max_rows=200,
                    audience="tool",
                )
                result = ch.build_query_result(executed, max_rows=200)
                return build_query_summary(
                    columns=result.columns,
                    rows=result.rows,
                    row_count=result.row_count,
                    rows_returned=result.rows_returned,
                    elapsed_seconds=result.elapsed_seconds,
                    database=result.database,
                    sql=result.sql,
                    warnings=result.warnings,
                    extra_notes=[f"Custom tool: {_tool.name}"],
                )
            except Exception as e:
                return f"Error executing {_tool.name}: {e}"

        return tool_fn

    fn = _make_fn()
    fn.__name__ = tool_def.name
    fn.__qualname__ = tool_def.name
    fn.__doc__ = tool_def.description

    # Build proper type annotations for FastMCP schema generation
    annotations: dict[str, Any] = {}
    params_list: list[inspect.Parameter] = []
    for pname, pspec in tool_def.parameters.items():
        py_type = CH_TYPE_TO_PYTHON.get(pspec.type, str)
        annotations[pname] = py_type
        if pspec.default is not None:
            params_list.append(
                inspect.Parameter(
                    pname,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=pspec.default,
                    annotation=py_type,
                )
            )
        else:
            params_list.append(
                inspect.Parameter(
                    pname,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=py_type,
                )
            )

    annotations["return"] = str
    fn.__annotations__ = annotations
    fn.__signature__ = inspect.Signature(params_list, return_annotation=str)

    # Register with FastMCP
    mcp.tool()(fn)
