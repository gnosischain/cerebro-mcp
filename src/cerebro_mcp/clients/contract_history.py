"""Read one view function across a range of blocks — pure RPC, inline results.

This is the "how did this value evolve" primitive behind
``contract_read_history`` and the Contract Explorer's history chart. It is
deliberately NOT part of the ``rpc_scan_*`` family: nothing is written to
ClickHouse, results come back in the tool response, and it needs no opt-in.

Shape of the work: resolve the block range (two bisections at most), sample it
evenly, then fan out one ``eth_call`` + one header read per sample over a
bounded thread pool. Sampling evenly by block and reading each sample's real
timestamp is far cheaper than bisecting per time interval (~2 reads per point
instead of ~2·log₂(range)) and the returned timestamps stay honest when block
times drift or the chain halts.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from web3 import Web3

from cerebro_mcp.chains import GNOSIS_CHAIN_ID, get_chain
from cerebro_mcp.clients.abi_resolver import resolve_abi
from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.clients.web3 import rpc_manager
from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.chunking import run_pool
from cerebro_mcp.rpc_scan.utils import (
    block_at_timestamp,
    block_timestamp,
    first_block_with_code,
    parse_timestamp,
)
from cerebro_mcp.runtime.tool_output import normalize_value


#: Head offset. ``latest`` moves between requests within one sweep, and a
#: reorg would silently change what an early sample measured.
HEAD_CONFIRMATIONS = 5

_RELATIVE_RE = re.compile(r"^(\d+)\s*([smhdwy])$", re.IGNORECASE)
_RELATIVE_SECONDS = {
    "s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 604_800, "y": 31_536_000,
}

#: Point statuses. Anything that is not ``ok`` still occupies a slot in the
#: series so the x-axis stays truthful — the chart renders them as gaps.
STATUS_OK = "ok"
STATUS_NOT_DEPLOYED = "not_deployed"
STATUS_REVERTED = "reverted"
STATUS_NO_STATE = "no_state"      # pruned/unavailable historical state
STATUS_ERROR = "error"


def _iso(unix_seconds: int) -> str:
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).strftime("%Y-%m-%d")


def _classify_error(exc: Exception) -> tuple[str, str]:
    """Map an eth_call failure to (status, human message).

    The distinction matters: a pre-deployment sample is expected and boring,
    a pruned archive node invalidates the whole sweep, and a revert is a real
    property of the contract at that block.
    """
    message = str(exc)
    lowered = message.lower()
    if "could not transact" in lowered or "returned no data" in lowered:
        # web3's BadFunctionCallOutput: eth_call answered "0x". Almost always
        # "no contract here yet" — Geth/Erigon return empty WITHOUT an error.
        return STATUS_NOT_DEPLOYED, "no code at this block (contract not deployed yet)"
    if "revert" in lowered:
        return STATUS_REVERTED, message
    if (
        "missing trie node" in lowered
        or "header not found" in lowered
        or "state is not available" in lowered
        or "state not available" in lowered
        or "missing state" in lowered
    ):
        return STATUS_NO_STATE, "historical state unavailable (node is pruned, not archive)"
    return STATUS_ERROR, message


def _resolve_relative(text: str) -> int | None:
    """``"30d"`` -> seconds. Returns None when ``text`` is not relative."""
    match = _RELATIVE_RE.match(text.strip())
    if not match:
        return None
    return int(match.group(1)) * _RELATIVE_SECONDS[match.group(2).lower()]


def _sample_blocks(lo: int, hi: int, points: int) -> list[int]:
    """``points`` evenly spaced block numbers over ``[lo, hi]``, inclusive.

    Deduplicated and sorted, so a range shorter than ``points`` yields one
    sample per block rather than repeats.
    """
    if hi < lo:
        lo, hi = hi, lo
    points = max(2, int(points))
    if hi - lo + 1 <= points:
        return list(range(lo, hi + 1))
    step = (hi - lo) / (points - 1)
    return sorted({lo + round(i * step) for i in range(points)})


def _to_float(value: Any, decimals: int | None) -> float | None:
    """Plottable scalar, or None when the output isn't a single number.

    Big ``uint256`` values must not cross the wire as JS numbers — the raw
    value ships as a string and this is only for the y-axis.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        scaled = float(value)
        if decimals:
            scaled = scaled / (10 ** int(decimals))
        return scaled
    return None


