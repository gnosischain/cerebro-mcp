---
id: fat-view-join-never-prunes
title: >-
  A JOIN on resolved anchor dates never prunes a fat view — resolve dates from
  the cheap authoritative table and add an IN predicate that folds to constants
status: observed
layer: sql
scope: >-
  every mini-app query that pins a scan to resolved snapshot/anchor dates via a
  CTE JOIN — the governance treasury plane over
  rpc_state_indexer.v_treasury_balances is the paid instance; any future plane
  whose base table another writer can grow is the same class
symptom: >-
  a whole section's datasets fail together with Code 241 "(total) memory limit
  exceeded ... would use ~10.8 GiB" and Code 159 20s timeouts, on queries that
  ran green for months and that no deploy touched; the error names a view over
  a table that recently grew by orders of magnitude
last_verified: 2026-08-26
evidence:
  - >-
    verified 2026-08-26: SELECT chain_id, max(snapshot_date) FROM
    v_treasury_balances WHERE job_name='daily_treasury' GROUP BY chain_id OOMs
    at 10.8 GiB, while the identical aggregate over
    rpc_state_indexer.census_publications answers in ~1s — token_balances grew
    from the treasury-only slice to 2.31B rows / 49 GiB when the holder census
    started writing the same table
  - >-
    verified 2026-08-26: the same treasury_summary body with
    "t.snapshot_date IN (SELECT as_of FROM asof)" beside the unchanged JOIN
    runs in 2.39s; a literal-date probe through the unmodified view runs in
    0.24s — pushdown works for constants, never for join keys
  - src/cerebro_mcp/tools/visualization/queries/governance/_cte_treasury_asof_per_chain.sql
  - tests/test_governance_explorer.py::test_treasury_dates_resolve_from_publications_and_every_scan_is_pruned
  - tests/test_governance_live_smoke.py::test_treasury_specs_execute_against_live_clickhouse
  - >-
    the month-end HISTORY datasets select many dates and the view costs ~0.4s
    per selected date (52 dates = 22s, over the 20s budget), so they are
    BOUNDED to the latest 24 month-ends per chain (TREASURY_HISTORY_MONTHS,
    disclosed in every history basis) and the wallet-history focus filter
    became a constant tuple-IN (the old expression-on-both-sides
    "token_address = multiIf(chain_id, ...)" defeated the index and cost ~13s
    alone); measured after both: wallet 6.4s, chain 11.6s, token 10.9s —
    full history returns when the indexer materializes a treasury slice
---
## Symptom

Every dataset of one mini-app section fails at once: Code 241 citing the
SERVER-WIDE "(total)" cap at ~10.8 GiB, or Code 159 at the 20s interactive
budget. The queries ran green for months and no deploy touched them. The
"(total)" wording resembles the dbt corpus's ch-overcommit-victim, but this one
reproduces on an idle cluster — it is not transient.

## Root cause

The plane pinned its scans to resolved anchor dates with a CTE JOIN:
`asof AS (SELECT max(snapshot_date) FROM <view> ...) ... INNER JOIN asof`. Two
compounding facts: (1) the CTE aggregates the VIEW, whose
`(SELECT * FROM token_balances FINAL)` body must merge the whole base table
first — fine while the treasury job was the only writer, fatal once the holder
census grew the same table to billions of rows; (2) even with cheap date
resolution, a JOIN never prunes the probe side — ClickHouse prunes partitions
and primary-key ranges only from CONSTANT predicates, and uncorrelated
scalar/IN subqueries fold to constants at plan time while join keys do not.
The failure arrived with someone else's data growth, so no diff review could
catch it and no hermetic test can either.

## Forbidden action

Never resolve anchor dates by aggregating a view over a table another writer
can grow, and never rely on a JOIN against resolved dates to bound a fat scan.
Do not "fix" a reproducible total-cap 241 by retrying (that is the
ch-overcommit-victim remedy — check reproducibility on an idle cluster first).

## Detection

The section's datasets fail together; the memory figure is far above the
per-query cap; `max(snapshot_date)` over the view alone already OOMs while the
same aggregate over the small authoritative table (census_publications — the
view INNER JOINs it, so a date exists in the view iff published) is instant.

## Safe remediation

Resolve dates from the cheap authoritative table
(`_cte_treasury_asof_per_chain.sql` and its three siblings read
`census_publications`), keep the JOIN for per-chain exactness and the as_of
output column, and add `t.snapshot_date IN (SELECT as_of FROM asof)` (or the
months/tuple form) beside EVERY view scan — the IN folds to a constant set and
prunes. Measured: OOM → 2.4s (summary), timeout → 4.1s (holdings, whose supply
CTE needed the same prune to stop materialising the census-dominated
publications aggregation). The second/third CTE reference this adds is a
declared allowance in test_treasury_specs_reference_each_cte_once — the CTE
source is small, so N references are N cheap scans.

## Enforcement

Hermetic: test_treasury_dates_resolve_from_publications_and_every_scan_is_pruned
pins publications-based resolution plus a prune on every view scan. Live:
test_treasury_specs_execute_against_live_clickhouse executes every treasury
section spec against the real server (the plane previously ran in NO test, so
the regression arrived invisibly). Enforced status requires both deployed plus
the live sweep in a scheduled run.
