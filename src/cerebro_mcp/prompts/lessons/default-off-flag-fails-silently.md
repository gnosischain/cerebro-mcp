---
id: default-off-flag-fails-silently
title: A subsystem behind a default-off flag reads as healthy when it is simply not running
status: enforced
layer: runtime
scope: >-
  every capability gated by a settings flag or an optional artifact —
  SEMANTIC_ENABLED (registry, metric discovery, graph catalog, Graph Explorer
  relationships), RPC_SCAN_ENABLED, MCP_UI_INLINE_ENABLED, DEV_MINI_APPS_ENABLED,
  and any loader whose failure path returns None instead of raising
symptom: >-
  a whole feature returns empty or "unavailable" in one environment and works in
  another, while every health signal in both — connectivity, manifest, docs,
  probes — is green
last_verified: 2026-07-30
evidence:
  - 'the deployed remote returned search_graph_catalog count=0 with "semantic snapshot unavailable" and discover_metrics "Semantic snapshot unavailable.", while local returned 10 edge profiles and full metric results — same code, same published artifacts'
  - 'both servers reported ClickHouse connected, manifest loaded (1242 models) and docs index loaded (2600 entries); nothing in system_status distinguished them'
  - 'settings.SEMANTIC_ENABLED defaults to False (config.py:128); loaders/semantic.py:145 returns None early, so current_snapshot() is None and every semantic tool degrades to a message'
  - 'all three artifacts were HTTP 200 and the remote fetched manifest.json from the same host, ruling out egress and publication'
  - 'SECOND INSTANCE of the same observable: 5139e86 fixed a NameError in loaders/artifacts.py that a broad `except Exception` turned into a log warning, silently emptying the registry, catalog, docs index and graph catalog in production while the suite passed and probes stayed green'
---
## Symptom

A feature works locally and does nothing in the deployed environment. No error
reaches the user — the UI renders its empty state, or a tool returns a polite
"unavailable" string. Meanwhile the status endpoint is entirely green:
databases connected, manifest loaded, docs loaded, probes passing.

The Graph Explorer loading no relationships on the remote while loading them
locally is the worked example.

## Root cause

Two mechanisms, one observable.

**The flag.** `SEMANTIC_ENABLED` defaults to `False`. `SemanticLoader.load()`
returns `None` immediately when it is off, so `current_snapshot()` is `None` and
every consumer degrades to a message rather than an exception. The local `.env`
sets it to `True`; a deployment that never set the variable inherits the default
and looks identical in every other respect.

**The swallowed failure.** The same observable is produced by a loader that
catches broadly and logs. `loaders/artifacts.py` once referenced `settings`
without importing it; the `except Exception` around the fetch turned the
`NameError` into a warning, and the registry, catalog, docs index and graph
catalog were all silently empty in production.

What makes both expensive is the *company the failure keeps*: manifest and docs
use their own loaders and are not gated, so they keep working and the report
reads healthy. A green status is evidence that the things it measures are fine —
not that the thing you care about is running.

## Forbidden action

Adding a capability behind a default-off flag, or behind an optional artifact,
without reporting that flag's state and the artifact's loaded/not-loaded state in
`system_status`. Concluding from a green status that a subsystem is running.
Catching broadly around a load and logging, when the caller cannot tell an empty
result from a failed one.

## Detection

**A/B the environments with the same tool.** Two servers are configured in this
session precisely so this is one call each:

```
search_graph_catalog(query="", min_quality_tier="all")   # on each server
discover_metrics(query="…")                              # on each server
```

Divergent output with identical code localises the cause to configuration. Then
read `system_status` → **Semantic Layer**, which now names the flag, says whether
the snapshot actually loaded, and prints both artifact sources.

Rule out the alternatives before blaming config: `curl` the artifact URLs for a
200 and a plausible byte count, and check whether some OTHER artifact from the
same host loaded — if it did, egress and publication are fine.

## Safe remediation

Set the variable in the deployment and restart (`SEMANTIC_ENABLED=true` here).

More durably: make the disabled state *legible*. Report every gating flag in the
status output, distinguish "disabled" from "enabled but failed to load", and have
the user-facing message name the variable to set — `dashboard_builder.py:44`
already models this with "Is SEMANTIC_ENABLED=true?", and the common path should
read the same way.

Prefer a default that fails loudly. A flag defaulting off is right when the
capability is genuinely optional; it is wrong when every real deployment wants it
on, because then the default only ever fires by accident.

## Enforcement

`system_status` carries a **Semantic Layer** section reporting `SEMANTIC_ENABLED`,
the snapshot's load state, and the registry / graph-catalog sources.
`tests/test_metadata_status.py` asserts both branches render and that the disabled
branch names the environment variable, so the diagnostic cannot regress into the
silence it exists to break.

Not enforced: the other gated capabilities (`RPC_SCAN_ENABLED`,
`MCP_UI_INLINE_ENABLED`, `DEV_MINI_APPS_ENABLED`) are still absent from the status
report. Extending it is the obvious next step — the flag list is short and the
failure mode is identical.
