"""Distill templates-suite benchmark results into the UI's benchmarks JSON.

Reads every ``benchmarks/results/*_templates_headless-real.json``, keeps the
LATEST run per model (a measurement session runs one leg per model), and writes
the checked-in ``ui/src/mini-apps/report-studio/model/benchmarks.gen.json``
that the Template Gallery merges into its catalog:

    {schema_version, sources: {model: run_file}, templates:
        {template_id: {model: {n_runs, delivered, review_passed, review_total,
                               duration_ms: {median,min,max},
                               tokens: {in_fresh, out, cache_read},
                               cost_usd: {median,min,max},
                               num_turns_median, measured_at}}}}

Separate from catalog.gen.json so re-measurement never touches template text
and template edits never fake-refresh measurements.

Usage: python -m benchmarks.distill_templates   (or: make distill-templates)
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent / "results"
OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "ui" / "src" / "mini-apps" / "report-studio" / "model" / "benchmarks.gen.json"
)

SCHEMA_VERSION = 1


def _spread(values: list[float]) -> dict[str, float] | None:
    cleaned = sorted(v for v in values if isinstance(v, (int, float)))
    if not cleaned:
        return None
    return {
        "median": round(statistics.median(cleaned), 3),
        "min": round(cleaned[0], 3),
        "max": round(cleaned[-1], 3),
    }


def _median(values: list[float]) -> float | None:
    cleaned = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(cleaned), 3) if cleaned else None


def _latest_run_per_model() -> dict[str, Path]:
    latest: dict[str, Path] = {}
    for path in sorted(RESULTS_DIR.glob("*_templates_headless-real.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        models = {
            c.get("meta", {}).get("model")
            for c in data.get("cases", [])
            if c.get("meta", {}).get("model")
        }
        for model in models:
            latest[model] = path  # sorted() => later files win
    return latest


def distill() -> dict[str, Any]:
    sources = _latest_run_per_model()
    if not sources:
        raise SystemExit(
            "no templates results found — run the suite first "
            "(python -m benchmarks.run --suite templates)"
        )
    templates: dict[str, dict[str, Any]] = {}
    for model, path in sources.items():
        data = json.loads(path.read_text())
        measured_at = data.get("started_at")
        for case in data.get("cases", []):
            meta = case.get("meta", {})
            if meta.get("model") != model:
                continue
            template_id = case.get("id", "").removeprefix("templates/")
            if not template_id or case.get("status") == "skipped":
                continue
            delivered_runs = [r for r in meta.get("runs", []) if r.get("delivered")]
            entry = {
                "n_runs": meta.get("runs_requested"),
                "delivered": len(delivered_runs),
                "review_passed": meta.get("review_passed"),
                "review_total": meta.get("review_total"),
                "duration_ms": _spread([r.get("duration_ms") for r in delivered_runs]),
                "tokens": {
                    "in_fresh": _median([r.get("input_tokens") for r in delivered_runs]),
                    "out": _median([r.get("output_tokens") for r in delivered_runs]),
                    "cache_read": _median(
                        [r.get("cache_read_input_tokens") for r in delivered_runs]
                    ),
                },
                "cost_usd": _spread([r.get("total_cost_usd") for r in delivered_runs]),
                "num_turns_median": _median([r.get("num_turns") for r in delivered_runs]),
                "measured_at": measured_at,
            }
            templates.setdefault(template_id, {})[model] = entry
    return {
        "schema_version": SCHEMA_VERSION,
        "sources": {model: path.name for model, path in sources.items()},
        "templates": dict(sorted(templates.items())),
    }


def main() -> int:
    payload = distill()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    n_templates = len(payload["templates"])
    print(f"wrote {OUTPUT_PATH} ({n_templates} templates, models: {sorted(payload['sources'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
