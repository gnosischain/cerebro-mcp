"""End-to-end log scans through the engine against a scripted RPC."""
import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.engine import ScanEngine
from cerebro_mcp.rpc_scan.jobs import ScanJobManager
from tests.rpc_scan_fakes import (
    FakeCH,
    FakeRouter,
    InMemoryRegistryStore,
    wait_for_terminal,
)

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOKEN = "0x" + "11" * 20
ALICE = "0x" + "aa" * 20
BOB = "0x" + "bb" * 20
CAROL = "0x" + "cc" * 20


def _topic(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:].lower()


def make_log(block, log_index, *, token=TOKEN, frm=ALICE, to=BOB, value=100,
             topics=None, data=None):
    return {
        "blockNumber": hex(block),
        "transactionHash": "0x" + f"{block:064x}",
        "transactionIndex": "0x0",
        "logIndex": hex(log_index),
        "address": token,
        "topics": topics or [TRANSFER_TOPIC0, _topic(frm), _topic(to)],
        "data": data if data is not None else "0x" + f"{value:064x}",
    }


def make_handler(logs):
    def handler(method, params):
        assert method == "eth_getLogs"
        filt = params[0]
        lo = int(filt["fromBlock"], 16)
        hi = int(filt["toBlock"], 16)
        out = []
        for lg in logs:
            block = int(lg["blockNumber"], 16)
            if not (lo <= block <= hi):
                continue
            if "address" in filt and lg["address"].lower() not in filt["address"]:
                continue
            topics_filter = filt.get("topics") or []
            match = True
            for i, tf in enumerate(topics_filter):
                if tf is None:
                    continue
                wanted = [tf] if isinstance(tf, str) else list(tf)
                if i >= len(lg["topics"]) or lg["topics"][i] not in wanted:
                    match = False
                    break
            if match:
                out.append(lg)
        return out

    return handler


def make_engine(logs, latest=1000):
    store = InMemoryRegistryStore()
    router = FakeRouter(make_handler(logs), latest=latest)
    engine = ScanEngine(
        FakeCH(), router=router, store=store,
        jobs=ScanJobManager(store, max_concurrent=2),
    )
    return engine, store, router


def test_basic_scan_decodes_typed_columns():
    logs = [make_log(100, 0, value=5), make_log(150, 1, frm=BOB, to=CAROL, value=7)]
    engine, store, _router = make_engine(logs)
    job, warnings = engine.start_log_scan(
        from_block=100, to_block=200, contracts=[TOKEN],
        event="Transfer(address indexed from, address indexed to, uint256 value)",
        label="basic",
    )
    wait_for_terminal(job)
    assert job.status == "completed"
    assert warnings == []

    table, cols, rows = store._fake_client.inserts[0][0], None, []
    for t, c, r in store._fake_client.inserts:
        if t.endswith(job.table_name):
            cols = c
            rows.extend(r)
    assert cols is not None
    assert {"arg_from", "arg_to", "arg_value"} <= set(cols)
    by_col = [dict(zip(cols, row)) for row in rows]
    assert {r["arg_value"] for r in by_col} == {5, 7}
    assert by_col[0]["address"] == TOKEN
    assert job.progress.rows_written == 2
    assert job.cursor.chunk_index == 1  # single pass finished


def test_zero_result_window_completes_and_checkpoints():
    engine, store, _ = make_engine([])
    job, _ = engine.start_log_scan(
        from_block=0, to_block=500, contracts=[TOKEN],
        event="Transfer(address,address,uint256)",
    )
    wait_for_terminal(job)
    assert job.status == "completed"
    assert job.progress.rows_written == 0
    # Cursor advanced through the whole window despite zero rows.
    assert job.progress.blocks_done == 501


def test_indexed_filter_chunks_topic_groups(monkeypatch):
    monkeypatch.setattr(settings, "RPC_SCAN_ADDRESS_BATCH", 2)
    targets = [BOB, CAROL, "0x" + "dd" * 20, "0x" + "ee" * 20, "0x" + "ff" * 20]
    logs = [make_log(10, 0, to=BOB), make_log(11, 1, to=CAROL), make_log(12, 2, to=ALICE)]
    engine, store, router = make_engine(logs)
    job, _ = engine.start_log_scan(
        from_block=0, to_block=20, contracts=[TOKEN],
        event="Transfer(address indexed from, address indexed to, uint256 value)",
        filter_arg="to", filter_addresses=targets,
    )
    wait_for_terminal(job)
    assert job.status == "completed"
    # 5 addresses at batch 2 -> 3 server-side passes.
    topic2_sizes = [
        len(params[0]["topics"][2])
        for _m, params in router.client.calls
        if len(params[0].get("topics", [])) > 2
    ]
    assert topic2_sizes == [2, 2, 1]
    # Only transfers to filtered addresses landed (ALICE not in set).
    assert job.progress.rows_written == 2


