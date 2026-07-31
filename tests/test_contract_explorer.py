"""Tests for the Contract Explorer mini-app."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients import abi_resolver
from cerebro_mcp.tools.web3 import contract_explorer
from cerebro_mcp.tools.visualization import mini_apps
from cerebro_mcp.runtime.mcp_server import CerebroFastMCP


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
    abi_resolver.clear_caches()
    mini_apps.reset_views_for_tests()
    yield
    abi_resolver.clear_caches()
    mini_apps.reset_views_for_tests()


@pytest.fixture(autouse=True)
def _no_live_sourcify(monkeypatch):
    """Sourcify is the last ABI tier — keep it off the network in tests.

    Without this, any drift in a ``_fetch_blockscout`` test double silently
    falls through to a real HTTP call to sourcify.dev instead of failing.
    """
    monkeypatch.setattr(
        abi_resolver, "_resolve_from_sourcify", lambda address, chain_id: None
    )


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
    server = CerebroFastMCP("test")
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
        "contract_explorer_read_history",
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


# ---------------------------------------------------------------------------
# On-chain proxy probe (Safe masterCopy / EIP-1967 / EIP-1167)
# ---------------------------------------------------------------------------

SAFE_PROXY = "0x295bA5c775969c6310Fa040A02C1BEC066a84967"
SAFE_SINGLETON = "0x29fcB43b46531BcA003ddC8FCB67FFE91900C762"
# GnosisSafeProxy: verified, but constructor + fallback only — no functions.
SAFE_PROXY_ABI = [
    {"type": "constructor", "stateMutability": "nonpayable",
     "inputs": [{"name": "_singleton", "type": "address"}]},
    {"type": "fallback", "stateMutability": "payable"},
]
SAFE_IMPL_ABI = [
    {"type": "function", "name": "getOwners", "inputs": [],
     "outputs": [{"name": "", "type": "address[]"}], "stateMutability": "view"},
    {"type": "function", "name": "getThreshold", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
]


def _make_ch_for_safe():
    """No dbt row for the proxy; the singleton is seeded under its own address."""
    ch = MagicMock()

    def _rows(sql, db, cache_key, parameters=None):
        addr = (parameters or {}).get("addr", "").lower()
        if addr == SAFE_SINGLETON.lower():
            return {"rows": [[
                SAFE_SINGLETON.lower(), "", json.dumps(SAFE_IMPL_ABI),
                "GnosisSafe_v1_4_1L2", "blockscout-seed",
            ]]}
        return {"rows": []}

    ch.execute_raw_cached.side_effect = _rows
    return ch


def _safe_proxy_blockscout(address, chain_id=100):
    # Blockscout does NOT recognise the slot-0 masterCopy pattern:
    # implementations is empty even though the contract is a proxy.
    assert address.lower() == SAFE_PROXY.lower()
    return {"name": "GnosisSafeProxy", "abi": SAFE_PROXY_ABI, "implementations": []}


def test_open_safe_proxy_resolves_singleton_via_onchain_probe():
    ch = _make_ch_for_safe()
    server = _build_server(ch)
    with patch.object(abi_resolver, "_fetch_blockscout", _safe_proxy_blockscout), \
         patch.object(abi_resolver, "_detect_implementation_onchain",
                      return_value=SAFE_SINGLETON) as probe:
        res = _tool(server, "open_contract_explorer")(address=SAFE_PROXY)
    probe.assert_called_once_with(SAFE_PROXY, 100)
    vs = res.structuredContent["view_state"]
    assert vs["address"] == SAFE_PROXY  # calls still target the proxy
    assert vs["implementation_address"] == SAFE_SINGLETON
    assert vs["contract_name"] == "GnosisSafe_v1_4_1L2"
    read_names = [f["name"] for f in vs["read_functions"]]
    assert {"getOwners", "getThreshold"}.issubset(read_names)


def test_open_safe_proxy_keeps_proxy_abi_when_probe_finds_nothing():
    ch = _make_ch_for_safe()
    server = _build_server(ch)
    with patch.object(abi_resolver, "_fetch_blockscout", _safe_proxy_blockscout), \
         patch.object(abi_resolver, "_detect_implementation_onchain",
                      return_value=""):
        res = _tool(server, "open_contract_explorer")(address=SAFE_PROXY)
    vs = res.structuredContent["view_state"]
    assert vs["contract_name"] == "GnosisSafeProxy"
    assert vs["implementation_address"] == ""
    assert vs["read_functions"] == []


def test_probe_not_called_for_contracts_with_functions():
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    with patch.object(abi_resolver, "_detect_implementation_onchain") as probe:
        _tool(server, "open_contract_explorer")(address=WXDAI_ADDR)
    probe.assert_not_called()


def test_probe_not_called_for_target_proxy():
    ch = _make_ch_for_safe()
    server = _build_server(ch)
    with patch.object(abi_resolver, "_fetch_blockscout", _safe_proxy_blockscout), \
         patch.object(abi_resolver, "_detect_implementation_onchain") as probe:
        res = _tool(server, "open_contract_explorer")(
            address=SAFE_PROXY, target="proxy"
        )
    probe.assert_not_called()
    assert res.structuredContent["view_state"]["contract_name"] == "GnosisSafeProxy"


# --- _detect_implementation_onchain unit tests (fake RPC) -------------------

class _FakeEth:
    def __init__(self, codes=None, storage=None, calls=None):
        self._codes = {k.lower(): v for k, v in (codes or {}).items()}
        self._storage = {(a.lower(), s): v for (a, s), v in (storage or {}).items()}
        # {to_address_lower: return_bytes} for eth_call (beacon lookups).
        self._calls = {k.lower(): v for k, v in (calls or {}).items()}

    def get_code(self, address):
        return self._codes.get(address.lower(), b"")

    def get_storage_at(self, address, slot):
        return self._storage.get((address.lower(), slot), b"\x00" * 32)

    def call(self, tx, *args, **kwargs):
        to = str(tx.get("to", "")).lower()
        if to not in self._calls:
            raise ValueError("execution reverted")
        return self._calls[to]


class _FakeRpcManager:
    def __init__(self, eth):
        self._w3 = MagicMock(eth=eth)

    def standard(self, chain_id=100):
        return self._w3

    def archive(self, chain_id=100):
        return self._w3

    def retry(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


def _word(address):
    return bytes.fromhex(address[2:]).rjust(32, b"\x00")


def test_detect_finds_safe_mastercopy_in_slot0(monkeypatch):
    eth = _FakeEth(
        codes={SAFE_PROXY: b"\x60\x80", SAFE_SINGLETON: b"\x01"},
        storage={(SAFE_PROXY, 0): _word(SAFE_SINGLETON)},
    )
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert abi_resolver._detect_implementation_onchain(SAFE_PROXY) == SAFE_SINGLETON


def test_detect_prefers_eip1967_slot(monkeypatch):
    eth = _FakeEth(
        codes={SAFE_PROXY: b"\x60\x80", EURE_IMPL: b"\x01", SAFE_SINGLETON: b"\x01"},
        storage={
            (SAFE_PROXY, abi_resolver._EIP1967_IMPL_SLOT): _word(EURE_IMPL),
            (SAFE_PROXY, 0): _word(SAFE_SINGLETON),
        },
    )
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert abi_resolver._detect_implementation_onchain(SAFE_PROXY) == EURE_IMPL


def test_detect_reads_eip1167_bytecode(monkeypatch):
    clone = "0x" + "cd" * 20
    code = (
        abi_resolver._EIP1167_PREFIX
        + bytes.fromhex(EURE_IMPL[2:])
        + abi_resolver._EIP1167_SUFFIX
    )
    eth = _FakeEth(codes={clone: code})
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert abi_resolver._detect_implementation_onchain(clone) == EURE_IMPL


def test_detect_rejects_slot0_value_that_is_not_a_contract(monkeypatch):
    # Slot 0 holds something address-shaped but with no code behind it —
    # ordinary storage, not a masterCopy pointer.
    eth = _FakeEth(
        codes={SAFE_PROXY: b"\x60\x80"},
        storage={(SAFE_PROXY, 0): _word("0x" + "ab" * 20)},
    )
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert abi_resolver._detect_implementation_onchain(SAFE_PROXY) == ""


def test_detect_returns_empty_when_rpc_unavailable(monkeypatch):
    class _DeadRpc:
        def standard(self, chain_id=100):
            raise RuntimeError("no RPC configured")

        def retry(self, fn, *args, **kwargs):
            raise RuntimeError("no RPC configured")

    monkeypatch.setattr(abi_resolver, "rpc_manager", _DeadRpc())
    assert abi_resolver._detect_implementation_onchain(SAFE_PROXY) == ""


def test_probe_upgrades_events_only_seed_to_blockscout_abi():
    """The dbt seed for Safe singletons is a decoding stub (events + setup,
    no view/pure functions) — the probe must fetch the full ABI from
    Blockscout instead of serving a read-less contract page."""
    stub_abi = [
        {"type": "event", "name": "ExecutionSuccess", "inputs": []},
        {"type": "function", "name": "setup", "inputs": [],
         "outputs": [], "stateMutability": "nonpayable"},
    ]
    ch = MagicMock()

    def _rows(sql, db, cache_key, parameters=None):
        addr = (parameters or {}).get("addr", "").lower()
        if addr == SAFE_SINGLETON.lower():
            return {"rows": [[
                SAFE_SINGLETON.lower(), "", json.dumps(stub_abi),
                "GnosisSafe_v1_4_1L2", "safe-global/safe-smart-account",
            ]]}
        return {"rows": []}

    ch.execute_raw_cached.side_effect = _rows

    def _blockscout(address, chain_id=100):
        if address.lower() == SAFE_PROXY.lower():
            return {"name": "GnosisSafeProxy", "abi": SAFE_PROXY_ABI,
                    "implementations": []}
        assert address.lower() == SAFE_SINGLETON.lower()
        return {"name": "GnosisSafeL2", "abi": SAFE_IMPL_ABI,
                "implementations": []}

    server = _build_server(ch)
    with patch.object(abi_resolver, "_fetch_blockscout", _blockscout), \
         patch.object(abi_resolver, "_detect_implementation_onchain",
                      return_value=SAFE_SINGLETON):
        res = _tool(server, "open_contract_explorer")(address=SAFE_PROXY)
    vs = res.structuredContent["view_state"]
    assert vs["implementation_address"] == SAFE_SINGLETON
    read_names = [f["name"] for f in vs["read_functions"]]
    assert {"getOwners", "getThreshold"}.issubset(read_names)
    assert vs["abi_source"] == "blockscout"


# ---------------------------------------------------------------------------
# Multi-chain + history series
# ---------------------------------------------------------------------------

def test_view_state_carries_chain_identity_and_options():
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    vs = _tool(server, "open_contract_explorer")(address=WXDAI_ADDR).structuredContent["view_state"]
    assert vs["chain_id"] == 100
    assert vs["chain_name"] == "Gnosis"
    assert vs["explorer"]["base_url"] == "https://gnosis.blockscout.com"
    # Only chains with an RPC configured may be offered by the selector.
    assert all(opt["chain_id"] for opt in vs["chain_options"])
    assert 100 in [opt["chain_id"] for opt in vs["chain_options"]]


def test_open_on_unconfigured_chain_names_the_env_var(monkeypatch):
    """Hermetic: clear the setting rather than assuming a chain is unset —
    which chains have RPC_URL_* configured is ambient .env state."""
    from cerebro_mcp import chains

    monkeypatch.setattr(chains.settings, "RPC_URL_AVALANCHE", "", raising=False)
    monkeypatch.setattr(chains.settings, "RPC_URL_AVALANCHE_ARCHIVE", "", raising=False)

    ch = MagicMock()
    server = _build_server(ch)
    sc = _tool(server, "open_contract_explorer")(
        address=WXDAI_ADDR, chain="avalanche"
    ).structuredContent
    assert sc["type"] == "SHOW_WARNING"
    assert any("RPC_URL_AVALANCHE" in w for w in sc["warnings"])
    # Must fail before any ABI lookup — the RPC is the missing piece, not the ABI.
    ch.execute_raw_cached.assert_not_called()


def test_open_on_unknown_chain_is_rejected():
    server = _build_server(MagicMock())
    sc = _tool(server, "open_contract_explorer")(
        address=WXDAI_ADDR, chain="solana"
    ).structuredContent
    assert sc["type"] == "SHOW_WARNING"
    assert any("Unknown chain" in w for w in sc["warnings"])


def test_load_without_chain_keeps_the_views_current_chain():
    """Omitting `chain` on a swap must not silently reset to Gnosis."""
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    view_id = _tool(server, "open_contract_explorer")(
        address=WXDAI_ADDR
    ).structuredContent["view_id"]
    record = mini_apps.get_view(view_id)
    record.view_state["chain_id"] = 100

    vs = _tool(server, "load_contract_explorer_address")(
        view_id=view_id, address=WXDAI_ADDR
    ).structuredContent["view_state"]
    assert vs["chain_id"] == 100


def _install_history(monkeypatch, signature="totalSupply()", points=3):
    def _fake(ch, address, **kwargs):
        return {
            "signature": kwargs.get("function_name", "") + "()"
            if kwargs.get("function_name") else signature,
            "from_block": 100, "to_block": 200, "output_index": 0,
            "decimals": None, "output_types": ["uint256"],
            "points": [
                {"block": 100 + i, "timestamp": 1_700_000_000 + i,
                 "status": "ok", "value": str(i), "value_float": float(i),
                 "error": ""}
                for i in range(points)
            ],
            "ok_count": points, "truncated": False, "warnings": [],
        }
    monkeypatch.setattr(contract_explorer, "read_function_history", _fake)


def test_read_history_appends_a_series_to_view_state(monkeypatch):
    _install_history(monkeypatch)
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    view_id = _tool(server, "open_contract_explorer")(
        address=WXDAI_ADDR
    ).structuredContent["view_id"]

    sc = _tool(server, "contract_explorer_read_history")(
        view_id=view_id, function_name="totalSupply", since="30d"
    ).structuredContent
    assert sc["type"] == "PATCH_VIEW_STATE"
    series = sc["patch"]["history"]
    assert len(series) == 1
    assert series[0]["signature"] == "totalSupply()"
    assert len(series[0]["points"]) == 3


def test_resweeping_the_same_function_replaces_rather_than_duplicates(monkeypatch):
    _install_history(monkeypatch)
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    view_id = _tool(server, "open_contract_explorer")(
        address=WXDAI_ADDR
    ).structuredContent["view_id"]

    run = _tool(server, "contract_explorer_read_history")
    run(view_id=view_id, function_name="totalSupply", since="30d")
    sc = run(view_id=view_id, function_name="totalSupply", since="7d").structuredContent
    history = sc["patch"]["history"]
    assert len(history) == 1
    assert history[0]["range_label"] == "7d"


def test_history_series_are_capped(monkeypatch):
    """Each series carries up to 200 points and the whole view_state ships on
    INITIAL_LOAD — unbounded sweeps would bloat the wire payload."""
    _install_history(monkeypatch)
    ch = _make_ch_for_wxdai()
    server = _build_server(ch)
    view_id = _tool(server, "open_contract_explorer")(
        address=WXDAI_ADDR
    ).structuredContent["view_id"]

    run = _tool(server, "contract_explorer_read_history")
    for i in range(contract_explorer.MAX_HISTORY_SERIES + 3):
        sc = run(view_id=view_id, function_name=f"fn{i}", since="30d").structuredContent
    assert len(sc["patch"]["history"]) == contract_explorer.MAX_HISTORY_SERIES
    # Most recent first.
    assert sc["patch"]["history"][0]["signature"].startswith("fn")


def test_read_history_without_an_address_is_rejected(monkeypatch):
    _install_history(monkeypatch)
    server = _build_server(MagicMock())
    view_id = _tool(server, "open_contract_explorer")().structuredContent["view_id"]
    res = _tool(server, "contract_explorer_read_history")(
        view_id=view_id, function_name="totalSupply"
    )
    assert res.isError or "no address" in str(res.content[0].text).lower()


# --- beacon proxies (Nomad/Optics UpgradeBeaconProxy) ----------------------
#
# Regression: https://celoscan.io/address/0xef4229c8c3250c675f21bcefa42f58efbff6002a
# resolved to a function-less "UpgradeBeaconProxy" because the beacon lives in
# a bytecode IMMUTABLE (every storage slot is empty) and, even once found, a
# beacon needs a second hop to reach the implementation.

BEACON = "0x3a5846882C0d5F8B0FA4bB04dc90C013104d125d"
BEACON_IMPL = "0xe41F1Bb38b5d155534fdAbb701C3331927c825e1"
BEACON_PROXY = "0xEf4229c8c3250C675F21BCefa42f58EfbfF6002a"


def _push32(address: str) -> bytes:
    """PUSH32 <address right-aligned in a word> — how solc inlines an immutable."""
    return b"\x7f" + bytes.fromhex(address[2:]).rjust(32, b"\x00")


def test_addresses_in_code_extracts_push32_immutables():
    code = b"\x60\x80\x60\x40" + _push32(BEACON) + b"\x50\x00"
    assert abi_resolver._addresses_in_code(code) == [BEACON]


def test_addresses_in_code_extracts_push20_immutables():
    code = b"\x60\x80" + b"\x73" + bytes.fromhex(BEACON[2:]) + b"\x50"
    assert abi_resolver._addresses_in_code(code) == [BEACON]


def test_addresses_in_code_skips_push_operands_rather_than_scanning_bytes():
    """A 0x73 byte INSIDE a push operand must not be re-read as a PUSH20 opcode
    — that is the whole reason for parsing pushes instead of sliding a window.
    A naive parser resuming mid-operand would surface a phantom address."""
    phantom = bytes.fromhex(BEACON_IMPL[2:])          # 20 bytes
    operand = b"\x73" + phantom + b"\x00" * 11        # PUSH20-looking byte inside
    code = b"\x7f" + operand + _push32(BEACON)

    found = abi_resolver._addresses_in_code(code)
    assert BEACON_IMPL not in found
    assert BEACON in found


def test_detect_follows_bytecode_immutable_beacon_to_implementation(monkeypatch):
    eth = _FakeEth(
        codes={
            BEACON_PROXY: _push32(BEACON),
            BEACON: b"\x01",
            BEACON_IMPL: b"\x02",
        },
        # Nomad UpgradeBeacon answers ANY calldata with the implementation.
        calls={BEACON: bytes.fromhex(BEACON_IMPL[2:]).rjust(32, b"\x00")},
    )
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert (
        abi_resolver._detect_implementation_onchain(BEACON_PROXY, 42220)
        == BEACON_IMPL
    )


def test_detect_follows_eip1967_beacon_slot_through_the_extra_hop(monkeypatch):
    """The beacon SLOT holds the beacon, not the implementation — returning the
    slot value directly would serve the beacon's ABI."""
    eth = _FakeEth(
        codes={SAFE_PROXY: b"\x60\x80", BEACON: b"\x01", BEACON_IMPL: b"\x02"},
        storage={(SAFE_PROXY, abi_resolver._EIP1967_BEACON_SLOT): _word(BEACON)},
        calls={BEACON: bytes.fromhex(BEACON_IMPL[2:]).rjust(32, b"\x00")},
    )
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert (
        abi_resolver._detect_implementation_onchain(SAFE_PROXY, 42220) == BEACON_IMPL
    )


