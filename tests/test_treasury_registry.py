"""Hermetic tests for the reviewed treasury registry, labels and spam classifier.

The registry is the ONLY source of USD identity on the governance treasury plane,
so its invariants are load-bearing: a wrong address prices a fake, an overlapping
window double-counts a migration (Monerium EURe/GBPe v1+v2 report the same
balance), and a classifier regression either hides real holdings or shows spam.
"""

from __future__ import annotations

import re

import pytest

from cerebro_mcp.tools.visualization import treasury_registry as registry
from tests.treasury_spam_fixtures import TREASURY_SPAM_FIXTURES


def test_registry_invariants_hold():
    assert registry.validate() == []


def test_validate_rejects_what_it_exists_to_reject(monkeypatch):
    """A guard never seen failing is untested: break each invariant once."""
    good = registry.TOKENS
    gno = next(e for e in good if e.chain_id == 1 and e.symbol == "GNO")
    cases = {
        "lowercase": registry.TokenEntry(**{**gno.__dict__, "address": gno.address.upper().replace("0X", "0x")}),
        "priced without price_symbol": registry.TokenEntry(**{**gno.__dict__, "price_symbol": ""}),
        "empty window": registry.TokenEntry(**{**gno.__dict__, "valid_from": "2030-01-01", "valid_to": "2020-01-01"}),
        "quote/@/backslash": registry.TokenEntry(**{**gno.__dict__, "note": "it's"}),
    }
    for needle, broken in cases.items():
        monkeypatch.setattr(registry, "TOKENS", good + (broken,))
        problems = registry.validate()
        assert any(needle in p for p in problems), (needle, problems)
    overlap = registry.TokenEntry(**{**gno.__dict__, "valid_from": "2020-01-01"})
    monkeypatch.setattr(registry, "TOKENS", good + (overlap,))
    assert any("overlapping" in p for p in registry.validate())
    orphan = registry.TokenEntry(1, "0x" + "ab" * 20, "retired_mirror", "OLD", 18, "NOPE",
                                 "Other", valid_from="2024-01-01")
    monkeypatch.setattr(registry, "TOKENS", good + (orphan,))
    assert any("without a priced successor" in p for p in registry.validate())


def test_monerium_v1_is_priced_before_the_migration_and_a_mirror_after():
    """Both v1 contracts report v2's balance after 2024-08-25; valuing both would
    double-count ~$1.3M. v2 must be priced across the migration day."""
    eure_v1 = "0xcb444e90d8198415266c6a2724b7900fb12fc56e"
    eure_v2 = "0x420ca0f9b9b604ce0fd9c18ef134c705e5fa3430"
    gbpe_v1 = "0x5cb9073902f2035222b9749f8fb0c9bfe5527108"
    for v1 in (eure_v1, gbpe_v1):
        assert registry.entry_at(100, v1, "2024-08-24").role == "priced"
        assert registry.entry_at(100, v1, registry.MONERIUM_MIGRATION).role == "retired_mirror"
    assert registry.entry_at(100, eure_v2, registry.MONERIUM_MIGRATION).role == "priced"


def test_hub_symbols_are_upper_case_and_every_priced_entry_has_one():
    params = registry.bind_params()
    assert params["hub_syms"] == sorted(set(params["hub_syms"]))
    assert all(sym == sym.upper() and sym for sym in params["hub_syms"])
    for entry in registry.TOKENS:
        if entry.role == "priced":
            assert entry.price_symbol in params["hub_syms"], entry


def test_gno_is_the_registry_gno_on_both_chains():
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    for chain, address in gov.GNO_TOKENS.items():
        entry = registry.entry_at(chain, address, registry.PRESENT)
        assert entry is not None and entry.asset_key == "GNO" and entry.role == "priced"


def test_bind_params_are_parallel_and_json_serialisable():
    import json

    params = registry.bind_params()
    arrays = [key for key in params if key.startswith("reg_")]
    lengths = {len(params[key]) for key in arrays}
    assert lengths == {len(registry.TOKENS)}
    json.dumps(params)  # cache keys hash the parameters


