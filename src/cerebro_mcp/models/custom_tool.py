"""Pydantic v2 models for the MCP Toolbox custom tools system."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Helper constant: ClickHouse type -> Python type
# ---------------------------------------------------------------------------

CH_TYPE_TO_PYTHON: dict[str, type] = {
    "String": str,
    "UInt8": int,
    "UInt16": int,
    "UInt32": int,
    "UInt64": int,
    "Int8": int,
    "Int16": int,
    "Int32": int,
    "Int64": int,
    "Float32": float,
    "Float64": float,
    "Date": str,
    "DateTime": str,
}

# Regex to match {param_name:Type} placeholders in SQL templates
_PLACEHOLDER_RE = re.compile(r"\{(\w+):(\w+)\}")

# Regex to validate snake_case tool names
_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CustomToolParameter(BaseModel):
    """A single parameter for a custom tool."""

    type: str = Field(..., description="ClickHouse type (String, UInt32, Float64, Date, etc.)")
    description: str = Field(default="", description="Human-readable description of the parameter")
    default: Any = Field(default=None, description="Default value. If set (not None), the parameter is optional; otherwise required.")


class CustomToolDefinition(BaseModel):
    """Definition of a single custom tool backed by a SQL template."""

    name: str = Field(..., description="Tool name in snake_case")
    description: str = Field(..., description="Human-readable description of what this tool does")
    parameters: dict[str, CustomToolParameter] = Field(default_factory=dict)
    database: str = Field(default="dbt", description="Target ClickHouse database")
    sql: str = Field(..., description="SQL template with {param_name:Type} placeholders")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _SNAKE_CASE_RE.match(v):
            raise ValueError(
                f"Tool name must be snake_case matching ^[a-z][a-z0-9_]*$, got: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_sql_params(self) -> CustomToolDefinition:
        """Ensure SQL placeholders and parameter definitions are consistent."""
        placeholders = _PLACEHOLDER_RE.findall(self.sql)
        placeholder_names = {name for name, _ in placeholders}
        param_names = set(self.parameters.keys())

        # Every placeholder must have a matching parameter definition
        missing = placeholder_names - param_names
        if missing:
            raise ValueError(
                f"SQL placeholders reference undefined parameters: {sorted(missing)}"
            )

        # Warn about parameters defined but never used in the SQL
        unused = param_names - placeholder_names
        if unused:
            import warnings

            warnings.warn(
                f"Parameters defined but not used in SQL template: {sorted(unused)}",
                UserWarning,
                stacklevel=2,
            )

        return self


class CustomToolsConfig(BaseModel):
    """Top-level configuration holding all custom tool definitions."""

    tools: list[CustomToolDefinition] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Number verification models
# ---------------------------------------------------------------------------


class VerificationClaim(BaseModel):
    """A single numerical claim to verify."""

    label: str = Field(..., description="What the number represents, e.g. 'net GNO inflow'")
    value: float = Field(..., description="The computed number to verify")
    formula: str = Field(default="", description="Arithmetic expression, e.g. 'received - sent'")
    components: dict[str, float] = Field(
        default_factory=dict,
        description="Named values used in the formula, e.g. {'received': 9352.5, 'sent': 9002.9}",
    )
    check_query: str = Field(default="", description="Optional SQL for independent cross-reference")
    check_database: str = Field(default="dbt", description="Database for check_query")
    tolerance_pct: float = Field(default=0.01, description="Tolerance % for arithmetic (default 0.01%)")
