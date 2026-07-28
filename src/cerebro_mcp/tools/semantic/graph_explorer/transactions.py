"""Transactions mode — per-transfer-leg forensics (app-only).

``load_graph_transactions`` opens a bounded set of transactions and returns
EVERY transfer leg of each, in chain order. Nothing is aggregated: the unit is
``(transaction_hash, log_index)``, because the signature of a swap, a batch
settlement, a liquidation or a drain lives in the order and adjacency of legs
inside one transaction — exactly what Flows' ``(source, target, token)``
aggregation destroys.

Three entry points, matching how an investigation actually arrives here:
  1. explicit ``tx_hashes`` — "what did this transaction do?"
  2. ``seed_node_id`` — "show me what this address has been doing" across the
     existing execution transaction/log tables plus their uncovered RPC head
  3. ``seed_node_id`` + ``counterparty_ids`` — "show me the transactions behind
     this flow edge", i.e. the drill-down that the 25-row evidence panel could
     never honestly provide.

Every payload carries a ``scope`` contract (rows returned vs rows that exist,
the window actually applied, the data horizon). The audit that motivated this
mode found panels reporting ``exact_bounded`` while silently capped, so this
module refuses to claim exactness it has not verified: ``truncated`` is derived
from a COUNT over the same predicate, not from whether the row buffer looked
full.

Transactions are never split. A partial transaction is worse than no
transaction — half a swap reads as a theft — so the leg cap drops whole
trailing transactions and says so.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import (
    DISCOVERY_QUERY_BUDGET,
    INTERACTIVE_QUERY_BUDGET,
    ClickHouseManager,
    QueryBudget,
)
from cerebro_mcp.chains import (
    GNOSIS_CHAIN_ID,
    get_chain,
    has_rpc,
    resolve_chain,
    rpc_env_hint,
)
from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.semantic.tx_queries import (
    BURN_ADDRESSES,
    CHAIN_LOG_RELATIONS,
    CHAIN_TRANSACTION_RELATIONS,
    PRICES_RELATION,
    TOKENS_META_RELATION,
    build_all_history_tx_discovery_chunk_sql,
    build_data_horizon_sql,
    build_leg_total_sql,
    build_legs_sql,
    build_token_contract_sql,
    build_tx_discovery_sql,
)
from cerebro_mcp.tools.visualization import mini_apps

from . import constants
from .forensics import (
    canonical_row_hash,
    forensic_scope,
    new_scope_id,
    source_record,
    validate_source_contract,
)
from .state import (
    build_dataset_append_patch,
    build_payload,
    dataset_from_rows,
    short_id,
)
from .ui_tools import _normalize_node_id

logger = logging.getLogger(__name__)


def _resolve_tx_blocks(
    hashes: list[str], *, chain_id: int = GNOSIS_CHAIN_ID
) -> tuple[dict[str, int], list[str]]:
    """Resolve each transaction hash to its block via RPC.

    Required, not an optimisation: ``execution.logs`` and
    ``execution.transactions`` are both ordered by block, so a bare
    ``WHERE transaction_hash = …`` is a full scan that times out at 30s
    (measured). ``eth_getTransactionByHash`` returns the block instantly and
    lets every subsequent query be block-bounded.

    Returns ``(hash -> block, unresolved)``. An unresolved hash is reported
    rather than silently dropped — "not found" and "not looked up" must not
    look the same to an investigator.
    """
    blocks: dict[str, int] = {}
    unresolved: list[str] = []
    if not hashes:
        return blocks, unresolved
    try:
        client = _router(chain_id).standard
    except Exception as exc:  # pragma: no cover - config dependent
        logger.info("tx mode: RPC unavailable: %s", exc)
        return blocks, list(hashes)
    for h in hashes:
        try:
            tx = client.request("eth_getTransactionByHash", [_hex0x(h)])
            if tx and tx.get("blockNumber"):
                blocks[h] = int(tx["blockNumber"], 16)
            else:
                unresolved.append(h)
        except Exception as exc:
            logger.info("tx mode: hash resolve failed for %s: %s", h, exc)
            unresolved.append(h)
    return blocks, unresolved


TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

_RPC_WORD_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_RPC_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_address_rpc_cache_lock = threading.Lock()
#: Keyed by (chain_id, address). The chain is part of the key because the same
#: address exists on every EVM chain and holds completely different history on
#: each; keying on the address alone would serve a Base scan's rows to a Gnosis
#: question. (This cache is still process-local and, notably, is written
#: without checking whether the scan COMPLETED — see the note at its write
#: site. Making it durable requires fixing that first.)
_address_rpc_cache: dict[tuple[int, str], tuple[int, list[list[Any]]]] = {}
_GNOSIS_CHAIN_GENESIS_UTC = datetime(2018, 10, 8)


def _router(chain_id: int) -> RpcRouter:
    """The RPC router for a chain.

    Single funnel replacing six ``RpcRouter.from_settings()`` calls, which read
    only ``GNOSIS_RPC_URL`` and so pinned this whole module to one chain.
    ``for_chain`` is memoized per chain and resolves through ``chains.py``,
    where Gnosis still honours the legacy env pair first — so chain 100 keeps
    byte-identical behaviour.
    """
    return RpcRouter.for_chain(int(chain_id))


def _redact_endpoint(exc: BaseException) -> str:
    """An RPC error message with any endpoint URL removed.

    ``requests``' ``raise_for_status`` formats as
    ``"503 Server Error: ... for url: <full URL>"``, and provider URLs
    routinely embed API keys (``chains.py`` states the rule: endpoint URLs must
    never reach tool output or logs). These strings land in the forensic scope,
    which ``caseExport`` writes verbatim into an exported case bundle — so a
    leak here would be persisted to disk and shared.
    """
    text = str(exc)
    text = re.sub(r"\s*for url:\s*\S+", "", text)
    text = re.sub(r"https?://\S+", "<endpoint redacted>", text)
    return text.strip() or exc.__class__.__name__
_MAX_RPC_DIRECT_TAIL_BLOCKS = 10_000
_DISCOVERY_MIN_SLICE = timedelta(hours=1)
# Real-data timings for the audited address: a seven-day June tile took 7.58s
# and a two-day tile 5.29s, while the one-day tile containing the known
# activity completed in 2.32s.  Start with the largest measured-safe unit and
# adapt down from there on timeout/memory pressure.
_DISCOVERY_DEFAULT_TILE_SECONDS = 24 * 60 * 60
_DISCOVERY_MIN_TILE_SECONDS = int(_DISCOVERY_MIN_SLICE.total_seconds())
_DISCOVERY_WALL_BUDGET_SECONDS = 3.75
_DISCOVERY_HORIZON_QUERY_BUDGET = QueryBudget(
    # A one-second guard made the four-relation UNION horizon probe fail at
    # ~1004ms on the live warehouse, which incorrectly made every discovery
    # source look unavailable before candidate scanning even began.
    max_execution_time=5,
    max_memory_usage=256 * 2**20,
    max_result_rows=100,
    max_threads=1,
)
_ADDRESS_DISCOVERY_QUERY_BUDGET = QueryBudget(
    max_execution_time=4,
    max_memory_usage=1536 * 2**20,
    max_result_rows=10_000,
    max_threads=2,
)
_TX_CONTEXT_COLUMNS = [
    "tx_hash",
    "initiator",
    "called_contract",
    "method_selector",
    "input",
    "nonce",
    "native_value_raw",
    "gas_limit",
    "gas_used",
    "effective_gas_price",
    "fee_wei",
    "receipt_status",
    "block_number",
    "transaction_index",
    "block_timestamp",
    "matched_because",
]


def _encode_discovery_cursor(row: list[Any]) -> str:
    """Opaque, deterministic keyset cursor for newest-first candidates."""
    payload = json.dumps(
        [int(row[1] or 0), int(row[2] or 0), _hex0x(str(row[0]))],
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class _CoverageDiscoveryCursor:
    """Continue an execution-time coverage scan without skipping a gap.

    ``before_time`` is always an exclusive upper bound.  When
    ``retry_from_time`` is present the cursor targets that exact unresolved
    slice first; after it succeeds, pagination continues below its lower
    boundary.  Older version-one cursors omit the lower bound and retain the
    original "continue before" behavior.
    """

    before_time: str
    tile_seconds: int = _DISCOVERY_DEFAULT_TILE_SECONDS
    retry_from_time: str | None = None


DiscoveryCursor = tuple[int, int, str] | _CoverageDiscoveryCursor


def _encode_coverage_discovery_cursor(
    before_time: str,
    *,
    tile_seconds: int = _DISCOVERY_DEFAULT_TILE_SECONDS,
    retry_from_time: str | None = None,
) -> str:
    boundary = datetime.fromisoformat(str(before_time).replace("Z", "+00:00"))
    if boundary.tzinfo is None:
        boundary = boundary.replace(tzinfo=timezone.utc)
    else:
        boundary = boundary.astimezone(timezone.utc)
    retry_start: datetime | None = None
    if retry_from_time is not None:
        retry_start = datetime.fromisoformat(
            str(retry_from_time).replace("Z", "+00:00")
        )
        if retry_start.tzinfo is None:
            retry_start = retry_start.replace(tzinfo=timezone.utc)
        else:
            retry_start = retry_start.astimezone(timezone.utc)
        if (
            retry_start < _GNOSIS_CHAIN_GENESIS_UTC.replace(tzinfo=timezone.utc)
            or retry_start >= boundary
        ):
            raise ValueError("coverage retry slice must be within chain history")
    cursor_payload: dict[str, Any] = {
        "v": 2 if retry_start is not None else 1,
        "kind": "coverage",
        "before_time": boundary.isoformat().replace("+00:00", "Z"),
        "tile_seconds": max(
            _DISCOVERY_MIN_TILE_SECONDS,
            min(int(tile_seconds), _DISCOVERY_DEFAULT_TILE_SECONDS),
        ),
    }
    if retry_start is not None:
        cursor_payload["retry_from_time"] = retry_start.isoformat().replace(
            "+00:00", "Z"
        )
    payload = json.dumps(
        cursor_payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_discovery_cursor(value: str) -> DiscoveryCursor:
    try:
        encoded = str(value or "").strip()
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        decoded = json.loads(raw.decode("utf-8"))
        if isinstance(decoded, dict):
            if decoded.get("v") not in {1, 2} or decoded.get("kind") != "coverage":
                raise ValueError("unsupported discovery cursor")
            boundary = datetime.fromisoformat(
                str(decoded.get("before_time") or "").replace("Z", "+00:00")
            )
            if boundary.tzinfo is None:
                boundary = boundary.replace(tzinfo=timezone.utc)
            boundary = boundary.astimezone(timezone.utc)
            if boundary <= _GNOSIS_CHAIN_GENESIS_UTC.replace(tzinfo=timezone.utc):
                raise ValueError("coverage cursor is already at chain genesis")
            tile_seconds = max(
                _DISCOVERY_MIN_TILE_SECONDS,
                min(
                    int(
                        decoded.get("tile_seconds")
                        or _DISCOVERY_DEFAULT_TILE_SECONDS
                    ),
                    _DISCOVERY_DEFAULT_TILE_SECONDS,
                ),
            )
            retry_from_time: str | None = None
            if decoded.get("retry_from_time") is not None:
                retry_start = datetime.fromisoformat(
                    str(decoded["retry_from_time"]).replace("Z", "+00:00")
                )
                if retry_start.tzinfo is None:
                    retry_start = retry_start.replace(tzinfo=timezone.utc)
                else:
                    retry_start = retry_start.astimezone(timezone.utc)
                if (
                    retry_start
                    < _GNOSIS_CHAIN_GENESIS_UTC.replace(tzinfo=timezone.utc)
                    or retry_start >= boundary
                ):
                    raise ValueError("invalid coverage retry slice")
                retry_from_time = retry_start.isoformat().replace("+00:00", "Z")
            return _CoverageDiscoveryCursor(
                before_time=boundary.isoformat().replace("+00:00", "Z"),
                tile_seconds=tile_seconds,
                retry_from_time=retry_from_time,
            )
        block, index, transaction_hash = decoded
        transaction_hash = _hex0x(str(transaction_hash))
        if not re.fullmatch(r"0x[0-9a-f]{64}", transaction_hash):
            raise ValueError("invalid transaction hash")
        return int(block), int(index), transaction_hash
    except (binascii.Error, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is not a valid Graph Explorer discovery cursor") from exc


def _coverage_continuation_boundary(
    scanned_ranges: list[dict[str, str]], *, through: str
) -> str | None:
    """Return the oldest boundary in the contiguous fully-scanned newest suffix.

    We never jump across an uncovered tile.  The returned instant is exclusive
    for the next page: everything at/after it was already searched.
    """
    try:
        horizon = datetime.fromisoformat(str(through).replace("Z", "+00:00"))
        if horizon.tzinfo is None:
            horizon = horizon.replace(tzinfo=timezone.utc)
        else:
            horizon = horizon.astimezone(timezone.utc)
        boundary = horizon + timedelta(seconds=1)
        parsed: list[tuple[datetime, datetime]] = []
        for item in scanned_ranges:
            lo = datetime.fromisoformat(str(item["t0"]).replace("Z", "+00:00"))
            hi = datetime.fromisoformat(str(item["t1"]).replace("Z", "+00:00"))
            if lo.tzinfo is None:
                lo = lo.replace(tzinfo=timezone.utc)
            else:
                lo = lo.astimezone(timezone.utc)
            if hi.tzinfo is None:
                hi = hi.replace(tzinfo=timezone.utc)
            else:
                hi = hi.astimezone(timezone.utc)
            if lo < hi:
                parsed.append((lo, hi))
    except (KeyError, TypeError, ValueError):
        return None

    advanced = True
    while advanced:
        advanced = False
        for lo, hi in parsed:
            if hi == boundary and lo < boundary:
                boundary = lo
                advanced = True
                break
    newest_end = horizon + timedelta(seconds=1)
    if (
        boundary >= newest_end
        or boundary <= _GNOSIS_CHAIN_GENESIS_UTC.replace(tzinfo=timezone.utc)
    ):
        return None
    return boundary.isoformat().replace("+00:00", "Z")


def _newest_uncovered_retry_slice(
    uncovered_ranges: list[dict[str, str]],
    *,
    tile_seconds: int,
) -> tuple[str, str] | None:
    """Return a bounded slice of the newest unresolved execution range.

    Coverage may contain both the tile that actually failed and a broad
    synthetic range describing older work that was deliberately not attempted.
    Selecting the range with the newest exclusive end retries the blocking gap
    first.  Capping it to the next adaptive tile ensures a multi-year
    "not-scanned" range never turns back into an unbounded query.
    """
    parsed: list[tuple[datetime, datetime]] = []
    try:
        for item in uncovered_ranges:
            lo = datetime.fromisoformat(str(item["t0"]).replace("Z", "+00:00"))
            hi = datetime.fromisoformat(str(item["t1"]).replace("Z", "+00:00"))
            if lo.tzinfo is None:
                lo = lo.replace(tzinfo=timezone.utc)
            else:
                lo = lo.astimezone(timezone.utc)
            if hi.tzinfo is None:
                hi = hi.replace(tzinfo=timezone.utc)
            else:
                hi = hi.astimezone(timezone.utc)
            lo = max(lo, _GNOSIS_CHAIN_GENESIS_UTC.replace(tzinfo=timezone.utc))
            if lo < hi:
                parsed.append((lo, hi))
    except (KeyError, TypeError, ValueError):
        return None
    if not parsed:
        return None
    lo, hi = max(parsed, key=lambda bounds: (bounds[1], bounds[0]))
    bounded_seconds = max(
        _DISCOVERY_MIN_TILE_SECONDS,
        min(int(tile_seconds), _DISCOVERY_DEFAULT_TILE_SECONDS),
    )
    retry_start = max(lo, hi - timedelta(seconds=bounded_seconds))
    return (
        retry_start.isoformat().replace("+00:00", "Z"),
        hi.isoformat().replace("+00:00", "Z"),
    )


def _uncovered_requires_smaller_tile(
    uncovered_ranges: list[dict[str, str]],
) -> bool:
    """Return true only when ClickHouse rejected an attempted tile.

    Reaching the loader's own wall budget means older work was not attempted;
    it is pagination, not evidence that the current one-day tile is too large.
    Shrinking on that signal made every subsequent click cover only half a day.
    """
    for item in uncovered_ranges:
        reason = str(item.get("reason") or "")
        if "interactive discovery wall-time budget reached" in reason.lower():
            continue
        if _is_subdividable_discovery_error(RuntimeError(reason)):
            return True
    return False


def _is_subdividable_discovery_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "timeout",
            "timed out",
            "time limit",
            "memory limit exceeded",
            "memory_limit_exceeded",
            "overcommittracker",
            "overcommit tracker",
            "code: 241",
            "error code 241",
        )
    )


def _coalesce_scanned_ranges(
    ranges: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Compact adjacent internal tiles before publishing scope metadata."""
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item["t0"], item["t1"]))
    merged: list[dict[str, str]] = []
    for item in ordered:
        if merged and merged[-1]["t1"] == item["t0"]:
            merged[-1]["t1"] = item["t1"]
        else:
            merged.append(dict(item))
    return merged


