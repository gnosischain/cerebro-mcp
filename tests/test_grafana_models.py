"""Schema validation tests for the Grafana dashboard publisher."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from cerebro_mcp.grafana.models import (
    GrafanaDashboardDef,
    GrafanaPanelDef,
    GrafanaVariableDef,
)


def _panel(**kw):
    base = dict(
        title="P",
        role="kpi",
        data_shape="single_value",
        sql_query="SELECT count() FROM t",
    )
    base.update(kw)
    return GrafanaPanelDef(**base)


def _dashboard(**kw):
    base = dict(uid="growth_x_daily", title="T", panels=[_panel()])
    base.update(kw)
    return GrafanaDashboardDef(**base)


# --- uid regex -----------------------------------------------------------

@pytest.mark.parametrize("uid", ["a", "growth_x_daily", "A-B_2", "x" * 40])
def test_uid_valid(uid):
    assert _dashboard(uid=uid).uid == uid


@pytest.mark.parametrize("uid", ["", "x" * 41, "has space", "bad/slash", "emoji😀"])
def test_uid_invalid(uid):
    with pytest.raises(ValidationError):
        _dashboard(uid=uid)


# --- unit allowlist ------------------------------------------------------

def test_unit_allowlist_rejects_unknown():
    with pytest.raises(ValidationError):
        _panel(unit="dollars")


def test_unit_allowlist_accepts_known():
    assert _panel(unit="currencyUSD").unit == "currencyUSD"


# --- stacking ------------------------------------------------------------
# Only the stacked-series viz families draw it; an explicit value anywhere
# else would be silently ignored, so the model rejects it at parse time.

def test_explicit_stacking_rejected_on_non_stackable_viz():
    with pytest.raises(ValidationError, match="stacking"):
        _panel(viz="stat", stacking="none")


def test_stacking_auto_valid_everywhere():
    assert _panel(viz="stat", stacking="auto").stacking == "auto"


def test_explicit_stacking_accepted_on_stackable_viz():
    p = _panel(
        title="Tiers", role="breakdown", viz="barchart_vertical",
        data_shape="category_value_multi", stacking="none",
        sql_query="SELECT p AS category, t AS series, v AS value FROM c",
    )
    assert p.stacking == "none"


def test_unknown_stacking_value_rejected():
    with pytest.raises(ValidationError):
        _panel(
            title="Tiers", role="breakdown", viz="barchart_vertical",
            data_shape="category_value_multi", stacking="grouped",
            sql_query="SELECT p AS category, t AS series, v AS value FROM c",
        )


# --- uniqueness ----------------------------------------------------------

def test_duplicate_panel_titles_rejected():
    with pytest.raises(ValidationError):
        _dashboard(panels=[_panel(title="Same"), _panel(title="same")])


def test_duplicate_variable_names_rejected():
    v1 = GrafanaVariableDef(name="chain", type="custom", options="a,b", default="a")
    v2 = GrafanaVariableDef(name="chain", type="custom", options="c,d", default="c")
    with pytest.raises(ValidationError):
        _dashboard(variables=[v1, v2])


@pytest.mark.parametrize("name", ["1bad", "has space", "x" * 32, "with-dash"])
def test_variable_name_regex(name):
    with pytest.raises(ValidationError):
        GrafanaVariableDef(name=name, type="custom", options="a", default="a")


# --- role x viz x shape rejection matrix --------------------------------

@pytest.mark.parametrize(
    "role,viz,shape,ok",
    [
        ("kpi", "stat", "single_value", True),
        ("kpi", "gauge", "single_value_bounded", True),
        ("kpi", "timeseries_line", "time_series_single", False),   # viz not in role
        ("trend", "timeseries_line", "time_series_single", True),
        ("trend", "stat", "single_value", False),                  # viz not in role
        ("breakdown", "piechart", "share_of_total", True),
        ("breakdown", "piechart", "time_series_multi", False),     # shape not accepted
        ("detail", "table", "tabular", True),
        ("detail", "barchart_vertical", "category_value", False),  # viz not in role
        ("trend", "timeseries_line", "single_value", False),       # shape not accepted
    ],
)
def test_role_viz_shape_matrix(role, viz, shape, ok):
    kw = dict(role=role, viz=viz, data_shape=shape)
    # gauge / state panels need extra fields to pass other validators
    if viz == "gauge":
        kw["unit"] = "percent"
    if ok:
        assert _panel(**kw).effective_viz == viz
    else:
        with pytest.raises(ValidationError):
            _panel(**kw)


def test_default_viz_applied_per_role():
    assert _panel(role="kpi", data_shape="single_value").effective_viz == "stat"
    assert _panel(role="trend", data_shape="time_series_single").effective_viz == "timeseries_line"
    assert _panel(role="breakdown", data_shape="category_value").effective_viz == "barchart_vertical"
    assert _panel(role="detail", data_shape="tabular").effective_viz == "table"


# --- gauge bounds + state mappings --------------------------------------

def test_gauge_without_bounds_or_bounded_unit_rejected():
    with pytest.raises(ValidationError):
        _panel(role="kpi", viz="gauge", data_shape="single_value_bounded", unit="short")


def test_gauge_with_explicit_bounds_ok():
    p = _panel(role="kpi", viz="gauge", data_shape="single_value_bounded",
               unit="short", min=0, max=200)
    assert p.effective_viz == "gauge"


def test_gauge_with_percent_unit_ok():
    p = _panel(role="kpi", viz="gauge", data_shape="single_value_bounded", unit="percent")
    assert p.effective_viz == "gauge"


def test_state_timeline_without_mappings_rejected():
    with pytest.raises(ValidationError):
        _panel(role="trend", viz="state_timeline", data_shape="category_state_over_time",
               sql_query="SELECT time, e, s FROM t")
