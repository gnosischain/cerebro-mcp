---
id: ch-alias-in-where-illegal-aggregation
title: An aggregate alias beside a same-level WHERE raises ILLEGAL_AGGREGATION (code 184)
status: enforced
layer: sql
scope: every aggregating SELECT that also filters at the same level
symptom: 'code 184 ILLEGAL_AGGREGATION at runtime, typically only on the widest scope arm'
last_verified: 2026-07-30
evidence:
  - src/cerebro_mcp/tools/visualization/cow_explorer.py:947
  - src/cerebro_mcp/tools/visualization/cow_explorer.py:1750
  - tests/test_cow_explorer.py:1025
  - tests/test_cow_explorer.py:1159-1167
---
## Symptom

`code 184 ILLEGAL_AGGREGATION`. Often reaches production because it fires on one
arm of a UNION (the all-networks arm) while the per-network arms pass.

## Root cause

`WHERE` is evaluated before aggregation, so an aggregate alias cannot be
referenced there — and because ClickHouse resolves SELECT-list aliases first (see
[[ch-output-alias-shadows-column]]), an aggregate alias that collides with a
filtered column name produces this error rather than the silent-empty variant.

## Forbidden action

Aliasing an aggregate to a name used in the same level's `WHERE`.

## Detection

An `argMax(...)/min(...)/max(...) AS <name>` where `<name>` also appears in the
same query's `WHERE`.

## Safe remediation

Move the column into the `GROUP BY` key instead of aggregating it — this is what
`open_orders` does with `creation_date`/`valid_to`, which also happens to bound
the scan. Or filter in a `HAVING`, or push the aggregate into a subquery.

## Enforcement

`tests/test_cow_explorer.py:1025` and `:1159-1167` assert the generated SQL for
both affected specs.
