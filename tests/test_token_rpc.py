"""The live ERC-20 metadata resolver behind the Pool Liquidity Explorer.

The state indexer catalogues tokens inside a census job's universe; the pool
jobs sweep pool addresses, not their assets, so it has metadata for 68 of the
3,400 tokens these pools hold. This module reads the rest off the chain.

Every test here is about one of three things: that a value never gets invented
(no placeholder symbol, no guessed decimals), that one bad target cannot take a
batch down, and that the cache stops a directory page re-asking the chain about
the same tokens on every load.
"""

from __future__ import annotations

import pytest
from eth_abi import decode as abi_decode, encode as abi_encode

from cerebro_mcp.rpc_scan.multicall import MULTICALL3_ADDRESS, selector
from cerebro_mcp.tools.visualization import token_rpc


TOKEN_A = "0x" + "a1" * 20
TOKEN_B = "0x" + "b2" * 20
TOKEN_C = "0x" + "c3" * 20
BLOCK = 48_296_053

SEL = {name: selector(name, []) for name in ("symbol", "decimals", "name")}
_BY_SELECTOR = {value: name for name, value in SEL.items()}


def _string_ret(text: str) -> bytes:
    return abi_encode(["string"], [text])


def _bytes32_ret(text: str) -> bytes:
    return text.encode("utf-8").ljust(32, b"\x00")


def _uint8_ret(value: int) -> bytes:
    return abi_encode(["uint8"], [value])


class FakeRpc:
    """A Multicall3 endpoint that answers from a per-token script.

    Decodes the real aggregate3 calldata rather than pattern-matching it, so a
    change to the encoder shows up here as a failure instead of being papered
    over by a stub that agrees with itself.
    """

    def __init__(self, script: dict[str, dict[str, bytes | None]], *, fail_on: int = -1):
        self.script = script
        self.fail_on = fail_on
        self.calls = 0
        self.batch_sizes: list[int] = []

    # -- RpcRouter surface --------------------------------------------------
    @property
    def standard(self):
        return self

    def latest_block(self) -> int:
        return BLOCK

    # -- RawRpcClient surface ----------------------------------------------
    def request(self, method: str, params: list):
        assert method == "eth_call"
        assert params[0]["to"] == MULTICALL3_ADDRESS
        assert params[1] == "latest"
        self.calls += 1
        data = bytes.fromhex(params[0]["data"].removeprefix("0x"))
        (entries,) = abi_decode(["(address,bool,bytes)[]"], data[4:])
        self.batch_sizes.append(len(entries) // 3)
        if self.calls == self.fail_on:
            raise RuntimeError("planned RPC failure")
        out = []
        for target, allow_failure, calldata in entries:
            assert allow_failure, "a reverting target must never abort the batch"
            field = _BY_SELECTOR[bytes(calldata)[:4]]
            answer = self.script.get(str(target).lower(), {}).get(field)
            out.append((answer is not None, answer or b""))
        return "0x" + abi_encode(["(bool,bytes)[]"], [out]).hex()


def _router(fake):
    return lambda chain_id: fake


@pytest.fixture(autouse=True)
def _clean_cache():
    token_rpc.reset_cache_for_tests()
    yield
    token_rpc.reset_cache_for_tests()


@pytest.fixture()
def patch_router(monkeypatch):
    def apply(fake):
        monkeypatch.setattr(token_rpc.RpcRouter, "for_chain",
                            staticmethod(_router(fake)))
        return fake
    return apply


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def test_a_standard_token_resolves(patch_router):
    fake = patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("sDAI"), "decimals": _uint8_ret(18),
                  "name": _string_ret("Savings xDAI")},
    }))
    resolved, stats = token_rpc.resolve_tokens(100, [TOKEN_A])
    meta = resolved[TOKEN_A]
    assert (meta.symbol, meta.decimals, meta.name) == ("sDAI", 18, "Savings xDAI")
    assert meta.encoding == "string"
    assert meta.block_number == BLOCK
    assert (stats.fetched, stats.unreadable, stats.from_cache) == (1, 0, 0)
    assert fake.calls == 1


