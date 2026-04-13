"""Tests for enriched PlanningError messages in the semantic planner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cerebro_mcp.semantic_planner import (
    PlanningError,
    _resolve_dimension_binding,
)


def _make_snapshot(
    *,
    models: dict | None = None,
    metrics: dict | None = None,
    dimension_index: dict | None = None,
    graph=None,
    registry_hash: str = "test",
):
    """Build a minimal snapshot namespace for planner tests."""
    return SimpleNamespace(
        models=models or {},
        metrics=metrics or {},
        dimension_index=dimension_index or {},
        graph=graph,
        registry_hash=registry_hash,
    )


class TestDimensionErrorMessages:
    def test_unreachable_dimension_lists_available_local_dims(self):
        snapshot = _make_snapshot(
            models={
                "root_model": {
                    "dimensions": [
                        {"name": "day"},
                        {"name": "month"},
                        {"name": "token"},
                    ],
                }
            },
            dimension_index={},  # "sector" not in index at all
        )
        with pytest.raises(PlanningError, match="Available dimensions on 'root_model': day, month, token"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_unreachable_dimension_no_local_dims(self):
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": []}},
            dimension_index={},
        )
        with pytest.raises(PlanningError, match="'root_model' has no local dimensions"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_unapproved_providers_mentioned(self):
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": [{"name": "day"}]}},
            dimension_index={
                "sector": [
                    {
                        "provider_model": "model_b",
                        "semantic_status": "candidate",
                        "dimension": {"name": "sector"},
                    },
                ]
            },
        )
        with pytest.raises(PlanningError, match="Providers exist but are not approved: model_b"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_rejected_path_reasons_included(self):
        """When providers exist and are approved but the path is blocked,
        the rejection reason should appear in the error."""
        # We need to set up a scenario where find_safest_path raises PlanningError
        # Use a snapshot where the provider is approved but graph pathfinding fails
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": [{"name": "day"}]}},
            dimension_index={
                "sector": [
                    {
                        "provider_model": "model_c",
                        "semantic_status": "approved",
                        "dimension": {"name": "sector"},
                    },
                ]
            },
            graph={},  # Empty graph — pathfinding will fail
        )
        with pytest.raises(PlanningError, match="Rejected paths:"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_error_starts_with_dimension_name(self):
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": []}},
            dimension_index={},
        )
        with pytest.raises(PlanningError, match=r"^Dimension 'missing_dim' is not reachable"):
            _resolve_dimension_binding(snapshot, "root_model", "missing_dim")

    def test_local_dimension_resolves_without_error(self):
        snapshot = _make_snapshot(
            models={
                "root_model": {
                    "dimensions": [{"name": "day"}],
                }
            },
        )
        binding, rejected = _resolve_dimension_binding(snapshot, "root_model", "day")
        assert binding["name"] == "day"
        assert binding["local"] is True
        assert rejected is None
