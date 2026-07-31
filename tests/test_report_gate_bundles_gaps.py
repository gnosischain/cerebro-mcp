"""A report request must always end in an artifact — unless it would be wrong.

Incident: an anniversary-campaign session ran 28 queries and built 7 charts,
called `generate_case_study_report`, and got back one complaint — "No
dimensional breakdown". It abandoned the report and delivered markdown files.
Nothing appeared in `list_reports`. Reproducing that session's state showed it
actually had TWO unmet requirements, so complying once would have been refused
again.

The contract now splits by what an unmet requirement MEANS:

- `composition` — the report is thin. Does not block; disclosed in a "Known
  limitations" section in the artifact.
- `correctness` — the numbers are wrong. Still blocks, because no disclosure
  substitutes for a false figure.
- `advisory` — a nudge. Never blocks, never disclaims.

Everything is evaluated before anything is reported, so a caller never fixes one
requirement only to discover the next.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cerebro_mcp.tools.governance.session_state import (
    SEVERITY_ADVISORY,
    SEVERITY_COMPOSITION,
    SEVERITY_CORRECTNESS,
    SessionState,
    render_report_contract,
    report_requirements_for_tier,
    split_limitations,
)

#: A stock measure summed across a date range, with no GROUP BY date and no
#: argMax — the canonical wrong-numbers case.
STOCK_FLOW_VIOLATION = (
    "SELECT sum(tvl_usd) FROM pools "
    "WHERE date BETWEEN '2026-01-01' AND '2026-07-01'"
)


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
    """n trend charts with no series_field and no scatter — the exact shape the
    failing session produced by calling `generate_chart` n times."""
    return {
        f"chart_{i}": {
            "chart_type": "line",
            "series_field": None,
            "created_at": datetime.now(),
            "sql": "SELECT 1",
        }
        for i in range(1, n + 1)
    }


def _sound_charts() -> dict:
    """Charts that satisfy every composition requirement."""
    return {
        "chart_1": {"chart_type": "line", "series_field": None,
                    "created_at": datetime.now(), "sql": "SELECT 1"},
        "chart_2": {"chart_type": "bar", "series_field": "token",
                    "created_at": datetime.now(), "sql": "SELECT 1"},
        "chart_3": {"chart_type": "scatter", "series_field": None,
                    "created_at": datetime.now(), "sql": "SELECT 1"},
    }


# --- the incident ---------------------------------------------------------


def test_the_anniversary_session_now_produces_a_report():
    """The exact state that was refused must now pass, with disclosure."""
    state = _report_ready_state()
    passed, reason, warnings = state.check_report_preconditions(
        _flat_line_charts(7)
    )

    assert passed is True, f"still refusing a thin-but-sound report: {reason}"
    limitations, _ = split_limitations(warnings)
    # Both gaps, not just the first — it had two, and only ever heard about one.
    assert any("dimensional breakdown" in item for item in limitations)
    assert any("relational analysis" in item for item in limitations)


def test_composition_gaps_are_disclosed_not_hidden():
    """Not blocking must not mean not mentioning. The reader is the one who
    needs to know the analysis is narrow."""
    state = _report_ready_state()
    _passed, _reason, warnings = state.check_report_preconditions(
        _flat_line_charts(1)
    )
    limitations, _ = split_limitations(warnings)
    assert any("Insufficient charts" in item for item in limitations)


# --- correctness still refuses --------------------------------------------


def test_a_wrong_number_still_blocks():
    charts = _sound_charts()
    charts["chart_1"] = dict(
        charts["chart_1"], sql=STOCK_FLOW_VIOLATION, title="Total TVL"
    )
    state = _report_ready_state()
    state.correlation_query_count = 1

    passed, reason, _warnings = state.check_report_preconditions(charts)
    assert passed is False, "summing a stock measure over a range must refuse"
    assert "override_reason" in reason, "the refusal must name its escape"


def test_the_documented_escape_actually_works():
    """`aggregates_stock_measure_over_time` took no chart_metadata at all, so
    the override the gate advertised did nothing for the rule most likely to
    fire on treasury work."""
    charts = _sound_charts()
    charts["chart_1"] = dict(
        charts["chart_1"],
        sql=STOCK_FLOW_VIOLATION,
        title="Total TVL (point-in-time snapshot, deliberate)",
    )
    state = _report_ready_state()
    state.correlation_query_count = 1

    passed, reason, _warnings = state.check_report_preconditions(charts)
    assert passed is True, f"acknowledged exception still refused: {reason}"


def test_a_bare_mention_does_not_excuse_a_violation():
    """The escape must require an acknowledgement, not a keyword. If naming the
    measure exonerated, the rule would exonerate every chart it applies to."""
    charts = _sound_charts()
    charts["chart_1"] = dict(
        charts["chart_1"], sql=STOCK_FLOW_VIOLATION, title="TVL over time"
    )
    state = _report_ready_state()
    state.correlation_query_count = 1

    passed, _reason, _warnings = state.check_report_preconditions(charts)
    assert passed is False


def test_a_legitimate_per_day_sum_is_not_flagged():
    """Guards the false-positive direction: a per-date TVL sum is correct."""
    charts = _sound_charts()
    charts["chart_1"] = dict(
        charts["chart_1"],
        sql="SELECT date, sum(tvl_usd) FROM pools GROUP BY date",
        title="TVL",
    )
    state = _report_ready_state()
    state.correlation_query_count = 1

    passed, reason, _warnings = state.check_report_preconditions(charts)
    assert passed is True, f"false positive on a legitimate query: {reason}"


# --- the contract itself --------------------------------------------------


def test_contract_covers_every_enforced_requirement():
    ids = {r.id for r in report_requirements_for_tier("report")}
    for expected in (
        "min_charts", "chart_diversity", "exploratory_queries",
        "dimensional_breakdown", "relational_analysis",
        "discovered_model_coverage", "stock_flow_discipline",
        "residual_bucket_disclosure", "stationarity_on_correlations",
        "aggregator_volume_dedup",
    ):
        assert expected in ids, f"{expected} enforced but absent from contract"


def test_severities_are_assigned_correctly():
    by_id = {r.id: r.severity for r in report_requirements_for_tier("report")}
    # Wrong numbers.
    for rid in ("stock_flow_discipline", "residual_bucket_disclosure",
                "stationarity_on_correlations", "aggregator_volume_dedup"):
        assert by_id[rid] == SEVERITY_CORRECTNESS
    # Thin, not wrong.
    for rid in ("min_charts", "dimensional_breakdown", "relational_analysis",
                "discovered_model_coverage"):
        assert by_id[rid] == SEVERITY_COMPOSITION
    # Advisory in code all along, while three documents called them rejects.
    for rid in ("statistical_query", "correlation_query"):
        assert by_id[rid] == SEVERITY_ADVISORY


def test_advisory_requirements_never_block_or_disclaim():
    state = _report_ready_state()
    state.statistical_query_count = 0
    state.correlation_query_count = 1  # satisfies the relational requirement

    passed, _reason, warnings = state.check_report_preconditions(_sound_charts())
    limitations, _ = split_limitations(warnings)
    assert passed is True
    assert not any("statistical" in item.lower() for item in limitations)


def test_rendered_contract_states_what_blocks():
    """A caller that thinks every requirement is fatal behaves the same as one
    that thinks none are."""
    text = render_report_contract("report")
    assert "BLOCK" in text
    assert "Known limitations" in text
    assert "series_field" in text
    # The scoped-count trap, which was documented nowhere.
    assert "REFERENCED" in text
    # The coverage escape hatch, also documented nowhere the model reads.
    assert "exclude_module" in text


@pytest.mark.parametrize("mode", ["chart", "answer"])
def test_non_report_tiers_carry_no_composition_requirements(mode):
    severities = {r.severity for r in report_requirements_for_tier(mode)}
    assert SEVERITY_COMPOSITION not in severities


def test_a_fully_sound_report_has_nothing_to_disclose():
    state = _report_ready_state()
    state.correlation_query_count = 1
    passed, _reason, warnings = state.check_report_preconditions(_sound_charts())
    limitations, _ = split_limitations(warnings)
    assert passed is True
    assert limitations == []
