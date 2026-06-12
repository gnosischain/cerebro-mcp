"""Bulk binary-search block finder (deployment / storage_change per address)."""
from __future__ import annotations

from typing import Any

from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.chunking import chunked, run_pool
from cerebro_mcp.rpc_scan.jobs import ScanJob, commit_unit
from cerebro_mcp.rpc_scan.schemas import blocks_table_ddl
from cerebro_mcp.rpc_scan.scratch import BatchInserter, ScratchStore
from cerebro_mcp.rpc_scan.utils import find_block_for_address


def run_blocks_scan(job: ScanJob, spec: dict[str, Any], *,
                    rpc: RpcRouter, store: ScratchStore) -> None:
    ddl, order_by, columns = blocks_table_ddl()
    store.create_scan_table(job.table_name, ddl, order_by)
    client = rpc.archive if rpc.has_archive() else rpc.standard

    kind = spec["find_kind"]
    slot = spec.get("slot", "0x0")
    floor = int(spec["floor_block"])
    ceiling = int(spec["ceiling_block"])
    addresses = spec["addresses"]
    job.progress.addresses_total = len(addresses)
    units = list(chunked(addresses, settings.RPC_SCAN_ADDRESS_BATCH))

    inserter = BatchInserter(
        store, job.table_name, columns,
        on_flush=lambda n: setattr(
            job.progress, "rows_written", job.progress.rows_written + n
        ),
    )

    def find_one(address: str) -> list[Any]:
        result = find_block_for_address(
            rpc, client, kind=kind, address=address, slot=slot,
            floor=floor, ceiling=ceiling,
        )
        job.progress.rpc_calls += 1  # approximate; bisection is O(log N) reads
        return [result[c] for c in columns]

    for ui in range(job.cursor.address_index, len(units)):
        if job.cancel_event.is_set():
            break
        for row in run_pool(
            find_one, units[ui],
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
