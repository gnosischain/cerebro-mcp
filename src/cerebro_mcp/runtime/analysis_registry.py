"""Per-(owner, handle) analysis cycles (connector plan R10 §4.1/P0-9, R9 P0-6).

Why this exists: ``session_state.state`` is a process-global singleton, and
``begin_analysis_cycle()`` — called by every ``preflight_analytics_request``
— selectively clears it for ALL concurrent users (the production incident is
recorded at ``charts.py:1078-1098``: finishing one report wiped another
conversation's discovery evidence mid-analysis). Root cause:
``STREAMABLE_HTTP_STATELESS`` means no session spans a conversation, so the
singleton was the only continuity there was. This registry supplies that
continuity KEYED BY IDENTITY instead.

Contract (decisions A2-A4):

- A handle is 128-bit CSPRNG hex — entropy, not format (a UUID from a weak
  RNG would satisfy "UUIDv4" and fail the actual requirement).
- Keyed ``(owner_hash, handle)`` with the owner taken from the VERIFIED
  principal contextvar, never from client input; a handle presented by any
  other owner is rejected (possession is not authentication).
- ``find``/``preflight`` MINT (or REUSE — reuse never re-runs
  ``begin_analysis_cycle``); every ``Handle.REQUIRED`` tool must present a
  live handle.
- Capacity: 8 live handles/owner, 2000 global; eviction considers ONLY idle
  handles (refcount 0) — creation fails with a typed error when every
  candidate is active, never evicts under a running call, never blocks.
- Expiry: 24 h idle / 7 d absolute; expired handles are rejected AND
  evictable — reject-don't-mint, so an expired id surfaces to the caller
  instead of silently becoming a fresh empty cycle.
- Refcounts decrement in ``finally`` (cancellation included); lookup,
  expiry validation and the increment are ONE atomic step under the
  registry lock (no check-then-increment race).

Off-profile (stdio / internal_full) the registry is bypassed entirely and
the legacy singleton serves — behavior there is unchanged.
"""

from __future__ import annotations

import contextvars
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

MAX_HANDLES_PER_OWNER = 8
MAX_HANDLES_GLOBAL = 2000
IDLE_EXPIRY_S = 24 * 3600
ABSOLUTE_EXPIRY_S = 7 * 24 * 3600


class AnalysisHandleError(Exception):
    """Typed, caller-visible handle failures (missing/unknown/expired/…)."""


class AnalysisCapacityError(AnalysisHandleError):
    """No idle victim to evict — the caller must retry later or reuse."""


@dataclass
class AnalysisCycle:
    owner: str | None
    handle: str
    state: Any                      # a SessionState (late import avoidance)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    refcount: int = 0
    #: Per-cycle chart registry (charts.py proxies to this under the
    #: profile). Scoping the STORAGE is what fixes the broadcast, listing
    #: and {{chart:ID}} disclosure surfaces in one stroke — a caller can
    #: only ever see or resolve chart ids from their own cycle.
    charts: dict = field(default_factory=dict)
    chart_counter: int = 0


_current_handle: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_analysis_handle", default=None
)

_lock = threading.Lock()
_cycles: dict[tuple[str | None, str], AnalysisCycle] = {}

#: Sentinel for "not owner-scoped". Distinct from None, which is a real
#: owner key (stdio / no principal).
_GLOBAL = object()


def _now() -> float:
    return time.time()


def _expired(cycle: AnalysisCycle, now: float) -> bool:
    return (
        now - cycle.last_used > IDLE_EXPIRY_S
        or now - cycle.created_at > ABSOLUTE_EXPIRY_S
    )


def _new_state():
    from cerebro_mcp.tools.governance.session_state import SessionState

    return SessionState()


