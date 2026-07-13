"""Minimal MCP-over-SSE client helpers for the load suite.

Adapted from ``scripts/mcp_smoke_test.py`` (which stays untouched). Only the
transport plumbing lives here — connect, initialize, flatten tool results —
so the load suite depends on the ``mcp`` SDK alone, never on ``cerebro_mcp``
(the server side runs in a separate subprocess with its own env).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


@asynccontextmanager
async def open_sse_session(
    url: str,
    token: str | None,
    *,
    timeout: float = 10.0,
    sse_read_timeout: float = 120.0,
) -> AsyncIterator[ClientSession]:
    """Yield an initialized :class:`ClientSession` over SSE.

    ``initialize()`` runs before the session is yielded, so the elapsed time
    of entering this context manager IS the session TTFB (connect + MCP
    handshake) — the load suite times exactly that.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with sse_client(
        url=url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def tool_text(result: Any) -> str:
    """Concatenate all text blocks from a ``CallToolResult``."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def tool_is_error(result: Any) -> bool:
    """True when a tool call failed — either the protocol-level ``isError``
    flag, or the cerebro convention of an ``Error:`` / ``Query rejected:``
    prefix in a plain text block (returned without setting ``isError``)."""
    if bool(getattr(result, "isError", False)):
        return True
    stripped = tool_text(result).lstrip()
    return stripped.startswith("Error:") or stripped.startswith("Query rejected:")
