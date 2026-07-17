"""Tests for the mini-app view store: dataset revisions and exact
view-state replacement (Phase 0 of the Metric Lab overhaul)."""

from __future__ import annotations

import pytest

from cerebro_mcp.models.mini_app import DatasetStats
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.tools.visualization import mini_apps


@pytest.fixture(autouse=True)
def reset_state():
    mini_apps.reset_views_for_tests()
    yield
    mini_apps.reset_views_for_tests()


def _dataset(sql: str = "SELECT 1") -> CachedDataset:
    return CachedDataset(
        columns=["a"],
        column_types=["UInt8"],
        rows=[[1]],
        stats=DatasetStats(
            row_count=1, rows_returned=1, mode="exact_bounded",
            sample_source_rows=None, elapsed_seconds=0.0, warnings=[],
        ),
        sql=sql,
        database="dbt",
    )


def test_attach_dataset_bumps_revision():
    view_id = mini_apps.create_view("metric_lab", "t")
    mini_apps.attach_dataset(view_id, "primary", _dataset())
    record = mini_apps.get_view(view_id)
    assert record.dataset_revisions == {"primary": 1}
    # Same SQL, re-attached (e.g. forced rerun) — revision still bumps.
    mini_apps.attach_dataset(view_id, "primary", _dataset())
    assert mini_apps.get_view(view_id).dataset_revisions == {"primary": 2}


def test_replace_view_datasets_bumps_and_drops_removed_keys():
    view_id = mini_apps.create_view("metric_lab", "t")
    mini_apps.attach_dataset(view_id, "primary", _dataset())
    mini_apps.attach_dataset(view_id, "secondary", _dataset())
    mini_apps.replace_view_datasets(view_id, {"primary": _dataset()})
    record = mini_apps.get_view(view_id)
    assert set(record.datasets) == {"primary"}
    assert record.dataset_revisions == {"primary": 2}
    assert "secondary" not in record.dataset_revisions


def test_set_view_state_replaces_exactly():
    view_id = mini_apps.create_view("metric_lab", "t")
    mini_apps.patch_view_state(
        view_id,
        {"aggregate_config": {"x": "date"}, "provenance": {"secondary": {"a": 1}}},
    )
    mini_apps.set_view_state(view_id, {"mode": "loaded"})
    record = mini_apps.get_view(view_id)
    assert record.view_state == {"mode": "loaded"}


def test_patch_view_state_still_deep_merges():
    view_id = mini_apps.create_view("metric_lab", "t")
    mini_apps.set_view_state(view_id, {"chart": {"xField": "date"}, "mode": "loaded"})
    mini_apps.patch_view_state(view_id, {"chart": {"yField": "v"}})
    record = mini_apps.get_view(view_id)
    assert record.view_state["chart"] == {"xField": "date", "yField": "v"}
    assert record.view_state["mode"] == "loaded"


def test_set_view_state_unknown_view_returns_none():
    assert mini_apps.set_view_state("nope", {"mode": "loaded"}) is None
