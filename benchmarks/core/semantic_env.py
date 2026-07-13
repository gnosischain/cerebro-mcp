"""Deterministic semantic runtime for the benchmark suites.

Reconstructs a real ``SemanticSnapshot`` from the recorded
``tests/fixtures/routing_registry.json.gz`` (rebuilding ``graph`` via
``build_semantic_graph`` and ``token_idf`` via ``build_token_idf`` — the exact
production build path), and applies the ``semantic_ready`` patch set from
``tests/test_semantic_find.py`` outside pytest via ``ExitStack``.

All ``cerebro_mcp`` imports are lazy (env-first discipline).
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest import mock

from tests.eval.corpus_fixtures import ROUTING_REGISTRY_PATH, load_routing_registry


def snapshot_from_fixture(registry: dict[str, Any] | None = None):
    """Real SemanticSnapshot rebuilt from the recorded routing registry."""
    from cerebro_mcp.models.semantic import SemanticSnapshot
    from cerebro_mcp.semantic.graph import build_semantic_graph
    from cerebro_mcp.semantic.index import build_token_idf

    registry = registry or load_routing_registry()
    models = registry["models_exec"]
    relationships = registry["relationships"]
    graph, vertex_ids = build_semantic_graph(models, relationships)
    metrics = registry["metrics"]
    return SemanticSnapshot(
        registry_hash=str(registry.get("metadata", {}).get("registry_hash", "fixture")),
        manifest_hash=str(registry.get("metadata", {}).get("manifest_hash", "")),
        catalog_hash=str(registry.get("metadata", {}).get("catalog_hash", "")),
        docs_hash="",
        graph=graph,
        vertex_ids=vertex_ids,
        synonym_index=registry["synonym_index"],
        dimension_index=registry["dimension_index"],
        metrics=metrics,
        models=models,
        relationships=relationships,
        docs_index={},
        loaded_at=0.0,
        token_idf=build_token_idf(metrics.values()),
    )


def fixture_fingerprint(snapshot=None) -> dict[str, Any]:
    """Provenance block recorded in result files (compare refuses latency
    diffs across differing fixture hashes)."""
    from benchmarks.core.envinfo import file_sha256

    fp: dict[str, Any] = {
        "fixture_sha": file_sha256(ROUTING_REGISTRY_PATH),
        "source": "fixture",
    }
    if snapshot is not None:
        fp["registry_hash"] = snapshot.registry_hash
        fp["n_models"] = len(snapshot.models)
        fp["n_metrics"] = len(snapshot.metrics)
    return fp


@contextlib.contextmanager
def deterministic_semantic_runtime(snapshot):
    """The ``semantic_ready`` patch set (tests/test_semantic_find.py:93),
    applied outside pytest. Pins the runtime to ``snapshot``, disables every
    reload path, and points ``data_catalog`` at the same snapshot."""
    import cerebro_mcp.tools.semantic.data_catalog as dc
    from cerebro_mcp.semantic.search import reset_search_cache_for_tests
    from cerebro_mcp.tools.semantic import semantic as semantic_tools
    from cerebro_mcp.tools.semantic.find import _reset_tool_corpus

    semantic_tools.state.reset()
    _reset_tool_corpus()
    reset_search_cache_for_tests()
    dc._INDEX_CACHE.clear()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(semantic_tools.settings, "SEMANTIC_ENABLED", True))
        stack.enter_context(
            mock.patch.object(semantic_tools.settings, "SEMANTIC_REFRESH_INTERVAL_SECONDS", 10_000)
        )
        stack.enter_context(mock.patch.object(semantic_tools.semantic_runtime, "_snapshot", snapshot))
        stack.enter_context(
            mock.patch.object(semantic_tools.semantic_runtime, "_execution_available", True)
        )
        stack.enter_context(mock.patch.object(semantic_tools.semantic_runtime, "_stale_reason", None))
        stack.enter_context(
            mock.patch.object(semantic_tools.manifest, "reload_if_changed", lambda: (False, None))
        )
        stack.enter_context(
            mock.patch.object(semantic_tools.catalog, "reload_if_changed", lambda: (False, None))
        )
        stack.enter_context(
            mock.patch.object(
                semantic_tools.semantic_runtime, "refresh_if_changed", lambda: (False, None)
            )
        )
        stack.enter_context(mock.patch.object(dc, "current_snapshot", lambda: snapshot))
        try:
            yield semantic_tools
        finally:
            semantic_tools.state.reset()
            _reset_tool_corpus()
            reset_search_cache_for_tests()
            dc._INDEX_CACHE.clear()


def reset_semantic_process_state() -> None:
    """Clear every process-global cache the semantic stack keeps, so each
    benchmark case starts cold-deterministic. Verified inventory:

    - session-state singleton (preflight cache lives on it, no TTL/bound)
    - semantic tool rolling stats + discover LRU (``reset_semantic_runtime_stats``)
    - token-idf cache (tools/semantic/semantic.py)
    - planner binding cache, graph path cache
    - ModelSearchIndex cache, find tool corpus, graph telemetry
    - chart registry / report cache (visualization)
    """
    import cerebro_mcp.semantic.graph as sgraph
    import cerebro_mcp.semantic.graph_telemetry as graph_telemetry
    import cerebro_mcp.semantic.planner as planner
    import cerebro_mcp.tools.semantic.semantic as semantic_tools

    from benchmarks.core.fakes import reset_server_state

    reset_server_state()  # includes state.reset() + find corpus + viz registry
    semantic_tools.reset_semantic_runtime_stats()
    semantic_tools._TOKEN_IDF_CACHE.clear()
    planner._BINDING_CACHE.clear()
    sgraph._PATH_CACHE.clear()
    graph_telemetry.reset()
    from cerebro_mcp.semantic.search import reset_search_cache_for_tests

    reset_search_cache_for_tests()
