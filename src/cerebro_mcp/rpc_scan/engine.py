"""ScanEngine — the facade the rpc_scan tool surface talks to.

Validates specs (all teaching errors originate here or in the tool layer),
creates scratch tables, submits jobs, and answers status/summary queries.
"""
from __future__ import annotations

import atexit
import json
import re
import threading
import time
from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.decoding import AmbiguousEventError, EventDecoder, parse_event_signature
from cerebro_mcp.rpc_scan.jobs import (
    RESUMABLE_STATUSES,
    TERMINAL_STATUSES,
    ScanCursor,
    ScanJob,
    ScanJobManager,
    new_job,
)
from cerebro_mcp.rpc_scan.schemas import DEDUP_KEYS
from cerebro_mcp.rpc_scan.scratch import ScratchStore
from cerebro_mcp.safety import validate_query

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SWEEP_INTERVAL_SECONDS = 6 * 3600


def _teaching_inline_cap(n: int) -> str:
    return (
        f"addresses has {n:,} entries; the inline cap is "
        f"{settings.RPC_SCAN_MAX_INLINE_ADDRESSES}. Pass "
        f'address_sql="SELECT <col> FROM <table>" instead — any size works '
        f"(any dbt model or a previous scan's scratch table can be the source)."
    )


class ScanEngine:
    def __init__(
        self,
        ch: ClickHouseManager,
        router: RpcRouter | None = None,
        store: ScratchStore | None = None,
        jobs: ScanJobManager | None = None,
    ):
        self._ch = ch
        self._router = router or RpcRouter.from_settings()
        self._store = store or ScratchStore()
        self._jobs = jobs or ScanJobManager(self._store)
        self._ready = False
        self._ready_lock = threading.Lock()
        self._sweeper_started = False

    # -- lifecycle -----------------------------------------------------------

    def _ensure_ready(self) -> None:
        with self._ready_lock:
            if self._ready:
                return
            self._store.ensure_ready()
            try:
                self._store.mark_orphans_on_startup()
            except Exception:  # noqa: BLE001
                pass
            self._start_maintenance()
            self._ready = True

    def _start_maintenance(self) -> None:
        if self._sweeper_started:
            return
        self._sweeper_started = True
        atexit.register(self._jobs.shutdown)

        def sweep_loop() -> None:
            while True:
                time.sleep(_SWEEP_INTERVAL_SECONDS)
                try:
                    self._store.sweep_expired()
                    self._jobs.cleanup_expired()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=sweep_loop, name="rpc-scan-sweeper", daemon=True).start()

    # -- shared validation -----------------------------------------------------

    def resolve_addresses(
        self,
        inline: list[str] | None,
        sql: str = "",
        *,
        required: bool = True,
        what: str = "addresses",
    ) -> list[str]:
        """Resolve an address set from an inline list XOR a read-only SELECT."""
        inline = inline or []
        sql = (sql or "").strip().rstrip(";")
        if inline and sql:
            raise ValueError(f"Provide exactly one of {what} / {what.rstrip('es')}_sql, not both.")
        if not inline and not sql:
            if required:
                raise ValueError(f"Provide exactly one of {what} / {what.rstrip('es')}_sql.")
            return []

        if inline:
            if len(inline) > settings.RPC_SCAN_MAX_INLINE_ADDRESSES:
                raise ValueError(_teaching_inline_cap(len(inline)))
            bad = [a for a in inline if not _ADDRESS_RE.fullmatch(str(a))]
            if bad:
                raise ValueError(f"Invalid address(es): {', '.join(map(str, bad[:5]))}")
            return sorted({a.lower() for a in inline})

        is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
        if not is_valid:
            raise ValueError(f"address_sql rejected: {error}")
        client = self._ch.get_client("dbt")
        describe = client.query(f"DESCRIBE ({sql})").result_rows
        if len(describe) != 1:
            raise ValueError(
                f"address_sql must SELECT exactly one address column; got "
                f"{len(describe)}. Wrap it: SELECT <col> FROM (<your sql>)."
            )
        col = str(describe[0][0])
        rows = client.query(
            f"SELECT DISTINCT lower(toString(`{col}`)) AS address FROM ({sql}) "
            f"ORDER BY address"
        ).result_rows
        addresses = [str(r[0]) for r in rows if _ADDRESS_RE.fullmatch(str(r[0]))]
        if not addresses:
            raise ValueError("address_sql returned no valid 0x… addresses.")
        if len(addresses) > settings.RPC_SCAN_MAX_ADDRESSES:
            raise ValueError(
                f"address_sql resolved {len(addresses):,} addresses, above the "
                f"RPC_SCAN_MAX_ADDRESSES cap ({settings.RPC_SCAN_MAX_ADDRESSES:,}). "
                f"Add a WHERE/LIMIT to narrow the set."
            )
        return addresses

    def resolve_block(self, value: int | str | None) -> int:
        if value is None or value == "" or value == "latest":
            return self._router.latest_block()
        if isinstance(value, int):
            return value
        s = str(value).strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        try:
            return int(s)
        except ValueError as exc:
            raise ValueError(f"Bad block identifier: {value!r}") from exc

    def require_archive_for(self, block: int, *, context: str) -> None:
        if not self._router.has_archive():
            raise ValueError(
                f"Block {block:,} needs archive state for {context}. "
                f'Set GNOSIS_ARCHIVE_RPC_URL, or use block="latest" for current state.'
            )

    # -- log scans ---------------------------------------------------------------

    def start_log_scan(
        self,
        *,
        from_block: int | str,
        to_block: int | str = "latest",
        contracts: list[str] | None = None,
        event: str = "",
        decode_abi_address: str = "",
        topics: list[Any] | None = None,
        filter_arg: str = "",
        filter_addresses: list[str] | None = None,
        filter_address_sql: str = "",
        label: str = "",
    ) -> tuple[ScanJob, list[str]]:
        """Validate, register, and submit a log scan. Returns (job, warnings)."""
        self._ensure_ready()
        warnings: list[str] = []

        lo = self.resolve_block(from_block)
        hi = self.resolve_block(to_block)
        if lo > hi:
            raise ValueError(f"from_block ({lo:,}) is after to_block ({hi:,}).")

        contracts = contracts or []
        if len(contracts) > 100:
            raise ValueError(
                f"contracts has {len(contracts)} entries (cap 100). For larger "
                "emitter sets, scan by event topic and post-filter in SQL."
            )
        bad = [a for a in contracts if not _ADDRESS_RE.fullmatch(str(a))]
        if bad:
            raise ValueError(f"Invalid contract address(es): {', '.join(map(str, bad[:5]))}")

        modes = [bool(event), bool(decode_abi_address), bool(topics)]
        if sum(modes) > 1:
            raise ValueError(
                "Pass at most ONE of event / decode_abi_address / topics."
            )
        if decode_abi_address and not _ADDRESS_RE.fullmatch(decode_abi_address):
            raise ValueError(f"Invalid decode_abi_address: {decode_abi_address!r}")

        decoder: EventDecoder | None = None
        if event:
            decoder = EventDecoder(parse_event_signature(event), promote=True)

        filter_set = self.resolve_addresses(
            filter_addresses, filter_address_sql,
            required=False, what="filter_addresses",
        )
        filter_mode = "none"
        filter_topic_position: int | None = None
        if filter_set:
            if not filter_arg:
                raise ValueError(
                    "filter_addresses/filter_address_sql needs filter_arg — the "
                    "decoded event argument to match (e.g. filter_arg=\"to\")."
                )
            if decoder is None:
                raise ValueError(
                    "filter_arg needs an `event` signature so the engine knows "
                    "the argument layout. Pass event=\"…\" (with indexed markers "
                    "for non-well-known events)."
                )
            exists, position = decoder.filter_layout(filter_arg)
            if not exists:
                names = [i.name for i in decoder.event.variants[0]]
                raise ValueError(
                    f'filter_arg "{filter_arg}" is not an argument of '
                    f"{decoder.event.canonical}. Arguments: {', '.join(names)}."
                )
            if position is not None:
                filter_mode = "indexed"
                filter_topic_position = position
            else:
                filter_mode = "unindexed"
                window = hi - lo + 1
                if window > settings.RPC_SCAN_UNINDEXED_FILTER_MAX_BLOCKS:
                    raise ValueError(
                        f'filter_arg "{filter_arg}" is not indexed, so filtering '
                        f"happens engine-side after decoding every matching log. "
                        f"That requires a window <= "
                        f"{settings.RPC_SCAN_UNINDEXED_FILTER_MAX_BLOCKS:,} blocks "
                        f"(got {window:,}). Narrow the window, or filter on an "
                        f"indexed argument instead."
                    )
                warnings.append(
                    f'filter_arg "{filter_arg}" is not indexed — the scan decodes '
                    "every matching log and filters engine-side. Rows that fail "
                    "to decode are dropped."
                )

        window = hi - lo + 1
        if window > 1_000_000:
            warnings.append(
                f"Window is {window:,} blocks — the job auto-chunks; no action "
                f"needed, but expect a long scan. Consider a tighter window if "
                f"you only need a specific period."
            )
        if not event and not decode_abi_address and not topics and not contracts:
            warnings.append(
                "No event, topics, or contracts filter — this scans EVERY log "
                "in the window. Expect a very large scratch table."
            )

        spec: dict[str, Any] = {
            "from_block": lo,
            "to_block": hi,
            "contracts": [a.lower() for a in contracts],
            "event": event,
            "decode_abi_address": decode_abi_address.lower() if decode_abi_address else "",
            "topics_override": topics or None,
            "filter_arg": filter_arg,
            "filter_addresses": filter_set,
            "filter_mode": filter_mode,
            "filter_topic_position": filter_topic_position,
            "label": label,
        }
        job = new_job("logs", label, spec)
        job.cursor.next_block = lo
        return self._submit(job), warnings

    # -- batch reads (multicall / storage / code) ---------------------------------

    def _resolve_pinned_block(self, block: int | str, *, context: str) -> tuple[int, bool]:
        """(resolved_block, needs_archive). Non-latest pins require archive."""
        is_latest = block in (None, "", "latest")
        resolved = self.resolve_block(block)
        if not is_latest:
            self.require_archive_for(resolved, context=context)
        return resolved, not is_latest

    def start_call_scan(
        self,
        *,
        calls: list[dict[str, Any]],
        addresses: list[str] | None = None,
        address_sql: str = "",
        block: int | str = "latest",
        label: str = "",
    ) -> tuple[ScanJob, list[str]]:
        from cerebro_mcp.rpc_scan.multicall import (
            KNOWN_MUTATOR_NAMES,
            parse_signature,
            selector,
        )

        self._ensure_ready()
        warnings: list[str] = []
        if not calls:
            raise ValueError("calls is empty — pass at least one call spec.")
        if len(calls) > 5:
            raise ValueError(
                f"calls has {len(calls)} entries (cap 5). Split into multiple "
                "rpc_batch_call jobs; the scratch tables join on address."
            )

        normalized: list[dict[str, Any]] = []
        seen_aliases: set[str] = set()
        for i, raw in enumerate(calls):
            if not isinstance(raw, dict) or not raw.get("function"):
                raise ValueError(f"calls[{i}] needs a 'function' signature.")
            name, in_types, out_types = parse_signature(str(raw["function"]))
            if name in KNOWN_MUTATOR_NAMES:
                raise ValueError(
                    f"{raw['function']} is state-changing. rpc_batch_call "
                    "executes via eth_call (read-only), so the result would be "
                    "meaningless. Did you mean a balance/allowance read?"
                )
            args = list(raw.get("args") or [])
            if len(args) != len(in_types):
                raise ValueError(
                    f"calls[{i}] ({name}): signature has {len(in_types)} "
                    f"input(s) but args has {len(args)}. Use \"{{address}}\" as "
                    "the placeholder for the swept address."
                )
            to = str(raw.get("to") or "")
            if to and not _ADDRESS_RE.fullmatch(to):
                raise ValueError(f"calls[{i}] has an invalid 'to' address: {to!r}")
            alias = str(raw.get("alias") or name)
            alias = re.sub(r"[^A-Za-z0-9_]", "_", alias)[:40] or f"call{i}"
            if alias in seen_aliases:
                raise ValueError(f"Duplicate alias {alias!r} — give each call a unique alias.")
            seen_aliases.add(alias)
            if not out_types:
                warnings.append(
                    f"{name}: no return types declared — results land as raw "
                    f"hex in `{alias}_out_raw`. Declare them cast-style, e.g. "
                    f'"balanceOf(address)(uint256)".'
                )
            normalized.append({
                "function": str(raw["function"]),
                "args": args,
                "to": to.lower(),
                "alias": alias,
                "selector": selector(name, in_types).hex(),
                "in_types": in_types,
                "out_types": out_types,
            })

        resolved_block, needs_archive = self._resolve_pinned_block(
            block, context="rpc_batch_call"
        )
        spec: dict[str, Any] = {
            "calls": normalized,
            "addresses": self.resolve_addresses(addresses, address_sql),
            "resolved_block": resolved_block,
            "needs_archive": needs_archive,
            "label": label,
        }
        return self._submit(new_job("calls", label, spec)), warnings

    def start_storage_scan(
        self,
        *,
        slots: list[int | str],
        addresses: list[str] | None = None,
        address_sql: str = "",
        block: int | str = "latest",
        label: str = "",
    ) -> tuple[ScanJob, list[str]]:
        self._ensure_ready()
        if not slots:
            raise ValueError("slots is empty — pass at least one storage slot.")
        if len(slots) > 8:
            raise ValueError(
                f"slots has {len(slots)} entries (cap 8). Run multiple jobs "
                "for wider slot sweeps."
            )
        normalized_slots: list[str] = []
        for s in slots:
            if isinstance(s, int):
                normalized_slots.append(hex(s))
            else:
                text = str(s).strip().lower()
                try:
                    normalized_slots.append(hex(int(text, 16 if text.startswith("0x") else 10)))
                except ValueError as exc:
                    raise ValueError(f"Bad storage slot: {s!r}") from exc

        resolved_block, needs_archive = self._resolve_pinned_block(
            block, context="rpc_read_storage"
        )
        spec: dict[str, Any] = {
            "slots": normalized_slots,
            "addresses": self.resolve_addresses(addresses, address_sql),
            "resolved_block": resolved_block,
            "needs_archive": needs_archive,
            "label": label,
        }
        return self._submit(new_job("storage", label, spec)), []

    def start_code_scan(
        self,
        *,
        addresses: list[str] | None = None,
        address_sql: str = "",
        block: int | str = "latest",
        detect_proxies: bool = True,
        store_bytecode: bool = False,
        label: str = "",
    ) -> tuple[ScanJob, list[str]]:
        self._ensure_ready()
        warnings: list[str] = []
        if store_bytecode:
            warnings.append(
                "store_bytecode=True stores full bytecode hex per contract — "
                "expect a large scratch table."
            )
        resolved_block, needs_archive = self._resolve_pinned_block(
            block, context="rpc_get_code"
        )
        spec: dict[str, Any] = {
            "addresses": self.resolve_addresses(addresses, address_sql),
            "resolved_block": resolved_block,
            "needs_archive": needs_archive,
            "detect_proxies": detect_proxies,
            "store_bytecode": store_bytecode,
            "label": label,
        }
        return self._submit(new_job("code", label, spec)), warnings

    # -- traces ---------------------------------------------------------------------

    def start_trace_scan(
        self,
        *,
        from_block: int | str,
        to_block: int | str,
        from_addresses: list[str] | None = None,
        from_address_sql: str = "",
        to_addresses: list[str] | None = None,
        to_address_sql: str = "",
        min_value_wei: str = "0",
        include_failed: bool = False,
        label: str = "",
    ) -> tuple[ScanJob, list[str]]:
        from cerebro_mcp.clients.raw_rpc import TEACHING_TRACE_UNSUPPORTED

        self._ensure_ready()
        warnings: list[str] = []
        if not self._router.has_archive():
            raise ValueError(
                "Trace scans require an archive node: set GNOSIS_ARCHIVE_RPC_URL "
                "to a trace-capable endpoint (Erigon, or Nethermind with the "
                "Trace module)."
            )
        if not self._router.supports("trace_filter"):
            raise ValueError(TEACHING_TRACE_UNSUPPORTED)

        lo = self.resolve_block(from_block)
        hi = self.resolve_block(to_block)
        if lo > hi:
            raise ValueError(f"from_block ({lo:,}) is after to_block ({hi:,}).")
        window = hi - lo + 1
        if window > settings.RPC_SCAN_TRACE_MAX_RANGE_BLOCKS:
            raise ValueError(
                f"Trace window is {window:,} blocks, above the "
                f"RPC_SCAN_TRACE_MAX_RANGE_BLOCKS cap "
                f"({settings.RPC_SCAN_TRACE_MAX_RANGE_BLOCKS:,}). trace_filter "
                f"costs ~100 blocks per call — split into several narrower "
                f"scans around the period you actually need."
            )

        from_set = self.resolve_addresses(
            from_addresses, from_address_sql, required=False, what="from_addresses",
        )
        to_set = self.resolve_addresses(
            to_addresses, to_address_sql, required=False, what="to_addresses",
        )
        if not from_set and not to_set:
            warnings.append(
                "No from/to address filter — this collects EVERY value-bearing "
                "call in the window."
            )

        # Push the small side to the node; filter the large side engine-side.
        push_threshold = 1000

        def split(side: list[str]) -> tuple[list[str], list[str]]:
            if side and len(side) <= push_threshold:
                return side, []
            return [], side

        push_from, engine_from = split(from_set)
        push_to, engine_to = split(to_set)
        if engine_from or engine_to:
            warnings.append(
                "A large address side is filtered engine-side (the node filter "
                "caps out around 1k addresses); the scan still sweeps the full "
                "window once."
            )

        spec: dict[str, Any] = {
            "from_block": lo,
            "to_block": hi,
            "push_from": push_from,
            "push_to": push_to,
            "engine_filter_from": engine_from,
            "engine_filter_to": engine_to,
            "min_value_wei": str(int(str(min_value_wei), 0)),
            "include_failed": include_failed,
            "label": label,
        }
        job = new_job("traces", label, spec)
        job.cursor.next_block = lo
        return self._submit(job), warnings

    def trace_transaction(self, tx_hash: str, *, max_depth: int = 8,
                          store_frames: bool = False) -> dict[str, Any]:
        """Sync single-tx call tree; optionally persists frames to a scratch
        table (registered as a completed 'traces' job)."""
        from cerebro_mcp.rpc_scan.scanners.traces import (
            net_native_flows,
            summarize_transaction_trace,
        )

        tx_hash = tx_hash.strip()
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
            raise ValueError(f"Bad transaction hash: {tx_hash!r}")
        tree = summarize_transaction_trace(self._router, tx_hash, max_depth=max_depth)
        out: dict[str, Any] = {
            "tx_hash": tx_hash.lower(),
            "tree": tree,
            "net_flows": net_native_flows(tree),
        }
        if store_frames:
            out["scratch_table"] = self._store_trace_frames(tx_hash.lower(), tree)
        return out

    def _store_trace_frames(self, tx_hash: str, tree: dict[str, Any]) -> str:
        from cerebro_mcp.rpc_scan.schemas import traces_table_ddl

        self._ensure_ready()
        job = new_job("traces", f"tx {tx_hash[:10]}", {"tx_hash": tx_hash})
        u256 = self._store.uint256_type()
        ddl, order_by, columns = traces_table_ddl(u256=u256)
        self._store.create_scan_table(job.table_name, ddl, order_by)
        rows: list[list[Any]] = []

        def walk(node: dict[str, Any], path: list[int]) -> None:
            rows.append([
                0, tx_hash, ".".join(str(i) for i in path),
                node["call_type"].lower(), node["from"], node["to"],
                node["value_wei"], node["gas_used"], node["selector"],
                0 if node["error"] else 1, node["error"][:200],
            ])
            for i, child in enumerate(node["children"]):
                walk(child, path + [i])

        walk(tree, [])
        self._store.insert_rows(job.table_name, columns, rows)
        job.status = "completed"
        job.progress.rows_written = len(rows)
        job.completed_at = time.time()
        self._store.upsert_job_row(job.registry_row())
        return f"{self._store.database}.{job.table_name}"

    # -- block finders ----------------------------------------------------------------

    def find_block_at_timestamp(self, timestamp: int | str) -> dict[str, Any]:
        from cerebro_mcp.rpc_scan.utils import block_at_timestamp, parse_timestamp

        ts = parse_timestamp(timestamp)
        block = block_at_timestamp(self._router, ts)
        return {"timestamp": ts, "block": block}

    def find_blocks_sync(
        self, *, kind: str, addresses: list[str], slot: str,
        floor: int, ceiling: int,
    ) -> list[dict[str, Any]]:
        from cerebro_mcp.rpc_scan.utils import find_block_for_address

        client = self._router.archive if self._router.has_archive() else self._router.standard
        return [
            find_block_for_address(
                self._router, client, kind=kind, address=a, slot=slot,
                floor=floor, ceiling=ceiling,
            )
            for a in addresses
        ]

    def start_blocks_scan(
        self, *, kind: str, addresses: list[str], slot: str,
        floor: int, ceiling: int, label: str = "",
    ) -> ScanJob:
        self._ensure_ready()
        spec: dict[str, Any] = {
            "find_kind": kind,
            "addresses": addresses,
            "slot": slot,
            "floor_block": floor,
            "ceiling_block": ceiling,
            "label": label,
        }
        return self._submit(new_job("blocks", label, spec))

    # -- job control ---------------------------------------------------------------

    def _runner_for(self, job: ScanJob):
        if job.kind == "logs":
            from cerebro_mcp.rpc_scan.scanners.logs import run_log_scan

            return lambda j: run_log_scan(
                j, j.spec, rpc=self._router, store=self._store, ch=self._ch
            )
        if job.kind == "calls":
            from cerebro_mcp.rpc_scan.scanners.calls import run_call_scan

            return lambda j: run_call_scan(j, j.spec, rpc=self._router, store=self._store)
        if job.kind == "storage":
            from cerebro_mcp.rpc_scan.scanners.calls import run_storage_scan

            return lambda j: run_storage_scan(j, j.spec, rpc=self._router, store=self._store)
        if job.kind == "code":
            from cerebro_mcp.rpc_scan.scanners.calls import run_code_scan

            return lambda j: run_code_scan(j, j.spec, rpc=self._router, store=self._store)
        if job.kind == "traces":
            from cerebro_mcp.rpc_scan.scanners.traces import run_trace_scan

            return lambda j: run_trace_scan(j, j.spec, rpc=self._router, store=self._store)
        if job.kind == "blocks":
            from cerebro_mcp.rpc_scan.scanners.blocks import run_blocks_scan

            return lambda j: run_blocks_scan(j, j.spec, rpc=self._router, store=self._store)
        raise ValueError(f"Unknown scan kind: {job.kind!r}")

    def _submit(self, job: ScanJob) -> ScanJob:
        return self._jobs.submit(job, self._runner_for(job))

    def cancel_job(self, job_id: str) -> bool:
        return self._jobs.cancel(job_id)

    def resume_job(self, job_id: str) -> ScanJob:
        self._ensure_ready()
        job = self._jobs.get(job_id)
        if job is not None:
            if job.status in ("pending", "running"):
                raise ValueError(f"Job '{job_id}' is still {job.status}; nothing to resume.")
            if job.status not in RESUMABLE_STATUSES:
                raise ValueError(
                    f"Job '{job_id}' is {job.status} and cannot be resumed. "
                    "Only partial/cancelled scans resume; start a new scan instead."
                )
            job.status = "pending"
            job.error = None
            job.completed_at = None
            job.cancel_event = threading.Event()
            job.submitted_at = time.time()
            # This run's skip counter resets; unresolved skips persist in
            # cursor.skipped and are re-queued by the scanner (traces).
            job.progress.skipped_ranges = 0
            job.progress.last_error = ""
            return self._submit(job)

        row = self._store.load_job_row(job_id)
        if row is None:
            raise ValueError(
                f"Job '{job_id}' not found. The server may have restarted; "
                "rpc_list_scans shows the persisted registry."
            )
        if row["status"] not in RESUMABLE_STATUSES:
            raise ValueError(
                f"Job '{job_id}' is {row['status']} and cannot be resumed. "
                "Only partial/cancelled scans resume."
            )
        spec = json.loads(row["spec_json"] or "{}")
        job = ScanJob(
            id=row["job_id"],
            kind=row["kind"],
            label=row.get("label", ""),
            spec=spec,
            table_name=row["table_name"],
            cursor=ScanCursor.from_json(row.get("cursor_json", "{}")),
        )
        job.progress.rows_written = int(row.get("rows_written", 0))
        return self._submit(job)

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_ready()
        seen: dict[str, dict[str, Any]] = {}
        for job in self._jobs.list_jobs():
            seen[job.id] = self._snapshot(job)
        for row in self._store.list_job_rows(limit=limit):
            if row["job_id"] not in seen:
                seen[row["job_id"]] = self._snapshot_from_row(row)
        ordered = sorted(seen.values(), key=lambda s: s.get("_sort", 0), reverse=True)
        return ordered[: max(1, limit)]

    def job_status(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is not None:
            return self._snapshot(job)
        self._ensure_ready()
        row = self._store.load_job_row(job_id)
        if row is None:
            raise ValueError(
                f"Job '{job_id}' not found. The server may have restarted; "
                "rpc_list_scans shows surviving scratch tables from the "
                "persisted registry."
            )
        return self._snapshot_from_row(row)

    def wait_or_status(self, job: ScanJob, sync_wait_seconds: float) -> dict[str, Any]:
        deadline = time.time() + max(
            0.0, min(sync_wait_seconds, settings.RPC_SCAN_SYNC_WAIT_MAX_SECONDS)
        )
        while time.time() < deadline and job.status not in TERMINAL_STATUSES:
            time.sleep(0.25)
        return self._snapshot(job)

    # -- summaries ---------------------------------------------------------------

    def summarize(self, job_id: str) -> dict[str, Any]:
        status = self.job_status(job_id)
        kind = status["kind"]
        table = status["table_name"]
        spec = status.get("spec") or {}
        dedup = DEDUP_KEYS.get(kind, "(1)")

        stat_exprs: dict[str, str] = {}
        top_column = ""
        sample_columns: list[str] = []
        if kind == "logs":
            stat_exprs = {
                "min_block": "min(block_number)",
                "max_block": "max(block_number)",
                "distinct_tx": "uniqExact(tx_hash)",
                "distinct_emitters": "uniqExact(address)",
            }
            sample_columns = ["block_number", "tx_hash", "log_index", "address", "event_name"]
            top_column = "address"
            if spec.get("event"):
                try:
                    decoder = EventDecoder(parse_event_signature(spec["event"]), promote=True)
                    promoted = decoder.promoted_columns()
                    sample_columns += [col for _a, col, _t in promoted]
                    if spec.get("filter_arg"):
                        match = [
                            col for arg, col, _t in promoted
                            if arg == spec["filter_arg"]
                        ]
                        if match:
                            top_column = match[0]
                except (ValueError, AmbiguousEventError):
                    pass
        elif kind == "storage":
            stat_exprs = {"distinct_addresses": "uniqExact(address)"}
            top_column = "value_address"
            sample_columns = ["address", "slot", "value", "value_uint", "value_address"]
        elif kind == "code":
            stat_exprs = {
                "distinct_addresses": "uniqExact(address)",
                "contracts": "sum(has_code)",
                "eip1167_clones": "sum(is_eip1167)",
            }
            top_column = "code_hash"
            sample_columns = ["address", "has_code", "code_size", "code_hash",
                              "is_eip1167", "eip1167_impl"]
        elif kind == "calls":
            stat_exprs = {"distinct_addresses": "uniqExact(address)"}
            sample_columns = ["address"]
            for c in spec.get("calls") or []:
                alias = c.get("alias", "")
                if alias:
                    sample_columns.append(f"{alias}_success")
                    out_types = c.get("out_types") or []
                    sample_columns += [f"{alias}_out_{i}" for i in range(len(out_types))]
                    if not out_types:
                        sample_columns.append(f"{alias}_out_raw")
        elif kind == "traces":
            stat_exprs = {
                "min_block": "min(block_number)",
                "max_block": "max(block_number)",
                "distinct_tx": "uniqExact(tx_hash)",
                "total_value_wei": "sum(value_wei)",
            }
            top_column = "to_address"
            sample_columns = ["block_number", "tx_hash", "from_address",
                              "to_address", "value_wei", "call_type"]
        elif kind == "blocks":
            top_column = "kind"
            sample_columns = ["address", "kind", "found_block"]

        summary = self._store.table_summary(
            table,
            dedup_key=dedup,
            stat_exprs=stat_exprs,
            top_column=top_column,
            sample_columns=sample_columns,
        )
        summary["status"] = status
        return summary

    # -- snapshots ---------------------------------------------------------------

    def _snapshot(self, job: ScanJob) -> dict[str, Any]:
        progress = job.progress.as_dict()
        if job.progress.blocks_total:
            progress["pct_blocks"] = round(
                100.0 * job.progress.blocks_done / job.progress.blocks_total, 1
            )
            elapsed = job.elapsed_seconds()
            if job.progress.blocks_done and job.status == "running" and elapsed > 1:
                rate = job.progress.blocks_done / elapsed
                remaining = job.progress.blocks_total - job.progress.blocks_done
                progress["eta_seconds"] = round(remaining / rate) if rate > 0 else None
        return {
            "job_id": job.id,
            "kind": job.kind,
            "status": job.status,
            "label": job.label,
            "table_name": job.table_name,
            "scratch_table": f"{self._store.database}.{job.table_name}",
            "elapsed_seconds": round(job.elapsed_seconds(), 1),
            "rows_written": job.progress.rows_written,
            "resumable": job.resumable,
            "error": job.error,
            "progress": progress,
            "spec": job.spec,
            "cursor": json.loads(job.cursor.to_json()),
            "_sort": job.submitted_at,
        }

    def _snapshot_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            spec = json.loads(row.get("spec_json") or "{}")
        except (TypeError, ValueError):
            spec = {}
        status = str(row["status"])
        return {
            "job_id": row["job_id"],
            "kind": row["kind"],
            "status": status,
            "label": row.get("label", ""),
            "table_name": row["table_name"],
            "scratch_table": f"{self._store.database}.{row['table_name']}",
            "elapsed_seconds": 0.0,
            "rows_written": int(row.get("rows_written", 0)),
            "resumable": status in RESUMABLE_STATUSES,
            "error": row.get("note") or None,
            "progress": {},
            "spec": spec,
            "cursor": json.loads(row.get("cursor_json") or "{}"),
            "_sort": 0,
        }


_engine: ScanEngine | None = None
_engine_lock = threading.Lock()


def default_scan_engine(ch: ClickHouseManager) -> ScanEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = ScanEngine(ch)
        return _engine


def reset_default_scan_engine() -> None:
    global _engine
    with _engine_lock:
        _engine = None
