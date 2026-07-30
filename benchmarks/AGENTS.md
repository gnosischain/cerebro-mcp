# Benchmarks — scoped guide

## Gate vs trend

- **Correctness and efficiency are the GATE** — deterministic, machine-independent,
  and CI/pre-push blocks on them.
- **Latency is a TREND only.** Never gate on it; it varies with the machine.

`make bench-check` runs the gate. `.githooks/pre-push` runs `make bench-check`, but
the hook is **opt-in**: `git config core.hooksPath .githooks`.

## Rules

- Never run the benchmark package under pytest — it is a CLI
  (`python -m benchmarks.run` / `benchmarks.compare`).
- Fixtures are **recorded**, with a named regeneration script. A fixture that drifts
  untraceably makes every comparison meaningless.
- A golden-query change is a deliberate ranking decision: update the pair **in the
  same change set, with a note** saying why.
- A no-op path that consumes budget has regressed into doing real work — that is an
  ERROR, not a slow pass.
