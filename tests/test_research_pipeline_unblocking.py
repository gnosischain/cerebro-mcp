"""A research project must always have a way forward.

Three traps made "study" requests structurally unable to succeed, none of which
a caller could fix by doing better research:

1. `statistical_depth` inspected only `kind == "query_result"`, so evidence from
   `query_metrics` (`semantic_query_result`) could never satisfy it. A
   purely-semantic project was permanently stuck, and the failure message named
   the one thing it had actually done.
2. `artifact_integrity` re-resolves every chart against a registry with a 2h TTL
   that dies with the process, so a long-running project failed verification for
   aging rather than for anything about its evidence.
3. `plan_research_phase` required status exactly `pending`, so once
   `verify_research_phase` set a phase to `failed` it could never be re-planned —
   the state that most needs a new plan was the one that refused one.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.models.research import ResearchProjectState
from cerebro_mcp.research.workflow import ensure_phase_status


class _Phase:
    def __init__(self, status: str) -> None:
        self.status = status


class _Project:
    """Minimal stand-in — ensure_phase_status only touches .phases[x].status."""

    def __init__(self, status: str) -> None:
        self.phases = {"verification": _Phase(status)}


def test_a_failed_phase_can_be_replanned():
    ensure_phase_status(_Project("failed"), "verification", "pending",
                        allow_failed=True)


def test_a_failed_phase_still_refuses_where_failure_is_not_expected():
    """`allow_failed` is opt-in: execute_research_phase must not silently accept
    a failed phase just because plan does."""
    with pytest.raises(ValueError):
        ensure_phase_status(_Project("failed"), "verification", "planned")


def test_an_unrelated_status_still_refuses():
    with pytest.raises(ValueError) as exc:
        ensure_phase_status(_Project("completed"), "verification", "pending",
                            allow_failed=True)
    assert "completed" in str(exc.value)


def test_semantic_query_evidence_counts_toward_statistical_depth():
    """The check must accept both query-result kinds.

    Asserted against the source rather than a live project because the
    alternative is standing up a full research project with a persisted
    semantic artifact; the defect was a single missing kind in a membership
    test, and that is what this pins.
    """
    import inspect

    from cerebro_mcp.tools.research import research

    src = inspect.getsource(research)
    idx = src.index("statistical_found = False")
    window = src[idx:idx + 900]
    assert "semantic_query_result" in window, (
        "statistical_depth ignores semantic_query_result evidence, so a "
        "project whose evidence came from query_metrics can never pass"
    )


def test_chart_evidence_falls_back_to_the_durable_record():
    """A chart aged out of the 2h in-memory registry must not fail integrity if
    a durable copy exists."""
    import inspect

    from cerebro_mcp.tools.research import research

    src = inspect.getsource(research._validate_evidence_ref)
    assert "restore_chart_registry" in src, (
        "chart evidence resolves only against the TTL'd in-memory registry, so "
        "a long-running project fails verification purely from aging"
    )


def test_published_reports_do_not_inherit_ambient_presentation_mode():
    """Session state is a process-global singleton shared by every concurrent
    client, so omitting presentation_mode lets another conversation's chart-mode
    request file a published research report as a throwaway visual answer."""
    import inspect

    from cerebro_mcp.tools.research import research

    src = inspect.getsource(research)
    idx = src.index("enforce_quality_gate=False")
    assert 'presentation_mode="research"' in src[idx - 400:idx + 700]


def test_storyteller_reports_do_not_inherit_ambient_presentation_mode():
    import inspect

    from cerebro_mcp.tools.storyteller import storyteller

    src = inspect.getsource(storyteller)
    assert 'extra_kwargs: dict[str, Any] = {"presentation_mode": "report"}' in src
