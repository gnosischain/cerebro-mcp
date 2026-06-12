"""Trace scans, single-tx call trees, and capability probes."""
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

ALICE = "0x" + "aa" * 20
BOB = "0x" + "bb" * 20
CAROL = "0x" + "cc" * 20
TX1 = "0x" + "01" * 32


def make_trace(block, tx, frm, to, value, *, call_type="call", error=None,
               trace_address=(0,), trace_type="call"):
    t = {
        "type": trace_type,
        "action": {
            "callType": call_type, "from": frm, "to": to,
            "value": hex(value), "input": "0xa9059cbb" + "00" * 8,
        },
        "blockNumber": block,
        "transactionHash": tx,
        "traceAddress": list(trace_address),
        "result": {"gasUsed": "0x5208"},
    }
    if error:
        t["error"] = error
        t.pop("result")
    return t


def trace_handler(traces):
    def handler(method, params):
        if method == "trace_filter":
            filt = params[0]
            lo, hi = int(filt["fromBlock"], 16), int(filt["toBlock"], 16)
            out = []
            for t in traces:
                if not (lo <= t["blockNumber"] <= hi):
                    continue
                if "fromAddress" in filt and t["action"]["from"].lower() not in filt["fromAddress"]:
                    continue
                if "toAddress" in filt and t["action"]["to"].lower() not in filt["toAddress"]:
                    continue
                out.append(t)
            return out
        raise AssertionError(method)

    return handler


def make_engine(handler, archive=True, supported=None):
    store = InMemoryRegistryStore()
    router = FakeRouter(handler, latest=1000, archive=archive, supported=supported)
    engine = ScanEngine(
        FakeCH(), router=router, store=store,
        jobs=ScanJobManager(store, max_concurrent=2),
    )
    return engine, store, router


def rows_of(store, job):
    cols, rows = None, []
    for t, c, r in store._fake_client.inserts:
        if t.endswith(job.table_name):
            cols = c
            rows.extend(r)
    return cols, rows


def test_trace_scan_collects_value_calls_and_advances_frontier():
    traces = [
        make_trace(10, TX1, ALICE, BOB, 500),
        make_trace(150, TX1, ALICE, CAROL, 0),          # below min_value
        make_trace(150, TX1, BOB, CAROL, 9, trace_type="create"),  # not a call
        make_trace(220, TX1, ALICE, BOB, 7, error="Reverted"),     # failed
        make_trace(220, TX1, CAROL, ALICE, 42),
    ]
    engine, store, _ = make_engine(trace_handler(traces))
    job, _ = engine.start_trace_scan(
        from_block=0, to_block=250, from_addresses=None,
        min_value_wei="1",
    )
    wait_for_terminal(job)
    assert job.status == "completed"
    cols, rows = rows_of(store, job)
    by_value = {dict(zip(cols, r))["value_wei"] for r in rows}
    assert by_value == {500, 42}
    assert job.cursor.next_block == 251  # contiguous frontier reached the end
    assert job.progress.blocks_done == 251


def test_trace_scan_pushes_small_side_filters_to_node():
    traces = [make_trace(5, TX1, ALICE, BOB, 100)]
    engine, _, router = make_engine(trace_handler(traces))
    job, _ = engine.start_trace_scan(
        from_block=0, to_block=50, to_addresses=[BOB],
    )
    wait_for_terminal(job)
    filt = router.client.calls[0][1][0]
    assert filt["toAddress"] == [BOB]


def test_trace_scan_large_side_filters_engine_side():
    many = ["0x" + f"{i:040x}" for i in range(1, 1002)]
    ch = FakeCH(describe_rows=[["addr"]], result_rows=[[a] for a in many + [BOB]])
    traces = [
        make_trace(5, TX1, ALICE, BOB, 100),
        make_trace(6, TX1, ALICE, CAROL, 100),
    ]
    store = InMemoryRegistryStore()
    router = FakeRouter(trace_handler(traces), latest=1000)
    engine = ScanEngine(ch, router=router, store=store,
                        jobs=ScanJobManager(store, max_concurrent=1))
    job, warnings = engine.start_trace_scan(
        from_block=0, to_block=50,
        to_address_sql="SELECT addr FROM dbt.holders",
    )
    wait_for_terminal(job)
    assert any("engine-side" in w for w in warnings)
    filt = router.client.calls[0][1][0]
    assert "toAddress" not in filt  # too big to push
    _cols, rows = rows_of(store, job)
    assert len(rows) == 1  # CAROL filtered out engine-side


def test_trace_scan_window_failure_marks_partial_and_skips():
    def handler(method, params):
        filt = params[0]
        if int(filt["fromBlock"], 16) == 100:
            raise RuntimeError("node choked")
        return []

    engine, _, _ = make_engine(handler)
    job, _ = engine.start_trace_scan(from_block=0, to_block=250, from_addresses=[ALICE])
    wait_for_terminal(job)
    assert job.status == "partial"
    assert [100, 199] in job.cursor.skipped
    assert job.resumable


