"""BenchContext + suite registry for the benchmark harness.

A suite is a module exposing::

    SUPPORTED_MODES: frozenset[str]          # subset of {"inprocess", "sse"}
    def run(ctx: BenchContext) -> list[CaseResult]: ...

Suites import ``cerebro_mcp`` lazily inside ``run()`` (env-first discipline).
"""

from __future__ import annotations

import fnmatch
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# name -> module path (imported lazily so `--suite latency` never imports load's deps)
SUITES: dict[str, str] = {
    "latency": "benchmarks.suites.latency",
    "load": "benchmarks.suites.load",
    "workflows": "benchmarks.suites.workflows",
    "search": "benchmarks.suites.search_quality",
    "semantic": "benchmarks.suites.semantic",
    "templates": "benchmarks.suites.templates_headless",
}

DEFAULT_MODE: dict[str, str] = {
    "latency": "inprocess",
    "load": "sse",
    "workflows": "inprocess",
    "search": "inprocess",
    "semantic": "inprocess",
    "templates": "headless",
}


@dataclass
class BenchContext:
    suite: str
    mode: str                         # "inprocess" | "sse"
    real_clickhouse: bool             # CEREBRO_EVAL_CLICKHOUSE=1
    live_registry: bool               # CEREBRO_EVAL_LIVE_REGISTRY=1 (or implied by real CH)
    scratch_dir: Path
    results_dir: Path
    iters: int | None = None          # CLI override; cases have their own defaults
    warmup: int | None = None
    concurrency: list[int] = field(default_factory=lambda: [1, 4, 8, 16])
    duration_s: int = 20
    max_heavy_concurrency: int = 8
    port: int = 8091
    only: str | None = None           # case-id glob filter
    update_golden: bool = False
    replay: bool = False
    replay_last: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def should_run(self, case_id: str) -> bool:
        return self.only is None or fnmatch.fnmatch(case_id, self.only)

    @property
    def mode_label(self) -> str:
        """Mode string recorded in result files: distinguishes fake vs real data."""
        if self.mode == "sse":
            return "sse-real"
        if self.mode == "headless":
            return "headless-real"
        return "inprocess-real" if self.real_clickhouse else "inprocess-fake"


def get_suite(name: str):
    if name not in SUITES:
        raise SystemExit(f"unknown suite {name!r}; choose from {sorted(SUITES)}")
    return importlib.import_module(SUITES[name])
