"""Focused forensic-safety tests for Transaction Detail backend contracts."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.semantic.tx_queries import (
    build_all_history_tx_discovery_chunk_sql,
    build_legs_sql,
)
from cerebro_mcp.tools.semantic.graph_explorer import constants
from cerebro_mcp.tools.semantic.graph_explorer.forensics import (
    reset_source_contract_cache_for_tests,
    validate_source_contract,
)
from cerebro_mcp.tools.semantic.graph_explorer.state import (
    empty_dataset,
    empty_state,
)
from cerebro_mcp.tools.semantic.graph_explorer.transactions import (
    TRANSFER_TOPIC0,
    _DISCOVERY_HORIZON_QUERY_BUDGET,
    _authoritative_leg_total,
    _append_only_discovery_delta,
    _coverage_continuation_boundary,
    _decode_discovery_cursor,
    _discover_address_direct_transactions_rpc,
    _discover_address_transactions_execution,
    _discover_address_transactions_rpc,
    _encode_discovery_cursor,
    _encode_coverage_discovery_cursor,
    _legs_from_receipts,
    _newest_uncovered_retry_slice,
    _uncovered_requires_smaller_tile,
    register_transaction_tools,
)
from cerebro_mcp.tools.visualization import mini_apps


TX_HASH = "0x" + "ab" * 32
TOKEN = "0x" + "10" * 20
SOURCE = "0x" + "20" * 20
TARGET = "0x" + "30" * 20


@pytest.fixture(autouse=True)
def reset_state():
    reset_cache_for_tests()
    reset_source_contract_cache_for_tests()
    mini_apps.reset_views_for_tests()
    yield
    reset_cache_for_tests()
    reset_source_contract_cache_for_tests()
    mini_apps.reset_views_for_tests()


class TxStubCH:
    def __init__(
        self,
        *,
        price: float | None = 2.0,
        validate_sources: bool = True,
        missing_relations: set[str] | None = None,
        sql_fallback_rows: list[list[Any]] | None = None,
        sql_leg_total: int = 7,
    ):
        self.price = price
        self.validate_sources = validate_sources
        self.missing_relations = missing_relations or set()
        self.sql_fallback_rows = sql_fallback_rows or []
        self.sql_leg_total = sql_leg_total
        self.sql_leg_count_calls = 0
        self.calls: list[dict[str, Any]] = []

    def run_query(
        self,
        sql: str,
        database: str = "dbt",
        requested_max_rows: int = 100,
        audience: str = "tool",
        fetch_mode: str = "auto",
        parameters: dict[str, Any] | None = None,
    ) -> ExecutedQuery:
        params = parameters or {}
        self.calls.append({"sql": sql, "parameters": dict(params)})

        def result(columns, rows):
            return ExecutedQuery(
                sql, sql, database, columns, rows, len(rows), 0.0, "rows", []
            )

        if "FROM system.columns" in sql:
            required = list(params.get("required") or [])
            relation = ".".join(
                part for part in (params.get("database"), params.get("table")) if part
            )
            relation_available = self.validate_sources
            rows = (
                [[name, "String"] for name in required]
                if relation_available and relation not in self.missing_relations
                else []
            )
            return result(["name", "type"], rows)
        if (
            "max(toString(`block_timestamp`))" in sql
            or "toString(max(`block_timestamp`))" in sql
        ):
            return result(["source_horizon"], [["2026-07-19 01:25:00"]])
        if "metadata_modification_time" in sql:
            return result(["source_horizon"], [["2026-07-19 08:13:13"]])
        if "max(toString(`date`))" in sql or "toString(max(`date`))" in sql:
            return result(["source_horizon"], [["2026-07-18"]])
        if "'execution.logs' AS relation" in sql:
            return result(
                ["relation", "horizon", "block_horizon"],
                [
                    ["execution.logs", "2026-07-19 00:00:00", 46_999_000],
                    ["execution_live.logs", "2026-07-19 01:00:00", 47_000_000],
                    ["execution.transactions", "2026-07-19 00:00:00", 46_999_000],
                    ["execution_live.transactions", "2026-07-19 01:00:00", 47_000_000],
                ],
            )
        if "stg_pools__tokens_meta" in sql:
            return result(
                ["token_address", "sym", "dec"], [[TOKEN, "TOK", 18]]
            )
        if "int_execution_token_prices_daily" in sql:
            return result(
                ["symbol", "price_date", "price"],
                [["TOK", "2026-07-18", self.price]],
            )
        if "count() AS legs_total" in sql:
            self.sql_leg_count_calls += 1
            # The retired/indexed path saw seven; the receipt below sees nine.
            return result(["legs_total", "tx_total"], [[self.sql_leg_total, 1]])
        if "AS raw_amount" in sql:
            return result(
                [
                    "transaction_hash",
                    "log_index",
                    "block_number",
                    "transaction_index",
                    "block_timestamp",
                    "source_id",
                    "target_id",
                    "token_address",
                    "raw_amount",
                ],
                self.sql_fallback_rows,
            )
        if "token_contract" in sql:
            return result(["token_contract"], [])
        return result([], [])


def test_quoted_source_contract_normalizes_each_identifier_component():
    class MetadataCH:
        def __init__(self):
            self.calls = []

        def execute_raw(self, sql, database="dbt", parameters=None):
            self.calls.append((database, parameters))
            return {"columns": ["name", "type"], "rows": [["source_id", "String"]]}

    ch = MetadataCH()
    checked = validate_source_contract(
        ch,
        "`dbt`.`some_relation`",
        ["`source_id`"],
    )

    assert checked["ok"] is True
    assert checked["relation"] == "dbt.some_relation"
    assert ch.calls == [
        ("dbt", {"database": "dbt", "table": "some_relation", "required": ["source_id"]})
    ]


def test_source_contract_rejects_present_but_unusable_column_type():
    class MetadataCH:
        def execute_raw(self, sql, database="dbt", parameters=None):
            return {
                "columns": ["name", "type"],
                "rows": [["source_id", "Nullable(Nothing)"]],
            }

    checked = validate_source_contract(
        MetadataCH(), "dbt.some_relation", ["source_id"]
    )

    assert checked["ok"] is False
    assert checked["missing_columns"] == []
    assert checked["incompatible_columns"] == ["source_id"]
    assert "Nullable(Nothing)" in checked["error"]


def _view_and_tool(ch: TxStubCH):
    view_id = mini_apps.create_view(constants.GRAPH_EXPLORER_APP_ID, "Transactions")
    mini_apps.set_view_state(view_id, empty_state("Transactions"))
    mini_apps.replace_view_datasets(
        view_id,
        {
            "tx_nodes": empty_dataset("tx_nodes", constants.TX_LEG_NODES_COLUMNS),
            "tx_legs": empty_dataset("tx_legs", constants.TX_LEG_EDGES_COLUMNS),
            "tx_list": empty_dataset("tx_list", constants.TX_LIST_COLUMNS),
        },
    )
    server = FastMCP("graph-transactions-test")
    tools = register_transaction_tools(server, ch)
    return view_id, tools["load_graph_transactions"]


def _receipt_rows(count: int = 9) -> list[list[Any]]:
    return [
        [
            TX_HASH,
            index,
            12345,
            7,
            "2026-07-18 12:00:00",
            SOURCE,
            TARGET,
            TOKEN,
            "",
            10**18,
            None,
            "success",
        ]
        for index in range(count)
    ]


def test_sql_fallback_query_is_raw_chain_only():
    sql, _params = build_legs_sql(
        tx_hashes=[TX_HASH], block_lo=12345, block_hi=12345, limit=100
    )
    assert "AS raw_amount" in sql
    assert "stg_pools__tokens_meta" not in sql
    assert "int_execution_token_prices_daily" not in sql
    assert "symbol" not in sql
    assert "amount_usd" not in sql


def test_address_discovery_page_merges_existing_transactions_and_transfer_logs():
    sql, params = build_all_history_tx_discovery_chunk_sql(
        address_ids=[SOURCE],
        t0="2018-10-08 00:00:00",
        t1_exclusive="2026-07-20 00:00:00",
        limit=25,
    )
    assert "FROM execution.logs" in sql
    assert "FROM execution_live.logs" in sql
    assert "FROM execution.transactions" in sql
    assert "FROM execution_live.transactions" in sql
    assert "topic1 IN {topics:Array(String)}" in sql
    assert "from_address IN {addresses:Array(String)}" in sql
    assert "to_address IN {addresses:Array(String)}" in sql
    assert "transfer_candidates" in sql
    assert "direct_candidates" in sql
    assert "GROUP BY transaction_hash" in sql
    assert "count() OVER () AS chunk_transaction_total" in sql
    assert params["addresses"] == [SOURCE[2:]]


def test_rpc_address_discovery_scans_genesis_to_head_and_deduplicates_self_transfers():
    padded_source = "0x" + "0" * 24 + SOURCE[2:]
    padded_target = "0x" + "0" * 24 + TARGET[2:]
    tx_one = "0x" + "41" * 32
    tx_self = "0x" + "42" * 32

    class FakeRpc:
        def __init__(self):
            self.log_calls: list[dict[str, Any]] = []

        def request(self, method: str, params: list[Any]):
            if method == "eth_blockNumber":
                return "0x13"
            assert method == "eth_getLogs"
            query = params[0]
            self.log_calls.append(query)
            lo = int(query["fromBlock"], 16)
            hi = int(query["toBlock"], 16)
            # Exercise adaptive transport splitting without altering the
            # genesis-to-head evidence predicate.
            if hi - lo + 1 > 10:
                raise RuntimeError("provider window limit")
            if not (lo <= 12 <= hi):
                return []
            outbound = query["topics"][1] == padded_source
            normal = {
                "transactionHash": tx_one,
                "logIndex": "0x1",
                "blockNumber": "0xc",
                "transactionIndex": "0x3",
                "address": TOKEN,
                "topics": [TRANSFER_TOPIC0, padded_source, padded_target],
            }
            self_transfer = {
                "transactionHash": tx_self,
                "logIndex": "0x2",
                "blockNumber": "0xc",
                "transactionIndex": "0x4",
                "address": TOKEN,
                "topics": [TRANSFER_TOPIC0, padded_source, padded_source],
            }
            return [normal, self_transfer] if outbound else [self_transfer]

    rpc = FakeRpc()
    rows, head = _discover_address_transactions_rpc(
        SOURCE,
        router=SimpleNamespace(standard=rpc),
        chunk_size=20,
        min_chunk_size=1,
        max_workers=1,
    )

    assert head == 19
    assert rows == [
        [tx_self, 12, 4, "", 1, 1],
        [tx_one, 12, 3, "", 1, 1],
    ]
    assert min(int(call["fromBlock"], 16) for call in rpc.log_calls) == 0
    assert max(int(call["toBlock"], 16) for call in rpc.log_calls) == 19


def test_rpc_direct_tail_scans_only_the_bounded_index_gap():
    direct_hash = "0x" + "43" * 32
    unrelated_hash = "0x" + "44" * 32

    class FakeRpc:
        def __init__(self):
            self.blocks: list[int] = []

        def request(self, method: str, params: list[Any]):
            assert method == "eth_getBlockByNumber"
            block_number = int(params[0], 16)
            assert params[1] is True
            self.blocks.append(block_number)
            return {
                "timestamp": hex(1_700_000_000 + block_number),
                "transactions": [
                    {
                        "hash": direct_hash,
                        "blockNumber": hex(block_number),
                        "transactionIndex": "0x2",
                        "from": SOURCE,
                        "to": TARGET,
                    },
                    {
                        "hash": unrelated_hash,
                        "blockNumber": hex(block_number),
                        "transactionIndex": "0x3",
                        "from": TARGET,
                        "to": TOKEN,
                    },
                ],
            }

    rpc = FakeRpc()
    rows = _discover_address_direct_transactions_rpc(
        SOURCE,
        after_block=100,
        through_block=102,
        router=SimpleNamespace(standard=rpc),
        max_workers=1,
    )

    assert rpc.blocks == [100, 101, 102]
    assert [row[0] for row in rows] == [direct_hash, direct_hash, direct_hash]
    assert {row[1] for row in rows} == {100, 101, 102}


def test_sql_fallback_keeps_raw_legs_when_enrichment_relations_are_missing():
    raw_fallback_row = [
        TX_HASH,
        3,
        12345,
        7,
        "2026-07-18 12:00:00",
        SOURCE,
        TARGET,
        TOKEN,
        str(10**18),
    ]
    ch = TxStubCH(
        missing_relations={
            "dbt.stg_pools__tokens_meta",
            "dbt.int_execution_token_prices_daily",
        },
        sql_fallback_rows=[raw_fallback_row],
        sql_leg_total=1,
    )
    view_id, load = _view_and_tool(ch)

    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=([], [TX_HASH], {}, {TX_HASH: 12345}, []),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH])

    legs = result.structuredContent["datasets"]["tx_legs"]["preview_rows"]
    assert len(legs) == 1
    assert legs[0][1:4] == [SOURCE, TARGET, TX_HASH]
    assert legs[0][9] == ""
    assert legs[0][10] is None
    assert legs[0][11] is None
    assert legs[0][14] == "unknown"
    assert legs[0][15] == str(10**18)

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["status"] == "partial"
    assert scope["verification"]["status"] == "unverified"
    assert scope["legs_returned"] == scope["legs_total"] == 1
    fallback_sources = [
        source for source in scope["sources"] if source["role"] == "fallback"
    ]
    assert fallback_sources
    assert all(source["status"] == "ok" for source in fallback_sources)
    enrichment_sources = [
        source for source in scope["sources"] if source["role"] == "enrichment"
    ]
    assert enrichment_sources
    assert all(source["status"] == "error" for source in enrichment_sources)

    fallback_query = next(
        call for call in ch.calls if "AS raw_amount" in call["sql"]
    )
    assert "stg_pools__tokens_meta" not in fallback_query["sql"]
    assert "int_execution_token_prices_daily" not in fallback_query["sql"]


def test_null_token_decimals_preserve_raw_amount_without_inventing_normalized_value():
    class NullDecimalsCH(TxStubCH):
        def run_query(self, sql: str, *args, **kwargs) -> ExecutedQuery:
            if (
                "FROM dbt.stg_pools__tokens_meta" in sql
                and "SELECT token_address" in sql
            ):
                return ExecutedQuery(
                    sql,
                    sql,
                    "dbt",
                    ["token_address", "sym", "dec"],
                    [[TOKEN, "TOK", None]],
                    1,
                    0.0,
                    "rows",
                    [],
                )
            return super().run_query(sql, *args, **kwargs)

    ch = NullDecimalsCH(price=2.0)
    view_id, load = _view_and_tool(ch)
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=(
            _receipt_rows(1), [], {TX_HASH: "success"}, {TX_HASH: 12345}, []
        ),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH])

    leg = result.structuredContent["datasets"]["tx_legs"]["preview_rows"][0]
    assert leg[9] == "TOK"
    assert leg[10] is None
    assert leg[11] is None
    assert leg[15] == str(10**18)

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["status"] == "partial"
    metadata_source = next(
        source
        for source in scope["sources"]
        if source["name"] == "dbt.stg_pools__tokens_meta"
    )
    assert metadata_source["status"] == "partial"
    assert "decimals missing" in metadata_source["error"]
    assert any("normalized amounts" in warning for warning in scope["warnings"])


def test_whitelist_symbol_price_is_address_accurate_when_fully_priced():
    # int_execution_token_prices_daily is keyed by (symbol, date), but the
    # tokens_whitelist seed maps every token_address to a UNIQUE symbol, and the
    # metadata symbols come from that same seed, so a resolved price is
    # address-accurate -- NOT a blanket "symbol-only, cannot distinguish
    # same-symbol contracts" caveat. A fully-priced load must read "ok" with no
    # false same-symbol alarm.
    ch = TxStubCH(price=2.0)
    view_id, load = _view_and_tool(ch)
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=(
            _receipt_rows(1), [], {TX_HASH: "success"}, {TX_HASH: 12345}, []
        ),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH])

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    price_source = next(
        source
        for source in scope["sources"]
        if source["name"] == "dbt.int_execution_token_prices_daily"
    )
    assert price_source["status"] == "ok"
    assert "token_address" not in (price_source.get("error") or "")
    assert not any(
        "same-symbol" in warning or "exposes no token_address" in warning
        for warning in scope["warnings"]
    )
    assert scope["verification"]["status"] == "verified"
    assert scope["unpriced_leg_count"] == 0
    # The value is present and, per the bijection above, address-accurate.
    assert result.structuredContent["datasets"]["tx_legs"]["preview_rows"][0][11] == 2.0


def test_receipt_decoder_captures_malformed_transfer_data_without_zero_row():
    topic_address = lambda address: "0x" + "0" * 24 + address[2:]

    class ReceiptClient:
        def request(self, method, params):
            if method == "eth_getBlockByNumber":
                return {"timestamp": hex(1_752_837_600)}
            assert method == "eth_getTransactionReceipt"
            return {
                "blockNumber": hex(12345),
                "transactionIndex": hex(7),
                "status": "0x1",
                "logs": [
                    {
                        "logIndex": "0x1",
                        "topics": [
                            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                            topic_address(SOURCE),
                            topic_address(TARGET),
                        ],
                        "address": TOKEN,
                        "data": "0x" + f"{10**18:064x}",
                    },
                    {
                        "logIndex": "0x2",
                        "topics": [
                            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                            topic_address(SOURCE),
                            topic_address(TARGET),
                        ],
                        "address": TOKEN,
                        "data": "0xnot-a-uint256",
                    },
                ],
            }

    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._router",
        return_value=SimpleNamespace(standard=ReceiptClient()),
    ):
        raw_receipts: list[list[Any]] = []
        rows, unresolved, statuses, blocks, failures = _legs_from_receipts(
            [TX_HASH], raw_receipt_rows=raw_receipts
        )

    assert unresolved == []
    assert statuses == {TX_HASH: "success"}
    assert blocks == {TX_HASH: 12345}
    assert len(rows) == 1
    assert rows[0][9] == 10**18
    assert all(row[9] != 0 for row in rows)
    assert failures == [
        {
            "transaction_hash": TX_HASH,
            "log_index": 2,
            "error": "data is not a 32-byte uint256 ABI word",
            "raw_data": "0xnot-a-uint256",
        }
    ]
    assert len(raw_receipts) == 1
    raw = raw_receipts[0]
    assert raw[0] == TX_HASH
    parsed_receipt = json.loads(raw[1])
    assert len(parsed_receipt["logs"]) == 2
    assert raw[2] == hashlib.sha256(raw[1].encode("utf-8")).hexdigest()
    canonical_logs = json.dumps(
        parsed_receipt["logs"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert raw[3] == hashlib.sha256(canonical_logs.encode("utf-8")).hexdigest()


def test_receipt_decode_failure_marks_scope_partial_with_authoritative_log_total():
    ch = TxStubCH(price=2.0)
    view_id, load = _view_and_tool(ch)
    failure = {
        "transaction_hash": TX_HASH,
        "log_index": 8,
        "error": "data is not a 32-byte uint256 ABI word",
        "raw_data": "0xbroken",
    }
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=(
            _receipt_rows(1),
            [],
            {TX_HASH: "success"},
            {TX_HASH: 12345},
            [failure],
        ),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH], request_id=42)

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["status"] == "partial"
    assert scope["verification"]["status"] == "unverified"
    assert scope["exact"] is False
    assert scope["legs_returned"] == 1
    assert scope["legs_total"] == 2
    assert scope["truncation"]["truncated"] is False
    assert scope["decode_failures"] == [failure]
    assert scope["sources"][0]["status"] == "partial"
    assert "failed ABI decoding" in scope["verification"]["method"]
    legs = result.structuredContent["datasets"]["tx_legs"]["preview_rows"]
    assert len(legs) == 1
    assert legs[0][15] == str(10**18)
    assert legs[0][10] != 0


def test_receipt_nine_legs_override_sql_seven_and_preserve_status_raw_amount():
    ch = TxStubCH(price=2.0)
    view_id, load = _view_and_tool(ch)
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=(
            _receipt_rows(9), [], {TX_HASH: "success"}, {TX_HASH: 12345}, []
        ),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH], request_id=41)

    assert result.isError is not True
    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert _authoritative_leg_total(
        receipt_complete=True, receipt_count=9, sql_count=7
    ) == 9
    assert scope["legs_returned"] == scope["legs_total"] == 9
    assert scope["exact"] is True
    assert scope["verification"]["status"] == "verified"
    assert scope["window_source"] == "ignored_for_explicit_hash"
    assert scope["sources"][0]["kind"] == "rpc"
    assert scope["sources"][0]["role"] == "primary"
    enrichment = {
        source["name"]: source
        for source in scope["sources"]
        if source["role"] == "enrichment"
    }
    assert enrichment["dbt.stg_pools__tokens_meta"]["horizon"] == (
        "2026-07-19 08:13:13"
    )
    assert enrichment["dbt.int_execution_token_prices_daily"]["horizon"] == (
        "2026-07-18"
    )
    assert all(source["fetched_at"] for source in enrichment.values())
    assert all("whitelist" not in text for text in scope["residuals"])
    # A complete receipt never pays the seven-second SQL COUNT penalty and can
    # never be overwritten by its stale/incomplete value.
    assert ch.sql_leg_count_calls == 0

    legs = result.structuredContent["datasets"]["tx_legs"]["preview_rows"]
    assert len(legs) == 9
    assert all(row[11] == 2.0 for row in legs)
    assert all(row[14] == "success" for row in legs)
    assert all(row[15] == str(10**18) for row in legs)


def test_missing_price_is_null_not_zero_and_scope_discloses_coverage():
    ch = TxStubCH(price=None)
    view_id, load = _view_and_tool(ch)
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=(
            _receipt_rows(2), [], {TX_HASH: "success"}, {TX_HASH: 12345}, []
        ),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH])

    legs = result.structuredContent["datasets"]["tx_legs"]["preview_rows"]
    assert [row[11] for row in legs] == [None, None]
    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["known_usd_total"] == 0.0
    assert scope["unpriced_leg_count"] == 2
    assert scope["coverage"]["usd"]["total"] is None
    # Genuine price GAP (whitelisted token, no price row for its date) is the
    # only honest "partial" for the price source -- and it names the gap, not a
    # blanket same-symbol caveat.
    price_source = next(
        source
        for source in scope["sources"]
        if source["name"] == "dbt.int_execution_token_prices_daily"
    )
    assert price_source["status"] == "partial"
    assert "no daily price" in (price_source["error"] or "")
    assert "left unknown" in (price_source["error"] or "")
    assert "token_address" not in (price_source["error"] or "")
    nodes = {
        row[0]: row
        for row in result.structuredContent["datasets"]["tx_nodes"]["preview_rows"]
    }
    assert nodes[SOURCE][5] == 0.0
    assert nodes[SOURCE][6] is None
    assert nodes[TARGET][5] is None
    assert nodes[TARGET][6] == 0.0


def test_missing_enrichment_contract_keeps_receipt_legs_but_marks_scope_partial():
    ch = TxStubCH(validate_sources=False)
    view_id, load = _view_and_tool(ch)
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=(
            _receipt_rows(2), [], {TX_HASH: "success"}, {TX_HASH: 12345}, []
        ),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH])

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["status"] == "partial"
    assert scope["verification"]["status"] == "verified"
    assert scope["legs_returned"] == scope["legs_total"] == 2
    enrichment = [
        source for source in scope["sources"] if source["role"] == "enrichment"
    ]
    assert enrichment and all(source["status"] == "error" for source in enrichment)
    legs = result.structuredContent["datasets"]["tx_legs"]["preview_rows"]
    assert len(legs) == 2
    assert all(row[11] is None for row in legs)


def test_verified_zero_leg_receipt_is_exact_zero_not_unknown():
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
            return_value=([], [], {TX_HASH: "success"}, {TX_HASH: 12345}, []),
        ),
    ):
        result = load(view_id=view_id, tx_hashes=[TX_HASH])

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["legs_total"] == 0
    assert scope["legs_returned"] == 0
    assert scope["exact"] is True
    assert scope["coverage"]["rows"]["total"] == 0
    assert scope["receipt_statuses"] == {TX_HASH: "success"}


def test_missing_discovery_relation_publishes_failed_scope_not_empty_ready_graph():
    ch = TxStubCH(validate_sources=False)
    view_id, load = _view_and_tool(ch)
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
        side_effect=RuntimeError("RPC unavailable"),
    ):
        result = load(view_id=view_id, seed_node_id=SOURCE, request_id=5)

    assert result.isError is not True
    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["status"] == "failed"
    assert scope["coverage"]["rows"]["total"] is None
    assert scope["sources"]
    assert all(source["status"] == "error" for source in scope["sources"])
    assert result.structuredContent["datasets"]["tx_legs"]["preview_rows"] == []


def test_complete_full_history_rpc_empty_discovery_is_verified_zero():
    ch = TxStubCH(validate_sources=True)
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([], 0, True, 0),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_000),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
    ):
        result = load(
            view_id=view_id,
            seed_node_id=SOURCE,
            range_days=30,
            t0="2026-07-18 00:00:00",
            t1="2026-07-19 00:00:00",
        )

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["status"] == "ready"
    assert scope["verification"]["status"] == "verified"
    assert scope["legs_total"] == 0
    assert scope["coverage"]["rows"]["total"] == 0
    assert scope["data_horizon"] == 47_000_000
    assert scope["result_observed_through"] is None
    assert scope["query_kind"] == "address_discovery"
    assert scope["evidence_class"] == "address_discovery"
    assert scope["discovery_path"] == "execution_tables_rpc_tail"
    assert scope["window"] == {
        "t0": None,
        "t1": None,
        "source": "execution_tables_plus_rpc_head",
    }
    assert {source["name"] for source in scope["sources"]} >= {
        "execution.logs",
        "execution_live.logs",
        "execution.transactions",
        "execution_live.transactions",
        "eth_getLogs",
        "eth_getBlockByNumber",
    }


def test_plain_address_ignores_legacy_date_ranges_instead_of_hiding_activity():
    ch = TxStubCH(validate_sources=True)
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([], 0, True, 0),
        ) as execution_discover,
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ) as rpc_discover,
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ) as direct_discover,
    ):
        result = load(
            view_id=view_id,
            seed_node_id=SOURCE,
            range_days=1,
            t0="2026-07-19 02:00:00",
            t1="2026-07-19 03:00:00",
        )

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    execution_discover.assert_called_once_with(
        ch,
        SOURCE,
        through="2026-07-19 01:00:00",
        limit=25,
    )
    # chain_id is now threaded explicitly; assert it too, so the Gnosis default
    # is locked rather than merely tolerated.
    rpc_discover.assert_called_once_with(
        SOURCE, after_block=47_000_001, chain_id=100
    )
    direct_discover.assert_called_once_with(
        SOURCE,
        after_block=47_000_001,
        through_block=47_000_001,
        chain_id=100,
    )
    assert scope["verification"]["status"] == "verified"
    assert result.structuredContent["view_state"]["transactions"]["range_days"] == 0
    assert scope["discovery_coverage"] == {
        "complete": True,
        "requested_t1": None,
        "source_horizon": 47_000_001,
    }


def test_plain_address_uses_execution_tables_then_authoritative_receipts():
    discovered_hash = "0x" + "cd" * 32
    discovered_row = [
        discovered_hash,
        12_500,
        3,
        "2026-07-19 00:30:00",
        2,
        1,
    ]
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([discovered_row], 1, True, 1),
        ) as execution_discover,
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_002),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
            return_value=(
                [[discovered_hash, *row[1:]] for row in _receipt_rows(2)],
                [],
                {discovered_hash: "success"},
                {discovered_hash: 12_500},
                [],
            ),
        ),
    ):
        result = load(view_id=view_id, seed_node_id=SOURCE, range_days=90)

    tx = result.structuredContent["view_state"]["transactions"]
    scope = tx["scope"]
    execution_discover.assert_called_once_with(
        ch, SOURCE, through="2026-07-19 01:00:00", limit=25
    )
    assert scope["discovery_path"] == "execution_tables_rpc_tail"
    assert scope["data_horizon"] == 47_000_002
    discovery_sources = {
        source["name"]
        for source in scope["sources"]
        if source["role"] == "discovery"
    }
    assert discovery_sources == {
        "execution.logs",
        "execution_live.logs",
        "execution.transactions",
        "execution_live.transactions",
    }
    assert all(
        source["kind"] != "dbt_aggregate"
        for source in scope["sources"]
        if source["role"] == "discovery"
    )
    assert tx["query_kind"] == "address_discovery"
    assert tx["query_hashes"] == []
    assert tx["result_hashes"] == [discovered_hash]
    assert result.structuredContent["datasets"]["tx_list"]["preview_rows"] == [
        [discovered_hash, 12_345, 7, "2026-07-18 12:00:00", 2, 1]
    ]

def test_result_count_paging_discloses_lower_bound_without_a_date_range():
    hashes = [f"0x{index:064x}" for index in range(1, 27)]
    rows = [
        [tx_hash, 20_000 - index, index, "2026-07-19 00:00:00", 1, 1]
        for index, tx_hash in enumerate(hashes)
    ]
    admitted = hashes[:25]
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=(rows, None, False, 28),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_100),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
            return_value=(
                [],
                [],
                {tx_hash: "success" for tx_hash in admitted},
                {tx_hash: 19_000 for tx_hash in admitted},
                [],
            ),
        ),
    ):
        result = load(view_id=view_id, seed_node_id=SOURCE, max_txs=25)

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["window"] == {
        "t0": None,
        "t1": None,
        "source": "execution_tables_plus_rpc_head",
    }
    assert scope["more_transactions_available"] is True
    assert scope["txs_total_matching"] is None
    assert scope["txs_total_lower_bound"] == 28
    assert scope["verification"]["status"] == "unverified"
    assert "older execution pages not scanned" in scope["truncation"]["rule"]
    assert len(result.structuredContent["datasets"]["tx_list"]["preview_rows"]) == 25


def test_failed_discovery_preserves_last_applied_receipt_datasets_and_scope():
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
        return_value=(
            _receipt_rows(1), [], {TX_HASH: "success"}, {TX_HASH: 12345}, []
        ),
    ):
        applied = load(view_id=view_id, tx_hashes=[TX_HASH], request_id=7)
    applied_tx = applied.structuredContent["view_state"]["transactions"]
    applied_scope_id = applied_tx["scope"]["scope_id"]
    applied_legs = applied.structuredContent["datasets"]["tx_legs"]["preview_rows"]

    ch.validate_sources = False
    reset_source_contract_cache_for_tests()
    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
        side_effect=RuntimeError("RPC unavailable"),
    ):
        failed = load(view_id=view_id, seed_node_id=SOURCE, request_id=8)
    tx = failed.structuredContent["view_state"]["transactions"]

    assert tx["scope"]["scope_id"] == applied_scope_id
    assert tx["tx_hashes"] == [TX_HASH]
    assert tx["last_attempt"]["status"] == "failed"
    assert tx["last_attempt"]["request_id"] == 8
    assert tx["last_attempt"]["query_kind"] == "address_discovery"
    assert tx["last_attempt"]["scope"]["status"] == "failed"
    assert failed.structuredContent["datasets"]["tx_legs"]["preview_rows"] == (
        applied_legs
    )


def test_money_edge_discovery_preserves_applied_endpoints_token_and_window():
    ch = TxStubCH(validate_sources=True)
    view_id, load = _view_and_tool(ch)
    result = load(
        view_id=view_id,
        seed_node_id=SOURCE,
        counterparty_ids=[TARGET],
        tokens=[TOKEN],
        range_days=90,
        t0="2026-06-01 00:00:00",
        t1="2026-07-01 00:00:00",
    )

    tx = result.structuredContent["view_state"]["transactions"]
    assert tx["seed"] == SOURCE
    assert tx["counterparties"] == [TARGET]
    assert tx["tokens"] == [TOKEN]
    assert tx["range_days"] == 90
    assert tx["t0"] == "2026-06-01 00:00:00"
    assert tx["t1"] == "2026-07-01 00:00:00"
    assert tx["scope"]["window"] == {
        "t0": "2026-06-01 00:00:00",
        "t1": "2026-07-01 00:00:00",
        "source": "money_trail_applied_window",
    }
    discovery = next(
        call for call in ch.calls if "ts0" in call["parameters"]
    )
    assert discovery["parameters"]["ts0"] == "2026-06-01 00:00:00"
    assert discovery["parameters"]["ts1"] == "2026-07-01 00:00:00"
    assert discovery["parameters"]["cps"]
    assert discovery["parameters"]["tokens"] == [TOKEN[2:]]


def test_candidate_sql_uses_keyset_activity_and_token_authority():
    cursor_hash = "0x" + "fe" * 32
    sql, params = build_all_history_tx_discovery_chunk_sql(
        address_ids=[SOURCE],
        t0="2026-01-01 00:00:00",
        t1_exclusive="2026-02-01 00:00:00",
        limit=25,
        before_block=123,
        before_index=4,
        before_hash=cursor_hash,
        activity_kinds=["direct", "erc20"],
        tokens=[TOKEN],
    )

    assert "transaction_hash < {before_hash:String}" in sql
    assert "address IN {tokens:Array(String)}" in sql
    candidates = sql.split("\n    candidates AS (", 1)[1].split(
        "),\n    grouped", 1
    )[0]
    assert "transfer_candidates" in candidates
    assert "direct_candidates" not in candidates
    assert params["before_block"] == 123
    assert params["before_index"] == 4
    assert params["before_hash"] == cursor_hash[2:]
    assert params["tokens"] == [TOKEN[2:]]


def test_discovery_cursor_round_trips_and_rejects_invalid_values():
    row = [TX_HASH, 12345, 7, "", 1, 1]
    cursor = _encode_discovery_cursor(row)
    assert _decode_discovery_cursor(cursor) == (12345, 7, TX_HASH)
    with pytest.raises(ValueError, match="valid Graph Explorer discovery cursor"):
        _decode_discovery_cursor("not-a-cursor")


def test_discovery_horizon_probe_has_five_second_budget():
    assert _DISCOVERY_HORIZON_QUERY_BUDGET.max_execution_time == 5
    assert _DISCOVERY_HORIZON_QUERY_BUDGET.max_memory_usage == 256 * 2**20
    assert _DISCOVERY_HORIZON_QUERY_BUDGET.max_threads == 1


def test_append_only_discovery_delta_rejects_changed_or_reordered_base():
    older_hash = "0x" + "cd" * 32
    first = [TX_HASH, 200, 2, "2026-01-02 00:00:00", 1, 1]
    older = [older_hash, 100, 1, "2026-01-01 00:00:00", 1, 0]

    assert _append_only_discovery_delta([first], [first, older]) == [older]
    assert _append_only_discovery_delta([first], [older, first]) is None
    changed = [*first[:4], 2, 1]
    assert _append_only_discovery_delta([first], [changed, older]) is None


def test_coverage_cursor_uses_only_contiguous_newest_scanned_suffix():
    boundary = _coverage_continuation_boundary(
        [
            {"t0": "2026-07-12T00:00:00Z", "t1": "2026-07-19T01:00:01Z"},
            # This older range is separated by a gap and must not be skipped.
            {"t0": "2026-07-01T00:00:00Z", "t1": "2026-07-05T00:00:00Z"},
        ],
        through="2026-07-19 01:00:00",
    )
    assert boundary == "2026-07-12T00:00:00Z"
    decoded = _decode_discovery_cursor(
        _encode_coverage_discovery_cursor(boundary)
    )
    assert getattr(decoded, "before_time", None) == boundary


def test_uncovered_retry_cursor_targets_newest_bounded_gap():
    retry_from, retry_before = _newest_uncovered_retry_slice(
        [
            {
                "t0": "2018-10-08T00:00:00Z",
                "t1": "2026-07-05T00:00:00Z",
                "reason": "older work not attempted",
            },
            {
                "t0": "2026-07-05T00:00:00Z",
                "t1": "2026-07-12T00:00:00Z",
                "reason": "query timed out",
            },
        ],
        tile_seconds=12 * 60 * 60,
    ) or (None, None)
    assert retry_from == "2026-07-11T12:00:00Z"
    assert retry_before == "2026-07-12T00:00:00Z"

    decoded = _decode_discovery_cursor(
        _encode_coverage_discovery_cursor(
            retry_before,
            retry_from_time=retry_from,
            tile_seconds=12 * 60 * 60,
        )
    )
    assert getattr(decoded, "before_time", None) == retry_before
    assert getattr(decoded, "retry_from_time", None) == retry_from


def test_wall_budget_pagination_keeps_one_day_tile():
    assert _uncovered_requires_smaller_tile(
        [
            {
                "t0": "2018-10-08T00:00:00Z",
                "t1": "2026-07-12T00:00:00Z",
                "reason": "interactive discovery wall-time budget reached",
            }
        ]
    ) is False
    assert _uncovered_requires_smaller_tile(
        [
            {
                "t0": "2026-07-11T00:00:00Z",
                "t1": "2026-07-12T00:00:00Z",
                "reason": "ClickHouse query timeout exceeded",
            }
        ]
    ) is True


def test_execution_discovery_subdivides_memory_errors_and_discloses_leaf_gaps():
    class MemoryCH:
        def run_query(self, *args, **kwargs):
            raise RuntimeError(
                "ClickHouse error code 241: MEMORY_LIMIT_EXCEEDED"
            )

    result = _discover_address_transactions_execution(
        MemoryCH(),
        SOURCE,
        since="2026-01-01 00:00:00",
        through="2026-01-01 01:59:59",
        limit=25,
        max_workers=1,
        detailed=True,
    )

    rows, total, complete, lower_bound, coverage = result
    assert rows == []
    assert total is None
    assert complete is False
    assert lower_bound == 0
    assert coverage["scanned_ranges"] == []
    assert coverage["uncovered_ranges"]
    assert all(
        "MEMORY_LIMIT_EXCEEDED" in gap["reason"]
        for gap in coverage["uncovered_ranges"]
    )


def test_discover_operation_returns_candidates_without_fetching_receipts():
    candidate_hash = "0x" + "cd" * 32
    candidate = [
        candidate_hash,
        12_500,
        3,
        "2026-07-19 00:30:00",
        2,
        1,
    ]
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    coverage = {
        "scanned_ranges": [
            {"t0": "2018-10-08T00:00:00Z", "t1": "2026-07-19T01:00:01Z"}
        ],
        "uncovered_ranges": [],
        "older_history_unscanned": True,
    }
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([candidate], None, False, 2, coverage),
        ) as execution_discover,
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._legs_from_receipts",
            side_effect=AssertionError("candidate discovery must not fetch receipts"),
        ),
    ):
        result = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            page_size=25,
            activity_kinds=["direct", "erc20"],
            request_id=11,
        )

    execution_discover.assert_called_once_with(
        ch,
        SOURCE,
        through="2026-07-19 01:00:00",
        since=None,
        limit=25,
        before=None,
        activity_kinds=["direct", "erc20"],
        tokens=[],
        detailed=True,
        tile_seconds=86_400,
    )
    tx = result.structuredContent["view_state"]["transactions"]
    assert tx["query"]["kind"] == "address"
    assert tx["query"]["window"] is None
    assert tx["result_hashes"] == [candidate_hash]
    assert tx["results"]["selected_hash"] is None
    assert tx["receipt_scope"] is None
    assert tx["discovery_coverage"]["complete"] is False
    assert tx["discovery_coverage"]["total_exact"] is None
    assert tx["discovery_coverage"]["total_lower_bound"] == 2
    continuation = tx["discovery_coverage"]["next_cursor"]
    assert continuation
    assert not isinstance(_decode_discovery_cursor(continuation), tuple)
    assert result.structuredContent["datasets"]["tx_legs"]["preview_rows"] == []


def test_discovery_pagination_returns_revision_safe_append_patch():
    newest_hash = "0x" + "cd" * 32
    older_hash = "0x" + "ce" * 32
    newest = [newest_hash, 12_500, 3, "2026-07-19 00:30:00", 2, 1]
    older = [older_hash, 12_400, 2, "2026-07-18 23:30:00", 1, 1]
    partial_coverage = {
        "scanned_ranges": [],
        "uncovered_ranges": [],
        "older_history_unscanned": True,
    }
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            side_effect=[
                ([newest], None, False, 2, partial_coverage),
                ([older], None, False, 1, partial_coverage),
            ],
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
    ):
        first = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            page_size=1,
            request_id=31,
        )
        cursor = first.structuredContent["view_state"]["transactions"][
            "discovery_coverage"
        ]["next_cursor"]
        base_revision = first.structuredContent["view_state"][
            "dataset_revisions"
        ]["tx_list"]
        second = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            cursor=cursor,
            page_size=1,
            request_id=32,
        )

    content = second.structuredContent
    assert content["type"] == "PATCH_VIEW_STATE"
    assert content.get("datasets") == {}
    delta = content["patch"]["dataset_deltas"]["tx_list"]
    assert delta["operation"] == "append"
    assert delta["base_revision"] == base_revision
    assert delta["dataset_revision"] == base_revision + 1
    assert delta["base_row_count"] == 1
    assert delta["rows"] == [older]
    # The fallback is deliberately cheap and hydration-safe from offset zero.
    assert delta["fallback"]["preview_rows"] == []
    assert delta["fallback"]["page_token"] == "offset:0"
    assert delta["fallback"]["stats"]["row_count"] == 2
    assert content["patch"]["view_state"]["transactions"][
        "result_hashes"
    ] == [newest_hash, older_hash]
    stored = mini_apps.snapshot_view(view_id)
    assert stored is not None
    assert stored.datasets["tx_list"].rows == [newest, older]


def test_zero_row_partial_discovery_cursor_remains_actionable_until_older_result():
    june_hash = "0x" + "cf" * 32
    june_activity = [june_hash, 12_000, 1, "2026-06-12 08:00:00", 1, 1]
    first_coverage = {
        "scanned_ranges": [
            {
                "t0": "2026-07-12T00:00:00Z",
                "t1": "2026-07-19T01:00:01Z",
            }
        ],
        "uncovered_ranges": [
            {
                "t0": "2018-10-08T00:00:00Z",
                "t1": "2026-07-12T00:00:00Z",
                "reason": "ClickHouse query timeout exceeded",
            }
        ],
        "older_history_unscanned": True,
    }
    second_coverage = {
        "scanned_ranges": [],
        "uncovered_ranges": [
            {
                "t0": "2026-07-11T12:00:00Z",
                "t1": "2026-07-12T00:00:00Z",
                "reason": "interactive discovery wall-time budget reached",
            }
        ],
        "older_history_unscanned": True,
        "tile_seconds": 43_200,
    }
    third_coverage = {
        "scanned_ranges": [
            {
                "t0": "2026-07-11T12:00:00Z",
                "t1": "2026-07-12T00:00:00Z",
            }
        ],
        "uncovered_ranges": [],
        "older_history_unscanned": False,
        "tile_seconds": 43_200,
    }
    fourth_coverage = {
        "scanned_ranges": [
            {
                "t0": "2026-06-05T00:00:00Z",
                "t1": "2026-07-11T12:00:00Z",
            }
        ],
        "uncovered_ranges": [],
        "older_history_unscanned": True,
        "tile_seconds": 43_200,
    }
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            side_effect=[
                ([], None, False, 0, first_coverage),
                ([], None, False, 0, second_coverage),
                ([], 0, True, 0, third_coverage),
                ([june_activity], None, False, 1, fourth_coverage),
            ],
        ) as execution_discover,
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ) as rpc_transfer_discover,
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ) as rpc_direct_discover,
    ):
        first = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            page_size=25,
            request_id=51,
        )
        first_tx = first.structuredContent["view_state"]["transactions"]
        coverage_cursor = first_tx["discovery_coverage"]["next_cursor"]
        assert coverage_cursor
        assert first_tx["result_hashes"] == []
        decoded_first = _decode_discovery_cursor(coverage_cursor)
        assert getattr(decoded_first, "before_time", None) == (
            "2026-07-12T00:00:00Z"
        )
        assert getattr(decoded_first, "retry_from_time", None) == (
            "2026-07-11T12:00:00Z"
        )
        assert getattr(decoded_first, "tile_seconds", None) == 43_200

        second = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            cursor=coverage_cursor,
            page_size=25,
            request_id=52,
        )
        second_tx = second.structuredContent["patch"]["view_state"][
            "transactions"
        ]
        retry_cursor = second_tx["discovery_coverage"]["next_cursor"]
        assert retry_cursor
        decoded_retry = _decode_discovery_cursor(retry_cursor)
        assert getattr(decoded_retry, "before_time", None) == (
            "2026-07-12T00:00:00Z"
        )
        assert getattr(decoded_retry, "retry_from_time", None) == (
            "2026-07-11T12:00:00Z"
        )
        assert getattr(decoded_retry, "tile_seconds", None) == 43_200

        third = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            cursor=retry_cursor,
            page_size=25,
            request_id=53,
        )
        third_tx = third.structuredContent["patch"]["view_state"][
            "transactions"
        ]
        older_cursor = third_tx["discovery_coverage"]["next_cursor"]
        assert older_cursor
        decoded_older = _decode_discovery_cursor(older_cursor)
        assert getattr(decoded_older, "before_time", None) == (
            "2026-07-11T12:00:00Z"
        )
        assert getattr(decoded_older, "retry_from_time", None) is None

        fourth = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            cursor=older_cursor,
            page_size=25,
            request_id=54,
        )

    assert execution_discover.call_count == 4
    first_call, second_call, third_call, fourth_call = (
        execution_discover.call_args_list
    )
    assert first_call.kwargs["through"] == "2026-07-19 01:00:00"
    assert second_call.kwargs["through"] == "2026-07-11 23:59:59"
    assert third_call.kwargs["through"] == "2026-07-11 23:59:59"
    assert fourth_call.kwargs["through"] == "2026-07-11 11:59:59"
    assert first_call.kwargs["since"] is None
    assert second_call.kwargs["since"] == "2026-07-11 12:00:00"
    assert third_call.kwargs["since"] == "2026-07-11 12:00:00"
    assert fourth_call.kwargs["since"] is None
    assert first_call.kwargs["tile_seconds"] == 86_400
    assert second_call.kwargs["tile_seconds"] == 43_200
    assert third_call.kwargs["tile_seconds"] == 43_200
    assert fourth_call.kwargs["tile_seconds"] == 43_200
    assert first_call.kwargs["before"] is None
    assert second_call.kwargs["before"] is None
    assert third_call.kwargs["before"] is None
    assert fourth_call.kwargs["before"] is None
    # The RPC head is already newer than the coverage boundary and is not
    # rescanned on the older continuation.
    assert rpc_transfer_discover.call_count == 1
    assert rpc_direct_discover.call_count == 1
    assert second.structuredContent["type"] == "PATCH_VIEW_STATE"
    assert third.structuredContent["type"] == "PATCH_VIEW_STATE"
    assert fourth.structuredContent["type"] == "PATCH_VIEW_STATE"
    delta = fourth.structuredContent["patch"]["dataset_deltas"]["tx_list"]
    assert delta["base_row_count"] == 0
    assert delta["rows"] == [june_activity]
    assert fourth.structuredContent["patch"]["view_state"]["transactions"][
        "result_hashes"
    ] == [june_hash]


def test_non_cursor_discovery_still_returns_full_initial_load():
    candidate = [TX_HASH, 12_500, 3, "2026-07-19 00:30:00", 1, 1]
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=(
                [candidate],
                1,
                True,
                1,
                {
                    "scanned_ranges": [],
                    "uncovered_ranges": [],
                    "older_history_unscanned": False,
                },
            ),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
    ):
        result = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            page_size=25,
            request_id=41,
        )

    assert result.structuredContent["type"] == "INITIAL_LOAD"
    assert result.structuredContent["datasets"]["tx_list"]["preview_rows"] == [
        candidate
    ]


def test_discovery_cursor_is_bound_to_exact_utc_window():
    candidate_hash = "0x" + "cd" * 32
    candidate = [candidate_hash, 12_500, 3, "2026-07-19 00:30:00", 2, 1]
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    coverage = {
        "scanned_ranges": [
            {"t0": "2026-07-01T00:00:00Z", "t1": "2026-07-20T00:00:00Z"}
        ],
        "uncovered_ranges": [],
        "older_history_unscanned": True,
    }
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([candidate], None, False, 2, coverage),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
    ):
        first = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            t0="2026-07-01T00:00:00Z",
            t1="2026-07-20T00:00:00Z",
            page_size=1,
            request_id=21,
        )
        cursor = first.structuredContent["view_state"]["transactions"][
            "discovery_coverage"
        ]["next_cursor"]
        replay = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            t0="2026-06-01T00:00:00Z",
            t1="2026-07-20T00:00:00Z",
            cursor=cursor,
            page_size=1,
            request_id=22,
        )

    assert replay.isError is True
    assert "cursor does not belong" in replay.content[0].text


def test_omitted_transaction_request_id_gets_positive_server_revision():
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([], 0, True, 0, {
                "scanned_ranges": [],
                "uncovered_ranges": [],
                "older_history_unscanned": False,
            }),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
    ):
        result = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
        )

    scope = result.structuredContent["view_state"]["transactions"]["scope"]
    assert scope["request_id"] > 0


def test_receipt_operation_preserves_address_list_and_adds_rpc_context():
    candidate_hash = "0x" + "cd" * 32
    candidate = [candidate_hash, 12_500, 3, "2026-07-19 00:30:00", 1, 1]
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    complete_coverage = {
        "scanned_ranges": [
            {"t0": "2018-10-08T00:00:00Z", "t1": "2026-07-19T01:00:01Z"}
        ],
        "uncovered_ranges": [],
        "older_history_unscanned": False,
    }
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([candidate], 1, True, 1, complete_coverage),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            return_value=([], 47_000_001),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_direct_transactions_rpc",
            return_value=[],
        ),
    ):
        discovered = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            request_id=12,
        )
    original_list = discovered.structuredContent["datasets"]["tx_list"][
        "preview_rows"
    ]

    structural = "0x" + "00" * 20
    topic = lambda address: "0x" + "0" * 24 + address[2:]

    class ReceiptRpc:
        def request(self, method, params):
            if method == "eth_getTransactionByHash":
                return {
                    "hash": candidate_hash,
                    "from": SOURCE,
                    "to": TARGET,
                    "input": "0xa9059cbb" + "00" * 32,
                    "nonce": "0x2",
                    "value": "0x0",
                    "gas": "0x5208",
                    "gasPrice": "0x3b9aca00",
                }
            if method == "eth_getTransactionReceipt":
                return {
                    "blockNumber": hex(12_500),
                    "transactionIndex": "0x3",
                    "status": "0x1",
                    "gasUsed": "0x5208",
                    "effectiveGasPrice": "0x3b9aca00",
                    "logs": [
                        {
                            "logIndex": "0x1",
                            "address": TOKEN,
                            "topics": [TRANSFER_TOPIC0, topic(structural), topic(SOURCE)],
                            "data": "0x" + f"{10**18:064x}",
                        }
                    ],
                }
            if method == "eth_getBlockByNumber":
                assert params[1] is False
                return {"timestamp": hex(1_752_837_600)}
            raise AssertionError(method)

    with patch(
        "cerebro_mcp.tools.semantic.graph_explorer.transactions._router",
        return_value=SimpleNamespace(standard=ReceiptRpc()),
    ):
        receipt = load(
            view_id=view_id,
            operation="receipt",
            tx_hashes=[candidate_hash],
            request_id=13,
        )

    tx = receipt.structuredContent["view_state"]["transactions"]
    assert receipt.structuredContent["datasets"]["tx_list"]["preview_rows"] == (
        original_list
    )
    assert tx["query"]["kind"] == "address"
    assert tx["query"]["address"] == SOURCE
    assert tx["results"]["selected_hash"] == candidate_hash
    assert tx["scope"]["evidence_class"] == "address_discovery"
    assert tx["receipt_scope"]["evidence_class"] == "rpc_receipt"
    context = receipt.structuredContent["datasets"]["tx_context"]["preview_rows"]
    assert len(context) == 1
    assert context[0][0:4] == [candidate_hash, SOURCE, TARGET, "0xa9059cbb"]
    assert context[0][10] == 21_000 * 1_000_000_000
    assert context[0][15] == ["direct_sender", "erc20_recipient"]
    leg = receipt.structuredContent["datasets"]["tx_legs"]["preview_rows"][0]
    assert leg[1] == structural
    nodes = {
        row[0]: row
        for row in receipt.structuredContent["datasets"]["tx_nodes"]["preview_rows"]
    }
    assert nodes[structural][2] == "burn"


def test_discover_operation_keeps_stored_candidates_when_rpc_head_fails():
    candidate_hash = "0x" + "ce" * 32
    candidate = [candidate_hash, 12_400, 2, "2026-07-18 23:00:00", 0, 0]
    ch = TxStubCH()
    view_id, load = _view_and_tool(ch)
    execution_coverage = {
        "scanned_ranges": [
            {"t0": "2018-10-08T00:00:00Z", "t1": "2026-07-19T01:00:01Z"}
        ],
        "uncovered_ranges": [],
        "older_history_unscanned": False,
    }
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
            return_value=([candidate], 1, True, 1, execution_coverage),
        ),
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_rpc",
            side_effect=RuntimeError("RPC temporarily unavailable"),
        ),
    ):
        result = load(
            view_id=view_id,
            operation="discover",
            seed_node_id=SOURCE,
            activity_kinds=["erc20"],
            request_id=14,
        )

    assert result.isError is not True
    tx = result.structuredContent["view_state"]["transactions"]
    assert tx["result_hashes"] == [candidate_hash]
    assert tx["discovery_scope"]["status"] == "partial"
    coverage = tx["discovery_coverage"]
    assert coverage["complete"] is False
    assert coverage["uncovered_ranges"]
    assert "RPC temporarily unavailable" in coverage["uncovered_ranges"][0][
        "reason"
    ]
    assert any(
        source["role"] == "discovery_tail" and source["status"] == "error"
        for source in tx["discovery_scope"]["sources"]
    )


# ---------------------------------------------------------------------------
# Multichain receipts (S5)
# ---------------------------------------------------------------------------


def test_router_is_chain_scoped_and_gnosis_is_unchanged():
    """`_router` must resolve per chain, and chain 100 must keep the legacy pair."""
    from cerebro_mcp.chains import GNOSIS_CHAIN_ID
    from cerebro_mcp.tools.semantic.graph_explorer.transactions import _router

    with mock.patch(
        "cerebro_mcp.clients.raw_rpc.RpcRouter.for_chain"
    ) as for_chain:
        _router(GNOSIS_CHAIN_ID)
        _router(8453)
    assert [call.args[0] for call in for_chain.call_args_list] == [100, 8453]


def test_address_rpc_cache_is_keyed_by_chain():
    """The same address holds different history on every chain.

    Keyed by address alone, a Base scan's rows would be served to a Gnosis
    question (and vice versa) — silently wrong evidence.
    """
    from cerebro_mcp.tools.semantic.graph_explorer import transactions as tx

    tx._address_rpc_cache.clear()
    tx._address_rpc_cache[(100, "0xabc")] = (10, [["gnosis-row"]])
    tx._address_rpc_cache[(8453, "0xabc")] = (10, [["base-row"]])
    assert tx._address_rpc_cache[(100, "0xabc")][1] == [["gnosis-row"]]
    assert tx._address_rpc_cache[(8453, "0xabc")][1] == [["base-row"]]
    tx._address_rpc_cache.clear()


def test_redact_endpoint_strips_urls_that_carry_api_keys():
    """Scope strings reach the exported case bundle; endpoint URLs embed keys."""
    from cerebro_mcp.tools.semantic.graph_explorer.transactions import (
        _redact_endpoint,
    )

    err = Exception(
        "503 Server Error: Service Unavailable for url: "
        "https://rpc.example.com/v1/SECRET_API_KEY"
    )
    out = _redact_endpoint(err)
    assert "SECRET_API_KEY" not in out
    assert "http" not in out
    assert "503" in out


def test_off_gnosis_enrichment_never_borrows_gnosis_metadata_or_price():
    """Off Gnosis the warehouse must not be consulted at all.

    The address -> symbol -> price bijection holds only because every symbol
    comes from the Gnosis whitelist seed. The same address exists on every EVM
    chain, so joining a Base token against it could hand back a Gnosis token's
    decimals and price — a silently wrong amount. USD must be unknown, not 0,
    and no unverified symbol may be presented as the asset's identity.
    """
    from cerebro_mcp.tools.semantic.graph_explorer import transactions as tx

    token = "0x" + "ab" * 20
    # rows: [.., block_timestamp@4, .., token_address@7, .., raw_amount@9, ..]
    rows = [["id", "src", "tgt", "0xhash", "2026-07-01", 1, 2, token, 0, 10**8, 0, "ok"]]

    with mock.patch.object(
        tx, "_erc20_decimals_onchain", return_value={token: 8}
    ) as onchain, mock.patch.object(tx, "validate_source_contract") as contract:
        out, statuses, warnings, _details = tx._enrich_rpc_legs(
            None, rows, chain_id=8453
        )

    contract.assert_not_called()          # no Gnosis relation was probed
    onchain.assert_called_once()
    assert statuses["prices"] == "not_applicable"
    leg = out[0]
    assert leg[8] == ""                    # no attacker-controlled symbol shown
    assert leg[9] == 1.0                   # normalized with the contract's decimals
    assert leg[10] is None                 # USD unknown, NOT zero
    assert leg[12] == str(10**8)           # raw amount stays authoritative
    assert any("USD is unavailable" in w for w in warnings)


def test_gnosis_enrichment_still_uses_the_warehouse():
    """The Gnosis path must be untouched by the multichain branch."""
    from cerebro_mcp.tools.semantic.graph_explorer import transactions as tx

    token = "0x" + "cd" * 20
    rows = [["id", "src", "tgt", "0xhash", "2026-07-01", 1, 2, token, 0, 10**18, 0, "ok"]]

    with mock.patch.object(tx, "_erc20_decimals_onchain") as onchain, mock.patch.object(
        tx, "validate_source_contract", return_value={"ok": False, "error": "stub"}
    ) as contract:
        _out, _statuses, _warnings, _details = tx._enrich_rpc_legs(
            None, rows, chain_id=100
        )

    contract.assert_called()               # warehouse contract WAS probed
    onchain.assert_not_called()            # and no on-chain fallback was used
