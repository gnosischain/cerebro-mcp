"""Phase 3: structured payloads for resumable workflow events.

The event log stores arbitrary JSON, but events that wrap LLM calls have a
binding contract: they MUST carry the *exact* message history sent to the
provider, the system prompt, and the tool schemas the model saw. Without
that, replay can't re-issue an interrupted call with the same context — it
would have to start the agent over from scratch, which defeats the point
of the event log.

This module defines the dataclasses and a couple of helpers. Everything is
plain-Python and JSON-serializable; nothing imports the SDK.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Event kinds (string-typed, free-form — these are the canonical names but
# callers can introduce new kinds without breaking the schema).
# ---------------------------------------------------------------------------

EVENT_WORKFLOW_STARTED = "workflow_started"
EVENT_PHASE_STARTED = "phase_started"
EVENT_PHASE_COMPLETED = "phase_completed"
EVENT_PHASE_FAILED = "phase_failed"
EVENT_SUBTASK_STARTED = "subtask_started"
EVENT_SUBTASK_COMPLETED = "subtask_completed"
EVENT_SUBTASK_FAILED = "subtask_failed"
EVENT_LLM_CALL_STARTED = "llm_call_started"
EVENT_LLM_CALL_COMPLETED = "llm_call_completed"
EVENT_LLM_CALL_FAILED = "llm_call_failed"
EVENT_GATE_REACHED = "gate_reached"
EVENT_GATE_PASSED = "gate_passed"
EVENT_GATE_FAILED = "gate_failed"
EVENT_NOTE = "note"

# Workflow status values
WORKFLOW_RUNNING = "running"
WORKFLOW_WAITING_GATE = "waiting_gate"
WORKFLOW_COMPLETED = "completed"
WORKFLOW_FAILED = "failed"
WORKFLOW_ORPHANED = "orphaned"

# Gate status values
GATE_PENDING = "pending"
GATE_READY = "ready"
GATE_PASSED = "passed"
GATE_FAILED = "failed"


# ---------------------------------------------------------------------------
# LLM message + call payloads
# ---------------------------------------------------------------------------


@dataclass
class LLMTurn:
    """One turn in an LLM conversation.

    The `content` field is a list of provider-shaped content blocks
    (Anthropic content blocks: `{"type": "text", "text": ...}`,
    `{"type": "tool_use", ...}`, `{"type": "tool_result", ...}`). We keep
    them opaque so we don't take an SDK dependency at this layer.
    """

    role: str                           # "user" | "assistant" | "tool"
    content: list[dict[str, Any]]
    model: str = ""                     # exact provider model id
    stop_reason: str | None = None      # "end_turn" | "tool_use" | None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMTurn":
        return cls(
            role=data.get("role", "user"),
            content=list(data.get("content", []) or []),
            model=data.get("model", "") or "",
            stop_reason=data.get("stop_reason"),
        )


@dataclass
class LLMCallEvent:
    """Payload for `llm_call_started` / `llm_call_completed` /
    `llm_call_failed` events.

    On `started`: `messages` is the full history sent in this call;
    `response` and `elapsed_seconds` are unset.

    On `completed`: `response` is the assistant's reply turn,
    `elapsed_seconds` is wall time of the API call.

    On `failed`: `response` is None, `error` carries provider message
    + class.
    """

    subtask_name: str                       # e.g. "tokenomics_analyst"
    call_id: str                            # caller-chosen, unique within workflow
    system_prompt: str
    messages: list[LLMTurn]
    tool_schemas: list[dict[str, Any]]      # provider-shaped tool defs
    response: LLMTurn | None = None
    elapsed_seconds: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_name": self.subtask_name,
            "call_id": self.call_id,
            "system_prompt": self.system_prompt,
            "messages": [t.to_dict() for t in self.messages],
            "tool_schemas": list(self.tool_schemas),
            "response": self.response.to_dict() if self.response else None,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMCallEvent":
        resp = data.get("response")
        return cls(
            subtask_name=data["subtask_name"],
            call_id=data["call_id"],
            system_prompt=data.get("system_prompt", "") or "",
            messages=[LLMTurn.from_dict(t) for t in data.get("messages", [])],
            tool_schemas=list(data.get("tool_schemas", []) or []),
            response=LLMTurn.from_dict(resp) if resp else None,
            elapsed_seconds=data.get("elapsed_seconds"),
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# Helpers for resume — given an event stream, find unfinished LLM calls.
# ---------------------------------------------------------------------------


def find_unfinished_llm_calls(events: list[dict[str, Any]]) -> list[LLMCallEvent]:
    """Walk events in order, return the LLMCallEvent payloads for calls
    that have a `started` but no matching `completed` or `failed`.

    Resume strategy: re-issue exactly these calls with the recorded
    `messages`. The replay finishes the work that was in flight when the
    process died, then resumes downstream phases.
    """
    started: dict[tuple[str, str], LLMCallEvent] = {}
    for ev in events:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind not in (
            EVENT_LLM_CALL_STARTED,
            EVENT_LLM_CALL_COMPLETED,
            EVENT_LLM_CALL_FAILED,
        ):
            continue
        try:
            call = LLMCallEvent.from_dict(payload)
        except KeyError:
            # Older or malformed events — skip rather than crash.
            continue
        key = (call.subtask_name, call.call_id)
        if kind == EVENT_LLM_CALL_STARTED:
            started[key] = call
        else:
            started.pop(key, None)
    return list(started.values())


def serialize_payload(payload: Any) -> str:
    """JSON-serialize an event payload. Dataclasses are converted via
    `to_dict()` if available; everything else passes through `json.dumps`
    with `default=str` as a fallback for non-serializable types
    (datetime, Path, etc.).
    """
    if hasattr(payload, "to_dict") and callable(payload.to_dict):
        return json.dumps(payload.to_dict(), default=str, separators=(",", ":"))
    if dataclasses.is_dataclass(payload):
        return json.dumps(dataclasses.asdict(payload), default=str, separators=(",", ":"))
    return json.dumps(payload, default=str, separators=(",", ":"))
