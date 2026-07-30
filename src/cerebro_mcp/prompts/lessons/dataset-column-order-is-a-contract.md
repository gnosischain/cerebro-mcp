---
id: dataset-column-order-is-a-contract
title: Column order is a contract wherever a consumer reads rows positionally
status: observed
layer: mcp-tool
scope: >-
  every QuerySpec / dataset whose consumer indexes row tuples (r[5], r[6]) rather
  than resolving by column name
symptom: 'values rendered under the wrong label — e.g. a token address displayed as the sender'
last_verified: 2026-07-30
evidence:
  - src/cerebro_mcp/semantic/tx_queries.py:147-150
  - 'reordering the SELECT list made the token address render as the sender'
---
## Symptom

Data appears in the wrong column of a UI, with no error. Every value is real, so
it reads as a data problem rather than a wiring problem.

## Root cause

Some consumers read `row[n]` positionally instead of zipping against the returned
column names. The SELECT list is then a load-bearing interface, and reordering it
for readability silently re-labels every downstream field.

## Forbidden action

Reordering, inserting into, or removing from a SELECT list whose consumer indexes
positionally, without updating the consumer in the same change.

## Detection

Grep the consuming TS/Python for `\[\s*\d+\s*\]` against a row variable. In the
mini-apps the safe pattern already exists: `rowsToObjects(dataset)` zips columns to
names — prefer it.

## Safe remediation

Convert the consumer to name-based access (`rowsToObjects`), which removes the
contract entirely. Where positional access must stay, assert the expected column
order in a test.

## Enforcement

None. The comment at `tx_queries.py:147-150` is the only record.
