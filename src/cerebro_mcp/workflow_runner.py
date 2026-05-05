"""Phase 3: parallel fan-out workflow runner with reviewer gates.

Implements the architecture from Google's "Science of Scaling Agent
Systems" paper: centralized orchestration + parallel independent
sub-tasks + a validator gate to bound error amplification (the paper
measured 17.2× for uncoordinated parallel agents vs 4.4× with a
validating orchestrator).

Public API:

    SubTask(name, coro)                         # one analyst sub-task
    run_parallel_phase(workflow_id, phase_name, # gather + gate
                       subtasks, gate_name)
    run_sequential_phase(workflow_id, phase_name, steps)

Both helpers write events to the EventStore at every transition so an
interrupted workflow can be replayed.

Concurrency:
    `run_parallel_phase` uses `asyncio.gather(..., return_exceptions=True)`
    so one sub-task failing doesn't kill its peers. Failures are recorded
    as `subtask_failed` events; if any sub-task failed, the gate is set
    to `failed` and the helper raises after recording every result.

Bounded parallelism:
    A semaphore bounded by `settings.WORKFLOW_MAX_PARALLEL` caps how many
    sub-tasks run concurrently. Default 8 — generous for analyst fan-out,
    not so high that the LLM provider rate-limits us.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from cerebro_mcp.config import settings
from cerebro_mcp.event_store import EventStore, default_event_store
from cerebro_mcp.workflow_payloads import (
    EVENT_PHASE_COMPLETED,
    EVENT_PHASE_FAILED,
    EVENT_PHASE_STARTED,
    EVENT_SUBTASK_COMPLETED,
    EVENT_SUBTASK_FAILED,
    EVENT_SUBTASK_STARTED,
    EVENT_WORKFLOW_STARTED,
    GATE_FAILED,
    GATE_READY,
    WORKFLOW_FAILED,
    WORKFLOW_RUNNING,
)

logger = logging.getLogger(__name__)


@dataclass
class SubTask:
    """One unit of work in a parallel phase.

    `coro` is a no-argument async callable that returns a JSON-serializable
    result dict. Wrapping closures keep call-sites clean:

        SubTask("tokenomics_analyst",
                lambda: analyst_run(workflow_id, ...))
    """

    name: str
    coro: Callable[[], Awaitable[dict[str, Any]]]


def new_workflow_id(prefix: str = "wf") -> str:
    """Generate a stable, URL-safe workflow id. Callers can also supply
    their own deterministic ids when they need cross-process coordination."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


async def begin_workflow(
    workflow_id: str,
    kind: str,
    metadata: dict[str, Any] | None = None,
    store: EventStore | None = None,
) -> EventStore:
    """Create the workflow row and emit the `workflow_started` event.

    Returns the EventStore so callers don't need to grab it separately.
    Idempotent only if the caller has already created the workflow row;
    otherwise the underlying INSERT raises on duplicate id.
    """
    s = store or default_event_store()
    await s.init()
    await s.create_workflow(workflow_id, kind, metadata or {})
    await s.append_event(
        workflow_id, EVENT_WORKFLOW_STARTED,
        {"kind": kind, "metadata": metadata or {}},
    )
    return s


async def run_parallel_phase(
    workflow_id: str,
    phase_name: str,
    subtasks: list[SubTask],
    gate_name: str,
    store: EventStore | None = None,
    max_parallel: int | None = None,
) -> dict[str, Any]:
    """Execute `subtasks` concurrently and gate on full success.

    Returns:
        `{name: result_dict}` for every successful subtask.

    Raises:
        `RuntimeError` if any sub-task raised. Every sub-task still runs
        to completion (or its own exception); the gate is set to `failed`
        and the names of the failures are included in the exception
        message and the gate payload.

    Behavior matches the scaling-paper "centralized parallel" pattern:
    fan-out to independent sub-agents, then gate at a validator before
    any downstream phase touches the results.
    """
    s = store or default_event_store()
    await s.init()
    sem = asyncio.Semaphore(max_parallel or settings.WORKFLOW_MAX_PARALLEL)

    await s.append_event(
        workflow_id, EVENT_PHASE_STARTED,
        {"phase": phase_name, "subtasks": [st.name for st in subtasks]},
    )

    async def _run_one(st: SubTask) -> tuple[str, Any]:
        async with sem:
            await s.append_event(
                workflow_id, EVENT_SUBTASK_STARTED, {"name": st.name}
            )
            try:
                result = await st.coro()
                await s.append_event(
                    workflow_id, EVENT_SUBTASK_COMPLETED,
                    {"name": st.name, "result": result},
                )
                return st.name, result
            except Exception as exc:
                logger.exception(
                    "subtask_failed workflow=%s name=%s",
                    workflow_id, st.name,
                )
                await s.append_event(
                    workflow_id, EVENT_SUBTASK_FAILED,
                    {"name": st.name, "error": f"{type(exc).__name__}: {exc}"},
                )
                return st.name, exc

    pairs = await asyncio.gather(*(_run_one(st) for st in subtasks))
    results: dict[str, Any] = {}
    failed: dict[str, str] = {}
    for name, value in pairs:
        if isinstance(value, Exception):
            failed[name] = f"{type(value).__name__}: {value}"
        else:
            results[name] = value

    if failed:
        await s.set_gate(
            workflow_id, gate_name, GATE_FAILED,
            {"failed": failed, "succeeded": list(results)},
        )
        await s.append_event(
            workflow_id, EVENT_PHASE_FAILED,
            {"phase": phase_name, "failed": failed},
        )
        await s.mark_workflow_status(workflow_id, WORKFLOW_FAILED)
        raise RuntimeError(
            f"Phase {phase_name!r} failed: {list(failed)}"
        )

    await s.set_gate(
        workflow_id, gate_name, GATE_READY,
        {"subtasks": list(results)},
    )
    await s.append_event(
        workflow_id, EVENT_PHASE_COMPLETED,
        {"phase": phase_name, "subtasks": list(results)},
    )
    return results


async def run_sequential_phase(
    workflow_id: str,
    phase_name: str,
    steps: list[SubTask],
    store: EventStore | None = None,
) -> dict[str, Any]:
    """Execute `steps` one after another. Use for sequential math chains
    where step N depends on step N-1. Per the scaling paper, sequential
    work should NOT fan out — the coordination tax kills the budget.
    """
    s = store or default_event_store()
    await s.init()
    await s.append_event(
        workflow_id, EVENT_PHASE_STARTED,
        {"phase": phase_name, "steps": [st.name for st in steps], "mode": "sequential"},
    )
    results: dict[str, Any] = {}
    for st in steps:
        await s.append_event(
            workflow_id, EVENT_SUBTASK_STARTED, {"name": st.name}
        )
        try:
            result = await st.coro()
        except Exception as exc:
            logger.exception(
                "subtask_failed workflow=%s name=%s", workflow_id, st.name
            )
            await s.append_event(
                workflow_id, EVENT_SUBTASK_FAILED,
                {"name": st.name, "error": f"{type(exc).__name__}: {exc}"},
            )
            await s.append_event(
                workflow_id, EVENT_PHASE_FAILED,
                {"phase": phase_name, "failed_at": st.name},
            )
            await s.mark_workflow_status(workflow_id, WORKFLOW_FAILED)
            raise
        await s.append_event(
            workflow_id, EVENT_SUBTASK_COMPLETED,
            {"name": st.name, "result": result},
        )
        results[st.name] = result
    await s.append_event(
        workflow_id, EVENT_PHASE_COMPLETED, {"phase": phase_name},
    )
    return results
