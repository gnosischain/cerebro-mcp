---
id: ch-final-three-way-rule
title: FINAL is mandatory, forbidden, or forbidden-for-memory depending on the relation — one rule, three branches
status: remediated
layer: sql
scope: >-
  every read of a ReplacingMergeTree relation — governance_db tables, the
  rpc_log_indexer / rpc_state_indexer canonical views, cow_db orders/trades/
  order_events, and every scratch.rpc_* table
symptom: >-
  either double-counted rows (FINAL omitted where required) or code 241
  MEMORY_LIMIT_EXCEEDED (FINAL applied to a multi-million-row table)
last_verified: 2026-07-30
evidence:
  - 'MANDATORY: src/cerebro_mcp/prompts/agents/dao_governance_analyst.md:16 — "Every FROM governance_db.<table> MUST be followed by FINAL. No exceptions"'
  - 'FORBIDDEN (view resolves dedup internally): src/cerebro_mcp/tools/visualization/governance_explorer.py:66-69, :83-84; tests/test_governance_explorer.py::test_treasury_specs_always_pin_the_job_and_never_use_final'
  - 'FORBIDDEN (memory): src/cerebro_mcp/tools/visualization/cow_explorer.py:1743-1752; tests/test_cow_explorer.py:1159-1167 asserts FINAL not in sql'
  - 'src/cerebro_mcp/prompts/agents/_forensic_standards.md:188 and 5 further sites for the scratch-table variant'
---
## Symptom

Two opposite failures, depending on which branch you got wrong:

- FINAL omitted on a raw ReplacingMergeTree table → duplicate rows survive until
  a background merge lands, so counts and sums are silently inflated.
- FINAL applied to a large table → `code 241 MEMORY_LIMIT_EXCEEDED`, because
  FINAL runs a whole-table k-way merge.

## Root cause

There is no single answer for "should this read use FINAL", and the repo states
the rule in **six different files** with imperative wording that reads as
contradictory ("No exceptions" beside a test literally named
`..._never_use_final`). This record is the arbiter. The three branches:

1. **Raw ReplacingMergeTree table → FINAL is required.** `governance_db` tables
   are re-inserted daily, so without FINAL you read several generations at once.
2. **Canonical view that already resolves dedup → FINAL is forbidden.**
   `v_delegate_events_gnosis` (built on `decoded_events_canonical`, reorg-safe and
   checkpoint-bounded) and `v_treasury_balances` deduplicate internally. Adding
   FINAL is redundant work over an already-correct relation.
3. **Large table where FINAL will not fit → FINAL is forbidden; bound the scan
   instead.** `cow_db.orders` reached ~12M rows and FINAL blew the budget at
   all-networks scope. Prefilter on an **immutable** column (`valid_to` is fixed
   per `order_uid`) so the `argMax` hash stays small, and only `argMax` the
   genuinely mutable columns.

A separate obligation rides along with branch 2: `v_treasury_balances` is **not
job-scoped**, so every read must also pin `job_name` — unpinned it spans 185M+
rows across every census job and double-counts any token measured twice.

## Forbidden action

Applying the rule from one branch to a relation in another, and — the thing that
caused this record — writing a fourth copy of the rule instead of citing it.

## Detection

`FINAL` present on a `cow_db` large table, or on a `v_*` canonical view; `FINAL`
absent on a bare `governance_db.<table>`; a `v_treasury_balances` read with no
`job_name` predicate; a `count()` over a `scratch.rpc_*` table that is not
`uniqExact` (those are ReplacingMergeTree too, so a bare count overcounts after a
resumed scan).

## Safe remediation

Identify which branch the relation is in before writing the read. Note also the
ordering trap: the alias must precede `FINAL` (`FROM t AS x FINAL`, never
`FROM t FINAL AS x`).

## Enforcement

Branches 2 and 3 are test-guarded (`test_treasury_specs_always_pin_the_job_and_never_use_final`,
`tests/test_cow_explorer.py:1159-1167`). Branch 1 is guarded for governance specs
(`tests/test_governance_explorer.py:1218`) and asserted for the persona
(`tests/test_domain_personas.py:100`). The scratch-table variant has six prose
sites and **no test**.
