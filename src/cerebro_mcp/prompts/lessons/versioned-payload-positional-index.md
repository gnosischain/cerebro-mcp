---
id: versioned-payload-positional-index
title: Never index a versioned external payload by fixed position — resolve by name from the row's own schema
status: enforced
layer: sql
scope: >-
  any array positional against an upstream schema — Snapshot vp_by_strategy vs a
  proposal's strategies, scores vs choices, ABI-decoded tuples vs their ABI
symptom: >-
  a subset of rows reporting 0 (or a plausible but wrong value) with no error,
  split cleanly by era — and aggregate totals that look reasonable
last_verified: 2026-07-30
evidence:
  - src/cerebro_mcp/tools/visualization/governance_explorer.py:938-953
  - tests/test_governance_explorer.py::test_delegation_power_never_indexes_vp_by_strategy_by_fixed_position
  - tests/test_governance_explorer.py::test_delegation_power_resolves_the_chain_from_network_not_position
  - tests/test_governance_explorer.py::test_delegation_power_matches_the_strategy_name_as_a_substring
  - 'live 2026-07-30: the 2-slot era (504 voters) and 4-slot era (5,593 voters) both returned 0 under the old read; only the 5-slot era (243 voters) worked'
  - src/cerebro_mcp/prompts/agents/_shared_quality_rules.md rule 9 (analyst-facing statement of the same rule)
---
## Symptom

Rows split by era: everything after the newest upstream schema change is right,
everything before it reads 0. Nothing errors, and because the affected rows read
*zero* rather than *wrong*, totals stay superficially plausible.

## Root cause

`delegation_power` read `vp_by_strategy[4]` + `[5]` guarded on
`length(vps) = 5`. `vp_by_strategy` is positional against **each proposal's own
`strategies` list**, and `gnosis.eth` rewrote that list three times (lengths 2, 4,
5). Every delegate whose latest final vote predated the newest layout therefore
failed the guard and reported 0 — 26.4% of all delegated voting power.

The near-miss is the more instructive half. "Just take the last two entries" looks
like a fix and is worse: the two delegation strategies appear in the **opposite
chain order** in the 4-slot and 5-slot layouts, so that version would have swapped
Ethereum mainnet and Gnosis Chain across 44,635 votes **without changing a single
total** — undetectable in any aggregate.

## Forbidden action

A literal subscript into an upstream-versioned array, and a `length(...) = N`
guard around one. A length guard reads as defensive and behaves as a silent
filter.

## Detection

`grep -nE '\bvps\s*\[\s*[0-9]+\s*\]'` and `length\([^)]*vps[^)]*\)\s*=` over
SQL-emitting code. More generally: any `[<literal>]` on a JSON-extracted array.

## Safe remediation

Join to the record that defines the layout and resolve the slot by **name +
network**, matching the name as a **substring** (the 2020-12 layout calls it
`erc20-balance-of-delegation`, not `delegation`). Pick the payload and its schema
from the same row atomically — `argMax((proposal_id, vps), created_at)` — or you
resolve slots against a different proposal's list and reintroduce the bug subtly.
Where no figure can be resolved, emit `NULL`, never `0`.

## Enforcement

Six tests in `tests/test_governance_explorer.py`. The root cause was the persona
prompt teaching the indices as law, so the fix also rewrote
`dao_governance_analyst.md` (three-era table with the inversion called out) and
added rule 9 to `_shared_quality_rules.md`.
