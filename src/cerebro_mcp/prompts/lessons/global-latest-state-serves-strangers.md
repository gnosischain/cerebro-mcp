---
id: global-latest-state-serves-strangers
title: A "latest artifact" pointer or process-global registry serves one user's data to whoever asks next
status: observed
layer: mcp-tool
scope: any process-global mutable state read by a tool, resource, or route on a multi-user transport
symptom: 'a caller receives another conversation''s charts/report/discovery state; exclusion sweeps report "Excluded 0 / kept 0" mid-analysis'
last_verified: 2026-07-31
evidence:
  - src/cerebro_mcp/tools/visualization/charts.py:1078
  - src/cerebro_mcp/tools/visualization/charts.py:3054
  - src/cerebro_mcp/runtime/analysis_registry.py:1
  - tests/test_analysis_isolation.py:1
  - tests/test_state_classification.py:1
  - "pending deploy: remediation exists in the working tree only (per-cycle proxies + profile guards)"
---
## Symptom

One user's artifacts or analysis evidence appear in another user's session.
Observed in production: finishing a report wiped a DIFFERENT conversation's
discovery evidence mid-analysis — signature `exclude_all_discovered_except`
returning "Excluded 0 / kept 0" (recorded in the code comment at
`charts.py:1078-1098`, where the offending `reset()` call was disabled).
Latent variants: `_LAST_VISUAL` + `ui://cerebro/visualization` served the
globally LATEST report (embedded data included) to anyone fetching within
600 s; `generate_chart` responses broadcast every registered chart id to
every caller; `{{chart:ID}}` resolved against the global registry, so
citing another user's charts was the cheapest way past
`check_report_preconditions`.

## Root cause

`STREAMABLE_HTTP_STATELESS` means no session spans a conversation, so
process-global singletons (`session_state.state`, `_chart_registry`,
`_LAST_VISUAL`, `reasoning._current_session`) became the only continuity —
and a global keyed by NOTHING is keyed by "whoever asks next". Aggravator:
the singletons are imported BY VALUE (`from ...session_state import
state`), so rebinding the module attribute deglobalizes nothing.

## Forbidden action

Adding module-level mutable state that a tool, resource, or route reads on
a multi-user transport without an explicit owner/cycle key or a profile
guard — including "convenience" latest-pointers.

## Detection

`tests/test_state_classification.py` walks the state-bearing modules and
fails on any mutable global without a reviewed disposition
(cycle-keyed / owner-keyed / disabled-http / safe-shared / const).
Two-principal tests assert the inverse property the old tests missed:
isolation between OWNERS, not between test cases.

## Safe remediation

Key the state by verified identity behind a resolve-per-access PROXY (the
by-value import problem makes rebinding useless): `session_state.state`
and `charts._chart_registry` resolve per `(owner, analysis_handle)` cycle
(`runtime/analysis_registry.py`); state that cannot be keyed is disabled
under the connector profile (`_LAST_VISUAL`, chart persist/restore,
`reasoning._current_session`).

## Enforcement

`tests/test_analysis_isolation.py` (two-owner clobber test reproducing the
production incident), `tests/test_state_classification.py` (unclassified
global = red build). Status stays `observed` until deployed.
