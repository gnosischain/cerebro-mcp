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
last_verified: 2026-09-26
evidence:
  - tests/test_governance_explorer.py::test_treasury_specs_reference_each_cte_once
  - tests/test_governance_explorer.py::test_treasury_entity_specs_reference_each_cte_once
  - 'a 4-reference CTE exhausted the 2 GiB cap at ~390 tokens and took the treasury panel down'
  - >-
    verified 2026-09-26 (pools plane): once the as-of resolver read
    v_publications_current, every `IN (SELECT as_of FROM asof)` prune re-ran it —
    pools_summary 1.45s -> 2.72s with four such prunes, token_directory 0.63s ->
    1.58s — while six identical `(SELECT as_of FROM asof)` scalars cost the same as
    one (0.34s vs 0.33s). Scalar prunes restored the one-evaluation cost
  - tests/test_pools_explorer.py::test_a_cte_is_referenced_once_unless_it_is_declared_cheap
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

The same holds for every `IN (SELECT ... FROM cte)`: each subquery context builds
its own set by executing the CTE chain again. The one exception is an IDENTICAL
scalar subquery — ClickHouse computes each distinct scalar subquery once per query
— so `col = (SELECT as_of FROM asof)` repeated N times costs one evaluation and
still folds to a constant that prunes, while N `IN (SELECT as_of FROM asof)` cost
N. Two scalar spellings over the same CTE (`(SELECT ras_of FROM rasof)` and
`(SELECT max(ras_of) FROM rasof)`) are two evaluations.

## Forbidden action

Referencing the same CTE more than once in a query that reads a large relation,
and reaching for a CTE as a "compute this once" device.

## Detection

Count references per CTE name in the emitted SQL. The guard for this has now had
**two** bugs of its own, both worth copying carefully:

1. A line-initial regex skipped the first CTE of every spec, so the check silently
   verified nothing. Match the name anywhere, not just at the start of a line.
2. It counted occurrences in the rendered template **including comments**, so prose
   naming a CTE scored as extra scans. That inflation is why the ceiling had been
   set to `<= 2`: it looked like real specs needed the slack. Comments stripped, all
   seven treasury specs reference every CTE exactly once. See
   [sql-guard-counts-comments-as-code](sql-guard-counts-comments-as-code.md).

Also assert the sweep found something (`seen >= 10`), or the whole check passes
vacuously the day the `WITH` spelling changes.

Bug 1 recurred in a SECOND suite: `tests/test_pools_explorer.py` matched
`^\s*(\w+) AS \(`, which never sees the CTE on the `WITH` line — so when that
first CTE became the as-of resolver's raw-candidate read (referenced twice), the
counter was blind to it (found and fixed 2026-09-26). A declared-cheap ALLOWANCE
is a cost claim about the CTE's body, not its name: the pools `asof` allowance
said "one row" while the body behind it grew a served-view read. Re-measure an
allowance whenever the body changes, and date the measurement beside it.

## Safe remediation

Either accept the repetition and keep the CTE narrow (project only the columns
needed, push the filter inside), or write to a scratch table / use a single pass
with conditional aggregation (`sumIf`, `argMax`) instead of several references.
For a one-value resolver referenced from many places, reference it only through
ONE identical scalar spelling (`= (SELECT as_of FROM asof)`), never `IN`. For a
set that a CTE inlined several times must test against (one pool's served
calendar), fold it into a scalar array and test with `has()`: the heatmap's
four inlined reads of a served-pinned day list went from 3.0s to 0.7s.

## Enforcement

`tests/test_governance_explorer.py` asserts **exactly one** reference per CTE for
both the treasury specs and the entity specs, over comment-stripped SQL, with a
non-vacuity floor. `tests/test_pools_explorer.py` counts references per CTE
(including the `WITH` line since 2026-09-26) against a declared, dated allowance,
and its served-publications guard rejects `IN (SELECT as_of FROM asof)` and any
second scalar spelling over a resolver. Every other SQL-emitting module is
currently **unguarded** — extending that assertion is the obvious next step.

Cost of getting this wrong, measured: `treasury_token_history` referenced
`per_bucket` twice, which doubled both its own scan and the `months` scan nested
inside it. At ~2.7s per scan of `v_treasury_balances` that was 6 effective scans,
and the dataset failed the 20s interactive budget in production (code 159).
Referencing it once — the second pass became a window function — took it to 3 scans
and 8.5-10.7s.
