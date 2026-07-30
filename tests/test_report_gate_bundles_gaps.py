"""The report gate must report every unmet requirement at once.

Incident: an anniversary-campaign session ran 28 queries and built 7 charts,
called `generate_case_study_report`, and got back exactly one complaint —
"No dimensional breakdown". It abandoned the report and delivered markdown
files instead. No report artifact was produced.

Two things made that outcome likely:

1. The requirements are only discoverable at report time, once every chart
   already exists. Learning "add a series_field chart" after building seven
   without one is expensive.
2. The gate returned them ONE AT A TIME. That session actually had TWO unmet
   requirements (dimensional breakdown AND relational analysis), so fixing
   the reported one would have produced a second rejection.

The chart gate already bundles its gaps via `_format_chart_gate_reason`; this
holds the report gate to the same contract.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cerebro_mcp.tools.governance.session_state import SessionState


def _report_ready_state() -> SessionState:
    """A session that has cleared routing and is at the composition stage."""
    s = SessionState()
    s.semantic_preflight_ran = True
    s.semantic_mode_last = "report"
    s.analysis_path = "hybrid"
    s.execute_query_count = 28
    s.statistical_query_count = 1
    s.correlation_query_count = 0
    return s


def _flat_line_charts(n: int) -> dict:
    """n trend charts with no series_field, no scatter/heatmap — the exact
    shape the failing session produced by calling `generate_chart` n times."""
    return {
        f"chart_{i}": {
            "chart_type": "line",
            "series_field": None,
            "created_at": datetime.now(),
        }
        for i in range(1, n + 1)
    }


def test_all_unmet_requirements_are_reported_together():
    state = _report_ready_state()
    passed, reason, _warnings = state.check_report_preconditions(
        _flat_line_charts(7)
    )

    assert passed is False
    # The incident session was told only about the breakdown. Both must appear.
    assert "dimensional breakdown" in reason
    assert "relational analysis" in reason, (
        "the second unmet requirement was withheld, so fixing the first would "
        "have produced another rejection"
    )


def test_bundled_message_names_the_next_action():
    """The rejection lands where the caller is most likely to give up, so it
    has to say what to do rather than only what is wrong."""
    state = _report_ready_state()
    _passed, reason, _warnings = state.check_report_preconditions(
        _flat_line_charts(7)
    )
    lowered = reason.lower()
    assert "generate_charts" in lowered
    assert "retry" in lowered
    # The observed failure mode, named explicitly.
    assert "markdown" in lowered


def test_a_single_gap_is_still_returned_verbatim():
    """One gap keeps its original one-line wording, so existing substring
    assertions and downstream classification keep working."""
    state = _report_ready_state()
    state.correlation_query_count = 1  # satisfies the relational requirement
    charts = _flat_line_charts(7)

    passed, reason, _warnings = state.check_report_preconditions(charts)
    assert passed is False
    assert reason.startswith("No dimensional breakdown")
    assert "\n- " not in reason, "single gap must not be bulleted"


def test_gate_passes_once_the_gaps_are_filled():
    state = _report_ready_state()
    state.correlation_query_count = 1
    charts = _flat_line_charts(6)
    charts["chart_7"] = {
        "chart_type": "bar",
        "series_field": "token",
        "created_at": datetime.now(),
    }

    passed, reason, _warnings = state.check_report_preconditions(charts)
    assert passed is True, f"gate still blocking: {reason}"


@pytest.mark.parametrize("n_charts", [0, 1, 2])
def test_chart_count_gap_bundles_with_the_others(n_charts):
    """Too few charts is not a special case — it joins the same list."""
    state = _report_ready_state()
    passed, reason, _warnings = state.check_report_preconditions(
        _flat_line_charts(n_charts)
    )
    assert passed is False
    assert "Insufficient charts" in reason
    # With too few charts, the composition requirements are unmet too, and the
    # caller should hear about them now rather than after adding charts.
    assert "dimensional breakdown" in reason
