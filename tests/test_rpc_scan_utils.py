"""Binary-search block finders against fake monotonic chain data."""
import pytest

from cerebro_mcp.rpc_scan.utils import (
    block_at_timestamp,
    first_block_storage_changed,
    first_block_with_code,
    parse_timestamp,
)
from tests.rpc_scan_fakes import FakeRouter

ADDR = "0x" + "ab" * 20


def chain_handler(*, deploy_block=500, slot_change_block=700,
                  genesis_ts=1_000_000, block_seconds=10):
    def handler(method, params):
        if method == "eth_getBlockByNumber":
            block = int(params[0], 16)
            return {"timestamp": hex(genesis_ts + block * block_seconds)}
        if method == "eth_getCode":
            block = int(params[1], 16)
            return "0x6080" if block >= deploy_block else "0x"
        if method == "eth_getStorageAt":
            block = int(params[2], 16)
            return ("0x" + "11" * 32) if block >= slot_change_block else ("0x" + "00" * 32)
        raise AssertionError(method)

    return handler


def test_parse_timestamp_forms():
    assert parse_timestamp(1_700_000_000) == 1_700_000_000
    assert parse_timestamp("1700000000") == 1_700_000_000
    assert parse_timestamp("2026-06-01T00:00:00Z") == 1_780_272_000
    assert parse_timestamp("2026-06-01T00:00:00+00:00") == 1_780_272_000


def test_block_at_timestamp_bisects():
    router = FakeRouter(chain_handler(), latest=10_000)
    # Block N has timestamp 1_000_000 + 10N -> ts 1_005_000 is exactly block 500.
    assert block_at_timestamp(router, 1_005_000) == 500
    assert block_at_timestamp(router, 1_005_001) == 501
    # Clamping at both ends.
    assert block_at_timestamp(router, 0) == 1
    assert block_at_timestamp(router, 10**12) == 10_000
    # O(log N): far fewer header reads than blocks.
    assert len(router.client.calls) < 80


def test_first_block_with_code():
    router = FakeRouter(chain_handler(deploy_block=4321), latest=10_000)
    client = router.standard
    assert first_block_with_code(router, client, ADDR, 1, 10_000) == 4321
    # Never deployed within range.
    router2 = FakeRouter(chain_handler(deploy_block=20_000), latest=10_000)
    assert first_block_with_code(router2, router2.standard, ADDR, 1, 10_000) is None


def test_first_block_storage_changed():
    router = FakeRouter(chain_handler(slot_change_block=777), latest=10_000)
    client = router.standard
    found, before, after = first_block_storage_changed(
        router, client, ADDR, "0x0", 1, 10_000
    )
    assert found == 777
    assert before == "0x" + "00" * 32
    assert after == "0x" + "11" * 32

    found2, before2, after2 = first_block_storage_changed(
        router, client, ADDR, "0x0", 800, 10_000
    )
    assert found2 is None and before2 == after2
