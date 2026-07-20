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
     complete address index plus its uncovered RPC head (stored execution logs
     remain a rollout fallback while the additive index is absent)
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

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.semantic.tx_queries import (
    BURN_ADDRESSES,
    CHAIN_LOG_RELATIONS,
    PRICES_RELATION,
    TOKENS_META_RELATION,
    TX_ADDRESS_INDEX_RELATION,
    build_all_history_tx_discovery_chunk_sql,
    build_data_horizon_sql,
    build_indexed_tx_discovery_sql,
    build_indexed_tx_membership_sql,
    build_leg_total_sql,
    build_legs_sql,
    build_latest_indexed_activity_sql,
    build_token_contract_sql,
    build_tx_discovery_sql,
    build_tx_index_horizon_sql,
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
from .state import build_payload, dataset_from_rows, short_id
from .ui_tools import _normalize_node_id

logger = logging.getLogger(__name__)


def _resolve_tx_blocks(hashes: list[str]) -> tuple[dict[str, int], list[str]]:
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
        client = RpcRouter.from_settings().standard
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
_address_rpc_cache: dict[str, tuple[int, list[list[Any]]]] = {}
_GNOSIS_CHAIN_GENESIS_UTC = datetime(2018, 10, 8)
_MAX_RPC_DIRECT_TAIL_BLOCKS = 10_000


def _raw_preview(value: Any, limit: int = 130) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _legs_from_receipts(
    hashes: list[str],
    *,
    raw_receipt_rows: list[list[Any]] | None = None,
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
        client = RpcRouter.from_settings().standard
    except Exception as exc:  # pragma: no cover - config dependent
        logger.info("tx mode: RPC unavailable, falling back to SQL: %s", exc)
        return rows, list(hashes), statuses, blocks, decode_failures
    block_timestamps: dict[int, str] = {}
    for h in hashes:
        try:
            rec = client.request("eth_getTransactionReceipt", [_hex0x(h)])
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

    rpc_router = router or RpcRouter.from_settings()
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
            cached = _address_rpc_cache.get(normalized)
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
            _address_rpc_cache[normalized] = (
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
    max_blocks: int = _MAX_RPC_DIRECT_TAIL_BLOCKS,
    max_workers: int = 8,
) -> list[list[Any]]:
    """Discover direct sender/recipient transactions in a small RPC head gap.

    Standard JSON-RPC has no address index. Scanning full blocks is therefore
    permitted only for the bounded gap after the DBT index watermark; it is
    never a full-history fallback. Transfer-log discovery runs separately and
    the two result sets are merged by transaction hash.
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
            f"{max_blocks}. Refresh the address index before retrying."
        )

    client = (router or RpcRouter.from_settings()).standard

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
    """Merge direct/index/Transfer discoveries without inventing evidence."""
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


def _discover_address_transactions_execution(
    ch: ClickHouseManager,
    address: str,
    *,
    through: str,
    limit: int,
    max_workers: int = 4,
) -> tuple[list[list[Any]], int | None, bool, int]:
    """Use stored execution logs as a newest-first address index.

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

    effective_limit = max(1, int(limit))

    chunks: list[tuple[datetime, datetime]] = []
    cursor = _GNOSIS_CHAIN_GENESIS_UTC
    while cursor < end:
        # The raw tables are ordered by time/block. Seven-day predicates are
        # the measured fast path (~1.2s) and avoid paying a 30s timeout before
        # adaptive splitting. These pages still tile genesis→horizon exactly.
        chunk_end = min(cursor + timedelta(days=7), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end

    def read_chunk(bounds: tuple[datetime, datetime]) -> tuple[list[list[Any]], int]:
        lo, hi = bounds
        sql, params = build_all_history_tx_discovery_chunk_sql(
            address_ids=[address],
            t0=lo.strftime("%Y-%m-%d %H:%M:%S"),
            t1_exclusive=hi.strftime("%Y-%m-%d %H:%M:%S"),
            limit=effective_limit,
        )
        try:
            result = _run(ch, sql, params)
        except Exception as exc:
            duration = hi - lo
            message = str(exc).lower()
            if (
                duration > timedelta(days=1)
                and ("timeout" in message or "time limit" in message)
            ):
                midpoint = lo + duration / 2
                left_rows, left_total = read_chunk((lo, midpoint))
                right_rows, right_total = read_chunk((midpoint, hi))
                merged = [*left_rows, *right_rows]
                merged.sort(
                    key=lambda row: (
                        int(row[1] or 0),
                        int(row[2] or 0),
                        str(row[0]),
                    ),
                    reverse=True,
                )
                return merged[: effective_limit + 1], left_total + right_total
            raise
        rows = [list(row) for row in result.rows]
        total = int(rows[0][6] or 0) if rows else 0
        return [row[:6] for row in rows], total

    chunks.reverse()  # newest first; result admission is count-based, not time-based
    candidates: list[list[Any]] = []
    scanned_total = 0
    complete = True
    worker_count = max(1, min(max_workers, len(chunks))) if chunks else 1
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        for offset in range(0, len(chunks), worker_count):
            batch = chunks[offset : offset + worker_count]
            futures = [pool.submit(read_chunk, chunk) for chunk in batch]
            for future in as_completed(futures):
                rows, chunk_total = future.result()
                candidates.extend(rows)
                scanned_total += chunk_total
            if scanned_total > effective_limit:
                complete = False
                break

    candidates = sorted(
        {str(row[0]): list(row) for row in candidates}.values(),
        key=lambda row: (int(row[1] or 0), int(row[2] or 0), str(row[0])),
        reverse=True,
    )[: effective_limit + 1]
    return (
        candidates,
        scanned_total if complete else None,
        complete,
        scanned_total,
    )


def _hex0x(value: str) -> str:
    v = str(value or "").strip().lower()
    return v if v.startswith("0x") else f"0x{v}"


def _enrich_rpc_legs(
    ch: ClickHouseManager, rows: list[list[Any]]
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

    The current price relation is physically keyed by ``(symbol, date)`` and has
    no token-address column. We may therefore display its price as an explicitly
    partial enrichment, but must not pretend it is address-qualified: distinct
    token contracts can share a symbol.
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
    if tokens:
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
                # The physical source contract has only symbol/date/price. Do
                # not silently promote a symbol match to address-qualified
                # evidence: the live token metadata contains same-symbol
                # contracts (and that can change independently of this load).
                statuses["prices"] = "partial"
                message = (
                    "price enrichment is keyed by symbol and date because "
                    "dbt.int_execution_token_prices_daily exposes no "
                    "token_address; USD values cannot distinguish same-symbol "
                    "token contracts"
                )
                source_details["prices"]["error"] = message
                warnings.append(message)
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


def _run(ch: ClickHouseManager, sql: str, params: dict[str, Any]):
    return mini_apps.run_structured_query(
        ch, sql, database="dbt", parameters=params, requested_max_rows=100_000
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
    mini_apps.patch_view_state(
        view_id,
        patch,
    )
    updated = mini_apps.get_view(view_id)
    assert updated is not None
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
    ) -> CallToolResult:
        """Open transactions and return every transfer leg (Transactions mode).

        ``expand_node_id`` + ``after_block``/``after_index`` follows ONE address
        forward in chain order — the next transactions it took part in after a
        cursor — and ``merge`` unions them with what is already loaded. That is
        the "what did it do next?" step: the chain of custody continues instead
        of the view restarting.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        state_tx = dict(record.view_state.get("transactions") or {})
        requested_t0 = str(t0 or "").strip()
        requested_t1 = str(t1 or "").strip()
        hashes = [_hex0x(h) for h in (tx_hashes or []) if str(h).strip()]
        explicit_hash_request = bool(hashes)
        request_id = max(0, int(request_id or 0))
        scope_id = new_scope_id("transactions", request_id)
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
            not explicit_hash_request and seed and not expand and not cps and not tokens
        )
        rpc_address_request = bool(not explicit_hash_request and seed and expand)
        all_history_address_request = bool(
            plain_address_request or rpc_address_request
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

        days = 0 if all_history_address_request else (
            int(range_days) if range_days else constants.TX_DEFAULT_RANGE_DAYS
        )
        if not all_history_address_request:
            days = max(1, days)
        limit_txs = int(max_txs) if max_txs else constants.TX_DEFAULT_MAX_TXS
        limit_txs = max(1, min(limit_txs, constants.TX_MAX_TXS))

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
                hres = _run(ch, hsql, hparams)
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
                observed_horizons = [
                    value for value in chain_horizons.values() if value
                ]
                if observed_horizons:
                    horizon = max(observed_horizons)
            except Exception as exc:  # pragma: no cover - defensive
                logger.info("tx mode: horizon lookup failed: %s", exc)
                warnings.append(f"chain data-horizon lookup failed: {exc}")

        exact_window_request = bool(
            requested_t0
            and requested_t1
            and not explicit_hash_request
            and not expand
            and (cps or tokens)
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
                    "execution_logs_plus_rpc_head"
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
                "follow"
                if expand
                else (
                    "money_edge"
                    if cps or tokens
                    else "address_discovery"
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

        def failed_load(
            message: str, sources: list[dict[str, Any]]
        ) -> CallToolResult:
            failed_warnings = [*warnings, message]
            scope_t0 = None if explicit_hash_request or all_history_address_request else t0.strftime("%Y-%m-%d %H:%M:%S")
            scope_t1 = None if explicit_hash_request or all_history_address_request else t1.strftime("%Y-%m-%d %H:%M:%S")
            scope = forensic_scope(
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
                    "native xDAI value is not represented by ERC-20 Transfer logs",
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
        # hash -> block. Filled by discovery (free) or by RPC (pasted hashes).
        block_of: dict[str, int] = {}

        # ---- Choose the transactions ---------------------------------------
        execution_discovery_attempted = False
        rpc_discovery_attempted = False
        if not hashes and plain_address_request:
            index_contract = validate_source_contract(
                ch,
                TX_ADDRESS_INDEX_RELATION,
                (
                    "chain_id",
                    "participant_address",
                    "transaction_hash",
                    "activity_source",
                    "block_number",
                    "transaction_index",
                    "block_timestamp",
                    "token_addresses",
                    "token_counterparties",
                    "indexed_transfer_leg_count",
                    "source_horizon_block",
                    "indexed_at",
                ),
            )
            index_stage_rows: dict[str, list[Any]] = {}
            index_history_complete = False
            if index_contract["ok"]:
                try:
                    index_horizon_sql, index_horizon_params = (
                        build_tx_index_horizon_sql()
                    )
                    index_horizon_result = _run(
                        ch, index_horizon_sql, index_horizon_params
                    )
                    index_stage_rows = {
                        str(row[0]): list(row)
                        for row in index_horizon_result.rows
                        if row and row[0]
                    }
                    required_stages = {"transactions", "transfers"}
                    stage_names_complete = required_stages.issubset(index_stage_rows)
                    # A relation created from a recent preflight must never be
                    # mistaken for the complete historical index. Presence in
                    # the launch month is a runtime guard; the deployment
                    # reconciliation remains the stronger release gate.
                    history_guard = _GNOSIS_CHAIN_GENESIS_UTC + timedelta(days=31)
                    first_events: list[datetime] = []
                    block_horizons: list[int] = []
                    if stage_names_complete:
                        for stage in sorted(required_stages):
                            row = index_stage_rows[stage]
                            first_events.append(parse_window(str(row[1])))
                            block_horizons.append(int(row[3] or 0))
                    index_history_complete = bool(
                        stage_names_complete
                        and block_horizons
                        and min(block_horizons) > 0
                        and all(first <= history_guard for first in first_events)
                    )
                except Exception as exc:
                    warnings.append(
                        f"Address-index horizon validation failed; using stored "
                        f"execution logs instead: {exc}"
                    )
                    index_history_complete = False

            if index_contract["ok"] and index_history_complete:
                execution_discovery_attempted = True
                applied_window_source = "address_index_plus_rpc_head"
                stage_event_horizons = [
                    str(index_stage_rows[stage][2])
                    for stage in ("transactions", "transfers")
                ]
                stage_block_horizons = [
                    int(index_stage_rows[stage][3] or 0)
                    for stage in ("transactions", "transfers")
                ]
                indexed_event_horizon = min(stage_event_horizons)
                indexed_head = min(stage_block_horizons)
                sources.append(
                    source_record(
                        kind="dbt_aggregate",
                        name=TX_ADDRESS_INDEX_RELATION,
                        role="discovery",
                        status="ok",
                        horizon=indexed_event_horizon,
                        horizon_basis=(
                            "minimum per-stage max(block_timestamp); "
                            + ", ".join(
                                f"{stage}=block {index_stage_rows[stage][3]}"
                                for stage in ("transactions", "transfers")
                            )
                        ),
                        fetched_at=index_contract.get("freshness_checked_at"),
                    )
                )
                try:
                    index_sql, index_params = build_indexed_tx_discovery_sql(
                        address_ids=[seed],
                        t0=_GNOSIS_CHAIN_GENESIS_UTC.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        t1_exclusive=(
                            datetime.now(timezone.utc).replace(tzinfo=None)
                            + timedelta(seconds=1)
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                        tokens=[],
                        counterparty_ids=[],
                        limit=limit_txs,
                    )
                    index_result = _run(ch, index_sql, index_params)
                    raw_index_rows = [list(row) for row in index_result.rows]
                    index_total = (
                        int(raw_index_rows[0][6] or 0)
                        if raw_index_rows and len(raw_index_rows[0]) >= 7
                        else 0
                    )
                    index_rows = [row[:6] for row in raw_index_rows]
                except Exception as exc:
                    return failed_load(
                        f"Address-index query failed: {exc}",
                        [
                            {
                                **source,
                                "status": "error",
                                "error": str(exc),
                            }
                            if source.get("name") == TX_ADDRESS_INDEX_RELATION
                            else source
                            for source in sources
                        ],
                    )

                try:
                    transfer_tail_rows, rpc_head = (
                        _discover_address_transactions_rpc(
                            seed,
                            after_block=indexed_head + 1,
                        )
                    )
                    direct_tail_rows = _discover_address_direct_transactions_rpc(
                        seed,
                        after_block=indexed_head + 1,
                        through_block=rpc_head,
                    )
                except Exception as exc:
                    return failed_load(
                        f"RPC address tail after index block {indexed_head} failed: {exc}",
                        [
                            *sources,
                            source_record(
                                kind="rpc",
                                name="eth_getLogs + eth_getBlockByNumber",
                                role="discovery_tail",
                                status="error",
                                horizon=indexed_head,
                                horizon_basis=(
                                    "address-index block horizon to eth_blockNumber"
                                ),
                                error=str(exc),
                            ),
                        ],
                    )
                sources.extend(
                    [
                        source_record(
                            kind="rpc",
                            name="eth_getLogs",
                            role="discovery_tail",
                            status="ok",
                            horizon=rpc_head,
                            horizon_basis=(
                                f"standard ERC-20 Transfer logs in blocks "
                                f"{indexed_head + 1} through eth_blockNumber"
                            ),
                        ),
                        source_record(
                            kind="rpc",
                            name="eth_getBlockByNumber",
                            role="discovery_tail",
                            status="ok",
                            horizon=rpc_head,
                            horizon_basis=(
                                f"direct sender/recipient transactions in blocks "
                                f"{indexed_head + 1} through eth_blockNumber"
                            ),
                        ),
                    ]
                )
                tail_rows = _merge_tx_discovery_rows(
                    direct_tail_rows, transfer_tail_rows
                )
                indexed_tail_hashes: set[str] = set()
                membership_verified = True
                if tail_rows:
                    try:
                        membership_sql, membership_params = (
                            build_indexed_tx_membership_sql(
                                address_id=seed,
                                tx_hashes=[str(row[0]) for row in tail_rows],
                            )
                        )
                        membership_result = _run(
                            ch, membership_sql, membership_params
                        )
                        indexed_tail_hashes = {
                            _hex0x(str(row[0]))
                            for row in membership_result.rows
                            if row and row[0]
                        }
                    except Exception as exc:
                        membership_verified = False
                        warnings.append(
                            "RPC-tail overlap with the address index could not "
                            f"be counted exactly: {exc}"
                        )
                tail_new = [
                    row
                    for row in tail_rows
                    if _hex0x(str(row[0])) not in indexed_tail_hashes
                ]
                all_rows = _merge_tx_discovery_rows(index_rows, tail_new)
                discovered_total_matching = (
                    index_total + len(tail_new)
                    if membership_verified
                    else None
                )
                discovered_total_lower_bound = max(index_total, len(all_rows))
                txs_truncated = bool(
                    len(all_rows) > limit_txs
                    or (
                        discovered_total_matching is not None
                        and discovered_total_matching > limit_txs
                    )
                )
                rows = all_rows[:limit_txs]
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
                discovery_path = "address_index_rpc_tail"
                discovery_coverage_complete = True
            elif index_contract["ok"]:
                warnings.append(
                    "Address-index relation exists but does not contain both "
                    "full-history source stages; using stored execution logs."
                )
                sources.append(
                    source_record(
                        kind="dbt_aggregate",
                        name=TX_ADDRESS_INDEX_RELATION,
                        role="discovery_candidate",
                        status="partial",
                        horizon=None,
                        horizon_basis="per-stage history contract",
                        error="full-history transactions/transfers stages not verified",
                    )
                )

        if not hashes and plain_address_request and not execution_discovery_attempted:
            execution_discovery_attempted = True
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
            if invalid or not horizon:
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
                            else "execution-log horizon unavailable"
                        ),
                    )
                    for check in source_checks
                ]
                return failed_load(
                    "Execution-log address discovery source is unavailable",
                    failed_sources,
                )
            try:
                (
                    execution_rows,
                    execution_total,
                    execution_complete,
                    execution_scanned_total,
                ) = (
                    _discover_address_transactions_execution(
                        ch,
                        seed,
                        through=horizon,
                        limit=limit_txs,
                    )
                )
            except Exception as exc:
                return failed_load(
                    f"All-history execution-log address discovery failed: {exc}",
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

            indexed_head = max(
                (
                    block
                    for block in chain_block_horizons.values()
                    if block is not None
                ),
                default=0,
            )
            try:
                rpc_tail_rows, rpc_head = _discover_address_transactions_rpc(
                    seed,
                    after_block=indexed_head + 1,
                )
            except Exception as exc:
                return failed_load(
                    f"RPC tail discovery after execution block {indexed_head} failed: {exc}",
                    [
                        *sources,
                        source_record(
                            kind="rpc",
                            name="eth_getLogs",
                            role="discovery_tail",
                            status="error",
                            horizon=indexed_head,
                            horizon_basis="execution-log block horizon to RPC head",
                            error=str(exc),
                        ),
                    ],
                )
            sources.append(
                source_record(
                    kind="rpc",
                    name="eth_getLogs",
                    role="discovery_tail",
                    status="ok",
                    horizon=rpc_head,
                    horizon_basis=f"blocks {indexed_head + 1} through eth_blockNumber",
                )
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
            discovery_path = "execution_logs_rpc_tail"
            discovery_coverage_complete = execution_complete
            if not execution_complete:
                warnings.append(
                    f"Newest-first result admission stopped after establishing at "
                    f"least {discovered_total_lower_bound} matching transaction(s). "
                    "Older execution history was not needed to fill this result page; "
                    "increase Results to continue farther back."
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
            index_contract = validate_source_contract(
                ch,
                TX_ADDRESS_INDEX_RELATION,
                (
                    "chain_id",
                    "participant_address",
                    "transaction_hash",
                    "activity_source",
                    "block_number",
                    "transaction_index",
                    "block_timestamp",
                    "token_addresses",
                    "token_counterparties",
                    "indexed_transfer_leg_count",
                    "source_horizon_block",
                    "indexed_at",
                ),
                probe_horizon=True,
                horizon_column="block_timestamp",
            )
            if index_contract["ok"]:
                try:
                    index_horizon_sql, index_horizon_params = (
                        build_tx_index_horizon_sql()
                    )
                    index_horizon_result = _run(
                        ch, index_horizon_sql, index_horizon_params
                    )
                    transfer_horizon_row = next(
                        (
                            list(row)
                            for row in index_horizon_result.rows
                            if row and str(row[0]) == "transfers"
                        ),
                        None,
                    )
                    if transfer_horizon_row is None:
                        raise RuntimeError("transfers stage is not populated")
                    horizon = str(transfer_horizon_row[2])
                except Exception as exc:
                    index_contract = {
                        **index_contract,
                        "ok": False,
                        "error": f"address-index stage horizon failed: {exc}",
                    }

            if index_contract["ok"]:
                discovery_path = "address_index"
                sources.append(
                    source_record(
                        kind="dbt_aggregate",
                        name=TX_ADDRESS_INDEX_RELATION,
                        role="discovery",
                        status="ok",
                        horizon=horizon,
                        horizon_basis="transfers stage max(block_timestamp)",
                        fetched_at=index_contract.get("freshness_checked_at"),
                    )
                )
                dsql, dparams = build_indexed_tx_discovery_sql(
                    address_ids=[seed],
                    t0=t0.strftime("%Y-%m-%d %H:%M:%S"),
                    t1_exclusive=t1.strftime("%Y-%m-%d %H:%M:%S"),
                    tokens=tokens or [],
                    counterparty_ids=cps,
                    limit=limit_txs,
                    after_block=int(after_block or 0),
                    after_index=int(after_index if after_index is not None else -1),
                )
            else:
                # Compatibility while the index rolls out. This bounded raw
                # scan is explicitly disclosed and may fail at large windows;
                # its failure is never converted into verified absence.
                discovery_path = "raw_chain_fallback"
                warnings.append(
                    "Address-index relation is unavailable; discovery used a "
                    "bounded raw execution.logs union and may time out: "
                    + str(index_contract.get("error") or "source unavailable")
                )
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
                    sources = [
                        source_record(
                            kind="chain",
                            name=check["relation"],
                            role="discovery",
                            status="error" if not check["ok"] else "ok",
                            horizon=chain_horizons.get(check["relation"]),
                            horizon_basis="max(block_timestamp)",
                            error=check.get("error"),
                        )
                        for check in source_checks
                    ]
                    return failed_load(
                        "Transaction discovery source contract failed: "
                        + "; ".join(str(check.get("error")) for check in invalid),
                        sources,
                    )
                sources.extend(
                    source_record(
                        kind="chain",
                        name=check["relation"],
                        role="discovery",
                        status="ok",
                        horizon=chain_horizons.get(check["relation"]),
                        horizon_basis="max(block_timestamp)",
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
                dres = _run(ch, dsql, dparams)
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
            raw_rows = [list(r) for r in dres.rows]
            if discovery_path == "address_index" and raw_rows:
                discovered_total_matching = (
                    int(raw_rows[0][6] or 0)
                    if len(raw_rows[0]) >= 7
                    else None
                )
            rows = [row[:6] for row in raw_rows]
            if len(rows) > limit_txs:
                txs_truncated = True
                rows = rows[:limit_txs]
            if (
                discovered_total_matching is not None
                and discovered_total_matching > limit_txs
            ):
                txs_truncated = True
            hashes = [_hex0x(str(r[0])) for r in rows]
            for r in rows:
                blk = int(r[1] or 0)
                if blk:
                    block_of[_hex0x(str(r[0]))] = blk
            if expand and not hashes:
                warnings.append(
                    f"{short_id(expand)} has no further transactions after block "
                    f"{after_block} inside the {days}d window — the trail ends "
                    "here for this address, OR the window/data horizon cuts it "
                    "off. Widen the range before concluding the former."
                )
            if merge:
                # Union with what is already on screen, preserving chain order
                # and never dropping a previously loaded transaction.
                prior = [_hex0x(str(h)) for h in (state_tx.get("tx_hashes") or [])]
                hashes = prior + [h for h in hashes if h not in prior]
                if len(hashes) > constants.TX_MAX_TXS:
                    hashes = hashes[-constants.TX_MAX_TXS :]
                    warnings.append(
                        f"Transaction set capped at {constants.TX_MAX_TXS}; the "
                        "oldest were dropped to make room for the newly followed "
                        "ones."
                    )
            tx_list_rows = [
                [_hex0x(str(r[0])), int(r[1] or 0), int(r[2] or 0), str(r[3] or ""),
                 int(r[4] or 0), int(r[5] or 0)]
                for r in rows
            ]
            if discovery_path == "address_index" and not rows and seed:
                try:
                    previous_sql, previous_params = build_latest_indexed_activity_sql(
                        address_id=seed,
                        before=t0.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    previous_result = _run(ch, previous_sql, previous_params)
                    if previous_result.rows and previous_result.rows[0][0]:
                        latest_before_t0 = str(previous_result.rows[0][0])
                except Exception as exc:  # guidance only; not result authority
                    warnings.append(
                        f"Latest activity before the applied window could not be read: {exc}"
                    )

            # A query can complete successfully against a source whose ingest
            # horizon ends before the requested t1. That proves only "nothing
            # observed through the horizon", not absence over the requested
            # window. Exact address-discovery emptiness requires full temporal
            # coverage; receipt verification remains independent below.
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
                horizon_covers_window = bool(
                    horizon_dt is not None and horizon_dt >= t1
                )
                # A bounded raw scan is a compatibility fallback, not an
                # independently maintained address index. Even when its two
                # source horizons cover t1, it may time out or omit an
                # unindexed physical shard; it must never certify absence.
                discovery_coverage_complete = bool(
                    discovery_path == "address_index" and horizon_covers_window
                )
            except ValueError:
                discovery_coverage_complete = False
            if not discovery_coverage_complete:
                if discovery_path == "raw_chain_fallback":
                    warnings.append(
                        "The bounded raw-log compatibility fallback cannot verify "
                        "address absence independently. An empty result is not "
                        "verified absence."
                    )
                else:
                    warnings.append(
                        "Address discovery does not cover the complete requested "
                        f"window through {t1:%Y-%m-%d %H:%M:%S}; source horizon is "
                        f"{horizon or 'unknown'}. An empty result is not verified absence."
                    )
                for source in sources:
                    if source.get("role") == "discovery" and source.get("status") == "ok":
                        if discovery_path == "raw_chain_fallback":
                            source["status"] = "partial"
                            source["error"] = (
                                "bounded raw-log fallback is not independent "
                                "address-absence proof"
                            )
                        else:
                            source["status"] = "stale"
                            source["error"] = (
                                "source horizon does not cover requested t1"
                            )

        if not hashes:
            if all_history_address_request and discovery_coverage_complete:
                warnings.append(
                    f"No direct or standard ERC-20 Transfer transactions found "
                    f"for {short_id(seed)} across the complete stored history "
                    f"and uncovered RPC tail through block {horizon}."
                )
            else:
                warnings.append(
                    f"No transactions found for {short_id(seed)} in the applied scope"
                    + (f" with counterparty {short_id(cps[0])}" if cps else "")
                    + " — lower min USD or clear the token filter."
                )

        requested_hashes = list(hashes)

        # ---- Resolve blocks, then bound every subsequent query --------------
        # The log relations are ordered by block; an unbounded hash predicate is
        # a 30s timeout. Hashes discovered above already carry their block;
        # user-pasted hashes are resolved over RPC.
        # Legs for KNOWN transactions come from RPC receipts: ~155ms vs ~7s for
        # the equivalent SQL scan, and the receipt is authoritative (no
        # whitelist, no indexer lag). Plain-address hashes come from the address
        # index plus a bounded RPC head scan, or the disclosed raw-log rollout
        # fallback. SQL leg reads remain only a receipt fallback.
        rpc_rows: list[list[Any]] = []
        rpc_unresolved = list(hashes)
        receipt_statuses: dict[str, str] = {}
        receipt_blocks: dict[str, int] = {}
        receipt_decode_failures: list[dict[str, Any]] = []
        raw_receipt_rows: list[list[Any]] = []
        if hashes:
            (
                rpc_rows,
                rpc_unresolved,
                receipt_statuses,
                receipt_blocks,
                receipt_decode_failures,
            ) = _legs_from_receipts(
                hashes,
                raw_receipt_rows=raw_receipt_rows,
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
            resolved, unresolved = _resolve_tx_blocks(missing)
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

            fallback_hashes = [
                h for h in hashes if _hex0x(h) not in receipt_statuses
            ]
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
            ) = _enrich_rpc_legs(ch, raw_leg_rows)
            enrichment_failed = any(
                status not in {"ok", "not_needed"}
                for status in enrichment_status.values()
            )
            warnings.extend(enrichment_warnings)
            sources.extend(
                [
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
            warnings.append(
                f"Leg cap reached ({constants.TX_MAX_LEGS}): showing "
                f"{len(kept_hashes)} of {len(hashes)} transactions in full. "
                "Whole transactions are dropped rather than split — a partial "
                "transaction misreads as a different operation."
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
            "complete address index plus bounded RPC-head discovery"
            if discovery_path == "address_index_rpc_tail"
            else (
                "complete execution-log history plus RPC-head discovery"
                if discovery_coverage_complete
                else "newest-first execution-log result admission plus RPC-head discovery"
            )
        )
        scope = forensic_scope(
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
            known_usd=round(known_usd_total, 6),
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
                "native xDAI value is not represented by ERC-20 Transfer logs",
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
                "known_usd_total": round(known_usd_total, 6),
                "unpriced_leg_count": unknown_usd_rows,
                "usd_coverage": (
                    None
                    if not legs_returned
                    else (legs_returned - unknown_usd_rows) / legs_returned
                ),
            }
        )

        mini_apps.attach_dataset(
            view_id, "tx_nodes",
            dataset_from_rows(constants.TX_LEG_NODES_COLUMNS, node_rows, "tx_nodes"),
        )
        mini_apps.attach_dataset(
            view_id, "tx_legs",
            dataset_from_rows(constants.TX_LEG_EDGES_COLUMNS, leg_edge_rows, "tx_legs"),
        )
        mini_apps.attach_dataset(
            view_id, "tx_list",
            dataset_from_rows(constants.TX_LIST_COLUMNS, tx_list_rows, "tx_list"),
        )
        mini_apps.attach_dataset(
            view_id,
            "tx_raw_receipts",
            dataset_from_rows(
                constants.TX_RAW_RECEIPTS_COLUMNS,
                raw_receipt_rows,
                "tx_raw_receipts",
            ),
        )

        # Data loader: writes ONLY its own namespace + datasets. `mode` and
        # `selection` belong to explicit mode commands (which bump
        # mode_revision), so a slow load can never yank the user's tab.
        mini_apps.patch_view_state(
            view_id,
            {
                "transactions": {
                    "tx_hashes": hashes,
                    # Separate query authority from result hashes. The legacy
                    # tx_hashes field remains for old URLs/clients, but a
                    # discovery result must never turn into an explicit-hash
                    # query when the page reloads.
                    "query_kind": query_kind,
                    "query_hashes": list(query_hashes),
                    "result_hashes": list(hashes),
                    "query": query_contract(),
                    "results": {
                        "hashes": list(hashes),
                        "selected_hash": hashes[0] if hashes else None,
                    },
                    "seed": seed,
                    # Addresses already followed forward, so the UI can mark a
                    # node as walked and the analyst can see the trail taken.
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
                    "scope": scope,
                    "last_attempt": None,
                },
                "dataset_scopes": {
                    "tx_nodes": scope_id,
                    "tx_legs": scope_id,
                    "tx_list": scope_id,
                    "tx_raw_receipts": scope_id,
                },
                "warnings": warnings,
            },
        )

        updated = mini_apps.get_view(view_id)
        assert updated is not None
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
