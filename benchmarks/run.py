"""Benchmark runner CLI.

    uv run python -m benchmarks.run --suite latency|load|workflows|search|semantic
        [--mode inprocess|sse] [--iters N] [--concurrency 1,4,8,16] [--duration 20]
        [--port 8091] [--only GLOB] [--tag NAME] [--replay] [--replay-last N]
        [--update-golden] [--keep-scratch]

Real ClickHouse is enabled by ``CEREBRO_EVAL_CLICKHOUSE=1`` (repo convention).
``CEREBRO_EVAL_LIVE_REGISTRY=1`` additionally enables the semantic suite's
live-registry coverage section (implied by ``CEREBRO_EVAL_CLICKHOUSE=1``).

IMPORTANT: this module must not import ``cerebro_mcp`` (or any benchmarks
module that does) before the scratch-env redirection in ``main()`` —
``cerebro_mcp.config.Settings()`` reads env at import time.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Every writable path a benchmark run may touch, redirected into scratch.
_SCRATCH_ENV = {
    "CEREBRO_REPORT_DIR": "reports",
    "THINKING_LOG_DIR": "logs",
    "EVENT_STORE_PATH": "cerebro_state.db",
    "CEREBRO_RESEARCH_DIR": "research",
    "MCP_SECURITY_LOG_DIR": "security_audit",
    "CEREBRO_SAVED_QUERIES_DIR": "saved_queries",
    "ASYNC_RESULT_DIR": "query_results",
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="benchmarks.run", description=__doc__)
    p.add_argument("--suite", required=True,
                   choices=["latency", "load", "workflows", "search", "semantic", "templates"])
    p.add_argument("--mode", choices=["inprocess", "sse", "headless"], default=None,
                   help="default: per-suite (load->sse, templates->headless, others->inprocess)")
    p.add_argument("--model", default=None,
                   help="templates suite: model for headless agent runs "
                        "(default claude-sonnet-5)")
    p.add_argument("--iters", type=int, default=None)
    p.add_argument("--warmup", type=int, default=None)
    p.add_argument("--concurrency", default="1,4,8,16",
                   help="load suite: comma-separated worker counts")
    p.add_argument("--duration", type=int, default=20, help="load suite: seconds per cell")
    p.add_argument("--max-heavy-concurrency", type=int, default=8)
    p.add_argument("--port", type=int, default=8091,
                   help="load suite: FASTMCP_PORT for the spawned server")
    p.add_argument("--only", default=None, help="case-id glob filter")
    p.add_argument("--tag", default=None, help="free-form tag recorded in params")
    p.add_argument("--out", default=None, help="results directory override")
    p.add_argument("--replay", action="store_true",
                   help="workflows suite: score recorded session traces instead")
    p.add_argument("--replay-last", type=int, default=None)
    p.add_argument("--update-golden", action="store_true",
                   help="semantic suite: rewrite tests/fixtures/semantic_sql_golden.json")
    p.add_argument("--keep-scratch", action="store_true")
    p.add_argument("--print-json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    results_dir = Path(args.out) if args.out else _RESULTS_DIR
    scratch = results_dir / ".scratch" / f"{run_id}_{args.suite}"
    scratch.mkdir(parents=True, exist_ok=True)

    # Replay reads pre-existing traces from the REAL log dir — capture before redirect.
    replay_log_dir = os.environ.get("THINKING_LOG_DIR", ".cerebro/logs")

    # ---- env redirection: MUST precede any cerebro_mcp import ----------------
    for var, sub in _SCRATCH_ENV.items():
        target = scratch / sub
        if not sub.endswith(".db"):
            target.mkdir(parents=True, exist_ok=True)
        os.environ[var] = str(target)
    os.environ.setdefault("REPORT_AUTO_OPEN", "false")
    os.environ.setdefault("SEMANTIC_AUTOLOAD_ON_LOCAL_MTIME", "false")
    # ---------------------------------------------------------------------------

    from benchmarks.core import envinfo
    from benchmarks.core.results import ERROR, RunResult, write_run
    from benchmarks.core.runner import DEFAULT_MODE, BenchContext, get_suite

    real_ch = os.environ.get("CEREBRO_EVAL_CLICKHOUSE") == "1"
    live_registry = real_ch or os.environ.get("CEREBRO_EVAL_LIVE_REGISTRY") == "1"
    mode = args.mode or DEFAULT_MODE[args.suite]

    ctx = BenchContext(
        suite=args.suite,
        mode=mode,
        real_clickhouse=real_ch,
        live_registry=live_registry,
        scratch_dir=scratch,
        results_dir=results_dir,
        iters=args.iters,
        warmup=args.warmup,
        concurrency=[int(n) for n in str(args.concurrency).split(",") if n.strip()],
        duration_s=args.duration,
        max_heavy_concurrency=args.max_heavy_concurrency,
        port=args.port,
        only=args.only,
        update_golden=args.update_golden,
        replay=args.replay,
        replay_last=args.replay_last,
        extra={"replay_log_dir": replay_log_dir, "model": args.model},
    )

    # Templates suite: deliverables/traces/review verdicts must survive for
    # inspection (and each run costs real money) — always keep scratch.
    if args.suite == "templates":
        args.keep_scratch = True

    suite_mod = get_suite(args.suite)
    supported = getattr(suite_mod, "SUPPORTED_MODES", frozenset({"inprocess", "sse"}))
    if mode not in supported:
        print(f"suite {args.suite!r} does not support mode {mode!r} (supported: {sorted(supported)})")
        return 2

    print(f"[bench] suite={args.suite} mode={ctx.mode_label} run_id={run_id} scratch={scratch}")
    failed = False
    try:
        cases = suite_mod.run(ctx)
    except Exception:
        failed = True
        raise
    finally:
        if failed or args.keep_scratch:
            print(f"[bench] scratch kept at {scratch}")

    finished = datetime.now(timezone.utc)
    run = RunResult(
        run_id=f"{run_id}-{envinfo._git('rev-parse', '--short', 'HEAD') or 'nogit'}",
        suite=args.suite,
        mode=ctx.mode_label,
        started_at=started.isoformat(timespec="seconds"),
        finished_at=finished.isoformat(timespec="seconds"),
        environment=envinfo.collect_environment(**ctx.extra.get("environment", {})),
        params={
            "iters": args.iters, "warmup": args.warmup, "concurrency": ctx.concurrency,
            "duration_s": args.duration, "only": args.only, "tag": args.tag,
            "replay": args.replay, "update_golden": args.update_golden,
        },
        cases=cases,
    )
    path = write_run(run, results_dir)
    summary = run.summary
    print(f"[bench] {summary['cases']} cases: {summary['ok']} ok, "
          f"{summary.get('over_budget', 0)} over budget, {summary['skipped']} skipped, "
          f"{summary['error']} error")
    print(f"[bench] results: {path}")
    if args.print_json:
        import json
        print(json.dumps(run.to_dict(), indent=2, default=str))

    has_errors = any(c.status == ERROR for c in cases)
    if not (args.keep_scratch or has_errors):
        shutil.rmtree(scratch, ignore_errors=True)
    for case in cases:
        if case.status == ERROR:
            print(f"[bench] ERROR {case.id}: {(case.error or '')[:300]}")
    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
