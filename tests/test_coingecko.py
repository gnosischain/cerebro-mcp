"""Contract tests for the shared CoinGecko access layer.

Two invariants carry the weight here and both are safety properties, not
conveniences:

* **Never blocks a request.** Every ``*_nowait`` call returns cached data and
  hands a miss to the background executor.
* **Never fabricates.** An unlisted token is ABSENT from the overlay — never a
  placeholder URL, never a zero price. Downstream renders "unpriced" from that
  absence, which is the truthful reading. This matters concretely: the
  GnosisDAO treasury holds 19 distinct tokens spoofing the symbol ``USDC``, and
  a fabricated $0 would make them indistinguishable from the real one.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.models.mini_app import DatasetStats
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.tools.visualization import coingecko

TOKEN_A = "0x" + "aa" * 20
TOKEN_B = "0x" + "bb" * 20
TREASURY_TOKEN_RE = coingecko.re.compile(r"^token_address$")


@pytest.fixture(autouse=True)
def reset():
    coingecko.reset_caches_for_tests()
    yield
    coingecko.reset_caches_for_tests()


class InlineExecutor:
    """Run the background fetch synchronously so tests are deterministic."""

    def submit(self, fn, *args):
        fn(*args)


def _dataset(rows, columns=("chain_id", "token_address")):
    return CachedDataset(
        columns=list(columns),
        column_types=["int", "str"],
        rows=rows,
        stats=DatasetStats(row_count=len(rows), rows_returned=len(rows), mode="exact_capped"),
        sql="--",
        database="rpc_state_indexer",
        parameters={},
    )


# --------------------------------------------------------------- coin index


def test_coin_index_maps_contracts_per_platform(monkeypatch):
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {
        "ethereum": {TOKEN_A: "alpha"}, "xdai": {TOKEN_B: "beta"},
    })
    index, pending = coingecko.coin_index_nowait()
    assert (index, pending) == ({}, True)          # first call never blocks
    index, pending = coingecko.coin_index_nowait()
    assert pending is False
    assert index["ethereum"][TOKEN_A] == "alpha"


def test_coin_index_ignores_non_address_platform_entries(monkeypatch):
    payload = [
        {"id": "alpha", "platforms": {"ethereum": TOKEN_A.upper()}},
        {"id": "nonevm", "platforms": {"solana": "So11111111111111111111111111"}},
        {"id": "empty", "platforms": {"ethereum": ""}},
        {"id": "malformed", "platforms": "not-a-dict"},
    ]

    class Response:
        def raise_for_status(self): pass
        def json(self): return payload

    monkeypatch.setattr(coingecko.requests, "get", lambda *a, **k: Response())
    index = coingecko.fetch_coin_index()
    # Addresses are normalized to lowercase — the plane stores lowercase and a
    # case mismatch would silently miss every lookup.
    assert index == {"ethereum": {TOKEN_A: "alpha"}}


# ------------------------------------------------------------------- prices


def test_price_map_resolves_through_the_coin_index(monkeypatch):
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {"ethereum": {TOKEN_A: "alpha"}})
    monkeypatch.setattr(coingecko, "fetch_prices", lambda ids: {"alpha": 12.5})

    prices, pending = coingecko.price_map_nowait({1: {TOKEN_A}})
    assert pending is True                          # index + quote fetched inline
    prices, pending = coingecko.price_map_nowait({1: {TOKEN_A}})
    assert pending is False
    assert prices == {1: {TOKEN_A: 12.5}}


def test_unlisted_token_is_absent_never_zero(monkeypatch):
    """The load-bearing safety property: a spoofed token must not read as $0."""
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {"ethereum": {TOKEN_A: "alpha"}})
    monkeypatch.setattr(coingecko, "fetch_prices", lambda ids: {"alpha": 3.0})

    for _ in range(2):
        prices, _ = coingecko.price_map_nowait({1: {TOKEN_A, TOKEN_B}})
    assert prices[1] == {TOKEN_A: 3.0}
    assert TOKEN_B not in prices[1]


def test_zero_is_a_real_quote_and_survives(monkeypatch):
    """0.0 means 'worthless', which is NOT the same as 'unknown'."""
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {"ethereum": {TOKEN_A: "alpha"}})
    monkeypatch.setattr(coingecko, "fetch_prices", lambda ids: {"alpha": 0.0})

    for _ in range(2):
        prices, _ = coingecko.price_map_nowait({1: {TOKEN_A}})
    assert prices[1][TOKEN_A] == 0.0


def test_fetch_prices_rejects_non_numeric_quotes(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {
                "alpha": {"usd": 1.5},
                "beta": {"usd": "not-a-number"},
                "gamma": {"usd": True},     # bool is an int subclass — reject it
                "delta": {},
                "epsilon": "not-a-dict",
            }

    monkeypatch.setattr(coingecko.requests, "get", lambda *a, **k: Response())
    assert coingecko.fetch_prices(["alpha", "beta", "gamma", "delta", "epsilon"]) == {"alpha": 1.5}


def test_price_failure_is_swallowed_and_cached(monkeypatch):
    """A 429 must degrade to 'unpriced', never raise into the request path."""
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {"ethereum": {TOKEN_A: "alpha"}})

    calls = []

    def boom(ids):
        calls.append(ids)
        raise coingecko.requests.RequestException("429 Too Many Requests")

    monkeypatch.setattr(coingecko, "fetch_prices", boom)
    prices, _ = coingecko.price_map_nowait({1: {TOKEN_A}})
    prices, _ = coingecko.price_map_nowait({1: {TOKEN_A}})
    assert prices == {}
    assert calls, "the fetch was attempted"


def test_chains_without_a_platform_id_are_never_fetched(monkeypatch):
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {"ethereum": {TOKEN_A: "alpha"}})
    monkeypatch.setattr(coingecko, "fetch_prices", lambda ids: {"alpha": 1.0})
    prices, _ = coingecko.price_map_nowait({11155111: {TOKEN_A}})
    assert prices == {}


# ------------------------------------------------------------------ overlay


def test_price_overlay_is_tagged_for_a_historical_drop_in(monkeypatch):
    """The shape must let a historical source replace spot with no client change."""
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_coin_index", lambda: {"ethereum": {TOKEN_A: "alpha"}})
    monkeypatch.setattr(coingecko, "fetch_prices", lambda ids: {"alpha": 7.25})

    datasets = {"treasury_holdings": _dataset([[1, TOKEN_A], [1, TOKEN_B]])}
    for _ in range(2):
        overlay, pending = coingecko.build_price_overlay(
            datasets, token_columns=TREASURY_TOKEN_RE
        )
    assert overlay["kind"] == "spot"
    assert overlay["by_chain"]["1"] == {TOKEN_A: 7.25}
    assert TOKEN_B not in overlay["by_chain"]["1"]


def test_icon_overlay_omits_unknown_tokens(monkeypatch):
    monkeypatch.setattr(coingecko, "_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(coingecko, "fetch_icon_map", lambda chain: {
        TOKEN_A: "https://assets.coingecko.com/coins/images/1/thumb/a.png",
    })
    datasets = {"treasury_holdings": _dataset([[1, TOKEN_A], [1, TOKEN_B]])}
    for _ in range(2):
        overlay, pending = coingecko.build_icon_overlay(
            datasets, token_columns=TREASURY_TOKEN_RE
        )
    assert overlay["1"] == {TOKEN_A: "https://assets.coingecko.com/coins/images/1/thumb/a.png"}
    assert TOKEN_B not in overlay["1"]


def test_logo_urls_outside_the_allowlist_are_dropped():
    assert coingecko._safe_logo_url("https://example.com/x.png") == ""
    assert coingecko._safe_logo_url("http://assets.coingecko.com/x.png") == ""
    assert coingecko._safe_logo_url("https://assets.coingecko.com/x.png")
    assert coingecko._safe_logo_url(None) == ""
