"""Tests for dashboard_models.py — Pydantic model validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cerebro_mcp.models.dashboard import (
    DashboardBlueprint,
    MetricPlacement,
    QuerySpec,
    TabSpec,
)


# ---------------------------------------------------------------------------
# MetricPlacement
# ---------------------------------------------------------------------------


class TestMetricPlacement:
    def test_valid_span_syntax(self):
        mp = MetricPlacement(
            id="my_metric",
            grid_row="1",
            grid_column="1 / span 6",
        )
        assert mp.grid_column == "1 / span 6"

    def test_grid_column_overflow_raises(self):
        with pytest.raises(ValidationError, match="overflow"):
            MetricPlacement(
                id="overflow_metric",
                grid_row="1",
                grid_column="10 / span 5",
            )

    def test_plain_column_number(self):
        mp = MetricPlacement(id="m", grid_row="1", grid_column="6")
        assert mp.grid_column == "6"

    def test_span_only_syntax(self):
        mp = MetricPlacement(id="m", grid_row="1", grid_column="span 4")
        assert mp.grid_column == "span 4"


# ---------------------------------------------------------------------------
# QuerySpec
# ---------------------------------------------------------------------------


class TestQuerySpec:
    def test_valid_snake_case_id(self):
        qs = QuerySpec(
            id="my_metric_1",
            name="My Metric",
            chart_type="line",
            query="SELECT 1",
        )
        assert qs.id == "my_metric_1"

    def test_non_snake_case_id_raises(self):
        with pytest.raises(ValidationError, match="must match"):
            QuerySpec(
                id="MyMetric",
                name="Bad ID",
                chart_type="line",
                query="SELECT 1",
            )

    def test_invalid_chart_type_raises(self):
        with pytest.raises(ValidationError):
            QuerySpec(
                id="good_id",
                name="Bad Chart",
                chart_type="sparkline",
                query="SELECT 1",
            )


# ---------------------------------------------------------------------------
# TabSpec
# ---------------------------------------------------------------------------


class TestTabSpec:
    def test_empty_metrics_raises(self):
        with pytest.raises(ValidationError, match="too_short"):
            TabSpec(name="Empty Tab", order=1, metrics=[])


# ---------------------------------------------------------------------------
# DashboardBlueprint round-trip
# ---------------------------------------------------------------------------


class TestDashboardBlueprint:
    def test_json_round_trip(self):
        placement = MetricPlacement(
            id="m1", grid_row="1", grid_column="1 / span 12"
        )
        tab = TabSpec(name="Overview", order=0, metrics=[placement])
        query = QuerySpec(
            id="m1",
            name="Metric One",
            chart_type="area",
            query="SELECT date, value FROM t",
            x_field="date",
            y_field="value",
        )
        bp = DashboardBlueprint(
            dashboard_id="gnosis",
            tab=tab,
            queries=[query],
            dry_run=True,
        )

        json_str = bp.model_dump_json()
        restored = DashboardBlueprint.model_validate_json(json_str)

        assert restored.dashboard_id == bp.dashboard_id
        assert restored.tab.name == bp.tab.name
        assert restored.tab.order == bp.tab.order
        assert len(restored.queries) == len(bp.queries)
        assert restored.queries[0].id == bp.queries[0].id
        assert restored.queries[0].chart_type == bp.queries[0].chart_type
        assert restored.dry_run is True
