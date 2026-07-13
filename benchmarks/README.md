# Cerebro-MCP Benchmark Harness

Five suites measuring the MCP server from different angles, with timestamped
JSON results and a compare command that flags regressions between runs.

```
uv run python -m benchmarks.run --suite latency|load|workflows|search|semantic
uv run python -m benchmarks.compare BASELINE.json CANDIDATE.json   # or "latest"
```

Make shortcuts: `make bench-latency`, `bench-workflows`, `bench-search`,
`bench-semantic`, `bench-load`, `bench-latency-real`,
`bench-compare BASE=... CAND=...`.

## How this fits the workflow: gate vs. trend

There are two kinds of signal here, and they are handled differently on purpose.

- **Correctness / efficiency = the gate (enforced).** The `search`, `workflows`,
  and `semantic` suites run fully in-process against the committed fixtures and
  produce the *same answer on any machine*. They self-gate: a lost hit@5, an
  SQL-golden AST-hash mismatch, a new orphan metric, or a broken workflow gate
  makes `benchmarks.run` exit non-zero. One command runs all three plus pytest:

  ```
  make bench-check          # pytest + search + workflows + semantic; exits non-zero on regression
  ```

  This is the local pre-push ritual AND the CI job
  ([.github/workflows/benchmarks.yml](../.github/workflows/benchmarks.yml)) that
  runs on every PR and push — a correctness regression cannot merge. No
  ClickHouse, no secrets, a few seconds on a plain runner. It needs no committed
  perf baseline: the "baselines" are the pinned fixtures/goldens
  (`tests/fixtures/*.gz`, `semantic_sql_golden.json`), refreshed deliberately via
  `--update-golden` / `record_routing_registry.py` in the same change that moves
  them.

- **Latency = a trend (informational, never gated).** `p50`/`p95` depend on the
  machine, so a laptop number can't be enforced on a shared CI runner without
  false failures. Latency is reviewed locally:

  ```
  make bench-latency          # (+ bench-latency-real, bench-load when a warehouse is available)
  make bench-report           # open benchmarks/results/index.html — trend sparklines per tool
  uv run python -m benchmarks.compare <prev>.json <new>.json   # or "latest"
  ```

  `bench-compare` does **not** guard against cross-machine comparison — latency
  is only meaningful against another run on the *same host*. It never runs in CI.

