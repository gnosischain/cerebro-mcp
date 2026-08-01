---
id: ch-union-arm-needs-own-alias
title: >-
  Each UNION ALL arm resolves identifiers alone — a GROUP BY on an alias
  needs that alias declared in the SAME arm
status: observed
layer: sql
scope: >-
  every multi-arm UNION ALL in the query planes (the LONG activity shape
  under tools/visualization/queries/) and in SQL-emitting Python
symptom: >-
  code 47 UNKNOWN_IDENTIFIER naming the GROUP BY column, scoped to the second
  or a later UNION ALL arm — fires only against real ClickHouse while every
  hermetic test passes
last_verified: 2026-07-31
evidence:
  - >-
    src/cerebro_mcp/tools/visualization/queries/governance/poll_activity.sql:30
    and likes_activity.sql:22 — the re-aliased arms (fix in working tree,
    pending commit)
  - >-
    tests/test_governance_live_smoke.py::test_every_spec_executes_against_live_clickhouse
    — the opt-in sweep that caught every filter/range variant of both specs
  - >-
    verified 2026-07-31 against live governance_db: a minimal two-arm repro
    fails code 47 when only the first arm carries AS bucket, passes when both
    do; the sweep went 1 failed -> 18 passed after aliasing both files
---
## Symptom

`code 47 UNKNOWN_IDENTIFIER` naming the `GROUP BY` column, with the error
scope showing the second (or a later) arm of a `UNION ALL`. Every hermetic
suite passes — the stub ClickHouse accepts any SQL — so the first sight of it
is the live spec sweep or production.

## Root cause

ClickHouse analyzes each `UNION ALL` arm as its own scope. An alias declared
in the first arm (`toStartOfDay(created_at) AS bucket`) exists only there:
the union's OUTPUT column names do come from the first arm, but identifier
resolution inside a later arm does not. So `GROUP BY bucket` in arm two has
no `bucket` to bind. The LONG activity shape invites the mistake — arm two
looks like a copy of arm one where the `AS` reads as redundant, and every
pre-existing multi-arm file (forum_activity.sql, governance_activity.sql,
proposal_activity.sql) already aliased every arm without stating why.

## Forbidden action

Writing a multi-arm `UNION ALL` in which any arm references an alias — in
`GROUP BY`, `ORDER BY`, or an expression — that is declared only in a
different arm.

## Detection

Grep each arm after the first `UNION ALL` for the bucket expression missing
`AS bucket` while the arm ends in `GROUP BY bucket`. At runtime, only a real
ClickHouse surfaces it: run the opt-in live sweep
(`CEREBRO_LIVE_CH_SMOKE=1`).

## Safe remediation

Declare the alias in EVERY arm: `SELECT @poll_bucket AS bucket, ...` — the
established house pattern in the three pre-existing multi-arm activity
files. Nothing else changes; output names and row shapes are identical.

## Enforcement

None hermetic — StubCH cannot catch a ClickHouse analyzer rule. The opt-in
live sweep (`tests/test_governance_live_smoke.py::
test_every_spec_executes_against_live_clickhouse`) demonstrably catches the
class for every governance spec, but only when run. The two affected files
are fixed in the working tree, pending commit.
