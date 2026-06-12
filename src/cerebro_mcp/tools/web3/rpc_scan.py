"""Bulk RPC scan tools: logs / calls / storage / code / traces into ClickHouse.

Counterpart to tools/web3/rpc.py (single-call inspection). Every scan tool
starts an engine job, waits up to ``sync_wait_seconds`` (capped), and
returns either the completed counts-first summary or a running snapshot —
identical shape either way. Full rows land in a ``scratch.rpc_*`` table the
model then analyzes with ``execute_query`` (joins against dbt models work).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.engine import ScanEngine, default_scan_engine
from cerebro_mcp.runtime.tool_output import truncate_response


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    return text if len(text) <= 70 else text[:67] + "..."


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    return "\n".join(lines)


def _example_sql(kind: str, scratch_table: str, spec: dict[str, Any]) -> list[str]:
    if kind == "logs":
        arg = spec.get("filter_arg") or ""
        group_col = f"arg_{arg}" if (arg and spec.get("event")) else "address"
        examples = [
            f"-- rows per {group_col}\n"
            f"SELECT `{group_col}`, uniqExact((block_number, log_index)) AS rows\n"
            f"FROM {scratch_table}\nGROUP BY `{group_col}` ORDER BY rows DESC",
        ]
        if spec.get("event"):
            examples.append(
                f"-- join token metadata from dbt\n"
                f"SELECT l.address AS token, count() AS transfers\n"
                f"FROM {scratch_table} l FINAL\n"
                f"GROUP BY token ORDER BY transfers DESC"
            )
        return examples
    if kind == "storage":
        return [
            f"-- classify slot values\n"
            f"SELECT value_address, uniqExact((address, slot)) AS addresses\n"
            f"FROM {scratch_table}\nGROUP BY value_address ORDER BY addresses DESC",
        ]
    if kind == "code":
        return [
            f"-- cluster identical deployments\n"
            f"SELECT code_hash, is_eip1167, any(eip1167_impl) AS impl, "
            f"uniqExact(address) AS addresses\n"
            f"FROM {scratch_table}\nGROUP BY code_hash, is_eip1167 "
            f"ORDER BY addresses DESC",
        ]
    if kind == "calls":
        return [
            f"SELECT * FROM {scratch_table} FINAL LIMIT 20",
        ]
    if kind == "traces":
        return [
            f"-- native value per recipient\n"
            f"SELECT to_address, sum(value_wei) AS wei, uniqExact(tx_hash) AS txs\n"
            f"FROM {scratch_table} FINAL\nGROUP BY to_address ORDER BY wei DESC",
        ]
    return [f"SELECT * FROM {scratch_table} FINAL LIMIT 20"]


def _render_running(status: dict[str, Any], warnings: list[str]) -> str:
    progress = status.get("progress") or {}
    lines = [
        f"# rpc_scan ({status['kind']}) — {status['status']}",
        f"- **Job**: `{status['job_id']}`"
        + (f' ("{status["label"]}")' if status.get("label") else "")
        + f" | **Elapsed**: {status['elapsed_seconds']}s",
        f"- **Partial rows already queryable**: `{status['scratch_table']}`",
    ]
    bits = []
    if progress.get("blocks_total"):
        bits.append(
            f"{_fmt(progress.get('blocks_done', 0))} / "
            f"{_fmt(progress['blocks_total'])} blocks "
            f"({progress.get('pct_blocks', 0)}%)"
        )
    if progress.get("addresses_total"):
        bits.append(
            f"{_fmt(progress.get('addresses_done', 0))} / "
            f"{_fmt(progress['addresses_total'])} addresses"
        )
    bits.append(f"{_fmt(status.get('rows_written', 0))} rows written")
    if progress.get("eta_seconds"):
        bits.append(f"~{progress['eta_seconds']}s remaining")
    lines.append(f"- **Progress**: {' | '.join(bits)}")
    if status.get("error"):
        lines.append(f"- **Last error**: {status['error']}")
    for w in warnings:
        lines.append(f"- **Note**: {w}")
    lines.append("")
    lines.append(
        f"Poll with `rpc_scan_status(\"{status['job_id']}\")`. You may run SQL "
        f"against the partial table now via `execute_query`."
    )
    return "\n".join(lines)


def _render_terminal(engine: ScanEngine, status: dict[str, Any],
                     warnings: list[str]) -> str:
    job_id = status["job_id"]
    scratch_table = status["scratch_table"]
    spec = status.get("spec") or {}
    expires = (_dt.datetime.utcnow()
               + _dt.timedelta(days=settings.RPC_SCAN_SCRATCH_TTL_DAYS)).date()

    lines = [
        f"# rpc_scan ({status['kind']}) — {status['status']}",
        f"- **Job**: `{job_id}`"
        + (f' ("{status["label"]}")' if status.get("label") else "")
        + f" | **Elapsed**: {status['elapsed_seconds']}s",
        f"- **Scratch table**: `{scratch_table}` (expires ~{expires})",
    ]
    if spec.get("from_block") is not None and spec.get("to_block") is not None:
        lines.append(
            f"- **Window**: blocks {_fmt(spec['from_block'])} -> {_fmt(spec['to_block'])}"
        )
    if status.get("error"):
        lines.append(f"- **Error**: {status['error']}")
    if status.get("resumable"):
        lines.append(
            f"- **Resumable**: yes — `rpc_scan_resume(\"{job_id}\")` continues "
            f"from the saved cursor into the same table."
        )

    try:
        summary = engine.summarize(job_id)
    except Exception as exc:  # noqa: BLE001
        lines.append(f"- **Summary unavailable**: {exc}")
        return "\n".join(lines)

    stat_bits = [f"**Rows**: {_fmt(summary.get('row_count', 0))} (uniqExact)"]
    for key in ("min_block", "max_block", "distinct_tx", "distinct_emitters",
                "distinct_addresses", "contracts", "eip1167_clones",
                "total_value_wei"):
        if key in summary:
            stat_bits.append(f"{key}: {_fmt(summary[key])}")
    lines.append("- " + " | ".join(stat_bits))

    for w in warnings:
        lines.append(f"- **Note**: {w}")

    top = summary.get("top_values") or []
    if top:
        lines.append("")
        lines.append(f"## Top values — `{summary.get('top_column')}` (by rows)")
        lines.append(_markdown_table([summary.get("top_column", "value"), "rows"], top))

    sample = summary.get("sample") or []
    if sample:
        cols = summary.get("sample_columns") or [
            f"col_{i}" for i in range(len(sample[0]))
        ]
        lines.append("")
        lines.append(f"## Sample rows ({len(sample)} of {_fmt(summary.get('row_count', 0))})")
        lines.append(_markdown_table(cols, sample))

    lines.append("")
    lines.append("## Next queries (run via execute_query)")
    for sql in _example_sql(status["kind"], scratch_table, spec):
        lines.append("```sql")
        lines.append(sql)
        lines.append("```")
    return "\n".join(lines)


def render_status(engine: ScanEngine, status: dict[str, Any],
                  warnings: list[str] | None = None) -> str:
    warnings = warnings or []
    if status["status"] in ("pending", "running"):
        return truncate_response(_render_running(status, warnings))
    return truncate_response(_render_terminal(engine, status, warnings))


def _render_call_tree(node: dict[str, Any], lines: list[str], *, indent: int) -> None:
    pad = "  " * indent
    value = f" value={node['value_wei']:,}" if node.get("value_wei") else ""
    selector = f" {node['selector']}" if node.get("selector") else ""
    status = f" REVERTED({node['error']})" if node.get("error") else ""
    lines.append(
        f"{pad}- {node['call_type']} `{node['from']}` -> `{node['to']}`"
        f"{selector}{value} gas={node['gas_used']:,}{status}"
    )
    for child in node.get("children") or []:
        _render_call_tree(child, lines, indent=indent + 1)
    if node.get("truncated"):
        lines.append(
            f"{pad}  - ... {node.get('hidden_children', '?')} deeper frame(s) "
            f"truncated (raise max_depth or use store=True)"
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_rpc_scan_tools(mcp, ch: ClickHouseManager,
                            engine: ScanEngine | None = None) -> None:
    """Register the bulk RPC scan tool family."""
    engine = engine or default_scan_engine(ch)

    @mcp.tool()
    def rpc_scan_logs(
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
        sync_wait_seconds: int = 10,
    ) -> str:
        """Sweep eth_getLogs over ANY block window into a ClickHouse scratch table.

        The engine auto-chunks the range (halving on provider "range too
        large" errors, growing back after success) — NEVER pre-chunk
        manually; any window size is fine. Partial rows are queryable
        mid-scan; finished scans return a counts-first summary plus the
        scratch table name. Continue analysis with `execute_query` — joins
        against dbt models work.

        Decoding — pick at most ONE:
        - `event`: an event signature. Full form with indexed markers decodes
          into typed `arg_*` columns, e.g.
          `"Transfer(address indexed from, address indexed to, uint256 value)"`.
          Well-known events (Transfer, Approval, TransferSingle, ...) also
          accept the short form `"Transfer(address,address,uint256)"` — the
          indexed layout is filled in per log line (ERC-20 vs ERC-721 is
          resolved by topic count).
        - `decode_abi_address`: resolve that contract's full ABI and decode
          every event it defines into `event_name` + `args_json` (no typed
          columns).
        - `topics`: raw JSON-RPC topics override (advanced).
        - none of them: raw scan, topics/data stored undecoded.

        Filtering by an address set: name a decoded argument (`filter_arg="to"`)
        and pass the set inline (`filter_addresses`, <=500) or as SQL
        (`filter_address_sql="SELECT safe_address FROM dbt.<model>"`, any
        size). Indexed args filter server-side via chunked topic groups at any
        set size; non-indexed args filter engine-side and require a window
        <= RPC_SCAN_UNINDEXED_FILTER_MAX_BLOCKS.

        Use this instead of raw `execution.logs` SQL when the window is
        recent/not yet in dbt, the event isn't decoded by any dbt model, or
        you need pipeline-independent verification. For long-history
        aggregates that dbt already models, use `execute_query` directly.
        """
        try:
            job, warnings = engine.start_log_scan(
                from_block=from_block,
                to_block=to_block,
                contracts=contracts,
                event=event,
                decode_abi_address=decode_abi_address,
                topics=topics,
                filter_arg=filter_arg,
                filter_addresses=filter_addresses,
                filter_address_sql=filter_address_sql,
                label=label,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        status = engine.wait_or_status(job, sync_wait_seconds)
        return render_status(engine, status, warnings)

    @mcp.tool()
    def rpc_batch_call(
        calls: list[dict[str, Any]],
        addresses: list[str] | None = None,
        address_sql: str = "",
        block: int | str = "latest",
        label: str = "",
        sync_wait_seconds: int = 10,
    ) -> str:
        """Batch view-function reads across thousands of addresses via
        Multicall3 aggregate3 (~600 reads per RPC round-trip; one reverting
        target never aborts a batch). A pinned `block` executes against
        historical state and requires GNOSIS_ARCHIVE_RPC_URL.

        Each entry in `calls` (max 5):
          {"function": "balanceOf(address)(uint256)",  # cast-style, return types included
           "args": ["{address}"],                      # "{address}" = the swept address
           "to": "0xTOKEN",                            # fixed target; empty = call each swept address
           "alias": "usdc"}                            # column prefix, defaults to function name

        Two shapes:
        - Swept addresses ARE the targets (`to` empty): {"function": "getOwners()(address[])"}
          calls every swept address — e.g. Safe owners/threshold/modules sweeps.
        - Fixed target, address as arg: balanceOf across N holders for one
          token; repeat per token (up to 5) for a multi-token sweep in ONE job.

        The scratch table is WIDE — one row per address with
        `<alias>_success`, `<alias>_out_N` (typed from the declared return
        types), `<alias>_error` columns, so owners+threshold+modules land in
        one joinable row. Only view/pure semantics: execution is via eth_call,
        nothing can change state. For ONE address use contract_call_function.
        """
        try:
            job, warnings = engine.start_call_scan(
                calls=calls, addresses=addresses, address_sql=address_sql,
                block=block, label=label,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        status = engine.wait_or_status(job, sync_wait_seconds)
        return render_status(engine, status, warnings)

    @mcp.tool()
    def rpc_read_storage(
        slots: list[int | str],
        addresses: list[str] | None = None,
        address_sql: str = "",
        block: int | str = "latest",
        label: str = "",
        sync_wait_seconds: int = 10,
    ) -> str:
        """Read raw storage slots (eth_getStorageAt) across an address set at
        one pinned block — parallelized and checkpointed by the engine.

        Each value is stored three ways for zero-friction SQL: raw 32-byte
        hex (`value`), `value_uint`, and `value_address` (last 20 bytes when
        the top 12 are zero) — so e.g. slot-0 singleton/implementation
        classification is a one-line GROUP BY on value_address.

        `slots` (max 8 per job) accepts ints (0) or hex strings. For a
        mapping entry, compute the slot as
        keccak256(pad32(key) ++ pad32(mapping_slot)) and pass the hex.
        Non-latest `block` requires GNOSIS_ARCHIVE_RPC_URL.
        """
        try:
            job, warnings = engine.start_storage_scan(
                slots=slots, addresses=addresses, address_sql=address_sql,
                block=block, label=label,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        status = engine.wait_or_status(job, sync_wait_seconds)
        return render_status(engine, status, warnings)

    @mcp.tool()
    def rpc_get_code(
        addresses: list[str] | None = None,
        address_sql: str = "",
        block: int | str = "latest",
        detect_proxies: bool = True,
        store_bytecode: bool = False,
        label: str = "",
        sync_wait_seconds: int = 10,
    ) -> str:
        """Classify every address in a set by its bytecode (eth_getCode):
        EOA vs contract, code size, keccak code_hash (clusters identical
        deployments), 16-byte prefix, EIP-1167 minimal-proxy detection with
        the implementation address extracted, and (detect_proxies) the
        EIP-1967 implementation/admin/beacon storage slots.

        The summary's top-values table groups by code_hash — it instantly
        shows "N of M addresses are clones of implementation X". Chain scans
        together: rpc_get_code(address_sql="SELECT DISTINCT arrayJoin(
        modules_out_0) FROM scratch.rpc_calls_<id>") classifies every module
        discovered by a previous rpc_batch_call sweep.
        """
        try:
            job, warnings = engine.start_code_scan(
                addresses=addresses, address_sql=address_sql, block=block,
                detect_proxies=detect_proxies, store_bytecode=store_bytecode,
                label=label,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        status = engine.wait_or_status(job, sync_wait_seconds)
        return render_status(engine, status, warnings)

    @mcp.tool()
    def rpc_scan_traces(
        from_block: int | str,
        to_block: int | str,
        from_addresses: list[str] | None = None,
        from_address_sql: str = "",
        to_addresses: list[str] | None = None,
        to_address_sql: str = "",
        min_value_wei: str = "1",
        include_failed: bool = False,
        label: str = "",
        sync_wait_seconds: int = 10,
    ) -> str:
        """Sweep trace_filter for NATIVE xDAI value flows (and internal calls)
        — the blind spot of every Transfer-log method, since native transfers
        emit no log. Use it to reconcile residuals that ERC-20 logs cannot
        explain.

        The node caps trace_filter at ~100 blocks per call; the engine chunks
        and parallelizes — pass any window up to
        RPC_SCAN_TRACE_MAX_RANGE_BLOCKS. from/to filters AND across
        parameters, OR within a list (node semantics). When one side is a
        huge set, the engine pushes the small side to the node and filters
        the large side itself. `min_value_wei` is a string (defaults "1":
        value-bearing calls only). Requires a trace-capable archive node
        (GNOSIS_ARCHIVE_RPC_URL).
        """
        try:
            job, warnings = engine.start_trace_scan(
                from_block=from_block, to_block=to_block,
                from_addresses=from_addresses, from_address_sql=from_address_sql,
                to_addresses=to_addresses, to_address_sql=to_address_sql,
                min_value_wei=min_value_wei, include_failed=include_failed,
                label=label,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        status = engine.wait_or_status(job, sync_wait_seconds)
        return render_status(engine, status, warnings)

    @mcp.tool()
    def rpc_trace_transaction(
        tx_hash: str,
        max_depth: int = 8,
        store: bool = False,
    ) -> str:
        """Render one transaction's full execution as an indented call tree
        (debug_traceTransaction callTracer): CALL/DELEGATECALL/CREATE frames
        with from -> to, value, gas, selector, and REVERTED markers, plus the
        net native-value movement per address. Use for "what did tx X
        actually do" — together with contract_decode_receipt_logs this covers
        a transaction completely.

        `store=True` also persists every frame to a scratch.rpc_traces_*
        table (for trees too large to read inline or for SQL joins).
        """
        try:
            result = engine.trace_transaction(
                tx_hash, max_depth=max_depth, store_frames=store,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        lines = [f"# Call tree for `{result['tx_hash']}`", ""]
        _render_call_tree(result["tree"], lines, indent=0)
        flows = result.get("net_flows") or {}
        if flows:
            lines.append("")
            lines.append("## Net native value movement (wei)")
            ordered = sorted(flows.items(), key=lambda kv: kv[1])
            lines.append(_markdown_table(
                ["address", "net_wei"], [[a, v] for a, v in ordered]
            ))
        if result.get("scratch_table"):
            lines.append("")
            lines.append(
                f"Frames stored in `{result['scratch_table']}` — query via "
                f"execute_query."
            )
        return truncate_response("\n".join(lines))

    @mcp.tool()
    def rpc_find_block(
        kind: str,
        timestamp: str = "",
        addresses: list[str] | None = None,
        address_sql: str = "",
        slot: int | str = 0,
        from_block: int = 1,
        to_block: int | str = "latest",
        label: str = "",
        sync_wait_seconds: int = 10,
    ) -> str:
        """Binary-search block finders (O(log N) RPC reads per target).

        kind="timestamp" — first block at/after a UTC time (`timestamp` is
        ISO-8601 or unix seconds). THE anchor-pinning primitive: resolve your
        incident window to blocks before any scan.

        kind="deployment" — first block where each address has code
        (addresses/address_sql). kind="storage_change" — first block where
        `slot` differs from its value at `from_block` (e.g. when was a Safe's
        slot-0 singleton overwritten). Both assume one transition in range.

        Up to 20 addresses answer inline; larger sets become a scan job
        writing a scratch.rpc_blocks_* table.
        """
        try:
            kind = kind.strip().lower()
            if kind == "timestamp":
                if not timestamp:
                    return 'Error: kind="timestamp" needs the timestamp argument.'
                found = engine.find_block_at_timestamp(timestamp)
                return (
                    f"First block at/after timestamp {timestamp}: "
                    f"**{found['block']:,}** (unix {found['timestamp']})."
                )
            if kind not in ("deployment", "storage_change"):
                return (
                    'Error: kind must be "timestamp", "deployment", or '
                    '"storage_change".'
                )
            resolved = engine.resolve_addresses(addresses, address_sql)
            floor = engine.resolve_block(from_block)
            ceiling = engine.resolve_block(to_block)
            slot_hex = hex(slot) if isinstance(slot, int) else hex(int(str(slot), 0))
            if len(resolved) <= 20:
                results = engine.find_blocks_sync(
                    kind=kind, addresses=resolved, slot=slot_hex,
                    floor=floor, ceiling=ceiling,
                )
                rows = [
                    [r["address"], r["found_block"] or "-",
                     r["value_before"], r["value_after"], r["error"]]
                    for r in results
                ]
                return truncate_response(
                    f"# rpc_find_block ({kind})\n\n"
                    + _markdown_table(
                        ["address", "found_block", "value_before",
                         "value_after", "error"], rows,
                    )
                )
            job = engine.start_blocks_scan(
                kind=kind, addresses=resolved, slot=slot_hex,
                floor=floor, ceiling=ceiling, label=label,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        status = engine.wait_or_status(job, sync_wait_seconds)
        return render_status(engine, status)

    @mcp.tool()
    def rpc_scan_status(job_id: str) -> str:
        """Status of an RPC scan job.

        Running jobs report progress (blocks/addresses done, rows written so
        far, ETA) and the partial scratch table — already queryable via
        `execute_query`. Terminal jobs return the full counts-first summary.
        """
        try:
            status = engine.job_status(job_id)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return render_status(engine, status)

    @mcp.tool()
    def rpc_scan_cancel(job_id: str) -> str:
        """Stop a running RPC scan. Partial rows are KEPT in the scratch table
        and the cursor is recorded, so `rpc_scan_resume` can continue later."""
        try:
            if engine.cancel_job(job_id):
                return (
                    f"Cancellation requested for job `{job_id}`. Partial rows "
                    f"stay queryable; check `rpc_scan_status(\"{job_id}\")` — "
                    f"once it reports cancelled, `rpc_scan_resume(\"{job_id}\")` "
                    f"continues from the saved cursor."
                )
            status = engine.job_status(job_id)
            return (
                f"Job `{job_id}` is already {status['status']}; nothing to cancel."
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

    @mcp.tool()
    def rpc_scan_resume(job_id: str, sync_wait_seconds: int = 10) -> str:
        """Resume a partial/cancelled/restart-orphaned scan from its persisted
        cursor into the SAME scratch table. Duplicate-safe: the table is a
        ReplacingMergeTree, so the one-unit overlap dedups on merge (count
        with uniqExact or FINAL)."""
        try:
            job = engine.resume_job(job_id)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        status = engine.wait_or_status(job, sync_wait_seconds)
        return render_status(engine, status)

    @mcp.tool()
    def rpc_list_scans(limit: int = 20) -> str:
        """List RPC scan jobs — in-memory ones plus the persisted registry
        (survives server restarts). Scratch tables expire after
        RPC_SCAN_SCRATCH_TTL_DAYS (default 7)."""
        try:
            jobs = engine.list_jobs(limit=limit)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        if not jobs:
            return "No RPC scan jobs found."
        rows = [
            [
                j["job_id"], j["kind"], j["label"] or "-", j["status"],
                j.get("rows_written", 0), j["scratch_table"],
                "yes" if j.get("resumable") else "",
            ]
            for j in jobs
        ]
        return truncate_response(
            _markdown_table(
                ["job", "kind", "label", "status", "rows", "scratch table", "resumable"],
                rows,
            )
        )