def test_the_older_bytes32_shape_still_resolves(patch_router):
    """Not hypothetical: bytes32 is the ORIGINAL ERC-20 metadata shape and
    several long-lived tokens never migrated. A string-only decoder reports
    them as nameless, which is indistinguishable from a token that has no
    symbol at all."""
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": _bytes32_ret("MKR"), "decimals": _uint8_ret(18),
                  "name": _bytes32_ret("Maker")},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert resolved[TOKEN_A].symbol == "MKR"
    assert resolved[TOKEN_A].name == "Maker"
    assert resolved[TOKEN_A].encoding == "bytes32"


def test_bytes_that_are_not_text_are_not_passed_off_as_a_symbol(patch_router):
    """A 32-byte return that happens not to be UTF-8 would decode to mojibake.
    Showing that under a real-looking name is worse than showing the address."""
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": bytes(range(32)), "decimals": _uint8_ret(6),
                  "name": None},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert resolved[TOKEN_A].symbol is None
    assert resolved[TOKEN_A].decimals == 6  # the readable half still lands


@pytest.mark.parametrize("value,expected", [(0, 0), (18, 18), (77, 77), (78, None),
                                            (255, None)])
def test_an_impossible_decimals_is_rejected_rather_than_scaled_by(
    patch_router, value, expected
):
    """10**78 overflows a uint256, so a decimals beyond 77 cannot be a real
    scaling factor. Accepting it would turn a balance into a plausible-looking
    wrong number instead of an honest blank."""
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("X"), "decimals": _uint8_ret(value),
                  "name": None},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert resolved[TOKEN_A].decimals == expected


def test_an_empty_symbol_is_none_not_an_empty_string(patch_router):
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret(""), "decimals": _uint8_ret(18), "name": None},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert resolved[TOKEN_A].symbol is None


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


def test_a_non_token_in_the_set_does_not_abort_the_batch(patch_router):
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("GNO"), "decimals": _uint8_ret(18), "name": None},
        TOKEN_B: {},  # every call reverts
    }))
    resolved, stats = token_rpc.resolve_tokens(100, [TOKEN_A, TOKEN_B])
    assert set(resolved) == {TOKEN_A}
    assert stats.unreadable == 1


def test_an_unreadable_token_is_omitted_never_given_a_placeholder(patch_router):
    """The client renders a short address from the absence. A blank symbol in
    the overlay would make an unreadable token look like a nameless one."""
    patch_router(FakeRpc({TOKEN_B: {}}))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_B])
    assert resolved == {}


def test_a_dead_rpc_returns_empty_rather_than_raising(monkeypatch):
    """A labelling overlay must never be able to take a panel down with it."""
    def boom(chain_id):
        raise RuntimeError("no endpoint configured")

    monkeypatch.setattr(token_rpc.RpcRouter, "for_chain", staticmethod(boom))
    resolved, stats = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert resolved == {}
    assert "no endpoint" in stats.error


def test_one_failing_batch_does_not_discard_the_batches_that_worked(
    patch_router, monkeypatch
):
    monkeypatch.setattr(token_rpc, "TOKENS_PER_BATCH", 1)
    fake = patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("A"), "decimals": _uint8_ret(18), "name": None},
        TOKEN_B: {"symbol": _string_ret("B"), "decimals": _uint8_ret(6), "name": None},
    }, fail_on=1))
    resolved, stats = token_rpc.resolve_tokens(100, [TOKEN_A, TOKEN_B])
    assert fake.calls == 2
    assert len(resolved) == 1
    assert stats.error


def test_malformed_addresses_are_rejected_before_any_rpc(patch_router):
    fake = patch_router(FakeRpc({}))
    resolved, stats = token_rpc.resolve_tokens(100, ["0xnothex", "", "not-an-address"])
    assert resolved == {} and stats.requested == 0
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_a_resolved_token_is_never_re_read(patch_router):
    """ERC-20 metadata is immutable, and a directory page re-asking the chain
    about the same 200 tokens on every load is the cost this cache exists to
    remove."""
    fake = patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("GNO"), "decimals": _uint8_ret(18), "name": None},
    }))
    token_rpc.resolve_tokens(100, [TOKEN_A])
    resolved, stats = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert fake.calls == 1
    assert (stats.from_cache, stats.fetched) == (1, 0)
    assert resolved[TOKEN_A].symbol == "GNO"


