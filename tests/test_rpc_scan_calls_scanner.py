"""End-to-end batch-read scans (multicall / storage / code) through the engine."""
import pytest
from eth_abi import decode as abi_decode, encode as abi_encode

from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.engine import ScanEngine
from cerebro_mcp.rpc_scan.jobs import ScanJobManager
from cerebro_mcp.rpc_scan.multicall import selector
from tests.rpc_scan_fakes import (
    FakeCH,
    FakeRouter,
    InMemoryRegistryStore,
    wait_for_terminal,
)

SAFE_A = "0x" + "0a" * 20
SAFE_B = "0x" + "0b" * 20
TOKEN = "0x" + "70" * 20
OWNER = "0x" + "0e" * 20
IMPL = "0x" + "1d" * 20

SEL_THRESHOLD = selector("getThreshold", []).hex()
SEL_BALANCE = selector("balanceOf", ["address"]).hex()
SEL_OWNERS = selector("getOwners", []).hex()


def make_engine(handler, archive=True):
    store = InMemoryRegistryStore()
    router = FakeRouter(handler, latest=1000, archive=archive)
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


# ---------------------------------------------------------------------------
# rpc_batch_call
# ---------------------------------------------------------------------------

def multicall_handler(balances=None, revert_thresholds=frozenset()):
    balances = balances or {}

    def handler(method, params):
        assert method == "eth_call"
        data = params[0]["data"]
        calls = abi_decode(["(address,bool,bytes)[]"], bytes.fromhex(data[2:])[4:])[0]
        results = []
        for target, _allow, calldata in calls:
            calldata = bytes(calldata)
            sel = calldata[:4].hex()
            if sel == SEL_THRESHOLD:
                if target.lower() in revert_thresholds:
                    results.append((False, b""))
                else:
                    results.append((True, abi_encode(["uint256"], [2])))
            elif sel == SEL_BALANCE:
                holder = abi_decode(["address"], calldata[4:])[0].lower()
                results.append((True, abi_encode(["uint256"], [balances.get(holder, 0)])))
            elif sel == SEL_OWNERS:
                results.append((True, abi_encode(["address[]"], [[OWNER]])))
            else:
                results.append((False, b""))
        return "0x" + abi_encode(["(bool,bytes)[]"], [results]).hex()

    return handler


def test_call_scan_wide_row_per_address():
    handler = multicall_handler(balances={SAFE_A: 111, SAFE_B: 0})
    engine, store, _ = make_engine(handler)
    job, warnings = engine.start_call_scan(
        calls=[
            {"function": "getThreshold()(uint256)", "alias": "threshold"},
            {"function": "balanceOf(address)(uint256)", "args": ["{address}"],
             "to": TOKEN, "alias": "usdc"},
            {"function": "getOwners()(address[])", "alias": "owners"},
        ],
        addresses=[SAFE_A, SAFE_B],
    )
    wait_for_terminal(job)
    assert job.status == "completed"
    cols, rows = rows_of(store, job)
    assert {"address", "threshold_success", "threshold_out_0", "usdc_out_0",
            "owners_out_0", "owners_error"} <= set(cols)
    by_addr = {dict(zip(cols, r))["address"]: dict(zip(cols, r)) for r in rows}
    assert by_addr[SAFE_A]["usdc_out_0"] == 111
    assert by_addr[SAFE_B]["usdc_out_0"] == 0
    assert by_addr[SAFE_A]["threshold_out_0"] == 2
    assert by_addr[SAFE_A]["owners_out_0"] == [OWNER]


def test_call_scan_revert_lands_as_failure_row():
    handler = multicall_handler(revert_thresholds={SAFE_B})
    engine, store, _ = make_engine(handler)
    job, _ = engine.start_call_scan(
        calls=[{"function": "getThreshold()(uint256)", "alias": "threshold"}],
        addresses=[SAFE_A, SAFE_B],
    )
    wait_for_terminal(job)
    cols, rows = rows_of(store, job)
    by_addr = {dict(zip(cols, r))["address"]: dict(zip(cols, r)) for r in rows}
    assert by_addr[SAFE_B]["threshold_success"] == 0
    assert by_addr[SAFE_B]["threshold_error"] == "reverted"
    assert by_addr[SAFE_A]["threshold_success"] == 1


def test_call_scan_batches_checkpoint(monkeypatch):
    monkeypatch.setattr(settings, "RPC_SCAN_MULTICALL_BATCH", 1)
    handler = multicall_handler()
    engine, _, router = make_engine(handler)
    job, _ = engine.start_call_scan(
        calls=[{"function": "getThreshold()(uint256)"}],
        addresses=[SAFE_A, SAFE_B],
    )
    wait_for_terminal(job)
    assert job.status == "completed"
    assert job.cursor.address_index == 2  # one unit per batch
    assert len(router.client.calls) == 2


def test_call_scan_rejects_mutators_and_bad_args():
    engine, _, _ = make_engine(multicall_handler())
    with pytest.raises(ValueError, match="state-changing"):
        engine.start_call_scan(
            calls=[{"function": "transfer(address,uint256)"}], addresses=[SAFE_A],
        )
    with pytest.raises(ValueError, match="argument count mismatch|input"):
        engine.start_call_scan(
            calls=[{"function": "balanceOf(address)(uint256)", "args": []}],
            addresses=[SAFE_A],
        )
    with pytest.raises(ValueError, match="cap 5"):
        engine.start_call_scan(
            calls=[{"function": f"f{i}()(uint256)"} for i in range(6)],
            addresses=[SAFE_A],
        )
    with pytest.raises(ValueError, match="Duplicate alias"):
        engine.start_call_scan(
            calls=[{"function": "a()(uint256)", "alias": "x"},
                   {"function": "b()(uint256)", "alias": "x"}],
            addresses=[SAFE_A],
        )


