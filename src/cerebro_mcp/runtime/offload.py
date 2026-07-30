"""Run synchronous MCP tool bodies on a worker thread.

FastMCP (the `mcp` SDK's bundled server) invokes sync tool functions inline on
the single asyncio event loop — it does not offload them to a worker thread. So
one slow tool (a 30s ClickHouse query, a manifest reload, a heavy report
render, a cold browser launch) blocks the whole server and times out *every*
concurrent tool at the client. Wrapping the body in an async offload keeps the
event loop responsive while the work runs on a thread.

This failure mode was observed twice in production, and both times the tool
that appeared to hang was NOT the tool doing the blocking — a bare `SELECT 1`
timed out even though `execute_query` was already offloaded, because the
*request* was never dispatched. That is why :func:`install_tool_offload` exists:
per-tool decoration only protects the tools someone remembered to decorate, and
the blocking tool is by definition the one nobody suspected.

`functools.wraps` preserves the wrapped function's signature, docstring, and
annotations, and FastMCP introspects tools via ``inspect.signature`` which
follows ``__wrapped__`` — so the generated input schema and tool description are
unchanged (verified against the pinned mcp SDK). ClickHouse clients are
thread-local (``clients/clickhouse.py``), so per-thread execution is safe.

Two ways to apply it:

* **Globally** (preferred) — call :func:`install_tool_offload` once after every
  ``register_*_tools`` call. It rewrites each already-registered SYNC tool in
  place and skips async ones.
* **Per tool** — place ``@offloaded`` BELOW ``@mcp.tool(...)`` so the tool
  registers the async wrapper::

      @mcp.tool()
      @offloaded
      def execute_query(sql: str) -> dict:
          ...
"""

import functools
import logging

import anyio

from cerebro_mcp.config import settings

logger = logging.getLogger(__name__)

_OFFLOAD_INSTALLED_ATTR = "_cerebro_tool_offload_installed"

#: Bounds how many tool bodies run concurrently. Without a cap, N slow tools
#: hold N threads and the default anyio limiter (40) becomes the new wedge
#: point; with a cap, excess calls queue on the limiter instead of exhausting
#: the pool that everything else — including the readiness probe's ClickHouse
#: check — also borrows from.
_TOOL_LIMITER = anyio.CapacityLimiter(settings.TOOL_OFFLOAD_MAX_THREADS)


def offloaded(fn, *, limiter: anyio.CapacityLimiter | None = None):
    """Wrap a sync callable so it runs on a worker thread when awaited."""

    @functools.wraps(fn)
    async def _wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(
            functools.partial(fn, *args, **kwargs),
            limiter=limiter,
        )

    return _wrapper


def install_tool_offload(mcp) -> int:
    """Offload every registered SYNC tool onto a worker thread.

    Idempotent, and safe to call when the SDK internals are absent — mirrors
    the defensive style of ``install_auto_tool_tracing``.

    Must run AFTER every ``register_*_tools`` call, otherwise tools registered
    later stay inline. ``tests/test_tool_offload.py`` asserts the invariant
    across all registrations so a late registration fails CI rather than
    silently reopening the hole.

    Patches the ``Tool`` object rather than ``ToolManager.call_tool`` because
    the sync/async decision is made further down, inside
    ``Tool.run -> fn_metadata.call_fn_with_arg_validation(self.fn, self.is_async, ...)``.
    Wrapping ``call_tool`` would mean re-implementing argument validation. The
    schema was already computed by ``Tool.from_function`` at registration time,
    so mutating ``fn`` afterwards cannot change it.

    Returns the number of tools wrapped.
    """
    if getattr(mcp, _OFFLOAD_INSTALLED_ATTR, False):
        return 0
    if not settings.TOOL_OFFLOAD_ENABLED:
        return 0

    tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
    if not isinstance(tools, dict):
        logger.warning(
            "install_tool_offload: no _tool_manager._tools dict on this SDK; "
            "sync tools will run inline on the event loop"
        )
        return 0

    wrapped = 0
    for tool in tools.values():
        # `is_async` is the correct automatic guard. Tools that genuinely need
        # the loop are already `async def` — `load_tools` awaits
        # `mcp.get_context().session`, and the workflow-resume tools use
        # aiosqlite — so they are skipped, as are the handful already carrying
        # the `@offloaded` decorator.
        if getattr(tool, "is_async", True):
            continue
        try:
            tool.fn = offloaded(tool.fn, limiter=_TOOL_LIMITER)
            tool.is_async = True
        except Exception:  # pragma: no cover - defensive against SDK changes
            logger.warning(
                "install_tool_offload: could not wrap %s",
                getattr(tool, "name", "?"),
                exc_info=True,
            )
            continue
        wrapped += 1

    setattr(mcp, _OFFLOAD_INSTALLED_ATTR, True)
    return wrapped
