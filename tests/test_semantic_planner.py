"""Tests for enriched PlanningError messages in the semantic planner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cerebro_mcp.semantic.planner import (
    PlanningError,
    _find_time_spine_grain,
    _resolve_dimension_binding,
    _try_time_spine_upcast,
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


# ─── PR 5: time-spine upcast helpers ─────────────────────────────────


def _spine_snapshot():
    """Snapshot containing all three time spines + a daily-grain root
    model with a `date` column. Mirrors the production shape so the
    upcast helpers exercise the same lookup path they will in production.
    """
    return _make_snapshot(
        models={
            "dim_time_spine_daily": {
                "name": "dim_time_spine_daily",
                "dimensions": [
                    {"name": "day", "type": "time",
                     "type_params": {"time_granularity": "day"}}
                ],
            },
            "dim_time_spine_weekly": {
                "name": "dim_time_spine_weekly",
                "dimensions": [
                    {"name": "week", "type": "time",
                     "type_params": {"time_granularity": "week"}}
                ],
            },
            "dim_time_spine_monthly": {
                "name": "dim_time_spine_monthly",
                "dimensions": [
                    {"name": "month", "type": "time",
                     "type_params": {"time_granularity": "month"}}
                ],
            },
            "api_consensus_validators_active_daily": {
                "name": "api_consensus_validators_active_daily",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "date",
                     "type_params": {"time_granularity": "day"}}
                ],
            },
        },
        dimension_index={},
    )


class TestTimeSpineGrainLookup:
    def test_finds_weekly_grain_from_spine_model(self):
        snap = _spine_snapshot()
        assert _find_time_spine_grain(snap, "week") == "week"

    def test_finds_monthly_grain_from_spine_model(self):
        snap = _spine_snapshot()
        assert _find_time_spine_grain(snap, "month") == "month"

    def test_returns_none_for_non_spine_dimension(self):
        snap = _spine_snapshot()
        assert _find_time_spine_grain(snap, "sector") is None


class TestTimeSpineUpcast:
    def test_week_upcast_from_daily_source(self):
        snap = _spine_snapshot()
        binding = _try_time_spine_upcast(
            snap, "api_consensus_validators_active_daily", "week"
        )

        assert binding is not None
        assert binding["local"] is True
        assert binding["_synthesised"] == "time_spine_upcast"
        assert binding["dimension"]["_upcast_template"] == "toMonday({col})"
        # Source column is read from the root model's daily dimension's `expr`.
        assert binding["dimension"]["_upcast_from_col"] == "date"
        assert binding["dimension"]["_upcast_source_grain"] == "day"

    def test_month_upcast_from_daily_source(self):
        snap = _spine_snapshot()
        binding = _try_time_spine_upcast(
            snap, "api_consensus_validators_active_daily", "month"
        )

        assert binding is not None
        assert binding["dimension"]["_upcast_template"] == "toStartOfMonth({col})"
        assert binding["dimension"]["_upcast_from_col"] == "date"

    def test_no_upcast_when_target_not_a_spine_grain(self):
        snap = _spine_snapshot()
        # `sector` isn't a time-spine grain — upcast doesn't apply.
        assert _try_time_spine_upcast(
            snap, "api_consensus_validators_active_daily", "sector"
        ) is None

    def test_no_downcast(self):
        # Asking for `day` from a weekly-only source should NOT synthesise
        # — you can't recover daily granularity from weekly aggregates.
        snap = _make_snapshot(
            models={
                "dim_time_spine_daily": {
                    "dimensions": [{"name": "day", "type": "time",
                                    "type_params": {"time_granularity": "day"}}]
                },
                "weekly_only_root": {
                    "name": "weekly_only_root",
                    "dimensions": [
                        {"name": "week", "type": "time", "expr": "week",
                         "type_params": {"time_granularity": "week"}}
                    ],
                },
            },
        )
        assert _try_time_spine_upcast(snap, "weekly_only_root", "day") is None

    def test_no_upcast_when_root_has_no_time_column(self):
        snap = _make_snapshot(
            models={
                "dim_time_spine_weekly": {
                    "dimensions": [{"name": "week", "type": "time",
                                    "type_params": {"time_granularity": "week"}}]
                },
                "no_time_root": {
                    "name": "no_time_root",
                    "dimensions": [{"name": "sector", "type": "categorical"}],
                },
            },
        )
        assert _try_time_spine_upcast(snap, "no_time_root", "week") is None


class TestResolveDimensionBindingUpcastFallback:
    def test_resolve_falls_through_to_upcast_when_no_normal_path(self):
        # `week` is not on the root and not in dimension_index → without
        # upcast support this would raise PlanningError. With it, the
        # planner synthesises a derived binding.
        snap = _spine_snapshot()
        binding, _ = _resolve_dimension_binding(
            snap, "api_consensus_validators_active_daily", "week"
        )
        assert binding["local"] is True
        assert binding["_synthesised"] == "time_spine_upcast"
        assert binding["dimension"]["_upcast_template"] == "toMonday({col})"
