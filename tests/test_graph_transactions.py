"""Focused forensic-safety tests for Transaction Detail backend contracts."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.runtime.mini_app_cache import reset_cache_for_tests
from cerebro_mcp.semantic.tx_queries import (
    TX_ADDRESS_INDEX_RELATION,
    build_indexed_tx_discovery_sql,
    build_indexed_tx_membership_sql,
    build_legs_sql,
    build_tx_index_horizon_sql,
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
    _authoritative_leg_total,
    _discover_address_direct_transactions_rpc,
    _discover_address_transactions_rpc,
    _legs_from_receipts,
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
        address_index: bool = False,
        index_rows: list[list[Any]] | None = None,
        missing_relations: set[str] | None = None,
        sql_fallback_rows: list[list[Any]] | None = None,
        sql_leg_total: int = 7,
    ):
        self.price = price
        self.validate_sources = validate_sources
        self.address_index = address_index
        self.index_rows = index_rows or []
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
            relation_available = self.validate_sources and (
                relation
                != "dbt.int_execution_address_activity"
                or self.address_index
            )
            rows = (
                [[name, "String"] for name in required]
                if relation_available and relation not in self.missing_relations
                else []
            )
            return result(["name", "type"], rows)
        if (
            "int_execution_address_activity" in sql
            and "GROUP BY activity_source" in sql
        ):
            return result(
                [
                    "activity_source",
                    "first_event_at",
                    "event_horizon",
                    "block_horizon",
                    "indexed_at",
                ],
                [
                    [
                        "transactions",
                        "2018-10-08 00:00:00",
                        "2026-07-19 01:30:00",
                        47_000_000,
                        "2026-07-19 01:35:00",
                    ],
                    [
                        "transfers",
                        "2018-10-09 00:00:00",
                        "2026-07-19 01:25:00",
                        46_999_999,
                        "2026-07-19 01:35:00",
                    ],
                ],
            )
        if "max(toString(`block_timestamp`))" in sql:
            return result(["source_horizon"], [["2026-07-19 01:25:00"]])
        if "metadata_modification_time" in sql:
            return result(["source_horizon"], [["2026-07-19 08:13:13"]])
        if "max(toString(`date`))" in sql:
            return result(["source_horizon"], [["2026-07-18"]])
        if "'execution.logs' AS relation" in sql:
            return result(
                ["relation", "horizon", "block_horizon"],
                [
                    ["execution.logs", "2026-07-19 00:00:00", 46_999_000],
                    ["execution_live.logs", "2026-07-19 01:00:00", 47_000_000],
                ],
            )
        if "int_execution_address_activity" in sql and "latest_before_t0" in sql:
            return result(["latest_before_t0"], [[None]])
        if (
            "int_execution_address_activity" in sql
            and "SELECT DISTINCT transaction_hash" in sql
        ):
            return result(["transaction_hash"], [])
        if "int_execution_address_activity" in sql:
            return result(
                [
                    "transaction_hash",
                    "block_number",
                    "transaction_index",
                    "block_timestamp",
                    "leg_count",
                    "token_count",
                ],
                self.index_rows,
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


def test_address_index_queries_match_the_additive_dbt_contract():
    sql, params = build_indexed_tx_discovery_sql(
        address_ids=[SOURCE],
        t0="2018-10-08 00:00:00",
        t1_exclusive="2026-07-20 00:00:00",
        tokens=[TOKEN],
        counterparty_ids=[TARGET],
        limit=25,
    )
    assert TX_ADDRESS_INDEX_RELATION == "dbt.int_execution_address_activity"
    assert f"FROM {TX_ADDRESS_INDEX_RELATION} FINAL" in sql
    assert "indexed_transfer_leg_count" in sql
    assert "token_counterparties" in sql
    assert "count() OVER () AS transaction_total" in sql
    assert "standard_erc20_leg_count" not in sql
    assert params["chain_id"] == 100

    horizon_sql, _ = build_tx_index_horizon_sql()
    assert "GROUP BY activity_source" in horizon_sql
    assert "max(source_horizon_block) AS block_horizon" in horizon_sql

    membership_sql, membership_params = build_indexed_tx_membership_sql(
        address_id=SOURCE,
        tx_hashes=[TX_HASH],
    )
    assert f"FROM {TX_ADDRESS_INDEX_RELATION} FINAL" in membership_sql
    assert membership_params["hashes"] == [TX_HASH]


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


def test_symbol_only_price_source_is_explicitly_partial_not_address_qualified():
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
    assert price_source["status"] == "partial"
    assert "exposes no token_address" in price_source["error"]
    assert scope["status"] == "partial"
    assert scope["verification"]["status"] == "verified"
    # The value remains inspectable, but its source limitation is not hidden.
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
        "cerebro_mcp.tools.semantic.graph_explorer.transactions.RpcRouter.from_settings",
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
    assert scope["discovery_path"] == "execution_logs_rpc_tail"
    assert scope["window"] == {
        "t0": None,
        "t1": None,
        "source": "execution_logs_plus_rpc_head",
    }
    assert {source["name"] for source in scope["sources"]} >= {
        "execution.logs",
        "execution_live.logs",
        "eth_getLogs",
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
    rpc_discover.assert_called_once_with(SOURCE, after_block=47_000_001)
    assert scope["verification"]["status"] == "verified"
    assert result.structuredContent["view_state"]["transactions"]["range_days"] == 0
    assert scope["discovery_coverage"] == {
        "complete": True,
        "requested_t1": None,
        "source_horizon": 47_000_001,
    }


def test_plain_address_uses_dbt_index_rpc_tail_then_authoritative_receipts():
    index_hash = "0x" + "cd" * 32
    ch = TxStubCH(
        address_index=True,
        index_rows=[
            [index_hash, 12_500, 3, "2026-07-19 02:00:00", 2, 1, 1],
        ],
    )
    view_id, load = _view_and_tool(ch)
    with (
        patch(
            "cerebro_mcp.tools.semantic.graph_explorer.transactions._discover_address_transactions_execution",
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
                [[index_hash, *row[1:]] for row in _receipt_rows(2)],
                [],
                {index_hash: "success"},
                {index_hash: 12_500},
                [],
            ),
        ),
    ):
        result = load(view_id=view_id, seed_node_id=SOURCE, range_days=90)

    tx = result.structuredContent["view_state"]["transactions"]
    scope = tx["scope"]
    execution_discover.assert_not_called()
    assert scope["discovery_path"] == "address_index_rpc_tail"
    assert scope["data_horizon"] == 47_000_002
    discovery_source = next(
        source for source in scope["sources"] if source["role"] == "discovery"
    )
    assert discovery_source["name"] == "dbt.int_execution_address_activity"
    assert any("participant_address IN" in call["sql"] for call in ch.calls)
    assert tx["query_kind"] == "address_discovery"
    assert tx["query_hashes"] == []
    assert tx["result_hashes"] == [index_hash]
    assert result.structuredContent["datasets"]["tx_list"]["preview_rows"] == [
        [index_hash, 12_345, 7, "2026-07-18 12:00:00", 2, 1]
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
        "source": "execution_logs_plus_rpc_head",
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
