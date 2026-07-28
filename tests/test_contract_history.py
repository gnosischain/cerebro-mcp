"""Historical contract reads: block sampling, error classification, fault tolerance."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cerebro_mcp.clients import abi_resolver, contract_history
from cerebro_mcp.clients.contract_history import (
    STATUS_ERROR,
    STATUS_NOT_DEPLOYED,
    STATUS_NO_STATE,
    STATUS_OK,
    STATUS_REVERTED,
    _classify_error,
    _resolve_relative,
    _sample_blocks,
    _to_float,
    read_function_history,
)


TOKEN = "0xe91D153E0b41518A2Ce8Dd3D7944Fa863463a97d"
TOKEN_ABI = [
    {"type": "function", "name": "totalSupply", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"type": "function", "name": "transfer",
     "inputs": [{"name": "to", "type": "address"},
                {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
]


@pytest.fixture(autouse=True)
def _clear_caches():
    abi_resolver.clear_caches()
    yield
    abi_resolver.clear_caches()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_sample_blocks_spans_the_range_inclusively():
    blocks = _sample_blocks(100, 200, 5)
    assert blocks[0] == 100
    assert blocks[-1] == 200
    assert len(blocks) == 5
    assert blocks == sorted(blocks)


def test_sample_blocks_is_evenly_spaced():
    blocks = _sample_blocks(0, 1000, 11)
    gaps = {b - a for a, b in zip(blocks, blocks[1:])}
    assert gaps == {100}


def test_sample_blocks_dedupes_when_range_is_shorter_than_points():
    """A 4-block range asked for 60 samples yields 4 blocks, not 60 repeats."""
    assert _sample_blocks(10, 13, 60) == [10, 11, 12, 13]


def test_sample_blocks_normalizes_reversed_bounds():
    assert _sample_blocks(200, 100, 3) == [100, 150, 200]


@pytest.mark.parametrize(
    "text,seconds",
    [("30d", 2_592_000), ("24h", 86_400), ("1w", 604_800), ("90 d", 7_776_000)],
)
def test_resolve_relative_windows(text, seconds):
    assert _resolve_relative(text) == seconds


@pytest.mark.parametrize("text", ["2024-01-01", "latest", "", "abc"])
def test_resolve_relative_rejects_non_windows(text):
    assert _resolve_relative(text) is None


def test_to_float_scales_by_decimals_and_skips_non_numbers():
    assert _to_float(10**18, 18) == 1.0
    assert _to_float(True, None) == 1.0
    assert _to_float("0xabc", None) is None
    assert _to_float(["a", "b"], None) is None


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Could not transact with/call contract function", STATUS_NOT_DEPLOYED),
        ("execution reverted: Pausable: paused", STATUS_REVERTED),
        ("missing trie node abc123", STATUS_NO_STATE),
        ("header not found", STATUS_NO_STATE),
        ("connection reset by peer", STATUS_ERROR),
    ],
)
def test_classify_error_separates_expected_from_fatal(message, expected):
    status, _ = _classify_error(Exception(message))
    assert status == expected


def test_not_deployed_message_does_not_echo_the_web3_jargon():
    """'Could not transact with/call contract function' reads as a scary bug;
    at a pre-deployment block it just means the contract wasn't there yet."""
    _, message = _classify_error(Exception("Could not transact with/call contract function"))
    assert "not deployed" in message


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

class _FakeRouter:
    def __init__(self, head=1000, lowest=1):
        self._head = head
        self._lowest = lowest
        self.standard = MagicMock()
        self.archive = MagicMock()

    def has_archive(self):
        return True

    def latest_block(self):
        return self._head

    def lowest_available_block(self, hi=None):
        return self._lowest


