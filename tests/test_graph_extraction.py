"""Tests for the pure graph extraction/validation module (WS2)."""

from __future__ import annotations

import pytest

from cerebro_mcp.semantic import graph_profiles
from cerebro_mcp.semantic.graph_extraction import (
    GraphExtractionError,
    extract_graph_profile,
    validate_graph_meta,
)


def _model(**graph_overrides):
    graph = {
        "enabled": True,
        "profile": "circles_trust",
        "source_column": "truster",
        "target_column": "trustee",
        "source_kind": "circles_avatar",
        "target_kind": "circles_avatar",
        "directed": True,
        "time_column": "valid_from",
    }
    graph.update(graph_overrides)
    return {
        "name": "api_execution_circles_v2_trust_relations_current",
        "relation_name": "api_execution_circles_v2_trust_relations_current",
        "module": "Circles",
        "description": "trust",
        "semantic_status": "approved",
        "quality_tier": "approved",
        "columns": {"truster": {}, "trustee": {}, "valid_from": {}},
        "semantic": {"meta": {"question_synonyms": ["who trusts whom"], "graph": graph}},
    }


def test_extract_returns_none_without_enabled_block():
    assert extract_graph_profile("m", {"semantic": {"meta": {}}}) is None
    assert extract_graph_profile("m", {"semantic": {"meta": {"graph": {"enabled": False}}}}) is None


def test_extract_builds_profile():
    p = extract_graph_profile("m", _model())
    assert p is not None
    assert p.profile == "circles_trust"
    assert p.source_kind == "circles_avatar"
    assert p.time_aware is True
    # evidence columns default to source/target columns
    assert p.evidence_source_column == "truster"
    assert p.evidence_target_column == "trustee"


def test_extract_raises_on_missing_required():
    bad = _model()
    del bad["semantic"]["meta"]["graph"]["target_kind"]
    with pytest.raises(GraphExtractionError) as exc:
        extract_graph_profile("m", bad)
    assert "target_kind" in str(exc.value)


def test_extract_strips_control_keys_from_default_filters():
    p = extract_graph_profile("m", _model(default_filters={"limit": 500, "invited_by": "valid_address"}))
    assert p.default_filters == {"invited_by": "valid_address"}


def test_discover_profiles_uses_extractor_roundtrip():
    # The live-discovery path must equal extracting each model directly (1:1).
    models = {"api_execution_circles_v2_trust_relations_current": _model()}
    via_discover = graph_profiles.discover_profiles(models=models)
    via_extract = sorted(
        (
            p
            for name, model in models.items()
            if (p := extract_graph_profile(name, model)) is not None
        ),
        key=lambda p: (p.module, p.profile),
    )
    assert [p.profile for p in via_discover] == [p.profile for p in via_extract]
    assert via_discover == via_extract


def test_discover_profiles_skips_malformed():
    # An enabled-but-malformed block is skipped (logged), not fatal.
    models = {
        "api_execution_circles_v2_trust_relations_current": _model(),
        "broken": {"semantic": {"meta": {"graph": {"enabled": True, "profile": "broken"}}}},
    }
    ids = {p.profile for p in graph_profiles.discover_profiles(models=models)}
    assert "broken" not in ids
    assert "circles_trust" in ids


def test_validate_graph_meta_unknown_kind_is_error():
    model = _model(source_kind="not_a_kind")
    graph = model["semantic"]["meta"]["graph"]
    issues = validate_graph_meta(
        model["name"], graph, {model["name"]: model}, allowed_kinds={"circles_avatar"}
    )
    codes = {i["code"] for i in issues if i["severity"] == "error"}
    assert "graph_meta_unknown_kind" in codes


def test_validate_graph_meta_unknown_column_is_error():
    model = _model(source_column="nonexistent")
    graph = model["semantic"]["meta"]["graph"]
    issues = validate_graph_meta(model["name"], graph, {model["name"]: model})
    codes = {i["code"] for i in issues}
    assert "graph_meta_unknown_column" in codes


def test_validate_graph_meta_clean_block_no_errors():
    model = _model()
    graph = model["semantic"]["meta"]["graph"]
    issues = validate_graph_meta(
        model["name"], graph, {model["name"]: model}, allowed_kinds={"circles_avatar"}
    )
    assert [i for i in issues if i["severity"] == "error"] == []
