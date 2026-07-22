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


def _dataset(
    sql: str = "SELECT 1", rows: list[list[object]] | None = None
) -> CachedDataset:
    values = rows if rows is not None else [[1]]
    return CachedDataset(
        columns=["a"],
        column_types=["UInt8"],
        rows=values,
        stats=DatasetStats(
            row_count=len(values), rows_returned=len(values), mode="exact_bounded",
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


def test_dataset_pages_honor_size_and_echo_revision():
    view_id = mini_apps.create_view("metric_lab", "t")
    mini_apps.attach_dataset(
        view_id, "primary", _dataset(rows=[[index] for index in range(12)])
    )

    page = mini_apps.get_view_dataset_page(
        view_id,
        "primary",
        page_size=5,
        dataset_revision=1,
    )

    assert page["rows"] == [[0], [1], [2], [3], [4]]
    assert page["page_size"] == 5
    assert page["dataset_revision"] == 1
    assert page["next_page_token"] == "offset:5"


def test_dataset_page_rejects_cross_revision_hydration():
    view_id = mini_apps.create_view("metric_lab", "t")
    mini_apps.attach_dataset(view_id, "primary", _dataset(rows=[[1], [2]]))
    mini_apps.attach_dataset(view_id, "primary", _dataset(rows=[[3], [4]]))

    with pytest.raises(ValueError, match="Dataset revision changed"):
        mini_apps.get_view_dataset_page(
            view_id,
            "primary",
            page_size=1,
            dataset_revision=1,
        )


@pytest.mark.parametrize(
    "token", ["garbage", "offset:", "offset:-1", "offset:1x", "other:1"]
)
def test_dataset_page_rejects_malformed_tokens(token):
    view_id = mini_apps.create_view("metric_lab", "t")
    mini_apps.attach_dataset(view_id, "primary", _dataset(rows=[[1], [2]]))
    with pytest.raises(ValueError, match="Invalid dataset page token"):
        mini_apps.get_view_dataset_page(
            view_id,
            "primary",
            page_token=token,
            dataset_revision=1,
        )


def test_atomic_view_commit_rejects_stale_request_per_channel():
    view_id = mini_apps.create_view("graph_explorer", "t")
    assert mini_apps.commit_view_update(
        view_id,
        request_channel="transactions.discovery",
        request_id=2,
        datasets={"tx_list": _dataset(rows=[[2]])},
        state_patch={"transactions": {"status": "ready"}},
    )
    assert not mini_apps.commit_view_update(
        view_id,
        request_channel="transactions.discovery",
        request_id=1,
        datasets={"tx_list": _dataset(rows=[[1]])},
        state_patch={"transactions": {"status": "stale"}},
    )
    # An independent receipt channel remains free to commit request 1.
    assert mini_apps.commit_view_update(
        view_id,
        request_channel="transactions.receipt",
        request_id=1,
        datasets={"tx_context": _dataset(rows=[[3]])},
    )

    snapshot = mini_apps.snapshot_view(view_id)
    assert snapshot is not None
    assert snapshot.datasets["tx_list"].rows == [[2]]
    assert snapshot.datasets["tx_context"].rows == [[3]]
    assert snapshot.view_state["transactions"]["status"] == "ready"
    assert snapshot.request_revisions == {
        "transactions.discovery": 2,
        "transactions.receipt": 1,
    }


def test_begin_view_request_blocks_an_older_commit_before_newer_finishes():
    view_id = mini_apps.create_view("graph_explorer", "t")
    assert mini_apps.begin_view_request(
        view_id, request_channel="money", request_id=1
    )
    assert mini_apps.begin_view_request(
        view_id, request_channel="money", request_id=2
    )

    assert not mini_apps.commit_view_update(
        view_id,
        request_channel="money",
        request_id=1,
        state_patch={"flows": {"scope_id": "old"}},
    )
    snapshot = mini_apps.snapshot_view(view_id)
    assert snapshot is not None
    assert snapshot.view_state.get("flows") is None
    assert snapshot.request_revisions["money"] == 2


def test_atomic_commit_can_guard_a_shared_subject_revision():
    view_id = mini_apps.create_view("graph_explorer", "t")
    assert mini_apps.begin_view_request(
        view_id, request_channel="transactions", request_id=4
    )
    assert not mini_apps.commit_view_update(
        view_id,
        request_channel="transactions.receipt",
        request_id=3,
        guard_channels=["transactions"],
        state_patch={"transactions": {"selected_hash": "stale"}},
    )


def test_legacy_request_gets_effective_revision_and_unreserved_zero_is_stale():
    view_id = mini_apps.create_view("graph_explorer", "t")
    first = mini_apps.reserve_view_request(
        view_id, request_channel="timeline", request_id=0
    )
    second = mini_apps.reserve_view_request(
        view_id, request_channel="timeline", request_id=0
    )
    assert (first, second) == (1, 2)
    assert not mini_apps.commit_view_update(
        view_id,
        request_channel="timeline",
        request_id=0,
        state_patch={"timeline": {"status": "legacy-stale"}},
    )
    assert mini_apps.commit_view_update(
        view_id,
        request_channel="timeline",
        request_id=second,
        state_patch={"timeline": {"status": "ready"}},
    )


def test_view_snapshot_is_detached_from_future_state_mutation():
    view_id = mini_apps.create_view("graph_explorer", "t")
    mini_apps.set_view_state(view_id, {"nested": {"value": 1}})
    before = mini_apps.snapshot_view(view_id)
    mini_apps.patch_view_state(view_id, {"nested": {"value": 2}})

    assert before is not None
    assert before.view_state == {"nested": {"value": 1}}
