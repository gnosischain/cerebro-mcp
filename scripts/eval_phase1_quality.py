#!/usr/bin/env python3
"""Phase 1 ranking-quality evaluation.

For each (query, expected_model) pair, we run BOTH the new hybrid ranker and
the old token-overlap-only ranker against the real dbt manifest, and report
hit@1 / hit@3 / hit@5 plus the rank delta. This is the single most important
test of Phase 1 — performance is meaningless if BM25/RRF doesn't actually
shift bad rankings.

Usage:
    python scripts/eval_phase1_quality.py
    python scripts/eval_phase1_quality.py --eval my_eval.json
    python scripts/eval_phase1_quality.py --verbose

Eval-set format (JSON):
    [
      {"query": "trades by token", "expected": "fct_execution_trades_by_token_daily"},
      {"query": "validator withdrawals", "expected": "api_consensus_validator_withdrawals_daily"}
    ]

You can also edit the DEFAULT_EVAL_SET below for a quick start.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp.loaders.manifest import ManifestLoader, manifest  # noqa: E402


# ---------------------------------------------------------------------------
# EDIT THIS — pairs of (query, expected_top_model_name).
#
# Pick queries that have historically misranked, OR that you'd like the
# analyst agent to nail on first attempt. `expected` is the *exact* dbt
# model short name (no `model.<project>.` prefix).
#
# If you don't know the right answer for a query, leave `expected: null`
# and the script will print the top-5 so you can fill it in.
# ---------------------------------------------------------------------------
DEFAULT_EVAL_SET: list[dict[str, Any]] = [
    # Expected answers chosen by inspection of the live cerebro-mcp manifest
    # (862 models). If any choice looks wrong for your workload, edit the
    # `expected` field — the evaluator only cares about exact name match.
    {"query": "trades by token",              "expected": "fct_execution_trades_by_token_daily"},
    {"query": "validator withdrawals",        "expected": "int_consensus_validators_withdrawals_daily"},
    {"query": "validators active",            "expected": "api_consensus_validators_active_daily"},
    # bridges have no TVL model in this project — pools do.
    {"query": "bridge tvl",                   "expected": "api_execution_pools_tvl_daily"},
    {"query": "gpay wallet activity",         "expected": "fct_execution_gpay_activity_daily"},
    {"query": "dex pool fees",                "expected": "api_execution_pools_fees_usd_daily"},
    {"query": "staking rewards",              "expected": "api_consensus_staked_daily"},
    {"query": "block production daily",       "expected": "api_consensus_blocks_daily"},
    {"query": "gas usage",                    "expected": "api_execution_blocks_gas_usage_pct_daily"},
    {"query": "token transfers daily",        "expected": "int_execution_tokens_transfers_daily"},
    {"query": "consensus validator balance",  "expected": "api_consensus_validators_balances_daily"},
    {"query": "execution transactions daily", "expected": "api_execution_transactions_cnt_daily"},
]


def _old_search_models(loader: ManifestLoader, query: str, limit: int = 10) -> list[str]:
    """Pre-Phase-1 token-overlap ranker, inlined."""
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


def _new_search_models(query: str, limit: int = 10) -> list[str]:
    return [m["name"] for m in manifest.search_models(query=query, limit=limit)]


def _rank_of(name: str | None, ranking: list[str]) -> int | None:
    if not name:
        return None
    try:
        return ranking.index(name) + 1
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", type=Path, default=None,
                    help="path to JSON eval set (overrides DEFAULT_EVAL_SET)")
    ap.add_argument("--limit", type=int, default=10,
                    help="how many results to consider for hit@k (default 10)")
    ap.add_argument("--verbose", action="store_true",
                    help="print top-5 for every query, not just misses")
    args = ap.parse_args()

    print("Phase 1 ranking-quality eval — Cerebro-MCP\n" + "=" * 50)

    if not manifest.is_loaded:
        print("Loading manifest...")
        manifest.load()
    if not manifest.is_loaded:
        print("ERROR: manifest did not load. Check DBT_MANIFEST_URL / DBT_MANIFEST_PATH.")
        return 2

    print(f"  models indexed: {manifest.model_count}\n")

    if args.eval:
        eval_set = json.loads(args.eval.read_text())
    else:
        eval_set = DEFAULT_EVAL_SET

    rows: list[dict] = []
    new_h1 = new_h3 = new_h5 = 0
    old_h1 = old_h3 = old_h5 = 0
    n_with_truth = 0

    print("=== Per-query ranks ===")
    print(f"  {'query':<40s}  {'expected':<45s}  {'NEW_rank':>9s}  {'OLD_rank':>9s}")
    print("  " + "-" * 110)

    for item in eval_set:
        q = item["query"]
        expected = item.get("expected")
        new_top = _new_search_models(q, args.limit)
        old_top = _old_search_models(manifest, q, args.limit)

        new_rank = _rank_of(expected, new_top)
        old_rank = _rank_of(expected, old_top)

        rows.append({
            "query": q,
            "expected": expected,
            "new_top": new_top,
            "old_top": old_top,
            "new_rank": new_rank,
            "old_rank": old_rank,
        })

        rank_str = lambda r: f"{r}" if r else "—"
        if expected:
            n_with_truth += 1
            if new_rank == 1: new_h1 += 1
            if new_rank and new_rank <= 3: new_h3 += 1
            if new_rank and new_rank <= 5: new_h5 += 1
            if old_rank == 1: old_h1 += 1
            if old_rank and old_rank <= 3: old_h3 += 1
            if old_rank and old_rank <= 5: old_h5 += 1

        exp_disp = (expected or "(unset)")[:45]
        print(f"  {q:<40s}  {exp_disp:<45s}  {rank_str(new_rank):>9s}  {rank_str(old_rank):>9s}")

    print()
    if n_with_truth:
        print("=== Aggregate (only queries with `expected` set) ===")
        print(f"  queries with truth   : {n_with_truth}")
        print(f"  hit@1   NEW / OLD    : {new_h1}/{n_with_truth}  vs  {old_h1}/{n_with_truth}")
        print(f"  hit@3   NEW / OLD    : {new_h3}/{n_with_truth}  vs  {old_h3}/{n_with_truth}")
        print(f"  hit@5   NEW / OLD    : {new_h5}/{n_with_truth}  vs  {old_h5}/{n_with_truth}")

        def pct(n): return f"{100*n/n_with_truth:.1f}%"
        gain1 = new_h1 - old_h1
        gain3 = new_h3 - old_h3
        gain5 = new_h5 - old_h5
        print(f"  Δ hit@1              : {'+' if gain1>=0 else ''}{gain1} ({pct(new_h1)} - {pct(old_h1)})")
        print(f"  Δ hit@3              : {'+' if gain3>=0 else ''}{gain3} ({pct(new_h3)} - {pct(old_h3)})")
        print(f"  Δ hit@5              : {'+' if gain5>=0 else ''}{gain5} ({pct(new_h5)} - {pct(old_h5)})")
    else:
        print("=== Aggregate skipped — no `expected` values set in eval set ===")
        print("    Edit DEFAULT_EVAL_SET in this script (or pass --eval my.json)")
        print("    to add `expected` model names. The top-5 below should help.")

    print("\n=== Top-5 per query (NEW vs OLD) ===")
    for r in rows:
        miss = r["expected"] and r["new_rank"] != 1
        if not (args.verbose or miss or not r["expected"]):
            continue
        print(f"\n  Q: {r['query']}")
        if r["expected"]:
            print(f"     expected: {r['expected']}")
        print(f"     NEW: {r['new_top'][:5]}")
        print(f"     OLD: {r['old_top'][:5]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
