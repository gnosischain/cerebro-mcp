---
id: stale-prebuilt-miniapp-bundle
title: Mini-apps are served from prebuilt bundles, so a source edit changes nothing until you rebuild
status: observed
layer: build-deploy
scope: every mini-app under ui/src/mini-apps/ served from src/cerebro_mcp/static/
symptom: >-
  a UI change that is provably correct in the source and absent in the running app;
  or a bug that cannot be reproduced under `make dev`
last_verified: 2026-07-30
evidence:
  - Makefile:21-24 (per-app targets build AND copy into static/)
  - 'src/cerebro_mcp/static/ carries 162 tracked generated files'
  - tests/test_graph_explorer_delivery.py::test_web_bundle_cache_invalidates_on_file_change
---
## Symptom

You edit `ui/src/mini-apps/<app>/…`, reload, and see the old UI. Or the inverse and
more confusing case: a reported bug does not reproduce under `make dev`, because the
dev server serves live source while the MCP serves the committed bundle.

## Root cause

The mini-apps are shipped as **prebuilt, git-tracked** bundles under
`src/cerebro_mcp/static/`. The server reads those, not `ui/src`. So source and
served artifact are two different things that drift apart silently.

## Forbidden action

Claiming a UI change works — or that a UI bug is fixed — without having run the
app's build target. And debugging a served-bundle bug under `make dev`, which
cannot reproduce it.

## Detection

Compare the mtime of the edited source against the corresponding
`src/cerebro_mcp/static/assets/<app>/*` files. `git status` showing modified
`ui/src` with unmodified `static/` is the signature.

## Safe remediation

`make build-ui-<app>` — the per-app targets deliberately build *and* copy into
`static/`, so one target plus a restart is self-contained. `make build-ui` rebuilds
all 11. Shared components (`ChartCard`, `themes/global.css`, the canvas subsystem)
mean an edit in one place can require rebuilding every app.

## Enforcement

Runtime cache invalidation is guarded (`test_web_bundle_cache_invalidates_on_file_change`
— the bundle cache is keyed on `(mtime_ns, size)`, so a rebuild is picked up
without a restart). Nothing guards "you forgot to rebuild".
