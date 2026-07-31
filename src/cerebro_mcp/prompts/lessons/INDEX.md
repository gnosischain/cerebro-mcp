# Lessons index — cerebro-mcp's own development

Mistake classes this repo has already paid for. **Check here before diagnosing a
symptom, and before writing SQL, a mini-app panel, or a build gate.**

Each record carries a status describing what is **deployed**, never what is in your
working tree:

- `proposed` — idea, no instance yet
- `observed` — seen, no safeguard; recurrence is likely
- `remediated` — the instance is fixed and merged, but nothing prevents recurrence
- `enforced` — a test, gate or code change prevents recurrence

A fix that exists only in the working tree is at most `observed` with a
"pending deploy" evidence line. It re-arms on every fresh clone until merged.

New mistake class? Use `/incident` (evidence required) — see
[docs/workflows/incident.md](../../../../docs/workflows/incident.md).

This corpus is about **this repo**. For dbt model hazards use
`get_dbt_change_context` / `search_dbt_knowledge`, which serve dbt-cerebro's
separate corpus.

## Silent wrong answers (highest blast radius)

- [ch-output-alias-shadows-column](ch-output-alias-shadows-column.md) `observed` —
  an output alias shadows the same-named source column, so a `WHERE` filters on the
  computed value and the query returns **zero rows with no error**. No test anywhere.
- [versioned-payload-positional-index](versioned-payload-positional-index.md)
  `enforced` — indexing an upstream-versioned array by position silently zeroed 26.4%
  of delegated voting power; the obvious fix would have swapped two chains across
  44,635 votes without changing any total.
- [ch-bare-limit-nondeterministic](ch-bare-limit-nondeterministic.md) `observed` —
  `LIMIT` without a total `ORDER BY` returned 17 distinct result sets in 20 calls.
- [dataset-column-order-is-a-contract](dataset-column-order-is-a-contract.md)
  `observed` — reordering a SELECT list re-labelled every field for consumers that
  read rows positionally.
- [dbt-sqlx-silently-not-compiled](dbt-sqlx-silently-not-compiled.md) `observed` — a
  model renamed to `.sqlx` left dbt's DAG with no error and froze ~12 days behind
  chain head; a whitelist in the same model claimed "COMPLETE · 7 of 7" for a 9-leg
  transaction.
- [wire-handler-binds-at-init](wire-handler-binds-at-init.md) `observed` — the
  `list_tools` visibility filter was installed by attribute assignment, but FastMCP
  binds handlers in `__init__`: every test passed while the wire served all 187
  tools unfiltered (app-only included) and `LEAN_CORE_ENABLED` was a no-op.
- [global-latest-state-serves-strangers](global-latest-state-serves-strangers.md)
  `observed` — process-global "latest"/registry state on a stateless multi-user
  transport is keyed by "whoever asks next": one report's finish wiped another
  conversation's discovery mid-analysis, and the latest-visual resource served one
  user's data to anyone within 600 s.

## ClickHouse platform

- [ch-final-three-way-rule](ch-final-three-way-rule.md) `remediated` — **the
  arbiter.** `FINAL` is mandatory on raw ReplacingMergeTree tables, forbidden on
  canonical views that dedup internally, and forbidden on large tables where its
  whole-table merge blows the memory cap. Six files state this and read as
  contradictory; this record resolves them.
- [ch-cte-inlined-per-reference](ch-cte-inlined-per-reference.md) `enforced` — a CTE
  is substituted per reference, not materialised; a 4-reference CTE exhausted the
  2 GiB cap. The guard for it had a line-initial-regex bug that made it check nothing.
- [ch-alias-in-where-illegal-aggregation](ch-alias-in-where-illegal-aggregation.md)
  `enforced` — an aggregate alias beside a same-level `WHERE` raises code 184, and
  only on the widest scope arm.
- [sql-guard-counts-comments-as-code](sql-guard-counts-comments-as-code.md)
  `enforced` — a textual SQL guard reads the file's comment header as code: prose
  saying "the FINAL SELECT" tripped a `no FINAL` assert, and five prose mentions of
  a CTE scored as five extra scans — which is also why the reference ceiling had
  been set to 2. The false-negative direction is worse: documenting a fix passes the
  test for having made it.

## Verification method

- [live-table-invalidates-cross-query-diff](live-table-invalidates-cross-query-diff.md)
  `remediated` — a before/after diff run as two separate queries against a
  continuously written table measures the write, not the change. The same query
  returned 1118 then 1119 rows minutes apart. Put both forms in ONE query, or prove
  each changed piece separately.

