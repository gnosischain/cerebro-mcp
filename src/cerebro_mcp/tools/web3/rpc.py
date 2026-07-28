"""Read-only Web3 RPC + contract decoding tools.

These tools are intentionally narrow: one contract, one call, one tx at a
time. For bulk on-chain work — log sweeps over block windows, multicall
view-function sweeps across address sets, storage/bytecode scans, native
value traces — use the rpc_scan_* family in tools/web3/rpc_scan.py
(registered when RPC_SCAN_ENABLED), which lands results in ClickHouse
scratch tables. Historical aggregates that dbt already models stay on the
dbt/execute_query path.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from web3 import Web3
from web3.logs import DISCARD

from cerebro_mcp.chains import CHAINS, GNOSIS_CHAIN_ID, configured_chains, resolve_chain
from cerebro_mcp.clients.abi_resolver import resolve_abi
from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.contract_history import read_function_history
from cerebro_mcp.runtime.tool_output import normalize_value, truncate_response
from cerebro_mcp.clients.web3 import rpc_manager


_BLOCK_TAGS = {"latest", "pending", "safe", "finalized", "earliest"}


def _coerce_block_identifier(value: int | str | None) -> int | str:
    """Accept ``"latest"``, named tags, ints, or numeric strings.

    UIs send everything as strings; web3.py wants ints for numeric block IDs.
    Hex strings (``0x…``) are passed through — web3.py handles them.
    """
    if value is None or value == "":
        return "latest"
    if isinstance(value, int):
        return value
    s = value.strip().lower()
    if s in _BLOCK_TAGS:
        return s
    if s.startswith("0x"):
        return s  # hex block hash or block number — web3.py accepts
    try:
        return int(s)
    except ValueError:
        return value  # let web3.py raise its own error


def call_view_function(
    ch: ClickHouseManager,
    address: str,
    *,
    function_name: str = "",
    function_signature: str = "",
    args: list[Any] | None = None,
    block_identifier: int | str = "latest",
    target: str = "auto",
    from_address: str = "",
    chain_id: int = GNOSIS_CHAIN_ID,
) -> dict[str, Any]:
    """Resolve ABI, call one view/pure function, return a structured result.

    Returns a dict with keys:
        ok                — bool
        address           — checksum-cased input address
        chain_id          — chain the call was routed to
        signature         — resolved canonical signature, e.g. ``"balanceOf(address)"``
        function          — bare function name
        mutability        — ``"view"`` / ``"pure"`` / other
        block             — the block identifier passed in
        args              — args after address-arg checksumming
        elapsed_seconds   — float
        result            — only when ``ok`` is True; normalized via ``normalize_value``
        error             — only when ``ok`` is False; human-readable message

    Validation failures (missing function name, non-view function, web3 errors)
    are surfaced via ``ok=False``; the function never raises for those. Resolver
    failures (ABI not found, RPC unreachable) propagate as exceptions so the
    caller can decide how to surface them.
    """
    block_identifier = _coerce_block_identifier(block_identifier)
    out: dict[str, Any] = {
        "ok": False,
        "address": "",
        "chain_id": int(chain_id),
        "signature": "",
        "function": function_name,
        "mutability": "",
        "block": block_identifier,
        "args": list(args or []),
        "elapsed_seconds": 0.0,
    }
    try:
        checksum = Web3.to_checksum_address(address)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Bad address: {exc}"
        return out
    out["address"] = checksum

    try:
        record = resolve_abi(ch, checksum, target=target, chain_id=chain_id)
    except Exception as exc:  # noqa: BLE001
        # Re-raise — caller decides how to display "ABI not found".
        raise

    w3 = rpc_manager.for_block(block_identifier, chain_id)
    contract = w3.eth.contract(address=checksum, abi=record.abi)

    try:
        if function_signature:
            fn_factory = contract.get_function_by_signature(function_signature)
        elif function_name:
            fn_factory = contract.get_function_by_name(function_name)
        else:
            out["error"] = "provide function_name or function_signature."
            return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Function not found: {exc}"
        return out

    fn_abi = fn_factory.abi
    out["function"] = fn_abi.get("name", function_name)
    out["mutability"] = fn_abi.get("stateMutability", "")
    # Build a canonical signature even when the user passed only a name.
    out["signature"] = (
        f"{fn_abi.get('name', '')}("
        f"{','.join(i.get('type', '') for i in fn_abi.get('inputs', []))})"
    )

    if out["mutability"] not in {"view", "pure"}:
        out["error"] = "only view/pure functions are allowed."
        return out

    tx: dict[str, Any] = {}
    if from_address:
        try:
            tx["from"] = Web3.to_checksum_address(from_address)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"Bad from_address: {exc}"
            return out

    try:
        normalized_args = _checksum_args(fn_abi, list(args or []))
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Bad argument: {exc}"
        return out
    out["args"] = normalized_args

    started = time.monotonic()
    try:
        result = rpc_manager.retry(
            fn_factory(*normalized_args).call,
            tx,
            block_identifier=block_identifier,
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{exc}"
        out["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return out

    out["ok"] = True
    out["result"] = normalize_value(result)
    out["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return out


def _checksum_args(fn_abi: dict[str, Any], args: list[Any]) -> list[Any]:
    """Re-checksum any ``address`` (or ``address[]``) inputs in ``args``.

    web3.py rejects lowercase or wrong-case addresses; users typing or pasting
    them shouldn't have to compute EIP-55 by hand. Walks the ABI inputs and
    normalizes anything that's typed as an address.
    """
    inputs = fn_abi.get("inputs") or []
    if len(args) != len(inputs):
        return args  # let web3 produce the real error

    out: list[Any] = []
    for value, spec in zip(args, inputs):
        t = spec.get("type", "")
        if t == "address" and isinstance(value, str):
            out.append(Web3.to_checksum_address(value))
        elif t == "address[]" and isinstance(value, (list, tuple)):
            out.append([
                Web3.to_checksum_address(v) if isinstance(v, str) else v
                for v in value
            ])
        else:
            out.append(value)
    return out


def _resolve_configured_chain(chain: str):
    """Resolve ``chain`` and fail early if it has no endpoint.

    Without this the ABI tiers are tried first and the user gets "ABI not
    found" — which blames the contract for what is actually a missing
    ``RPC_URL_*`` setting.
    """
    from cerebro_mcp.chains import has_rpc, rpc_env_hint

    chain_info = resolve_chain(chain)
    if not has_rpc(chain_info.chain_id):
        raise ValueError(
            f"{chain_info.name} (chain {chain_info.chain_id}) has no RPC "
            f"endpoint configured. Set {rpc_env_hint(chain_info.chain_id)}. "
            f"Call list_chains to see what is available."
        )
    return chain_info


def register_rpc_tools(mcp, ch: ClickHouseManager) -> None:
    """Register the RPC / contract tools with the MCP server."""

    @mcp.tool()
    def list_chains() -> str:
        """List the EVM chains this server can reach, and which are configured.

        Call before passing ``chain`` to any ``contract_*`` tool when you are
        unsure whether a chain is available — a chain with no ``RPC_URL_*`` set
        cannot be queried, and this says so explicitly rather than failing at
        call time.
        """
        available = {c.chain_id for c in configured_chains()}
        lines = ["| Chain | id | key | RPC | Explorer |", "|---|---|---|---|---|"]
        for chain in CHAINS.values():
            status = "configured" if chain.chain_id in available else "not set"
            lines.append(
                f"| {chain.name} | {chain.chain_id} | {chain.rpc_env_key.lower()} "
                f"| {status} | {chain.explorer.brand} |"
            )
        lines.append("")
        lines.append(
            f"{len(available)} of {len(CHAINS)} chains configured. "
            "Set RPC_URL_<KEY> to enable the rest."
        )
        return "\n".join(lines)

    @mcp.tool()
    def contract_explore(
        address: str,
        include_abi: bool = False,
        chain: str = "",
    ) -> str:
        """Quickly inspect one contract by address — what functions/events it exposes, the proxy implementation, and where the ABI was resolved from.

        Use when:
        - The user asks "what does contract X do?"
        - You're about to call ``contract_call_function`` and don't yet know the
          exact function name or signature.
        - You suspect a proxy and need the implementation address before calling.

        ``chain`` accepts a name, key, or id ("mainnet", "base", 42161) and
        defaults to Gnosis. Only chains with an ``RPC_URL_*`` configured can be
        used — ``list_chains`` shows which.

        For bulk decoded data across many contracts, prefer the dbt models
        surfaced via ``execute_query`` instead.
        """
        try:
            chain_info = _resolve_configured_chain(chain)
            checksum = Web3.to_checksum_address(address)
            record = resolve_abi(ch, checksum, chain_id=chain_info.chain_id)

            functions = [x for x in record.abi if x.get("type") == "function"]
            events = [x for x in record.abi if x.get("type") == "event"]

            lines = [
                f"# Contract `{checksum}`",
                f"- Chain: {chain_info.name} ({chain_info.chain_id})",
                f"- Name: {record.contract_name or 'unknown'}",
                f"- ABI source: {record.source}",
                f"- Implementation: `{record.implementation_address or 'none'}`",
                f"- Functions: {len(functions)}",
                f"- Events: {len(events)}",
                f"- Explorer: {chain_info.explorer.address_url_template.format(address=checksum)}",
                "",
                "## Functions (first 40)",
            ]
            for fn in functions[:40]:
                mut = fn.get("stateMutability", "")
                args = ", ".join(i.get("type", "") for i in fn.get("inputs", []))
                lines.append(f"- `{fn.get('name')}({args})` [{mut}]")

            if events:
                lines.append("")
                lines.append("## Events (first 20)")
                for ev in events[:20]:
                    args = ", ".join(
                        i.get("type", "") for i in ev.get("inputs", [])
                    )
                    lines.append(f"- `{ev.get('name')}({args})`")

            if include_abi:
                lines.append("")
                lines.append("## ABI")
                lines.append("```json")
                lines.append(json.dumps(normalize_value(record.abi), indent=2))
                lines.append("```")

            return truncate_response("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

    @mcp.tool()
    def contract_call_function(
        address: str,
        function_name: str = "",
        args: list[Any] | None = None,
        block_identifier: int | str = "latest",
        function_signature: str = "",
        target: str = "auto",
        from_address: str = "",
        chain: str = "",
    ) -> str:
        """Get current on-chain state at one address with one RPC round-trip.

        Examples:
        - Current ERC20 balance: ``function_name="balanceOf"``, ``args=[holder]``
        - Current ``totalSupply()`` / ``decimals()`` / ``symbol()``
        - Current ``owner()``, ``paused()``, ``allowance(owner, spender)``
        - Any view/pure read where you have the contract address and function name.

        Prefer this over ``fct_*_balances`` SQL or the Portfolio mini-app for
        single-address *current* reads — one round-trip, no dbt latency, fresh
        on-chain state. Use the dbt path instead when you need: multi-address
        sweeps, historical values, USD-valued holdings, aggregations across
        addresses, or dashboards.

        State-changing functions are rejected at the tool layer. Pass either
        ``function_name`` or an exact ``function_signature`` (e.g.
        ``"transfer(address,uint256)"`` — useful when overloads exist).

        ``chain`` accepts a name, key, or id ("mainnet", "base", 42161) and
        defaults to Gnosis; the chain must have an ``RPC_URL_*`` configured.

        ``block_identifier`` defaults to ``"latest"``. Any non-latest tag
        (numeric block, ``"earliest"``, etc.) reads historical state and needs
        an archive endpoint for that chain. For the SAME function across MANY
        blocks, use ``contract_read_history`` instead of looping this tool.
        """
        try:
            chain_info = _resolve_configured_chain(chain)
            outcome = call_view_function(
                ch,
                address,
                function_name=function_name,
                function_signature=function_signature,
                args=args,
                block_identifier=block_identifier,
                target=target,
                from_address=from_address,
                chain_id=chain_info.chain_id,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

        if not outcome["ok"]:
            return f"Error: {outcome.get('error', 'unknown error')}"

        return truncate_response(
            f"Function: `{outcome['function']}` ({outcome['mutability']})\n"
            f"Chain: {chain_info.name} ({chain_info.chain_id})\n"
            f"Signature: `{outcome['signature']}`\n"
            f"Block: `{outcome['block']}`\n"
            f"Elapsed: {outcome['elapsed_seconds']}s\n\n"
            f"```json\n{json.dumps(outcome['result'], indent=2, default=str)}\n```"
        )

    @mcp.tool()
    def contract_read_history(
        address: str,
        function_name: str = "",
        args: list[Any] | None = None,
        since: str = "",
        until: str = "",
        from_block: int | str = "",
        to_block: int | str = "",
        points: int = 0,
        chain: str = "",
        function_signature: str = "",
        target: str = "auto",
        output_index: int = 0,
        decimals: int = 0,
    ) -> str:
        """Read one view function across a BLOCK RANGE to see how its value evolved.

        Use when the user asks how an on-chain value changed over time:
        "totalSupply over the last 30 days", "how did this vault's balance
        evolve", "when did `paused()` flip", "owner() history". This is the
        historical counterpart to ``contract_call_function`` — never loop that
        tool over blocks, use this instead.

        Range: ``since``/``until`` accept a relative window ("30d", "12h"), an
        ISO date, or a unix timestamp; ``from_block``/``to_block`` pin exact
        blocks. Defaults to the last 30 days. ``points`` is how many samples to
        take across the range (default 60).

        Reads historical state, so the chain needs an archive endpoint. The
        range is clamped to the contract's deployment block, and samples that
        fail individually (revert, pruned state) are reported per point rather
        than failing the sweep.

        Prefer a dbt model via ``execute_query`` when one already tracks this
        quantity — this walks the chain live and is slower.
        """
        try:
            chain_info = _resolve_configured_chain(chain)
            outcome = read_function_history(
                ch,
                address,
                chain_id=chain_info.chain_id,
                function_name=function_name,
                function_signature=function_signature,
                args=args,
                from_block=from_block or None,
                to_block=to_block or None,
                since=since,
                until=until,
                points=points,
                target=target,
                output_index=output_index,
                decimals=decimals or None,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

        series = outcome["points"]
        lines = [
            f"# `{outcome['signature']}` on {outcome['contract_name'] or outcome['address']}",
            f"- Chain: {outcome['chain_name']} ({outcome['chain_id']})",
            f"- Blocks: {outcome['from_block']:,} to {outcome['to_block']:,} "
            f"({len(series)} samples, {outcome['ok_count']} ok)",
            f"- Elapsed: {outcome['elapsed_seconds']}s",
        ]
        for warning in outcome["warnings"]:
            lines.append(f"- WARNING: {warning}")

        lines += ["", "| block | timestamp (UTC) | value | status |", "|---|---|---|---|"]
        for point in series:
            ts = point["timestamp"]
            when = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                if ts else "—"
            )
            if point["status"] != "ok":
                shown = point["error"]
            elif decimals and point["value_float"] is not None:
                # Raw uint256 is unreadable; show the scaled figure when the
                # caller told us the decimals.
                shown = f"{point['value_float']:,.6f}".rstrip("0").rstrip(".")
            else:
                shown = point["value"]
            lines.append(
                f"| {point['block']:,} | {when} | {str(shown)[:60]} | {point['status']} |"
            )
        return truncate_response("\n".join(lines))

    @mcp.tool()
    def contract_decode_transaction_input(
        address: str = "",
        tx_hash: str = "",
        input_data: str = "",
        target: str = "auto",
        chain: str = "",
    ) -> str:
        """Decode a single transaction's calldata back into function name + arguments using the resolved contract ABI.

        Use when the user asks "what was tx 0x… doing?" or "decode this
        calldata". Provide either ``tx_hash`` (the tool fetches the tx) or both
        ``address`` and ``input_data``. ``chain`` defaults to Gnosis.
        """
        try:
            chain_info = _resolve_configured_chain(chain)
            w3 = rpc_manager.standard(chain_info.chain_id)
            if tx_hash:
                tx = rpc_manager.retry(w3.eth.get_transaction, tx_hash)
                input_data = input_data or tx.get("input") or tx.get("data") or ""
                address = address or tx.get("to") or ""

            if not address or not input_data:
                return "Error: need address and input_data (or tx_hash)."

            checksum = Web3.to_checksum_address(address)
            record = resolve_abi(
                ch, checksum, target=target, chain_id=chain_info.chain_id
            )
            contract = w3.eth.contract(address=checksum, abi=record.abi)

            fn, params = contract.decode_function_input(input_data)
            return truncate_response(
                f"Function: `{fn.fn_name}`\n"
                f"Chain: {chain_info.name} ({chain_info.chain_id})\n"
                f"Contract: `{checksum}` ({record.contract_name or 'unknown'})\n\n"
                f"```json\n{json.dumps(normalize_value(params), indent=2, default=str)}\n```"
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

    @mcp.tool()
    def contract_decode_receipt_logs(
        tx_hash: str,
        address: str = "",
        max_logs: int = 50,
        max_contracts: int = 10,
        chain: str = "",
    ) -> str:
        """Decode the event logs in a transaction receipt back into event name + args, with ABIs resolved per emitting contract.

        Use when the user asks "what events did tx 0x… emit?" or wants the
        human-readable event trail for a tx. If ``address`` is given, only logs
        from that contract are decoded. Otherwise the tool resolves an ABI per
        distinct emitting address (capped by ``max_contracts``).
        ``chain`` defaults to Gnosis.
        """
        try:
            chain_info = _resolve_configured_chain(chain)
            w3 = rpc_manager.standard(chain_info.chain_id)
            receipt = rpc_manager.retry(w3.eth.get_transaction_receipt, tx_hash)
            logs = (receipt.get("logs") or [])[:max_logs]
            if address:
                target = Web3.to_checksum_address(address)
                logs = [
                    log for log in logs
                    if Web3.to_checksum_address(log["address"]) == target
                ]

            decoded: list[Any] = []
            seen: set[str] = set()
            for log in logs:
                log_address = Web3.to_checksum_address(log["address"])
                if log_address not in seen:
                    if len(seen) >= max_contracts:
                        break
                    seen.add(log_address)

                try:
                    record = resolve_abi(ch, log_address, chain_id=chain_info.chain_id)
                except Exception:
                    continue
                contract = w3.eth.contract(address=log_address, abi=record.abi)
                for event_abi in [
                    x for x in record.abi if x.get("type") == "event"
                ]:
                    name = event_abi.get("name")
                    if not name:
                        continue
                    try:
                        event = getattr(contract.events, name)()
                        for entry in event.process_receipt(receipt, errors=DISCARD):
                            decoded.append({
                                "address": log_address,
                                "event": name,
                                "args": dict(entry.get("args", {})),
                                "log_index": entry.get("logIndex"),
                            })
                    except Exception:
                        continue

            return truncate_response(
                f"Decoded {len(decoded)} log(s) from {len(seen)} contract(s).\n\n"
                f"```json\n{json.dumps(normalize_value(decoded), indent=2, default=str)}\n```"
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
