---
id: unexported-build-stage-never-caches
title: An intermediate image stage re-runs on every build unless the cache exports it and a .dockerignore pins the context
status: observed
layer: build-deploy
scope: Dockerfile multi-stage builds and the CI workflows that invoke them — anything with a `COPY --from=<stage>` whose producer is not in the final image
symptom: 'CI takes as long for a one-line change as it does from scratch; a build stage that nothing touched re-runs every push'
last_verified: 2026-07-31
evidence:
  - Dockerfile:7 (ui-builder stage) and Dockerfile:13-23 (11 vite builds in ONE RUN behind ONE `COPY ui/ .`)
  - '.github/workflows/build-and-release.yml — was two full `docker buildx build` calls, no --cache-from/--cache-to, both --platform linux/amd64,linux/arm64'
  - 'measured 2026-07-31 (colima, buildkit v0.17.3, linux/arm64): cold ui-builder stage 245.1s; after the fix a Python-only change rebuilt in 32s total with the vite RUN logged CACHED; 11/11 bundle sha256 identical cold vs cached; adding one file under ui/src re-ran the stage'
  - 'context transferred fell to 43.61MB with a .dockerignore, from a ~1.8GB working tree'
  - fix in tree 2026-07-31, pending deploy — status stays `observed` until a push to main proves the cache restores across runs
---
## Symptom

The image build costs the same whether you changed one Python line or everything.
Nothing errors and nothing looks misconfigured — a build that rebuilds is exactly
what a build looks like, so the cost reads as inherent to the project rather than as
a defect. Here it was 245s of mini-app compilation on every push to main, four times
over, to produce byte-identical HTML.

## Root cause

Four things compound, and fixing any one alone changes nothing:

1. **No cache flags.** `docker buildx build` with no `--cache-from` / `--cache-to`
   has nothing to restore from between CI runs. The local layer cache a developer
   relies on does not exist on a fresh runner.
2. **`mode=max` is load-bearing.** Even once a cache is wired, the default
   `mode=min` exports only the layers of the **final** stage. An intermediate
   builder stage is not in the final image, so its layers are discarded and it
   re-runs every time — the stage you most wanted to skip is precisely the one the
   default drops.
3. **The build context is the cache key.** `COPY ui/ .` keys on the content of
   everything the context carries. With no `.dockerignore`, untracked local files —
   `node_modules`, `dist/`, a stray log — shift the key and bust the stage. Locally
   it is worse than a miss: a host `ui/node_modules` copied over the layer that just
   ran `npm ci` silently replaces the installed tree.
4. **A stage with no `--platform` is built once per target arch.** Under
   `--platform linux/amd64,linux/arm64` a node stage emitting arch-independent HTML
   runs twice, the second time emulated under QEMU.

## Forbidden action

Concluding an expensive stage "has to" re-run. Also: adding `--cache-to` without
`mode=max` and believing an intermediate stage is now cached — that combination
looks correct in the workflow file and caches nothing you care about.

## Detection

Read the build log, not the wall time. Every layer BuildKit restored prints
`CACHED`; a stage that re-ran prints its command output. If the expensive stage has
no `CACHED` line on a build that changed nothing in its inputs, the cache is not
covering it. `#N transferring context: <size>` on the first lines tells you whether
the context is pinned.

## Safe remediation

`.dockerignore` first — without a stable context the other two changes cannot land a
hit. Then `--cache-from` / `--cache-to ...,mode=max` (registry cache rather than
`type=gha`: no 10GB budget, no eviction, survives across branches). Then
`FROM --platform=$BUILDPLATFORM` on any stage whose output is arch-independent.
Collapse multi-tag pushes into one invocation — two `buildx build` calls are two
full builds.

**Test both directions.** A cache that never misses is worse than none: it ships a
stale artifact. Prove the stage is `CACHED` when its inputs are untouched AND that
it re-runs when one file under its input tree changes, then diff the produced
artifacts against a cold build. Here that was 11 matching `sha256sum`s — the same
concern as [[stale-prebuilt-miniapp-bundle]], one layer down.

## Enforcement

None. No test can assert on CI wall time, and the failure is invisible to
`make bench-check`. The `build_and_gates` profile now resolves for `Dockerfile`,
`.dockerignore` and `.github/workflows/`, which it previously did not — a change
packet for those paths returned global rules only, so nothing pointed at this class
while the build was being edited.