def test_eip1967_impl_slot_still_wins_over_bytecode_immutables(monkeypatch):
    """Standards before heuristics — an embedded address must not outrank the
    declared implementation slot."""
    eth = _FakeEth(
        codes={SAFE_PROXY: _push32(BEACON), EURE_IMPL: b"\x01", BEACON: b"\x01"},
        storage={(SAFE_PROXY, abi_resolver._EIP1967_IMPL_SLOT): _word(EURE_IMPL)},
    )
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert abi_resolver._detect_implementation_onchain(SAFE_PROXY, 42220) == EURE_IMPL


def test_embedded_address_without_code_is_ignored(monkeypatch):
    """A hardcoded EOA or token address in a proxy is not an implementation."""
    eth = _FakeEth(codes={BEACON_PROXY: _push32(BEACON)})  # BEACON has no code
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert abi_resolver._detect_implementation_onchain(BEACON_PROXY, 42220) == ""


def test_non_beacon_embedded_contract_is_used_directly(monkeypatch):
    """When the embedded contract answers no beacon dialect, treat it as the
    implementation — we only reach here for ABIs with no callable functions."""
    eth = _FakeEth(codes={BEACON_PROXY: _push32(BEACON), BEACON: b"\x01"})
    monkeypatch.setattr(abi_resolver, "rpc_manager", _FakeRpcManager(eth))
    assert abi_resolver._detect_implementation_onchain(BEACON_PROXY, 42220) == BEACON
