"""Tests for the Contract Explorer mini-app."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp import abi_resolver
from cerebro_mcp.tools import contract_explorer, mini_apps


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

WXDAI_ADDR = "0xe91D153E0b41518A2Ce8Dd3D7944Fa863463a97d"
WXDAI_ABI = [
    {"type": "function", "name": "symbol", "inputs": [],
     "outputs": [{"name": "", "type": "string"}], "stateMutability": "view"},
    {"type": "function", "name": "decimals", "inputs": [],
     "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view"},
    {"type": "function", "name": "balanceOf",
     "inputs": [{"name": "owner", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"type": "function", "name": "transfer",
     "inputs": [{"name": "to", "type": "address"},
                {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
    {"type": "event", "name": "Transfer",
     "inputs": [{"name": "from", "type": "address", "indexed": True},
                {"name": "to", "type": "address", "indexed": True},
                {"name": "value", "type": "uint256", "indexed": False}]},
]

EURE_PROXY = "0x420CA0f9B9b604cE0fd9C18EF134C705e5Fa3430"
EURE_IMPL = "0x60cb9FdD0fcFd9BB3b2B721864Db5E7C07F4635D"
EURE_PROXY_ABI = [
    {"type": "function", "name": "implementation", "inputs": [],
     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view"},
]
EURE_IMPL_ABI = [
    {"type": "function", "name": "balanceOf",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"type": "function", "name": "totalSupply", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"type": "function", "name": "mint",
     "inputs": [{"name": "to", "type": "address"},
                {"name": "amount", "type": "uint256"}],
     "outputs": [], "stateMutability": "nonpayable"},
]


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    abi_resolver._cache.clear()
    mini_apps.reset_views_for_tests()
    yield
    abi_resolver._cache.clear()
    mini_apps.reset_views_for_tests()


def _make_ch_for_wxdai():
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {"rows": [[
        WXDAI_ADDR, "", json.dumps(WXDAI_ABI), "WXDAI", "blockscout-seed",
    ]]}
    return ch


def _make_ch_for_eure():
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {"rows": [
        # impl row first (ORDER BY implementation_address != '' DESC)
        [EURE_PROXY, EURE_IMPL, json.dumps(EURE_IMPL_ABI),
         "GnosisControllerToken", "blockscout"],
        [EURE_PROXY, "", json.dumps(EURE_PROXY_ABI), "", "blockscout"],
    ]}
    return ch


def _build_server(ch):
    server = FastMCP("test")
    mini_apps.register_mini_app_infra(server, ch)
    contract_explorer.register_contract_explorer_tools(server, ch)
    return server


def _tool(server, name):
    return next(
        t.fn for t in server._tool_manager._tools.values() if t.name == name
    )


# ---------------------------------------------------------------------------
# open_contract_explorer
# ---------------------------------------------------------------------------

def test_open_with_empty_address_returns_empty_initial_load():
    ch = MagicMock()
    server = _build_server(ch)
    res = _tool(server, "open_contract_explorer")()
    sc = res.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["app_id"] == "contract_explorer"
    assert sc["view_state"]["address"] == ""
    assert sc["view_state"]["read_functions"] == []
    ch.execute_raw_cached.assert_not_called()


def test_open_resolves_clickhouse_abi():
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    res = _tool(server, "open_contract_explorer")(address=WXDAI_ADDR)
    sc = res.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    vs = sc["view_state"]
    assert vs["contract_name"] == "WXDAI"
    assert vs["abi_source"] == "blockscout-seed"
    assert vs["implementation_address"] == ""
    read_names = [f["name"] for f in vs["read_functions"]]
    write_names = [f["name"] for f in vs["write_functions"]]
    assert {"symbol", "decimals", "balanceOf"}.issubset(read_names)
    assert "transfer" in write_names
    assert any(e["name"] == "Transfer" for e in vs["events"])


def test_open_follows_proxy_for_eure():
    ch = _make_ch_for_eure()
    server = _build_server(ch)
    res = _tool(server, "open_contract_explorer")(address=EURE_PROXY)
    vs = res.structuredContent["view_state"]
    assert vs["contract_name"] == "GnosisControllerToken"
    assert vs["implementation_address"].lower() == EURE_IMPL.lower()
    read_names = [f["name"] for f in vs["read_functions"]]
    assert "balanceOf" in read_names
    assert "totalSupply" in read_names
    # Proxy's own implementation() must NOT leak into the merged view (target=auto).
    assert "implementation" not in read_names


def test_open_target_proxy_returns_proxy_abi():
    ch = _make_ch_for_eure()
    server = _build_server(ch)
    res = _tool(server, "open_contract_explorer")(
        address=EURE_PROXY, target="proxy"
    )
    vs = res.structuredContent["view_state"]
    read_names = [f["name"] for f in vs["read_functions"]]
    assert "implementation" in read_names
    assert "balanceOf" not in read_names
    assert vs["implementation_address"] == ""


def test_open_returns_show_warning_when_abi_unresolved():
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {"rows": []}
    server = _build_server(ch)
    with patch.object(
        abi_resolver, "_fetch_blockscout",
        return_value={"name": "", "abi": [], "implementations": []},
    ):
        res = _tool(server, "open_contract_explorer")(address="0x" + "ab" * 20)
    sc = res.structuredContent
    assert sc["type"] == "SHOW_WARNING"
    assert sc["status"] == "error"
    assert any("ABI not found" in w for w in sc["warnings"])


# ---------------------------------------------------------------------------
# load_contract_explorer_address
# ---------------------------------------------------------------------------

def test_load_swaps_address_in_existing_view():
    # Open with WXDAI…
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    open_fn = _tool(server, "open_contract_explorer")
    res = open_fn(address=WXDAI_ADDR)
    view_id = res.structuredContent["view_id"]

    # …then swap to EURe in the same view.
    ch.execute_raw_cached.return_value = {"rows": [
        [EURE_PROXY, EURE_IMPL, json.dumps(EURE_IMPL_ABI),
         "GnosisControllerToken", "blockscout"],
        [EURE_PROXY, "", json.dumps(EURE_PROXY_ABI), "", "blockscout"],
    ]}
    load_fn = _tool(server, "load_contract_explorer_address")
    res2 = load_fn(view_id=view_id, address=EURE_PROXY)
    sc = res2.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["view_id"] == view_id
    assert sc["view_state"]["contract_name"] == "GnosisControllerToken"
    # Old WXDAI functions are gone.
    read_names = [f["name"] for f in sc["view_state"]["read_functions"]]
    assert "symbol" not in read_names
    assert "balanceOf" in read_names


def test_load_with_unknown_view_returns_error():
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    res = _tool(server, "load_contract_explorer_address")(
        view_id="bogus", address=WXDAI_ADDR,
    )
    assert res.isError
    assert "Unknown" in res.content[0].text


# ---------------------------------------------------------------------------
# contract_explorer_call_function
# ---------------------------------------------------------------------------

def _open_eure(server):
    res = _tool(server, "open_contract_explorer")(address=EURE_PROXY)
    return res.structuredContent["view_id"]


def test_explorer_call_appends_history():
    ch = _make_ch_for_eure()
    server = _build_server(ch)
    view_id = _open_eure(server)

    fake_outcome = {
        "ok": True,
        "address": EURE_PROXY,
        "function": "balanceOf",
        "signature": "balanceOf(address)",
        "mutability": "view",
        "block": "latest",
        "args": ["0xbDA14C8F73773469a819C52C110FDC1a63884ADC"],
        "elapsed_seconds": 0.11,
        "result": "512148270000000000000000",
    }
    with patch.object(
        contract_explorer, "call_view_function", return_value=fake_outcome,
    ) as m:
        res = _tool(server, "contract_explorer_call_function")(
            view_id=view_id,
            function_name="balanceOf",
            args=["0xbda14c8f73773469a819c52c110fdc1a63884adc"],  # lowercase
        )

    m.assert_called_once()
    sc = res.structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    assert sc["patch"]["call_history"][0]["ok"] is True
    assert sc["patch"]["call_history"][0]["result"] == "512148270000000000000000"
    assert sc["patch"]["call_history"][0]["function"] == "balanceOf"


def test_explorer_call_records_failure_without_raising():
    ch = _make_ch_for_eure()
    server = _build_server(ch)
    view_id = _open_eure(server)

    bad = {
        "ok": False,
        "address": EURE_PROXY,
        "function": "transfer",
        "signature": "transfer(address,uint256)",
        "mutability": "nonpayable",
        "block": "latest",
        "args": [],
        "elapsed_seconds": 0.0,
        "error": "only view/pure functions are allowed.",
    }
    with patch.object(
        contract_explorer, "call_view_function", return_value=bad,
    ):
        res = _tool(server, "contract_explorer_call_function")(
            view_id=view_id, function_name="transfer",
        )

    sc = res.structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    entry = sc["patch"]["call_history"][0]
    assert entry["ok"] is False
    assert "view/pure" in entry["error"]


def test_explorer_call_history_capped():
    ch = _make_ch_for_eure()
    server = _build_server(ch)
    view_id = _open_eure(server)

    fake_outcome = {
        "ok": True, "address": EURE_PROXY, "function": "totalSupply",
        "signature": "totalSupply()", "mutability": "view",
        "block": "latest", "args": [], "elapsed_seconds": 0.05,
        "result": "1",
    }
    with patch.object(
        contract_explorer, "call_view_function", return_value=fake_outcome,
    ):
        call_fn = _tool(server, "contract_explorer_call_function")
        for i in range(contract_explorer.MAX_CALL_HISTORY + 10):
            call_fn(view_id=view_id, function_name="totalSupply")

    record = mini_apps.get_view(view_id)
    assert len(record.view_state["call_history"]) == contract_explorer.MAX_CALL_HISTORY


def test_explorer_call_unknown_view_returns_error():
    ch = MagicMock()
    server = _build_server(ch)
    res = _tool(server, "contract_explorer_call_function")(
        view_id="bogus", function_name="symbol",
    )
    assert res.isError


def test_explorer_call_without_address_in_view_errors():
    ch = MagicMock()
    server = _build_server(ch)
    # Open empty (no address resolved → no address persisted in view_state).
    res_open = _tool(server, "open_contract_explorer")()
    view_id = res_open.structuredContent["view_id"]

    res = _tool(server, "contract_explorer_call_function")(
        view_id=view_id, function_name="symbol",
    )
    assert res.isError
    assert "no address" in res.content[0].text.lower()


# ---------------------------------------------------------------------------
# Security registry
# ---------------------------------------------------------------------------

def test_security_registry_has_explorer_entries():
    from cerebro_mcp.security import (
        TOOL_RISK_REGISTRY,
        RiskClass,
    )
    expected = {RiskClass.READ_ONLY}
    for name in (
        "open_contract_explorer",
        "load_contract_explorer_address",
        "contract_explorer_call_function",
    ):
        assert TOOL_RISK_REGISTRY[name] == frozenset(expected), name


# ---------------------------------------------------------------------------
# ABI projection
# ---------------------------------------------------------------------------

def test_project_abi_separates_read_write_events_and_sorts():
    proj = contract_explorer._project_abi(WXDAI_ABI)
    assert [f["name"] for f in proj["read_functions"]] == sorted(
        [f["name"] for f in proj["read_functions"]], key=str.lower
    )
    assert [f["name"] for f in proj["read_functions"]] == [
        "balanceOf", "decimals", "symbol",
    ]
    assert [f["name"] for f in proj["write_functions"]] == ["transfer"]
    assert [e["name"] for e in proj["events"]] == ["Transfer"]
    sym = next(f for f in proj["read_functions"] if f["name"] == "symbol")
    assert sym["signature"] == "symbol()"
    assert sym["stateMutability"] == "view"
    bal = next(f for f in proj["read_functions"] if f["name"] == "balanceOf")
    assert bal["signature"] == "balanceOf(address)"
    assert bal["inputs"] == [{"name": "owner", "type": "address"}]
