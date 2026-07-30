---
id: live-table-invalidates-cross-query-diff
title: You cannot prove two queries equivalent by running them separately against a live table
status: remediated
layer: verification
scope: >-
  any before/after equivalence check on a continuously written source — the
  rpc_state_indexer treasury views, cow_db, governance_db, and every raw indexer
  plane whose job re-inserts on a schedule
symptom: >-
  a refactor that is provably equivalent by inspection returns a different row
  count or checksum, and re-running either side changes the answer again
last_verified: 2026-07-30
evidence:
  - 'the SAME query on rpc_state_indexer.v_treasury_balances returned 1118 rows then 1119 rows minutes apart, with different checksums, while the daily treasury job wrote'
  - 'an earlier "identical checksum 8844325882820127511 both ways" conclusion was unreproducible an hour later — the methodology was wrong even though the conclusion held'
  - 'the same three changes, each with BOTH forms inside ONE query: per_bucket 11566 rows / checksum 12306172241941041485 both; changes 309 tokens / 0 disagreements; selection 0 divergent tokens'
---
## Symptom

A performance rewrite that should not move a number moves one. Row counts differ
by a handful, checksums differ entirely, and running the *original* twice gives two
different answers — so there is no stable baseline to compare against.

## Root cause

The source is being written while you measure. `v_treasury_balances` is fed by a
daily job, and a full read of it takes 8–15s across 3–6 internal scans, so an
insert or a ReplacingMergeTree merge landing mid-flight changes what the later
scans see. Two queries issued minutes apart read two different databases.

The failure is in the *method*, and it is seductive because it produces
authoritative-looking evidence: two checksums that happen to match on a quiet
table read as proof, and the same comparison is simply wrong the next hour.

## Forbidden action

Comparing `elapsed`/rows/checksums between two separately issued queries and
calling it equivalence. Recording such a checksum in a comment as if it were a
fixture — the next reader will treat a legitimate data change as a regression.

## Detection

Run one side twice. If the two runs disagree, every cross-query comparison you
have made against that table is void.

## Safe remediation

Put **both** forms inside a single query so they read one consistent snapshot, and
diff there:

```sql
WITH old_form AS (...), new_form AS (...)
SELECT (SELECT count() FROM old_form), (SELECT count() FROM new_form),
       (SELECT sum(cityHash64(...)) FROM old_form),
       (SELECT sum(cityHash64(...)) FROM new_form)
```

If the combined query exceeds the budget — it will, since it pays both costs —
decompose by *changed piece* rather than falling back to separate runs. Prove each
edit independently, each with both forms in one query: the join vs the tuple `IN`,
the GROUP BY vs the window, the `LIMIT n BY` vs `dense_rank`. Composition then
gives you the whole, and each part is individually reproducible.

Record equivalence as *what was compared*, not as an absolute number. A checksum
from a live table is evidence of a comparison, never a fixture.

## Enforcement

Not test-enforceable — it is a measurement discipline, not a code property. The
`treasury_token_history.sql` header states the per-piece results and says
explicitly why a cross-query diff on that view is invalid, so the next person
editing it reads the method before repeating the mistake.
