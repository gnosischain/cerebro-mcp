"""Run a synchronous MCP tool body on a worker thread.

FastMCP (the `mcp` SDK's bundled server) invokes sync tool functions inline on
the single asyncio event loop — it does not offload them to a worker thread. So
one slow tool (a 30s ClickHouse query, a manifest reload, a heavy report
render, a cold browser launch) blocks the whole stdio server and times out
*every* concurrent tool at the client. Wrapping the body in an async offload
keeps the event loop responsive while the work runs on a thread.

`functools.wraps` preserves the wrapped function's signature, docstring, and
annotations, and FastMCP introspects tools via ``inspect.signature`` which
follows ``__wrapped__`` — so the generated input schema and tool description are
unchanged (verified against the pinned mcp SDK). ClickHouse clients are
thread-local (``clients/clickhouse.py``), so per-thread execution is safe.

Usage — place BELOW ``@mcp.tool(...)`` so the tool registers the async wrapper::

    @mcp.tool()
    @offloaded
    def execute_query(sql: str) -> dict:
        ...
"""

import functools

import anyio


def offloaded(fn):
    @functools.wraps(fn)
    async def _wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(
            functools.partial(fn, *args, **kwargs)
        )

    return _wrapper
