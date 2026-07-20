"""Renderer + normalization tests for agent-context schema v1 AND v2 payloads.

The MCP is the CONSUMER of the dbt-repo artifact and must accept both shapes:
v1 (scalar agents_md, direct-children downstream_count) and v2 (list agents_md,
downstream_direct_count + transitive downstream_api_models capped with
downstream_api_count). Normalization happens once at load; the renderer reads
only normalized fields.
"""

from __future__ import annotations

from cerebro_mcp.loaders.agent_context import normalize_artifact
from cerebro_mcp.tools.analytics.agent_knowledge import _format_contract


def v1_entry():
    return {
        "path": "models/execution/int_thing.sql",
        "materialized": "incremental",
        "incremental_strategy": "insert_overwrite",
        "strategy_expression": False,
        "partition_by": "toStartOfMonth(date)",
        "reads_this": False,
        "has_meta_full_refresh": False,
        "high_risk": True,
        "downstream_count": 2,
        "downstream_api_models": ["api_direct_child"],
        "contract": {
            "agents_md": "models/execution/AGENTS.md",
            "hazards": [{"id": "some-lesson", "status": "enforced", "title": "T"}],
            "rules": [],
            "validation": ["make check-fast"],
        },
    }


def v2_entry():
    return {
        "path": "models/execution/int_thing.sql",
        "materialized": "incremental",
        "incremental_strategy": "insert_overwrite",
        "strategy_expression": True,
        "partition_by": "toStartOfMonth(date)",
        "reads_this": False,
        "has_meta_full_refresh": True,
        "high_risk": True,
        "downstream_direct_count": 2,
        "downstream_api_models": ["api_a", "api_b"],
        "downstream_api_count": 25,
        "contract": {
            "agents_md": ["models/execution/AGENTS.md", "scripts/full_refresh/AGENTS.md"],
            "hazards": [],
            "rules": [],
            "validation": [],
        },
    }


def artifact_with(entry, version):
    return {"schema_version": version, "models": {"int_thing": entry}, "lessons": {}}


class TestNormalization:
    def test_v1_agents_md_scalar_becomes_list(self):
        data = normalize_artifact(artifact_with(v1_entry(), 1))
        assert data["models"]["int_thing"]["contract"]["agents_md"] == [
            "models/execution/AGENTS.md"
        ]

    def test_v1_lineage_fields_aliased(self):
        entry = normalize_artifact(artifact_with(v1_entry(), 1))["models"]["int_thing"]
        assert entry["downstream_direct_count"] == 2
        assert entry["downstream_api_count"] == 1

    def test_v2_passes_through_unchanged(self):
        entry = normalize_artifact(artifact_with(v2_entry(), 2))["models"]["int_thing"]
        assert entry["downstream_direct_count"] == 2
        assert entry["downstream_api_count"] == 25
        assert entry["contract"]["agents_md"] == [
            "models/execution/AGENTS.md", "scripts/full_refresh/AGENTS.md",
        ]

    def test_normalize_is_idempotent(self):
        once = normalize_artifact(artifact_with(v1_entry(), 1))
        twice = normalize_artifact(once)
        assert twice["models"]["int_thing"]["downstream_direct_count"] == 2


class TestRenderer:
    def test_renders_v1_normalized(self):
        entry = normalize_artifact(artifact_with(v1_entry(), 1))["models"]["int_thing"]
        text = _format_contract("int_thing", entry)
        assert "downstream: 2 direct child model(s)" in text
        assert "api_direct_child" in text
        assert "+0 more" not in text

    def test_renders_v2_with_transitive_cap(self):
        entry = normalize_artifact(artifact_with(v2_entry(), 2))["models"]["int_thing"]
        text = _format_contract("int_thing", entry)
        assert "downstream: 2 direct child model(s)" in text
        assert "api_a, api_b (+23 more)" in text
        assert "transitive" in text

    def test_renders_no_api_impact_quietly(self):
        entry = v2_entry()
        entry["downstream_api_models"] = []
        entry["downstream_api_count"] = 0
        entry = normalize_artifact(artifact_with(entry, 2))["models"]["int_thing"]
        text = _format_contract("int_thing", entry)
        assert "api marts affected" not in text