def _install_fakes(monkeypatch, *, call_side_effect, head=1000, deployed_at=1, lowest=1):
    """Wire the sweep against fakes: no ClickHouse, no network."""
    ch = MagicMock()
    ch.execute_raw_cached.return_value = {"rows": [[
        TOKEN, "", json.dumps(TOKEN_ABI), "WXDAI", "seed",
    ]]}

    router = _FakeRouter(head, lowest)
    monkeypatch.setattr(
        contract_history.RpcRouter, "for_chain", classmethod(lambda cls, cid: router)
    )
    monkeypatch.setattr(
        contract_history, "block_timestamp", lambda r, c, block: 1_700_000_000 + block
    )
    monkeypatch.setattr(
        contract_history, "first_block_with_code",
        lambda r, c, addr, floor, ceiling: deployed_at,
    )

    fn_factory = MagicMock()
    fn_factory.abi = TOKEN_ABI[0]
    bound = MagicMock()
    bound.call.side_effect = call_side_effect
    fn_factory.return_value = bound

    contract = MagicMock()
    contract.get_function_by_name.return_value = fn_factory
    contract.get_function_by_signature.return_value = fn_factory
    w3 = MagicMock()
    w3.eth.contract.return_value = contract
    monkeypatch.setattr(
        contract_history.rpc_manager, "archive", lambda chain_id=100: w3
    )
    return ch


def test_sweep_returns_one_point_per_sampled_block(monkeypatch):
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 1234)
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply", from_block=100, to_block=200, points=5
    )
    assert len(out["points"]) == 5
    assert out["ok_count"] == 5
    assert [p["block"] for p in out["points"]] == [100, 125, 150, 175, 200]
    assert all(p["value"] == 1234 for p in out["points"])
    assert out["signature"] == "totalSupply()"


def test_sweep_survives_a_mid_range_failure(monkeypatch):
    """One reverting block must not lose the other samples."""
    def _call(block_identifier=None, **kw):
        if block_identifier == 150:
            raise Exception("execution reverted: Pausable: paused")
        if block_identifier == 175:
            raise Exception("missing trie node deadbeef")
        return 42

    ch = _install_fakes(monkeypatch, call_side_effect=_call)
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply", from_block=100, to_block=200, points=5
    )

    by_block = {p["block"]: p for p in out["points"]}
    assert len(out["points"]) == 5
    assert by_block[150]["status"] == STATUS_REVERTED
    assert by_block[175]["status"] == STATUS_NO_STATE
    assert by_block[100]["status"] == STATUS_OK
    assert out["ok_count"] == 3
    assert any("archive node" in w for w in out["warnings"])


def test_range_is_clamped_to_the_deployment_block(monkeypatch):
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 1, deployed_at=150)
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply", from_block=100, to_block=200, points=3
    )
    assert out["from_block"] == 150
    assert min(p["block"] for p in out["points"]) == 150
    assert any("deployment block" in w for w in out["warnings"])


def test_undeployed_contract_fails_loudly(monkeypatch):
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 1)
    monkeypatch.setattr(
        contract_history, "first_block_with_code",
        lambda r, c, addr, floor, ceiling: None,
    )
    with pytest.raises(ValueError, match="No contract code"):
        read_function_history(
            ch, TOKEN, function_name="totalSupply", from_block=100, to_block=200
        )


def test_write_functions_are_rejected(monkeypatch):
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 1)
    fn_factory = MagicMock()
    fn_factory.abi = TOKEN_ABI[1]  # transfer -> nonpayable
    w3 = contract_history.rpc_manager.archive(100)
    w3.eth.contract.return_value.get_function_by_name.return_value = fn_factory
    with pytest.raises(ValueError, match="only view/pure"):
        read_function_history(ch, TOKEN, function_name="transfer", from_block=1, to_block=2)


def test_points_are_capped(monkeypatch):
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 1, head=10_000)
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply",
        from_block=1, to_block=9_000, points=100_000,
    )
    assert out["requested_points"] == 200
    assert len(out["points"]) == 200
    assert any("capped at 200" in w for w in out["warnings"])


def test_missing_function_name_is_an_error(monkeypatch):
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 1)
    with pytest.raises(ValueError, match="function_name or function_signature"):
        read_function_history(ch, TOKEN, from_block=1, to_block=2)


def test_head_is_backed_off_for_reorg_safety(monkeypatch):
    """`latest` shifts between requests within one sweep — pin behind head."""
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 1, head=1000)
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply", from_block=900, to_block="latest", points=3
    )
    assert out["to_block"] == 1000 - contract_history.HEAD_CONFIRMATIONS


def test_tuple_output_uses_output_index(monkeypatch):
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: [7, 9, 11])
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply",
        from_block=1, to_block=3, output_index=1,
    )
    assert all(p["value"] == 9 for p in out["points"])


