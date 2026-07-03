"""Tests for `tools/tool_meta.py` classification + coverage lint."""

from cerebro_mcp.tools.tool_meta import (
    CORE_TOOL_NAMES,
    TOOL_META,
    classify_tool,
    core_tool_names,
    is_core_tool,
    lint_tool_meta_coverage,
)


def test_classify_explicit_meta_wins():
    meta = classify_tool("query_metrics")
    assert meta["domain"] == "semantic"
    assert meta["tier"] == "core"
    assert "metric" in meta["tags"]


def test_classify_unknown_tool_infers_and_defaults_advanced():
    meta = classify_tool("get_validator_balance_history", "Return validator balance history over time.")
    assert meta["tier"] == "advanced"
    # domain inferred (not blank), tags fall back to the docstring first line
    assert meta["domain"]
    assert meta["tags"]


def test_classify_never_raises_on_empty():
    meta = classify_tool("totally_unknown_tool_xyz")
    assert meta["domain"]
    assert meta["tier"] == "advanced"


def test_curated_static_tools_are_covered():
    """Every hand-curated TOOL_META tool passes the coverage lint (it either has
    a meta entry — which it does by construction — so nothing is flagged)."""
    missing = lint_tool_meta_coverage(set(TOOL_META.keys()))
    assert missing == []


def test_lint_flags_unknown_static_tool():
    missing = lint_tool_meta_coverage({"a_brand_new_static_tool_with_no_meta"})
    assert missing == ["a_brand_new_static_tool_with_no_meta"]


def test_lint_exempts_dynamic_tools():
    """Dynamic/custom tools are auto-classified → never flagged even without a
    meta or risk-registry entry."""
    name = "a_dynamic_custom_tool"
    missing = lint_tool_meta_coverage({name}, dynamic_tool_names={name})
    assert missing == []


def test_lint_covers_risk_registry_tools():
    """A static tool absent from TOOL_META but present in TOOL_RISK_REGISTRY is
    covered (risk registry is enough to infer a domain)."""
    # `describe_table` is in the risk registry; even if it were dropped from
    # TOOL_META it would still be covered. Use a known risk-registry-only tool.
    missing = lint_tool_meta_coverage({"list_saved_queries"})
    assert missing == []


def test_core_tool_names_match_meta_tiers():
    """CORE_TOOL_NAMES is the authoritative lean-core set: it must equal the set
    of TOOL_META entries tagged tier="core" (no drift between the two)."""
    meta_core = {n for n, m in TOOL_META.items() if m.get("tier") == "core"}
    assert set(CORE_TOOL_NAMES) == meta_core
    assert core_tool_names() == CORE_TOOL_NAMES


def test_is_core_tool_matches_classify():
    assert is_core_tool("query_metrics") is True
    assert is_core_tool("contract_explore") is False
    assert is_core_tool("totally_unknown_tool_xyz") is False


def test_expected_core_tools_present():
    """The plan's 17 answer/chart/report core tools are all classified core."""
    expected = {
        "find",
        "query_metrics",
        "execute_query",
        "describe_table",
        "get_model_details",
        "get_metric_details",
        "explain_metric_query",
        "preflight_analytics_request",
        "quick_chart",
        "quick_metric_chart",
        "generate_chart",
        "generate_charts",
        "generate_metric_charts",
        "generate_report",
        "get_help",
        "system_status",
        "verify_numbers",
    }
    assert expected <= set(CORE_TOOL_NAMES)
    for name in expected:
        assert classify_tool(name)["tier"] == "core"
