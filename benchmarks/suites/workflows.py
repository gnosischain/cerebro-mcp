"""Suite 3: agent task-efficiency workflows (and ``--replay`` trace scoring).

Workflow mode drives the seven pinned multi-step workflows from
``benchmarks/cases/workflow_cases.py`` against the deterministic in-process
stack (fixture manifest + fixture semantic snapshot + ``BenchClickHouse``),
through the PRODUCTION tool path (``mcp._tool_manager.call_tool``). Each case
asserts the gate behavior the workflow is designed around (blocked vs clean,
message anchors, routing) and records task-efficiency metrics: executed vs
optimal tool calls, response volume, expected/unexpected gate blocks, and
per-step timings (``samples_ms`` is the step-duration distribution, not
repeated iterations — there is no budget).

Replay mode (``--replay``) scores recorded production session traces from
``.cerebro/logs/session_*.json`` instead: tier classification from the trace
summary's action counts, plus blocked-step / report-retry / wall-time metrics.
Scoring is informational — replay cases never fail.

All ``cerebro_mcp`` imports are lazy (env-first discipline).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from benchmarks.cases.workflow_cases import WORKFLOW_CASES, WorkflowCase
from benchmarks.core.results import CaseResult

SUPPORTED_MODES = frozenset({"inprocess"})

# Backticked tool mentions, including call forms like
# `preflight_analytics_request(query, mode="report")`.
_BACKTICKED_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)(?:\([^`]*\))?`")

# Actions that classify a replayed session's tier (report beats chart).
_REPLAY_REPORT_ACTIONS = frozenset({
    "generate_report",
    "generate_research_report",
    "generate_case_study_report",
    "storyteller_generate_story_report",
})
_REPLAY_CHART_ACTIONS = frozenset({
    "generate_chart",
    "generate_charts",
    "quick_chart",
    "quick_metric_chart",
    "generate_metric_charts",
})
# tier -> optimal tool calls (mirrors the pinned workflow shapes: W3=11 report,
# W2=5 chart, W1=2 answer).
_REPLAY_OPTIMAL = {"report": 11, "chart": 5, "answer": 2}


# ---------------------------------------------------------------------------
# Result flattening / assertion helpers
# ---------------------------------------------------------------------------


def _flatten(result: Any) -> str:
    """Tool result -> text, matching how a client would read it."""
    from mcp.types import CallToolResult
    from pydantic import BaseModel

    if isinstance(result, CallToolResult):
        return "\n".join(
            block.text
            for block in result.content
            if getattr(block, "text", None)
        )
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    if isinstance(result, dict):
        return json.dumps(result, indent=1, default=str)
    return str(result)


def _extract_route(result: Any) -> str | None:
    """`route` field from a find (dict) or preflight (pydantic) result."""
    if isinstance(result, dict):
        route = result.get("route")
    else:
        route = getattr(result, "route", None)
    return route if isinstance(route, str) else None


def _block_markers() -> tuple[str, ...]:
    """The gate-block anchors: the trace-summary patterns plus the report
    gate's exception prefix (which reasoning.py does not need to pattern-match
    because blocked reports raise instead of returning)."""
    from cerebro_mcp.tools.governance.reasoning import _WORKFLOW_BLOCK_PATTERNS

    return tuple(_WORKFLOW_BLOCK_PATTERNS) + ("Report quality gate failed",)


def _snippet(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


# ---------------------------------------------------------------------------
# Workflow mode
# ---------------------------------------------------------------------------


async def _run_case(mcp, case: WorkflowCase, tool_names: set[str]) -> CaseResult:
    from cerebro_mcp.tools.governance.session_state import state

    markers = _block_markers()
    samples_ms: list[float] = []
    steps_meta: list[dict[str, Any]] = []
    blocks_meta: list[dict[str, Any]] = []
    total_chars = 0
    gate_blocks_expected = 0
    gate_blocks_unexpected = 0
    first_block_idx: int | None = None
    last_blocked = False
    error: str | None = None

    for i, step in enumerate(case.steps):
        started = time.perf_counter()
        try:
            raw = await mcp._tool_manager.call_tool(step.tool, dict(step.args))
        except Exception as exc:  # tool infrastructure failure, not a gate
            error = f"step {i} ({step.tool}) raised: {exc}"
            break
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        text = _flatten(raw)
        blocked = any(marker in text for marker in markers)

        samples_ms.append(elapsed_ms)
        total_chars += len(text)
        last_blocked = blocked
        steps_meta.append({
            "step": i,
            "tool": step.tool,
            "ms": round(elapsed_ms, 3),
            "chars": len(text),
            "blocked": blocked,
        })
        if blocked:
            if first_block_idx is None:
                first_block_idx = i
            mentioned = sorted(set(_BACKTICKED_RE.findall(text)) & tool_names)
            blocks_meta.append({
                "step": i,
                "tool": step.tool,
                "block_message_chars": len(text),
                "actionable": bool(mentioned),
                "tools_mentioned": mentioned,
            })
            if step.expect_block:
                gate_blocks_expected += 1
            else:
                gate_blocks_unexpected += 1

        if blocked != step.expect_block:
            verb = "was blocked" if blocked else "was NOT blocked"
            error = (
                f"step {i} ({step.tool}) {verb} (expected "
                f"{'block' if step.expect_block else 'clean'}): {_snippet(text)}"
            )
            break
        missing = [s for s in step.expect_substrings if s not in text]
        if missing:
            error = (
                f"step {i} ({step.tool}) missing expected substring(s) "
                f"{missing!r}: {_snippet(text)}"
            )
            break
        present = [s for s in step.forbid_substrings if s in text]
        if present:
            error = (
                f"step {i} ({step.tool}) contains forbidden substring(s) "
                f"{present!r}: {_snippet(text)}"
            )
            break
        if step.route_expect is not None:
            route = _extract_route(raw)
            if route != step.route_expect:
                error = (
                    f"step {i} ({step.tool}) routed {route!r}, expected "
                    f"{step.route_expect!r}"
                )
                break

    # A successful generate_report must fire the full session reset.
    if (
        error is None
        and case.steps
        and case.steps[-1].tool == "generate_report"
        and not case.steps[-1].expect_block
        and state.search_models_count != 0
    ):
        error = (
            "session state not reset after successful generate_report "
            f"(search_models_count={state.search_models_count})"
        )

    executed = len(steps_meta)
    meta: dict[str, Any] = {
        "tier": case.tier,
        "tool_calls": executed,
        "optimal_calls": case.optimal_calls,
        "overhead_ratio": (
            round(executed / case.optimal_calls, 3) if case.optimal_calls else None
        ),
        "total_response_chars": total_chars,
        "est_tokens": total_chars // 4,
        "gate_blocks_expected": gate_blocks_expected,
        "gate_blocks_unexpected": gate_blocks_unexpected,
        "sse_safe": case.sse_safe,
        "steps": steps_meta,
    }
    if case.notes:
        meta["notes"] = case.notes
    if blocks_meta:
        meta["blocks"] = blocks_meta
    # Recovery cost: calls between the first gate block and a clean finish
    # (only meaningful when the workflow actually recovered, e.g. W6).
    if first_block_idx is not None and not last_blocked and error is None:
        meta["recovery_calls"] = executed - first_block_idx - 1

    if error:
        return CaseResult.error_case(case.id, error, tool="workflow", meta=meta)
    return CaseResult(id=case.id, tool="workflow", samples_ms=samples_ms, meta=meta)


def _run_workflows(ctx) -> list[CaseResult]:
    from benchmarks.core.fakes import (
        bench_clickhouse_from_corpus,
        build_bench_server,
        reset_server_state,
    )
    from benchmarks.core.semantic_env import (
        deterministic_semantic_runtime,
        fixture_fingerprint,
        snapshot_from_fixture,
    )
    from tests.eval.corpus_fixtures import install_fixture_manifest, load_search_corpus

    corpus = load_search_corpus()
    install_fixture_manifest(corpus)
    snapshot = snapshot_from_fixture()
    ctx.extra.setdefault("environment", {})["fixture"] = fixture_fingerprint(snapshot)

    results: list[CaseResult] = []
    with deterministic_semantic_runtime(snapshot):
        ch = bench_clickhouse_from_corpus(corpus)
        # `find` registration requires the patched SEMANTIC_ENABLED, so the
        # server must be built INSIDE the deterministic runtime.
        mcp = build_bench_server(ch)
        tool_names = {tool.name for tool in mcp._tool_manager.list_tools()}

        for case in WORKFLOW_CASES:
            if not ctx.should_run(case.id):
                continue
            if case.needs_clickhouse and not ctx.real_clickhouse:
                results.append(CaseResult.skipped_case(
                    case.id,
                    "needs real ClickHouse (set CEREBRO_EVAL_CLICKHOUSE=1)",
                    tool="workflow",
                ))
                continue
            reset_server_state()
            results.append(asyncio.run(_run_case(mcp, case, tool_names)).finalize())
        reset_server_state()
    return results


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------


def _classify_replay_tier(actions: dict[str, Any]) -> tuple[str, int]:
    keys = set(actions)
    if keys & _REPLAY_REPORT_ACTIONS:
        return "report", _REPLAY_OPTIMAL["report"]
    if keys & _REPLAY_CHART_ACTIONS:
        return "chart", _REPLAY_OPTIMAL["chart"]
    return "answer", _REPLAY_OPTIMAL["answer"]


def _replay_case(path: Path) -> CaseResult:
    session_id = path.stem.removeprefix("session_")
    case_id = f"workflows/replay/{session_id}"
    try:
        trace = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return CaseResult.skipped_case(case_id, f"unreadable trace: {exc}", tool="replay")
    if not isinstance(trace, dict):
        return CaseResult.skipped_case(case_id, "trace is not a JSON object", tool="replay")

    summary = trace.get("summary")
    if not isinstance(summary, dict) or "total_steps" not in summary:
        return CaseResult.skipped_case(case_id, "trace has no usable summary", tool="replay")

    actions = summary.get("actions")
    if not isinstance(actions, dict):
        actions = {}
    tier, optimal = _classify_replay_tier(actions)

    action_counts = [v for v in actions.values() if isinstance(v, (int, float))]
    tool_calls = int(sum(action_counts)) if action_counts else int(summary.get("total_steps") or 0)

    # `reports_emitted` is an int counter on SessionTrace; tolerate a list in
    # case the persistence shape ever changes.
    emitted = trace.get("reports_emitted", 0)
    if isinstance(emitted, list):
        n_reports = len(emitted)
    elif isinstance(emitted, (int, float)):
        n_reports = int(emitted)
    else:
        n_reports = 0

    meta = {
        "tier": tier,
        "total_steps": int(summary.get("total_steps") or 0),
        "tool_calls": tool_calls,
        "optimal_calls": optimal,
        "overhead_ratio": round(tool_calls / optimal, 3) if optimal else None,
        "workflow_blocked_steps": int(summary.get("workflow_blocked_steps") or 0),
        "report_retries": max(0, n_reports - 1),
        "wall_ms": int(summary.get("wall_duration_ms") or 0),
    }
    return CaseResult(id=case_id, tool="replay", meta=meta)


def _run_replay(ctx) -> list[CaseResult]:
    log_dir = Path(ctx.extra.get("replay_log_dir") or ".cerebro/logs").expanduser()
    limit = ctx.replay_last or 5

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    files = sorted(log_dir.glob("session_*.json"), key=_mtime, reverse=True)[:limit]
    if not files:
        return [CaseResult.skipped_case(
            "workflows/replay",
            f"no session_*.json traces under {log_dir}",
            tool="replay",
        )]

    results = []
    for path in files:
        case = _replay_case(path)
        if ctx.should_run(case.id):
            results.append(case.finalize())
    return results


def run(ctx) -> list[CaseResult]:
    if ctx.replay:
        return _run_replay(ctx)
    return _run_workflows(ctx)
