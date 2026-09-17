---
id: docker-rebuilds-ui-from-its-own-list
title: >-
  The image does not ship the committed bundles — it wipes them and rebuilds
  from a list only the Dockerfile holds, so a new mini-app is blank in
  production while working everywhere locally
status: observed
layer: build-deploy
scope: >-
  every mini-app added to ui/vite.config.ts ENTRY_MAP. The split-bundle apps
  (SPLIT_BASE) are the ones that break silently; an inline app carries its
  script inside the HTML and survives.
symptom: >-
  a newly added mini-app renders a completely blank page in the deployed
  environment and works perfectly on localhost; the page HTML returns 200 and
  its JS returns 404, with no error in any log
last_verified: 2026-09-17
evidence:
  - >-
    verified 2026-09-17 in production: the running pod was on the correct new
    image (ef9af5d, ready, 0 restarts) and `ls static/assets/` inside the
    container showed cow_explorer, data_catalog, governance, graph_explorer and
    NO pools_explorer, while static/pools_explorer.html was present
  - >-
    verified 2026-09-17 over HTTPS: every route returned 401 without a token
    EXCEPT /app/pools_explorer/assets/<hash>.js, which returned 404 — the
    mismatched status is what localised it to a missing file rather than auth
  - >-
    the 37 asset files were correctly committed and present in origin/main; the
    image lacked them anyway, which is the whole point of the record
  - Dockerfile `RUN rm -rf src/cerebro_mcp/static/assets` immediately before the per-app COPY lines
  - tests/test_docker_ui_entries.py (9 tests, each mutation-checked against the shipped bug)
  - >-
    fix in tree 2026-09-17, pending deploy — status stays observed until an
    image built from the corrected Dockerfile is running
---

## Symptom

A new mini-app is a blank page in the deployed environment. The page itself
loads (200), the pod is on the right image, is ready and has not restarted, and
nothing appears in any log. `make dev` and `make serve-catalog` both render it
perfectly. Every other app in the same deployment is fine.

## Root cause

The Dockerfile does not ship the bundles committed to `static/`. It runs
`rm -rf src/cerebro_mcp/static/assets`, rebuilds every app inside a `ui-builder`
stage, and copies the results back — with the app list, the build commands and
the COPY destinations all written out by hand. That list and `ENTRY_MAP` in
`ui/vite.config.ts` are the same list written twice, plus a third time in the
Makefile's `build-ui` fan-out, and nothing tied them together. The comment above
it says "mirrors `make build-ui`", which is a statement of intent, not a
mechanism.

An app added to Vite and the Makefile but not the Dockerfile therefore ships its
HTML — a tracked file the Dockerfile never overwrites, so it survives — with its
JS and CSS deleted by the `rm -rf` and never rebuilt. Hence a blank page rather
than a 404 on the page, and hence no error: the browser asks for a module script
that is not there and renders nothing.

Local development cannot see it. `make dev` serves live source; the standalone
web app serves the committed bundles. Only the image rebuilds. It works on every
machine and is broken in the only place that matters.

## Forbidden action

Adding an entry to `ENTRY_MAP` without adding the matching build command AND the
matching asset COPY to the Dockerfile. Also: concluding from "the pod is on the
new image and healthy" that the deploy delivered the change.

## Detection

Statically, `tests/test_docker_ui_entries.py`. In a live environment, the
diagnostic that localised it is the status-code mismatch: fetch the page and one
of its hashed assets, and compare. Equal statuses (401/401) means auth; a page
that answers while its asset 404s means the file is absent from the image.
`kubectl exec <pod> -- ls static/assets/` settles it in one command.

## Safe remediation

Add both halves to the Dockerfile — the `CEREBRO_UI_ENTRY=<entry>` build and the
`COPY --from=ui-builder /ui/dist-<app>/assets/ src/cerebro_mcp/static/assets/<dir>/`
— then rebuild the image and redeploy. Note the asset directory is NOT derivable
from the camelCase entry name: `graphExplorerWeb` serves out of
`static/assets/graph_explorer/`. Read the destination from that entry's
`SPLIT_BASE` value, which is what the built HTML actually requests.

## Enforcement

`tests/test_docker_ui_entries.py` pins all three copies of the list together:
every Vite entry is built by the Dockerfile, every split-bundle app has its
assets copied to the directory its `SPLIT_BASE` names, and `make build-ui` fans
out to all of them. A fourth test asserts the `rm -rf` is still present, so the
guards fail loudly if the fragility they exist for is ever removed rather than
silently protecting nothing. Each was mutation-checked by reintroducing the
shipped bug. Reaching `enforced` requires a corrected image to be deployed.
