from __future__ import annotations

import asyncio
import atexit
import logging
from pathlib import Path

from cerebro_mcp.config import settings

logger = logging.getLogger(__name__)


def init_ssl_trust() -> bool:
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Phase 2: sandbox lifecycle
#
# - atexit: tear down every sandbox so we never leak parquet files or
#   in-memory DuckDB connections when the server stops.
# - periodic sweep: drop sandboxes idle longer than SANDBOX_TTL_SECONDS so
#   abandoned simulations don't accumulate disk/memory.
# ---------------------------------------------------------------------------

_sandbox_atexit_installed = False
_sandbox_sweeper_task: asyncio.Task | None = None


def install_sandbox_atexit() -> None:
    """Idempotent. Registers an atexit hook that calls
    `SandboxManager.shutdown()` so the process exit cleans state."""
    global _sandbox_atexit_installed
    if _sandbox_atexit_installed:
        return
    # Lazy import — sandbox_manager pulls in duckdb, which we don't want to
    # load if the server never invokes a sandbox tool.
    from cerebro_mcp.runtime.sandbox_manager import default_sandbox_manager

    def _shutdown() -> None:
        try:
            default_sandbox_manager().shutdown()
        except Exception:
            logger.exception("sandbox shutdown raised during atexit")

    atexit.register(_shutdown)
    _sandbox_atexit_installed = True


def install_sandbox_sweeper(loop: asyncio.AbstractEventLoop) -> None:
    """Schedule a periodic sweep of expired sandboxes on the given event loop.

    Cadence is `SANDBOX_TTL_SECONDS // 6` (clamped between 60 s and 600 s)
    so sandboxes don't out-live their TTL by more than ~17%.
    """
    global _sandbox_sweeper_task
    if _sandbox_sweeper_task is not None and not _sandbox_sweeper_task.done():
        return

    interval = max(60, min(600, settings.SANDBOX_TTL_SECONDS // 6))
    from cerebro_mcp.runtime.sandbox_manager import default_sandbox_manager

    async def _sweeper() -> None:
        mgr = default_sandbox_manager()
        while True:
            try:
                await asyncio.sleep(interval)
                evicted = mgr.sweep_expired()
                if evicted:
                    logger.info("sandbox_sweep evicted=%d", evicted)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sandbox sweep iteration failed")

    _sandbox_sweeper_task = loop.create_task(_sweeper())


# ---------------------------------------------------------------------------
# Phase 3: workflow event-log bootstrap
#
# On server startup we open the SQLite event store and mark abandoned
# workflows as `orphaned`. "Abandoned" = status `running` or `waiting_gate`
# but `updated_at` older than `WORKFLOW_ORPHAN_AGE_SECONDS` (default 24h).
# This stops a leftover from an older crash from looking like a live
# workflow forever; it does NOT yet auto-resume — that requires a
# workflow registry (kind → resume_fn) which lands incrementally as
# individual workflow types are migrated to the event-log model.
# ---------------------------------------------------------------------------


async def init_event_store_async() -> dict[str, int]:
    """Initialize the event store schema and run the WorkflowRegistry
    resume sweep on stale workflows.

    Behavior:
      1. Bootstrap the SQLite schema (idempotent).
      2. Register all known resume handlers (currently: research_project).
      3. For every workflow in `running` / `waiting_gate` whose
         `updated_at` is older than `WORKFLOW_ORPHAN_AGE_SECONDS`:
           - Dispatch to the registered handler if one exists.
             Handler decides: ready_to_resume / complete / failed.
             Resume hint is written as a `workflow_resume_hint` event.
           - If no handler exists, fall back to marking `orphaned`
             (the previous behavior).
      4. Return a counter dict `{ready_to_resume, complete, failed,
         orphaned, no_handler}` so the caller can log a summary.

    Safe to call multiple times — every step is idempotent.
    """
    # Lazy imports — keep startup paths that don't use the event store
    # off the aiosqlite / registry import chains.
    from cerebro_mcp.workflow.event_store import default_event_store
    from cerebro_mcp.research.resume import install_research_resume_handler
    from cerebro_mcp.storyteller.resume import (
        install_storyteller_resume_handler,
    )
    from cerebro_mcp.workflow.registry import (
        ACTION_COMPLETE,
        ACTION_FAILED,
        ACTION_NO_HANDLER,
        ACTION_ORPHAN,
        ACTION_READY_TO_RESUME,
        default_workflow_registry,
    )

    store = default_event_store()
    await store.init()

    # Register all known kinds. New handlers (storyteller, mmm, etc.)
    # land here as they're migrated.
    install_research_resume_handler()
    install_storyteller_resume_handler()

    registry = default_workflow_registry()
    outcomes = await registry.resume_all_running(
        max_age_seconds=settings.WORKFLOW_ORPHAN_AGE_SECONDS,
    )
    counts = {
        ACTION_READY_TO_RESUME: 0,
        ACTION_COMPLETE: 0,
        ACTION_FAILED: 0,
        ACTION_ORPHAN: 0,
        ACTION_NO_HANDLER: 0,
    }
    for outcome in outcomes:
        counts[outcome.action] = counts.get(outcome.action, 0) + 1
        logger.info(
            "workflow_resume_outcome id=%s kind=%s action=%s",
            outcome.workflow_id, outcome.kind, outcome.action,
        )
    return counts


def init_event_store_sync() -> int:
    """Run `init_event_store_async` from a synchronous context. Used by
    `server.py:main()` before FastMCP starts its own event loop."""
    try:
        return asyncio.run(init_event_store_async())
    except RuntimeError as exc:
        # `asyncio.run` raises if a loop is already running. Fall back
        # to scheduling on whatever loop is current.
        if "already running" in str(exc):
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(init_event_store_async())
        raise


def validate_remote_transport_auth(auth_token: str | None) -> None:
    if auth_token or settings.ALLOW_INSECURE_REMOTE_TRANSPORT:
        return
    raise RuntimeError(
        "MCP_AUTH_TOKEN is required for SSE unless "
        "ALLOW_INSECURE_REMOTE_TRANSPORT=true"
    )


def ensure_writable_dir(path: Path) -> None:
    normalized = path.expanduser()
    try:
        normalized.mkdir(parents=True, exist_ok=True)
        probe = normalized / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"Directory '{normalized}' is not writable. "
            "Set CEREBRO_RESEARCH_DIR to a writable local path before starting "
            "the server."
        ) from exc