def test_unindexed_filter_post_filters_engine_side():
    sig = "Moved(address indexed actor, address target)"
    from cerebro_mcp.rpc_scan.decoding import parse_event_signature

    topic0 = parse_event_signature(sig).topic0
    logs = [
        make_log(5, 0, topics=[topic0, _topic(ALICE)],
                 data="0x" + "00" * 12 + BOB[2:]),
        make_log(6, 1, topics=[topic0, _topic(ALICE)],
                 data="0x" + "00" * 12 + CAROL[2:]),
    ]
    engine, store, _ = make_engine(logs)
    job, warnings = engine.start_log_scan(
        from_block=0, to_block=10, contracts=[TOKEN],
        event=sig, filter_arg="target", filter_addresses=[BOB],
    )
    wait_for_terminal(job)
    assert job.status == "completed"
    assert any("not indexed" in w for w in warnings)
    assert job.progress.rows_written == 1  # CAROL row dropped engine-side


def test_unindexed_filter_rejects_huge_window():
    engine, _, _ = make_engine([], latest=10_000_000)
    with pytest.raises(ValueError, match="not indexed"):
        engine.start_log_scan(
            from_block=0, to_block=1_000_000, contracts=[TOKEN],
            event="Moved(address indexed actor, address target)",
            filter_arg="target", filter_addresses=[BOB],
        )


def test_max_rows_per_job_aborts_to_partial(monkeypatch):
    monkeypatch.setattr(settings, "RPC_SCAN_MAX_ROWS_PER_JOB", 1)
    logs = [make_log(1, 0), make_log(2, 1), make_log(3, 2)]
    engine, _, _ = make_engine(logs)
    job, _ = engine.start_log_scan(
        from_block=0, to_block=10, contracts=[TOKEN],
        event="Transfer(address,address,uint256)",
    )
    wait_for_terminal(job)
    assert job.status == "partial"
    assert "RPC_SCAN_MAX_ROWS_PER_JOB" in (job.error or "")
    assert job.resumable


def test_resume_from_registry_restarts_at_cursor():
    logs = [make_log(120, 0), make_log(180, 1)]
    engine, store, router = make_engine(logs)
    spec = {
        "from_block": 100, "to_block": 200, "contracts": [TOKEN],
        "event": "Transfer(address indexed from, address indexed to, uint256 value)",
        "decode_abi_address": "", "topics_override": None,
        "filter_arg": "", "filter_addresses": [], "filter_mode": "none",
        "filter_topic_position": None, "label": "resumed",
    }
    store.registry["feedc0de"] = {
        "job_id": "feedc0de", "kind": "logs", "label": "resumed",
        "table_name": "rpc_logs_feedc0de",
        "spec_json": __import__("json").dumps(spec),
        "status": "partial",
        "cursor_json": '{"next_block": 150, "address_index": 0, "chunk_index": 0, "skipped": []}',
        "rows_written": 1, "note": "server_restart",
        "created_at": 1.0, "updated_at": None,
    }
    job = engine.resume_job("feedc0de")
    wait_for_terminal(job)
    assert job.status == "completed"
    assert job.table_name == "rpc_logs_feedc0de"  # SAME table
    first_call = router.client.calls[0]
    assert int(first_call[1][0]["fromBlock"], 16) == 150  # resumed at cursor
    # Only the block-180 log is re-scanned; the 120 one was already durable.
    assert any(
        r and int(r[0][0]) == 180
        for t, _c, r in store._fake_client.inserts
        if t.endswith("rpc_logs_feedc0de")
    )


def test_resume_of_completed_job_is_a_teaching_error():
    engine, _, _ = make_engine([])
    job, _ = engine.start_log_scan(
        from_block=0, to_block=5, contracts=[TOKEN],
        event="Transfer(address,address,uint256)",
    )
    wait_for_terminal(job)
    with pytest.raises(ValueError, match="cannot be resumed"):
        engine.resume_job(job.id)


def test_unknown_job_teaching_error_mentions_registry():
    engine, _, _ = make_engine([])
    with pytest.raises(ValueError, match="rpc_list_scans"):
        engine.job_status("0badc0de")