def read_function_history(
    ch: ClickHouseManager,
    address: str,
    *,
    chain_id: int = GNOSIS_CHAIN_ID,
    function_name: str = "",
    function_signature: str = "",
    args: list[Any] | None = None,
    from_block: int | str | None = None,
    to_block: int | str | None = None,
    since: str = "",
    until: str = "",
    points: int = 0,
    target: str = "auto",
    output_index: int = 0,
    decimals: int | None = None,
) -> dict[str, Any]:
    """Sample one view/pure function across a block range.

    Range may be given as blocks (``from_block``/``to_block``), as timestamps
    or ISO dates (``since``/``until``), or as a relative window
    (``since="30d"``). Defaults to the last 30 days.

    Returns a dict with ``points`` — one entry per sampled block, each
    ``{block, timestamp, status, value, value_float, error}`` — plus the
    resolved range, the signature, and any ``warnings``. Individual sample
    failures never abort the sweep; a wall-clock deadline returns partial
    results with ``truncated=True`` rather than stalling the server.
    """
    started = time.monotonic()
    deadline = started + settings.CONTRACT_HISTORY_DEADLINE_SECONDS
    chain = get_chain(chain_id)
    checksum = Web3.to_checksum_address(address)
    warnings: list[str] = []

    max_points = settings.CONTRACT_HISTORY_MAX_POINTS
    requested = int(points or settings.CONTRACT_HISTORY_DEFAULT_POINTS)
    if requested > max_points:
        warnings.append(f"points capped at {max_points} (requested {requested})")
        requested = max_points

    # -- function ---------------------------------------------------------
    record = resolve_abi(ch, checksum, target=target, chain_id=chain.chain_id)
    w3 = rpc_manager.archive(chain.chain_id)
    contract = w3.eth.contract(address=checksum, abi=record.abi)
    if function_signature:
        fn_factory = contract.get_function_by_signature(function_signature)
    elif function_name:
        fn_factory = contract.get_function_by_name(function_name)
    else:
        raise ValueError("provide function_name or function_signature.")

    fn_abi = fn_factory.abi
    mutability = fn_abi.get("stateMutability", "")
    if mutability not in {"view", "pure"}:
        raise ValueError("only view/pure functions can be read historically.")
    if mutability == "pure":
        warnings.append("function is pure — its value cannot change over blocks")

    signature = (
        f"{fn_abi.get('name', '')}("
        f"{','.join(i.get('type', '') for i in fn_abi.get('inputs', []))})"
    )
    outputs = fn_abi.get("outputs") or []
    call_args = _checksum_args(fn_abi, list(args or []))

    # -- range ------------------------------------------------------------
    router = RpcRouter.for_chain(chain.chain_id)
    client = router.archive if router.has_archive() else router.standard
    head = max(1, router.latest_block() - HEAD_CONFIRMATIONS)

    hi, _ = _resolve_bound(router, to_block, until, default=head, head=head)
    lo, requested_start_ts = _resolve_bound(
        router,
        from_block,
        since or f"{settings.CONTRACT_HISTORY_DEFAULT_DAYS}d",
        default=None,
        head=head,
        reference_block=hi,
        client=client,
    )
    if lo > hi:
        lo, hi = hi, lo

    # Floor every lookup at what the endpoint actually serves. Below it,
    # eth_getCode does not merely return empty — it errors with "no state
    # found", which would abort the whole sweep.
    floor = router.lowest_available_block(hi)
    if lo < floor:
        lo = floor
        truncated_by_node = True
    else:
        # `block_at_timestamp` clamps to the floor internally, so a request
        # reaching further back arrives here ALREADY equal to it. Compare the
        # timestamp the caller actually asked for against the floor block's,
        # or the truncation is silent — the user asked for three years and
        # would have quietly received one.
        truncated_by_node = bool(
            requested_start_ts is not None
            and lo == floor
            and requested_start_ts < block_timestamp(router, client, floor) - 60
        )

    if truncated_by_node:
        warnings.append(
            f"history before block {floor:,} "
            f"({_iso(block_timestamp(router, client, floor))}) is not served by "
            f"this endpoint — pruned, or below a chain migration boundary. "
            f"The range starts there, not where you asked."
        )

    # Clamp to the deployment block: eth_call against an address with no code
    # returns "0x" with NO error, which decodes into a confusing failure
    # rather than an honest "didn't exist yet".
    deployed_at = first_block_with_code(router, client, checksum, floor, hi)
    if deployed_at is None:
        raise ValueError(
            f"No contract code at {checksum} on {chain.name} as of block {hi}."
        )
    if deployed_at > lo:
        warnings.append(
            f"range starts at the deployment block {deployed_at:,} "
            f"(requested {lo:,})"
        )
        lo = deployed_at

    blocks = _sample_blocks(lo, hi, requested)

    # -- sweep ------------------------------------------------------------
    def sample(block: int) -> dict[str, Any]:
        """Never raises — run_pool's fut.result() would propagate it."""
        point: dict[str, Any] = {
            "block": block,
            "timestamp": None,
            "status": STATUS_OK,
            "value": None,
            "value_float": None,
            "error": "",
        }
        try:
            point["timestamp"] = block_timestamp(router, client, block)
        except Exception as exc:  # noqa: BLE001 — a missing header is not fatal
            point["error"] = f"header unavailable: {exc}"
        try:
            raw = fn_factory(*call_args).call(block_identifier=block)
        except Exception as exc:  # noqa: BLE001
            status, message = _classify_error(exc)
            point["status"] = status
            point["error"] = message
            return point

        value = raw[output_index] if isinstance(raw, (list, tuple)) else raw
        point["value"] = normalize_value(value)
        point["value_float"] = _to_float(value, decimals)
        return point

    collected: list[dict[str, Any]] = []
    truncated = False

    def past_deadline() -> bool:
        nonlocal truncated
        if time.monotonic() >= deadline:
            truncated = True
            return True
        return False

    for point in run_pool(
        sample,
        blocks,
        settings.CONTRACT_HISTORY_WORKERS,
        should_stop=past_deadline,
    ):
        collected.append(point)

    collected.sort(key=lambda p: p["block"])

    if truncated:
        warnings.append(
            f"stopped after {settings.CONTRACT_HISTORY_DEADLINE_SECONDS}s — "
            f"{len(collected)} of {len(blocks)} samples returned. "
            "Narrow the range or lower `points`."
        )
    if any(p["status"] == STATUS_NO_STATE for p in collected):
        warnings.append(
            f"Historical state is unavailable on this endpoint — set "
            f"RPC_URL_{chain.rpc_env_key}_ARCHIVE to a true archive node."
        )

    ok_points = [p for p in collected if p["status"] == STATUS_OK]
    return {
        "chain_id": chain.chain_id,
        "chain_name": chain.name,
        "address": checksum,
        "contract_name": record.contract_name,
        "signature": signature,
        "args": call_args,
        "output_types": [o.get("type", "") for o in outputs],
        "output_index": output_index,
        "decimals": decimals,
        "from_block": lo,
        "to_block": hi,
        "requested_points": requested,
        "points": collected,
        "ok_count": len(ok_points),
        "truncated": truncated,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "warnings": warnings,
    }


