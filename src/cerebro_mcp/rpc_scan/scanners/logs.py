"""eth_getLogs scanner: adaptive block chunking + chunked indexed-topic filters.

Address-filter policy:

* ``filter_mode == "indexed"`` — server-side at ANY set size: the address
  set is split into RPC_SCAN_ADDRESS_BATCH-sized topic chunks and the
  window is swept once per chunk (the forensics exp07 pattern; 73k
  addresses is ~123 cheap, selective passes).
* ``filter_mode == "unindexed"`` — engine-side post-filter; the engine has
  already validated the scan is tight (contracts/topic0 + bounded window).
  Rows that fail to decode are dropped (they cannot match the filter).
"""
from __future__ import annotations

from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.chunking import chunked, scan_adaptive
from cerebro_mcp.rpc_scan.decoding import (
    EventDecoder,
    pad_address_topic,
    parse_event_signature,
    parsed_event_from_abi,
)
from cerebro_mcp.rpc_scan.jobs import ScanJob, commit_unit
from cerebro_mcp.rpc_scan.schemas import default_for_ch_type, logs_table_ddl
from cerebro_mcp.rpc_scan.scratch import BatchInserter, ScratchStore


def build_decoders(spec: dict[str, Any], ch: ClickHouseManager | None) -> list[EventDecoder]:
    """Rebuildable from the persisted spec (resume-safe)."""
    if spec.get("event"):
        return [EventDecoder(parse_event_signature(spec["event"]), promote=True)]
    if spec.get("decode_abi_address"):
        if ch is None:
            return []
        from cerebro_mcp.clients.abi_resolver import resolve_abi

        record = resolve_abi(ch, spec["decode_abi_address"])
        return [
            EventDecoder(parsed_event_from_abi(item), promote=False)
            for item in record.abi
            if item.get("type") == "event" and item.get("name")
        ]
    return []


def _topics_for(spec: dict[str, Any], decoders: list[EventDecoder],
                address_chunk: list[str] | None) -> list[Any] | None:
    if spec.get("topics_override"):
        return spec["topics_override"]
    if not decoders:
        return None
    if len(decoders) == 1:
        topics: list[Any] = [decoders[0].topic0]
    else:
        topics = [[d.topic0 for d in decoders]]
    position = spec.get("filter_topic_position")
    if address_chunk and position:
        while len(topics) <= position:
            topics.append(None)
        topics[position] = [pad_address_topic(a) for a in address_chunk]
    while topics and topics[-1] is None:
        topics.pop()
    return topics


def _log_to_row(
    lg: dict[str, Any],
    decoders_by_topic0: dict[str, EventDecoder],
    promoted: list[tuple[str, str, str]],
    u256: str,
    post_filter_arg: str,
    post_filter_set: set[str] | None,
) -> list[Any] | None:
    from cerebro_mcp.rpc_scan.decoding import args_to_json
    from cerebro_mcp.rpc_scan.schemas import ch_type_for_solidity

    topics = lg.get("topics") or []
    padded = list(topics[:4]) + [""] * (4 - len(topics[:4]))
    event_name, args, decode_error = "", {}, ""
    decoder = decoders_by_topic0.get(topics[0]) if topics else None
    if decoder is not None:
        event_name = decoder.event_name
        args, decode_error = decoder.decode(topics, lg.get("data") or "0x")

    if post_filter_set is not None:
        value = args.get(post_filter_arg)
        if not isinstance(value, str) or value.lower() not in post_filter_set:
            return None

    row: list[Any] = [
        int(lg["blockNumber"], 16),
        (lg.get("transactionHash") or "").lower(),
        int(lg.get("transactionIndex") or "0x0", 16),
        int(lg.get("logIndex") or "0x0", 16),
        (lg.get("address") or "").lower(),
        padded[0], padded[1], padded[2], padded[3],
        lg.get("data") or "0x",
        event_name,
        args_to_json(args) if args else "{}",
        decode_error,
    ]
    for arg_name, _col, sol_type in promoted:
        ch_type = ch_type_for_solidity(sol_type, u256=u256)
        value = args.get(arg_name)
        if value is None:
            value = default_for_ch_type(ch_type)
        elif isinstance(value, bool):
            value = int(value)
        row.append(value)
    return row


