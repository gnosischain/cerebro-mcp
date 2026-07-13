"""Cerebro-MCP benchmark harness.

Run via ``python -m benchmarks.run`` — NEVER under pytest.

The pytest suite installs an autouse fixture (``tests/conftest.py::
_disable_tool_offload``) that strips the ``@offloaded`` thread-pool hop from
heavy tools. Running benchmark code under pytest would therefore silently
measure a different code path than production. ``python -m benchmarks.run``
never imports conftest, so the offload wrapper (and the auto tool tracing
installed on the server) stay exactly as they are in a deployed server.

Import discipline: ``cerebro_mcp`` reads env at import time
(``config.Settings()`` is a module global), so nothing under ``benchmarks/``
may import ``cerebro_mcp`` at module top level. ``run.py`` redirects all
artifact paths (reports, thinking logs, event store, research dir, security
audit) into a per-run scratch directory FIRST, then dynamically imports the
suites.

Files here deliberately avoid the ``test_*.py`` naming pattern so a bare
``pytest`` at the repo root does not collect them.
"""
