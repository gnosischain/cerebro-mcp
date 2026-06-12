"""Tool-surface registration smoke tests and teaching errors."""
from typing import Any

import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.engine import ScanEngine
from cerebro_mcp.rpc_scan.jobs import ScanJobManager
from cerebro_mcp.tools.web3.rpc_scan import register_rpc_scan_tools
from tests.rpc_scan_fakes import FakeCH, FakeRouter, InMemoryRegistryStore

TOKEN = "0x" + "11" * 20
BOB = "0x" + "bb" * 20


class StubMCP:
    def __init__(self):
        self.tools: dict[str, Any] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _capture(handler=None, ch=None):
    store = InMemoryRegistryStore()
    router = FakeRouter(handler or (lambda m, p: []), latest=1000)
    engine = ScanEngine(
        ch or FakeCH(), router=router, store=store,
        jobs=ScanJobManager(store, max_concurrent=1),
    )
    stub = StubMCP()
    register_rpc_scan_tools(stub, ch or FakeCH(), engine=engine)
    return stub.tools, engine, store


def test_tools_register_with_expected_names():
    tools, _, _ = _capture()
    assert set(tools) == {
        "rpc_scan_logs",
        "rpc_batch_call",
        "rpc_read_storage",
        "rpc_get_code",
        "rpc_scan_traces",
        "rpc_trace_transaction",
        "rpc_find_block",
        "rpc_scan_status",
        "rpc_scan_cancel",
        "rpc_scan_resume",
        "rpc_list_scans",
    }


def test_find_block_timestamp_sync():
    def handler(method, params):
        if method == "eth_getBlockByNumber":
            block = int(params[0], 16)
            return {"timestamp": hex(1_000_000 + block * 10)}
        raise AssertionError(method)

    tools, _, _ = _capture(handler=handler)
    out = tools["rpc_find_block"](kind="timestamp", timestamp="1005000")
    assert "500" in out


def test_find_block_deployment_inline_table():
    def handler(method, params):
        if method == "eth_getCode":
            return "0x6080" if int(params[1], 16) >= 7 else "0x"
        raise AssertionError(method)

    tools, _, _ = _capture(handler=handler)
    out = tools["rpc_find_block"](
        kind="deployment", addresses=[BOB], from_block=1, to_block=100,
    )
    assert "| 7 |" in out


def test_find_block_bad_kind():
    tools, _, _ = _capture()
    out = tools["rpc_find_block"](kind="nope")
    assert out.startswith("Error:")


def test_trace_transaction_tool_renders_tree():
    def handler(method, params):
        assert method == "debug_traceTransaction"
        return {
            "type": "CALL", "from": "0x" + "aa" * 20, "to": BOB,
            "value": hex(5), "gasUsed": "0x10", "input": "0x12345678",
        }

    tools, _, _ = _capture(handler=handler)
    out = tools["rpc_trace_transaction"]("0x" + "01" * 32)
    assert "Call tree" in out and "0x12345678" in out
    assert "Net native value movement" in out


def test_scan_completes_within_sync_budget_and_renders_summary():
    tools, _, _ = _capture()
    out = tools["rpc_scan_logs"](
        from_block=0, to_block=10, contracts=[TOKEN],
        event="Transfer(address,address,uint256)", label="smoke",
    )
    assert "completed" in out
    assert "scratch.rpc_logs_" in out
    assert "Next queries" in out


def test_inline_cap_teaching_error(monkeypatch):
    monkeypatch.setattr(settings, "RPC_SCAN_MAX_INLINE_ADDRESSES", 3)
    tools, _, _ = _capture()
    out = tools["rpc_scan_logs"](
        from_block=0, to_block=10,
        event="Transfer(address,address,uint256)",
        filter_arg="to",
        filter_addresses=["0x" + f"{i:040x}" for i in range(4)],
    )
    assert out.startswith("Error:")
    assert "address_sql" in out


def test_both_address_inputs_rejected():
    tools, _, _ = _capture()
    out = tools["rpc_scan_logs"](
        from_block=0, to_block=10,
        event="Transfer(address,address,uint256)",
        filter_arg="to",
        filter_addresses=[BOB],
        filter_address_sql="SELECT a FROM t",
    )
    assert out.startswith("Error:") and "exactly one" in out


def test_address_sql_column_count_teaching_error():
    ch = FakeCH(describe_rows=[["a"], ["b"], ["c"]])
    tools, _, _ = _capture(ch=ch)
    out = tools["rpc_scan_logs"](
        from_block=0, to_block=10,
        event="Transfer(address,address,uint256)",
        filter_arg="to",
        filter_address_sql="SELECT * FROM dbt.some_model",
    )
    assert out.startswith("Error:") and "exactly one address column" in out


def test_address_sql_resolves_addresses():
    ch = FakeCH(describe_rows=[["safe_address"]], result_rows=[[BOB]])
    tools, _, _ = _capture(ch=ch)
    out = tools["rpc_scan_logs"](
        from_block=0, to_block=10, contracts=[TOKEN],
        event="Transfer(address indexed from, address indexed to, uint256 value)",
        filter_arg="to",
        filter_address_sql="SELECT safe_address FROM dbt.gnosis_pay_safes",
    )
    assert "completed" in out


def test_ambiguous_short_signature_teaching_error():
    tools, _, _ = _capture()
    out = tools["rpc_scan_logs"](
        from_block=0, to_block=10, event="Foo(address,uint256)",
    )
    assert out.startswith("Error:") and "decode_abi_address" in out


def test_filter_arg_unknown_argument_lists_options():
    tools, _, _ = _capture()
    out = tools["rpc_scan_logs"](
        from_block=0, to_block=10,
        event="Transfer(address indexed from, address indexed to, uint256 value)",
        filter_arg="recipient", filter_addresses=[BOB],
    )
    assert out.startswith("Error:") and "from, to, value" in out


def test_status_unknown_job_mentions_registry():
    tools, _, _ = _capture()
    out = tools["rpc_scan_status"]("0badc0de")
    assert out.startswith("Error:") and "rpc_list_scans" in out


def test_cancel_terminal_job_reports_status():
    tools, _, _ = _capture()
    started = tools["rpc_scan_logs"](
        from_block=0, to_block=5, contracts=[TOKEN],
        event="Transfer(address,address,uint256)",
    )
    job_id = started.split("`")[1]
    out = tools["rpc_scan_cancel"](job_id)
    assert "already" in out


def test_list_scans_includes_registry_rows():
    tools, _, store = _capture()
    store.registry["feedc0de"] = {
        "job_id": "feedc0de", "kind": "logs", "label": "old scan",
        "table_name": "rpc_logs_feedc0de", "spec_json": "{}",
        "status": "partial", "cursor_json": "{}", "rows_written": 7,
        "note": "server_restart", "created_at": 1.0, "updated_at": None,
    }
    out = tools["rpc_list_scans"]()
    assert "feedc0de" in out and "old scan" in out and "yes" in out


def test_resume_unknown_job_teaching_error():
    tools, _, _ = _capture()
    out = tools["rpc_scan_resume"]("0badc0de")
    assert out.startswith("Error:") and "not found" in out
