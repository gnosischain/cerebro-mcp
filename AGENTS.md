# cerebro-mcp — agent guide

The canonical guide for **changing this repo**. Vendor-neutral; `CLAUDE.md` is a
thin shim that imports it and adds the analyst-runtime workflow.

If you are here to *use* the platform (query data, build a report), you want
`CLAUDE.md` and `get_agent_persona(...)`. Everything below is about modifying the
code.

## Required workflow for any code change

1. **Get the change packet.** `get_cerebro_change_context(paths="<path you will
   edit>")` returns the rules, known hazards, guides and validation commands for
   that layer. **Until you have run it, do not assume you know that layer's failure
   modes** — over half of the recorded lessons were originally misdiagnosed as
   something else.
2. **Read the scoped guide** it names (see below).
3. Make the change.
4. **Validate** — see Gates.
5. **Record a new mistake class** if you diagnosed one: `docs/workflows/incident.md`
   (or `/incident`). Evidence required.
6. **Stop.** The user commits their own work. Never `git commit`, `git push`, or
   stage.

## Where knowledge lives

- **`src/cerebro_mcp/prompts/lessons/INDEX.md`** — mistake classes this repo has
  already paid for, with a status (`observed → remediated → enforced`) and evidence.
  **Check here before diagnosing a symptom.** Searchable via
  `search_cerebro_knowledge(query)`.
- `src/cerebro_mcp/prompts/lessons/profiles.yml` — which lessons apply to which
  paths. Profiles match path *classes*; they never enumerate files.
- **dbt models are a different corpus.** `get_dbt_change_context` /
  `search_dbt_knowledge` serve dbt-cerebro's lessons over a remote artifact. Do not
  look for cerebro-mcp lessons there, or vice versa.
- `docs/` — engineering design docs (`MINI_APPS.md`, `phase*_*.md`,
  `memory_and_resume.md`) and analyst-facing conceptual docs (`measurement/`).
  Two files in there are de-facto postmortems rather than live specs:
  `graph_explorer_forensic_assessment.md` and `WS10_MINI_APPS_UX_PASS.md`.

Status describes what is **deployed**, never your working tree. A fix that exists
only locally is at most `observed` with a "pending deploy" note.

## Layout

| Path | What it is |
|---|---|
| `src/cerebro_mcp/server.py` | FastMCP server; ~192 tool registrations + the MCP instructions block |
| `src/cerebro_mcp/tools/` | tool implementations, grouped by domain (`analytics/`, `visualization/`, `semantic/`, `governance/`, `web3/`, `workflow/`) |
| `src/cerebro_mcp/tools/visualization/queries/` | hand-written `.sql` for the mini-app planes, loaded by `sql_loader` |
| `src/cerebro_mcp/prompts/agents/` | 35 analyst personas + 2 shared contracts, served by `get_agent_persona` |
| `src/cerebro_mcp/prompts/lessons/` | this repo's lesson corpus (package data) |
| `src/cerebro_mcp/semantic/` | model search, SQL compiler, graph profiles |
| `src/cerebro_mcp/loaders/` | manifest / semantic registry / knowledge-artifact loaders |
| `src/cerebro_mcp/static/` | **generated** — prebuilt mini-app bundles, git-tracked |
| `ui/src/mini-apps/` | React sources for the mini-apps |
| `benchmarks/` | regression harness (correctness = gate, latency = trend) |

## Commands

```bash
.venv/bin/python -m pytest tests/ -q      # ~2,104 pass / 19 skip
npm test --prefix ui                      # ~820
make bench-check                          # THE gate: pytest + deterministic suites
make build-ui                             # all 11 mini-app bundles
make build-ui-<app>                       # one app: builds AND copies into static/
make dev                                  # serves LIVE source, not the bundles
```

## Gates

- **`make bench-check`** is the deterministic regression gate — it runs
  `pytest tests/` plus the search/workflows/semantic suites (never latency, which is
  machine-dependent). `.github/workflows/benchmarks.yml` runs it on every PR and
  push to main, so anything in `tests/` is CI-enforced. See `benchmarks/README.md`.
