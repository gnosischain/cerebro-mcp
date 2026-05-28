"""Tool-layer tests for the Grafana dashboard publisher (mocked HTTP)."""
from __future__ import annotations

import pytest
import requests

import cerebro_mcp.tools.visualization.grafana as g
from cerebro_mcp.config import settings
from cerebro_mcp.grafana.models import GrafanaDashboardDef


class FakeMCP:
    """Captures @mcp.tool()-decorated functions by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


def _verify_ok(n_panels: int) -> dict:
    """A /api/ds/query response where every panel returns one row."""
    return {"results": {
        f"P{i}": {"frames": [{"data": {"values": [[1]]}}]} for i in range(n_panels)
    }}


def _routing_post(captured: dict, *, verify=None, db_json=None, db_status=200, db_text=""):
    """A requests.post replacement that routes by URL.

    /api/ds/query  -> verification response (defaults to 1-panel OK)
    /api/dashboards/db -> publish response (records url + payload in captured)
    """
    def fake_post(url, **kw):
        if url.endswith("/api/ds/query"):
            return FakeResp(200, verify if verify is not None else _verify_ok(1))
        captured["db_url"] = url
        captured["db_json"] = kw.get("json")
        if db_status >= 400:
            return FakeResp(db_status, text=db_text)
        return FakeResp(200, db_json if db_json is not None else {"url": "/d/x/t", "version": 1})
    return fake_post


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(settings, "GRAFANA_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "GRAFANA_URL", "https://grafana.example.com/")
    monkeypatch.setattr(settings, "GRAFANA_API_TOKEN", "glsa_secret_token")
    monkeypatch.setattr(settings, "GRAFANA_CLICKHOUSE_DATASOURCE_UID", "ch-uid")
    monkeypatch.setattr(settings, "GRAFANA_FOLDER_UID", "")
    mcp = FakeMCP()
    g.register_grafana_tools(mcp, None)
    return mcp.tools


def _spec(**kw):
    base = dict(
        uid="growth_x_daily",
        title="T",
        panels=[
            {"title": "Users", "role": "kpi", "data_shape": "single_value",
             "sql_query": "SELECT count() FROM u", "unit": "short"},
        ],
    )
    base.update(kw)
    return GrafanaDashboardDef(**base)


def test_not_registered_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "GRAFANA_TOOLS_ENABLED", False)
    mcp = FakeMCP()
    g.register_grafana_tools(mcp, None)
    assert mcp.tools == {}


def test_publish_success(monkeypatch, tools):
    captured = {}
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    monkeypatch.setattr(
        g.requests, "post",
        _routing_post(captured, db_json={"url": "/d/growth_x_daily/t", "version": 3}),
    )
    out = tools["publish_grafana_dashboard"](_spec())
    assert "version 3" in out
    assert "Verified 1 panel" in out
    assert captured["db_url"] == "https://grafana.example.com/api/dashboards/db"
    assert captured["db_json"]["overwrite"] is True
    assert "folderUid" not in captured["db_json"]
    assert "id" not in captured["db_json"]["dashboard"]


def test_publish_refuses_untagged_existing(monkeypatch, tools):
    monkeypatch.setattr(
        g.requests, "get",
        lambda *a, **k: FakeResp(200, {"dashboard": {"tags": ["hand-made"]}}),
    )
    monkeypatch.setattr(g.requests, "post", lambda *a, **k: pytest.fail("must not POST"))
    out = tools["publish_grafana_dashboard"](_spec())
    assert "not created by cerebro-mcp" in out


def test_publish_force_overwrite_bypasses_guard(monkeypatch, tools):
    captured = {}
    monkeypatch.setattr(
        g.requests, "get",
        lambda *a, **k: FakeResp(200, {"dashboard": {"tags": ["hand-made"]}}),
    )
    monkeypatch.setattr(g.requests, "post", _routing_post(captured, db_json={"url": "/d/x/t", "version": 9}))
    out = tools["publish_grafana_dashboard"](_spec(force_overwrite=True))
    assert "version 9" in out


def test_publish_blocks_on_panel_error(monkeypatch, tools):
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    verify = {"results": {"P0": {"error": "invalid format value: time_series"}}}

    def fake_post(url, **kw):
        if url.endswith("/api/ds/query"):
            return FakeResp(200, verify)
        pytest.fail("must not POST to /db when a panel errors")

    monkeypatch.setattr(g.requests, "post", fake_post)
    out = tools["publish_grafana_dashboard"](_spec())
    assert "not publishing" in out
    assert "FAIL 'Users'" in out
    assert "invalid format value" in out


def test_publish_blocks_on_empty_panel(monkeypatch, tools):
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    verify = {"results": {"P0": {"frames": [{"data": {"values": [[]]}}]}}}

    def fake_post(url, **kw):
        if url.endswith("/api/ds/query"):
            return FakeResp(200, verify)
        pytest.fail("must not POST to /db when a panel is empty")

    monkeypatch.setattr(g.requests, "post", fake_post)
    out = tools["publish_grafana_dashboard"](_spec())
    assert "not publishing" in out
    assert "EMPTY 'Users'" in out
    assert "allow_empty=true" in out


def test_publish_allow_empty_overrides(monkeypatch, tools):
    captured = {}
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    verify = {"results": {"P0": {"frames": [{"data": {"values": [[]]}}]}}}
    monkeypatch.setattr(g.requests, "post", _routing_post(captured, verify=verify))
    out = tools["publish_grafana_dashboard"](_spec(allow_empty=True))
    assert "Published" in out
    assert "1 empty (allowed)" in out


def test_http_error_body_scrubbed(monkeypatch, tools):
    captured = {}
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    leaky = "fail Authorization: Bearer glsa_secret_token " + "x" * 1000
    monkeypatch.setattr(g.requests, "post", _routing_post(captured, db_status=403, db_text=leaky))
    out = tools["publish_grafana_dashboard"](_spec())
    assert "Grafana returned 403" in out
    assert "glsa_secret_token" not in out
    assert "Bearer glsa" not in out
    assert len(out) < 600


def test_request_exception_returns_class_name_only(monkeypatch, tools):
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))

    def boom(*a, **k):
        raise requests.ConnectionError("connect to https://grafana.example.com failed; token=glsa_secret_token")

    monkeypatch.setattr(g.requests, "post", boom)
    out = tools["publish_grafana_dashboard"](_spec())
    assert out == "Grafana request failed: ConnectionError"
    assert "glsa_secret_token" not in out


def test_missing_config_returns_error(monkeypatch, tools):
    monkeypatch.setattr(settings, "GRAFANA_CLICKHOUSE_DATASOURCE_UID", "")
    out = tools["publish_grafana_dashboard"](_spec())
    assert "not fully configured" in out


def test_folder_uid_included_when_set(monkeypatch, tools):
    monkeypatch.setattr(settings, "GRAFANA_FOLDER_UID", "folder-42")
    captured = {}
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    monkeypatch.setattr(g.requests, "post", _routing_post(captured))
    tools["publish_grafana_dashboard"](_spec())
    assert captured["db_json"]["folderUid"] == "folder-42"


def test_url_no_double_slash(monkeypatch, tools):
    captured = {}
    monkeypatch.setattr(g.requests, "get", lambda url, **k: captured.__setitem__("get", url) or FakeResp(404))
    monkeypatch.setattr(g.requests, "post", _routing_post(captured))
    tools["publish_grafana_dashboard"](_spec())
    assert "//api" not in captured["db_url"]
    assert captured["db_url"] == "https://grafana.example.com/api/dashboards/db"
    assert captured["get"] == "https://grafana.example.com/api/dashboards/uid/growth_x_daily"


def test_get_dashboard_not_found(monkeypatch, tools):
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    out = tools["get_grafana_dashboard"]("missing")
    assert "not found" in out


def test_validate_rejects_unsafe_sql(tools):
    spec = _spec(panels=[
        {"title": "Bad", "role": "detail", "data_shape": "tabular",
         "sql_query": "DROP TABLE users"},
    ])
    out = tools["validate_grafana_dashboard"](spec)
    assert out.startswith("INVALID")


def test_validate_rejects_unknown_macro(tools):
    spec = _spec(panels=[
        {"title": "M", "role": "detail", "data_shape": "tabular",
         "sql_query": "SELECT $__nope(x) FROM t"},
    ])
    out = tools["validate_grafana_dashboard"](spec)
    assert out.startswith("INVALID")


def test_validate_ok_runs_live_check(monkeypatch, tools):
    captured = {}
    monkeypatch.setattr(g.requests, "post", _routing_post(captured))
    out = tools["validate_grafana_dashboard"](_spec())
    assert out.startswith("SQL VALID")
    assert "ALL PANELS RETURN DATA" in out
    assert "OK 'Users': 1 row" in out


def test_verify_tool_reports_rows(monkeypatch, tools):
    monkeypatch.setattr(g.requests, "post", _routing_post({}))
    out = tools["verify_grafana_dashboard"](_spec())
    assert "OK 'Users': 1 row(s)" in out


def _multi_spec():
    return GrafanaDashboardDef(uid="u", title="T", panels=[
        {"title": "K", "role": "kpi", "data_shape": "single_value",
         "sql_query": "SELECT 1 AS value", "unit": "short"},
        {"title": "Tr", "role": "trend", "data_shape": "time_series_single",
         "sql_query": "SELECT t AS time, 1 AS value FROM x", "unit": "short"},
        {"title": "D", "role": "detail", "data_shape": "tabular",
         "sql_query": "SELECT a FROM y LIMIT 5"},
    ])


def test_publish_multisection_skips_row_panels(monkeypatch, tools):
    # Multi-role dashboards get section "row" panels (no targets). Verification
    # must skip them and check only the 3 data panels.
    captured = {}
    monkeypatch.setattr(g.requests, "get", lambda *a, **k: FakeResp(404))
    monkeypatch.setattr(g.requests, "post", _routing_post(captured, verify=_verify_ok(3)))
    out = tools["publish_grafana_dashboard"](_multi_spec())
    assert "Verified 3 panel(s) return data" in out


def test_verify_multisection_reports_only_data_panels(monkeypatch, tools):
    monkeypatch.setattr(g.requests, "post", _routing_post({}, verify=_verify_ok(3)))
    out = tools["verify_grafana_dashboard"](_multi_spec())
    assert "OK 'K'" in out and "OK 'Tr'" in out and "OK 'D'" in out
    # exactly 3 report lines — section row panels are not verified
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 3


def test_preview_tool_returns_sketch(tools):
    # Preview is pure/local — no network, no publish.
    out = tools["preview_grafana_dashboard"](_spec())
    assert "Dashboard:" in out
    assert "Users" in out
    assert "Approve to publish" in out