def _raw_preview(value: Any, limit: int = 130) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _legs_from_receipts(
    hashes: list[str],
    *,
    raw_receipt_rows: list[list[Any]] | None = None,
    transaction_context_rows: list[list[Any]] | None = None,
    match_address: str = "",
    filter_tokens: list[str] | None = None,
    chain_id: int = GNOSIS_CHAIN_ID,
) -> tuple[
    list[list[Any]],
    list[str],
    dict[str, str],
    dict[str, int],
    list[dict[str, Any]],
]:
    """Read transfer legs straight from the chain via RPC receipts.

    For a KNOWN transaction this beats SQL on every axis that matters:
    ~155ms vs ~7s (the log tables are ordered by block, so a hash predicate
    scans), and the receipt is the authoritative leg set — no whitelist, no
    indexer lag, plus the execution status. Plain address discovery uses stored
    execution logs plus an ``eth_getLogs`` scan of only the unindexed RPC head;
    SQL is retained for an explicitly scoped Money Trail edge and as a
    disclosed receipt fallback.

    Returns ``(raw_rows, unresolved, statuses, blocks, decode_failures)`` shaped
    like the SQL path so the caller is agnostic: [tx_hash, log_index,
    block_number, transaction_index, block_timestamp, source, target, token,
    symbol, amount, amount_usd].
    Symbol/amount/USD are filled by the enrichment pass; the receipt carries
    only raw values.  A status entry also proves that a receipt with zero
    ERC-20 legs was successfully inspected; absence of rows cannot do that.
    """
    rows: list[list[Any]] = []
    unresolved: list[str] = []
    statuses: dict[str, str] = {}
    blocks: dict[str, int] = {}
    decode_failures: list[dict[str, Any]] = []
    if not hashes:
        return rows, unresolved, statuses, blocks, decode_failures
    try:
        client = _router(chain_id).standard
    except Exception as exc:  # pragma: no cover - config dependent
        logger.info("tx mode: RPC unavailable, falling back to SQL: %s", exc)
        return rows, list(hashes), statuses, blocks, decode_failures
    block_timestamps: dict[int, str] = {}
    normalized_match = _normalize_node_id(match_address) if match_address else ""
    normalized_tokens = {
        _normalize_node_id(token) for token in (filter_tokens or []) if token
    }

    def quantity(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(str(value), 16)
        except (TypeError, ValueError):
            return None

    for h in hashes:
        tx: dict[str, Any] | None = None
        try:
            if transaction_context_rows is None:
                rec = client.request("eth_getTransactionReceipt", [_hex0x(h)])
            else:
                # Receipt and envelope are independent RPC reads. Fetching them
                # concurrently keeps selection latency close to one round trip
                # without downloading a full block's transaction objects.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    receipt_future = pool.submit(
                        client.request, "eth_getTransactionReceipt", [_hex0x(h)]
                    )
                    tx_future = pool.submit(
                        client.request, "eth_getTransactionByHash", [_hex0x(h)]
                    )
                    rec = receipt_future.result()
                    try:
                        tx_value = tx_future.result()
                        tx = tx_value if isinstance(tx_value, dict) else None
                    except Exception as exc:
                        # The receipt remains authoritative for its log set.
                        # Missing envelope context makes enrichment partial; it
                        # must not erase a successfully read receipt.
                        logger.info(
                            "tx mode: transaction envelope failed for %s: %s", h, exc
                        )
                        tx = None
        except Exception as exc:
            logger.info("tx mode: receipt failed for %s: %s", h, exc)
            unresolved.append(h)
            continue
        if not rec:
            unresolved.append(h)
            continue
        block = int(rec.get("blockNumber", "0x0"), 16)
        tx_index = int(rec.get("transactionIndex", "0x0"), 16)
        raw_status = rec.get("status")
        if raw_status is None:
            status = "unknown"
        else:
            try:
                status = "success" if int(str(raw_status), 16) == 1 else "reverted"
            except (TypeError, ValueError):
                status = "unknown"
        statuses[_hex0x(h)] = status
        if block:
            blocks[_hex0x(h)] = block
        if raw_receipt_rows is not None:
            receipt_json = json.dumps(
                rec,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            )
            logs_json = json.dumps(
                rec.get("logs") or [],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            )
            raw_receipt_rows.append(
                [
                    _hex0x(h),
                    receipt_json,
                    hashlib.sha256(receipt_json.encode("utf-8")).hexdigest(),
                    hashlib.sha256(logs_json.encode("utf-8")).hexdigest(),
                    block,
                    tx_index,
                    str(rec.get("blockHash") or "").lower(),
                    status,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                ]
            )
        if block and block not in block_timestamps:
            try:
                block_data = client.request("eth_getBlockByNumber", [hex(block), False])
                timestamp = int((block_data or {}).get("timestamp", "0x0"), 16)
                block_timestamps[block] = (
                    datetime.fromtimestamp(timestamp, timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    if timestamp
                    else ""
                )
            except Exception as exc:  # timestamp is useful, not leg authority
                logger.info("tx mode: block timestamp failed for %s: %s", block, exc)
                block_timestamps[block] = ""

        if transaction_context_rows is not None:
            envelope = tx or {}
            initiator = _normalize_node_id(str(envelope.get("from") or ""))
            called_contract = _normalize_node_id(str(envelope.get("to") or ""))
            input_data = str(envelope.get("input") or "")
            method_selector = (
                input_data[:10].lower()
                if input_data.startswith("0x") and len(input_data) >= 10
                else ""
            )
            matched_because: list[str] = []
            if normalized_match:
                if initiator == normalized_match:
                    matched_because.append("direct_sender")
                if called_contract == normalized_match:
                    matched_because.append("direct_recipient")
                for rpc_log in rec.get("logs") or []:
                    topics = rpc_log.get("topics") or []
                    if len(topics) != 3 or str(topics[0]).lower() != TRANSFER_TOPIC0:
                        continue
                    source = "0x" + str(topics[1])[-40:].lower()
                    target = "0x" + str(topics[2])[-40:].lower()
                    token = _normalize_node_id(str(rpc_log.get("address") or ""))
                    if source == normalized_match:
                        matched_because.append("erc20_sender")
                    if target == normalized_match:
                        matched_because.append("erc20_recipient")
                    if normalized_tokens and token in normalized_tokens:
                        matched_because.append("token_filter")
            if not matched_because:
                matched_because.append("explicit_hash")
            gas_used = quantity(rec.get("gasUsed"))
            gas_price = quantity(
                rec.get("effectiveGasPrice") or envelope.get("gasPrice")
            )
            native_value = quantity(envelope.get("value"))
            transaction_context_rows.append(
                [
                    _hex0x(h),
                    initiator,
                    called_contract,
                    method_selector,
                    input_data,
                    quantity(envelope.get("nonce")),
                    str(native_value) if native_value is not None else None,
                    quantity(envelope.get("gas")),
                    gas_used,
                    gas_price,
                    gas_used * gas_price
                    if gas_used is not None and gas_price is not None
                    else None,
                    status,
                    block,
                    tx_index,
                    block_timestamps.get(block, ""),
                    sorted(set(matched_because)),
                ]
            )
        for log in rec.get("logs") or []:
            topics = log.get("topics") or []
            # 3 topics == ERC-20 Transfer; 4 means ERC-721 (tokenId indexed).
            if (
                len(topics) != 3
                or not isinstance(topics[0], str)
                or topics[0].lower() != TRANSFER_TOPIC0
            ):
                continue
            failure_index: int | None = None
            try:
                raw_log_index = log.get("logIndex")
                if not isinstance(raw_log_index, str):
                    raise ValueError("logIndex is not a hex quantity")
                failure_index = int(raw_log_index, 16)

                topic1, topic2 = topics[1], topics[2]
                if not isinstance(topic1, str) or not _RPC_WORD_RE.fullmatch(topic1):
                    raise ValueError("topic1 is not a 32-byte indexed address")
                if not isinstance(topic2, str) or not _RPC_WORD_RE.fullmatch(topic2):
                    raise ValueError("topic2 is not a 32-byte indexed address")
                if topic1[2:26] != "0" * 24 or topic2[2:26] != "0" * 24:
                    raise ValueError("indexed address topic is not zero-padded")

                token = str(log.get("address", ""))
                if not _RPC_ADDRESS_RE.fullmatch(token):
                    raise ValueError("emitter is not a 20-byte token address")

                data = log.get("data")
                if not isinstance(data, str) or not _RPC_WORD_RE.fullmatch(data):
                    raise ValueError("data is not a 32-byte uint256 ABI word")
                raw_amount = int(data, 16)
            except (TypeError, ValueError) as exc:
                decode_failures.append(
                    {
                        "transaction_hash": _hex0x(h),
                        "log_index": failure_index,
                        "error": str(exc),
                        "raw_data": _raw_preview(log.get("data")),
                    }
                )
                continue
            rows.append([
                _hex0x(h),
                failure_index,
                block,
                tx_index,
                block_timestamps.get(block, ""),
                "0x" + topic1[-40:].lower(),  # from
                "0x" + topic2[-40:].lower(),  # to
                token.lower(),                 # token
                "",                                   # symbol (enriched)
                raw_amount,                           # RAW (scaled on enrich)
                None,                                 # usd (enriched)
                status,
            ])
    rows.sort(key=lambda r: (r[2], r[3], r[1]))
    return rows, unresolved, statuses, blocks, decode_failures


def _discover_address_transactions_rpc(
    address: str,
    *,
    after_block: int = 0,
    after_index: int = -1,
    tokens: list[str] | None = None,
    counterparty_ids: list[str] | None = None,
    router: RpcRouter | None = None,
    chain_id: int = GNOSIS_CHAIN_ID,
    chunk_size: int = 500_000,
    min_chunk_size: int = 100_000,
    max_workers: int = 8,
) -> tuple[list[list[Any]], int]:
    """Discover an address's standard ERC-20 Transfer transactions via RPC.

    The analyst scope is the complete chain history from genesis through the
    head read at request start. ``chunk_size`` is only an RPC transport detail:
    nodes commonly reject one enormous ``eth_getLogs`` call, so failed chunks
    split recursively without changing the evidence predicate. The result is
    considered complete only if every outbound and inbound chunk succeeds.

    Rows match ``TX_LIST_COLUMNS`` except that timestamps are initially blank;
    authoritative receipt loading below supplies transaction/log details.
    """
    normalized = _normalize_node_id(address)
    if not normalized or not _RPC_ADDRESS_RE.fullmatch(normalized):
        raise ValueError("address must be a 20-byte hex address")
    if chunk_size < 1 or min_chunk_size < 1:
        raise ValueError("RPC log chunk sizes must be positive")

    rpc_router = router or _router(chain_id)
    client = rpc_router.standard
    head_raw = client.request("eth_blockNumber", [])
    if not isinstance(head_raw, str):
        raise RuntimeError("eth_blockNumber returned no block quantity")
    head = int(head_raw, 16)
    requested_start = max(0, int(after_block or 0))
    cacheable = requested_start == 0 and not tokens and not counterparty_ids
    cached_head = -1
    cached_rows: list[list[Any]] = []
    if cacheable:
        with _address_rpc_cache_lock:
            cached = _address_rpc_cache.get((int(chain_id), normalized))
            if cached:
                cached_head, cached_rows = cached[0], [list(row) for row in cached[1]]
    start = max(requested_start, cached_head + 1)
    if start > head:
        return cached_rows, head

    topic_address = "0x" + ("0" * 24) + normalized[2:]
    directions = (
        [TRANSFER_TOPIC0, topic_address, None],
        [TRANSFER_TOPIC0, None, topic_address],
    )

    def fetch_window(lo: int, hi: int, topics_filter: list[Any]) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                result = client.request(
                    "eth_getLogs",
                    [{
                        "fromBlock": hex(lo),
                        "toBlock": hex(hi),
                        "topics": topics_filter,
                    }],
                )
                if result is None:
                    return []
                if not isinstance(result, list):
                    raise RuntimeError("eth_getLogs returned a non-list result")
                return [item for item in result if isinstance(item, dict)]
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        width = hi - lo + 1
        message = str(last_error).lower()
        rate_limited = "rate limit" in message or "429" in message
        splittable = not rate_limited and any(
            marker in message
            for marker in (
                "timeout",
                "timed out",
                "block range",
                "window limit",
                "query returned more",
                "response size",
            )
        )
        if not splittable or width <= min_chunk_size:
            raise RuntimeError(
                f"eth_getLogs failed for blocks {lo}-{hi}: {last_error}"
            ) from last_error
        mid = lo + (hi - lo) // 2
        return [
            *fetch_window(lo, mid, topics_filter),
            *fetch_window(mid + 1, hi, topics_filter),
        ]

    jobs: list[tuple[int, int, list[Any]]] = []
    for lo in range(start, head + 1, chunk_size):
        hi = min(head, lo + chunk_size - 1)
        jobs.extend((lo, hi, topic_filter) for topic_filter in directions)

    logs: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(jobs)))) as pool:
        futures = {
            pool.submit(fetch_window, lo, hi, topic_filter): (lo, hi)
            for lo, hi, topic_filter in jobs
        }
        for future in as_completed(futures):
            logs.extend(future.result())

    allowed_tokens = {
        _normalize_node_id(value) for value in (tokens or []) if value
    }
    allowed_tokens.discard("")
    allowed_counterparties = {
        _normalize_node_id(value) for value in (counterparty_ids or []) if value
    }
    allowed_counterparties.discard("")

    # Self-transfers are returned by both topic filters. A log's chain position
    # is its identity; never count the duplicate twice.
    unique_logs: dict[tuple[str, int], dict[str, Any]] = {}
    for log in logs:
        if bool(log.get("removed")):
            continue
        topics = log.get("topics") or []
        if (
            len(topics) != 3
            or not all(isinstance(topic, str) for topic in topics)
            or str(topics[0]).lower() != TRANSFER_TOPIC0
            or not _RPC_WORD_RE.fullmatch(str(topics[1]))
            or not _RPC_WORD_RE.fullmatch(str(topics[2]))
        ):
            continue
        transaction_hash = _hex0x(str(log.get("transactionHash") or ""))
        if not re.fullmatch(r"0x[0-9a-f]{64}", transaction_hash):
            continue
        try:
            log_index = int(str(log.get("logIndex")), 16)
            block_number = int(str(log.get("blockNumber")), 16)
            transaction_index = int(str(log.get("transactionIndex")), 16)
        except (TypeError, ValueError):
            continue
        if block_number == start and after_index >= 0 and transaction_index <= after_index:
            continue
        token = _normalize_node_id(str(log.get("address") or ""))
        source = "0x" + str(topics[1])[-40:].lower()
        target = "0x" + str(topics[2])[-40:].lower()
        if allowed_tokens and token not in allowed_tokens:
            continue
        counterparty = target if source == normalized else source
        if allowed_counterparties and counterparty not in allowed_counterparties:
            continue
        unique_logs[(transaction_hash, log_index)] = {
            "transaction_hash": transaction_hash,
            "block_number": block_number,
            "transaction_index": transaction_index,
            "token": token,
        }

    transactions: dict[str, dict[str, Any]] = {}
    for log in unique_logs.values():
        transaction_hash = str(log["transaction_hash"])
        row = transactions.setdefault(
            transaction_hash,
            {
                "block_number": int(log["block_number"]),
                "transaction_index": int(log["transaction_index"]),
                "leg_count": 0,
                "tokens": set(),
            },
        )
        row["leg_count"] += 1
        row["tokens"].add(str(log["token"]))

    ordered = sorted(
        transactions.items(),
        key=lambda item: (
            int(item[1]["block_number"]),
            int(item[1]["transaction_index"]),
            item[0],
        ),
        reverse=True,
    )
    discovered_rows = [
        [
            transaction_hash,
            int(values["block_number"]),
            int(values["transaction_index"]),
            "",
            int(values["leg_count"]),
            len(values["tokens"]),
        ]
        for transaction_hash, values in ordered
    ]
    if cached_rows:
        by_hash = {str(row[0]): list(row) for row in cached_rows}
        by_hash.update({str(row[0]): list(row) for row in discovered_rows})
        discovered_rows = sorted(
            by_hash.values(),
            key=lambda row: (int(row[1] or 0), int(row[2] or 0), str(row[0])),
            reverse=True,
        )
    if cacheable:
        with _address_rpc_cache_lock:
            # NOTE: this records `head` as the covered watermark without
            # checking that the scan actually completed. A run cut short by the
            # wall-clock budget therefore caches a PARTIAL result, and the next
            # call resumes at head+1 and never revisits the gap. The cache is
            # process-local, so today the hole dies with the process; moving it
            # to the scratch DB requires switching this value to covered
            # ranges first (see the S7 cache task).
            _address_rpc_cache[(int(chain_id), normalized)] = (
                head,
                [list(row) for row in discovered_rows],
            )
    return discovered_rows, head


def _discover_address_direct_transactions_rpc(
    address: str,
    *,
    after_block: int,
    through_block: int,
    router: RpcRouter | None = None,
    chain_id: int = GNOSIS_CHAIN_ID,
    max_blocks: int = _MAX_RPC_DIRECT_TAIL_BLOCKS,
    max_workers: int = 8,
) -> list[list[Any]]:
    """Discover direct sender/recipient transactions in a small RPC head gap.

    Standard JSON-RPC has no address index. Scanning full blocks is therefore
    permitted only for the bounded gap after the common stored execution-table
    watermark; it is never a full-history fallback. Transfer-log discovery runs
    separately and the two result sets are merged by transaction hash.
    """
    normalized = _normalize_node_id(address)
    if not normalized or not _RPC_ADDRESS_RE.fullmatch(normalized):
        raise ValueError("address must be a 20-byte hex address")
    start = max(0, int(after_block or 0))
    end = max(0, int(through_block or 0))
    if start > end:
        return []
    width = end - start + 1
    if width > max(1, int(max_blocks)):
        raise RuntimeError(
            f"RPC direct-transaction tail is {width} blocks; safety cap is "
            f"{max_blocks}. Refresh the execution ingestion before retrying."
        )

    client = (router or _router(chain_id)).standard

    def quantity(value: Any, fallback: int = 0) -> int:
        if isinstance(value, int):
            return value
        try:
            return int(str(value), 16)
        except (TypeError, ValueError):
            return fallback

    def fetch(block_number: int) -> list[list[Any]]:
        block = client.request("eth_getBlockByNumber", [hex(block_number), True])
        if not isinstance(block, dict):
            raise RuntimeError(
                f"eth_getBlockByNumber returned no block for {block_number}"
            )
        timestamp_value = quantity(block.get("timestamp"))
        timestamp = (
            datetime.fromtimestamp(timestamp_value, timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if timestamp_value
            else ""
        )
        rows: list[list[Any]] = []
        for fallback_index, transaction in enumerate(block.get("transactions") or []):
            if not isinstance(transaction, dict):
                continue
            source = _normalize_node_id(str(transaction.get("from") or ""))
            target = _normalize_node_id(str(transaction.get("to") or ""))
            if normalized not in {source, target}:
                continue
            transaction_hash = _hex0x(str(transaction.get("hash") or ""))
            if not re.fullmatch(r"0x[0-9a-f]{64}", transaction_hash):
                continue
            rows.append(
                [
                    transaction_hash,
                    quantity(transaction.get("blockNumber"), block_number),
                    quantity(transaction.get("transactionIndex"), fallback_index),
                    timestamp,
                    0,
                    0,
                ]
            )
        return rows

    rows: list[list[Any]] = []
    worker_count = max(1, min(int(max_workers), width))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(fetch, block) for block in range(start, end + 1)]
        for future in as_completed(futures):
            rows.extend(future.result())
    rows.sort(
        key=lambda row: (int(row[1] or 0), int(row[2] or 0), str(row[0])),
        reverse=True,
    )
    return rows


