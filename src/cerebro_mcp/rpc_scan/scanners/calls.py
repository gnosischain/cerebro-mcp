"""Batch read scanners: Multicall3 view sweeps, storage slots, bytecode.

Unit = one address batch; resume restarts at ``cursor.address_index``.
All reads are pinned to a single resolved block.
"""
from __future__ import annotations

from typing import Any

from eth_utils import keccak

from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.chunking import chunked, run_pool
from cerebro_mcp.rpc_scan.jobs import ScanJob, commit_unit
from cerebro_mcp.rpc_scan.multicall import (
    decode_aggregate3,
    decode_outputs,
    encode_aggregate3,
    encode_call,
)
from cerebro_mcp.rpc_scan.schemas import (
    calls_table_ddl,
    code_table_ddl,
    default_for_ch_type,
    ch_type_for_solidity,
    storage_table_ddl,
)
from cerebro_mcp.rpc_scan.scratch import BatchInserter, ScratchStore

EIP1167_PREFIX = "363d3d373d3d3d363d73"
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"

_U256_DECIMAL_MAX = 10 ** 76 - 1


def _fit_u256(value: int, u256: str) -> int:
    """Decimal(76,0) fallback cannot hold the full uint256 range; hash-like
    values get clamped to 0 — the raw hex column stays authoritative."""
    if u256 == "UInt256":
        return value
    return value if 0 <= value <= _U256_DECIMAL_MAX else 0


def _value_as_address(value_hex: str) -> str:
    bare = value_hex.removeprefix("0x").rjust(64, "0")
    if bare[:24] == "0" * 24 and bare[24:] != "0" * 40:
        return "0x" + bare[24:].lower()
    return ""


def _normalize_output(sol_type: str, value: Any, u256: str) -> Any:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if sol_type == "address" and isinstance(value, str):
        return value.lower()
    if isinstance(value, (list, tuple)):
        return [str(v).lower() if isinstance(v, str) else str(v) for v in value]
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and sol_type.startswith("uint"):
        bits = int(sol_type[4:] or 256)
        if bits > 64:
            return _fit_u256(value, u256)
    return value


# ---------------------------------------------------------------------------
# Multicall view sweep
# ---------------------------------------------------------------------------