def test_wallet_labels_cover_the_census_universe_with_safe_text():
    assert len(registry.WALLET_LABELS) == 23
    assert registry.WALLET_LABELS["0x604e4557e9020841f4e8eb98148de3d3cdea350c"] == "Gnosis Ltd."
    assert registry.wallet_label("0x458CD345B4C05E8DF39D0A07220FEB4EC19F5E6F") == "GNO Main Treasury"
    assert "README" in registry.WALLET_LABEL_SOURCE


def test_ltd_wallet_matches_the_governance_constant():
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    assert set(gov.LTD_WALLETS) <= set(registry.WALLET_LABELS)


@pytest.mark.parametrize(
    "chain,address,symbol,name,wallets,active,expected", TREASURY_SPAM_FIXTURES
)
def test_python_classifier_matches_the_fixture_table(
    chain, address, symbol, name, wallets, active, expected
):
    assert registry.classify(chain, address, symbol, name, wallets, active) == expected


def test_every_rule_is_exercised_by_the_fixture_table():
    reasons = {row[6] for row in TREASURY_SPAM_FIXTURES}
    assert reasons == set(registry.SPAM_REASONS) | {""}


def test_removing_any_rule_flips_at_least_one_fixture(monkeypatch):
    """Each rule must be load-bearing on the fixture table — otherwise a rule could
    be deleted (or never wired in SQL) with every test still green."""
    def classify_all():
        return [registry.classify(c, a, s, n, w, x) for c, a, s, n, w, x, _ in TREASURY_SPAM_FIXTURES]

    baseline = classify_all()
    monkeypatch.setattr(registry, "_LURE_PY", re.compile(r"(?!x)x"))
    assert classify_all() != baseline
    monkeypatch.undo()
    monkeypatch.setattr(registry, "_obfuscated_py", lambda text: False)
    assert classify_all() != baseline
    monkeypatch.undo()
    monkeypatch.setattr(registry, "verified_folds", lambda: frozenset())
    assert classify_all() != baseline
    monkeypatch.undo()
    monkeypatch.setattr(registry, "MASS_AIRDROP_MIN_WALLETS", 10_000)
    assert classify_all() != baseline
    monkeypatch.undo()
    monkeypatch.setattr(registry, "MAX_SYMBOL_LEN", 10_000)
    assert classify_all() != baseline


def test_fold_symbol_defeats_invisible_characters():
    assert registry.fold_symbol("US͏DC") == "USDC"
    assert registry.fold_symbol("usdc.e") == "USDC.E"
    assert registry.fold_symbol(None) == ""


def test_siblings_link_the_same_asset_and_skip_retired_mirrors():
    siblings = registry.siblings(1, "0x6810e776880c02933d47db1b9fc05908e5386b96")
    assert siblings == ["100:0x9c58bacc331c9aa871afd802db6379a98e80cedb"]
    eure_v2 = registry.siblings(100, "0x420ca0f9b9b604ce0fd9c18ef134c705e5fa3430")
    assert "1:0x39b8b6385416f4ca36a20319f70d28621895279d" in eure_v2
    assert all(not s.endswith("0xcb444e90d8198415266c6a2724b7900fb12fc56e") for s in eure_v2)


def test_search_resolves_labels_addresses_and_symbols():
    by_label = registry.search("swarm (bzz) 2")
    assert {c["identifier"] for c in by_label} == {
        "1:0x45a09fabd540b54f499212b3f579f0cf3393ab6b",
        "100:0x45a09fabd540b54f499212b3f579f0cf3393ab6b",
    }
    by_address = registry.search("0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f")
    assert {c["entity_type"] for c in by_address} == {"treasury_wallet"}
    by_symbol = registry.search("gno")
    assert {"1:0x6810e776880c02933d47db1b9fc05908e5386b96",
            "100:0x9c58bacc331c9aa871afd802db6379a98e80cedb"} <= {c["identifier"] for c in by_symbol}
    assert registry.search("") == []
