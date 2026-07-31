"""Grant-closure tests (connector plan R10 C4) + manifest activation pin."""

from __future__ import annotations

import pytest

from cerebro_mcp.connector_grants import (
    GrantClosureError,
    compute_grant_closure,
    render_grant_script,
)


def _manifest() -> dict:
    """Synthetic raw manifest: mart -> stg view -> source, plus a privacy
    branch and a narrowing view."""

    def model(name, materialized, schema="dbt", tags=(), meta=None):
        return {
            "resource_type": "model",
            "name": name,
            "schema": schema,
            "alias": name,
            "tags": list(tags),
            "meta": meta or {},
            "config": {"materialized": materialized},
        }

    def source(name, schema):
        return {
            "resource_type": "source",
            "name": name,
            "schema": schema,
            "identifier": name,
        }

    return {
        "nodes": {
            "model.p.mart_ok": model("mart_ok", "table"),
            "model.p.stg_pass": model("stg_pass", "view"),
            "model.p.mart_on_source": model("mart_on_source", "table"),
            "model.p.bridge": model(
                "bridge", "table", tags=("internal_only",)
            ),
            "model.p.mart_on_bridge": model("mart_on_bridge", "table"),
            "model.p.mart_meta_hidden": model(
                "mart_meta_hidden", "table", meta={"expose_to_mcp": False}
            ),
        },
        "sources": {
            "source.p.raw.specs": source("specs", "consensus"),
            "source.p.raw.secret_feed": source("secret_feed", "crawlers_data"),
        },
        "parent_map": {
            "model.p.mart_ok": ["model.p.stg_pass"],
            "model.p.stg_pass": ["source.p.raw.specs"],
            "model.p.mart_on_source": ["source.p.raw.secret_feed"],
            "model.p.mart_on_bridge": ["model.p.bridge"],
            "model.p.bridge": [],
            "model.p.mart_meta_hidden": [],
        },
    }


def test_privacy_closure_fails_closed():
    """A model over an internal-only ancestor is EXCLUDED, never granted."""
    result = compute_grant_closure(
        _manifest(),
        approved_sources={"consensus.specs"},
    )
    assert "mart_on_bridge" in result.excluded
    assert "bridge" in result.excluded  # the tagged model itself
    assert "mart_meta_hidden" in result.excluded  # meta.expose_to_mcp: false
    assert all("bridge" not in rel for rel in result.granted)


def test_unapproved_source_lands_in_review_not_grants():
    result = compute_grant_closure(
        _manifest(),
        approved_sources={"consensus.specs"},
    )
    assert "mart_on_source" in result.review_required
    assert result.review_required["mart_on_source"] == [
        "source:crawlers_data.secret_feed"
    ]
    assert "crawlers_data.secret_feed" not in result.granted


def test_view_parents_are_granted_with_the_view():
    """An invoker-executed view cannot read what the caller cannot, so a
    granted model's closure necessarily grants its parents.

    This documents a REAL residual risk rather than pretending to solve it:
    a caller can query the wider parent directly and bypass a narrowing
    view. The earlier per-view "passthrough approval" flag could not express
    the distinction — every view's parents are already in its own root's
    closure — so it merely blocked marts while granting their inputs. The
    remedy for an unacceptable case is a DEFINER view or a connector-safe
    materialization, decided per case.
    """
    result = compute_grant_closure(
        _manifest(), approved_sources={"consensus.specs"}
    )
    assert "mart_ok" not in result.review_required
    # the view AND its source parent are both granted, together
    assert "dbt.stg_pass" in result.granted
    assert "consensus.specs" in result.granted


def test_approved_chain_grants_physical_relations():
    result = compute_grant_closure(
        _manifest(),
        approved_sources={"consensus.specs"},
    )
    assert "dbt.mart_ok" in result.granted
    assert "dbt.stg_pass" in result.granted
    assert "consensus.specs" in result.granted


def test_missing_lineage_node_is_a_hard_error():
    manifest = _manifest()
    manifest["parent_map"]["model.p.mart_ok"].append("model.p.ghost")
    with pytest.raises(GrantClosureError, match="ghost"):
        compute_grant_closure(
            manifest,
            approved_sources={"consensus.specs"},
        )


def test_missing_schema_is_a_hard_error():
    manifest = _manifest()
    del manifest["nodes"]["model.p.mart_ok"]["schema"]
    with pytest.raises(GrantClosureError, match="schema"):
        compute_grant_closure(
            manifest,
            approved_sources={"consensus.specs"},
        )


def test_render_emits_staged_identity_and_pin():
    result = compute_grant_closure(
        _manifest(),
        approved_sources={"consensus.specs"},
    )
    script = render_grant_script(result, user="cerebro_connector_v1", manifest_sha="ab" * 32)
    assert "CREATE USER IF NOT EXISTS cerebro_connector_v1" in script
    assert "GRANT SELECT ON `dbt`.`mart_ok` TO cerebro_connector_v1;" in script
    assert ("ab" * 32) in script
    assert "REVIEW REQUIRED" in script  # the worklist is part of the artifact


# ---------------------------------------------------------------------------
# Manifest activation pin (loaders/manifest.py)
# ---------------------------------------------------------------------------


def test_manifest_pin_violation_logic(monkeypatch):
    from cerebro_mcp.config import settings
    from cerebro_mcp.loaders.manifest import ManifestLoader

    monkeypatch.setattr(settings, "MCP_EXPECTED_MANIFEST_SHA256", "aa" * 32)
    assert ManifestLoader._manifest_pin_violation("aa" * 32) is None
    assert ManifestLoader._manifest_pin_violation(("AA" * 32)) is None  # case
    violation = ManifestLoader._manifest_pin_violation("bb" * 32)
    assert violation and "unreconciled" in violation

    monkeypatch.setattr(settings, "MCP_EXPECTED_MANIFEST_SHA256", "")
    assert ManifestLoader._manifest_pin_violation("bb" * 32) is None