def run_call_scan(job: ScanJob, spec: dict[str, Any], *,
                  rpc: RpcRouter, store: ScratchStore) -> None:
    u256 = store.uint256_type()
    calls = spec["calls"]  # [{function, args, to, alias, selector, in_types, out_types}]
    aliases = [(c["alias"], c["out_types"]) for c in calls]
    ddl, order_by, columns = calls_table_ddl(aliases, u256=u256)
    store.create_scan_table(job.table_name, ddl, order_by)

    client = rpc.for_capability(needs_archive=spec.get("needs_archive", False))
    block = int(spec["resolved_block"])
    addresses = spec["addresses"]
    per_batch = max(1, settings.RPC_SCAN_MULTICALL_BATCH // max(1, len(calls)))
    batches = list(chunked(addresses, per_batch))
    job.progress.addresses_total = len(addresses)
    job.progress.addresses_done = min(
        len(addresses), job.cursor.address_index * per_batch
    )

    inserter = BatchInserter(
        store, job.table_name, columns,
        on_flush=lambda n: setattr(
            job.progress, "rows_written", job.progress.rows_written + n
        ),
    )

    for bi in range(job.cursor.address_index, len(batches)):
        if job.cancel_event.is_set():
            break
        batch = batches[bi]
        mc: list[tuple[str, bool, bytes]] = []
        for addr in batch:
            for c in calls:
                target = c["to"] or addr
                args = [addr if a == "{address}" else a for a in c["args"]]
                mc.append((
                    target, True,
                    encode_call(bytes.fromhex(c["selector"]), c["in_types"], args),
                ))
        job.progress.rpc_calls += 1
        raw = rpc.retry(lambda payload=mc: client.request(
            "eth_call",
            [{"to": settings.RPC_SCAN_MULTICALL_ADDRESS,
              "data": encode_aggregate3(payload)}, hex(block)],
        ))
        results = decode_aggregate3(raw)
        job.progress.items_found += len(results)
        for i, addr in enumerate(batch):
            row: list[Any] = [addr.lower(), block]
            for j, c in enumerate(calls):
                ok, ret = results[i * len(calls) + j]
                row.extend(_call_columns(c, ok, ret, u256))
            inserter.add(row)
        job.progress.addresses_done += len(batch)
        commit_unit(job, inserter, store, address_index=bi + 1)
    inserter.close()
    commit_unit(job, inserter, store, force_persist=True)


def _call_columns(c: dict[str, Any], ok: bool, ret: bytes, u256: str) -> list[Any]:
    """[success, out_0..n | out_raw, error] matching calls_table_ddl order."""
    out_types: list[str] = c["out_types"]
    if not ok:
        defaults = [
            default_for_ch_type(ch_type_for_solidity(t, u256=u256))
            for t in out_types
        ] or [""]
        return [0, *defaults, "reverted"]
    if not out_types:
        return [1, "0x" + ret.hex(), ""]
    decoded = decode_outputs(out_types, ret)
    if decoded is None:
        defaults = [
            default_for_ch_type(ch_type_for_solidity(t, u256=u256))
            for t in out_types
        ]
        return [1, *defaults, f"decode_failed: raw=0x{ret.hex()[:200]}"]
    values = [
        _normalize_output(t, v, u256) for t, v in zip(out_types, decoded)
    ]
    return [1, *values, ""]


# ---------------------------------------------------------------------------
# Storage slot sweep
# ---------------------------------------------------------------------------

def run_storage_scan(job: ScanJob, spec: dict[str, Any], *,
                     rpc: RpcRouter, store: ScratchStore) -> None:
    u256 = store.uint256_type()
    ddl, order_by, columns = storage_table_ddl(u256=u256)
    store.create_scan_table(job.table_name, ddl, order_by)
    client = rpc.for_capability(needs_archive=spec.get("needs_archive", False))
    block = int(spec["resolved_block"])
    slots: list[str] = spec["slots"]
    pairs = [(a, s) for a in spec["addresses"] for s in slots]
    job.progress.addresses_total = len(pairs)
    units = list(chunked(pairs, settings.RPC_SCAN_ADDRESS_BATCH))

    inserter = BatchInserter(
        store, job.table_name, columns,
        on_flush=lambda n: setattr(
            job.progress, "rows_written", job.progress.rows_written + n
        ),
    )

    def read_one(pair: tuple[str, str]) -> list[Any]:
        addr, slot = pair
        job.progress.rpc_calls += 1
        try:
            value = rpc.retry(lambda: client.request(
                "eth_getStorageAt", [addr, slot, hex(block)]
            ))
            value = str(value)
            uint = _fit_u256(int(value, 16), u256)
            return [addr, slot, block, value, uint, _value_as_address(value), ""]
        except Exception as exc:  # noqa: BLE001
            return [addr, slot, block, "", 0, "", str(exc)[:200]]

    for ui in range(job.cursor.address_index, len(units)):
        if job.cancel_event.is_set():
            break
        for row in run_pool(
            read_one, units[ui],
            workers=spec.get("workers") or settings.RPC_SCAN_STORAGE_WORKERS,
            should_stop=job.cancel_event.is_set,
        ):
            inserter.add(row)
            job.progress.addresses_done += 1
        if job.cancel_event.is_set():
            break
        commit_unit(job, inserter, store, address_index=ui + 1)
    inserter.close()
    commit_unit(job, inserter, store, force_persist=True)


# ---------------------------------------------------------------------------
# Bytecode sweep
# ---------------------------------------------------------------------------

def run_code_scan(job: ScanJob, spec: dict[str, Any], *,
                  rpc: RpcRouter, store: ScratchStore) -> None:
    store_bytecode = bool(spec.get("store_bytecode"))
    detect_proxies = bool(spec.get("detect_proxies", True))
    ddl, order_by, columns = code_table_ddl(store_bytecode=store_bytecode)
    store.create_scan_table(job.table_name, ddl, order_by)
    client = rpc.for_capability(needs_archive=spec.get("needs_archive", False))
    block = int(spec["resolved_block"])
    addresses = spec["addresses"]
    job.progress.addresses_total = len(addresses)
    units = list(chunked(addresses, settings.RPC_SCAN_ADDRESS_BATCH))

    inserter = BatchInserter(
        store, job.table_name, columns,
        on_flush=lambda n: setattr(
            job.progress, "rows_written", job.progress.rows_written + n
        ),
    )

    def read_slot_address(addr: str, slot: str) -> str:
        value = rpc.retry(lambda: client.request(
            "eth_getStorageAt", [addr, slot, hex(block)]
        ))
        return _value_as_address(str(value))

    def read_one(addr: str) -> list[Any]:
        try:
            code = str(rpc.retry(lambda: client.request(
                "eth_getCode", [addr, hex(block)]
            )) or "0x")
            job.progress.rpc_calls += 1
            bare = code.removeprefix("0x").lower()
            has_code = 1 if bare else 0
            code_bytes = bytes.fromhex(bare) if bare else b""
            code_hash = "0x" + keccak(code_bytes).hex() if bare else ""
            prefix_16 = "0x" + bare[:32] if bare else ""
            is_1167, impl_1167 = 0, ""
            idx = bare.find(EIP1167_PREFIX)
            if idx >= 0 and len(bare) >= idx + len(EIP1167_PREFIX) + 40:
                is_1167 = 1
                impl_1167 = "0x" + bare[idx + len(EIP1167_PREFIX):
                                        idx + len(EIP1167_PREFIX) + 40]
            impl_1967 = admin_1967 = beacon_1967 = ""
            if detect_proxies and has_code and not is_1167:
                job.progress.rpc_calls += 3
                impl_1967 = read_slot_address(addr, EIP1967_IMPL_SLOT)
                admin_1967 = read_slot_address(addr, EIP1967_ADMIN_SLOT)
                beacon_1967 = read_slot_address(addr, EIP1967_BEACON_SLOT)
            row: list[Any] = [
                addr, block, has_code, len(code_bytes), code_hash, prefix_16,
                is_1167, impl_1167, impl_1967, admin_1967, beacon_1967, "",
            ]
            if store_bytecode:
                row.append(code if has_code else "")
            return row
        except Exception as exc:  # noqa: BLE001
            row = [addr, block, 0, 0, "", "", 0, "", "", "", "", str(exc)[:200]]
            if store_bytecode:
                row.append("")
            return row

    for ui in range(job.cursor.address_index, len(units)):
        if job.cancel_event.is_set():
            break
        for row in run_pool(
            read_one, units[ui],
            workers=spec.get("workers") or settings.RPC_SCAN_CODE_WORKERS,
            should_stop=job.cancel_event.is_set,
        ):
            inserter.add(row)
            job.progress.addresses_done += 1
        if job.cancel_event.is_set():
            break
        commit_unit(job, inserter, store, address_index=ui + 1)
    inserter.close()
    commit_unit(job, inserter, store, force_persist=True)
