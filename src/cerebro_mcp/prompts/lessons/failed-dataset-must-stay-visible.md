---
id: failed-dataset-must-stay-visible
title: A dataset that fails to load must render a stub, never disappear
status: observed
layer: mcp-tool
scope: every mini-app panel driven by a QuerySpec / deferred dataset group
symptom: 'a missing panel, which a reader interprets as "there is no data" rather than "this failed"'
last_verified: 2026-07-30
evidence:
  - src/cerebro_mcp/tools/visualization/mini_apps.py:1244-1248
  - src/cerebro_mcp/tools/visualization/cow_explorer.py:2148
  - src/cerebro_mcp/tools/visualization/governance_explorer.py:1585
  - 'three independent sites carry this rule; no test asserts it'
---
## Symptom

A panel is simply absent. The page looks complete, so nobody investigates — the
failure is indistinguishable from a legitimate empty result.

## Root cause

The natural error path is to omit what could not be built. For an analytics
surface that converts a *load failure* into an apparent *finding*, which is the
more expensive of the two errors.

## Forbidden action

Dropping a panel, series, or row on error. Also: rendering an empty region with no
words — an empty box with no explanation reads as "still loading" or "broken".

## Detection

Force a spec to fail (bad SQL, unreachable DB) and confirm the panel still appears
carrying an error state and a retry affordance.

## Safe remediation

Emit a stub descriptor with the error attached, and let the UI render a visible
failed state. The `GroupGate` / `DatasetPanel` components already do this; the
counterpart rule is that a deliberate exclusion must be **counted**, not silently
dropped (see the `gip_pipeline` exclusion counts, which partition every omitted row
and are asserted by
`tests/test_governance_explorer.py::test_gip_pipeline_exclusion_counts_leave_no_undisclosed_rows`).

## Enforcement

None directly — the repo's most-repeated design principle (three sites) is
untested.
