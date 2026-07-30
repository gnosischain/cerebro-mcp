"""Tests for the contract explorer (`tools/rpc.py`) and ABI resolver."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cerebro_mcp.clients import abi_resolver
from cerebro_mcp.clients.abi_resolver import resolve_abi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_abi_cache():
    abi_resolver.clear_caches()
    yield
    abi_resolver.clear_caches()


@pytest.fixture(autouse=True)
def _no_live_sourcify(monkeypatch):
    """Sourcify is the last ABI tier — keep it off the network in tests."""
    monkeypatch.setattr(
        abi_resolver, "_resolve_from_sourcify", lambda address, chain_id: None
    )


@pytest.fixture
def fake_ch():
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {
        "rows": [[
            "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d",  # contract_address
            "",                                            # implementation_address
            json.dumps([{
                "type": "function",
                "name": "symbol",
                "inputs": [],
                "outputs": [{"type": "string"}],
                "stateMutability": "view",
            }]),
            "WXDAI",
            "blockscout-seed",
        ]]
    }
    return ch


# ---------------------------------------------------------------------------
# ABI resolver — basic
# ---------------------------------------------------------------------------

def test_resolver_uses_clickhouse_first(fake_ch):
    rec = resolve_abi(fake_ch, "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d")
    assert rec.source == "blockscout-seed"
    assert rec.contract_name == "WXDAI"
    assert any(f["name"] == "symbol" for f in rec.abi)


def test_resolver_caches_results(fake_ch):
    addr = "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d"
    resolve_abi(fake_ch, addr)
    resolve_abi(fake_ch, addr)
    # ClickHouse hit only once; second call hits cache.
    assert fake_ch.execute_raw_cached.call_count == 1


def test_resolver_falls_back_to_blockscout(fake_ch):
    fake_ch.execute_raw_cached.return_value = {"rows": []}
    body = {
        "name": "Foo",
        "abi": [{
            "type": "function", "name": "foo",
            "inputs": [], "outputs": [], "stateMutability": "view",
        }],
        "implementations": [],
    }
    with patch.object(abi_resolver, "_fetch_blockscout", return_value=body) as m:
        rec = resolve_abi(fake_ch, "0x" + "ab" * 20)
    assert rec.source == "blockscout"
    assert rec.contract_name == "Foo"
    m.assert_called_once()


def test_resolver_raises_when_nothing_found(fake_ch):
    fake_ch.execute_raw_cached.return_value = {"rows": []}
    body = {"name": "", "abi": [], "implementations": []}
    with patch.object(abi_resolver, "_fetch_blockscout", return_value=body):
        with pytest.raises(ValueError, match="ABI not found"):
            resolve_abi(fake_ch, "0x" + "ab" * 20)


# ---------------------------------------------------------------------------
# ABI resolver — proxy handling (matches dbt-cerebro convention)
# ---------------------------------------------------------------------------

PROXY_ADDR = "0x" + "ab" * 20
IMPL_ADDR = "0x" + "cd" * 20

PROXY_ABI = [{
    "type": "function", "name": "implementation",
    "inputs": [], "outputs": [{"type": "address"}],
    "stateMutability": "view",
}]
IMPL_ABI = [{
    "type": "function", "name": "deposit",
    "inputs": [], "outputs": [], "stateMutability": "view",
}]


def _proxy_two_row_response():
    return {"rows": [
        # ORDER BY implementation_address != '' DESC → impl row first
        [PROXY_ADDR, IMPL_ADDR, json.dumps(IMPL_ABI), "AaveV3Pool", "blockscout"],
        [PROXY_ADDR, "",        json.dumps(PROXY_ABI), "",          "blockscout"],
    ]}


def test_resolver_picks_impl_row_for_proxy(fake_ch):
    fake_ch.execute_raw_cached.return_value = _proxy_two_row_response()
    rec = resolve_abi(fake_ch, PROXY_ADDR)  # target="auto" (default)
    assert rec.implementation_address.lower() == IMPL_ADDR
    assert any(f["name"] == "deposit" for f in rec.abi)
    assert not any(f["name"] == "implementation" for f in rec.abi)


def test_resolver_target_proxy_picks_proxy_row(fake_ch):
    fake_ch.execute_raw_cached.return_value = _proxy_two_row_response()
    rec = resolve_abi(fake_ch, PROXY_ADDR, target="proxy")
    assert rec.implementation_address == ""
    assert any(f["name"] == "implementation" for f in rec.abi)


def test_resolver_proxy_falls_back_to_blockscout_when_no_impl_row(fake_ch):
    """Only the proxy row is in CH (impl row not yet seeded); abi_json is empty
    so resolver must fall through to Blockscout, which chases the impl."""
    fake_ch.execute_raw_cached.return_value = {"rows": [
        [PROXY_ADDR, "", json.dumps([]), "", "blockscout"],
    ]}
    proxy_body = {
        "name": "Proxy", "abi": [],
        "implementations": [{"address_hash": IMPL_ADDR}],
    }
    impl_body = {
        "name": "Impl",
        "abi": [{"type": "function", "name": "x", "inputs": [],
                 "outputs": [], "stateMutability": "view"}],
        "implementations": [],
    }
    with patch.object(
        abi_resolver, "_fetch_blockscout",
        side_effect=[proxy_body, impl_body],
    ):
        rec = resolve_abi(fake_ch, PROXY_ADDR)
    assert rec.contract_name == "Impl"
    assert rec.implementation_address.lower() == IMPL_ADDR


# ---------------------------------------------------------------------------
# Web3 RPC manager
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "chain_id,reported,expect_ok",
    [
        (100, 100, True),
        (1, 1, True),
        (100, 1, False),   # Gnosis env var pointed at mainnet
        (1, 100, False),   # mainnet env var pointed at Gnosis
    ],
)
def test_chain_id_mismatch_rejected(monkeypatch, chain_id, reported, expect_ok):
    """An endpoint pointed at the wrong network returns confidently wrong state."""
    from cerebro_mcp.clients.web3 import ChainRpcManager

    fake_w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=reported))
    fake_web3_cls = MagicMock(return_value=fake_w3)
    fake_web3_cls.HTTPProvider = MagicMock(return_value=None)

    monkeypatch.setattr("cerebro_mcp.clients.web3.Web3", fake_web3_cls)
    mgr = ChainRpcManager()
    if expect_ok:
        assert mgr._make("http://example/rpc", chain_id) is fake_w3
    else:
        with pytest.raises(ValueError, match=f"reports chain_id {reported}"):
            mgr._make("http://example/rpc", chain_id)


def test_chain_id_error_does_not_leak_the_endpoint_url(monkeypatch):
    """Endpoint URLs embed API keys — they must never reach tool output."""
    from cerebro_mcp.clients.web3 import ChainRpcManager

    fake_w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    fake_web3_cls = MagicMock(return_value=fake_w3)
    fake_web3_cls.HTTPProvider = MagicMock(return_value=None)
    monkeypatch.setattr("cerebro_mcp.clients.web3.Web3", fake_web3_cls)

    secret = "https://rpc.example/v2/SUPERSECRETKEY"
    with pytest.raises(ValueError) as excinfo:
        ChainRpcManager()._make(secret, 100)
    assert "SUPERSECRETKEY" not in str(excinfo.value)
    assert "RPC_URL_GNOSIS" in str(excinfo.value)


def test_for_block_routes_latest_to_standard(monkeypatch):
    from cerebro_mcp.clients.web3 import ChainRpcManager
    mgr = ChainRpcManager()
    mgr._clients[(100, "standard")] = "STD"  # type: ignore[assignment]
    mgr._clients[(100, "archive")] = "ARCH"  # type: ignore[assignment]
    assert mgr.for_block("latest") == "STD"
    assert mgr.for_block(None) == "STD"
    assert mgr.for_block(12345) == "ARCH"
    # Explicit chain routes to that chain's cached clients.
    mgr._clients[(1, "archive")] = "ETH-ARCH"  # type: ignore[assignment]
    assert mgr.for_block(12345, 1) == "ETH-ARCH"


def test_unconfigured_chain_names_the_env_var(monkeypatch):
    from cerebro_mcp import chains
    from cerebro_mcp.clients.web3 import ChainRpcManager

    monkeypatch.setattr(chains.settings, "RPC_URL_AVALANCHE", "", raising=False)
    monkeypatch.setattr(chains.settings, "RPC_URL_AVALANCHE_ARCHIVE", "", raising=False)
    with pytest.raises(ValueError, match="RPC_URL_AVALANCHE"):
        ChainRpcManager().archive(43114)


def test_gnosis_archive_falls_back_to_the_standard_endpoint(monkeypatch):
    """Archive nodes serve both roles — a single URL must not raise."""
    from cerebro_mcp import chains
    from cerebro_mcp.clients.web3 import ChainRpcManager

    monkeypatch.setattr(chains.settings, "GNOSIS_RPC_URL", "https://gno.example")
    monkeypatch.setattr(chains.settings, "GNOSIS_ARCHIVE_RPC_URL", "")

    fake_w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=100))
    fake_web3_cls = MagicMock(return_value=fake_w3)
    fake_web3_cls.HTTPProvider = MagicMock(return_value=None)
    monkeypatch.setattr("cerebro_mcp.clients.web3.Web3", fake_web3_cls)

    assert ChainRpcManager().archive(100) is fake_w3


def test_module_import_does_not_create_rpc():
    """Importing the web3_client module must not instantiate any Web3 client."""
    import importlib
    import cerebro_mcp.clients.web3 as wc
    importlib.reload(wc)
    assert wc.rpc_manager._clients == {}


# ---------------------------------------------------------------------------
# Tool registration smoke test (state-mutation guard)
# ---------------------------------------------------------------------------

def _capture_tools():
    """Register the RPC tools onto a stub MCP and return the captured callables."""
    from cerebro_mcp.tools.web3.rpc import register_rpc_tools

    captured: dict[str, Any] = {}

    class StubMCP:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    register_rpc_tools(StubMCP(), MagicMock())
    return captured


def test_tools_register_with_expected_names():
    tools = _capture_tools()
    assert set(tools) == {
        "list_chains",
        "contract_explore",
        "contract_call_function",
        "contract_read_history",
        "contract_decode_transaction_input",
        "contract_decode_receipt_logs",
    }


def test_contract_call_rejects_non_view_function(fake_ch, monkeypatch):
    """State-changing functions must be refused at the tool layer."""
    fake_ch.execute_raw_cached.return_value = {"rows": [[
        "0x" + "ab" * 20,
        "",
        json.dumps([{
            "type": "function", "name": "transfer",
            "inputs": [{"type": "address", "name": "to"},
                       {"type": "uint256", "name": "amount"}],
            "outputs": [{"type": "bool"}],
            "stateMutability": "nonpayable",
        }]),
        "Token",
        "seed",
    ]]}

    # Patch the tool-side ch reference: re-register tools with our fake_ch.
    from cerebro_mcp.tools.web3.rpc import register_rpc_tools
    captured: dict = {}

    class StubMCP:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    register_rpc_tools(StubMCP(), fake_ch)

    # Stub the Web3 contract so we never touch the network.
    fake_fn = MagicMock()
    fake_fn.abi = {"stateMutability": "nonpayable"}
    fake_contract = MagicMock()
    fake_contract.get_function_by_name.return_value = fake_fn
    fake_w3 = MagicMock()
    fake_w3.eth.contract.return_value = fake_contract
    monkeypatch.setattr(
        "cerebro_mcp.tools.web3.rpc.rpc_manager.for_block",
        lambda _b, _chain_id=100: fake_w3,
    )

    out = captured["contract_call_function"](
        "0x" + "ab" * 20,
        function_name="transfer",
        args=["0x" + "00" * 20, 1],
    )
    assert "only view/pure functions are allowed" in out


def test_resolver_cache_includes_target_no_cross_pollution(fake_ch):
    """Regression: a target=proxy load must not poison a later target=auto load
    for the same address. Earlier the in-memory cache only keyed on address,
    so once the proxy stub was cached, every subsequent call returned it."""
    fake_ch.execute_raw_cached.return_value = {"rows": []}

    proxy_addr = "0x" + "ab" * 20
    impl_addr = "0x" + "cd" * 20
    proxy_body = {
        "name": "ERC1967Proxy",
        "abi": [{"type": "function", "name": "implementation", "inputs": [],
                 "outputs": [{"type": "address"}], "stateMutability": "view"}],
        "implementations": [{"address_hash": impl_addr,
                             "name": "GnosisControllerToken"}],
    }
    impl_body = {
        "name": "GnosisControllerToken",
        "abi": [{"type": "function", "name": "balanceOf",
                 "inputs": [{"name": "account", "type": "address"}],
                 "outputs": [{"type": "uint256"}], "stateMutability": "view"}],
        "implementations": [],
    }

    # First call: target="proxy" → returns proxy stub.
    with patch.object(
        abi_resolver, "_fetch_blockscout", return_value=proxy_body,
    ):
        rec_proxy = resolve_abi(fake_ch, proxy_addr, target="proxy")
    assert rec_proxy.contract_name == "ERC1967Proxy"
    assert rec_proxy.implementation_address == ""

    # Second call: target="auto" must NOT return the cached proxy stub —
    # it must follow the implementation.
    with patch.object(
        abi_resolver, "_fetch_blockscout",
        side_effect=[proxy_body, impl_body],
    ):
        rec_auto = resolve_abi(fake_ch, proxy_addr, target="auto")
    assert rec_auto.contract_name == "GnosisControllerToken"
    assert rec_auto.implementation_address.lower() == impl_addr
    assert any(f["name"] == "balanceOf" for f in rec_auto.abi)


def test_block_identifier_coercion():
    """UI sends every block as a string; coercer turns numerics into ints."""
    from cerebro_mcp.tools.web3.rpc import _coerce_block_identifier
    assert _coerce_block_identifier("latest") == "latest"
    assert _coerce_block_identifier("LATEST") == "latest"
    assert _coerce_block_identifier(" finalized ") == "finalized"
    assert _coerce_block_identifier("30000000") == 30000000
    assert _coerce_block_identifier(30000000) == 30000000
    assert _coerce_block_identifier(None) == "latest"
    assert _coerce_block_identifier("") == "latest"
    # Hex passthrough — web3.py handles hex block numbers and hashes.
    assert _coerce_block_identifier("0x1c9c380") == "0x1c9c380"
