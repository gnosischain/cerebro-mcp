---
id: ch-cte-inlined-per-reference
title: ClickHouse inlines a CTE once per reference, so an N-referenced CTE scans N times
status: enforced
layer: sql
scope: >-
  every module that emits SQL with a WITH clause — QuerySpec builders under
  tools/visualization/, semantic/tx_queries.py, semantic/flow_queries.py,
  semantic/sql_compiler.py, and every .sql under tools/visualization/queries/
symptom: >-
  code 241 MEMORY_LIMIT_EXCEEDED on a query that looks cheap; a panel that works
  on a small filter and dies as soon as the scope widens
last_verified: 2026-07-30
evidence:
  - tests/test_governance_explorer.py::test_treasury_specs_reference_each_cte_once
  - tests/test_governance_explorer.py::test_treasury_entity_specs_reference_each_cte_once
  - 'a 4-reference CTE exhausted the 2 GiB cap at ~390 tokens and took the treasury panel down'
---
## Symptom

`code 241 MEMORY_LIMIT_EXCEEDED`, or a query whose cost grows far faster than the
data it appears to read. Typically shows up when a filter widens — the small case
fits under the cap and the real case does not.

## Root cause

ClickHouse does not materialise a CTE. It **substitutes the subquery at each
reference**, so a CTE named three times is executed three times. A CTE that reads
a wide table and is referenced once per output column multiplies that scan by the
column count.

This is the opposite of the intuition carried over from Postgres/Snowflake, where
a CTE is an optimisation fence that is computed once.

## Forbidden action

Referencing the same CTE more than once in a query that reads a large relation,
and reaching for a CTE as a "compute this once" device.

## Detection

Count references per CTE name in the emitted SQL. Note the guard for this had a
bug of its own worth copying carefully: a line-initial regex skipped the first CTE
of every spec, so the check silently verified nothing. Match the name anywhere,
not just at the start of a line.

## Safe remediation

Either accept the repetition and keep the CTE narrow (project only the columns
needed, push the filter inside), or write to a scratch table / use a single pass
with conditional aggregation (`sumIf`, `argMax`) instead of several references.

## Enforcement

`tests/test_governance_explorer.py` asserts one reference per CTE for both the
treasury specs and the entity specs. Every other SQL-emitting module is currently
**unguarded** — extending that assertion is the obvious next step.
