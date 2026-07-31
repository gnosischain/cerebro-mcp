"""Relation-boundary tests: qualified-name allowlists and table functions.

Two layers, both widened guards (never parallel ones):

1. ``validate_query`` (safety.py) — all profiles: a database-qualified
   reference outside ``ALLOWED_DATABASES`` is rejected (GDPR-audit M1: the
   deny list was checked by bare table name and the connection database only,
   so ``execute_query(sql="SELECT ... FROM mixpanel_ga.<t>", database="dbt")``
   passed every application-layer control). Table-function coverage widened
   beyond the legacy eight (remoteSecure, sqlite, cluster*, merge, dict*, …).
2. Connector profile (``team_analytics_v1``) — the caller-SQL surface narrows
   to ``dbt`` plus the explicit ``consensus.specs`` grant; the connection
   database itself narrows to ``dbt``.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query, validate_relation_access
from cerebro_mcp.tools import tool_policy


@pytest.fixture
def connector_profile(monkeypatch):
    monkeypatch.setattr(
        settings, "MCP_SURFACE_PROFILE", tool_policy.PROFILE_TEAM_ANALYTICS_V1
    )


# ---------------------------------------------------------------------------
# Layer 1 — all profiles (M1 fix + table-function widening)
# ---------------------------------------------------------------------------


def test_qualified_reference_outside_allowed_databases_rejected():
    """M1: the qualifier must be enforced, not just the connection database."""
    ok, err = validate_query("SELECT * FROM mixpanel_ga.mixpanel_raw_events")
    assert not ok, (
        "a qualified reference to a database outside ALLOWED_DATABASES "
        "passed validate_query — the M1 bypass is open"
    )
    assert "mixpanel_ga" in err


def test_qualified_reference_inside_allowed_databases_passes():
    ok, _ = validate_query("SELECT count() FROM governance_db.snapshot_votes FINAL")
    assert ok


def test_backtick_qualified_reference_outside_allowed_rejected():
    ok, _ = validate_query("SELECT * FROM `mixpanel_ga`.`mixpanel_raw_profiles`")
    assert not ok


@pytest.mark.parametrize(
    "fn",
    [
        "remoteSecure('host', db, t)",
        "sqlite('/tmp/x.db', 't')",
        "mongodb('h:27017', 'db', 'c', 'u', 'p')",
        "clusterAllReplicas('c', system.one)",
        "cluster('c', system.one)",
        "merge('dbt', '^int_')",
        "dictGet('d', 'attr', 1)",
        "executable('x.sh', TSV, 'a String')",
        "azureBlobStorage('conn', 'cont', 'p')",
        "iceberg('http://x', 'k', 's')",
        "deltaLake('http://x')",
        "urlCluster('c', 'http://x')",
        "s3Cluster('c', 'http://x')",
        "format(JSONEachRow, '{}')",
    ],
)
def test_widened_table_functions_rejected(fn):
    ok, err = validate_query(f"SELECT * FROM {fn}")
    assert not ok, f"table function passed validate_query: {fn}"


def test_legit_aggregate_combinators_not_false_positived():
    """`sumMerge(` / `uniqMerge(` must NOT trip the merge() denial."""
    ok, _ = validate_query(
        "SELECT sumMerge(s), uniqMerge(u) FROM dbt.some_agg_table GROUP BY d"
    )
    assert ok


# ---------------------------------------------------------------------------
# Layer 2 — connector profile narrowing
# ---------------------------------------------------------------------------


def test_connector_profile_narrows_qualified_refs(connector_profile):
    ok, err = validate_query("SELECT count() FROM governance_db.snapshot_votes")
    assert not ok, (
        "governance_db is reachable through caller SQL under the connector "
        "profile — the boundary is dbt + consensus.specs only"
    )
    ok, _ = validate_query("SELECT * FROM dbt.fct_execution_pools_daily LIMIT 1")
    assert ok
    ok, _ = validate_query("SELECT * FROM consensus.specs LIMIT 1")
    assert ok
    ok, _ = validate_query("SELECT * FROM consensus.validators LIMIT 1")
    assert not ok, "only consensus.specs is granted, not consensus.*"


def test_connector_profile_relation_access(connector_profile):
    """The typed-metadata path (describe_table) authorizes the relation."""
    ok, _ = validate_relation_access("dbt", "fct_execution_pools_daily")
    assert ok
    ok, err = validate_relation_access("governance_db", "snapshot_votes")
    assert not ok
    ok, _ = validate_relation_access("consensus", "specs")
    assert ok
    ok, _ = validate_relation_access("consensus", "validators")
    assert not ok


def test_relation_access_open_without_profile():
    ok, _ = validate_relation_access("governance_db", "snapshot_votes")
    assert ok


def test_internal_only_still_blocked_by_qualified_form():
    """The privacy deny list must hold for `db.table` references too."""
    ok, _ = validate_query(
        "SELECT * FROM dbt.int_execution_gpay_user_identity_bridge"
    )
    assert not ok
