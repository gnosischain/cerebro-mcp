"""Run/case result records and JSON persistence for the benchmark harness.

One benchmark run produces exactly one JSON file under ``benchmarks/results/``
named ``<UTCts>_<suite>_<mode>.json``. Raw latency samples are kept per case
(they are small) so ``compare`` can re-derive stats under a different outlier
policy without re-running.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.core.stats import sample_stats, valid_efficiency_score

SCHEMA_VERSION = 1

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

# Case statuses
OK = "ok"
ERROR = "error"
SKIPPED = "skipped"
OVER_BUDGET = "over_budget"


@dataclass
class CaseResult:
    """One benchmark case record. ``id`` is the stable join key for compare."""

    id: str
    tool: str | None = None
    status: str = OK
    skip_reason: str | None = None
    error: str | None = None
    samples_ms: list[float] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)
    budget_ms: float | None = None
    ves: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def finalize(self) -> "CaseResult":
        """Compute stats/VES from samples; flag over-budget (informational)."""
        if self.samples_ms:
            self.stats = {k: round(v, 3) for k, v in sample_stats(self.samples_ms).items()}
            self.samples_ms = [round(s, 3) for s in self.samples_ms]
            if self.budget_ms is not None:
                self.ves = round(valid_efficiency_score(self.budget_ms, self.samples_ms), 2)
                if self.status == OK and self.stats.get("p50", 0.0) > self.budget_ms:
                    self.status = OVER_BUDGET
        return self

    @staticmethod
    def skipped_case(case_id: str, reason: str, *, tool: str | None = None) -> "CaseResult":
        return CaseResult(id=case_id, tool=tool, status=SKIPPED, skip_reason=reason)

    @staticmethod
    def error_case(case_id: str, err: str, *, tool: str | None = None,
                   meta: dict[str, Any] | None = None) -> "CaseResult":
        return CaseResult(id=case_id, tool=tool, status=ERROR, error=err, meta=meta or {})


@dataclass
class RunResult:
    run_id: str
    suite: str
    mode: str
    started_at: str
    finished_at: str
    environment: dict[str, Any]
    params: dict[str, Any]
    cases: list[CaseResult]

    @property
    def summary(self) -> dict[str, int]:
        counts = {"cases": len(self.cases), OK: 0, SKIPPED: 0, ERROR: 0, OVER_BUDGET: 0}
        for c in self.cases:
            counts[c.status] = counts.get(c.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "suite": self.suite,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "environment": self.environment,
            "params": self.params,
            "cases": [asdict(c) for c in self.cases],
            "summary": self.summary,
        }


def write_run(run: RunResult, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = run.started_at.replace("+00:00", "Z").replace("-", "").replace(":", "")
    path = out_dir / f"{ts}_{run.suite}_{run.mode}.json"
    path.write_text(json.dumps(run.to_dict(), indent=2, default=str) + "\n")
    return path


def load_run(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version {data.get('schema_version')} != {SCHEMA_VERSION}"
        )
    return data


def find_latest(suite: str, mode: str | None = None, results_dir: Path | None = None) -> Path | None:
    """Most recent result file for a suite (used by ``--baseline latest``)."""
    results_dir = results_dir or RESULTS_DIR
    if not results_dir.exists():
        return None
    pattern = f"*_{suite}_{mode}.json" if mode else f"*_{suite}_*.json"
    matches = sorted(results_dir.glob(pattern))
    return matches[-1] if matches else None
