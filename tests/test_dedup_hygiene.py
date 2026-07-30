"""ReplacingMergeTree read hygiene warnings on ad-hoc SQL.

The mini-app SQL specs are already test-guarded
(`test_every_spec_targets_governance_db_with_final_order_by_and_binds`), but
ad-hoc model-authored SQL going through `execute_query` had no check at all.
Omitting FINAL on a raw ReplacingMergeTree does not error — duplicate
generations survive until a background merge, so counts and sums come back
silently INFLATED.

Implements the DETECTION section of the `ch-final-three-way-rule` lesson.
Warnings, never rejections: which branch applies depends on the physical
relation, and a regex cannot always tell.
"""

import pytest

from cerebro_mcp.safety import dedup_hygiene_warnings


# --- Branch 1: raw governance_db table, FINAL MANDATORY -------------------


def test_governance_table_without_final_warns():
    warnings = dedup_hygiene_warnings(
        "SELECT count() FROM governance_db.snapshot_votes WHERE proposal_id = 'x'"
    )
    assert len(warnings) == 1
    assert "snapshot_votes" in warnings[0]
    assert "inflated" in warnings[0].lower()


def test_governance_table_with_final_is_clean():
    assert dedup_hygiene_warnings(
        "SELECT count() FROM governance_db.snapshot_votes FINAL"
    ) == []


def test_alias_before_final_is_accepted():
    """`FROM t AS x FINAL` is the correct form; the reverse is a known trap."""
    assert dedup_hygiene_warnings(
        "SELECT v.vp FROM governance_db.snapshot_votes AS v FINAL"
    ) == []


def test_final_inside_a_comment_does_not_count():
    """Comments are stripped first, so a `-- FINAL` cannot satisfy the check."""
    warnings = dedup_hygiene_warnings(
        "SELECT 1 -- remember FINAL\nFROM governance_db.snapshot_votes"
    )
    assert len(warnings) == 1


def test_every_unfinaled_table_is_reported_separately():
    warnings = dedup_hygiene_warnings(
        "SELECT 1 FROM governance_db.snapshot_votes "
        "JOIN governance_db.snapshot_proposals ON 1=1"
    )
    assert len(warnings) == 2


def test_mixed_query_only_flags_the_unfinaled_side():
    warnings = dedup_hygiene_warnings(
        "SELECT 1 FROM governance_db.snapshot_votes FINAL "
        "JOIN governance_db.snapshot_proposals ON 1=1"
    )
    assert len(warnings) == 1
    assert "snapshot_proposals" in warnings[0]


# --- Branch 2: canonical views resolve dedup internally -------------------


def test_canonical_view_without_final_is_clean():
    """v_* views dedup internally — FINAL there is forbidden, not required."""
    assert dedup_hygiene_warnings(
        "SELECT 1 FROM rpc_log_indexer.v_delegate_events_gnosis"
    ) == []


def test_governance_prefixed_view_is_not_flagged():
    assert dedup_hygiene_warnings("SELECT 1 FROM governance_db.v_something") == []


# --- Branch 2 rider: the job-scoped treasury view -------------------------


def test_treasury_view_without_job_pin_warns():
    warnings = dedup_hygiene_warnings(
        "SELECT sum(balance) FROM rpc_state_indexer.v_treasury_balances"
    )
    assert len(warnings) == 1
    assert "job_name" in warnings[0]


def test_treasury_view_with_job_pin_is_clean():
    assert dedup_hygiene_warnings(
        "SELECT sum(balance) FROM rpc_state_indexer.v_treasury_balances "
        "WHERE job_name = 'census_daily'"
    ) == []


# --- Scratch scan tables --------------------------------------------------


def test_scratch_bare_count_warns():
    warnings = dedup_hygiene_warnings("SELECT count() FROM scratch.rpc_logs_abc")
    assert len(warnings) == 1
    assert "uniqExact" in warnings[0]


def test_scratch_uniqexact_is_clean():
    assert dedup_hygiene_warnings(
        "SELECT uniqExact(tx_hash) FROM scratch.rpc_logs_abc"
    ) == []


# --- cow_db must NOT be flagged (branch 3: FINAL is FORBIDDEN there) ------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count() FROM cow_db.orders",
        "SELECT count() FROM cow_db.trades WHERE valid_to > 0",
    ],
)
def test_cow_db_is_never_flagged_for_missing_final(sql):
    """cow_db large tables are branch 3 — FINAL there OOMs (code 241).

    Telling a caller to add FINAL would be actively wrong advice, so the
    detector must stay scoped to the plane where FINAL is mandatory.
    """
    assert dedup_hygiene_warnings(sql) == []


# --- Wiring: the warning must actually reach the caller -------------------


def test_warning_is_surfaced_through_run_query():
    """A guard nobody sees fire is not a guard.

    Exercises the real run_query path so the warning genuinely lands on the
    result, rather than only testing the detector in isolation.
    """
    from unittest.mock import MagicMock, patch

    from cerebro_mcp.clients.clickhouse import ClickHouseManager

    manager = ClickHouseManager()
    client = MagicMock()
    with patch.object(manager, "get_client", return_value=client), patch.object(
        manager,
        "_fetch_rows",
        return_value=(["c"], [[1]], "rows", []),
    ):
        executed = manager.run_query(
            "SELECT count() AS c FROM governance_db.snapshot_votes",
            database="governance_db",
        )

    assert any("DEDUP RISK" in w for w in executed.warnings), executed.warnings
