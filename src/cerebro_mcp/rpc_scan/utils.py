"""Binary-search block finders (monotonic bisections, O(log N) RPC reads).

Ports of the patterns proven in gp_rpc_forensics: ``block_at_timestamp``
(lib/rpc.py) and ``deployment_block`` (lib/safe_abi.py). All predicates are
monotonic: timestamps increase, code appears once (no selfdestruct
reappearance assumed), and ``storage_change`` finds the FIRST divergence
from the value at the floor block.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from cerebro_mcp.clients.raw_rpc import RawRpcClient, RpcRouter


def parse_timestamp(value: int | str) -> int:
    """Accept unix seconds (int or numeric string) or ISO-8601 (Z ok)."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    iso = text.replace("Z", "+00:00")
    dt = _dt.datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp())


def block_timestamp(rpc: RpcRouter, client: RawRpcClient, block: int) -> int:
    header = rpc.retry(lambda: client.request(
        "eth_getBlockByNumber", [hex(block), False]
    ))
    if not header:
        raise ValueError(f"Block {block} not found")
    return int(header["timestamp"], 16)


def block_at_timestamp(rpc: RpcRouter, ts: int, lo: int = 1,
                       hi: int | None = None) -> int:
    """First block whose timestamp is >= ts (clamped to [lo, hi])."""
    client = rpc.standard
    hi = hi if hi is not None else rpc.latest_block()
    if block_timestamp(rpc, client, lo) >= ts:
        return lo
    if block_timestamp(rpc, client, hi) < ts:
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if block_timestamp(rpc, client, mid) >= ts:
            hi = mid
        else:
            lo = mid
    return hi


def first_block_with_code(rpc: RpcRouter, client: RawRpcClient, address: str,
                          floor: int, ceiling: int) -> int | None:
    """First block where the address has code; None if never within range."""

    def has_code(block: int) -> bool:
        code = rpc.retry(lambda: client.request("eth_getCode", [address, hex(block)]))
        return bool(code) and code != "0x"

    if not has_code(ceiling):
        return None
    if has_code(floor):
        return floor
    lo, hi = floor, ceiling
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if has_code(mid):
            hi = mid
        else:
            lo = mid
    return hi


def first_block_storage_changed(
    rpc: RpcRouter, client: RawRpcClient, address: str, slot: str,
    floor: int, ceiling: int,
) -> tuple[int | None, str, str]:
    """First block where ``slot`` differs from its value at ``floor``.

    Returns (block | None, value_before, value_after). Assumes one
    transition within the range (monotonic predicate) — for slots that
    flip repeatedly this finds A change, not every change.
    """

    def read(block: int) -> str:
        return str(rpc.retry(lambda: client.request(
            "eth_getStorageAt", [address, slot, hex(block)]
        )))

    before = read(floor)
    after = read(ceiling)
    if after == before:
        return None, before, after
    lo, hi = floor, ceiling
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if read(mid) != before:
            hi = mid
        else:
            lo = mid
    return hi, before, after


def find_block_for_address(
    rpc: RpcRouter, client: RawRpcClient, *, kind: str, address: str,
    slot: str, floor: int, ceiling: int,
) -> dict[str, Any]:
    """One bulk-scan work item; returns a blocks-table-shaped dict."""
    try:
        if kind == "deployment":
            found = first_block_with_code(rpc, client, address, floor, ceiling)
            return {"address": address, "kind": kind,
                    "found_block": found or 0,
                    "value_before": "", "value_after": "",
                    "error": "" if found else "no code in range"}
        if kind == "storage_change":
            found, before, after = first_block_storage_changed(
                rpc, client, address, slot, floor, ceiling
            )
            return {"address": address, "kind": kind,
                    "found_block": found or 0,
                    "value_before": before, "value_after": after,
                    "error": "" if found else "no change in range"}
        raise ValueError(f"unknown find_block kind: {kind!r}")
    except Exception as exc:  # noqa: BLE001
        return {"address": address, "kind": kind, "found_block": 0,
                "value_before": "", "value_after": "", "error": str(exc)[:200]}
