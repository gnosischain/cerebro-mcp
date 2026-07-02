"""Tests for catalog-backed profile reconstruction + fallback (WS4, mcp side)."""

from __future__ import annotations

from dataclasses import asdict

import cerebro_mcp.loaders.semantic as sem
from cerebro_mcp.semantic.graph_extraction import (
    extract_graph_profile,
    profile_from_catalog_row,
)
from cerebro_mcp.semantic.graph_profiles import discover_profiles


def _model():
    return {
        "name": "m_trust",
        "relation_name": "m_trust",
        "module": "Circles",
        "description": "trust",
        "semantic_status": "approved",
        "quality_tier": "approved",
        "semantic_source_file": "semantic/authoring/Circles/semantic_models.yml",
        "semantic": {
            "meta": {
                "question_synonyms": ["who trusts whom"],
                "graph": {
                    "enabled": True,
                    "profile": "circles_trust",
                    "source_column": "truster",
                    "target_column": "trustee",
                    "source_kind": "circles_avatar",
                    "target_kind": "circles_avatar",
                    "directed": True,
                    "time_column": "valid_from",
                },
            }
        },
    }


def _row(model=None):
    return asdict(extract_graph_profile("m_trust", model or _model()))


def test_profile_from_catalog_row_roundtrip():
    original = extract_graph_profile("m_trust", _model())
    reconstructed = profile_from_catalog_row(asdict(original))
    assert reconstructed == original


def test_profile_from_catalog_row_handles_field_drift():
    # An older/newer catalog row missing a field falls back to dataclass default;
    # an unknown extra field is ignored. Neither raises (D4).
    row = _row()
    row.pop("semantic_source_file")
    row["future_field"] = "ignored"
    row["question_synonyms"] = list(row["question_synonyms"])  # JSON gives a list
    p = profile_from_catalog_row(row)
    assert p.profile == "circles_trust"
    assert p.semantic_source_file == ""
    assert p.question_synonyms == ("who trusts whom",)


def _registry(manifest_hash="abc"):
    return {"metadata": {"manifest_hash": manifest_hash}}


def _catalog(manifest_hash="abc", schema_version=1, profiles=None):
    return {
        "metadata": {
            "schema_version": schema_version,
            "registry_manifest_hash": manifest_hash,
            "graph_catalog_hash": "cat-hash-123",
        },
        "profiles": profiles if profiles is not None else {"circles_trust": _row()},
    }


def _resolve(registry, models, catalog):
    return sem.SemanticRuntime._resolve_graph_profiles(
        registry, models, catalog, discover_profiles, profile_from_catalog_row
    )


def test_uses_catalog_when_manifest_matches():
    profiles, cat_hash = _resolve(_registry("abc"), {}, _catalog("abc"))
    assert [p.profile for p in profiles] == ["circles_trust"]
    assert cat_hash == "cat-hash-123"


def test_falls_back_on_manifest_mismatch():
    models = {"m_trust": _model()}
    profiles, cat_hash = _resolve(_registry("NEW"), models, _catalog("OLD"))
    # live discovery off models, no catalog hash
    assert [p.profile for p in profiles] == ["circles_trust"]
    assert cat_hash == ""


def test_falls_back_on_unsupported_schema_version():
    models = {"m_trust": _model()}
    profiles, cat_hash = _resolve(_registry("abc"), models, _catalog("abc", schema_version=99))
    assert cat_hash == ""
    assert [p.profile for p in profiles] == ["circles_trust"]


def test_falls_back_when_catalog_absent():
    models = {"m_trust": _model()}
    profiles, cat_hash = _resolve(_registry("abc"), models, None)
    assert cat_hash == ""
    assert [p.profile for p in profiles] == ["circles_trust"]


def test_falls_back_on_empty_profiles():
    models = {"m_trust": _model()}
    profiles, cat_hash = _resolve(_registry("abc"), models, _catalog("abc", profiles={}))
    assert cat_hash == ""
    assert [p.profile for p in profiles] == ["circles_trust"]


def test_falls_back_on_corrupt_profile_row():
    models = {"m_trust": _model()}
    # A row missing required positional args would raise in reconstruction.
    bad = _catalog("abc", profiles={"x": {"not": "a profile"}})
    profiles, cat_hash = _resolve(_registry("abc"), models, bad)
    assert cat_hash == ""
    assert [p.profile for p in profiles] == ["circles_trust"]