def test_decimals_scale_only_the_plot_value_not_the_raw(monkeypatch):
    """uint256 exceeds JS's safe integer range, so the raw value ships as an
    exact string and only `value_float` is scaled for the y-axis."""
    ch = _install_fakes(monkeypatch, call_side_effect=lambda **kw: 5 * 10**18)
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply",
        from_block=1, to_block=3, decimals=18,
    )
    point = out["points"][0]
    assert point["value"] == "5000000000000000000"
    assert point["value_float"] == 5.0


# ---------------------------------------------------------------------------
# Non-contiguous chains (pruned nodes, the Celo L1->L2 migration)
# ---------------------------------------------------------------------------
#
# Regression: a Celo sweep died with "Block 1 not found". That endpoint serves
# genesis and everything from block 31,056,500 (the 2025-03-26 migration)
# onward, but answers null in between — and eth_getCode there errors outright.

CELO_FLOOR = 31_056_500


def test_range_floor_is_raised_to_the_lowest_served_block(monkeypatch):
    ch = _install_fakes(
        monkeypatch, call_side_effect=lambda **kw: 7,
        head=73_000_000, lowest=CELO_FLOOR, deployed_at=CELO_FLOOR,
    )
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply",
        from_block=1, to_block=73_000_000, points=4,
    )
    assert out["from_block"] >= CELO_FLOOR
    assert min(p["block"] for p in out["points"]) >= CELO_FLOOR
    assert any("not served by this endpoint" in w for w in out["warnings"])


def test_deployment_search_starts_at_the_served_floor_not_block_one(monkeypatch):
    """eth_getCode below the floor fails with "no state found" rather than
    returning empty, so bisecting from block 1 aborts the whole sweep."""
    seen = {}

    ch = _install_fakes(
        monkeypatch, call_side_effect=lambda **kw: 7,
        head=73_000_000, lowest=CELO_FLOOR,
    )

    def _record(r, c, addr, floor, ceiling):
        seen["floor"] = floor
        return CELO_FLOOR

    monkeypatch.setattr(contract_history, "first_block_with_code", _record)
    read_function_history(
        ch, TOKEN, function_name="totalSupply",
        from_block=1, to_block=73_000_000, points=3,
    )
    assert seen["floor"] == CELO_FLOOR


def test_contiguous_chain_keeps_the_requested_floor(monkeypatch):
    """Gnosis and friends start at block 1 — no warning, no clamping."""
    ch = _install_fakes(
        monkeypatch, call_side_effect=lambda **kw: 7, head=1000, lowest=1
    )
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply", from_block=100, to_block=200, points=3
    )
    assert out["from_block"] == 100
    assert not any("not served" in w for w in out["warnings"])


def test_time_range_reaching_past_the_floor_is_not_silently_truncated(monkeypatch):
    """`block_at_timestamp` clamps to the floor internally, so `lo` arrives
    ALREADY equal to it — the returned block alone cannot reveal that a 3-year
    request became a 1-year one. The warning must come from the timestamp."""
    ch = _install_fakes(
        monkeypatch, call_side_effect=lambda **kw: 7,
        head=73_000_000, lowest=CELO_FLOOR, deployed_at=CELO_FLOOR,
    )
    # block_timestamp is faked as 1_700_000_000 + block, and block_at_timestamp
    # is the real clamping one -> stub it to mimic the internal floor clamp.
    monkeypatch.setattr(
        contract_history, "block_at_timestamp",
        lambda rpc, ts, lo=1, hi=None: max(CELO_FLOOR, lo),
    )
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply", since="1000d", points=3
    )
    assert out["from_block"] == CELO_FLOOR
    assert any("not served by this endpoint" in w for w in out["warnings"])
    assert any("not where you asked" in w for w in out["warnings"])


def test_time_range_inside_available_history_warns_about_nothing(monkeypatch):
    ch = _install_fakes(
        monkeypatch, call_side_effect=lambda **kw: 7,
        head=73_000_000, lowest=CELO_FLOOR, deployed_at=CELO_FLOOR,
    )
    # Requested start sits well ABOVE the floor -> no truncation.
    monkeypatch.setattr(
        contract_history, "block_at_timestamp",
        lambda rpc, ts, lo=1, hi=None: 72_000_000,
    )
    out = read_function_history(
        ch, TOKEN, function_name="totalSupply", since="30d", points=3
    )
    assert out["from_block"] == 72_000_000
    assert not any("not served" in w for w in out["warnings"])
