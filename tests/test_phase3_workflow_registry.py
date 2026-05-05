"""Tests for the WorkflowRegistry + research resume handler.

Two layers:

1. Registry mechanics: register/dispatch/idempotency, no-handler fallback,
   exception isolation, status flips on terminal outcomes.
2. Research resume handler: state machine over event streams (terminal,
   ready_to_resume, failed paths), hint payload shape, unfinished LLM
   call surfacing.

All async — uses `pytest-asyncio` like the existing Phase 3 tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from cerebro_mcp import event_store_sync as ev
from cerebro_mcp import config as cerebro_config
from cerebro_mcp.event_store import EventStore
from cerebro_mcp.research_resume import (
    install_research_resume_handler,
    resume_research_project,
)
from cerebro_mcp.workflow_payloads import (
    EVENT_LLM_CALL_STARTED,
    LLMCallEvent,
    LLMTurn,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_ORPHANED,
    WORKFLOW_RUNNING,
)
from cerebro_mcp.workflow_registry import (
    ACTION_COMPLETE,
    ACTION_FAILED,
    ACTION_NO_HANDLER,
    ACTION_ORPHAN,
    ACTION_READY_TO_RESUME,
    ResumeOutcome,
    WorkflowRegistry,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> EventStore:
    s = EventStore(db_path=tmp_path / "registry_test.db",
                   compression_threshold=2_000_000)
    await s.init()
    return s


@pytest_asyncio.fixture
async def registry(store: EventStore) -> WorkflowRegistry:
    return WorkflowRegistry(event_store=store)


# ---------------------------------------------------------------------------
# Registry mechanics
# ---------------------------------------------------------------------------


class TestRegistryMechanics:
    async def test_register_and_dispatch(self, registry, store):
        await store.create_workflow("wid_1", "demo_kind")

        async def handler(workflow_id, wf, events):
            return ResumeOutcome(
                workflow_id=workflow_id, kind="demo_kind",
                action=ACTION_READY_TO_RESUME,
                summary="ok",
                resume_hint={"phase": "first"},
            )

        registry.register("demo_kind", handler)
        outcome = await registry.resume("wid_1")
        assert outcome.action == ACTION_READY_TO_RESUME
        assert outcome.resume_hint == {"phase": "first"}

    async def test_unknown_workflow_returns_orphan(self, registry):
        outcome = await registry.resume("nonexistent")
        assert outcome.action == ACTION_ORPHAN

    async def test_no_handler_returns_no_handler(self, registry, store):
        await store.create_workflow("wid_nh", "unregistered_kind")
        outcome = await registry.resume("wid_nh")
        assert outcome.action == ACTION_NO_HANDLER

    async def test_handler_exception_caught(self, registry, store):
        await store.create_workflow("wid_boom", "buggy")

        async def buggy(*a, **kw):
            raise ValueError("intentional")

        registry.register("buggy", buggy)
        outcome = await registry.resume("wid_boom")
        assert outcome.action == ACTION_FAILED
        assert "ValueError" in outcome.summary

    async def test_handler_wrong_return_type_caught(self, registry, store):
        await store.create_workflow("wid_bad_return", "kind_x")

        async def bad_return(*a, **kw):
            return {"action": "ready_to_resume"}  # plain dict, not ResumeOutcome

        registry.register("kind_x", bad_return)
        outcome = await registry.resume("wid_bad_return")
        assert outcome.action == ACTION_FAILED
        assert "TypeError" in outcome.summary or "ResumeOutcome" in outcome.summary

    async def test_register_idempotent_same_fn(self, registry):
        async def fn(*a, **kw):
            return ResumeOutcome("x", "k", ACTION_READY_TO_RESUME)
        registry.register("k", fn)
        registry.register("k", fn)
        assert registry.has_handler("k")

    async def test_register_replaces_different_fn(self, registry):
        async def fn1(*a, **kw):
            return ResumeOutcome("x", "k", ACTION_READY_TO_RESUME, "v1")
        async def fn2(*a, **kw):
            return ResumeOutcome("x", "k", ACTION_READY_TO_RESUME, "v2")
        registry.register("k", fn1)
        registry.register("k", fn2)
        # fn2 should be the active handler now.
        assert registry._handlers["k"] is fn2

    async def test_resume_records_hint_event(self, registry, store):
        await store.create_workflow("wid_hint", "kind_h")

        async def fn(*a, **kw):
            return ResumeOutcome("wid_hint", "kind_h",
                                 ACTION_READY_TO_RESUME, "summary",
                                 resume_hint={"x": 1})

        registry.register("kind_h", fn)
        await registry.resume("wid_hint")
        events = await store.replay("wid_hint")
        assert any(e["kind"] == "workflow_resume_hint" for e in events)

    async def test_complete_outcome_marks_workflow_completed(
        self, registry, store,
    ):
        await store.create_workflow("wid_done", "kind_d")

        async def fn(*a, **kw):
            return ResumeOutcome("wid_done", "kind_d", ACTION_COMPLETE,
                                 "all done")

        registry.register("kind_d", fn)
        await registry.resume("wid_done")
        wf = await store.get_workflow("wid_done")
        assert wf["status"] == WORKFLOW_COMPLETED

    async def test_failed_outcome_marks_workflow_failed(self, registry, store):
        await store.create_workflow("wid_f", "kind_f")

        async def fn(*a, **kw):
            return ResumeOutcome("wid_f", "kind_f", ACTION_FAILED,
                                 "rejected")

        registry.register("kind_f", fn)
        await registry.resume("wid_f")
        wf = await store.get_workflow("wid_f")
        assert wf["status"] == WORKFLOW_FAILED

    async def test_ready_to_resume_keeps_running_status(self, registry, store):
        await store.create_workflow("wid_r", "kind_r")

        async def fn(*a, **kw):
            return ResumeOutcome("wid_r", "kind_r",
                                 ACTION_READY_TO_RESUME, "in flight")

        registry.register("kind_r", fn)
        await registry.resume("wid_r")
        wf = await store.get_workflow("wid_r")
        assert wf["status"] == WORKFLOW_RUNNING

    async def test_no_handler_marks_orphaned(self, registry, store):
        await store.create_workflow("wid_x", "totally_unknown")
        await registry.resume("wid_x")
        wf = await store.get_workflow("wid_x")
        assert wf["status"] == WORKFLOW_ORPHANED

    async def test_resume_all_running(self, registry, store):
        # Three workflows: 1 with handler, 1 without, 1 already completed.
        await store.create_workflow("a", "k_a")
        await store.create_workflow("b", "k_b")
        await store.create_workflow("c", "k_a")
        from cerebro_mcp.workflow_payloads import WORKFLOW_COMPLETED as DONE
        await store.mark_workflow_status("c", DONE)

        async def k_a_fn(workflow_id, wf, events):
            # Echo the real workflow_id back, like a real handler.
            return ResumeOutcome(workflow_id, "k_a",
                                 ACTION_READY_TO_RESUME, "")

        registry.register("k_a", k_a_fn)
        outcomes = await registry.resume_all_running()
        ids = {o.workflow_id for o in outcomes}
        # `c` is completed, should NOT be picked up.
        assert ids == {"a", "b"}


# ---------------------------------------------------------------------------
# research_project resume handler
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def research_workflow(store, tmp_path, monkeypatch):
    """A research workflow with the canonical metadata shape."""
    monkeypatch.setattr(
        cerebro_config.settings, "EVENT_STORE_PATH",
        str(tmp_path / "registry_test.db"),
        raising=True,
    )
    workflow_id = "research_proj_xyz"
    await store.create_workflow(
        workflow_id, "research_project",
        metadata={"project_id": "proj_xyz", "hypothesis": "h", "scope": "s"},
    )
    return workflow_id


class TestResearchResumeHandler:
    async def test_fresh_project_resumes_at_mapping(self, store, research_workflow):
        await store.append_event(research_workflow, "workflow_started",
                                 {"project_id": "proj_xyz"})
        wf = await store.get_workflow(research_workflow)
        events = await store.replay(research_workflow)
        out = await resume_research_project(research_workflow, wf, events)
        assert out.action == ACTION_READY_TO_RESUME
        assert out.resume_hint["current_phase"] == "mapping"
        assert out.resume_hint["next_action"] == "plan_research_phase"

    async def test_after_mapping_completed_resumes_at_hypothesis(
        self, store, research_workflow,
    ):
        await store.append_event(research_workflow, "workflow_started",
                                 {"project_id": "proj_xyz"})
        await store.append_event(research_workflow, "phase_planned",
                                 {"phase": "mapping"})
        await store.append_event(research_workflow, "phase_completed",
                                 {"phase": "mapping",
                                  "advanced_to": "hypothesis"})
        wf = await store.get_workflow(research_workflow)
        events = await store.replay(research_workflow)
        out = await resume_research_project(research_workflow, wf, events)
        assert out.action == ACTION_READY_TO_RESUME
        assert out.resume_hint["current_phase"] == "hypothesis"
        assert "mapping" in out.resume_hint["completed_phases"]

    async def test_planned_but_not_completed_stays_on_planned_phase(
        self, store, research_workflow,
    ):
        await store.append_event(research_workflow, "workflow_started",
                                 {"project_id": "proj_xyz"})
        await store.append_event(research_workflow, "phase_planned",
                                 {"phase": "execution"})
        # NB: no phase_completed for execution.
        wf = await store.get_workflow(research_workflow)
        events = await store.replay(research_workflow)
        out = await resume_research_project(research_workflow, wf, events)
        assert out.resume_hint["current_phase"] == "execution"

    async def test_published_returns_complete(self, store, research_workflow):
        await store.append_event(research_workflow, "workflow_started",
                                 {"project_id": "proj_xyz"})
        await store.append_event(research_workflow, "report_published",
                                 {"report_id": "r_1", "title": "T"})
        wf = await store.get_workflow(research_workflow)
        events = await store.replay(research_workflow)
        out = await resume_research_project(research_workflow, wf, events)
        assert out.action == ACTION_COMPLETE

    async def test_peer_review_rejected_returns_failed(
        self, store, research_workflow,
    ):
        await store.append_event(research_workflow, "workflow_started",
                                 {"project_id": "proj_xyz"})
        await store.append_event(research_workflow, "peer_review_recorded",
                                 {"status": "rejected"})
        wf = await store.get_workflow(research_workflow)
        events = await store.replay(research_workflow)
        out = await resume_research_project(research_workflow, wf, events)
        assert out.action == ACTION_FAILED
        assert "rejected" in out.summary

    async def test_unfinished_llm_call_surfaced(
        self, store, research_workflow,
    ):
        await store.append_event(research_workflow, "workflow_started",
                                 {"project_id": "proj_xyz"})
        call = LLMCallEvent(
            subtask_name="defi_analyst", call_id="call_001",
            system_prompt="…",
            messages=[LLMTurn(role="user",
                              content=[{"type": "text", "text": "TVL?"}])],
            tool_schemas=[],
        )
        await store.append_event(research_workflow, EVENT_LLM_CALL_STARTED, call)
        # No completion → unfinished.
        wf = await store.get_workflow(research_workflow)
        events = await store.replay(research_workflow)
        out = await resume_research_project(research_workflow, wf, events)
        assert len(out.unfinished_llm_calls) == 1
        assert out.unfinished_llm_calls[0].subtask_name == "defi_analyst"

    async def test_verification_failed_surfaced_in_hint(
        self, store, research_workflow,
    ):
        await store.append_event(research_workflow, "workflow_started",
                                 {"project_id": "proj_xyz"})
        await store.append_event(research_workflow, "verification_completed",
                                 {"phase": "verification", "passed": False})
        wf = await store.get_workflow(research_workflow)
        events = await store.replay(research_workflow)
        out = await resume_research_project(research_workflow, wf, events)
        # Verification failure alone doesn't mark workflow failed —
        # peer-review failure does. Verification can be re-run.
        assert out.action == ACTION_READY_TO_RESUME
        assert out.resume_hint["verification_gate"] == "failed"


# ---------------------------------------------------------------------------
# install_research_resume_handler() registers on the default registry
# ---------------------------------------------------------------------------


class TestMCPToolHandlersAreAsync:
    """Regression: the workflow-resume MCP tools must be `async def` so
    FastMCP can await them. The earlier `def + asyncio.run(...)` shape
    crashed with `RuntimeError: asyncio.run() cannot be called from a
    running event loop` because FastMCP serves tool calls inside its own
    asyncio loop. Live failure observed 2026-04-27 via the user's MCP
    client.
    """

    async def test_list_default_does_not_filter_by_age(self):
        """Regression: the agent-facing list MUST default to no age
        filter. Earlier default of 86400 hid every workflow the
        bootstrap sweep just touched (because writing the hint bumps
        `updated_at`), making the boot sweep useless to agents.
        Live regression observed 2026-04-27.
        """
        import inspect
        from cerebro_mcp.tools.workflow_resume import register_workflow_resume_tools

        captured = {}

        class FakeMCP:
            def tool(self, *a, **kw):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn
                return deco

        register_workflow_resume_tools(FakeMCP())
        sig = inspect.signature(captured["list_resumable_workflows"])
        # Whatever the parameter is named, its default MUST be 0 / None
        # so unfiltered listing is the default.
        for p in sig.parameters.values():
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          inspect.Parameter.KEYWORD_ONLY):
                assert p.default in (0, None), (
                    f"list_resumable_workflows({p.name}={p.default!r}): "
                    "default must be 0 or None so the boot sweep's "
                    "freshly-touched workflows are visible. See "
                    "2026-04-27 live regression."
                )

    async def test_register_uses_async_handlers(self):
        import inspect
        from cerebro_mcp.tools.workflow_resume import register_workflow_resume_tools

        captured: dict[str, object] = {}

        class FakeMCP:
            def tool(self, *a, **kw):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn
                return deco

        register_workflow_resume_tools(FakeMCP())
        for name in (
            "list_resumable_workflows",
            "get_workflow_resume_hint",
            "recompute_workflow_resume_hint",
        ):
            fn = captured.get(name)
            assert fn is not None, f"{name} not registered"
            assert inspect.iscoroutinefunction(fn), (
                f"{name} must be `async def` so FastMCP can await it; "
                f"got {fn} which is not a coroutine function. "
                "Ref: 2026-04-27 RuntimeError live regression."
            )


class TestRegistration:
    async def test_install_research_handler_idempotent(self):
        from cerebro_mcp.workflow_registry import (
            default_workflow_registry,
            reset_default_workflow_registry,
        )
        reset_default_workflow_registry()
        install_research_resume_handler()
        install_research_resume_handler()  # twice — must not raise
        registry = default_workflow_registry()
        assert registry.has_handler("research_project")
        # Cleanup so other tests aren't poisoned.
        reset_default_workflow_registry()
