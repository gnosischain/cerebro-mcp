---
id: sql-loader-cache-needs-restart
title: sql_loader is lru_cached, so a .sql edit does nothing until the server restarts
status: observed
layer: build-deploy
scope: every .sql under src/cerebro_mcp/tools/visualization/queries/ loaded by sql_loader
symptom: >-
  a query change with no effect — the panel still shows the old numbers, and
  re-running the tool does not help
last_verified: 2026-07-30
evidence:
  - 'src/cerebro_mcp/tools/visualization/sql_loader.py — two @lru_cache(maxsize=None) readers'
  - 'observed 2026-07-30 while tightening the gip_pipeline window: the SQL and its tests were correct and the running server kept serving the previous query'
---
## Symptom

You edit a `.sql`, the tests that render it pass, and the running server keeps
returning the old result. Nothing errors, so the natural conclusion is that the edit
was wrong — and the next hour goes into re-editing a file that was already correct.

## Root cause

`sql_loader`'s readers are `@lru_cache(maxsize=None)`. The first load of each query
is memoised for the life of the process, so the file on disk and the query being
executed diverge the moment you save.

This compounds with [[stale-prebuilt-miniapp-bundle]] into a two-sided trap: a UI
change needs a **rebuild**, a SQL change needs a **restart**, and the symptom does not
tell you which one you are looking at.

## Forbidden action

Concluding a SQL change is wrong — or right — from the behaviour of an
already-running server.

## Detection

Restart and re-run. If the behaviour changes, the cache was the cause. Without a
restart, inspect the loader's `cache_info()` for hits.

## Safe remediation

Restart the server after editing a `.sql`. Note why this survives review: in tests
each run is a fresh process, so the cache never bites there — it only appears against
a live server.

## Enforcement

None. The requirement is stated in the queries scoped guide and in the
`sql_query_files` profile, and `scripts/agent_context/guard.py` warns when a
query-plane file is edited.