def test_pinned_block_without_archive_teaching_error():
    engine, _, _ = make_engine(multicall_handler(), archive=False)
    with pytest.raises(ValueError, match="GNOSIS_ARCHIVE_RPC_URL"):
        engine.start_call_scan(
            calls=[{"function": "getThreshold()(uint256)"}],
            addresses=[SAFE_A], block=123,
        )


# ---------------------------------------------------------------------------
# rpc_read_storage
# ---------------------------------------------------------------------------

def test_storage_scan_decodes_value_three_ways():
    slot0_values = {
        SAFE_A: "0x" + "00" * 12 + IMPL[2:],   # address-shaped
        SAFE_B: "0x" + "ff" * 32,               # hash-like, not address-shaped
    }

    def handler(method, params):
        assert method == "eth_getStorageAt"
        return slot0_values[params[0]]

    engine, store, _ = make_engine(handler)
    job, _ = engine.start_storage_scan(slots=[0], addresses=[SAFE_A, SAFE_B])
    wait_for_terminal(job)
    assert job.status == "completed"
    cols, rows = rows_of(store, job)
    by_addr = {dict(zip(cols, r))["address"]: dict(zip(cols, r)) for r in rows}
    assert by_addr[SAFE_A]["value_address"] == IMPL
    assert by_addr[SAFE_A]["value_uint"] == int(IMPL, 16)
    assert by_addr[SAFE_B]["value_address"] == ""  # top 12 bytes nonzero
    assert by_addr[SAFE_A]["slot"] == "0x0"


def test_storage_scan_per_address_error_does_not_kill_job():
    def handler(method, params):
        if params[0] == SAFE_B:
            raise RuntimeError("flaky node")
        return "0x" + "00" * 32

    engine, store, _ = make_engine(handler)
    job, _ = engine.start_storage_scan(slots=[0], addresses=[SAFE_A, SAFE_B])
    wait_for_terminal(job)
    assert job.status == "completed"
    cols, rows = rows_of(store, job)
    by_addr = {dict(zip(cols, r))["address"]: dict(zip(cols, r)) for r in rows}
    assert "flaky node" in by_addr[SAFE_B]["error"]
    assert by_addr[SAFE_A]["error"] == ""


def test_storage_scan_slot_validation():
    engine, _, _ = make_engine(lambda m, p: "0x0")
    with pytest.raises(ValueError, match="cap 8"):
        engine.start_storage_scan(slots=list(range(9)), addresses=[SAFE_A])
    with pytest.raises(ValueError, match="Bad storage slot"):
        engine.start_storage_scan(slots=["zz"], addresses=[SAFE_A])


# ---------------------------------------------------------------------------
# rpc_get_code
# ---------------------------------------------------------------------------

EIP1167_CODE = "0x363d3d373d3d3d363d73" + IMPL[2:] + "5af43d82803e903d91602b57fd5bf3"
PLAIN_CODE = "0x6080604052"
EOA = "0x" + "e0" * 20
PROXY_1967 = "0x" + "19" * 20
CLONE = "0x" + "c1" * 20
PLAIN = "0x" + "b0" * 20

EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


def code_handler(method, params):
    if method == "eth_getCode":
        return {
            EOA: "0x",
            CLONE: EIP1167_CODE,
            PLAIN: PLAIN_CODE,
            PROXY_1967: PLAIN_CODE,
        }[params[0]]
    if method == "eth_getStorageAt":
        if params[0] == PROXY_1967 and params[1] == EIP1967_IMPL_SLOT:
            return "0x" + "00" * 12 + IMPL[2:]
        return "0x" + "00" * 32
    raise AssertionError(method)


def test_code_scan_classifies_eoa_clone_and_proxy():
    engine, store, _ = make_engine(code_handler)
    job, _ = engine.start_code_scan(addresses=[EOA, CLONE, PLAIN, PROXY_1967])
    wait_for_terminal(job)
    assert job.status == "completed"
    cols, rows = rows_of(store, job)
    by_addr = {dict(zip(cols, r))["address"]: dict(zip(cols, r)) for r in rows}
    assert by_addr[EOA]["has_code"] == 0
    assert by_addr[CLONE]["is_eip1167"] == 1
    assert by_addr[CLONE]["eip1167_impl"] == IMPL
    assert by_addr[PROXY_1967]["eip1967_impl"] == IMPL
    assert by_addr[PLAIN]["is_eip1167"] == 0
    # Identical bytecode clusters by code_hash.
    assert by_addr[PLAIN]["code_hash"] == by_addr[PROXY_1967]["code_hash"]


def test_code_scan_skips_1967_reads_when_disabled():
    calls = []

    def handler(method, params):
        calls.append(method)
        return code_handler(method, params)

    engine, _, _ = make_engine(handler)
    job, _ = engine.start_code_scan(
        addresses=[PLAIN, PROXY_1967], detect_proxies=False,
    )
    wait_for_terminal(job)
    assert "eth_getStorageAt" not in calls