def test_an_unreadable_token_is_not_retried_within_the_negative_ttl(patch_router):
    fake = patch_router(FakeRpc({TOKEN_B: {}}))
    token_rpc.resolve_tokens(100, [TOKEN_B])
    token_rpc.resolve_tokens(100, [TOKEN_B])
    assert fake.calls == 1


def test_the_negative_cache_expires(patch_router, monkeypatch):
    fake = patch_router(FakeRpc({TOKEN_B: {}}))
    token_rpc.resolve_tokens(100, [TOKEN_B])
    monkeypatch.setattr(token_rpc, "NEGATIVE_TTL_SECONDS", -1)
    token_rpc.resolve_tokens(100, [TOKEN_B])
    assert fake.calls == 2


def test_force_refresh_bypasses_the_cache(patch_router):
    fake = patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("GNO"), "decimals": _uint8_ret(18), "name": None},
    }))
    token_rpc.resolve_tokens(100, [TOKEN_A])
    token_rpc.resolve_tokens(100, [TOKEN_A], force_refresh=True)
    assert fake.calls == 2


def test_the_cache_is_keyed_per_chain(patch_router):
    fake = patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("GNO"), "decimals": _uint8_ret(18), "name": None},
    }))
    token_rpc.resolve_tokens(100, [TOKEN_A])
    token_rpc.resolve_tokens(1, [TOKEN_A])
    assert fake.calls == 2, "the same address is a different token on another chain"


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_a_batch_carries_at_most_the_configured_token_count(patch_router):
    tokens = ["0x%040x" % n for n in range(1, 451)]
    fake = patch_router(FakeRpc({
        t: {"symbol": _string_ret("T"), "decimals": _uint8_ret(18), "name": None}
        for t in tokens
    }))
    token_rpc.resolve_tokens(100, tokens)
    assert max(fake.batch_sizes) <= token_rpc.TOKENS_PER_BATCH
    assert sum(fake.batch_sizes) == 450


def test_an_oversized_request_is_truncated_and_says_so(patch_router, monkeypatch):
    """Silently dropping the tail would leave the extra tokens looking like
    tokens the chain could not answer for."""
    monkeypatch.setattr(token_rpc, "MAX_TOKENS_PER_CALL", 2)
    tokens = ["0x%040x" % n for n in range(1, 6)]
    patch_router(FakeRpc({
        t: {"symbol": _string_ret("T"), "decimals": _uint8_ret(18), "name": None}
        for t in tokens
    }))
    resolved, stats = token_rpc.resolve_tokens(100, tokens)
    assert len(resolved) == 2
    assert stats.truncated is True


def test_the_overlay_entry_carries_its_provenance(patch_router):
    """An indexer value is verified at a pinned finalized block with a
    publication behind it; this one is current chain state with neither. The
    consumer has to be able to tell them apart without guessing."""
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("GNO"), "decimals": _uint8_ret(18), "name": None},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A])
    entry = resolved[TOKEN_A].as_overlay()
    assert entry["source"] == "rpc"
    assert entry["block_number"] == BLOCK
    assert set(entry) == {"symbol", "name", "decimals", "block_number", "encoding",
                          "source"}


def test_addresses_are_normalized_before_lookup(patch_router):
    fake = patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("GNO"), "decimals": _uint8_ret(18), "name": None},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A.upper().replace("0X", "0x")])
    assert TOKEN_A in resolved
    token_rpc.resolve_tokens(100, [TOKEN_A])
    assert fake.calls == 1, "case must not create a second cache entry"


def test_an_absurdly_long_symbol_is_rejected(patch_router):
    """A token is free to return a kilobyte of text. Rendering it verbatim is a
    layout break at best and a spoofing surface at worst."""
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("A" * 500), "decimals": _uint8_ret(18),
                  "name": None},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert resolved[TOKEN_A].symbol is None


def test_a_symbol_of_control_characters_is_rejected(patch_router):
    """Bytes 0x00-0x1f are valid UTF-8, so these decode cleanly and only an
    isprintable() check keeps them out of the UI."""
    patch_router(FakeRpc({
        TOKEN_A: {"symbol": _string_ret("\x01\x02\x03"), "decimals": _uint8_ret(18),
                  "name": None},
    }))
    resolved, _ = token_rpc.resolve_tokens(100, [TOKEN_A])
    assert resolved[TOKEN_A].symbol is None
