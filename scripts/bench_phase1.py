#!/usr/bin/env python3
"""Phase 1 performance benchmark.

Measures the cost of the new BM25 + networkx machinery against the real dbt
manifest. Prints a structured report you can paste back for review.

Usage:
    python scripts/bench_phase1.py
    python scripts/bench_phase1.py --queries 50      # more samples per query
    python scripts/bench_phase1.py --json out.json   # also dump raw numbers

Sections:
  1. Manifest load + index build time
  2. search_models latency (NEW hybrid path) vs OLD token-overlap path
  3. Lineage query latency
  4. Column-scoping latency on the widest model
  5. Memory footprint of the new indexes

The OLD token-overlap path is reimplemented inline so this script is a true
A/B test against Phase 1 — no `git stash` needed.
"""

from __future__ import annotations

import argparse
import gc
import json
import pickle
import re
import statistics
import sys
import time
from pathlib import Path

# Make `cerebro_mcp` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp.manifest_loader import ManifestLoader, manifest  # noqa: E402
from cerebro_mcp.schema_context import build_scoped_schema_block  # noqa: E402


# ---------------------------------------------------------------------------
# Edit this list. Pick queries that exercise your real workload — the more
# realistic, the better the latency numbers reflect production.
# ---------------------------------------------------------------------------
PROBE_QUERIES = [
    "trades by token",
    "validator withdrawals",
    "bridge tvl",
    "gpay wallet activity",
    "dex pool fees",
    "staking rewards",
    "block production",
    "gas usage daily",
    "token transfers",
    "api summary",
    "consensus validators active",
    "execution transactions",
]


