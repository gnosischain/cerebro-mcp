---
id: ch-output-alias-shadows-column
title: An output alias shadows the same-named source column, silently emptying the result
status: observed
layer: sql
scope: >-
  any SELECT that aliases an expression to a name that also exists as a column on
  a table in the same query, then filters or joins on that name — canonical sites
  semantic/tx_queries.py and semantic/flow_queries.py
symptom: >-
  a query that returns zero rows with no error, where the same predicate matches
  when run standalone
last_verified: 2026-07-30
evidence:
  - src/cerebro_mcp/semantic/tx_queries.py:475-479
  - src/cerebro_mcp/semantic/flow_queries.py:1006-1007
  - 'the external dbt corpus records the same class as ch-alias-shadows-where'
  - 'NO test guards this anywhere in this repo'
---
## Symptom

An empty result set, no exception. `WHERE address IN {ids}` matches nothing even
though the addresses demonstrably exist in the table.

## Root cause

ClickHouse resolves an identifier in `WHERE` against the SELECT list before the
table's own columns. Aliasing an expression to `address` therefore makes
`WHERE address ...` refer to the *computed* value, not the column — so a filter
intended to narrow the scan compares against something else entirely.

The related aggregate form fails louder: `min(block_number) AS block_number`
raises `ILLEGAL_AGGREGATION` rather than returning nothing.

## Forbidden action

Aliasing any expression to the name of a column that exists on a table in the
same query.

## Detection

Grep the emitted SQL for `AS <name>` where `<name>` also appears as a bare column
reference in a `WHERE`, `JOIN ... ON`, or `GROUP BY` at the same level.

## Safe remediation

Give the alias a distinct name (`address_out`, `resolved_address`), or wrap the
projection in a subquery so the filter runs at a level where the alias does not
exist.

## Enforcement

None. This is the highest-value untested lesson in the SQL layer: two sites carry
the comment, no test asserts it, and the failure mode is a silent empty result
rather than an error.
