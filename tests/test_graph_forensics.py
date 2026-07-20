"""Shared forensic provenance contract tests."""

from cerebro_mcp.tools.semantic.graph_explorer.forensics import (
    canonical_row_hash,
    forensic_scope,
    source_record,
)


def test_forensic_scope_v2_preserves_unknown_totals_and_identity():
    source = source_record(
        kind="rpc",
        name="eth_getTransactionReceipt",
        role="primary",
        status="ok",
        horizon=123,
        horizon_basis="receipt.blockNumber",
    )
    scope = forensic_scope(
        scope_id="transactions:7:test",
        request_id=7,
        status="partial",
        t0=None,
        t1=None,
        window_source="ignored_for_explicit_hash",
        data_horizon=123,
        sources=[source],
        rows_returned=21,
        rows_total=None,
        query_kind="transaction_receipt",
        evidence_class="rpc_receipt_logs",
        subjects=["0xhash"],
        result_row_hash=canonical_row_hash([["0xhash", 0]]),
    )

    assert scope["schema_version"] == 2
    assert scope["chain_id"] == 100
    assert scope["query_kind"] == "transaction_receipt"
    assert scope["evidence_class"] == "rpc_receipt_logs"
    assert scope["predicate"] == {
        "subjects": ["0xhash"],
        "t0": None,
        "t1": None,
        "as_of": None,
    }
    assert scope["coverage"]["rows"]["total"] is None
    assert scope["result_row_hash"] == canonical_row_hash([["0xhash", 0]])
    assert scope["app_commit"]
    assert scope["dbt_manifest_sha256"]
    assert scope["retrieved_at"].endswith("Z")
    assert source["contract_status"] == "ok"


def test_canonical_row_hash_is_stable_and_order_sensitive():
    assert canonical_row_hash([[1, "a"]]) == canonical_row_hash([[1, "a"]])
    assert canonical_row_hash([[1], [2]]) != canonical_row_hash([[2], [1]])