def _resolve_bound(
    router: RpcRouter,
    block: int | str | None,
    when: str,
    *,
    default: int | None,
    head: int,
    reference_block: int | None = None,
    client: Any = None,
) -> tuple[int, int | None]:
    """Resolve one end of the range to ``(block, requested_timestamp)``.

    Precedence: an explicit block wins; otherwise a timestamp / ISO date /
    relative window is bisected into a block; otherwise ``default``.

    The second element is the timestamp the caller actually asked for, or
    ``None`` for block-based bounds. The caller needs it to notice when the
    endpoint could not reach that far back — ``block_at_timestamp`` clamps
    silently, so the returned block alone cannot reveal the truncation.
    """
    if block is not None and block != "":
        if isinstance(block, int):
            return int(block), None
        text = str(block).strip().lower()
        if text in {"latest", "safe", "finalized", "pending"}:
            return head, None
        if text == "earliest":
            return 1, None
        if text.startswith("0x"):
            return int(text, 16), None
        if text.isdigit():
            return int(text), None
        raise ValueError(f"Bad block identifier: {block!r}")

    if when:
        relative = _resolve_relative(when)
        if relative is not None:
            anchor = reference_block if reference_block is not None else head
            anchor_ts = block_timestamp(router, client or router.standard, anchor)
            target = anchor_ts - relative
        else:
            target = parse_timestamp(when)
        return block_at_timestamp(router, target, 1, head), target

    if default is None:
        raise ValueError("could not resolve the start of the range")
    return default, None


def _checksum_args(fn_abi: dict[str, Any], args: list[Any]) -> list[Any]:
    """Re-checksum address inputs — mirrors ``tools/web3/rpc._checksum_args``."""
    inputs = fn_abi.get("inputs") or []
    if len(args) != len(inputs):
        return args
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


__all__ = [
    "HEAD_CONFIRMATIONS",
    "STATUS_ERROR",
    "STATUS_NOT_DEPLOYED",
    "STATUS_NO_STATE",
    "STATUS_OK",
    "STATUS_REVERTED",
    "read_function_history",
]