def mint_or_reuse(owner: str | None, handle: str | None) -> tuple[str, bool]:
    """find/preflight entry: reuse a live handle or mint a fresh one.

    Returns (handle, reused). Reuse NEVER resets the cycle — R9-audit
    blocker 6: re-running begin_analysis_cycle on reuse split one analysis
    across cycles and reintroduced the redo loop. An expired or unknown
    supplied handle is REJECTED (reject-don't-mint), not silently replaced.
    """
    now = _now()
    with _lock:
        if handle:
            cycle = _cycles.get((owner, handle))
            if cycle is None:
                raise AnalysisHandleError(
                    f"analysis_id {handle[:8]}… is unknown for this caller — "
                    "omit it to start a fresh cycle"
                )
            if _expired(cycle, now):
                del _cycles[(owner, handle)]
                raise AnalysisHandleError(
                    f"analysis_id {handle[:8]}… has expired — omit it to "
                    "start a fresh cycle"
                )
            cycle.last_used = now
            return handle, True

        # Mint. Capacity is reserved atomically under the same lock.
        _evict_expired_locked(now)
        owned = [c for (o, _), c in _cycles.items() if o == owner]
        if len(owned) >= MAX_HANDLES_PER_OWNER:
            if not _evict_one_idle_locked(owner, now):
                raise AnalysisCapacityError(
                    f"{MAX_HANDLES_PER_OWNER} analysis cycles are live for "
                    "this caller and all are active — finish or reuse one"
                )
        if len(_cycles) >= MAX_HANDLES_GLOBAL:
            if not _evict_one_idle_locked(_GLOBAL, now):
                raise AnalysisCapacityError(
                    "global analysis-cycle capacity reached with no idle "
                    "cycle to evict"
                )
        new_handle = secrets.token_hex(16)
        _cycles[(owner, new_handle)] = AnalysisCycle(
            owner=owner, handle=new_handle, state=_new_state()
        )
        return new_handle, False


def _evict_expired_locked(now: float) -> None:
    dead = [k for k, c in _cycles.items() if c.refcount == 0 and _expired(c, now)]
    for k in dead:
        del _cycles[k]


def _evict_one_idle_locked(owner, now: float) -> bool:
    """Evict the oldest-idle cycle. Never touches refcount > 0.

    ``owner`` is either an owner key (scoped eviction) or the ``_GLOBAL``
    sentinel (unscoped). A plain ``None`` cannot mean "unscoped" here:
    ``None`` is a LEGITIMATE owner key (stdio / no principal), so
    conflating them let a None-owner caller evict other owners' cycles and
    slip past its own per-owner cap.
    """
    candidates = [
        (c.last_used, k)
        for k, c in _cycles.items()
        if c.refcount == 0 and (owner is _GLOBAL or k[0] == owner)
    ]
    if not candidates:
        return False
    _, victim = min(candidates)
    del _cycles[victim]
    return True


def acquire(owner: str | None, handle: str) -> None:
    """Validate + refcount-increment ATOMICALLY (Handle.REQUIRED tools)."""
    now = _now()
    with _lock:
        cycle = _cycles.get((owner, handle))
        if cycle is None:
            raise AnalysisHandleError(
                f"analysis_id {handle[:8]}… is unknown for this caller — "
                "call find or preflight_analytics_request first"
            )
        if _expired(cycle, now):
            if cycle.refcount == 0:
                del _cycles[(owner, handle)]
            raise AnalysisHandleError(
                f"analysis_id {handle[:8]}… has expired — call find or "
                "preflight_analytics_request for a fresh one"
            )
        cycle.refcount += 1
        cycle.last_used = now


def release(owner: str | None, handle: str) -> None:
    """Refcount decrement — callers run this in ``finally`` so a cancelled
    request can never pin a cycle forever."""
    with _lock:
        cycle = _cycles.get((owner, handle))
        if cycle is not None and cycle.refcount > 0:
            cycle.refcount -= 1


def state_for(owner: str | None, handle: str):
    with _lock:
        cycle = _cycles.get((owner, handle))
        return cycle.state if cycle is not None else None


def current_cycle() -> AnalysisCycle | None:
    """The cycle bound to the current request context, or None."""
    from cerebro_mcp.runtime.identity import get_current_owner

    handle = _current_handle.get()
    if not handle:
        return None
    with _lock:
        return _cycles.get((get_current_owner(), handle))


def set_current_handle(handle: str | None) -> contextvars.Token:
    return _current_handle.set(handle)


def reset_current_handle(token: contextvars.Token) -> None:
    _current_handle.reset(token)


def get_current_handle() -> str | None:
    return _current_handle.get()


def reset_registry_for_tests() -> None:
    with _lock:
        _cycles.clear()


def registry_size() -> int:
    with _lock:
        return len(_cycles)