## Mini-app UI

- [shared-stylesheet-unscoped-selectors](shared-stylesheet-unscoped-selectors.md)
  `enforced` — two unscoped rules in a 4,000-line shared stylesheet misaligned chart
  grids in a different app two tabs away; the follow-on mistake was adding a JS
  height hook to compensate for the broken cascade.
- [css-undefined-token-drops-rule](css-undefined-token-drops-rule.md) `enforced` —
  `var()` on a token that was never assigned discards the WHOLE declaration with no
  console warning; the governance Overview's top cards rendered as bare prose
  because three invented tokens silently dropped their border and background.
- [failed-dataset-must-stay-visible](failed-dataset-must-stay-visible.md) `observed` —
  a panel dropped on error reads as "no data" rather than "this failed". Three sites
  state the rule, none test it.
- [echarts-curveness-sign-is-relative-to-the-chord](echarts-curveness-sign-is-relative-to-the-chord.md)
  `enforced` — an arc's side is set by `curveness` times the sign of the chord, so
  deriving the sign from the same property that orders `coords` cancels out. Every
  GIP citation arc bowed downward past the y=0 floor and was clipped away; the guard
  had asserted the two signs were opposite, which was true and yet exactly the bug.

## Gates and workflow

- [late-gate-one-gap-at-a-time](late-gate-one-gap-at-a-time.md) `enforced` — the
  report gate fires only after every chart exists and used to name one unmet
  requirement per call. A campaign session with 28 queries and 7 charts was told
  "no dimensional breakdown", abandoned the report and wrote markdown files
  instead; it actually had TWO gaps, so complying once would have been refused
  again. A late gate that rations its feedback gets abandoned, not satisfied.

## Runtime and configuration

- [silenced-write-can-still-block](silenced-write-can-still-block.md) `enforced` —
  a `try/except` around every writer handles the wrong half: a write that BLOCKS is
  not an exception. `sqlite3.connect(timeout=...)` bounds only the BUSY handler, not
  `mkdir`/`open`/fsync/WAL, and the offload layer shields the thread — so one event
  write stranded a storyteller pipeline that had passed every gate, while a read-only
  tool on the same lock stayed instant. The fix's own traps: a `ThreadPoolExecutor`
  worker blocks interpreter exit, and the owner contextvar does not cross a thread.
- [default-off-flag-fails-silently](default-off-flag-fails-silently.md)
  `enforced` — a subsystem behind a default-off flag reads as healthy when it is
  simply not running. `SEMANTIC_ENABLED` defaults False, so the deployed remote
  served an empty graph catalog and no metric discovery while ClickHouse, the
  manifest and the docs index were all green — nothing in `system_status`
  distinguished it from a working server. Second instance of that observable, after
  a swallowed loader `NameError` produced exactly the same silence.

## Build, deploy and gates

- [stale-prebuilt-miniapp-bundle](stale-prebuilt-miniapp-bundle.md) `observed` — the
  mini-apps are served from git-tracked prebuilt bundles, so a source edit changes
  nothing until `make build-ui-<app>`; and a served-bundle bug cannot reproduce under
  `make dev`.
- [orphaned-sql-template-never-wired](orphaned-sql-template-never-wired.md)
  `enforced` — moving SQL into a .sql file is TWO changes; `activity.sql` was written
  and the three call sites kept building the same envelope in Python for three
  commits. Nothing caught it, and `available()`'s docstring claimed a registry test
  that did not exist — a comment asserting a guard stops the next person looking.
- [sql-loader-cache-needs-restart](sql-loader-cache-needs-restart.md) `observed` —
  a `.sql` edit does nothing until the server restarts, so a correct query looks
  wrong; pairs with the bundle trap above into "rebuild or restart, and the symptom
  will not tell you which".
- [negated-grep-passes-when-tool-absent](negated-grep-passes-when-tool-absent.md)
  `remediated` — `! rg …` returns success when `rg` is absent from `/bin/sh`'s PATH,
  so the dev-fixture leak gate had been passing unconditionally.
- [unexported-build-stage-never-caches](unexported-build-stage-never-caches.md)
  `observed` — the mini-app stage recompiled on every push because nothing exported
  it: no cache flags, and `mode=max` is what carries an INTERMEDIATE stage (the
  default `mode=min` keeps only the final one). Compounded by a missing
  `.dockerignore` — the context IS the `COPY` cache key — and by a stage with no
  `--platform`, which a multi-arch build runs once per arch.