- **There is no *style* lint gate, with one exception.** `ruff` is not in the dev
  dependencies, and running it with defaults reports ~29 findings on untouched files.
  Match the surrounding style rather than reformatting to satisfy it.
  The exception is **`make lint-undefined`** (`ruff check --select F821`, run via
  `uvx`), which `bench-check` depends on so it gates CI and pre-push. F821 alone is
  clean on this repo, so it costs nothing and catches a class the test suite
  structurally cannot: a name that only resolves when a rarely-exercised branch runs.
  It was added after `settings` was used in `loaders/artifacts.py` without being
  imported — the broad `except Exception` around the fetch turned the `NameError`
  into a log warning, so the semantic registry, catalog, docs index and graph
  catalog all silently failed to load in production while the full suite passed and
  every health probe stayed green. Do not widen the rule selection without a
  separate decision; the value here is that it is zero-noise.
- **`.githooks/pre-push` runs it, but is opt-in.** Enable with
  `git config core.hooksPath .githooks`. Nothing enables it for you.
- `tests/test_cerebro_lessons.py` validates the lesson corpus (id/filename, status
  vocabulary, required sections, evidence, index completeness both ways, profile
  references, staleness, retrieval quality).
- `.claude/hooks/bash_guard.py` warns before known-dangerous commands, citing the
  lesson. It is **advisory** — it asks, never denies. Tests and CI are the
  authority.

## Rules

- **SQL lives in a `.sql` file, never in Python.** Every query and every reusable
  fragment in the mini-app query planes is its own file under
  `src/cerebro_mcp/tools/visualization/queries/<app>/`; Python composes named
  fragments and passes parameters. Fragments are files too, prefixed by kind:
  `_cte_*`, `_pred_*`, `_join_*`, `_anchor_*`, `_expr_*`. A fragment's comment
  header is stripped before substitution so it can carry its rationale without
  landing inside the rendered statement. Rationale and the one carve-out are in
  `queries/AGENTS.md`; enforced by `tests/test_sql_lives_in_files.py`.
  **Scope note:** this is enforced for the mini-app planes only. Elsewhere in the
  repo SQL is still built in Python — the semantic layer's `sql_compiler.py` /
  `flow_queries.py` / `graph_profiles.py` emit SQL by design, and
  `workflow/event_store.py` carries embedded SQLite DDL. Do not "fix" those to
  satisfy this rule without asking; do not add new inline SQL to the planes that
  are clean.
- **The user commits their own work.** Never `git commit`, `git push`, or stage.

## Cross-cutting traps

Full records in the lessons index; the short version:

- **`FINAL` has three branches, not one rule** — mandatory on raw
  ReplacingMergeTree tables, forbidden on canonical `v_*` views, forbidden on large
  tables where it OOMs. Six files state this and read as contradictory;
  `ch-final-three-way-rule` is the arbiter.
- **A CTE is inlined per reference**, not materialised. Referencing one twice scans
  twice.
- **An output alias shadows the same-named column**, so a `WHERE` on it silently
  returns nothing. No test guards this anywhere.
- **Never index an upstream-versioned array by position** — and a
  `length(...) = N` guard is not a schema check, it is a silent filter.
- **The mini-apps are served from prebuilt bundles.** A source edit changes nothing
  until `make build-ui-<app>`; and `make dev` cannot reproduce a bundle bug.
- **Editing a `.sql` needs a server restart** — `sql_loader` is `lru_cache`d.
- **A failed dataset must stay visible** as a stub; a deliberate exclusion must be
  counted. Neither may silently vanish.

## Scoped guides

Read the one for the directory you touch:

- `src/cerebro_mcp/tools/visualization/AGENTS.md` — mini-app backends, QuerySpec
- `src/cerebro_mcp/tools/visualization/queries/AGENTS.md` — the SQL planes
- `ui/src/mini-apps/AGENTS.md` — React / ECharts / WebGL front-ends
- `benchmarks/AGENTS.md` — the regression harness

Vendor-neutral workflows: `docs/workflows/`.