def run_log_scan(
    job: ScanJob,
    spec: dict[str, Any],
    *,
    rpc: RpcRouter,
    store: ScratchStore,
    ch: ClickHouseManager | None,
) -> None:
    decoders = build_decoders(spec, ch)
    u256 = store.uint256_type()
    ddl, order_by, columns = logs_table_ddl(decoders, u256=u256)
    store.create_scan_table(job.table_name, ddl, order_by)
    client = rpc.for_capability(needs_archive=False)

    promoted = (
        decoders[0].promoted_columns()
        if len(decoders) == 1 and decoders[0].promote
        else []
    )
    decoders_by_topic0 = {d.topic0: d for d in decoders}

    filter_addresses = [a.lower() for a in spec.get("filter_addresses") or []]
    if spec.get("filter_mode") == "indexed" and filter_addresses:
        addr_chunks: list[list[str] | None] = list(
            chunked(filter_addresses, settings.RPC_SCAN_ADDRESS_BATCH)
        )
        post_filter_set = None
    elif spec.get("filter_mode") == "unindexed" and filter_addresses:
        addr_chunks = [None]
        post_filter_set = set(filter_addresses)
    else:
        addr_chunks = [None]
        post_filter_set = None

    from_block, to_block = int(spec["from_block"]), int(spec["to_block"])
    contracts = [a.lower() for a in spec.get("contracts") or []] or None
    job.progress.blocks_total = (to_block - from_block + 1) * len(addr_chunks)
    job.progress.addresses_total = len(filter_addresses)

    def bump_rows(n: int) -> None:
        job.progress.rows_written += n

    inserter = BatchInserter(store, job.table_name, columns, on_flush=bump_rows)

    start_chunk = job.cursor.chunk_index
    for ci in range(start_chunk, len(addr_chunks)):
        if job.cancel_event.is_set():
            break
        chunk = addr_chunks[ci]
        topics = _topics_for(spec, decoders, chunk)
        start = job.cursor.next_block if ci == start_chunk else from_block
        start = max(start, from_block)

        def fetch(lo: int, hi: int) -> list[Any]:
            params: dict[str, Any] = {"fromBlock": hex(lo), "toBlock": hex(hi)}
            if contracts:
                params["address"] = contracts
            if topics:
                params["topics"] = topics
            job.progress.rpc_calls += 1
            return client.request("eth_getLogs", [params])

        def on_skip(block: int, exc: Exception) -> None:
            job.progress.skipped_ranges += 1
            job.progress.last_error = f"block {block}: {exc}"
            job.cursor.skipped.append([block, block])

        for rng in scan_adaptive(
            start, to_block, fetch,
            init_chunk=settings.RPC_SCAN_LOG_INIT_CHUNK_BLOCKS,
            should_stop=job.cancel_event.is_set,
            on_skip=on_skip,
        ):
            job.progress.items_found += len(rng.items)
            for lg in rng.items:
                row = _log_to_row(
                    lg, decoders_by_topic0, promoted, u256,
                    spec.get("filter_arg", ""), post_filter_set,
                )
                if row is not None:
                    inserter.add(row)
            job.progress.blocks_done += rng.hi - rng.lo + 1
            # Unit checkpoint — fires for empty ranges too.
            commit_unit(job, inserter, store, next_block=rng.hi + 1, chunk_index=ci)
            if job.progress.rows_written > settings.RPC_SCAN_MAX_ROWS_PER_JOB:
                raise RuntimeError(
                    f"RPC_SCAN_MAX_ROWS_PER_JOB ({settings.RPC_SCAN_MAX_ROWS_PER_JOB}) "
                    "exceeded; job kept as partial — narrow the window or filters, "
                    "then resume."
                )
        if job.cancel_event.is_set():
            break
        # Pass finished: rewind the block cursor for the next chunk.
        commit_unit(
            job, inserter, store,
            next_block=from_block, chunk_index=ci + 1, force_persist=True,
        )
    inserter.close()
    commit_unit(job, inserter, store, force_persist=True)
