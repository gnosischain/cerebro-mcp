---
id: dbt-sqlx-silently-not-compiled
title: dbt compiles only .sql, so renaming a model to .sqlx retires it without any error
status: observed
layer: dbt-modelling
scope: any upstream dbt model this repo reads; canonical instance was a tx-leg model
symptom: 'a model whose data is frozen at a past date while every query against it succeeds'
last_verified: 2026-07-30
evidence:
  - src/cerebro_mcp/semantic/tx_queries.py:10-19
  - src/cerebro_mcp/semantic/flow_queries.py:13-21
  - 'the model froze ~12 days behind chain head; the symptom appeared in cerebro-mcp, the cause was in dbt-cerebro'
---
## Symptom

A table that answers queries normally but stops advancing. Freshness looks like a
data-availability problem, so investigation starts in the wrong repo.

## Root cause

dbt discovers models by the `.sql` extension. Renaming to `.sqlx` removes the model
from the DAG with no warning — the existing table stays queryable at whatever state
it was last built in. Here the model fell ~12 days behind before anyone noticed.

The same retired model carried a second defect worth recording alongside: a token
whitelist that dropped real transfers, so a transaction with 9 on-chain legs
surfaced 7 and the UI asserted "COMPLETE · 7 of 7" — confidently wrong, which is
worse than visibly incomplete.

## Forbidden action

Treating dbt as the authority on transaction legs. dbt is **enrichment only**: a
missing price row must never erase a chain leg.

## Detection

`SELECT max(<date column>) FROM <table>` against today before quoting anything as
current. For completeness claims, reconcile leg counts against an on-chain read
rather than against the model.

## Safe remediation

Resolve legs from the chain (RPC / `execution` + `execution_live` unioned and
deduped — `execution_live` runs ~800 blocks ahead) and use dbt only to attach
prices and labels.

## Enforcement

None. Two comment sites, no test — and the guard would have to live in the dbt
repo, not this one.
