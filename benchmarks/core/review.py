"""Adversarial review of template-run deliverables.

Every DELIVERED benchmark run is attacked by two independent reviewer agents,
each a cheap single-shot headless invocation with NO tools and NO MCP servers
(``--tools "" --strict-mcp-config`` with no config = pure text review). Each is
instructed to REFUTE the deliverable through a distinct lens:

1. data-discipline  — armed with prompts/agents/_shared_quality_rules.md
2. statistical      — the statistical_reviewer persona's methodology gate

A run "passes review" only when BOTH reviewers fail to find a critical defect.
A failed review does NOT void the time/token measurement — quality is published
as its own axis next to speed and cost.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from benchmarks.core.headless import REPO_ROOT, _claude_env, resolve_claude_bin

#: Reviews always run on the cheap model regardless of the measured leg.
REVIEW_MODEL = "claude-sonnet-5"
REVIEW_TIMEOUT_S = 240
REVIEW_BUDGET_USD = 1.0

_PROMPTS_DIR = REPO_ROOT / "src" / "cerebro_mcp" / "prompts" / "agents"

_VERDICT_SCHEMA = (
    'Respond with ONLY a JSON object, no prose, no code fences: '
    '{"verdict": "PASS" | "FAIL", "findings": [{"severity": "critical" | "minor", '
    '"claim": "<the claim or number at fault>", "why": "<one sentence>"}]}. '
    "CALIBRATION — CRITICAL means the deliverable is actively wrong or fabricated: "
    "a stated number that contradicts the deliverable's own data, a statistic or "
    "method claimed but not actually computed by any visible query, or a metric "
    "presented as something it is not. "
    "NOT critical (mark minor): descriptive language that could carry more "
    "statistical rigor (a hedged 'trending up' without a regression slope), "
    "missing extra context, exhaustiveness or style suggestions. A plainly "
    "reported value with a visible supporting query is fine even if you would "
    "have analyzed it more deeply. FAIL only on at least one CRITICAL defect."
)


def _lens_prompt(lens: str, deliverable: str, template_label: str) -> str:
    if lens == "data_discipline":
        rules = (_PROMPTS_DIR / "_shared_quality_rules.md").read_text()[:12_000]
        charge = (
            "You are an adversarial data-discipline reviewer. Your job is to REFUTE "
            "the analysis below. Hunt specifically for: stock-vs-flow violations "
            "(summing balance/TVL/supply over time), undisclosed residual-bucket "
            "exclusions, correlations on non-stationary time series, denominator "
            "games, revenue-vs-volume mislabeling, and SQL that does not support "
            "the claim attached to it.\n\nThe quality rules you enforce:\n" + rules
        )
    else:
        persona = (_PROMPTS_DIR / "statistical_reviewer.md").read_text()[:12_000]
        charge = (
            "You are an adversarial statistical reviewer. Your job is to REFUTE the "
            "analysis below. Hunt specifically for: causal language without causal "
            "design, claims not traceable to a number actually present, tiny or "
            "undisclosed sample sizes, fake precision, misleading chart choices.\n\n"
            "Your methodology standards:\n" + persona
        )
    return (
        f"{charge}\n\n---\nDeliverable under review (template: {template_label}):\n\n"
        f"{deliverable}\n\n---\n{_VERDICT_SCHEMA}"
    )


def _parse_verdict(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"\{.*\}", text, flags=re.S)
    if fence:
        text = fence.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"verdict": "UNPARSEABLE", "findings": [], "raw": raw[:2000]}
    verdict = str(data.get("verdict", "")).upper()
    findings = data.get("findings") if isinstance(data.get("findings"), list) else []
    return {"verdict": verdict if verdict in {"PASS", "FAIL"} else "UNPARSEABLE",
            "findings": findings}


def _run_reviewer(lens: str, deliverable: str, template_label: str, run_dir: Path) -> dict[str, Any]:
    prompt = _lens_prompt(lens, deliverable, template_label)
    cmd = [
        resolve_claude_bin(),
        "-p", prompt,
        "--output-format", "json",
        "--strict-mcp-config",           # no --mcp-config => zero MCP servers
        "--model", REVIEW_MODEL,
        "--tools", "",
        "--max-budget-usd", str(REVIEW_BUDGET_USD),
        "--no-session-persistence",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=run_dir, env=_claude_env(),
            capture_output=True, text=True, timeout=REVIEW_TIMEOUT_S,
        )
        payload = json.loads(proc.stdout)
        verdict = _parse_verdict(str(payload.get("result") or ""))
        verdict["lens"] = lens
        verdict["cost_usd"] = payload.get("total_cost_usd")
        return verdict
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return {"lens": lens, "verdict": "ERROR", "findings": [], "error": str(exc)[:300]}


def review_deliverable(deliverable: str, template_label: str, run_dir: Path) -> dict[str, Any]:
    """Run both adversarial lenses; write verdicts next to the run artifacts."""
    verdicts = [
        _run_reviewer("data_discipline", deliverable, template_label, run_dir),
        _run_reviewer("statistical", deliverable, template_label, run_dir),
    ]

    def _has_critical(v: dict[str, Any]) -> bool:
        return v.get("verdict") == "FAIL" and any(
            isinstance(f, dict) and f.get("severity") == "critical" for f in v.get("findings", [])
        ) or (v.get("verdict") == "FAIL" and not v.get("findings"))

    passed = all(not _has_critical(v) for v in verdicts)
    result = {"pass": passed, "verdicts": verdicts}
    (run_dir / "review.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result
