---
id: ch-folded-projection-loses-its-type
title: >-
  A projection ClickHouse constant-folds arrives with a different wire type, and
  toString() is the only cast that survives the fold
status: observed
layer: sql
scope: >-
  any dataset column whose value ClickHouse can fold to a constant — a scalar
  subquery over a one-row CTE, a bare min()/max() aggregate, a GROUP BY key on a
  fully-pinned scan. Bites hardest on Date and DateTime columns, where the folded
  form arrives as a raw day number. Canonical site: the as-of resolvers in
  src/cerebro_mcp/tools/visualization/queries/pools/
symptom: >-
  the same dataset column is a date on one code path and an integer like 20712 on
  another; a date renders in the UI as a five-digit number, or an "as of" 
  comparison always reports a shift, with no error anywhere and the query itself
  returning the right rows
last_verified: 2026-09-17
evidence:
  - >-
    verified 2026-09-17 against live ClickHouse: with the as-of CTE predicate
    unbounded (`AND 1`), `SELECT (SELECT as_of FROM asof)` returned the int 20712
    to clickhouse_connect; with the predicate bound
    (`snapshot_date <= {as_of:Date}`) the identical projection returned
    datetime.date(2026, 9, 16). 20712 days after 1970-01-01 IS 2026-09-16, so the
    value was right and only the type was wrong
  - >-
    verified 2026-09-17, four casts measured on the folded branch:
    `toDate((SELECT x FROM a))` -> 20712, `CAST((SELECT x FROM a) AS Date)` ->
    20712, `materialize((SELECT x FROM a))` -> 20712, `SELECT a.x FROM a` ->
    20712, `toString((SELECT x FROM a))` -> '2026-09-16'. Only toString survives
  - >-
    the same class then appeared on `min(snapshot_date)` / `max(snapshot_date)`
    in coverage_summary and on the GROUP BY key in publication_calendar and
    missing_days — 5 further columns across the plane, all caught by sweeping
    every spec rather than by the first symptom
  - src/cerebro_mcp/tools/visualization/queries/pools/_cte_asof.sql (rationale header)
  - tests/test_pools_explorer.py::test_every_date_column_is_emitted_as_a_string
  - tests/test_pools_explorer_live_smoke.py::test_every_date_column_arrives_as_a_string
  - >-
    fix in tree 2026-09-17, pending deploy — status stays observed until merged
---

## Symptom

A date column renders as a five-digit integer, or a string comparison against a
requested date never matches so an "as of" shift is reported on every load. The
query returns the correct rows and raises nothing; only the type is wrong, and
only on some code paths. Typically it is correct while you are testing with an
explicit date filter and wrong in the default view — which is the reverse of the
usual "the edge case is broken" intuition.

## Root cause

When every input to a projection is constant, ClickHouse evaluates it at plan
time and substitutes a literal. A `Date` literal is an integer day count, and
the folded column loses the type annotation the driver needs to convert it back.
`clickhouse_connect` hands the caller the raw number.

Whether the fold happens depends on things that have nothing to do with the
column: here, a one-row CTE aggregate folded only when its `WHERE` carried no
bound parameter. So the SAME projection has two wire types depending on an
argument three CTEs away, and a hermetic test that renders SQL cannot see it at
all.

Casting does not help, because the cast is folded too — `toDate`, `CAST ... AS
Date` and `materialize` all return the integer. `toString` works because its
result type is the one the driver cannot misread.

## Forbidden action

Emitting a `Date` or `DateTime` column that can fold to a constant — a scalar
subquery over a one-row CTE, a bare `min()`/`max()`, a GROUP BY key on a fully
pinned scan — and assuming the driver will hand the consumer a date. Equally
forbidden: "fixing" it with `toDate()` or `CAST`, both of which measurably do
not.

## Detection

Run the dataset on BOTH branches of whatever makes the fold conditional — for an
as-of plane, once with the date bound and once without — and assert the Python
type of every date column. A single-branch test passes while the bug is live.
Statically, grep the final projection of each template for a date-named output
alias not wrapped in `toString(`.

## Safe remediation

Emit every date column as an ISO string with `toString(...)`. ISO strings sort
lexicographically the same as dates, so `ORDER BY` is unaffected, and they
round-trip exactly through a deep link that hands the date back as an argument.
Make it a plane-wide contract rather than a per-column patch: the first instance
found was one column, and sweeping every spec found five more.

Two consequences to carry: a CTE must keep the real `Date` for its own
arithmetic and only the OUTER projection converts; and if the projection aliases
the converted value back to the source column's name, every later reference to
it must be table-qualified, or it resolves to the String and raises
`ILLEGAL_TYPE_OF_ARGUMENT` (see ch-output-alias-shadows-column — this fix walks
straight into that one).

## Enforcement

Hermetic: `test_every_date_column_is_emitted_as_a_string` reads the final
projection of every rendered spec, alias-aware, and was mutation-proven to fail
when a single `toString` is removed. Live:
`test_every_date_column_arrives_as_a_string` executes every spec on both as-of
branches and asserts the Python type, because only one branch folds and the
hermetic test cannot tell them apart. Reaching `enforced` needs both deployed.
