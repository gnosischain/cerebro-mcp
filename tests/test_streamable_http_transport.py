"""Streamable HTTP transport (`cerebro-mcp --http`, endpoint ``/mcp``).

These cover the transport that replaces the flaky legacy-SSE + ``mcp-remote``
path for remote Claude Desktop:

* the combined app serves BOTH ``/mcp`` (Streamable HTTP) and the legacy
  ``/sse`` + ``/messages/`` routes from one process (zero-downtime cutover);
* ``/mcp`` authenticates via ``Authorization: Bearer`` OR ``?token=`` and
  rejects unauthenticated calls with 401;
* ``/health`` / ``/metrics`` stay auth-exempt;
* ``main()`` routes ``--http`` to the streamable runner.

Note: ``StreamableHTTPSessionManager.run()`` is single-use and the manager is
cached on the FastMCP singleton, so ``_build`` resets it per test.
"""

import importlib
import json
import sys

import pytest
from starlette.testclient import TestClient


AUTH = "test-token"
JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


@pytest.fixture(scope="module")
def server():
    sys.modules.pop("cerebro_mcp.server", None)
    return importlib.import_module("cerebro_mcp.server")


def _build(server, auth=AUTH, include_sse=True):
    # Fresh single-use session manager for each app instance.
    server.mcp._session_manager = None
    return server.build_streamable_http_app(auth, include_sse=include_sse)


def _extract_rpc(resp):
    """Return the JSON-RPC object from a JSON or SSE-framed response body."""
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
    raise AssertionError(f"no JSON-RPC payload in body: {resp.text!r}")


def test_combined_app_has_both_transports(server):
    app = _build(server)
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/mcp" in paths                        # streamable HTTP
    assert "/sse" in paths                        # legacy SSE (dual-served)
    assert any(p and p.startswith("/messages") for p in paths)
    # Custom routes ride along on the streamable app.
    assert {"/health", "/metrics", "/"} <= paths


def test_mcp_requires_auth(server):
    app = _build(server)
    with TestClient(app) as client:
        resp = client.post("/mcp", json=TOOLS_LIST, headers=JSON_HEADERS)
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_mcp_accepts_bearer_header_and_lists_tools(server):
    app = _build(server)
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json=TOOLS_LIST,
            headers={**JSON_HEADERS, "Authorization": f"Bearer {AUTH}"},
        )
    assert resp.status_code == 200
    tools = _extract_rpc(resp)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "execute_query" in names                # representative core tool
    assert len(tools) > 10


def test_mcp_accepts_query_token(server):
    app = _build(server)
    with TestClient(app) as client:
        resp = client.post(
            f"/mcp?token={AUTH}", json=TOOLS_LIST, headers=JSON_HEADERS
        )
    assert resp.status_code == 200
    assert _extract_rpc(resp)["result"]["tools"]


def test_mcp_rejects_wrong_query_token(server):
    app = _build(server)
    with TestClient(app) as client:
        resp = client.post(
            "/mcp?token=wrong", json=TOOLS_LIST, headers=JSON_HEADERS
        )
    assert resp.status_code == 401


def test_health_and_metrics_are_auth_exempt(server):
    app = _build(server)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200


def test_query_token_only_applies_to_mcp(server):
    """A ?token= on a non-/mcp authenticated path must NOT bypass auth."""
    app = _build(server)
    with TestClient(app) as client:
        # /messages/ is not exempt and does not honor ?token= — still 401.
        resp = client.post(
            "/messages/?token=" + AUTH,
            json={"jsonrpc": "2.0"},
            headers=JSON_HEADERS,
        )
    assert resp.status_code == 401


def test_no_auth_token_leaves_mcp_open(server):
    """Without MCP_AUTH_TOKEN the middleware is not attached (local/dev)."""
    app = _build(server, auth=None)
    with TestClient(app) as client:
        resp = client.post("/mcp", json=TOOLS_LIST, headers=JSON_HEADERS)
    assert resp.status_code == 200


def test_main_routes_http_flag_to_streamable_runner(server, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(server, "setup_logging", lambda: None)
    monkeypatch.setattr(server, "RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(server, "ensure_writable_dir", lambda p: None)
    for loader in ("manifest", "catalog", "docs_index", "semantic_runtime"):
        monkeypatch.setattr(getattr(server, loader), "load", lambda: None)
    monkeypatch.setattr(
        "cerebro_mcp.runtime.bootstrap.init_event_store_sync",
        lambda: {},
    )
    monkeypatch.setattr(server, "validate_remote_transport_auth", lambda t: None)
    monkeypatch.setattr(
        server, "_run_streamable_http_with_auth", lambda: calls.append("http")
    )
    monkeypatch.setattr(server, "_run_sse_with_auth", lambda: calls.append("sse"))
    monkeypatch.setattr(sys, "argv", ["cerebro-mcp", "--http"])

    server.main()

    assert calls == ["http"]
