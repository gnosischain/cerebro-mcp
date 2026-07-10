"""Unit tests for the canonical model-search backend (semantic/search.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cerebro_mcp.semantic.search import (
    FieldDoc,
    ModelSearchIndex,
    reset_search_cache_for_tests,
    tokenize,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_search_cache_for_tests()
    yield
    reset_search_cache_for_tests()


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def test_tokenize_keeps_short_tokens_and_stems_plurals():
    assert "tx" in tokenize("tx count l1 mev")
    assert "l1" in tokenize("tx count l1 mev")
    # plural-strip symmetric stemming
    assert tokenize("bridges") == tokenize("bridge")
    assert tokenize("balances") == ["balance"]
    # double-s words are not butchered
    assert tokenize("address") == ["address"]
    # underscores and dots split
    assert tokenize("api_bridges_flows.daily") == ["api", "bridge", "flow", "daily"]


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _index() -> ModelSearchIndex:
    docs = [
        FieldDoc(
            name="api_bridges_flows_daily",
            name_text="api_bridges_flows_daily api bridges flows daily",
            aux_text="production bridges tier1 date bridge volume_usd",
            body_text="Daily bridge flow volume in USD across all Gnosis bridges.",
        ),
        FieldDoc(
            name="api_execution_gas_daily",
            name_text="api_execution_gas_daily api execution gas daily",
            aux_text="production execution date gas_used gas_price",
            body_text="Daily gas consumption on the execution layer.",
        ),
        FieldDoc(
            name="fct_bridges_kpis_snapshot",
            name_text="fct_bridges_kpis_snapshot fct bridges kpis snapshot",
            aux_text="bridges mart bridge tvl_usd",
            body_text="Point-in-time bridge KPI snapshot.",
        ),
    ]
    return ModelSearchIndex.from_field_docs(docs)


def test_exact_name_ranks_first():
    hits = _index().search("api_bridges_flows_daily")
    assert hits[0].name == "api_bridges_flows_daily"
    assert "name" in hits[0].matched_fields


def test_field_weighting_prefers_name_over_description():
    # "bridge" appears in the NAME of two models and only the description of
    # none — but "gas" is in the name of one and body of none.
    hits = _index().search("bridge flows")
    assert hits[0].name == "api_bridges_flows_daily"


def test_plural_query_matches_singular_index():
    hits = _index().search("bridge flow")  # singular query
    assert hits and hits[0].name == "api_bridges_flows_daily"
    hits2 = _index().search("bridges flows")  # plural query
    assert hits2 and hits2[0].name == "api_bridges_flows_daily"


def test_column_tokens_match_via_aux_field():
    hits = _index().search("gas_used")
    assert hits and hits[0].name == "api_execution_gas_daily"
    assert "aux" in hits[0].matched_fields


def test_fuzzy_fallback_on_typo():
    # "api_briges_flows_daily" (typo) — lexical match is weak, fuzzy fires.
    hits = _index().search("api_briges_flows_daily")
    assert hits
    assert hits[0].name == "api_bridges_flows_daily"


def test_empty_query_and_empty_index():
    assert _index().search("") == []
    assert ModelSearchIndex.from_field_docs([]).search("anything") == []


# ---------------------------------------------------------------------------
# Snapshot constructor + column matches + cache
# ---------------------------------------------------------------------------


def _fake_snapshot():
    return SimpleNamespace(
        registry_hash="hash-1",
        models={
            "api_execution_gas_daily": {
                "description": "Daily gas usage.",
                "tags": ["execution", "production"],
                "module": "execution",
                "columns": {
                    "date": {"data_type": "Date"},
                    "gas_used": {"data_type": "UInt64", "description": "total gas"},
                    "gas_price_avg": {"data_type": "Float64"},
                },
            },
            "consensus.blocks": {
                "description": "Raw consensus blocks.",
                "tags": [],
                "module": "consensus",
                "columns": {"slot": {"data_type": "UInt64"}},
            },
        },
    )


def test_for_snapshot_builds_and_caches():
    snap = _fake_snapshot()
    idx1 = ModelSearchIndex.for_snapshot(snap)
    idx2 = ModelSearchIndex.for_snapshot(snap)
    assert idx1 is idx2  # cached by registry_hash
    assert len(idx1) == 2


def test_column_matches_shape_and_cap():
    snap = _fake_snapshot()
    idx = ModelSearchIndex.for_snapshot(snap)
    hits = idx.search("gas", include_column_matches=True)
    assert hits[0].name == "api_execution_gas_daily"
    cols = hits[0].matched_columns
    assert cols and all(set(c) == {"name", "score"} for c in cols)
    assert len(cols) <= 5
    assert any(c["name"] == "gas_used" for c in cols)
    # not requested → empty
    hits_plain = idx.search("gas")
    assert hits_plain[0].matched_columns == []


def test_dotted_source_names_searchable():
    idx = ModelSearchIndex.for_snapshot(_fake_snapshot())
    hits = idx.search("consensus blocks")
    assert hits and hits[0].name == "consensus.blocks"
