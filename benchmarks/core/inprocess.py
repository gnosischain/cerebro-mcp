"""In-process server builders for the benchmark suites.

Two flavors:

- :func:`build_inprocess_fake` — the deterministic bench server over the
  recorded fixtures (corpus manifest + routing-registry snapshot + canned
  ClickHouse). No network, no credentials.
- :func:`build_inprocess_real` — the PRODUCTION module-global server from
  ``cerebro_mcp.server``, with the same loader sequence ``main()`` runs before
  serving. Only meaningful when ``CEREBRO_EVAL_CLICKHOUSE=1`` supplies real
  ClickHouse credentials.

All ``cerebro_mcp`` imports are lazy (env-first discipline: ``benchmarks/run.py``
must redirect artifact env vars before the first ``cerebro_mcp`` import).
"""

from __future__ import annotations

from typing import Any


def build_inprocess_fake(*, tracing: bool = True) -> tuple[Any, Any, Any, dict]:
    """Deterministic in-process bench server over the recorded fixtures.

    Returns ``(mcp, ch, snapshot, corpus)``:

    - ``mcp`` — FastMCP with the benchmarked tool surface (server.py order).
    - ``ch`` — ``BenchClickHouse`` wired with the corpus table schemas.
    - ``snapshot`` — real ``SemanticSnapshot`` rebuilt from the routing
      registry fixture.
    - ``corpus`` — the frozen model corpus dict.

    IMPORTANT: the caller must enter
    ``benchmarks.core.semantic_env.deterministic_semantic_runtime(snapshot)``
    around any tool calls — the server is built here, but the semantic runtime
    (pinned snapshot, disabled reload paths, ``SEMANTIC_ENABLED``) is only
    live inside that context manager.

    Tool REGISTRATION itself is performed with ``SEMANTIC_ENABLED`` forced on
    (``register_semantic_tools`` / ``register_find_tool`` early-return when it
    is off), so the built surface does not depend on the developer's ``.env``.
    """
    from unittest import mock

    from benchmarks.core.fakes import bench_clickhouse_from_corpus, build_bench_server
    from benchmarks.core.semantic_env import snapshot_from_fixture
    from tests.eval.corpus_fixtures import install_fixture_manifest, load_search_corpus

    corpus = load_search_corpus()
    install_fixture_manifest(corpus)
    snapshot = snapshot_from_fixture()
    ch = bench_clickhouse_from_corpus(corpus)

    from cerebro_mcp.config import settings

    with mock.patch.object(settings, "SEMANTIC_ENABLED", True):
        mcp = build_bench_server(ch, tracing=tracing)
    return mcp, ch, snapshot, corpus


def build_inprocess_real() -> Any:
    """The production module-global server, loaders primed like ``main()``.

    Imports ``cerebro_mcp.server`` (module import already constructs the
    global ``mcp`` / ``ch`` and installs auto tool tracing) and then runs the
    exact loader sequence ``main()`` performs before ``mcp.run``:
    ``manifest.load() -> catalog.load() -> docs_index.load() ->
    semantic_runtime.load()``.

    Deliberately omitted from ``main()``: transport selection, sandbox
    atexit hooks, and the workflow event-store resume sweep — none of them
    affect per-tool latency and the event store would write outside the
    scratch redirection contract.

    Only call when ``ctx.real_clickhouse`` — the global ``ClickHouseManager``
    connects with real credentials on first query.
    """
    import cerebro_mcp.server as server
    from cerebro_mcp.loaders.catalog import catalog
    from cerebro_mcp.loaders.docs import docs_index
    from cerebro_mcp.loaders.manifest import manifest
    from cerebro_mcp.loaders.semantic import semantic_runtime

    manifest.load()
    catalog.load()
    docs_index.load()
    semantic_runtime.load()
    return server.mcp