Optional local automation: a pre-push hook is provided at
[`.githooks/pre-push`](../.githooks/pre-push) that runs `make bench-check`
before every push. It's opt-in — enable with `git config core.hooksPath
.githooks` (disable with `git config --unset core.hooksPath`). CI enforces the
same gate regardless, so the hook is just a faster local feedback loop.

## Exploring results — the HTML dashboard

```
make bench-report          # -> benchmarks/results/index.html
```

`benchmarks/report.py` reads every `results/*.json` and renders one
self-contained, theme-aware page: an overview, a suite-tailored table per run
(latency budgets with p50/budget bars, semantic coverage tiles + routing
distribution + stage timings, search hit@k, workflow call efficiency, load
concurrency scaling), and a per-tool p50 **trend** sparkline wherever a
suite+mode has more than one run. Filter by suite with the chips; toggle
light/dark. Re-run after each benchmark to refresh. Open the file directly, or
`make`-serve it (there is a `bench-report` entry in `.claude/launch.json` on
port 5193). The generated `index.html` is gitignored alongside the JSON.

## Load suite: keep the sweep small

The default load grid (4 workloads × concurrency `1,4,8,16` × 20s) is heavy —
and because tool dispatch serializes (~2.5s per call under concurrency), a full
run takes many minutes and looks stuck. For a quick check use a smaller sweep:
`uv run python -m benchmarks.run --suite load --concurrency 1,4 --duration 8`.

## Suites and what they own

| Suite | Measures | Mode |
|---|---|---|
| `latency` | p50/p95 per tool vs budgets, all 18 core tools + representatives | in-process (fake CH default, real via env) |
| `load` | SSE concurrency sweep: TTFB, latency under load, throughput, error rate | spawned `cerebro-mcp --sse` on port 8091 (real CH only) |
| `workflows` | Scripted SOP workflows (CLAUDE.md tiers): tool-call count vs optimal, gate compliance, response chars; `--replay` scores recorded session traces | in-process |
| `search` | hit@1/3/5 + MRR across the 4 search surfaces; find/preflight route correctness; discovery precision | in-process, fully deterministic |
| `semantic` | Registry build/refresh, routing latency + preflight cache, planner modes, compiled-SQL goldens (normalized-AST hash), query_metrics E2E incl. repair path, registry coverage health, semantic chart tools | in-process |

Ownership boundaries: Suite `search` owns route **correctness** (per-query
pass/fail); suite `semantic` owns route **economics** (latency, cache,
aggregate distribution). Suite `latency` keeps one smoke-level budget per
semantic tool; `semantic` is the authoritative owner of semantic budgets and
stage-level internals — a semantic budget change happens in
`benchmarks/cases/semantic_cases.py`, never in the latency suite.

## Environments

- **Default**: deterministic in-process. ClickHouse is faked
  (`core/fakes.BenchClickHouse`), the manifest and semantic registry come from
  pinned fixtures (`tests/fixtures/search_corpus.json.gz`,
  `tests/fixtures/routing_registry.json.gz`). Reproducible run-to-run.
- **`CEREBRO_EVAL_CLICKHOUSE=1`**: real ClickHouse (repo convention shared
  with `tests/eval`). Enables real-mode latency cases and the load suite.
- **`CEREBRO_EVAL_LIVE_REGISTRY=1`**: semantic suite's coverage section reads
  the live registry (implied by `CEREBRO_EVAL_CLICKHOUSE=1`).

## Never run under pytest

The pytest suite installs an autouse fixture that strips the `@offloaded`
thread-pool hop from heavy tools — running benchmark code under pytest would
silently measure a different code path than production. `python -m
benchmarks.run` never imports conftest. Files here deliberately avoid
`test_*.py` names so bare `pytest` does not collect them.

## ClickHouse safety (real mode)

All benchmark SQL is read-only by construction (session `readonly=1`, 30s
`max_execution_time`, 4 GiB memory cap), date-bounded and `LIMIT`ed, against
`api_*`/`int_*` marts — never raw `execution.*`/`consensus.*` tables. The load
suite caps heavy-workload concurrency at 8 by default
(`--max-heavy-concurrency` to override): N × 4 GiB-cap queries is an outage
vector on the shared instance.

## Results, scratch, artifacts

One run writes one file: `benchmarks/results/<UTCts>_<suite>_<mode>.json`
(gitignored). Raw samples are kept per case so compare can re-derive stats.
Every writable server path (reports, thinking logs, event store, research
dir, security audit, saved queries) is redirected into
`benchmarks/results/.scratch/<run_id>/` before any `cerebro_mcp` import;
scratch is deleted on success, kept on failure or `--keep-scratch`.

## Compare semantics

Latency regression = worse than baseline by BOTH >25% (`--pct`) and >20ms
(`--abs-ms`) on p50/p95. `ok -> error` always fails. Suite-specific rules:
workflows fail on any tool-call-count increase or unexpected gate block;
search fails on a lost hit@5; semantic refuses latency diffs across differing
fixture hashes, fails on new orphan metrics or a >5% approved-metric drop,
warns on coverage decreases. Exit codes: 0 clean, 1 regressions,
2 incomparable.

## Updating pinned baselines

Pinned inputs (golden search pairs, routing cases, workflow queries, SQL
goldens) deliberately freeze behavior. When a deliberate change breaks one,
update the pinned case **in the same changeset** with a note — same policy as
`tests/test_search_quality.py`. SQL goldens: `--suite semantic
--update-golden` rewrites `tests/fixtures/semantic_sql_golden.json`; the
routing registry fixture is re-recorded via
`tests/fixtures/record_routing_registry.py` (deliberately, never gratuitously).
