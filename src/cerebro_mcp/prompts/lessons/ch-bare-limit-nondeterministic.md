---
id: ch-bare-limit-nondeterministic
title: LIMIT without ORDER BY returns an arbitrary subset that changes between identical calls
status: observed
layer: sql
scope: every LIMIT over an unordered scan, especially evidence/drill-down surfaces
symptom: 'the same query returning different rows on repeat calls — measured 17 distinct result sets across 20 calls'
last_verified: 2026-07-30
evidence:
  - src/cerebro_mcp/semantic/graph_profiles.py:531-534
  - src/cerebro_mcp/tools/visualization/mini_apps.py:1259 ('must define a deterministic ORDER BY')
  - tests/test_governance_explorer.py asserts a deterministic ORDER BY for all 12 entity specs
---
## Symptom

Identical calls return different rows. Observed at 17 distinct result sets over 20
calls of the same query.

## Root cause

ClickHouse parallelises the scan and `LIMIT` stops as soon as enough rows arrive,
so which rows arrive first depends on thread scheduling. Without `ORDER BY` there
is no tiebreak.

## Forbidden action

`LIMIT` without a fully-determining `ORDER BY` on anything a user will read as
evidence. As the original comment puts it: "evidence that changes when you look at
it twice is not evidence."

## Detection

`LIMIT` present with no `ORDER BY` at the same level; or an `ORDER BY` whose key
is not unique, which narrows the nondeterminism without removing it.

## Safe remediation

Add `ORDER BY` ending in a unique column (an id or hash) so the ordering is total.

## Enforcement

Asserted for the 12 governance entity specs and stated at
`mini_apps.py:1259`; the general case is unguarded.
