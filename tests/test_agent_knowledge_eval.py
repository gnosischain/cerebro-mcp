"""Eval corpus for the agent-knowledge tools (docs/lessons in dbt-cerebro).

Gate: for each acceptance scenario, the correct lesson must rank in the top-3
of `AgentContextLoader.search`. This must pass against the real artifact
before publication is considered live.

Point AGENT_CONTEXT_PATH at a built artifact, e.g.:
    AGENT_CONTEXT_PATH=../dbt-cerebro/target/agent_context.public.json pytest tests/test_agent_knowledge_eval.py
Skips (with a clear reason) when no artifact is reachable — CI without the
sibling checkout still passes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.loaders.agent_context import AgentContextLoader

_DEFAULT_LOCAL = (
    Path(__file__).resolve().parents[2]
    / "dbt-cerebro"
    / "target"
    / "agent_context.public.json"
)

# (query, model_name or None, expected lesson id — or tuple of acceptable ids —
#  in top 3)
# Derived from the L1-L8 / OC-1 / OC-2 investigation plus the promoted
# session-memory lessons — the "same mistakes over and over" corpus.
SCENARIOS = [
    # L1/L3 — the same incident recorded from two layers: the raw backfill that
    # landed below the append watermarks. raw-logs-ingestion-holes' evidence
    # names the event and routes to the watermark lesson — either is a correct
    # diagnosis start.
    ("decoded events missing after raw backfill", None,
     ("decode-watermark-late-logs", "raw-logs-ingestion-holes")),
    # L1 pinned uniquely — the widening above must not leave the watermark
    # lesson unpinned (its other tuple appearance below is satisfied by
    # frontier-day).
    ("logs arrived late missing from decoded models", None, "decode-watermark-late-logs"),
    # Two lessons legitimately own this symptom (historical drop vs frozen
    # frontier day) — either surfacing is a correct diagnosis start.
    ("negative token balances real holder", None,
     ("decode-watermark-late-logs", "frontier-day-incomplete-inputs")),
    # L2
    ("token history starts too late whitelist", None, "late-start-mis-staging"),
    # L3
    ("blocks missing logs all contracts", None, "raw-logs-ingestion-holes"),
    # L4
    ("constant offset cumulative series duplicate seed", None, "duplicate-seed-drift"),
    # L7a — weekly revenue reprocess
    ("weekly revenue cohorts doubled", None, "append-over-populated-duplicates"),
    ("reprocess duplicated rows start_month append", None, "append-over-populated-duplicates"),
    # L7b
    ("delete insert wipe background mutation", None, "wide-delete-insert-wipe"),
    # L8 — pool carry-forward
    ("pool reserves missing days thin series", None, "global-frontier-carry-forward"),
    # OC-1 — new wrapper token
    ("new wrapper token shows zero usd", None, "unpriced-wrapper-token"),
    # OC-2
    ("incremental model empty zero rows green runs", None, "never-seeded-incremental"),
    # memory-promoted
    ("insert_overwrite wiped month partition staged refresh", None, "staged-insert-overwrite-wipe"),
    ("Code 241 memory total overcommit", None, "ch-overcommit-victim"),
    ("too many partitions insert error 252", None, "ch-partition-cap"),
    ("resume state clobbered refresh", None, "refresh-state-collision"),
    ("table only has last batch after refresh", None, "table-mat-batch-vars-truncation"),
    ("left join returns zero instead of null", None, "ch-left-join-nulls"),
    ("where clause alias shadows column", None, "ch-alias-shadows-where"),
    ("quarter end value stale not updating", None, "stale-snapshot-caveat"),
    ("retire model breaks semantic registry ci", None, "semantic-retirement-gate"),
    ("negative balances all on one day upstream looks fine", None, "frontier-day-incomplete-inputs"),
    ("balance usd null for whole day price missing", None, "frontier-day-incomplete-inputs"),
    ("supply doubled every token after refill", None, "refill-append-aggregator-inflation"),
    ("aggregate exactly 2x but no duplicate rows", None, "refill-append-aggregator-inflation"),
    ("same rows survive every reprocess spent to zero", None, "sparse-zero-row-stale-survival"),
    ("why does clickhouse have so many engines", None, "ch-merge-semantics-primer"),
    # model-scoped boost: the balances model's blast radius should surface
    # its hazards even for a vaguer query.
    # The balances model's blast radius now spans several balance-wrongness
    # classes — any of them is a correct first diagnosis to surface.
    ("balance wrong for one address", "int_execution_tokens_balances_native_daily",
     ("duplicate-seed-drift", "sparse-zero-row-stale-survival",
      "frontier-day-incomplete-inputs", "backfill-order-cumulative")),
]


@pytest.fixture(scope="module")
def loader():
    path = os.environ.get("AGENT_CONTEXT_PATH") or settings.AGENT_CONTEXT_PATH
    if not path and _DEFAULT_LOCAL.exists():
        path = str(_DEFAULT_LOCAL)
    if not path or not os.path.exists(path):
        pytest.skip(
            "no agent-context artifact available (set AGENT_CONTEXT_PATH to "
            "<dbt-cerebro>/target/agent_context.public.json)"
        )
    settings.AGENT_CONTEXT_PATH = path
    ldr = AgentContextLoader()
    ldr.load()
    assert ldr.is_loaded, ldr.last_error
    return ldr


def test_artifact_shape(loader):
    assert len(loader.lessons) >= 20
    entry = loader.get_model("int_execution_tokens_balances_native_daily")
    assert entry is not None
    hazards = {h["id"] for h in entry["contract"]["hazards"]}
    assert "duplicate-seed-drift" in hazards


@pytest.mark.parametrize("query,model_name,expected", SCENARIOS)
def test_correct_lesson_in_top3(loader, query, model_name, expected):
    results = loader.search(query, model_name=model_name, limit=3)
    ids = [r["id"] for r in results]
    acceptable = expected if isinstance(expected, tuple) else (expected,)
    assert any(e in ids for e in acceptable), (
        f"'{query}' -> {ids} (expected one of {acceptable} in top 3)"
    )