def _merge_tx_discovery_rows(*groups: list[list[Any]]) -> list[list[Any]]:
    """Merge direct/Transfer discoveries without inventing evidence."""
    merged: dict[str, list[Any]] = {}
    for group in groups:
        for raw in group:
            row = list(raw[:6])
            if len(row) < 6:
                continue
            transaction_hash = _hex0x(str(row[0]))
            current = merged.get(transaction_hash)
            if current is None:
                row[0] = transaction_hash
                merged[transaction_hash] = row
                continue
            # Envelope position must agree. Keep the highest non-null values
            # defensively and preserve any timestamp supplied by either path.
            current[1] = max(int(current[1] or 0), int(row[1] or 0))
            current[2] = max(int(current[2] or 0), int(row[2] or 0))
            current[3] = str(current[3] or row[3] or "")
            current[4] = max(int(current[4] or 0), int(row[4] or 0))
            current[5] = max(int(current[5] or 0), int(row[5] or 0))
    return sorted(
        merged.values(),
        key=lambda row: (int(row[1] or 0), int(row[2] or 0), str(row[0])),
        reverse=True,
    )


def _append_only_discovery_delta(
    previous_rows: list[list[Any]], merged_rows: list[list[Any]]
) -> list[list[Any]] | None:
    """Return the strict older-page suffix, or ``None`` when replacement is safer.

    Keyset pagination guarantees that an older page extends the existing
    newest-first list.  If any prior row changed position/value, the response
    is not append-only and must use the ordinary full snapshot protocol.
    """
    previous = [list(row) for row in previous_rows]
    merged = [list(row) for row in merged_rows]
    if len(merged) < len(previous) or merged[: len(previous)] != previous:
        return None
    return merged[len(previous) :]


def _hydrate_rpc_discovery_timestamps(
    rows: list[list[Any]],
    *,
    router: RpcRouter | None = None,
    chain_id: int = GNOSIS_CHAIN_ID,
    max_workers: int = 8,
) -> list[list[Any]]:
    """Fill candidate timestamps with header-only RPC calls.

    Transfer logs carry a block number but not its timestamp. Explicit UTC
    discovery bounds therefore require the corresponding block headers; a
    transaction-filled block is neither necessary nor requested.
    """
    pending_blocks = sorted(
        {
            int(row[1] or 0)
            for row in rows
            if int(row[1] or 0) and not str(row[3] or "")
        }
    )
    if not pending_blocks:
        return [list(row) for row in rows]
    client = (router or _router(chain_id)).standard

    def fetch(block_number: int) -> tuple[int, str]:
        block = client.request("eth_getBlockByNumber", [hex(block_number), False])
        if not isinstance(block, dict):
            raise RuntimeError(f"missing block header {block_number}")
        timestamp = int(str(block.get("timestamp") or "0x0"), 16)
        return (
            block_number,
            datetime.fromtimestamp(timestamp, timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if timestamp
            else "",
        )

    timestamps: dict[int, str] = {}
    with ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(pending_blocks)))
    ) as pool:
        for block_number, timestamp in pool.map(fetch, pending_blocks):
            timestamps[block_number] = timestamp
    hydrated: list[list[Any]] = []
    for raw in rows:
        row = list(raw)
        if not str(row[3] or ""):
            row[3] = timestamps.get(int(row[1] or 0), "")
        hydrated.append(row)
    return hydrated


def _candidate_precedes(
    row: list[Any], cursor: tuple[int, int, str] | None
) -> bool:
    if cursor is None:
        return True
    row_key = (int(row[1] or 0), int(row[2] or 0), _hex0x(str(row[0])))
    return row_key < cursor