def test_trace_scan_resume_requeues_and_heals_skipped_windows():
    flaky = {"broken": True}
    payload = [make_trace(150, TX1, ALICE, BOB, 999)]

    def handler(method, params):
        filt = params[0]
        lo = int(filt["fromBlock"], 16)
        if lo == 100:
            if flaky["broken"]:
                raise RuntimeError("node choked")
            return payload
        return []

    engine, store, _ = make_engine(handler)
    job, _ = engine.start_trace_scan(from_block=0, to_block=250,
                                     from_addresses=[ALICE])
    wait_for_terminal(job)
    assert job.status == "partial"
    assert [100, 199] in job.cursor.skipped

    flaky["broken"] = False
    job2 = engine.resume_job(job.id)
    wait_for_terminal(job2)
    assert job2.status == "completed"          # skip healed
    assert job2.cursor.skipped == []
    _cols, rows = rows_of(store, job2)
    assert any(int(r[0]) == 150 for r in rows)  # the healed window's trace landed


def test_trace_scan_requires_archive_and_capability():
    engine, _, _ = make_engine(lambda m, p: [], archive=False)
    with pytest.raises(ValueError, match="archive node"):
        engine.start_trace_scan(from_block=0, to_block=10)

    engine2, _, _ = make_engine(
        lambda m, p: [], supported={"trace_filter": False},
    )
    with pytest.raises(ValueError, match="trace_filter is not available"):
        engine2.start_trace_scan(from_block=0, to_block=10)


def test_trace_scan_range_cap_teaching_error():
    engine, _, _ = make_engine(lambda m, p: [])
    with pytest.raises(ValueError, match="RPC_SCAN_TRACE_MAX_RANGE_BLOCKS"):
        engine.start_trace_scan(
            from_block=0,
            to_block=settings.RPC_SCAN_TRACE_MAX_RANGE_BLOCKS + 10,
        )


# ---------------------------------------------------------------------------
# rpc_trace_transaction
# ---------------------------------------------------------------------------

def call_tracer_handler(method, params):
    if method == "debug_traceTransaction":
        return {
            "type": "CALL", "from": ALICE, "to": BOB,
            "value": hex(1000), "gasUsed": "0xafff",
            "input": "0x12345678" + "00" * 4,
            "calls": [
                {"type": "DELEGATECALL", "from": BOB, "to": CAROL,
                 "value": "0x0", "gasUsed": "0x10", "input": "0x"},
                {"type": "CALL", "from": BOB, "to": CAROL,
                 "value": hex(400), "gasUsed": "0x20", "input": "0x",
                 "error": "execution reverted"},
            ],
        }
    raise AssertionError(method)


def test_trace_transaction_compact_tree_and_net_flows():
    engine, _, _ = make_engine(call_tracer_handler)
    result = engine.trace_transaction(TX1)
    tree = result["tree"]
    assert tree["from"] == ALICE and tree["to"] == BOB
    assert tree["selector"] == "0x12345678"
    assert len(tree["children"]) == 2
    assert tree["children"][1]["error"] == "execution reverted"
    # Net flows: only the successful 1000-wei top-level transfer counts.
    assert result["net_flows"] == {ALICE: -1000, BOB: 1000}


def test_trace_transaction_depth_truncation():
    def deep_handler(method, params):
        frame = {"type": "CALL", "from": ALICE, "to": BOB, "value": "0x0",
                 "gasUsed": "0x1", "input": "0x"}
        node = dict(frame)
        cursor = node
        for _ in range(5):
            child = dict(frame)
            cursor["calls"] = [child]
            cursor = child
        return node

    engine, _, _ = make_engine(deep_handler)
    result = engine.trace_transaction(TX1, max_depth=2)
    level2 = result["tree"]["children"][0]["children"][0]
    assert level2["truncated"] is True
    assert level2["hidden_children"] == 3


def test_trace_transaction_store_persists_frames():
    engine, store, _ = make_engine(call_tracer_handler)
    result = engine.trace_transaction(TX1, store_frames=True)
    table = result["scratch_table"].split(".")[-1]
    rows = store._fake_client.rows_for(table)
    assert len(rows) == 3  # root + 2 children
    assert any(r[2] == "1" for r in rows)  # trace_address path of second child


def test_trace_transaction_capability_teaching_error():
    engine, _, _ = make_engine(
        call_tracer_handler, supported={"debug_traceTransaction": False},
    )
    with pytest.raises(Exception, match="debug_traceTransaction is not available"):
        engine.trace_transaction(TX1)


def test_trace_transaction_bad_hash():
    engine, _, _ = make_engine(call_tracer_handler)
    with pytest.raises(ValueError, match="Bad transaction hash"):
        engine.trace_transaction("0x123")
