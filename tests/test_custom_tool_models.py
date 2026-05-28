"""Tests for custom_tool_models.py — MCP Toolbox Pydantic models."""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from cerebro_mcp.models.custom_tool import (
    CH_TYPE_TO_PYTHON,
    CustomToolDefinition,
    CustomToolParameter,
    CustomToolsConfig,
)


# ---------------------------------------------------------------------------
# CustomToolDefinition validation
# ---------------------------------------------------------------------------


class TestCustomToolDefinition:
    def test_valid_definition(self):
        tool = CustomToolDefinition(
            name="get_token_volume",
            description="Get volume for a token",
            parameters={
                "token": CustomToolParameter(type="String", description="Token symbol"),
            },
            sql="SELECT sum(volume) FROM t WHERE token = {token:String}",
        )
        assert tool.name == "get_token_volume"
        assert "token" in tool.parameters

    def test_sql_placeholder_without_parameter_raises(self):
        with pytest.raises(
            ValidationError, match="undefined parameters"
        ):
            CustomToolDefinition(
                name="bad_tool",
                description="Missing param def",
                parameters={},
                sql="SELECT * FROM t WHERE token = {token:String}",
            )

    def test_unused_parameter_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            CustomToolDefinition(
                name="extra_param",
                description="Has an unused param",
                parameters={
                    "token": CustomToolParameter(type="String"),
                    "unused": CustomToolParameter(type="UInt64"),
                },
                sql="SELECT * FROM t WHERE token = {token:String}",
            )
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "unused" in str(user_warnings[0].message)

    def test_invalid_name_raises(self):
        with pytest.raises(ValidationError, match="snake_case"):
            CustomToolDefinition(
                name="BadName",
                description="Invalid name",
                sql="SELECT 1",
            )


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


class TestParameterDefaults:
    def test_parameter_with_default_is_optional(self):
        param = CustomToolParameter(type="String", default="all")
        assert param.default == "all"

    def test_parameter_without_default_is_required(self):
        param = CustomToolParameter(type="String")
        assert param.default is None


# ---------------------------------------------------------------------------
# CH_TYPE_TO_PYTHON mapping
# ---------------------------------------------------------------------------


class TestChTypeToPython:
    @pytest.mark.parametrize(
        "ch_type, py_type",
        [
            ("String", str),
            ("UInt64", int),
            ("Float64", float),
            ("Date", str),
            ("DateTime", str),
            ("Int32", int),
        ],
    )
    def test_type_mapping(self, ch_type, py_type):
        assert CH_TYPE_TO_PYTHON[ch_type] is py_type


# ---------------------------------------------------------------------------
# CustomToolsConfig round-trip from YAML
# ---------------------------------------------------------------------------


class TestCustomToolsConfig:
    def test_round_trip_from_yaml(self):
        import yaml

        yaml_str = """\
tools:
  - name: get_bridge_volume
    description: Get bridge volume by token
    parameters:
      token:
        type: String
        description: Token symbol
      days:
        type: UInt32
        description: Lookback days
        default: 30
    database: dbt
    sql: >-
      SELECT sum(volume) AS vol
      FROM bridge_transfers
      WHERE token = {token:String}
        AND date >= today() - {days:UInt32}
"""
        raw = yaml.safe_load(yaml_str)
        config = CustomToolsConfig(**raw)

        assert len(config.tools) == 1
        tool = config.tools[0]
        assert tool.name == "get_bridge_volume"
        assert tool.parameters["token"].type == "String"
        assert tool.parameters["days"].default == 30
        assert tool.database == "dbt"