def _discover_address_transactions_execution(
    ch: ClickHouseManager,
    address: str,
    *,
    through: str,
    limit: int,
    max_workers: int = 4,
    since: str | None = None,
    before: tuple[int, int, str] | None = None,
    activity_kinds: list[str] | None = None,
    tokens: list[str] | None = None,
    detailed: bool = False,
    tile_seconds: int = _DISCOVERY_DEFAULT_TILE_SECONDS,
) -> tuple[list[list[Any]], int | None, bool, int] | tuple[
    list[list[Any]], int | None, bool, int, dict[str, Any]
]:
    """Page stored execution transactions and Transfer logs newest first.

    ClickHouse cannot complete one unbounded topic scan inside the service's
    30-second safety limit. Newest-first storage pages stop only after the
    requested result admission is satisfied, never at an arbitrary date. The
    return values are ``(rows, exact_total_or_none, complete, scanned_total)``.
    Empty/under-limit results are verified only after every page to genesis.
    """
    horizon = datetime.fromisoformat(str(through).replace("Z", "+00:00"))
    if horizon.tzinfo is not None:
        horizon = horizon.astimezone(timezone.utc).replace(tzinfo=None)
    end = horizon + timedelta(seconds=1)
    start = _GNOSIS_CHAIN_GENESIS_UTC
    if since:
        start = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
        if start.tzinfo is not None:
            start = start.astimezone(timezone.utc).replace(tzinfo=None)
        start = max(start, _GNOSIS_CHAIN_GENESIS_UTC)
    if end <= start:
        coverage = {
            "scanned_ranges": [],
            "uncovered_ranges": [],
            "older_history_unscanned": False,
        }
        base: tuple[list[list[Any]], int | None, bool, int] = ([], 0, True, 0)
        return (*base, coverage) if detailed else base

    effective_limit = max(1, int(limit))
    effective_tile_seconds = max(
        _DISCOVERY_MIN_TILE_SECONDS,
        min(int(tile_seconds), _DISCOVERY_DEFAULT_TILE_SECONDS),
    )
    tile_span = timedelta(seconds=effective_tile_seconds)
    deadline = time.monotonic() + _DISCOVERY_WALL_BUDGET_SECONDS

    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        # The raw tables are ordered by time/block. One day is the largest
        # measured-safe interactive tile; a coverage cursor can reduce it only
        # after an actual query timeout or memory rejection.
        chunk_end = min(cursor + tile_span, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end

    def publish_bound(value: datetime) -> str:
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    def read_chunk(
        bounds: tuple[datetime, datetime],
    ) -> tuple[
        list[list[Any]],
        int,
        list[dict[str, str]],
        list[dict[str, str]],
    ]:
        lo, hi = bounds
        if time.monotonic() >= deadline:
            return (
                [],
                0,
                [],
                [{
                    "t0": publish_bound(lo),
                    "t1": publish_bound(hi),
                    "reason": "interactive discovery wall-time budget reached",
                }],
            )
        sql, params = build_all_history_tx_discovery_chunk_sql(
            address_ids=[address],
            t0=lo.strftime("%Y-%m-%d %H:%M:%S"),
            t1_exclusive=hi.strftime("%Y-%m-%d %H:%M:%S"),
            limit=effective_limit,
            before_block=before[0] if before else None,
            before_index=before[1] if before else None,
            before_hash=before[2] if before else None,
            activity_kinds=activity_kinds,
            tokens=tokens,
        )
        try:
            result = _run(
                ch,
                sql,
                params,
                query_budget=_ADDRESS_DISCOVERY_QUERY_BUDGET,
            )
        except Exception as exc:
            duration = hi - lo
            if _is_subdividable_discovery_error(exc) and duration > _DISCOVERY_MIN_SLICE:
                midpoint = lo + duration / 2
                # Newest half first. This preserves one contiguous scanned
                # suffix even when the wall clock expires during subdivision.
                right_rows, right_total, right_scanned, right_uncovered = read_chunk(
                    (midpoint, hi)
                )
                if right_uncovered:
                    return (
                        right_rows[: effective_limit + 1],
                        right_total,
                        right_scanned,
                        [
                            {
                                "t0": publish_bound(lo),
                                "t1": publish_bound(midpoint),
                                "reason": (
                                    "not scanned below an unresolved newer tile: "
                                    f"{right_uncovered[0]['reason']}"
                                ),
                            },
                            *right_uncovered,
                        ],
                    )
                left_rows, left_total, left_scanned, left_uncovered = read_chunk(
                    (lo, midpoint)
                )
                merged = [*left_rows, *right_rows]
                merged.sort(
                    key=lambda row: (
                        int(row[1] or 0),
                        int(row[2] or 0),
                        str(row[0]),
                    ),
                    reverse=True,
                )
                return (
                    merged[: effective_limit + 1],
                    left_total + right_total,
                    [*left_scanned, *right_scanned],
                    [*left_uncovered, *right_uncovered],
                )
            if detailed and _is_subdividable_discovery_error(exc):
                return (
                    [],
                    0,
                    [],
                    [
                        {
                            "t0": publish_bound(lo),
                            "t1": publish_bound(hi),
                            "reason": str(exc),
                        }
                    ],
                )
            raise
        rows = [list(row) for row in result.rows]
        total = int(rows[0][6] or 0) if rows else 0
        return (
            [row[:6] for row in rows],
            total,
            [{"t0": publish_bound(lo), "t1": publish_bound(hi)}],
            [],
        )

    chunks.reverse()  # newest first; result admission is count-based, not time-based
    candidates: list[list[Any]] = []
    scanned_total = 0
    complete = True
    scanned_ranges: list[dict[str, str]] = []
    uncovered_ranges: list[dict[str, str]] = []
    older_history_unscanned = False
    # Queries may execute in a small bounded batch, but their results are
    # admitted strictly newest-first. If a newer tile is unresolved, any older
    # result from the same batch is discarded and represented as uncovered;
    # this keeps the published coverage monotonic without paying serial query
    # latency for independent one-day tiles.
    worker_count = max(1, min(int(max_workers), 4, len(chunks)))
    offset = 0
    while offset < len(chunks):
        if time.monotonic() >= deadline:
            remaining = chunks[offset:]
            if remaining:
                uncovered_ranges.append({
                    "t0": publish_bound(min(bounds[0] for bounds in remaining)),
                    "t1": publish_bound(max(bounds[1] for bounds in remaining)),
                    "reason": "interactive discovery wall-time budget reached",
                })
            complete = False
            older_history_unscanned = bool(remaining)
            break
        batch = chunks[offset : offset + worker_count]
        if len(batch) == 1:
            batch_results = [read_chunk(batch[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futures = [pool.submit(read_chunk, chunk) for chunk in batch]
                # Preserve chunk order rather than completion order.
                batch_results = [future.result() for future in futures]

        stop = False
        for batch_index, (chunk, result) in enumerate(zip(batch, batch_results)):
            rows, chunk_total, scanned, uncovered = result
            candidates.extend(rows)
            scanned_total += chunk_total
            scanned_ranges.extend(scanned)
            uncovered_ranges.extend(uncovered)
            absolute_index = offset + batch_index
            if uncovered:
                # Never publish coverage below the newest unresolved tile.
                remaining = chunks[absolute_index + 1 :]
                if remaining:
                    uncovered_ranges.append({
                        "t0": publish_bound(min(bounds[0] for bounds in remaining)),
                        "t1": publish_bound(max(bounds[1] for bounds in remaining)),
                        "reason": (
                            "not admitted below an unresolved newer tile: "
                            f"{uncovered[0]['reason']}"
                        ),
                    })
                complete = False
                older_history_unscanned = True
                stop = True
                break
            if scanned_total > effective_limit:
                complete = False
                older_history_unscanned = absolute_index + 1 < len(chunks)
                stop = True
                break
        if stop:
            break
        offset += len(batch)
        if time.monotonic() >= deadline and offset < len(chunks):
            remaining = chunks[offset:]
            uncovered_ranges.append({
                "t0": publish_bound(min(bounds[0] for bounds in remaining)),
                "t1": publish_bound(max(bounds[1] for bounds in remaining)),
                "reason": "interactive discovery wall-time budget reached",
            })
            complete = False
            older_history_unscanned = True
            break

    if uncovered_ranges:
        complete = False

    candidates = sorted(
        {str(row[0]): list(row) for row in candidates}.values(),
        key=lambda row: (int(row[1] or 0), int(row[2] or 0), str(row[0])),
        reverse=True,
    )[: effective_limit + 1]
    base_result: tuple[list[list[Any]], int | None, bool, int] = (
        candidates,
        scanned_total if complete else None,
        complete,
        scanned_total,
    )
    if not detailed:
        return base_result
    return (
        *base_result,
        {
            "scanned_ranges": _coalesce_scanned_ranges(scanned_ranges),
            "uncovered_ranges": sorted(
                uncovered_ranges, key=lambda item: (item["t0"], item["t1"])
            ),
            "older_history_unscanned": older_history_unscanned,
            "tile_seconds": effective_tile_seconds,
        },
    )


def _hex0x(value: str) -> str:
    v = str(value or "").strip().lower()
    return v if v.startswith("0x") else f"0x{v}"


def _erc20_decimals_onchain(
    tokens: list[str], *, chain_id: int
) -> dict[str, int | None]:
    """``decimals()`` read straight from each token contract.

    Used only OFF Gnosis, where the warehouse has no metadata. Returns None for
    any token that does not answer, so the leg keeps its authoritative raw
    amount and reports an unknown normalized amount rather than guessing 18.
    """
    out: dict[str, int | None] = {}
    if not tokens:
        return out
    client = _router(chain_id).standard
    for token in tokens:
        try:
            raw = client.request(
                "eth_call",
                [{"to": token, "data": "0x313ce567"}, "latest"],  # decimals()
            )
            value = int(str(raw), 16) if isinstance(raw, str) and raw not in ("", "0x") else None
            # A sane ERC-20 reports 0-36; anything else is a non-standard or
            # hostile contract and must not silently rescale an amount.
            out[token] = value if value is not None and 0 <= value <= 36 else None
        except Exception as exc:  # pragma: no cover - enrichment is optional
            logger.info("tx mode: on-chain decimals failed for %s: %s", token, _redact_endpoint(exc))
            out[token] = None
    return out


def _enrich_rpc_legs(
    ch: ClickHouseManager, rows: list[list[Any]], *, chain_id: int = GNOSIS_CHAIN_ID
) -> tuple[
    list[list[Any]],
    dict[str, str],
    list[str],
    dict[str, dict[str, Any]],
]:
    """Attach symbol / decimals / USD to RPC-sourced legs.

    One lookup over the tokens actually present, not a per-row join. A token
    missing from the metadata (or carrying null decimals) keeps its authoritative
    raw amount while its normalized amount and USD value remain null. The leg is
    never dropped, because enrichment availability is not chain truth.

    The price relation is physically keyed by ``(symbol, date)`` with no
    token-address column, but that is not the ambiguity it appears to be. Every
    symbol looked up here is sourced from ``stg_pools__tokens_meta`` (the
    ``tokens_whitelist`` seed), which maps every token_address to a UNIQUE symbol
    (verified: zero addresses carry >1 symbol; the only symbols shared across
    addresses are the EURe/GBPe Monerium migrations -- same asset, same peg, with
    adjacent validity windows meeting at the 2024-08-25 cutover, so both
    contracts resolve to the identical peg price regardless). The address ->
    symbol -> price chain is therefore a per-date bijection and is
    address-accurate. A non-whitelisted contract is absent from the seed, never
    receives a symbol, and so stays unpriced rather than borrowing another
    token's price; any orphan symbol the price hub carries but no whitelisted
    address maps to (e.g. XAUT0) is simply never consumed. The only honest caveat
    is a genuine price GAP: a whitelisted, decimals-known token (whose USD would
    otherwise be computable) with no price row for its trade date -- surfaced as
    a partial enrichment naming the affected pairs.
    """
    tokens = sorted({str(r[7]) for r in rows if r[7]})
    meta: dict[str, tuple[str, int | None]] = {}
    price: dict[tuple[str, str], float] = {}
    statuses = {"metadata": "not_needed", "prices": "not_needed"}
    source_details: dict[str, dict[str, Any]] = {
        "metadata": {
            "horizon": None,
            "horizon_basis": None,
            "fetched_at": None,
            "error": None,
        },
        "prices": {
            "horizon": None,
            "horizon_basis": None,
            "fetched_at": None,
            "error": None,
        },
    }
    warnings: list[str] = []
    if tokens and int(chain_id) != GNOSIS_CHAIN_ID:
        # OFF GNOSIS: the warehouse relations below describe Gnosis and only
        # Gnosis. The address -> symbol -> price bijection argued for in this
        # docstring holds *because* every symbol comes from the Gnosis
        # tokens_whitelist seed; it says nothing about another chain. The same
        # address exists on every EVM chain, so joining a Base token against
        # stg_pools__tokens_meta could hand it a Gnosis token's decimals and a
        # Gnosis token's price — a silently wrong amount under a confident
        # verification badge.
        #
        # So: no warehouse join at all. Decimals come from the contract, USD
        # stays unknown, and no symbol is emitted — a symbol read from an
        # arbitrary contract is attacker-controlled, and this module's UI
        # renders symbol-first, so a spoof "USDC" would read as USDC in a
        # fraud investigation. The address is shown instead.
        onchain = _erc20_decimals_onchain(tokens, chain_id=chain_id)
        for token, decimals in onchain.items():
            meta[token] = ("", decimals)
        chain_name = get_chain(int(chain_id)).name
        unknown = sorted(t for t, d in onchain.items() if d is None)
        statuses["metadata"] = "partial" if unknown else "onchain_unverified"
        statuses["prices"] = "not_applicable"
        source_details["metadata"]["horizon_basis"] = "erc20_decimals_call"
        source_details["prices"]["error"] = (
            f"no USD price plane exists for {chain_name}; USD is unknown, not zero"
        )
        warnings.append(
            f"{chain_name}: token symbols are not resolved and USD is unavailable "
            "(the price plane covers Gnosis only). Amounts are normalized from the "
            "contract's own decimals(); raw amounts are authoritative."
        )
        if unknown:
            warnings.append(
                f"decimals() unavailable for {len(unknown)} token(s); their "
                "normalized amounts remain unknown and only raw amounts are shown"
            )
    elif tokens:
        metadata_contract = validate_source_contract(
            ch,
            TOKENS_META_RELATION,
            ("token_address", "token", "decimals"),
            probe_horizon=True,
        )
        prices_contract = validate_source_contract(
            ch,
            PRICES_RELATION,
            ("symbol", "date", "price"),
            probe_horizon=True,
            horizon_column="date",
        )
        source_details["metadata"].update(
            {
                "horizon": metadata_contract.get("horizon"),
                "horizon_basis": metadata_contract.get("horizon_basis"),
                "fetched_at": metadata_contract.get("freshness_checked_at"),
                "error": metadata_contract.get("error"),
            }
        )
        source_details["prices"].update(
            {
                "horizon": prices_contract.get("horizon"),
                "horizon_basis": prices_contract.get("horizon_basis"),
                "fetched_at": prices_contract.get("freshness_checked_at"),
                "error": prices_contract.get("error"),
            }
        )
        if not metadata_contract["ok"]:
            statuses["metadata"] = "error"
            warnings.append(
                "token metadata enrichment failed source contract: "
                + str(metadata_contract.get("error") or "unavailable")
            )
        else:
            try:
                res = _run(
                    ch,
                    """
                    SELECT token_address, coalesce(token, '') AS sym,
                           decimals AS dec
                    FROM dbt.stg_pools__tokens_meta
                    WHERE token_address IN {toks:Array(String)}
                    """,
                    {"toks": tokens},
                )
                for row in res.rows:
                    meta[str(row[0])] = (
                        str(row[1]),
                        None if row[2] is None else int(row[2]),
                    )
                missing_metadata = sorted(set(tokens) - set(meta))
                missing_decimals = sorted(
                    token for token, (_symbol, decimals) in meta.items()
                    if decimals is None
                )
                missing_symbols = sorted(
                    token for token, (symbol, _decimals) in meta.items()
                    if not symbol
                )
                if missing_metadata or missing_decimals or missing_symbols:
                    statuses["metadata"] = "partial"
                    problems: list[str] = []
                    if missing_metadata:
                        problems.append(
                            f"metadata row missing for {len(missing_metadata)} token(s)"
                        )
                    if missing_decimals:
                        problems.append(
                            f"decimals missing for {len(missing_decimals)} token(s)"
                        )
                    if missing_symbols:
                        problems.append(
                            f"symbol missing for {len(missing_symbols)} token(s)"
                        )
                    message = (
                        "; ".join(problems)
                        + "; affected normalized amounts and/or USD values remain unknown"
                    )
                    source_details["metadata"]["error"] = message
                    warnings.append(message)
                else:
                    statuses["metadata"] = "ok"
            except Exception as exc:  # pragma: no cover - enrichment is optional
                logger.info("tx mode: token meta lookup failed: %s", exc)
                statuses["metadata"] = "error"
                source_details["metadata"]["error"] = str(exc)
                warnings.append(f"token metadata enrichment failed: {exc}")
        symbols = sorted({m[0] for m in meta.values() if m[0]})
        dates = sorted({str(r[4])[:10] for r in rows if r[4]})
        if not prices_contract["ok"]:
            statuses["prices"] = "error"
            warnings.append(
                "price enrichment failed source contract: "
                + str(prices_contract.get("error") or "unavailable")
            )
        elif symbols and dates:
            try:
                res = _run(
                    ch,
                    """
                    SELECT symbol, toString(date) AS price_date, price
                    FROM dbt.int_execution_token_prices_daily
                    WHERE symbol IN {syms:Array(String)}
                      AND date IN {dates:Array(Date)}
                    """,
                    {"syms": symbols, "dates": dates},
                )
                for row in res.rows:
                    if row[2] is not None:
                        price[(str(row[0]), str(row[1])[:10])] = float(row[2])
                # int_execution_token_prices_daily is keyed by (symbol, date),
                # but that is NOT the same-symbol hazard it looks like: its whole
                # universe is the tokens_whitelist seed, which maps every
                # token_address to a unique symbol, and the metadata symbols used
                # here come from that same seed (stg_pools__tokens_meta). So the
                # address -> symbol -> price chain is a per-date bijection and is
                # address-accurate; a non-whitelisted contract never receives a
                # symbol and stays unpriced rather than borrowing a price. The
                # only honest caveat is a genuine GAP -- a whitelisted (symbol,
                # date) with no price row -- so surface exactly that, and only
                # that. `needed` mirrors the leg loop's price_key = (sym, date),
                # and gates on decimals too: USD is only computable when decimals
                # are known, so a null-decimals leg is a metadata gap, not a
                # price gap, and must not double-report here.
                needed = {
                    (meta[str(r[7])][0], str(r[4])[:10])
                    for r in rows
                    if str(r[7]) in meta
                    and meta[str(r[7])][0]
                    and meta[str(r[7])][1] is not None
                    and r[4]
                }
                missing = sorted(needed - set(price))
                if missing:
                    statuses["prices"] = "partial"
                    message = (
                        f"no daily price for {len(missing)} whitelisted "
                        "(token, date) pair(s); those legs' USD is left unknown"
                    )
                    source_details["prices"]["error"] = message
                    warnings.append(message)
                else:
                    statuses["prices"] = "ok"
            except Exception as exc:  # pragma: no cover
                logger.info("tx mode: price lookup failed: %s", exc)
                statuses["prices"] = "error"
                source_details["prices"]["error"] = str(exc)
                warnings.append(f"price enrichment failed: {exc}")
    out: list[list[Any]] = []
    for r in rows:
        metadata = meta.get(str(r[7]))
        sym, dec = metadata if metadata is not None else ("", None)
        amount = None if dec is None else float(r[9]) / (10 ** dec)
        price_key = (sym, str(r[4])[:10])
        usd = (
            round(amount * price[price_key], 6)
            if amount is not None and price_key in price
            else None
        )
        out.append([
            r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
            sym,
            amount,
            usd,
            r[11] if len(r) > 11 else "unknown",
            str(r[9]),
        ])
    return out, statuses, warnings, source_details


def _authoritative_leg_total(
    *, receipt_complete: bool, receipt_count: int, sql_count: int | None
) -> int | None:
    """Receipt enumeration wins even when SQL reports a smaller nonzero count."""
    if receipt_complete:
        return int(receipt_count)
    return None if sql_count is None else int(sql_count)


def _run(
    ch: ClickHouseManager,
    sql: str,
    params: dict[str, Any],
    *,
    query_budget: QueryBudget | None = INTERACTIVE_QUERY_BUDGET,
):
    return mini_apps.run_structured_query(
        ch,
        sql,
        database="dbt",
        parameters=params,
        requested_max_rows=100_000,
        query_budget=query_budget,
    )


def _role_of(address: str, *, is_token: bool, seeds: set[str]) -> str:
    """Coarse participant role — drives node colour and reading order.

    ``token`` is not cosmetic: a leg whose endpoint is an ERC-20 contract is a
    mint, burn or reserve payout, NOT a payment to a counterparty. Rendering
    the two identically is what made an earlier investigation wrong.
    """
    if address in BURN_ADDRESSES:
        return "burn"
    if is_token:
        return "token"
    if address in seeds:
        return "seed"
    return "address"


def _failed_transaction_result(
    *,
    view_id: str,
    record,
    tx_state: dict[str, Any],
    scope: dict[str, Any],
    warnings: list[str],
    summary: str,
    request_channel: str = "transactions.discovery",
) -> CallToolResult:
    """Record a failed *attempt* without destroying accepted evidence.

    A discovery request is draft state until its scope is accepted.  Clearing
    the three transaction datasets here used to turn a timeout into an empty
    canvas/table and erase the receipt the analyst was reading.  Preserve the
    last applied namespace and dataset revisions; publish the failed scope as
    ``last_attempt`` instead.  When no scope has ever been accepted, retain the
    initial empty datasets and expose the failed scope as the current scope so
    the first-load error remains inspectable.
    """
    previous = dict(record.view_state.get("transactions") or {})
    previous_scope = previous.get("scope")
    has_applied_scope = bool(
        isinstance(previous_scope, dict) and previous_scope.get("scope_id")
    )
    attempt = {
        "request_id": int(scope.get("request_id") or 0),
        "status": "failed",
        "elapsed_ms": None,
        "error_code": "source_or_query_failure",
        "message": summary,
        # Legacy alias retained for clients written before the structured
        # last-attempt contract.
        "error": summary,
        "retryable": True,
        "query_kind": scope.get("query_kind"),
        "requested": dict(tx_state),
        "scope": scope,
    }
    next_tx = (
        {**previous, "last_attempt": attempt}
        if has_applied_scope
        else {
            **tx_state,
            "tx_count": 0,
            "leg_count": 0,
            "scope": scope,
            "last_attempt": attempt,
        }
    )
    patch: dict[str, Any] = {
        "transactions": next_tx,
        "warnings": warnings,
    }
    if not has_applied_scope:
        # The datasets are already empty on a fresh view. Attribute that
        # absence to the failed scope without bumping their revisions or
        # manufacturing a successful empty response.
        patch["dataset_scopes"] = {
            "tx_nodes": scope["scope_id"],
            "tx_legs": scope["scope_id"],
            "tx_list": scope["scope_id"],
        }
    committed = mini_apps.commit_view_update(
        view_id,
        request_channel=request_channel,
        request_id=int(scope.get("request_id") or 0),
        guard_channels=("transactions",),
        state_patch=patch,
    )
    updated = mini_apps.snapshot_view(view_id)
    assert updated is not None
    if not committed:
        summary = "Transaction request was superseded by a newer request."
    return mini_apps.payload_to_call_tool_result(
        build_payload(updated), summary_text=summary
    )


def register_transaction_tools(mcp, ch: ClickHouseManager) -> dict[str, Any]:
    @mcp.tool()
    def load_graph_transactions(
        view_id: str,
        tx_hashes: list[str] | None = None,
        seed_node_id: str = "",
        counterparty_ids: list[str] | None = None,
        range_days: int = 0,
        t0: str = "",
        t1: str = "",
        max_txs: int = 0,
        tokens: list[str] | None = None,
        min_usd: float = 0.0,
        expand_node_id: str = "",
        after_block: int = 0,
        after_index: int = -1,
        merge: bool = False,
        request_id: int = 0,
        operation: str = "legacy",
        cursor: str = "",
        page_size: int = 0,
        activity_kinds: list[str] | None = None,
        chain: str = "",
    ) -> CallToolResult:
        """Open transactions and return every transfer leg (Transactions mode).

        ``operation=discover`` returns only the newest candidate page for an
        address. ``operation=receipt`` opens exactly one candidate while
        preserving the accepted discovery list. The default ``legacy`` mode
        retains the historical all-in-one contract for existing callers.

        ``expand_node_id`` + ``after_block``/``after_index`` follows ONE address
        forward in chain order — the next transactions it took part in after a
        cursor — and ``merge`` unions them with what is already loaded. That is
        the "what did it do next?" step: the chain of custody continues instead
        of the view restarting.

        ``chain`` selects the EVM chain (id, env key or name; default Gnosis).
        Receipts are RPC-sourced and therefore portable to any configured
        chain. Address DISCOVERY is not: it reads the Gnosis ``execution.*``
        warehouse, which has no equivalent elsewhere, so off Gnosis this tool
        answers explicit transaction hashes and refuses address discovery
        rather than silently returning an empty, "verified" result.
        """
        record = mini_apps.snapshot_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        try:
            chain_info = resolve_chain(chain or record.view_state.get(
                "transactions", {}
            ).get("chain_id") or GNOSIS_CHAIN_ID)
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))
        chain_id = chain_info.chain_id
        if not has_rpc(chain_id):
            # Never echo the endpoint itself — only the env var to set.
            return mini_apps.error_call_tool_result(
                f"No RPC endpoint configured for {chain_info.name} "
                f"(chain {chain_id}). Set {rpc_env_hint(chain_id)}."
            )
        is_gnosis = chain_id == GNOSIS_CHAIN_ID

        state_tx = dict(record.view_state.get("transactions") or {})
        operation = str(operation or "legacy").strip().lower()
        if operation not in {"legacy", "discover", "receipt"}:
            return mini_apps.error_call_tool_result(
                "operation must be one of: legacy, discover, receipt"
            )
        candidate_only = operation == "discover"
        receipt_only = operation == "receipt"
        normalized_activity_kinds = sorted(
            {
                str(kind).strip().lower()
                for kind in (activity_kinds or ["direct", "erc20"])
                if str(kind).strip()
            }
        )
        if not normalized_activity_kinds or any(
            kind not in {"direct", "erc20"} for kind in normalized_activity_kinds
        ):
            return mini_apps.error_call_tool_result(
                "activity_kinds must contain direct and/or erc20"
            )
        effective_activity_kinds = (
            {"erc20"} if tokens else set(normalized_activity_kinds)
        )
        try:
            discovery_before = _decode_discovery_cursor(cursor) if cursor else None
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))
        row_discovery_before = (
            discovery_before
            if isinstance(discovery_before, tuple)
            else None
        )
        coverage_discovery_before = (
            discovery_before
            if isinstance(discovery_before, _CoverageDiscoveryCursor)
            else None
        )

        preserved_tx_list = record.datasets.get("tx_list") if receipt_only else None
        preserved_discovery = {
            key: state_tx.get(key)
            for key in (
                "query_kind",
                "query_hashes",
                "result_hashes",
                "query",
                "seed",
                "counterparties",
                "range_days",
                "t0",
                "t1",
                "max_txs",
                "tokens",
                "min_usd",
                "discovery_scope",
                "discovery_coverage",
            )
            if key in state_tx
        }
        requested_t0 = str(t0 or "").strip()
        requested_t1 = str(t1 or "").strip()
        hashes = [_hex0x(h) for h in (tx_hashes or []) if str(h).strip()]
        explicit_hash_request = bool(hashes)
        if receipt_only and len(hashes) != 1:
            return mini_apps.error_call_tool_result(
                "operation=receipt requires exactly one transaction hash"
            )
        if candidate_only and (hashes or not seed_node_id or expand_node_id):
            return mini_apps.error_call_tool_result(
                "operation=discover requires seed_node_id and does not accept "
                "tx_hashes or expand_node_id"
            )
        request_id = max(0, int(request_id or 0))
        seed = _normalize_node_id(seed_node_id) if seed_node_id else ""
        expand = _normalize_node_id(expand_node_id) if expand_node_id else ""
        cps = [_normalize_node_id(c) for c in (counterparty_ids or []) if c]
        cps = [c for c in cps if c]

        # Forward traversal walks the EXPANDED address, not the original seed,
        # and inherits the view's filters so a follow-on step can never widen
        # the population behind the analyst's back.
        if expand:
            seed = expand
            explicit_hash_request = False
            if not range_days:
                range_days = int(state_tx.get("range_days") or 0)
            if not max_txs:
                max_txs = int(state_tx.get("max_txs") or 0)
            hashes = []

        # Preserve the analyst's query independently from the admitted/result
        # hashes. Result caps and discovery must never rewrite URL/query state.
        query_hashes = list(hashes) if explicit_hash_request else []

        # Exact UTC bounds belong to a Money Trail edge drill-down. Plain
        # address activity is deliberately history-wide hybrid discovery;
        # legacy txrange/t0/t1 URL fields do not narrow it.
        plain_address_request = bool(
            not explicit_hash_request
            and seed
            and not expand
            and not cps
            and (candidate_only or not tokens)
        )
        rpc_address_request = bool(not explicit_hash_request and seed and expand)
        explicit_discovery_window = bool(
            candidate_only and requested_t0 and requested_t1
        )
        all_history_address_request = bool(
            (plain_address_request and not explicit_discovery_window)
            or rpc_address_request
        )
        if candidate_only and bool(requested_t0) != bool(requested_t1):
            return mini_apps.error_call_tool_result(
                "Address discovery requires both t0 and t1, or neither."
            )
        # Off Gnosis there is no `execution.*` warehouse to discover against.
        # A bounded eth_getLogs scan is the alternative, but a bounded scan that
        # finds nothing currently reaches legs_total = 0 and a "verified"
        # verification status — i.e. it would report a scanned sliver as a
        # verified absence. Refuse explicitly instead, and say what DOES work.
        if not is_gnosis and (plain_address_request or rpc_address_request):
            return mini_apps.error_call_tool_result(
                f"Address discovery is not available on {chain_info.name} "
                f"(chain {chain_id}): it relies on the indexed Gnosis "
                "execution tables, which have no equivalent here. Open an "
                "explicit transaction hash instead — receipts are read from "
                "this chain's RPC and are fully supported."
            )
        if (
            not explicit_hash_request
            and not expand
            and (cps or tokens)
            and bool(requested_t0) != bool(requested_t1)
        ):
            return mini_apps.error_call_tool_result(
                "Transaction discovery requires both t0 and t1, or neither."
            )

        if not hashes and not seed:
            return mini_apps.error_call_tool_result(
                "Provide tx_hashes (open specific transactions), seed_node_id "
                "(discover an address's transaction history), or expand_node_id "
                "(follow an address forward from a cursor)"
            )

        days = 0 if all_history_address_request or candidate_only else (
            int(range_days) if range_days else constants.TX_DEFAULT_RANGE_DAYS
        )
        if not all_history_address_request:
            days = max(1, days)
        if candidate_only:
            requested_page_size = int(page_size or max_txs or constants.TX_DEFAULT_MAX_TXS)
            limit_txs = max(1, min(requested_page_size, 100))
        else:
            limit_txs = int(max_txs) if max_txs else constants.TX_DEFAULT_MAX_TXS
        limit_txs = max(1, min(limit_txs, constants.TX_MAX_TXS))

        transaction_channel = (
            "transactions.receipt"
            if receipt_only or explicit_hash_request
            else "transactions.discovery"
        )
        effective_request_id = mini_apps.reserve_view_request(
            view_id,
            request_channel="transactions",
            request_id=request_id,
        )
        if effective_request_id is None:
            current = mini_apps.snapshot_view(view_id)
            assert current is not None
            return mini_apps.payload_to_call_tool_result(
                build_payload(current),
                summary_text="Transaction request was superseded by a newer request.",
            )
        if mini_apps.reserve_view_request(
            view_id,
            request_channel=transaction_channel,
            request_id=effective_request_id,
        ) is None:
            current = mini_apps.snapshot_view(view_id)
            assert current is not None
            return mini_apps.payload_to_call_tool_result(
                build_payload(current),
                summary_text="Transaction request was superseded by a newer request.",
            )
        request_id = effective_request_id
        scope_id = new_scope_id("transactions", request_id)

        warnings: list[str] = []
        sources: list[dict[str, Any]] = []

        # ---- Data horizons -------------------------------------------------
        # The historical and live tails have different clocks. Keep both on
        # their source records; their union's newest watermark is only the
        # combined discovery bound, never a replacement for either source.
        horizon: str | None = None
        chain_horizons: dict[str, str | None] = {}
        chain_block_horizons: dict[str, int | None] = {}
        if not explicit_hash_request and not rpc_address_request:
            try:
                hsql, hparams = build_data_horizon_sql()
                hres = _run(
                    ch,
                    hsql,
                    hparams,
                    query_budget=(
                        _DISCOVERY_HORIZON_QUERY_BUDGET
                        if candidate_only
                        else INTERACTIVE_QUERY_BUDGET
                    ),
                )
                for row in hres.rows:
                    if len(row) >= 2:
                        relation = str(row[0])
                        chain_horizons[relation] = (
                            str(row[1]) if row[1] is not None else None
                        )
                        chain_block_horizons[relation] = (
                            int(row[2])
                            if len(row) >= 3 and row[2] is not None
                            else None
                        )
                    elif row and row[0]:  # compatibility with older fixtures
                        horizon = str(row[0])
                log_horizons = [
                    chain_horizons.get(relation)
                    for relation in CHAIN_LOG_RELATIONS
                    if chain_horizons.get(relation)
                ]
                transaction_horizons = [
                    chain_horizons.get(relation)
                    for relation in CHAIN_TRANSACTION_RELATIONS
                    if chain_horizons.get(relation)
                ]
                if plain_address_request:
                    # A complete address page includes both normal transaction
                    # envelopes and Transfer-event participation. Stop stored
                    # history at the slower source-family horizon, then cover
                    # the common uncovered head through RPC.
                    if candidate_only and effective_activity_kinds == {"erc20"}:
                        horizon = max(log_horizons) if log_horizons else None
                    elif candidate_only and effective_activity_kinds == {"direct"}:
                        horizon = (
                            max(transaction_horizons)
                            if transaction_horizons
                            else None
                        )
                    elif log_horizons and transaction_horizons:
                        horizon = min(
                            max(log_horizons),
                            max(transaction_horizons),
                        )
                elif log_horizons:
                    horizon = max(log_horizons)
            except Exception as exc:  # pragma: no cover - defensive
                logger.info("tx mode: horizon lookup failed: %s", exc)
                warnings.append(f"chain data-horizon lookup failed: {exc}")

        exact_window_request = bool(
            requested_t0
            and requested_t1
            and not explicit_hash_request
            and not expand
            and (candidate_only or cps or tokens)
        )

        def parse_window(value: str) -> datetime:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed

        if exact_window_request:
            try:
                t0 = parse_window(requested_t0)
                t1 = parse_window(requested_t1)
            except ValueError:
                return mini_apps.error_call_tool_result(
                    "t0 and t1 must be ISO-8601 datetimes."
                )
            if t1 <= t0:
                return mini_apps.error_call_tool_result("t1 must be later than t0.")
        elif all_history_address_request:
            # Sentinel datetimes keep the legacy internal SQL/fallback code
            # typed. They are never published as the analyst predicate and do
            # not constrain RPC log discovery.
            t0 = datetime(1970, 1, 1)
            t1 = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            t1 = datetime.now(timezone.utc).replace(tzinfo=None)
            t0 = t1 - timedelta(days=days)
        applied_window_source = (
            "ignored_for_explicit_hash"
            if explicit_hash_request
            else (
                (
                    "custom_utc_window"
                    if explicit_discovery_window
                    else "execution_tables_plus_rpc_head"
                    if plain_address_request
                    else "rpc_cursor_to_head"
                )
                if all_history_address_request
                else (
                "money_trail_applied_window"
                if exact_window_request and (cps or tokens)
                else (
                    "custom_utc_window"
                    if exact_window_request
                    else f"range_days={days}"
                )
                )
            )
        )
        query_kind = (
            "explicit_hash"
            if explicit_hash_request
            else (
                "address_discovery"
                if candidate_only
                else (
                "follow"
                if expand
                else (
                    "money_edge"
                    if cps or tokens
                    else "address_discovery"
                )
                )
            )
        )

        def query_contract() -> dict[str, Any]:
            public_kind = {
                "explicit_hash": "hash",
                "address_discovery": "address",
                "money_edge": "money_edge",
                "follow": "follow",
            }[query_kind]
            return {
                "kind": public_kind,
                "hashes": list(query_hashes),
                "address": None if explicit_hash_request else (seed or None),
                "counterparties": list(cps),
                "tokens": list(tokens or []),
                "activity_kinds": list(normalized_activity_kinds),
                "cursor": str(cursor or "") or None,
                "page_size": limit_txs,
                "window": (
                    None
                    if explicit_hash_request or all_history_address_request
                    else {
                        "t0": t0.strftime("%Y-%m-%d %H:%M:%S"),
                        "t1": t1.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": applied_window_source,
                    }
                ),
            }

        def cursor_query_fingerprint(query: dict[str, Any]) -> tuple[Any, ...]:
            window = query.get("window") or {}
            return (
                str(query.get("kind") or ""),
                _normalize_node_id(str(query.get("address") or "")),
                tuple(sorted(str(value) for value in query.get("counterparties") or [])),
                tuple(sorted(str(value) for value in query.get("tokens") or [])),
                tuple(sorted(str(value) for value in query.get("activity_kinds") or [])),
                str(window.get("t0") or ""),
                str(window.get("t1") or ""),
                str(window.get("source") or ""),
            )

        cursor_matches_query = not bool(discovery_before)
        if candidate_only and discovery_before:
            previous_query = state_tx.get("query") or {}
            cursor_matches_query = cursor_query_fingerprint(
                previous_query
            ) == cursor_query_fingerprint(query_contract())
            if not cursor_matches_query:
                return mini_apps.error_call_tool_result(
                    "cursor does not belong to the current address discovery query"
                )

        def failed_load(
            message: str, sources: list[dict[str, Any]]
        ) -> CallToolResult:
            failed_warnings = [*warnings, message]
            scope_t0 = None if explicit_hash_request or all_history_address_request else t0.strftime("%Y-%m-%d %H:%M:%S")
            scope_t1 = None if explicit_hash_request or all_history_address_request else t1.strftime("%Y-%m-%d %H:%M:%S")
            scope = forensic_scope(
                chain_id=chain_id,
                scope_id=scope_id,
                request_id=request_id,
                status="failed",
                t0=scope_t0,
                t1=scope_t1,
                window_source=applied_window_source,
                data_horizon=horizon,
                sources=sources,
                rows_returned=0,
                rows_total=None,
                nodes_returned=0,
                nodes_total=None,
                edges_returned=0,
                edges_total=None,
                residuals=(
                    f"native {chain_info.native_symbol} value is not represented by "
                    "ERC-20 Transfer logs",
                    "internal-call value requires trace_transaction",
                    "missing metadata or prices do not remove ERC-20 legs",
                ),
                warnings=failed_warnings,
                verification_status="failed",
                verification_method="source contract / query execution",
                query_kind=query_kind,
                evidence_class=(
                    "rpc_receipt"
                    if query_kind == "explicit_hash"
                    else "address_discovery"
                ),
                subjects=(hashes if query_kind == "explicit_hash" else [seed]),
                result_row_hash=canonical_row_hash([]),
            )
            scope.update(
                {
                    "query_kind": query_kind,
                    "evidence_class": (
                        "rpc_receipt"
                        if query_kind == "explicit_hash"
                        else "address_discovery"
                    ),
                    "t0": scope["window"]["t0"],
                    "t1": scope["window"]["t1"],
                    "window_source": scope["window"]["source"],
                    "txs_requested": 0,
                    "txs_total_matching": None,
                    "legs_returned": 0,
                    "legs_total": None,
                    "truncated": False,
                    "ordered": True,
                    "exact": False,
                }
            )
            return _failed_transaction_result(
                view_id=view_id,
                record=record,
                tx_state={
                    "tx_hashes": list(hashes),
                    "seed": seed,
                    "expanded": list(state_tx.get("expanded") or []),
                    "counterparties": cps,
                    "range_days": 0 if all_history_address_request else days,
                    "t0": "" if all_history_address_request else t0.strftime("%Y-%m-%d %H:%M:%S"),
                    "t1": "" if all_history_address_request else t1.strftime("%Y-%m-%d %H:%M:%S"),
                    "max_txs": limit_txs,
                    "tokens": tokens or [],
                    "min_usd": float(min_usd or 0),
                    "query_kind": query_kind,
                    "query": query_contract(),
                    "results": {"hashes": [], "selected_hash": None},
                },
                scope=scope,
                warnings=failed_warnings,
                summary=f"Transaction load failed safely: {message}",
                request_channel=(
                    "transactions.receipt"
                    if receipt_only
                    else "transactions.discovery"
                ),
            )

        if horizon and not all_history_address_request:
            try:
                hz = datetime.fromisoformat(horizon.replace("Z", ""))
                if hz < t0:
                    warnings.append(
                        f"Requested window starts {t0:%Y-%m-%d} but the transfer "
                        f"chain-log union only has data to {hz:%Y-%m-%d} — this window is "
                        "entirely past the data horizon, so an empty result means "
                        "STALE DATA, not absence of activity."
                    )
            except ValueError:
                pass

        tx_list_rows: list[list[Any]] = []
        txs_truncated = False
        discovered_total_matching: int | None = None
        discovered_total_lower_bound: int | None = None
        latest_before_t0: str | None = None
        discovery_path = "rpc_receipt" if explicit_hash_request else "unknown"
        discovery_coverage_complete = bool(explicit_hash_request)
        discovery_scanned_ranges: list[dict[str, str]] = []
        discovery_uncovered_ranges: list[dict[str, str]] = []
        older_history_unscanned = False
        next_discovery_cursor: str | None = None
        # hash -> block. Filled by discovery (free) or by RPC (pasted hashes).
        block_of: dict[str, int] = {}

        # ---- Choose the transactions ---------------------------------------
        execution_discovery_attempted = False
        rpc_discovery_attempted = False
        if not hashes and plain_address_request:
            execution_discovery_attempted = True
            log_source_checks = [
                validate_source_contract(
                    ch,
                    relation,
                    (
                        "block_number",
                        "transaction_index",
                        "log_index",
                        "transaction_hash",
                        "block_timestamp",
                        "address",
                        "topic0",
                        "topic1",
                        "topic2",
                        "topic3",
                        "data",
                    ),
                )
                for relation in CHAIN_LOG_RELATIONS
            ]
            transaction_source_checks = [
                validate_source_contract(
                    ch,
                    relation,
                    (
                        "block_number",
                        "transaction_index",
                        "transaction_hash",
                        "block_timestamp",
                        "from_address",
                        "to_address",
                    ),
                )
                for relation in CHAIN_TRANSACTION_RELATIONS
            ]
            source_checks = [
                *(
                    log_source_checks
                    if "erc20" in effective_activity_kinds or not candidate_only
                    else []
                ),
                *(
                    transaction_source_checks
                    if "direct" in effective_activity_kinds or not candidate_only
                    else []
                ),
            ]
            invalid = [check for check in source_checks if not check["ok"]]
            log_heads = [
                int(chain_block_horizons[relation])
                for relation in CHAIN_LOG_RELATIONS
                if chain_block_horizons.get(relation) is not None
            ]
            transaction_heads = [
                int(chain_block_horizons[relation])
                for relation in CHAIN_TRANSACTION_RELATIONS
                if chain_block_horizons.get(relation) is not None
            ]
            required_heads_available = bool(
                ("erc20" not in effective_activity_kinds or log_heads)
                and ("direct" not in effective_activity_kinds or transaction_heads)
            )
            if invalid or not horizon or not required_heads_available:
                failed_sources = [
                    source_record(
                        kind="chain",
                        name=check["relation"],
                        role="discovery",
                        status="error" if not check["ok"] else "partial",
                        horizon=chain_horizons.get(check["relation"]),
                        horizon_basis="max(block_timestamp), max(block_number)",
                        error=(
                            check.get("error")
                            if not check["ok"]
                            else "common execution-table horizon unavailable"
                        ),
                    )
                    for check in source_checks
                ]
                return failed_load(
                    "Execution transaction/log address discovery source is unavailable",
                    failed_sources,
                )
            execution_through = horizon
            execution_since: str | None = None
            execution_query_floor = (
                t0 if explicit_discovery_window else _GNOSIS_CHAIN_GENESIS_UTC
            )
            discovery_tile_seconds = (
                coverage_discovery_before.tile_seconds
                if coverage_discovery_before is not None
                else _DISCOVERY_DEFAULT_TILE_SECONDS
            )
            coverage_boundary: datetime | None = None
            coverage_retry_start: datetime | None = None
            if coverage_discovery_before is not None:
                # ``before_time`` is exclusive. Version-two cursors may also
                # carry the exact lower bound of an unresolved slice; those
                # retry that slice before moving into older history.
                coverage_boundary = datetime.fromisoformat(
                    coverage_discovery_before.before_time.replace("Z", "+00:00")
                ).astimezone(timezone.utc).replace(tzinfo=None)
                execution_through = (
                    coverage_boundary - timedelta(seconds=1)
                ).strftime("%Y-%m-%d %H:%M:%S")
                if coverage_discovery_before.retry_from_time:
                    coverage_retry_start = datetime.fromisoformat(
                        coverage_discovery_before.retry_from_time.replace(
                            "Z", "+00:00"
                        )
                    ).astimezone(timezone.utc).replace(tzinfo=None)
            if explicit_discovery_window:
                stored_horizon_dt = parse_window(horizon)
                execution_end = min(t1, stored_horizon_dt + timedelta(seconds=1))
                if coverage_boundary is not None:
                    execution_end = min(execution_end, coverage_boundary)
                execution_through = (
                    execution_end - timedelta(seconds=1)
                ).strftime("%Y-%m-%d %H:%M:%S")
                execution_since = t0.strftime("%Y-%m-%d %H:%M:%S")
            if coverage_retry_start is not None:
                execution_since = max(
                    execution_query_floor, coverage_retry_start
                ).strftime("%Y-%m-%d %H:%M:%S")
            resolved_retry_boundary: str | None = None
            try:
                execution_result = _discover_address_transactions_execution(
                    ch,
                    seed,
                    through=execution_through,
                    since=execution_since,
                    limit=limit_txs,
                    before=row_discovery_before,
                    activity_kinds=normalized_activity_kinds,
                    tokens=tokens or [],
                    detailed=candidate_only,
                    tile_seconds=discovery_tile_seconds,
                ) if candidate_only else _discover_address_transactions_execution(
                        ch,
                        seed,
                        through=horizon,
                        limit=limit_txs,
                )
                (
                    execution_rows,
                    execution_total,
                    execution_complete,
                    execution_scanned_total,
                ) = execution_result[:4]
                if candidate_only:
                    execution_coverage = execution_result[4]
                    if (
                        coverage_retry_start is not None
                        and execution_complete
                        and coverage_retry_start > execution_query_floor
                    ):
                        # The exact failed slice is now resolved, but history
                        # below it is intentionally still pending.  Converting
                        # this bounded success into an overall exact zero would
                        # be forensically wrong; emit a generic continuation at
                        # the slice's lower edge instead.
                        resolved_retry_boundary = coverage_retry_start.replace(
                            tzinfo=timezone.utc
                        ).isoformat().replace("+00:00", "Z")
                        execution_complete = False
                        execution_total = None
                        execution_coverage["older_history_unscanned"] = True
                    discovery_scanned_ranges.extend(
                        execution_coverage["scanned_ranges"]
                    )
                    discovery_uncovered_ranges.extend(
                        execution_coverage["uncovered_ranges"]
                    )
                    older_history_unscanned = bool(
                        execution_coverage["older_history_unscanned"]
                    )
            except Exception as exc:
                return failed_load(
                    f"Execution transaction/log address discovery failed: {exc}",
                    [
                        source_record(
                            kind="chain",
                            name=check["relation"],
                            role="discovery",
                            status="error",
                            horizon=chain_horizons.get(check["relation"]),
                            horizon_basis="complete storage chunks through source horizon",
                            error=str(exc),
                        )
                        for check in source_checks
                    ],
                )
            sources.extend(
                source_record(
                    kind="chain",
                    name=check["relation"],
                    role="discovery",
                    status="ok" if execution_complete else "partial",
                    horizon=chain_horizons.get(check["relation"]),
                    horizon_basis=(
                        "complete storage chunks from chain genesis"
                        if execution_complete
                        else "newest-first storage pages until result admission"
                    ),
                    error=(
                        None
                        if execution_complete
                        else "older execution history remains outside this result page"
                    ),
                )
                for check in source_checks
            )

            relevant_heads = [
                *([max(log_heads)] if "erc20" in effective_activity_kinds else []),
                *(
                    [max(transaction_heads)]
                    if "direct" in effective_activity_kinds
                    else []
                ),
            ]
            stored_head = min(relevant_heads)
            rpc_tail_failed = False
            try:
                bounded_before_stored_head = bool(
                    coverage_discovery_before is not None
                    or (
                        candidate_only
                        and explicit_discovery_window
                        and t1 <= parse_window(str(horizon)) + timedelta(seconds=1)
                    )
                )
                if bounded_before_stored_head:
                    rpc_head = stored_head
                    transfer_tail_rows = []
                    direct_tail_rows = []
                elif candidate_only:
                    if "erc20" in effective_activity_kinds:
                        transfer_tail_rows, rpc_head = (
                            _discover_address_transactions_rpc(
                                seed,
                                after_block=stored_head + 1,
                                tokens=tokens or [],
                                chain_id=chain_id,
                            )
                        )
                    else:
                        rpc_client = _router(chain_id).standard
                        rpc_head = int(rpc_client.request("eth_blockNumber", []), 16)
                        transfer_tail_rows = []
                    direct_tail_rows = (
                        _discover_address_direct_transactions_rpc(
                            seed,
                            after_block=stored_head + 1,
                            through_block=rpc_head,
                            chain_id=chain_id,
                        )
                        if "direct" in effective_activity_kinds
                        else []
                    )
                else:
                    transfer_tail_rows, rpc_head = _discover_address_transactions_rpc(
                        seed,
                        after_block=stored_head + 1,
                        chain_id=chain_id,
                    )
                    direct_tail_rows = _discover_address_direct_transactions_rpc(
                        seed,
                        after_block=stored_head + 1,
                        through_block=rpc_head,
                        chain_id=chain_id,
                    )
            except Exception as exc:
                if not candidate_only:
                    return failed_load(
                        f"RPC tail discovery after execution block {stored_head} failed: {exc}",
                        [
                            *sources,
                            source_record(
                                kind="rpc",
                                name="eth_getLogs + eth_getBlockByNumber",
                                role="discovery_tail",
                                status="error",
                                horizon=stored_head,
                                horizon_basis="common execution-table horizon to RPC head",
                                error=str(exc),
                            ),
                        ],
                    )
                rpc_tail_failed = True
                transfer_tail_rows = []
                direct_tail_rows = []
                rpc_head = stored_head
                discovery_coverage_complete = False
                tail_start = parse_window(str(horizon)).replace(tzinfo=timezone.utc)
                tail_end = (
                    t1.replace(tzinfo=timezone.utc)
                    if explicit_discovery_window
                    else datetime.now(timezone.utc)
                )
                discovery_uncovered_ranges.append(
                    {
                        "t0": tail_start.isoformat().replace("+00:00", "Z"),
                        "t1": tail_end.isoformat().replace("+00:00", "Z"),
                        "reason": str(exc),
                    }
                )
                warnings.append(
                    "RPC-head discovery failed; stored execution candidates remain "
                    "inspectable and the uncovered head is disclosed."
                )
                sources.append(
                    source_record(
                        kind="rpc",
                        name="eth_getLogs + eth_getBlockByNumber",
                        role="discovery_tail",
                        status="error",
                        horizon=stored_head,
                        horizon_basis="common execution-table horizon to RPC head",
                        error=str(exc),
                    )
                )
            rpc_tail_sources: list[dict[str, Any]] = []
            if (
                not rpc_tail_failed
                and (not candidate_only or "erc20" in effective_activity_kinds)
                and not bounded_before_stored_head
            ):
                rpc_tail_sources.append(
                    source_record(
                        kind="rpc",
                        name="eth_getLogs",
                        role="discovery_tail",
                        status="ok",
                        horizon=rpc_head,
                        horizon_basis=(
                            f"Transfer logs in blocks {stored_head + 1} "
                            "through eth_blockNumber"
                        ),
                    )
                )
            if (
                not rpc_tail_failed
                and (not candidate_only or "direct" in effective_activity_kinds)
                and not bounded_before_stored_head
            ):
                rpc_tail_sources.append(
                    source_record(
                        kind="rpc",
                        name="eth_getBlockByNumber",
                        role="discovery_tail",
                        status="ok",
                        horizon=rpc_head,
                        horizon_basis=(
                            f"direct transactions in blocks {stored_head + 1} "
                            "through eth_blockNumber"
                        ),
                    )
                )
            sources.extend(rpc_tail_sources)
            rpc_tail_rows = _merge_tx_discovery_rows(
                direct_tail_rows, transfer_tail_rows
            )
            if candidate_only:
                rpc_tail_rows = [
                    row
                    for row in rpc_tail_rows
                    if _candidate_precedes(row, row_discovery_before)
                ]
                if explicit_discovery_window and rpc_tail_rows:
                    rpc_tail_rows = _hydrate_rpc_discovery_timestamps(
                        rpc_tail_rows, chain_id=chain_id
                    )
                    rpc_tail_rows = [
                        row
                        for row in rpc_tail_rows
                        if str(row[3] or "")
                        and t0 <= parse_window(str(row[3])) < t1
                    ]
                stored_horizon_bound = parse_window(str(horizon))
                tail_end = t1 if explicit_discovery_window else datetime.now(
                    timezone.utc
                ).replace(tzinfo=None)
                if (
                    not rpc_tail_failed
                    and not bounded_before_stored_head
                    and tail_end > stored_horizon_bound
                ):
                    discovery_scanned_ranges.append(
                        {
                            "t0": stored_horizon_bound.replace(
                                tzinfo=timezone.utc
                            ).isoformat().replace("+00:00", "Z"),
                            "t1": tail_end.replace(tzinfo=timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        }
                    )
            execution_hashes = {_hex0x(str(row[0])) for row in execution_rows}
            tail_new = [
                row
                for row in rpc_tail_rows
                if _hex0x(str(row[0])) not in execution_hashes
            ]
            discovered_total_matching = (
                execution_total + len(tail_new)
                if execution_total is not None
                else None
            )
            discovered_total_lower_bound = execution_scanned_total + len(tail_new)
            combined_by_hash = {
                _hex0x(str(row[0])): list(row)
                for row in [*execution_rows, *tail_new]
            }
            all_rows = sorted(
                combined_by_hash.values(),
                key=lambda row: (
                    int(row[1] or 0),
                    int(row[2] or 0),
                    str(row[0]),
                ),
                reverse=True,
            )
            txs_truncated = bool(
                not execution_complete
                or (
                    discovered_total_matching is not None
                    and discovered_total_matching > limit_txs
                )
            )
            rows = all_rows[:limit_txs]
            if candidate_only and len(all_rows) > limit_txs:
                older_history_unscanned = True
            # A row cursor is only useful when the scanned portion itself
            # contains another admitted page.  When a partial scan found fewer
            # than ``limit_txs`` matches, a row cursor would restart at the
            # source horizon and rescan the same recent tiles before reaching
            # older history.  Continue from the measured coverage boundary
            # instead; this is both faster and preserves monotonic coverage.
            if candidate_only and len(all_rows) > limit_txs:
                next_discovery_cursor = _encode_discovery_cursor(rows[-1])
            elif (
                candidate_only
                and not execution_complete
                and (
                    older_history_unscanned
                    or bool(execution_coverage["uncovered_ranges"])
                )
            ):
                attempted_tile_seconds = int(
                    execution_coverage.get(
                        "tile_seconds", discovery_tile_seconds
                    )
                )
                resource_failure = _uncovered_requires_smaller_tile(
                    execution_coverage["uncovered_ranges"]
                )
                retry_tile_seconds = (
                    max(
                        _DISCOVERY_MIN_TILE_SECONDS,
                        attempted_tile_seconds // 2,
                    )
                    if resource_failure
                    else attempted_tile_seconds
                )
                unresolved_slice = _newest_uncovered_retry_slice(
                    execution_coverage["uncovered_ranges"],
                    tile_seconds=retry_tile_seconds,
                )
                continuation_boundary = _coverage_continuation_boundary(
                    execution_coverage["scanned_ranges"],
                    through=execution_through,
                )
                if not resource_failure and continuation_boundary:
                    # Ordinary pagination stopped at the loader wall budget.
                    # Continue below the fully scanned suffix and retain the
                    # measured-safe one-day tile so each click still advances
                    # a bounded batch rather than half a day.
                    next_discovery_cursor = _encode_coverage_discovery_cursor(
                        continuation_boundary,
                        tile_seconds=attempted_tile_seconds,
                    )
                elif unresolved_slice is not None:
                    retry_from, retry_before = unresolved_slice
                    next_discovery_cursor = _encode_coverage_discovery_cursor(
                        retry_before,
                        retry_from_time=retry_from,
                        tile_seconds=retry_tile_seconds,
                    )
                elif resolved_retry_boundary is not None:
                    next_discovery_cursor = _encode_coverage_discovery_cursor(
                        resolved_retry_boundary,
                        tile_seconds=attempted_tile_seconds,
                    )
                elif continuation_boundary:
                    next_discovery_cursor = _encode_coverage_discovery_cursor(
                        continuation_boundary,
                        tile_seconds=attempted_tile_seconds,
                    )
                else:
                    # Defensive fallback for older/mocked coverage without an
                    # explicit uncovered interval. Retry one bounded newest
                    # slice; never drop the only action that can advance a
                    # partial all-history search.
                    retry_boundary = (
                        parse_window(execution_through)
                        + timedelta(seconds=1)
                    )
                    retry_from = max(
                        execution_query_floor,
                        retry_boundary - timedelta(seconds=retry_tile_seconds),
                    )
                    if (
                        retry_boundary > execution_query_floor
                        and retry_from < retry_boundary
                    ):
                        next_discovery_cursor = (
                            _encode_coverage_discovery_cursor(
                                retry_boundary.replace(
                                    tzinfo=timezone.utc
                                ).isoformat().replace("+00:00", "Z"),
                                retry_from_time=retry_from.replace(
                                    tzinfo=timezone.utc
                                ).isoformat().replace("+00:00", "Z"),
                                tile_seconds=retry_tile_seconds,
                            )
                        )
            hashes = [_hex0x(str(row[0])) for row in rows]
            block_of.update(
                {
                    _hex0x(str(row[0])): int(row[1] or 0)
                    for row in rows
                    if int(row[1] or 0)
                }
            )
            tx_list_rows = [list(row) for row in rows]
            horizon = rpc_head
            discovery_path = "execution_tables_rpc_tail"
            discovery_coverage_complete = execution_complete and not rpc_tail_failed
            if not execution_complete:
                if rows:
                    warnings.append(
                        f"Newest-first admission returned at least "
                        f"{discovered_total_lower_bound} matching transaction(s); "
                        "older stored history remains available through the continuation cursor."
                    )
                else:
                    warnings.append(
                        "The newest stored-data slices contained no matching transaction. "
                        "Older stored history remains unscanned and is available through "
                        "the continuation cursor; this is not verified absence."
                    )

        if not hashes and rpc_address_request:
            rpc_discovery_attempted = True
            try:
                all_rpc_rows, rpc_head = _discover_address_transactions_rpc(
                    seed,
                    after_block=int(after_block or 0) if expand else 0,
                    after_index=int(after_index if after_index is not None else -1),
                    tokens=tokens or [],
                    counterparty_ids=cps,
                    chain_id=chain_id,
                )
            except Exception as exc:
                return failed_load(
                    f"Cursor-to-head RPC address discovery failed: {exc}",
                    [
                        source_record(
                            kind="rpc",
                            name="eth_getLogs",
                            role="discovery",
                            status="error",
                            horizon=horizon,
                            horizon_basis=f"eth_blockNumber; cursor-to-head scan after block {after_block}",
                            error=str(exc),
                        )
                    ],
                )
            horizon = rpc_head
            discovery_path = "rpc_logs_full_history"
            discovery_coverage_complete = True
            discovered_total_matching = len(all_rpc_rows)
            sources.append(
                source_record(
                    kind="rpc",
                    name="eth_getLogs",
                    role="discovery",
                    status="ok",
                    horizon=rpc_head,
                    horizon_basis=f"eth_blockNumber; complete cursor-to-head scan after block {after_block}",
                )
            )
            txs_truncated = len(all_rpc_rows) > limit_txs
            rows = all_rpc_rows[:limit_txs]
            hashes = [_hex0x(str(row[0])) for row in rows]
            block_of.update(
                {
                    _hex0x(str(row[0])): int(row[1] or 0)
                    for row in rows
                    if int(row[1] or 0)
                }
            )
            tx_list_rows = [list(row) for row in rows]
            if expand and not hashes:
                warnings.append(
                    f"{short_id(expand)} has no further standard ERC-20 Transfer "
                    f"transactions after block {after_block} through RPC head {rpc_head}."
                )
            if merge:
                prior = [_hex0x(str(h)) for h in (state_tx.get("tx_hashes") or [])]
                hashes = prior + [h for h in hashes if h not in prior]
                if len(hashes) > constants.TX_MAX_TXS:
                    hashes = hashes[-constants.TX_MAX_TXS :]
                    warnings.append(
                        f"Transaction set capped at {constants.TX_MAX_TXS}; the "
                        "oldest were dropped to make room for newly followed ones."
                    )

        if (
            not hashes
            and not rpc_discovery_attempted
            and not execution_discovery_attempted
        ):
            # Money Trail edge drill-down is already bounded by the exact
            # applied UTC window and token/counterparty predicates. Query the
            # existing Transfer-log union directly; no participant index is
            # required or preferred.
            discovery_path = "execution_logs_window"
            source_checks = [
                validate_source_contract(
                    ch,
                    relation,
                    (
                        "block_number",
                        "transaction_index",
                        "log_index",
                        "transaction_hash",
                        "block_timestamp",
                        "address",
                        "topic0",
                        "topic1",
                        "topic2",
                        "topic3",
                        "data",
                    ),
                )
                for relation in CHAIN_LOG_RELATIONS
            ]
            invalid = [check for check in source_checks if not check["ok"]]
            if invalid:
                failed_sources = [
                    source_record(
                        kind="chain",
                        name=check["relation"],
                        role="discovery",
                        status="error" if not check["ok"] else "ok",
                        horizon=chain_horizons.get(check["relation"]),
                        horizon_basis="max(block_timestamp), max(block_number)",
                        error=check.get("error"),
                    )
                    for check in source_checks
                ]
                return failed_load(
                    "Transaction discovery source contract failed: "
                    + "; ".join(str(check.get("error")) for check in invalid),
                    failed_sources,
                )
            sources.extend(
                source_record(
                    kind="chain",
                    name=check["relation"],
                    role="discovery",
                    status="ok",
                    horizon=chain_horizons.get(check["relation"]),
                    horizon_basis="bounded UTC window over Transfer logs",
                )
                for check in source_checks
            )
            dsql, dparams = build_tx_discovery_sql(
                address_ids=[seed],
                t0=t0.strftime("%Y-%m-%d %H:%M:%S"),
                t1_exclusive=t1.strftime("%Y-%m-%d %H:%M:%S"),
                min_usd=float(min_usd or 0),
                tokens=tokens or [],
                counterparty_ids=cps,
                limit=limit_txs,
                after_block=int(after_block or 0),
                after_index=int(after_index if after_index is not None else -1),
            )
            try:
                dres = _run(
                    ch,
                    dsql,
                    dparams,
                    query_budget=DISCOVERY_QUERY_BUDGET,
                )
            except Exception as exc:
                failed_sources = [dict(source) for source in sources]
                for source in failed_sources:
                    if source.get("role") == "discovery":
                        source["status"] = "error"
                        source["error"] = str(exc)
                return failed_load(
                    f"Transaction discovery query failed: {exc}",
                    failed_sources,
                )
            raw_rows = [list(row[:6]) for row in dres.rows]
            discovered_total_lower_bound = len(raw_rows)
            txs_truncated = len(raw_rows) > limit_txs
            rows = raw_rows[:limit_txs]
            hashes = [_hex0x(str(row[0])) for row in rows]
            for row in rows:
                block_number = int(row[1] or 0)
                if block_number:
                    block_of[_hex0x(str(row[0]))] = block_number
            tx_list_rows = [list(row) for row in rows]

            try:
                horizon_dt = (
                    datetime.fromisoformat(str(horizon).replace("Z", "+00:00"))
                    if horizon
                    else None
                )
                if horizon_dt is not None and horizon_dt.tzinfo is not None:
                    horizon_dt = horizon_dt.astimezone(timezone.utc).replace(
                        tzinfo=None
                    )
                discovery_coverage_complete = bool(
                    horizon_dt is not None and horizon_dt >= t1
                )
            except ValueError:
                discovery_coverage_complete = False
            if not discovery_coverage_complete:
                warnings.append(
                    "Execution-log discovery does not cover the complete requested "
                    f"window through {t1:%Y-%m-%d %H:%M:%S}; source horizon is "
                    f"{horizon or 'unknown'}. An empty result is not verified absence."
                )
                for source in sources:
                    if (
                        source.get("role") == "discovery"
                        and source.get("status") == "ok"
                    ):
                        source["status"] = "stale"
                        source["error"] = "source union does not cover requested t1"

        if not hashes:
            if all_history_address_request and discovery_coverage_complete:
                warnings.append(
                    f"No direct or standard ERC-20 Transfer transactions found "
                    f"for {short_id(seed)} across the complete stored history "
                    f"and uncovered RPC tail through block {horizon}."
                )
            else:
                qualifier = (
                    f" with counterparty {short_id(cps[0])}" if cps else ""
                )
                if candidate_only and not discovery_coverage_complete:
                    warnings.append(
                        f"No matching transaction has been admitted yet for "
                        f"{short_id(seed)}{qualifier}; older stored history remains "
                        "unscanned, so absence is not verified."
                    )
                else:
                    warnings.append(
                        f"No transactions found for {short_id(seed)} in the applied scope"
                        + qualifier
                        + (" with the applied token filter." if tokens else ".")
                    )

        if candidate_only:
            previous_dataset = record.datasets.get("tx_list")
            same_query = bool(
                cursor
                and cursor_matches_query
                and previous_dataset is not None
            )
            base_tx_list_revision = int(
                record.dataset_revisions.get("tx_list", 0)
            )
            base_tx_list_row_count = (
                len(previous_dataset.rows) if previous_dataset is not None else 0
            )
            append_delta_rows: list[list[Any]] | None = None
            if same_query:
                previous_rows = [list(row) for row in previous_dataset.rows]
                merged_rows = _merge_tx_discovery_rows(
                    previous_rows, tx_list_rows
                )
                append_delta_rows = _append_only_discovery_delta(
                    previous_rows, merged_rows
                )
                tx_list_rows = merged_rows
                prior_coverage = state_tx.get("discovery_coverage") or {}
                discovery_scanned_ranges = [
                    *list(prior_coverage.get("scanned_ranges") or []),
                    *discovery_scanned_ranges,
                ]
                discovery_uncovered_ranges = [
                    *list(prior_coverage.get("uncovered_ranges") or []),
                    *discovery_uncovered_ranges,
                ]
                if discovery_uncovered_ranges:
                    discovery_coverage_complete = False

            total_exact = (
                (
                    len(tx_list_rows)
                    if same_query
                    else discovered_total_matching
                )
                if discovery_coverage_complete
                else None
            )
            total_lower_bound = max(
                int(discovered_total_lower_bound or 0), len(tx_list_rows)
            )
            coverage = {
                "complete": discovery_coverage_complete,
                "total_exact": total_exact,
                "total_lower_bound": total_lower_bound,
                "next_cursor": next_discovery_cursor,
                "scanned_ranges": _coalesce_scanned_ranges(
                    discovery_scanned_ranges
                ),
                "uncovered_ranges": discovery_uncovered_ranges,
                "older_history_unscanned": bool(
                    older_history_unscanned or txs_truncated
                ),
            }
            verified_empty = bool(
                not tx_list_rows
                and coverage["complete"]
                and coverage["total_exact"] == 0
                and not coverage["uncovered_ranges"]
            )
            scope_status = (
                "ready"
                if coverage["complete"] and not coverage["uncovered_ranges"]
                else "partial"
            )
            scope = forensic_scope(
                chain_id=chain_id,
                scope_id=scope_id,
                request_id=request_id,
                status=scope_status,
                t0=(t0.strftime("%Y-%m-%d %H:%M:%S") if exact_window_request else None),
                t1=(t1.strftime("%Y-%m-%d %H:%M:%S") if exact_window_request else None),
                window_source=applied_window_source,
                data_horizon=horizon,
                sources=sources,
                rows_returned=len(tx_list_rows),
                rows_total=total_exact,
                nodes_returned=0,
                nodes_total=0 if verified_empty else None,
                edges_returned=0,
                edges_total=0 if verified_empty else None,
                truncated=bool(txs_truncated),
                truncation_rule=(
                    f"newest-first keyset page of {limit_txs} candidates"
                    if txs_truncated
                    else None
                ),
                residuals=(
                    "candidate discovery is not receipt verification",
                    "native/internal value semantics require receipt and trace evidence",
                ),
                warnings=warnings,
                verification_status="verified" if verified_empty else "unverified",
                verification_method=(
                    "complete execution/live history plus bounded RPC head"
                    if coverage["complete"]
                    else "newest-first admitted candidates with disclosed remaining coverage"
                ),
                query_kind="address_discovery",
                evidence_class="address_discovery",
                subjects=[seed],
                result_row_hash=canonical_row_hash(tx_list_rows),
            )
            scope.update(
                {
                    "query_kind": "address_discovery",
                    "evidence_class": "address_discovery",
                    "discovery_path": discovery_path,
                    "discovery_coverage": coverage,
                    "receipt_verification_status": "not_loaded",
                    "txs_total_matching": total_exact,
                    "txs_total_lower_bound": total_lower_bound,
                    "more_transactions_available": bool(next_discovery_cursor),
                    "exact": verified_empty,
                    "ordered": True,
                    "legs_returned": 0,
                    "legs_total": 0 if verified_empty else None,
                }
            )
            query = query_contract()
            append_patch_safe = bool(
                same_query
                and append_delta_rows is not None
                and base_tx_list_revision > 0
            )
            candidate_datasets = {
                "tx_list": dataset_from_rows(
                    constants.TX_LIST_COLUMNS, tx_list_rows, "tx_list"
                ),
            }
            if not append_patch_safe:
                candidate_datasets.update(
                    {
                        "tx_nodes": dataset_from_rows(
                            constants.TX_LEG_NODES_COLUMNS, [], "tx_nodes"
                        ),
                        "tx_legs": dataset_from_rows(
                            constants.TX_LEG_EDGES_COLUMNS, [], "tx_legs"
                        ),
                        "tx_raw_receipts": dataset_from_rows(
                            constants.TX_RAW_RECEIPTS_COLUMNS,
                            [],
                            "tx_raw_receipts",
                        ),
                        "tx_context": dataset_from_rows(
                            _TX_CONTEXT_COLUMNS, [], "tx_context"
                        ),
                    }
                )
            candidate_dataset_scopes = {"tx_list": scope_id}
            if not append_patch_safe:
                candidate_dataset_scopes.update(
                    {
                        "tx_nodes": scope_id,
                        "tx_legs": scope_id,
                        "tx_raw_receipts": scope_id,
                        "tx_context": scope_id,
                    }
                )
            candidate_state_patch = {
                "transactions": {
                    "operation": "discover",
                    "tx_hashes": [],
                    "query_kind": "address_discovery",
                    "query_hashes": [],
                    "result_hashes": [str(row[0]) for row in tx_list_rows],
                    "query": query,
                    "results": {
                        "hashes": [str(row[0]) for row in tx_list_rows],
                        "selected_hash": None,
                    },
                    "seed": seed,
                    "counterparties": [],
                    "range_days": 0,
                    "t0": query.get("window", {}).get("t0", "")
                    if query.get("window")
                    else "",
                    "t1": query.get("window", {}).get("t1", "")
                    if query.get("window")
                    else "",
                    "max_txs": limit_txs,
                    "page_size": limit_txs,
                    "tokens": tokens or [],
                    "activity_kinds": normalized_activity_kinds,
                    "tx_count": len(tx_list_rows),
                    "leg_count": 0,
                    "scope": scope,
                    "discovery_scope": scope,
                    "receipt_scope": None,
                    "discovery_coverage": coverage,
                    "last_attempt": None,
                },
                "dataset_scopes": candidate_dataset_scopes,
                "warnings": warnings,
            }
            committed = mini_apps.commit_view_update(
                view_id,
                request_channel="transactions.discovery",
                request_id=request_id,
                guard_channels=("transactions",),
                datasets=candidate_datasets,
                state_patch=candidate_state_patch,
            )
            updated = mini_apps.snapshot_view(view_id)
            assert updated is not None
            if not committed:
                return mini_apps.payload_to_call_tool_result(
                    build_payload(updated),
                    summary_text="Transaction discovery was superseded by a newer request.",
                )
            summary = (
                f"No matching address activity for {short_id(seed)} (verified)."
                if verified_empty
                else f"Discovered {len(tx_list_rows)} candidate transaction(s) for "
                f"{short_id(seed)}; select one to verify its receipt."
            )
            response_payload = (
                build_dataset_append_patch(
                    updated,
                    dataset_key="tx_list",
                    base_revision=base_tx_list_revision,
                    base_row_count=base_tx_list_row_count,
                    append_rows=append_delta_rows or [],
                    view_state_patch=candidate_state_patch,
                    scope=scope,
                )
                if append_patch_safe
                else build_payload(updated)
            )
            return mini_apps.payload_to_call_tool_result(
                response_payload, summary_text=summary
            )

        requested_hashes = list(hashes)

        # ---- Resolve blocks, then bound every subsequent query --------------
        # The log relations are ordered by block; an unbounded hash predicate is
        # a 30s timeout. Hashes discovered above already carry their block;
        # user-pasted hashes are resolved over RPC.
        # Legs for KNOWN transactions come from RPC receipts: ~155ms vs ~7s for
        # the equivalent SQL scan, and the receipt is authoritative (no
        # whitelist, no indexer lag). Plain-address hashes come from bounded,
        # newest-first execution transaction/log pages plus the uncovered RPC
        # head. SQL leg reads remain only a receipt fallback.
        rpc_rows: list[list[Any]] = []
        rpc_unresolved = list(hashes)
        receipt_statuses: dict[str, str] = {}
        receipt_blocks: dict[str, int] = {}
        receipt_decode_failures: list[dict[str, Any]] = []
        raw_receipt_rows: list[list[Any]] = []
        transaction_context_rows: list[list[Any]] = []
        if hashes:
            receipt_kwargs: dict[str, Any] = {
                "raw_receipt_rows": raw_receipt_rows,
            }
            if receipt_only:
                previous_query = state_tx.get("query") or {}
                receipt_kwargs.update(
                    {
                        "transaction_context_rows": transaction_context_rows,
                        "match_address": str(previous_query.get("address") or ""),
                        "filter_tokens": list(previous_query.get("tokens") or []),
                    }
                )
            (
                rpc_rows,
                rpc_unresolved,
                receipt_statuses,
                receipt_blocks,
                receipt_decode_failures,
            ) = _legs_from_receipts(
                hashes,
                chain_id=chain_id,
                **receipt_kwargs,
            )
            block_of.update(receipt_blocks)
            for r in rpc_rows:
                block_of.setdefault(str(r[0]), int(r[2]))

            # Discovery only establishes which hashes touch the address. Once
            # the receipts are present, make the result table transaction-wide
            # and authoritative: timestamp, standard ERC-20 leg count, and
            # token count all come from the decoded receipt rows.
            receipt_rows_by_hash: dict[str, list[list[Any]]] = {}
            for row in rpc_rows:
                receipt_rows_by_hash.setdefault(str(row[0]), []).append(row)
            receipt_tx_list_rows: list[list[Any]] = []
            for row in tx_list_rows:
                transaction_hash = str(row[0])
                receipt_rows = receipt_rows_by_hash.get(transaction_hash) or []
                if not receipt_rows:
                    receipt_tx_list_rows.append(list(row))
                    continue
                first = receipt_rows[0]
                receipt_tx_list_rows.append(
                    [
                        transaction_hash,
                        int(first[2] or 0),
                        int(first[3] or 0),
                        str(first[4] or ""),
                        len(receipt_rows),
                        len({str(item[7]) for item in receipt_rows}),
                    ]
                )
            tx_list_rows = receipt_tx_list_rows

        missing = [h for h in hashes if h not in block_of]
        if missing:
            resolved, unresolved = _resolve_tx_blocks(missing, chain_id=chain_id)
            block_of.update(resolved)
            if unresolved:
                warnings.append(
                    f"{len(unresolved)} transaction hash(es) could not be resolved "
                    "to a block (unknown hash, or the RPC is unavailable) and were "
                    "skipped: " + ", ".join(short_id(h) for h in unresolved[:3])
                )
                hashes = [h for h in hashes if h in block_of]

        unresolved_hashes = [h for h in requested_hashes if h not in hashes]

        # A hash that could not be block-resolved cannot safely enter the SQL
        # fallback.  Keep every successfully read receipt row/status, and make
        # the unresolved remainder explicit in the scope.
        requested_set = set(hashes)
        rpc_rows = [r for r in rpc_rows if str(r[0]) in requested_set]
        receipt_statuses = {
            h: status for h, status in receipt_statuses.items() if h in requested_set
        }
        raw_receipt_rows = [
            row for row in raw_receipt_rows if str(row[0]) in requested_set
        ]
        receipt_decode_failures = [
            failure
            for failure in receipt_decode_failures
            if str(failure.get("transaction_hash")) in requested_set
        ]
        if receipt_decode_failures:
            warnings.append(
                f"{len(receipt_decode_failures)} matching ERC-20 Transfer log(s) "
                "failed receipt decoding; those legs are omitted and this scope "
                "is PARTIAL."
            )

        known = [block_of[h] for h in hashes if h in block_of]
        block_lo = min(known) if known else 0
        block_hi = max(known) if known else 0

        # ---- Load every leg -------------------------------------------------
        # Receipt and SQL fallback rows share one raw shape. Enrichment is
        # deliberately deferred until *after* raw chain recovery so a missing
        # dbt metadata/price relation cannot erase fallback evidence.
        raw_leg_rows: list[list[Any]] = list(rpc_rows)
        leg_rows_raw: list[list[Any]] = []
        sql_leg_total: int | None = None
        sql_tx_total: int | None = None
        fallback_query_failed = False
        enrichment_failed = False
        receipts_available = bool(hashes) and all(
            _hex0x(h) in receipt_statuses for h in hashes
        )
        receipt_complete = receipts_available and not receipt_decode_failures
        if hashes:
            sources.append(
                source_record(
                    kind="rpc",
                    name="eth_getTransactionReceipt",
                    role="primary",
                    status="ok" if receipt_complete else "partial",
                    horizon=max(known) if known else None,
                    horizon_basis="receipt block_number",
                    error=(
                        None
                        if receipt_complete
                        else (
                            f"{len(receipt_decode_failures)} matching Transfer log(s) "
                            "failed decoding"
                            if receipts_available and receipt_decode_failures
                            else (
                                f"receipt unavailable for {len(hashes) - len(receipt_statuses)} "
                                "transaction(s)"
                            )
                        )
                    ),
                )
            )
            if receipt_only:
                context_complete = bool(
                    transaction_context_rows
                    and all(row[1] for row in transaction_context_rows)
                )
                sources.append(
                    source_record(
                        kind="rpc",
                        name="eth_getTransactionByHash",
                        role="primary",
                        status="ok" if context_complete else "partial",
                        horizon=max(known) if known else None,
                        horizon_basis="transaction block_number",
                        error=(
                            None
                            if context_complete
                            else "transaction envelope unavailable or incomplete"
                        ),
                    )
                )

            fallback_hashes = [
                h for h in hashes if _hex0x(h) not in receipt_statuses
            ]
            # The SQL fallback reads the GNOSIS execution tables. Off Gnosis it
            # would attach `execution.logs` as a source record — and, worse,
            # could return another chain's legs for a same-numbered block. A
            # receipt that the RPC could not serve stays unresolved instead.
            if fallback_hashes and not is_gnosis:
                warnings.append(
                    f"{chain_info.name}: {len(fallback_hashes)} receipt(s) could "
                    "not be read from RPC. There is no indexed fallback for this "
                    "chain, so those transactions are reported unresolved rather "
                    "than filled from another chain's tables."
                )
                fallback_query_failed = True
                fallback_hashes = []
            if fallback_hashes:
                checks = [
                    validate_source_contract(
                        ch,
                        relation,
                        (
                            "block_number",
                            "transaction_index",
                            "log_index",
                            "transaction_hash",
                            "block_timestamp",
                            "address",
                            "topic0",
                            "topic1",
                            "topic2",
                            "topic3",
                            "data",
                        ),
                    )
                    for relation in CHAIN_LOG_RELATIONS
                ]
                invalid_checks = [check for check in checks if not check["ok"]]
                sources.extend(
                    source_record(
                        kind="chain",
                        name=check["relation"],
                        role="fallback",
                        status="error" if not check["ok"] else "ok",
                        horizon=chain_horizons.get(check["relation"]),
                        horizon_basis="max(block_timestamp)",
                        error=check.get("error"),
                    )
                    for check in checks
                )
                if invalid_checks:
                    fallback_query_failed = True
                    warnings.append(
                        "SQL leg fallback unavailable: "
                        + "; ".join(
                            str(check.get("error")) for check in invalid_checks
                        )
                    )
                else:
                    fallback_blocks = [block_of[h] for h in fallback_hashes]
                    fallback_lo, fallback_hi = min(fallback_blocks), max(fallback_blocks)
                    try:
                        tsql, tparams = build_leg_total_sql(
                            tx_hashes=fallback_hashes,
                            block_lo=fallback_lo,
                            block_hi=fallback_hi,
                        )
                        tres = _run(ch, tsql, tparams)
                        if tres.rows and tres.rows[0]:
                            sql_leg_total = int(tres.rows[0][0] or 0)
                            sql_tx_total = int(tres.rows[0][1] or 0)
                    except Exception as exc:
                        warnings.append(f"SQL fallback COUNT failed: {exc}")
                    try:
                        lsql, lparams = build_legs_sql(
                            tx_hashes=fallback_hashes,
                            block_lo=fallback_lo,
                            block_hi=fallback_hi,
                            limit=constants.TX_MAX_LEGS,
                        )
                        lres = _run(ch, lsql, lparams)
                        # SQL returns chain fields only. Normalise them to the
                        # receipt decoder's raw shape; metadata, decimals, and
                        # prices are attached by the optional pass below.
                        raw_leg_rows.extend(
                            [
                                list(r[:8])
                                + ["", str(r[8]), None, "unknown"]
                                for r in lres.rows
                            ]
                        )
                    except Exception as exc:
                        warnings.append(f"SQL leg fallback failed: {exc}")
                        fallback_query_failed = True
                        for source in sources:
                            if source.get("role") == "fallback":
                                source["status"] = "error"
                                source["error"] = str(exc)

            (
                leg_rows_raw,
                enrichment_status,
                enrichment_warnings,
                enrichment_sources,
            ) = _enrich_rpc_legs(ch, raw_leg_rows, chain_id=chain_id)
            enrichment_failed = any(
                status not in {"ok", "not_needed"}
                for status in enrichment_status.values()
            )
            warnings.extend(enrichment_warnings)
            # Only claim the warehouse relations when they were actually
            # consulted. Off Gnosis `_enrich_rpc_legs` skips them entirely, so
            # listing them would assert a Gnosis source for another chain's
            # evidence — and `caseExport` copies sources into the bundle.
            sources.extend(
                []
                if not is_gnosis
                else [
                    source_record(
                        kind="dbt_aggregate",
                        name=TOKENS_META_RELATION,
                        role="enrichment",
                        status=enrichment_status["metadata"],
                        horizon=enrichment_sources["metadata"].get("horizon"),
                        horizon_basis=enrichment_sources["metadata"].get(
                            "horizon_basis"
                        ),
                        fetched_at=enrichment_sources["metadata"].get("fetched_at"),
                        error=enrichment_sources["metadata"].get("error"),
                    ),
                    source_record(
                        kind="dbt_aggregate",
                        name=PRICES_RELATION,
                        role="enrichment",
                        status=enrichment_status["prices"],
                        horizon=enrichment_sources["prices"].get("horizon"),
                        horizon_basis=enrichment_sources["prices"].get(
                            "horizon_basis"
                        ),
                        fetched_at=enrichment_sources["prices"].get("fetched_at"),
                        error=enrichment_sources["prices"].get("error"),
                    ),
                ]
            )

        if requested_hashes and not any(
            source.get("name") == "eth_getTransactionReceipt" for source in sources
        ):
            sources.append(
                source_record(
                    kind="rpc",
                    name="eth_getTransactionReceipt",
                    role="primary",
                    status="error",
                    error=(
                        f"{len(unresolved_hashes)} transaction hash(es) could not "
                        "be read or block-resolved"
                    ),
                )
            )

        receipt_leg_count = len(rpc_rows)
        receipt_transfer_log_total = receipt_leg_count + len(receipt_decode_failures)
        if receipts_available:
            # This is the 7-vs-9 safety rule: a nonzero SQL count can never
            # override the receipt decoder. A failed per-log decode still
            # establishes that the matching log EXISTS, while refusing to
            # invent a row/value for it.
            legs_total: int | None = receipt_transfer_log_total
            tx_total: int | None = len(receipt_statuses)
        elif sql_leg_total is not None:
            legs_total = receipt_transfer_log_total + sql_leg_total
            tx_total = len(receipt_statuses) + int(sql_tx_total or 0)
        elif (
            not requested_hashes
            and not explicit_hash_request
            and discovery_coverage_complete
        ):
            # The discovery predicate completed and returned no matching
            # transactions; this is an independently established zero.
            legs_total = 0
            tx_total = 0
        else:
            legs_total = None
            tx_total = None

        leg_rows_raw.sort(key=lambda r: (int(r[2] or 0), int(r[3] or 0), int(r[1] or 0)))

        legs_capped = len(leg_rows_raw) > constants.TX_MAX_LEGS
        if legs_capped:
            # Drop WHOLE trailing transactions — never render half a swap.
            leg_rows_raw = leg_rows_raw[: constants.TX_MAX_LEGS]
            kept_hashes: list[str] = []
            seen_order: list[str] = []
            for r in leg_rows_raw:
                h = str(r[0])
                if h not in seen_order:
                    seen_order.append(h)
            drop = seen_order[-1] if len(seen_order) > 1 else None
            if drop:
                leg_rows_raw = [r for r in leg_rows_raw if str(r[0]) != drop]
                kept_hashes = [h for h in seen_order if h != drop]
            else:
                kept_hashes = seen_order
            if len(seen_order) == 1:
                warnings.append(
                    f"Leg cap reached ({constants.TX_MAX_LEGS}): the selected "
                    f"transaction contains {legs_total or len(leg_rows_raw)} "
                    "standard ERC-20 Transfer logs, so only the first capped "
                    "legs are displayed. This receipt is PARTIAL in the UI."
                )
            else:
                warnings.append(
                    f"Leg cap reached ({constants.TX_MAX_LEGS}): showing "
                    f"{len(kept_hashes)} of {len(hashes)} transactions in full. "
                    "Whole trailing transactions are dropped rather than split."
                )
            hashes = kept_hashes
            kept_receipt_hashes = set(kept_hashes)
            raw_receipt_rows = [
                row for row in raw_receipt_rows if str(row[0]) in kept_receipt_hashes
            ]

        # ---- Identify token contracts among the participants ----------------
        participants: list[str] = []
        for r in leg_rows_raw:
            for nid in (str(r[5]), str(r[6])):
                if nid and nid not in participants:
                    participants.append(nid)
        token_contracts: set[str] = set()
        if participants:
            try:
                tcsql, tcparams = build_token_contract_sql(
                    participants, block_lo=block_lo, block_hi=block_hi
                )
                tcres = _run(ch, tcsql, tcparams)
                token_contracts = {str(r[0]) for r in tcres.rows if r and r[0]}
            except Exception as exc:  # pragma: no cover - defensive
                logger.info("tx mode: token-contract lookup failed: %s", exc)

        # ---- Assemble legs + participants -----------------------------------
        seeds = {seed} if seed else set()
        tx_rank: dict[str, int] = {}
        for r in leg_rows_raw:
            h = str(r[0])
            if h not in tx_rank:
                tx_rank[h] = len(tx_rank)

        leg_edge_rows: list[list[Any]] = []
        in_usd: dict[str, float] = {}
        out_usd: dict[str, float] = {}
        in_unpriced: set[str] = set()
        out_unpriced: set[str] = set()
        leg_count: dict[str, int] = {}
        known_usd_total = 0.0
        unknown_usd_rows = 0
        for seq, r in enumerate(leg_rows_raw):
            tx_hash, log_index = str(r[0]), int(r[1] or 0)
            src, tgt = str(r[5]), str(r[6])
            usd = None if r[10] is None else float(r[10])
            if usd is None:
                unknown_usd_rows += 1
                out_unpriced.add(src)
                in_unpriced.add(tgt)
            else:
                known_usd_total += usd
                out_usd[src] = out_usd.get(src, 0.0) + usd
                in_usd[tgt] = in_usd.get(tgt, 0.0) + usd
            leg_count[src] = leg_count.get(src, 0) + 1
            leg_count[tgt] = leg_count.get(tgt, 0) + 1
            leg_edge_rows.append([
                f"leg:{tx_hash}:{log_index}",
                src,
                tgt,
                tx_hash,
                log_index,
                int(r[2] or 0),
                int(r[3] or 0),
                str(r[4] or ""),
                str(r[7] or ""),
                str(r[8] or ""),
                None if r[9] is None else float(r[9]),
                None if usd is None else round(usd, 6),
                seq,
                tx_rank.get(tx_hash, 0),
                str(r[11] or "unknown") if len(r) > 11 else "unknown",
                str(r[12]) if len(r) > 12 else "",
            ])

        node_rows: list[list[Any]] = []
        for nid in participants:
            is_token = nid in token_contracts
            role = _role_of(nid, is_token=is_token, seeds=seeds)
            flags = []
            if is_token:
                flags.append("token_contract")
            if nid in BURN_ADDRESSES:
                flags.append("burn_address")
            if nid in seeds:
                flags.append("seed")
            node_rows.append([
                nid,
                short_id(nid),
                role,
                "",
                0,
                None if nid in in_unpriced else round(in_usd.get(nid, 0.0), 2),
                None if nid in out_unpriced else round(out_usd.get(nid, 0.0), 2),
                leg_count.get(nid, 0),
                flags,
            ])

        # ---- Scope contract --------------------------------------------------
        # Receipt enumeration is the independent count for resolved hashes.
        # SQL COUNT is used only for hashes whose receipt could not be read and
        # therefore cannot make the mixed result "verified".
        legs_returned = len(leg_edge_rows)
        # Missing/undecodable source rows make the scope partial, not
        # "truncated". Reserve truncation for an explicit admission cap so the
        # UI never mislabels a decode failure as "whole transactions dropped".
        truncated = bool(legs_capped or txs_truncated)
        empty_discovery_verified = bool(
            not explicit_hash_request
            and not hashes
            and not txs_truncated
            and discovery_coverage_complete
        )
        verification_status = (
            "verified"
            if (
                (explicit_hash_request and receipt_complete)
                or empty_discovery_verified
                or (
                    not explicit_hash_request
                    and discovery_coverage_complete
                    and receipt_complete
                )
            )
            else "unverified"
        )
        exact = bool(
            verification_status == "verified"
            and legs_total is not None
            and legs_returned == legs_total
            and not truncated
        )
        no_answer_for_requested = bool(
            requested_hashes
            and not leg_rows_raw
            and not receipt_complete
            and (fallback_query_failed or unresolved_hashes)
        )
        if no_answer_for_requested:
            verification_status = "failed"
            exact = False
        scope_status = (
            "failed"
            if no_answer_for_requested
            else ("ready" if exact and not enrichment_failed else "partial")
        )
        result_observed_through = next(
            (
                value
                for value in sorted(
                    {str(r[4]) for r in leg_rows_raw if len(r) > 4 and r[4]},
                    reverse=True,
                )
            ),
            None,
        )
        data_horizon = (
            max(known)
            if explicit_hash_request and known
            else horizon
        )
        total_usd = round(known_usd_total, 6) if unknown_usd_rows == 0 and exact else None
        scope_t0 = (
            None
            if explicit_hash_request or all_history_address_request
            else t0.strftime("%Y-%m-%d %H:%M:%S")
        )
        scope_t1 = (
            None
            if explicit_hash_request or all_history_address_request
            else t1.strftime("%Y-%m-%d %H:%M:%S")
        )
        plain_discovery_method = (
            "complete execution transaction/log history plus RPC-head discovery"
            if discovery_coverage_complete
            else (
                "newest-first execution transaction/log result admission plus "
                "RPC-head discovery"
            )
        )
        scope = forensic_scope(
            chain_id=chain_id,
            scope_id=scope_id,
            request_id=request_id,
            status=scope_status,
            t0=scope_t0,
            t1=scope_t1,
            window_source=applied_window_source,
            data_horizon=data_horizon,
            result_observed_through=result_observed_through,
            sources=sources,
            rows_returned=legs_returned,
            rows_total=legs_total,
            nodes_returned=len(node_rows),
            nodes_total=len(node_rows) if exact else None,
            edges_returned=legs_returned,
            edges_total=legs_total,
            known_usd=round(known_usd_total, 6) if is_gnosis else None,
            total_usd=total_usd,
            unknown_usd_rows=unknown_usd_rows,
            truncated=truncated,
            truncation_rule=(
                "; ".join(
                    rule
                    for rule in (
                        (
                            (
                                f"newest {limit_txs} transactions admitted from "
                                f"{discovered_total_matching} discovered"
                                if discovered_total_matching is not None
                                else (
                                    f"newest {limit_txs} transactions admitted after "
                                    f"establishing at least "
                                    f"{discovered_total_lower_bound or limit_txs + 1} matches; "
                                    "older execution pages not scanned"
                                )
                            )
                            if txs_truncated
                            else None
                        ),
                        (
                            f"whole-transaction admission under "
                            f"{constants.TX_MAX_LEGS}-leg cap"
                            if legs_capped
                            else None
                        ),
                    )
                    if rule
                )
                or None
            ),
            residuals=(
                f"native {chain_info.native_symbol} value is not represented by "
                    "ERC-20 Transfer logs",
                "internal-call value requires trace_transaction",
                "missing metadata or prices do not remove ERC-20 legs",
            ),
            warnings=warnings,
            verification_status=verification_status,
            verification_method=(
                "source unavailable; no leg set could be verified"
                if no_answer_for_requested
                else (
                    (
                        (
                            f"{plain_discovery_method}; eth_getTransactionReceipt "
                            "transfer-log enumeration"
                            if plain_address_request
                            else "cursor-to-head eth_getLogs discovery plus "
                            "eth_getTransactionReceipt transfer-log enumeration"
                        )
                        if all_history_address_request
                        else "eth_getTransactionReceipt transfer-log enumeration"
                    )
                    if receipt_complete
                    else (
                        "eth_getTransactionReceipt read succeeded, but matching "
                        "Transfer logs failed ABI decoding"
                        if receipt_decode_failures
                        else (
                            (
                                (
                                    f"{plain_discovery_method} returned no matching "
                                    "direct or standard ERC-20 Transfer transaction"
                                    if plain_address_request
                                    else "complete cursor-to-head eth_getLogs scan returned "
                                    "no matching standard ERC-20 Transfer transaction"
                                )
                                if all_history_address_request
                                else "chain-log discovery returned no matching transaction"
                            )
                            if empty_discovery_verified
                            else (
                                "address discovery source horizon does not cover requested t1"
                                if (
                                    not explicit_hash_request
                                    and not requested_hashes
                                    and not discovery_coverage_complete
                                )
                                else "mixed receipt and SQL fallback; not chain-verified"
                            )
                        )
                    )
                )
            ),
            query_kind=query_kind,
            evidence_class=(
                "rpc_receipt"
                if query_kind == "explicit_hash"
                else "address_discovery"
            ),
            subjects=(requested_hashes if explicit_hash_request else [seed]),
            result_row_hash=canonical_row_hash(
                [*tx_list_rows, *node_rows, *leg_edge_rows]
            ),
        )
        # The shared helper conservatively infers truncation from shown<total.
        # Here that gap may instead be an explicitly captured decode failure;
        # only the transaction/leg admission caps are truncation policy.
        scope["truncation"]["truncated"] = truncated
        # Compatibility fields for the current client; the nested contract is
        # authoritative and the UI migration can remove these later.
        scope.update(
            {
                "query_kind": query_kind,
                "evidence_class": (
                    "rpc_receipt"
                    if query_kind == "explicit_hash"
                    else "address_discovery"
                ),
                "discovery_path": discovery_path,
                "discovery_coverage": {
                    "complete": discovery_coverage_complete,
                    "requested_t1": scope["window"]["t1"],
                    "source_horizon": data_horizon,
                },
                "receipt_verification_status": (
                    "verified" if receipt_complete else "unverified"
                ),
                "latest_before_t0": latest_before_t0,
                "t0": scope["window"]["t0"],
                "t1": scope["window"]["t1"],
                "window_source": scope["window"]["source"],
                "txs_requested": len(requested_hashes),
                "txs_total_matching": (
                    tx_total
                    if explicit_hash_request
                    else (
                        discovered_total_matching
                        if discovery_coverage_complete
                        else None
                    )
                ),
                "txs_total_lower_bound": discovered_total_lower_bound,
                "legs_returned": legs_returned,
                "legs_total": legs_total,
                "truncated": truncated,
                "ordered": True,
                "exact": exact,
                "more_transactions_available": txs_truncated,
                "receipt_statuses": receipt_statuses,
                "unresolved_hashes": unresolved_hashes,
                "decode_failures": receipt_decode_failures,
                "raw_receipt_evidence": [
                    {
                        "transaction_hash": row[0],
                        "receipt_sha256": row[2],
                        "logs_sha256": row[3],
                        "retrieved_at": row[8],
                    }
                    for row in raw_receipt_rows
                ],
                # None, not 0.0, where no price plane applies. The nested
                # coverage.usd already reports unknown; these two flat compat
                # fields would otherwise say "$0.00 known" and "0% priced" for
                # the same legs — a verified-looking zero contradicting an
                # explicit unknown. `None` means unknown; `0` means a verified
                # zero (forensics.py).
                "known_usd_total": (
                    round(known_usd_total, 6) if is_gnosis else None
                ),
                "unpriced_leg_count": unknown_usd_rows,
                "usd_coverage": (
                    None
                    if not legs_returned or not is_gnosis
                    else (legs_returned - unknown_usd_rows) / legs_returned
                ),
            }
        )

        if receipt_only and no_answer_for_requested:
            return _failed_transaction_result(
                view_id=view_id,
                record=record,
                tx_state={
                    **state_tx,
                    "operation": "receipt",
                    "requested_hash": requested_hashes[0]
                    if requested_hashes
                    else None,
                },
                scope=scope,
                warnings=warnings,
                summary="Selected transaction receipt could not be verified; "
                "the accepted discovery list and receipt evidence were preserved.",
                request_channel="transactions.receipt",
            )

        transaction_datasets = {
            "tx_nodes": dataset_from_rows(
                constants.TX_LEG_NODES_COLUMNS, node_rows, "tx_nodes"
            ),
            "tx_legs": dataset_from_rows(
                constants.TX_LEG_EDGES_COLUMNS, leg_edge_rows, "tx_legs"
            ),
            "tx_raw_receipts": dataset_from_rows(
                constants.TX_RAW_RECEIPTS_COLUMNS,
                raw_receipt_rows,
                "tx_raw_receipts",
            ),
            "tx_context": dataset_from_rows(
                _TX_CONTEXT_COLUMNS, transaction_context_rows, "tx_context"
            ),
        }
        if not receipt_only or preserved_tx_list is None:
            transaction_datasets["tx_list"] = dataset_from_rows(
                constants.TX_LIST_COLUMNS, tx_list_rows, "tx_list"
            )

        generated_transaction_state = {
            "operation": operation,
            "tx_hashes": hashes,
            "query_kind": query_kind,
            "query_hashes": list(query_hashes),
            "result_hashes": list(hashes),
            "query": query_contract(),
            "results": {
                "hashes": list(hashes),
                "selected_hash": hashes[0] if hashes else None,
            },
            "seed": seed,
            "expanded": (
                sorted({*(state_tx.get("expanded") or []), expand})
                if expand
                else list(state_tx.get("expanded") or [])
            ),
            "counterparties": cps,
            "range_days": (
                0 if explicit_hash_request or all_history_address_request else days
            ),
            "t0": (
                ""
                if explicit_hash_request or all_history_address_request
                else t0.strftime("%Y-%m-%d %H:%M:%S")
            ),
            "t1": (
                ""
                if explicit_hash_request or all_history_address_request
                else t1.strftime("%Y-%m-%d %H:%M:%S")
            ),
            "max_txs": limit_txs,
            "tokens": tokens or [],
            "min_usd": float(min_usd or 0),
            "tx_count": len(tx_rank),
            "leg_count": legs_returned,
            # Persist the resolved chain so a follow-up call (and the UI's
            # picker) stays on it without re-passing `chain` every time.
            "chain_id": chain_id,
            "scope": scope,
            "receipt_scope": scope if receipt_only else None,
            "last_attempt": None,
        }
        preserved_address_query = bool(
            receipt_only
            and preserved_tx_list is not None
            and isinstance(preserved_discovery.get("query"), dict)
            and preserved_discovery["query"].get("kind") == "address"
        )
        if preserved_address_query:
            discovery_scope = preserved_discovery.get("discovery_scope") or state_tx.get(
                "scope"
            )
            prior_hashes = list(preserved_discovery.get("result_hashes") or [])
            generated_transaction_state.update(preserved_discovery)
            generated_transaction_state.update(
                {
                    "operation": "receipt",
                    "tx_hashes": [],
                    "query_kind": "address_discovery",
                    "result_hashes": prior_hashes,
                    "results": {
                        "hashes": prior_hashes,
                        "selected_hash": hashes[0] if hashes else None,
                    },
                    "tx_count": len(prior_hashes),
                    "leg_count": legs_returned,
                    "scope": discovery_scope,
                    "discovery_scope": discovery_scope,
                    "receipt_scope": scope,
                    "last_attempt": None,
                }
            )

        dataset_scopes = {
            "tx_nodes": scope_id,
            "tx_legs": scope_id,
            "tx_raw_receipts": scope_id,
            "tx_context": scope_id,
        }
        if not receipt_only or preserved_tx_list is None:
            dataset_scopes["tx_list"] = scope_id

        # Data loader: writes ONLY its own namespace + datasets. `mode` and
        # `selection` belong to explicit mode commands (which bump
        # mode_revision), so a slow load can never yank the user's tab.
        committed = mini_apps.commit_view_update(
            view_id,
            request_channel=(
                "transactions.receipt"
                if explicit_hash_request
                else "transactions.discovery"
            ),
            request_id=request_id,
            guard_channels=("transactions",),
            datasets=transaction_datasets,
            state_patch={
                "transactions": {
                    **generated_transaction_state,
                },
                "dataset_scopes": dataset_scopes,
                "warnings": warnings,
            },
        )

        updated = mini_apps.snapshot_view(view_id)
        assert updated is not None
        if not committed:
            return mini_apps.payload_to_call_tool_result(
                build_payload(updated),
                summary_text="Transaction request was superseded by a newer request.",
            )
        return mini_apps.payload_to_call_tool_result(
            build_payload(updated),
            summary_text=(
                (
                    f"No standard ERC-20 Transfer transaction was found for "
                    f"{short_id(seed)} across execution history plus RPC head through "
                    f"block {data_horizon}"
                    if empty_discovery_verified
                    else (
                        f"Opened {len(tx_rank)} transaction(s): {legs_returned} transfer "
                        f"leg(s) across {len(node_rows)} participant(s)"
                    )
                )
                + ("" if scope["exact"] else " (PARTIAL — see scope)")
                + "."
            ),
        )

    mini_apps.mark_app_only("load_graph_transactions")
    return {"load_graph_transactions": load_graph_transactions}
