"""Shared chain registry: identity, RPC URL precedence, and the CoW subset contract."""
from __future__ import annotations

import pytest

from cerebro_mcp import chains
from cerebro_mcp.chains import (
    CHAINS,
    GNOSIS_CHAIN_ID,
    chain_rpc_urls,
    configured_chains,
    get_chain,
    has_rpc,
    resolve_chain,
    rpc_env_hint,
)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_every_chain_has_a_distinct_rpc_env_key():
    keys = [c.rpc_env_key for c in CHAINS.values()]
    assert all(keys), "every chain needs an env key to resolve its RPC URL"
    assert len(set(keys)) == len(keys)


def test_blockscout_chains_get_an_api_base_others_do_not():
    """`base_url` is the human site; only Blockscout exposes an ABI REST API."""
    for chain in CHAINS.values():
        if chain.explorer.provider == "blockscout":
            assert chain.explorer.api_base_url == f"{chain.explorer.base_url}/api/v2"
        else:
            assert chain.explorer.api_base_url == ""


def test_get_chain_rejects_unknown_id_and_names_the_known_ones():
    with pytest.raises(ValueError, match="Unknown chain_id 999"):
        get_chain(999)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 100),
        ("", 100),
        (1, 1),
        ("1", 1),
        ("gnosis", 100),
        ("GNOSIS", 100),
        ("mainnet", 1),
        ("Arbitrum One", 42161),
        ("arbitrum", 42161),
        ("bnb", 56),
        ("celo", 42220),
    ],
)
def test_resolve_chain_accepts_ids_env_keys_and_names(value, expected):
    assert resolve_chain(value).chain_id == expected


def test_resolve_chain_rejects_garbage():
    with pytest.raises(ValueError, match="Unknown chain"):
        resolve_chain("not-a-chain")


# ---------------------------------------------------------------------------
# RPC URL precedence
# ---------------------------------------------------------------------------

def _clear_urls(monkeypatch, chain_id: int) -> str:
    key = get_chain(chain_id).rpc_env_key
    monkeypatch.setattr(chains.settings, f"RPC_URL_{key}", "", raising=False)
    monkeypatch.setattr(chains.settings, f"RPC_URL_{key}_ARCHIVE", "", raising=False)
    return key


def test_single_url_serves_as_both_standard_and_archive(monkeypatch):
    """The configured nodes are archive nodes — one URL covers both roles."""
    key = _clear_urls(monkeypatch, 1)
    monkeypatch.setattr(chains.settings, f"RPC_URL_{key}", "https://eth.example", raising=False)
    assert chain_rpc_urls(1) == ("https://eth.example", "https://eth.example")


def test_explicit_archive_override_wins(monkeypatch):
    key = _clear_urls(monkeypatch, 1)
    monkeypatch.setattr(chains.settings, f"RPC_URL_{key}", "https://eth.example", raising=False)
    monkeypatch.setattr(
        chains.settings, f"RPC_URL_{key}_ARCHIVE", "https://eth-archive.example", raising=False
    )
    assert chain_rpc_urls(1) == ("https://eth.example", "https://eth-archive.example")


def test_unconfigured_chain_returns_empty_without_raising(monkeypatch):
    _clear_urls(monkeypatch, 43114)
    assert chain_rpc_urls(43114) == ("", "")
    assert has_rpc(43114) is False


def test_gnosis_legacy_settings_take_precedence(monkeypatch):
    """Existing deployments set GNOSIS_RPC_URL — it must keep winning."""
    key = _clear_urls(monkeypatch, GNOSIS_CHAIN_ID)
    monkeypatch.setattr(chains.settings, f"RPC_URL_{key}", "https://new.example", raising=False)
    monkeypatch.setattr(chains.settings, "GNOSIS_RPC_URL", "https://legacy.example")
    monkeypatch.setattr(chains.settings, "GNOSIS_ARCHIVE_RPC_URL", "https://legacy-archive.example")
    assert chain_rpc_urls(GNOSIS_CHAIN_ID) == (
        "https://legacy.example",
        "https://legacy-archive.example",
    )


def test_gnosis_falls_back_to_new_style_when_legacy_unset(monkeypatch):
    key = _clear_urls(monkeypatch, GNOSIS_CHAIN_ID)
    monkeypatch.setattr(chains.settings, f"RPC_URL_{key}", "https://new.example", raising=False)
    monkeypatch.setattr(chains.settings, "GNOSIS_RPC_URL", "")
    monkeypatch.setattr(chains.settings, "GNOSIS_ARCHIVE_RPC_URL", "")
    # Legacy archive unset -> the single new-style URL covers both roles.
    assert chain_rpc_urls(GNOSIS_CHAIN_ID) == ("https://new.example", "https://new.example")


def test_configured_chains_reflects_only_chains_with_urls(monkeypatch):
    for chain_id in CHAINS:
        _clear_urls(monkeypatch, chain_id)
    monkeypatch.setattr(chains.settings, "GNOSIS_RPC_URL", "")
    monkeypatch.setattr(chains.settings, "GNOSIS_ARCHIVE_RPC_URL", "")
    assert configured_chains() == []

    monkeypatch.setattr(chains.settings, "RPC_URL_BASE", "https://base.example", raising=False)
    monkeypatch.setattr(chains.settings, "RPC_URL_MAINNET", "https://eth.example", raising=False)
    # Registry order, not insertion order of the settings.
    assert [c.chain_id for c in configured_chains()] == [1, 8453]


def test_rpc_env_hint_names_the_var_not_the_url():
    """Endpoint URLs embed API keys — error messages must never carry them."""
    assert rpc_env_hint(43114) == "RPC_URL_AVALANCHE"
    assert "http" not in rpc_env_hint(1)


# ---------------------------------------------------------------------------
# CoW subset contract
# ---------------------------------------------------------------------------

def test_cow_chains_membership_and_order_are_locked():
    """Iteration order feeds `chain_id IN (...)` SQL and the mini-app dropdown."""
    from cerebro_mcp.tools.visualization.cow_explorer import COW_CHAINS

    assert list(COW_CHAINS) == [
        1, 100, 42161, 8453, 56, 137, 43114, 59144, 57073, 9745, 11155111,
    ]
    # Celo lives in the shared registry but CoW does not settle there.
    assert 42220 in CHAINS
    assert 42220 not in COW_CHAINS


def test_cow_chains_are_the_shared_registry_objects():
    from cerebro_mcp.tools.visualization.cow_explorer import COW_CHAINS

    for chain_id, chain in COW_CHAINS.items():
        assert chain is CHAINS[chain_id]
