"""Templates suite — measures instruction templates through a REAL Claude agent.

Unlike every other suite this drives headless ``claude -p`` subprocesses (real
LLM, real tokens, real money) against the cerebro stdio server with real
ClickHouse. It therefore:

- requires ``CEREBRO_EVAL_CLICKHOUSE=1`` (cases hard-skip without it),
- runs strictly sequentially (the shared ClickHouse instance is OOM-prone under
  concurrent agent load, and parallel runs would skew each other's latency),
- keeps its scratch dir (run.py forces --keep-scratch for this suite) so every
  run's deliverables, traces, and review verdicts remain inspectable,
- adversarially reviews every delivered run (benchmarks/core/review.py) unless
  ``CEREBRO_BENCH_SKIP_REVIEW=1``.

Model selection: ``--model`` CLI arg (ctx.extra["model"]), default Sonnet 5.
Runs per template come from each template's frontmatter; ``--iters N``
overrides them all (used for the 1-run smoke).

Usage (smoke):
    CEREBRO_EVAL_CLICKHOUSE=1 CEREBRO_BENCH_CLAUDE_CONFIG_DIR=~/.claude-personal \
        uv run python -m benchmarks.run --suite templates \
        --only "templates/quick_scalar*" --iters 1
"""

from __future__ import annotations

import os
import statistics
from typing import Any

from benchmarks.cases.template_cases import TemplateCase, load_template_cases
from benchmarks.core.results import CaseResult
from benchmarks.core.runner import BenchContext

SUPPORTED_MODES = frozenset({"headless"})

DEFAULT_MODEL = "claude-sonnet-5"


def _median(values: list[float]) -> float | None:
    cleaned = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(cleaned), 3) if cleaned else None


def _run_case(ctx: BenchContext, case: TemplateCase, model: str, skip_review: bool) -> CaseResult:
    from benchmarks.core.headless import (
        extract_deliverable_content,
        fill_instructions,
        run_template,
    )

    try:
        instructions = fill_instructions(case.instructions, case.params)
    except ValueError as exc:
        return CaseResult.error_case(case.case_id, str(exc))

    runs = ctx.iters or case.runs
    records: list[dict[str, Any]] = []
    samples_ms: list[float] = []
    case_dir = ctx.scratch_dir / "templates" / case.id / model.replace("/", "_")

    attempt = 0
    completed = 0
    retried = False
    while completed < runs:
        attempt += 1
        run_dir = case_dir / f"run{attempt}"
        print(f"[templates] {case.id} model={model} run {completed + 1}/{runs} "
              f"(attempt {attempt}) -> {run_dir}")
        record = run_template(
            instructions=instructions,
            verify=case.verify,
            verify_personas=list(case.verify_personas),
            run_dir=run_dir,
            model=model,
            timeout_s=case.timeout_s,
            budget_usd=case.budget_usd,
        )
        if record.delivered and not skip_review:
            from benchmarks.core.review import review_deliverable

            content = extract_deliverable_content(run_dir, case.verify, record.result_text)
            record.review = review_deliverable(content, case.label, run_dir)
        entry = record.to_meta()
        entry["attempt"] = attempt
        records.append(entry)
        if record.delivered:
            completed += 1
            if record.duration_ms is not None:
                samples_ms.append(float(record.duration_ms))
        elif not retried:
            # Retry-once policy: one extra attempt for the whole case.
            retried = True
            print(f"[templates] {case.id}: run failed ({record.fail_reason}); retrying once")
        else:
            print(f"[templates] {case.id}: run failed again ({record.fail_reason}); moving on")
            break

    delivered = [r for r in records if r.get("delivered")]
    reviews = [r["review"] for r in delivered if r.get("review")]
    meta: dict[str, Any] = {
        "model": model,
        "tier": case.tier,
        "verify": case.verify,
        "verify_personas": list(case.verify_personas),
        "params": case.params,
        "runs_requested": runs,
        "runs_delivered": len(delivered),
        "review_passed": sum(1 for r in reviews if r.get("pass")),
        "review_total": len(reviews),
        "medians": {
            "duration_ms": _median([r.get("duration_ms") for r in delivered]),
            "total_cost_usd": _median([r.get("total_cost_usd") for r in delivered]),
            "input_tokens": _median([r.get("input_tokens") for r in delivered]),
            "output_tokens": _median([r.get("output_tokens") for r in delivered]),
            "cache_read_input_tokens": _median(
                [r.get("cache_read_input_tokens") for r in delivered]
            ),
            "num_turns": _median([r.get("num_turns") for r in delivered]),
        },
        "runs": records,
    }
    if not delivered:
        reasons = sorted({str(r.get("fail_reason")) for r in records})
        return CaseResult.error_case(
            case.case_id, f"no delivered runs (reasons: {reasons})", meta=meta
        )
    return CaseResult(
        id=case.case_id,
        tool="claude-headless",
        samples_ms=samples_ms,
        budget_ms=float(case.timeout_s * 1000),
        meta=meta,
    ).finalize()


def run(ctx: BenchContext) -> list[CaseResult]:
    cases = load_template_cases()
    model = ctx.extra.get("model") or DEFAULT_MODEL
    skip_review = os.environ.get("CEREBRO_BENCH_SKIP_REVIEW") == "1"

    results: list[CaseResult] = []
    for case in cases:
        if not ctx.should_run(case.case_id):
            continue
        if not ctx.real_clickhouse:
            results.append(
                CaseResult.skipped_case(
                    case.case_id,
                    "templates suite requires CEREBRO_EVAL_CLICKHOUSE=1 (real data)",
                )
            )
            continue
        results.append(_run_case(ctx, case, model, skip_review))
    return results