def _old_search_models(loader: ManifestLoader, query: str, limit: int = 20) -> list[str]:
    """The pre-Phase-1 token-overlap-only ranker, inlined for A/B comparison."""
    if not query:
        return sorted(loader._models.keys())[:limit]
    tokens = re.split(r"[\s_]+", query.lower())
    tokens = [t for t in tokens if len(t) >= 3] or [query.lower()]
    scored: list[tuple[int, str]] = []
    for name, blob in loader._search_index.items():
        hits = sum(1 for t in tokens if t in blob)
        if hits:
            scored.append((hits, name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [n for _, n in scored[:limit]]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _stats(name: str, values_ms: list[float]) -> dict:
    return {
        "label": name,
        "n": len(values_ms),
        "median_ms": round(statistics.median(values_ms), 3) if values_ms else 0.0,
        "p95_ms": round(_percentile(values_ms, 95), 3),
        "max_ms": round(max(values_ms), 3) if values_ms else 0.0,
        "mean_ms": round(statistics.fmean(values_ms), 3) if values_ms else 0.0,
    }


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("  (no rows)")
        return
    cols = ["label", "n", "median_ms", "p95_ms", "max_ms", "mean_ms"]
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "  " + "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  " + "  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  " + "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


# ---------------------------------------------------------------------------
# Section 1: load + index build
# ---------------------------------------------------------------------------


def section_load() -> dict:
    print("\n=== 1. Manifest load + index build ===")
    # Load the GLOBAL singleton so sections 2–5 (which reference `manifest`)
    # see real data. Earlier versions of this script loaded a local loader
    # and left `manifest` empty — every downstream measurement was bogus.
    t0 = time.perf_counter()
    manifest.load()
    cold_ms = (time.perf_counter() - t0) * 1000

    if not manifest.is_loaded:
        print("  ERROR: manifest did not load. Check DBT_MANIFEST_URL / DBT_MANIFEST_PATH.")
        return {"loaded": False}

    # Re-build indexes only (excludes I/O). Use a throwaway loader so we're
    # not racing the live one used by the rest of the script.
    throwaway = ManifestLoader()
    data, _ = throwaway._fetch_manifest()  # type: ignore[attr-defined]
    samples = []
    for _ in range(5):
        t = time.perf_counter()
        throwaway._build_indexes_internal(data)
        samples.append((time.perf_counter() - t) * 1000)
    loader = manifest

    info = {
        "loaded": True,
        "model_count": loader.model_count,
        "source_count": len(loader._sources),
        "bm25_models_corpus": len(loader._bm25_models),
        "bm25_columns_corpus": len(loader._bm25_columns),
        "lineage_nodes": loader._lineage_graph.number_of_nodes(),
        "lineage_edges": loader._lineage_graph.number_of_edges(),
        "cold_load_ms": round(cold_ms, 1),
        "build_indexes_ms": _stats("build_indexes", samples),
    }
    print(f"  models={info['model_count']}   sources={info['source_count']}")
    print(f"  bm25 models corpus  : {info['bm25_models_corpus']} docs")
    print(f"  bm25 columns corpus : {info['bm25_columns_corpus']} docs")
    print(f"  lineage DAG         : {info['lineage_nodes']} nodes / {info['lineage_edges']} edges")
    print(f"  cold load           : {info['cold_load_ms']} ms (one-shot, includes JSON I/O)")
    print(f"  build indexes only  : median {info['build_indexes_ms']['median_ms']} ms "
          f"(p95 {info['build_indexes_ms']['p95_ms']}, n={info['build_indexes_ms']['n']})")
    return info


# ---------------------------------------------------------------------------
# Section 2: search_models A/B
# ---------------------------------------------------------------------------


def section_search(samples_per_query: int) -> dict:
    print("\n=== 2. search_models latency: NEW (hybrid) vs OLD (token-overlap) ===")
    new_runs_all: list[float] = []
    old_runs_all: list[float] = []
    per_query: list[dict] = []

    for q in PROBE_QUERIES:
        # warm-up
        manifest.search_models(query=q, limit=20)
        _old_search_models(manifest, q, 20)

        new_runs = []
        for _ in range(samples_per_query):
            t = time.perf_counter()
            manifest.search_models(query=q, limit=20)
            new_runs.append((time.perf_counter() - t) * 1000)

        old_runs = []
        for _ in range(samples_per_query):
            t = time.perf_counter()
            _old_search_models(manifest, q, 20)
            old_runs.append((time.perf_counter() - t) * 1000)

        new_med = statistics.median(new_runs)
        old_med = statistics.median(old_runs)
        per_query.append(
            {
                "query": q,
                "new_median_ms": round(new_med, 3),
                "old_median_ms": round(old_med, 3),
                "delta_ms": round(new_med - old_med, 3),
                "delta_pct": round(100 * (new_med - old_med) / max(old_med, 1e-6), 1),
            }
        )
        new_runs_all.extend(new_runs)
        old_runs_all.extend(old_runs)

    aggregate = [_stats("NEW (hybrid)", new_runs_all), _stats("OLD (token-overlap)", old_runs_all)]
    print()
    _print_table(aggregate)

    print("\n  per-query medians:")
    for r in per_query:
        sign = "+" if r["delta_ms"] >= 0 else ""
        print(f"    {r['query']:35s}  NEW {r['new_median_ms']:6.2f}  OLD {r['old_median_ms']:6.2f}  "
              f"Δ {sign}{r['delta_ms']:6.2f} ms ({sign}{r['delta_pct']:+.1f}%)")

    return {"aggregate": aggregate, "per_query": per_query}


# ---------------------------------------------------------------------------
# Section 3: lineage
# ---------------------------------------------------------------------------


def section_lineage() -> dict:
    print("\n=== 3. Lineage query latency ===")
    names = manifest.get_all_model_names()
    if not names:
        print("  (no models)")
        return {}
    sample_models = names[: min(100, len(names))]

    runs = []
    fanout = []
    for m in sample_models:
        t = time.perf_counter()
        up = manifest.upstream(m)
        down = manifest.downstream(m)
        runs.append((time.perf_counter() - t) * 1000)
        fanout.append((m, len(up), len(down)))

    fanout.sort(key=lambda x: x[1] + x[2], reverse=True)
    s = _stats("upstream+downstream", runs)
    _print_table([s])

    print("\n  top-5 by total fan-in/out (these are slowest if any):")
    for m, up, down in fanout[:5]:
        print(f"    {m:50s}  up={up:4d}  down={down:4d}")
    return {"latency": s, "top_fanout": fanout[:5]}


# ---------------------------------------------------------------------------
# Section 4: column scoping
# ---------------------------------------------------------------------------


def section_scoping() -> dict:
    print("\n=== 4. Column-scoping latency (widest model) ===")
    names = manifest.get_all_model_names()
    widest_name = None
    widest_cols: dict = {}
    for n in names:
        d = manifest.get_model_details(n) or {}
        c = d.get("columns") or {}
        if len(c) > len(widest_cols):
            widest_cols = c
            widest_name = n

    if not widest_name:
        print("  (no models with column docs)")
        return {}

    print(f"  widest model: {widest_name}  ({len(widest_cols)} columns)")

    probe_q = "balance value over time"
    runs = []
    for _ in range(100):
        t = time.perf_counter()
        scoped = build_scoped_schema_block(
            widest_name,
            widest_cols,
            probe_q,
            top_columns_for_model=manifest.top_columns_for_model,
        )
        runs.append((time.perf_counter() - t) * 1000)

    s = _stats("build_scoped_schema_block", runs)
    _print_table([s])
    print(f"  example output: kept {len(scoped.kept_columns)} of "
          f"{scoped.total_columns} cols, was_scoped={scoped.was_scoped}")
    return {
        "widest_model": widest_name,
        "widest_col_count": len(widest_cols),
        "latency": s,
        "kept": len(scoped.kept_columns),
        "was_scoped": scoped.was_scoped,
    }


# ---------------------------------------------------------------------------
# Section 5: memory
# ---------------------------------------------------------------------------


def section_memory() -> dict:
    print("\n=== 5. Memory footprint (pickle-size proxy) ===")
    gc.collect()
    parts = {
        "_bm25_models": manifest._bm25_models,
        "_bm25_columns": manifest._bm25_columns,
        "_lineage_graph": manifest._lineage_graph,
        "_models": manifest._models,
        "_sources": manifest._sources,
        "_search_index": manifest._search_index,
        "_parent_map": manifest._parent_map,
        "_child_map": manifest._child_map,
    }
    sizes = []
    for name, obj in parts.items():
        try:
            kb = len(pickle.dumps(obj)) / 1024
        except Exception as e:
            kb = -1.0
            print(f"  warn: failed to pickle {name}: {e}")
        sizes.append({"part": name, "kb": round(kb, 1)})
    cols = ["part", "kb"]
    widths = {c: max(len(c), max(len(str(r[c])) for r in sizes)) for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    print("  " + "  ".join("-" * widths[c] for c in cols))
    for r in sizes:
        print("  " + "  ".join(str(r[c]).ljust(widths[c]) for c in cols))
    return {"sizes_kb": sizes}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=30,
                    help="samples per probe query (default 30)")
    ap.add_argument("--json", type=Path, default=None,
                    help="optional path to dump raw report as JSON")
    args = ap.parse_args()

    print("Phase 1 benchmark — Cerebro-MCP\n" + "=" * 50)

    report: dict = {}
    report["load"] = section_load()
    if not report["load"].get("loaded"):
        return 2
    report["search"] = section_search(samples_per_query=args.queries)
    report["lineage"] = section_lineage()
    report["scoping"] = section_scoping()
    report["memory"] = section_memory()

    print("\n=== Summary ===")
    print(f"  models indexed     : {report['load']['model_count']}")
    print(f"  cold load          : {report['load']['cold_load_ms']} ms")
    print(f"  index build (med)  : {report['load']['build_indexes_ms']['median_ms']} ms")
    new_med = report["search"]["aggregate"][0]["median_ms"]
    old_med = report["search"]["aggregate"][1]["median_ms"]
    print(f"  search NEW median  : {new_med} ms")
    print(f"  search OLD median  : {old_med} ms")
    print(f"  search overhead    : +{round(new_med - old_med, 2)} ms "
          f"(+{round(100 * (new_med - old_med) / max(old_med, 1e-6), 1)}%)")
    if "latency" in report["lineage"]:
        print(f"  lineage  median    : {report['lineage']['latency']['median_ms']} ms")
    if "latency" in report["scoping"]:
        print(f"  scoping  median    : {report['scoping']['latency']['median_ms']} ms "
              f"(on {report['scoping']['widest_col_count']}-column model)")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str))
        print(f"\n  raw JSON -> {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
