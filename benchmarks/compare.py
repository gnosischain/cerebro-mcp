"""Compare two benchmark result files and flag regressions.

    uv run python -m benchmarks.compare BASELINE.json CANDIDATE.json
        [--pct 25] [--abs-ms 20] [--metric p50,p95] [--json]

``BASELINE`` may be the literal ``latest`` to resolve the most recent result
file for the candidate's suite+mode from ``benchmarks/results/``.

Regression = for any compared metric m: ``cand.m > base.m * (1 + pct/100)``
AND ``cand.m > base.m + abs_ms`` — the AND of relative+absolute thresholds
prevents micro-case noise (2ms -> 3ms) and slow-case drowning alike.

Exit codes: 0 = no regressions, 1 = regressions found, 2 = incomparable inputs.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from benchmarks.core.results import find_latest, load_run

FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"


@dataclass
class Finding:
    level: str        # FAIL | WARN | INFO
    case_id: str
    message: str


def _by_id(run: dict) -> dict[str, dict]:
    return {c["id"]: c for c in run.get("cases", [])}


def _latency_findings(base: dict, cand: dict, *, pct: float, abs_ms: float,
                      metrics: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for metric in metrics:
        b = (base.get("stats") or {}).get(metric)
        c = (cand.get("stats") or {}).get(metric)
        if b is None or c is None or b <= 0:
            continue
        if c > b * (1 + pct / 100.0) and c > b + abs_ms:
            findings.append(Finding(FAIL, cand["id"],
                f"{metric} regressed {b:.1f}ms -> {c:.1f}ms (+{(c / b - 1) * 100:.0f}%)"))
        elif b > c * (1 + pct / 100.0) and b > c + abs_ms:
            findings.append(Finding(INFO, cand["id"],
                f"{metric} improved {b:.1f}ms -> {c:.1f}ms"))
    return findings


def _status_findings(base: dict, cand: dict) -> list[Finding]:
    b, c = base["status"], cand["status"]
    if b == c:
        return []
    if c == "error":
        return [Finding(FAIL, cand["id"], f"status {b} -> error: {(cand.get('error') or '')[:200]}")]
    if b == "ok" and c == "skipped":
        return [Finding(WARN, cand["id"], f"status ok -> skipped ({cand.get('skip_reason')})")]
    if b == "ok" and c == "over_budget":
        return [Finding(WARN, cand["id"], "status ok -> over_budget")]
    return [Finding(INFO, cand["id"], f"status {b} -> {c}")]


# ---- suite-specific rules ---------------------------------------------------

def _workflows_findings(base: dict, cand: dict) -> list[Finding]:
    findings: list[Finding] = []
    bm, cm = base.get("meta", {}), cand.get("meta", {})
    if "tool_calls" in bm and "tool_calls" in cm and cm["tool_calls"] > bm["tool_calls"]:
        findings.append(Finding(FAIL, cand["id"],
            f"tool_calls increased {bm['tool_calls']} -> {cm['tool_calls']} "
            "(a gate change added a mandatory call; update the baseline in the same changeset if intentional)"))
    if cm.get("gate_blocks_unexpected", 0) > 0:
        findings.append(Finding(FAIL, cand["id"],
            f"{cm['gate_blocks_unexpected']} unexpected gate block(s)"))
    b_chars, c_chars = bm.get("total_response_chars"), cm.get("total_response_chars")
    if b_chars and c_chars and b_chars > 0:
        growth = (c_chars / b_chars - 1) * 100
        if growth > 25:
            findings.append(Finding(FAIL, cand["id"],
                f"total_response_chars grew {b_chars} -> {c_chars} (+{growth:.0f}% > 25%)"))
        elif growth > 10:
            findings.append(Finding(WARN, cand["id"],
                f"total_response_chars grew {b_chars} -> {c_chars} (+{growth:.0f}%)"))
    return findings


def _search_findings(base: dict, cand: dict) -> list[Finding]:
    findings: list[Finding] = []
    bm, cm = base.get("meta", {}), cand.get("meta", {})
    if bm.get("hit5") is True and cm.get("hit5") is False:
        findings.append(Finding(FAIL, cand["id"],
            f"lost hit@5 (rank {bm.get('rank')} -> {cm.get('rank')})"))
    b_rank, c_rank = bm.get("rank"), cm.get("rank")
    if isinstance(b_rank, int) and isinstance(c_rank, int) and c_rank - b_rank > 2:
        findings.append(Finding(WARN, cand["id"], f"rank slipped {b_rank} -> {c_rank}"))
    b_mrr, c_mrr = bm.get("mrr"), cm.get("mrr")
    if isinstance(b_mrr, (int, float)) and isinstance(c_mrr, (int, float)) and b_mrr - c_mrr > 0.02:
        findings.append(Finding(WARN, cand["id"], f"MRR dropped {b_mrr:.3f} -> {c_mrr:.3f}"))
    return findings


def _semantic_findings(base: dict, cand: dict) -> list[Finding]:
    findings: list[Finding] = []
    bm, cm = base.get("meta", {}), cand.get("meta", {})
    if cm.get("kind") != "coverage":
        return findings
    b_val, c_val = bm.get("value"), cm.get("value")
    stat = cm.get("stat", cand["id"])
    if not isinstance(b_val, (int, float)) or not isinstance(c_val, (int, float)):
        return findings
    if stat == "orphan_metrics":
        if c_val > 0 and b_val == 0:
            findings.append(Finding(FAIL, cand["id"], f"new orphan metrics: {c_val}"))
        return findings
    if stat == "metrics_approved_count" and b_val > 0 and c_val < b_val * 0.95:
        findings.append(Finding(FAIL, cand["id"],
            f"approved metric count dropped {b_val} -> {c_val} (>5% — possible bad registry deploy)"))
        return findings
    if c_val < b_val and cm.get("direction") == "higher_is_better":
        findings.append(Finding(WARN, cand["id"], f"coverage {stat} decreased {b_val} -> {c_val}"))
    return findings


def _load_findings(base: dict, cand: dict) -> list[Finding]:
    findings: list[Finding] = []
    bm, cm = base.get("meta", {}), cand.get("meta", {})
    b_err, c_err = bm.get("error_rate"), cm.get("error_rate")
    if isinstance(b_err, (int, float)) and isinstance(c_err, (int, float)) and c_err - b_err > 0.02:
        findings.append(Finding(FAIL, cand["id"],
            f"error rate rose {b_err:.1%} -> {c_err:.1%} (+{(c_err - b_err) * 100:.1f}pp)"))
    b_tp, c_tp = bm.get("throughput_cps"), cm.get("throughput_cps")
    if isinstance(b_tp, (int, float)) and isinstance(c_tp, (int, float)) and b_tp > 0 and c_tp < b_tp * 0.8:
        findings.append(Finding(FAIL, cand["id"],
            f"throughput dropped {b_tp:.1f} -> {c_tp:.1f} calls/s (>20%)"))
    return findings


_SUITE_RULES = {
    "workflows": _workflows_findings,
    "search": _search_findings,
    "semantic": _semantic_findings,
    "load": _load_findings,
}

# Load-suite latency thresholds are looser (network noise).
_SUITE_ABS_MS = {"load": 50.0}


def compare_runs(base: dict, cand: dict, *, pct: float, abs_ms: float,
                 metrics: list[str]) -> tuple[list[Finding], list[str]]:
    """Returns (findings, incomparable_reasons)."""
    reasons: list[str] = []
    if base["suite"] != cand["suite"]:
        reasons.append(f"suite mismatch: {base['suite']} vs {cand['suite']}")
    if base["mode"] != cand["mode"]:
        reasons.append(f"mode mismatch: {base['mode']} vs {cand['mode']}")
    if base["suite"] == "semantic":
        b_fx = base.get("environment", {}).get("fixture_sha")
        c_fx = cand.get("environment", {}).get("fixture_sha")
        if b_fx and c_fx and b_fx != c_fx:
            reasons.append(f"semantic fixture hash mismatch: {b_fx} vs {c_fx} "
                           "(latency numbers are not comparable across fixtures)")
    if reasons:
        return [], reasons

    findings: list[Finding] = []
    b_fp = base.get("environment", {}).get("config_fingerprint", {})
    c_fp = cand.get("environment", {}).get("config_fingerprint", {})
    for key in sorted(set(b_fp) | set(c_fp)):
        if b_fp.get(key) != c_fp.get(key):
            findings.append(Finding(WARN, "-",
                f"config drift: {key} {b_fp.get(key)!r} -> {c_fp.get(key)!r}"))
    b_manifest = base.get("environment", {}).get("manifest_hash")
    c_manifest = cand.get("environment", {}).get("manifest_hash")
    if b_manifest and c_manifest and b_manifest != c_manifest:
        findings.append(Finding(WARN, "-",
            f"manifest hash drift: {b_manifest} -> {c_manifest} (search/lineage numbers may shift)"))

    base_cases, cand_cases = _by_id(base), _by_id(cand)
    suite_rule = _SUITE_RULES.get(cand["suite"])
    abs_ms = _SUITE_ABS_MS.get(cand["suite"], abs_ms)

    for case_id in sorted(set(base_cases) | set(cand_cases)):
        b, c = base_cases.get(case_id), cand_cases.get(case_id)
        if b is None:
            findings.append(Finding(INFO, case_id, "new case"))
            continue
        if c is None:
            findings.append(Finding(WARN, case_id, "case removed"))
            continue
        findings.extend(_status_findings(b, c))
        if b["status"] in ("ok", "over_budget") and c["status"] in ("ok", "over_budget"):
            findings.extend(_latency_findings(b, c, pct=pct, abs_ms=abs_ms, metrics=metrics))
            if suite_rule:
                findings.extend(suite_rule(b, c))
    return findings, []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="benchmarks.compare", description=__doc__)
    p.add_argument("baseline", help="baseline result file, or 'latest'")
    p.add_argument("candidate", help="candidate result file")
    p.add_argument("--pct", type=float, default=25.0)
    p.add_argument("--abs-ms", type=float, default=20.0)
    p.add_argument("--metric", default="p50,p95")
    p.add_argument("--json", action="store_true", dest="as_json")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    cand = load_run(Path(args.candidate))
    if args.baseline == "latest":
        cand_path = Path(args.candidate).resolve()
        base_path = None
        for candidate_path in sorted(
            (Path(p2) for p2 in cand_path.parent.glob(f"*_{cand['suite']}_{cand['mode']}.json")),
            reverse=True,
        ):
            if candidate_path.resolve() != cand_path:
                base_path = candidate_path
                break
        if base_path is None:
            base_path = find_latest(cand["suite"], cand["mode"])
        if base_path is None:
            print("no baseline found for --baseline latest")
            return 2
    else:
        base_path = Path(args.baseline)
    base = load_run(base_path)

    metrics = [m.strip() for m in args.metric.split(",") if m.strip()]
    findings, reasons = compare_runs(base, cand, pct=args.pct, abs_ms=args.abs_ms, metrics=metrics)

    if reasons:
        for r in reasons:
            print(f"INCOMPARABLE: {r}")
        return 2

    if args.as_json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        print(f"baseline:  {base_path}  ({base['run_id']})")
        print(f"candidate: {args.candidate}  ({cand['run_id']})")
        if not findings:
            print("no differences beyond thresholds")
        for f in sorted(findings, key=lambda f: (f.level != FAIL, f.level != WARN, f.case_id)):
            print(f"{f.level:5} {f.case_id:55} {f.message}")

    n_fail = sum(1 for f in findings if f.level == FAIL)
    n_warn = sum(1 for f in findings if f.level == WARN)
    print(f"\n{n_fail} regression(s), {n_warn} warning(s)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
