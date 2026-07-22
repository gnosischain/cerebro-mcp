"""Transaction-grain SQL builders (Transactions mode).

EVIDENCE PLANE: selected transaction legs come from RPC receipts. Address
candidate discovery reads the existing historical/live execution transaction
and log relations in bounded pages; it does not require a separate dbt index.
Token metadata and USD prices are optional enrichment and can never remove a
receipt leg.

Why SQL is not receipt authority: ``int_execution_transfers_whitelisted_raw``
was the earlier leg candidate and is RETIRED — the file is ``.sqlx`` (dbt compiles only
``.sql``) and it is absent from the manifest, so it stopped being rebuilt and
froze ~12 days behind the chain. It also applied the token whitelist, which
silently dropped real transfers: a verified settlement showed **9** transfer
legs on-chain and only **7** through the model, while the UI claimed
"COMPLETE · 7 of 7". Raw logs are live and complete. Leg recovery never
references dbt: metadata and price enrichment happens in a separate, optional
pass after these queries return, so a missing enrichment relation cannot erase
a chain leg.

``execution_live`` runs ~800 blocks (~70 min) ahead of ``execution``, so both
log and transaction relations are unioned and de-duplicated on their natural
chain-position keys. Address discovery reads these existing relations in
newest-first bounded chunks and stops when the requested page is full; it does
not require a chain-wide DBT participant index.

STORAGE FORMAT — both cost hours to rediscover, so they are pinned here:
  * hex columns carry NO ``0x`` prefix and are lowercase, so an address sits in
    the LAST 40 chars of a 64-char topic word (``substring(topic, 25, 40)``);
  * ``topic3`` is ``NULL`` (not ``''``) for a 3-topic ERC-20 Transfer, and that
    NULL check is what excludes 4-topic ERC-721 Transfers.

PERFORMANCE — the tables are ordered by block, NOT by hash:
  * ``WHERE transaction_hash = …`` alone TIMES OUT at 30s (full scan). Every
    leg query must be bounded by ``block_number``; the caller resolves a bare
    hash to its block via RPC ``eth_getTransactionByHash`` first.
  * A ``block_timestamp``-bounded topic scan is fine as a disclosed fallback:
    7 days of one address is ~1.2s.
"""

from __future__ import annotations

from typing import Any

# Chain truth. `execution_live` is the hot tail; both are unioned + deduped.
CHAIN_LOG_RELATIONS = ("execution.logs", "execution_live.logs")
CHAIN_TRANSACTION_RELATIONS = (
    "execution.transactions",
    "execution_live.transactions",
)
# keccak("Transfer(address,address,uint256)"), stored without the 0x prefix.
TRANSFER_TOPIC0 = (
    "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
# dbt, ENRICHMENT ONLY — never the source of truth for whether a leg exists.
TOKENS_META_RELATION = "dbt.stg_pools__tokens_meta"
PRICES_RELATION = "dbt.int_execution_token_prices_daily"
CHAIN_ORDER = "block_number, transaction_index, log_index"

BURN_ADDRESSES = (
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
)


def _bare(value: str) -> str:
    """Lowercase hex without the 0x prefix — the on-disk format."""
    v = str(value or "").strip().lower()
    return v[2:] if v.startswith("0x") else v


def _bare_list(values: list[str]) -> list[str]:
    return [_bare(v) for v in values or [] if str(v or "").strip()]


def _addr_topic(address: str) -> str:
    """An address as a 32-byte topic word: 24 zero bytes + the 20-byte address."""
    return "0" * 24 + _bare(address)


def _union_legs_cte(where: str) -> str:
    """Both log relations under one predicate, de-duplicated on the natural key.

    ``execution_live`` overlaps ``execution`` for recent blocks, so without the
    GROUP BY every leg in the overlap would render twice.
    """
    parts = []
    for rel in CHAIN_LOG_RELATIONS:
        parts.append(
            f"""
        SELECT block_number, transaction_index, log_index, transaction_hash,
               block_timestamp, address, topic1, topic2, data
        FROM {rel}
        WHERE {where}"""
        )
    union = "\n        UNION ALL".join(parts)
    return f"""
    raw AS ({union}
    ),
    legs AS (
        SELECT block_number, transaction_index, log_index, transaction_hash,
               any(block_timestamp) AS block_timestamp,
               any(address)         AS token_bare,
               any(topic1)          AS topic1,
               any(topic2)          AS topic2,
               any(data)            AS data
        FROM raw
        GROUP BY block_number, transaction_index, log_index, transaction_hash
    )"""


def _union_transactions_cte(where: str) -> str:
    """Historical + live transaction envelopes under one bounded predicate."""
    parts = []
    for rel in CHAIN_TRANSACTION_RELATIONS:
        parts.append(
            f"""
        SELECT block_number, transaction_index, transaction_hash,
               block_timestamp, from_address, to_address, insert_version
        FROM {rel}
        WHERE {where}"""
        )
    union = "\n        UNION ALL".join(parts)
    return f"""
    raw_transactions AS ({union}
    ),
    direct_transactions AS (
        SELECT
            block_number,
            transaction_index,
            transaction_hash,
            argMax(block_timestamp, insert_version) AS block_timestamp
        FROM raw_transactions
        GROUP BY block_number, transaction_index, transaction_hash
    )"""


# Shared raw projection: decode only fields carried by the chain logs. Do not
# add dbt joins here. This query is the last-resort evidence path when receipts
# are unavailable, so making it depend on enrichment relations would turn a
# missing symbol or price table into missing forensic evidence.
_RAW_LEG_SELECT = """
    SELECT
        concat('0x', legs.transaction_hash)          AS transaction_hash,
        legs.log_index                               AS log_index,
        legs.block_number                            AS block_number,
        legs.transaction_index                       AS transaction_index,
        legs.block_timestamp                         AS block_timestamp,
        -- COLUMN ORDER IS A CONTRACT: the caller reads these positionally
        -- (r[5]=source, r[6]=target, r[7]=token). Reordering silently made the
        -- token address render as the sender.
        concat('0x', substring(legs.topic1, 25, 40)) AS source_id,
        concat('0x', substring(legs.topic2, 25, 40)) AS target_id,
        concat('0x', legs.token_bare)                AS token_address,
        toString(reinterpretAsUInt256(reverse(unhex(legs.data))))
                                                      AS raw_amount
    FROM legs
"""


def build_legs_sql(
    *,
    tx_hashes: list[str],
    block_lo: int,
    block_hi: int,
    limit: int,
) -> tuple[str, dict[str, Any]]:
    """Every raw ERC-20 transfer leg of the named transactions, in chain order.

    ``block_lo``/``block_hi`` are MANDATORY: the relations are ordered by block,
    so an unbounded hash predicate is a full scan that times out at 30s. The
    caller resolves each hash to its block (RPC) before calling. The returned
    columns contain no metadata or prices; callers enrich them separately and
    must retain these rows if enrichment is unavailable.
    """
    if block_hi < block_lo:
        block_lo, block_hi = block_hi, block_lo
    params: dict[str, Any] = {
        "hashes": _bare_list(tx_hashes),
        "lo": int(block_lo),
        "hi": int(block_hi),
        "t0": TRANSFER_TOPIC0,
        "lim": int(limit) + 1,
    }
    where = (
        "block_number >= {lo:UInt64} AND block_number <= {hi:UInt64}"
        " AND transaction_hash IN {hashes:Array(String)}"
        " AND topic0 = {t0:String}"
        # 3-topic Transfer only; a 4th topic means ERC-721 (tokenId indexed).
        " AND topic3 IS NULL"
    )
    sql = f"""
    WITH{_union_legs_cte(where)}
    {_RAW_LEG_SELECT}
    ORDER BY {CHAIN_ORDER}
    LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_tx_discovery_sql(
    *,
    address_ids: list[str],
    t0: str,
    t1_exclusive: str,
    min_usd: float,
    tokens: list[str] | None,
    counterparty_ids: list[str] | None,
    limit: int,
    after_block: int = 0,
    after_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """Pick WHICH transactions to open, and report the block range they span.

    Bounded by ``block_timestamp`` (the sort prefix), which is what keeps a
    topic scan at ~1.2s for 7 days of one address.

    ``after_block``/``after_index`` switch to FORWARD traversal: the next
    transactions after a cursor, OLDEST first. The sort must flip — taking the
    newest N and calling them "next" would skip the intervening activity, which
    is exactly the chain of custody being followed. The cursor compares
    ``(block_number, transaction_index)`` lexicographically so transactions
    earlier in the same block are not re-admitted.

    ``min_usd`` is deliberately NOT applied here: USD needs the enrichment join,
    and filtering transactions by value before the analyst has seen them is how
    the Flows budget hid 83% of counterparties. The caller filters after load.
    """
    params: dict[str, Any] = {
        "topics": [_addr_topic(a) for a in address_ids or []],
        "ts0": t0,
        "ts1": t1_exclusive,
        "t0": TRANSFER_TOPIC0,
        "lim": int(limit) + 1,
    }

    where = (
        "block_timestamp >= {ts0:DateTime} AND block_timestamp < {ts1:DateTime}"
        " AND topic0 = {t0:String}"
        " AND topic3 IS NULL"
        " AND (topic1 IN {topics:Array(String)} OR topic2 IN {topics:Array(String)})"
    )
    forward = after_block > 0
    if forward:
        params["ab"] = int(after_block)
        params["ai"] = int(after_index)
        where += (
            " AND (block_number > {ab:UInt64}"
            " OR (block_number = {ab:UInt64}"
            " AND transaction_index > {ai:Int64}))"
        )

    having: list[str] = []
    if tokens:
        params["tokens"] = _bare_list(tokens)
        having.append("countIf(token_bare IN {tokens:Array(String)}) > 0")
    if counterparty_ids:
        params["cps"] = [_addr_topic(c) for c in counterparty_ids]
        # Both parties must appear ANYWHERE in the transaction, not on one leg:
        # a swap routed through a settlement contract never has payer and payee
        # on the same leg, so requiring that would hide the interesting ones.
        having.append(
            "countIf(topic1 IN {cps:Array(String)}"
            " OR topic2 IN {cps:Array(String)}) > 0"
        )
    having_clause = (" HAVING " + " AND ".join(having)) if having else ""

    order = (
        "block_number ASC, transaction_index ASC, transaction_hash"
        if forward
        else "block_number DESC, transaction_index DESC, transaction_hash"
    )

    sql = f"""
    WITH{_union_legs_cte(where)}
    SELECT
        concat('0x', transaction_hash) AS transaction_hash,
        min(block_number)       AS block_number,
        min(transaction_index)  AS transaction_index,
        min(block_timestamp)    AS block_timestamp,
        count()                 AS leg_count,
        uniqExact(token_bare)   AS token_count
    FROM legs
    GROUP BY transaction_hash{having_clause}
    ORDER BY {order}
    LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_all_history_tx_discovery_chunk_sql(
    *,
    address_ids: list[str],
    t0: str,
    t1_exclusive: str,
    limit: int,
    before_block: int | None = None,
    before_index: int | None = None,
    before_hash: str | None = None,
    activity_kinds: list[str] | None = None,
    tokens: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Discover address transactions in one internal storage partition.

    The caller tiles these half-open UTC chunks from chain genesis through the
    common execution-table horizon. Token candidates come from indexed Transfer
    topics; normal/native candidates come from transaction envelopes. Chunks
    are a storage transport detail, not an analyst time filter: completeness
    requires every chunk plus the RPC tail to succeed.
    """
    normalized_kinds = {
        str(kind).strip().lower() for kind in (activity_kinds or ["direct", "erc20"])
    }
    invalid_kinds = normalized_kinds - {"direct", "erc20"}
    if invalid_kinds:
        raise ValueError(
            "activity_kinds must contain only 'direct' and/or 'erc20'"
        )
    if not normalized_kinds:
        raise ValueError("activity_kinds cannot be empty")

    # A token predicate describes Transfer-log participation. It must never
    # accidentally retain normal/native transaction candidates merely because
    # the same address also submitted a transaction in the selected period.
    if tokens:
        normalized_kinds.discard("direct")
        normalized_kinds.add("erc20")

    params: dict[str, Any] = {
        "topics": [_addr_topic(address) for address in address_ids or []],
        "addresses": _bare_list(address_ids),
        "ts0": t0,
        "ts1": t1_exclusive,
        "t0": TRANSFER_TOPIC0,
        "lim": int(limit) + 1,
    }
    log_where = (
        "block_timestamp >= {ts0:DateTime} AND block_timestamp < {ts1:DateTime}"
        " AND topic0 = {t0:String}"
        " AND topic3 IS NULL"
        " AND (topic1 IN {topics:Array(String)} OR topic2 IN {topics:Array(String)})"
    )
    transaction_where = (
        "block_timestamp >= {ts0:DateTime} AND block_timestamp < {ts1:DateTime}"
        " AND transaction_hash IS NOT NULL"
        " AND (from_address IN {addresses:Array(String)}"
        " OR to_address IN {addresses:Array(String)})"
    )
    if tokens:
        params["tokens"] = _bare_list(tokens)
        log_where += " AND address IN {tokens:Array(String)}"

    if before_block is not None:
        params.update(
            {
                "before_block": int(before_block),
                "before_index": int(before_index if before_index is not None else -1),
                "before_hash": _bare(str(before_hash or "f" * 64)),
            }
        )
        before_predicate = (
            " AND (block_number < {before_block:UInt64}"
            " OR (block_number = {before_block:UInt64}"
            " AND (transaction_index < {before_index:Int64}"
            " OR (transaction_index = {before_index:Int64}"
            " AND transaction_hash < {before_hash:String}))))"
        )
        log_where += before_predicate
        transaction_where += before_predicate

    candidate_parts: list[str] = []
    if "erc20" in normalized_kinds:
        candidate_parts.append("SELECT * FROM transfer_candidates")
    if "direct" in normalized_kinds:
        candidate_parts.append("SELECT * FROM direct_candidates")
    candidate_union = "\n        UNION ALL\n        ".join(candidate_parts)
    sql = f"""
    WITH{_union_legs_cte(log_where)},
    {_union_transactions_cte(transaction_where)},
    transfer_candidates AS (
        SELECT
            transaction_hash,
            min(block_number) AS discovered_block_number,
            min(transaction_index) AS discovered_transaction_index,
            min(block_timestamp) AS discovered_block_timestamp,
            count() AS discovered_leg_count,
            uniqExact(token_bare) AS discovered_token_count
        FROM legs
        GROUP BY transaction_hash
    ),
    direct_candidates AS (
        SELECT
            transaction_hash,
            min(block_number) AS discovered_block_number,
            min(transaction_index) AS discovered_transaction_index,
            min(block_timestamp) AS discovered_block_timestamp,
            toUInt64(0) AS discovered_leg_count,
            toUInt64(0) AS discovered_token_count
        FROM direct_transactions
        GROUP BY transaction_hash
    ),
    candidates AS (
        {candidate_union}
    ),
    grouped AS (
        SELECT
            transaction_hash,
            min(discovered_block_number) AS discovered_block_number,
            min(discovered_transaction_index) AS discovered_transaction_index,
            min(discovered_block_timestamp) AS discovered_block_timestamp,
            max(discovered_leg_count) AS discovered_leg_count,
            max(discovered_token_count) AS discovered_token_count
        FROM candidates
        GROUP BY transaction_hash
    )
    SELECT
        concat('0x', transaction_hash) AS transaction_hash,
        discovered_block_number AS block_number,
        discovered_transaction_index AS transaction_index,
        toString(discovered_block_timestamp) AS block_timestamp,
        discovered_leg_count AS leg_count,
        discovered_token_count AS token_count,
        count() OVER () AS chunk_transaction_total
    FROM grouped
    ORDER BY discovered_block_number DESC,
             discovered_transaction_index DESC,
             transaction_hash DESC
    LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_leg_total_sql(
    *, tx_hashes: list[str], block_lo: int, block_hi: int
) -> tuple[str, dict[str, Any]]:
    """True leg count for the requested transactions.

    The scope contract compares this against what was returned, so the UI can
    never present a truncated leg set as a whole transaction.
    """
    if block_hi < block_lo:
        block_lo, block_hi = block_hi, block_lo
    where = (
        "block_number >= {lo:UInt64} AND block_number <= {hi:UInt64}"
        " AND transaction_hash IN {hashes:Array(String)}"
        " AND topic0 = {t0:String}"
        " AND topic3 IS NULL"
    )
    sql = f"""
    WITH{_union_legs_cte(where)}
    SELECT count() AS legs_total, uniqExact(transaction_hash) AS tx_total
    FROM legs
    """
    return sql, {
        "hashes": _bare_list(tx_hashes),
        "lo": int(block_lo),
        "hi": int(block_hi),
        "t0": TRANSFER_TOPIC0,
    }


def build_token_contract_sql(
    node_ids: list[str], *, block_lo: int, block_hi: int
) -> tuple[str, dict[str, Any]]:
    """Which of these addresses EMITTED a Transfer, i.e. are ERC-20 contracts?

    An address that emits Transfer is the token itself. A leg whose endpoint is
    a token contract is a mint/burn or a reserve/vault payout — NOT a payment to
    a counterparty wallet. (The mint/burn counterparty is the ZERO address; see
    BURN_ADDRESSES.) Conflating the two invalidated an entire earlier
    investigation, so the distinction is drawn from the chain rather than from a
    whitelist that would miss unlisted tokens.
    """
    if block_hi < block_lo:
        block_lo, block_hi = block_hi, block_lo
    # The output alias must NOT be `address`: an alias shadows the column of the
    # same name, so `WHERE address IN {ids}` would compare the PREFIXED value
    # ('0xedbc…') against the bare ids ('edbc…') and silently match nothing.
    # (Same alias-shadowing trap as `min(block_number) AS block_number` raising
    # ILLEGAL_AGGREGATION in the discovery query.)
    parts = []
    for rel in CHAIN_LOG_RELATIONS:
        parts.append(
            f"""
        SELECT DISTINCT concat('0x', t.address) AS token_contract
        FROM {rel} AS t
        WHERE t.block_number >= {{lo:UInt64}} AND t.block_number <= {{hi:UInt64}}
          AND t.topic0 = {{t0:String}}
          AND t.address IN {{ids:Array(String)}}"""
        )
    sql = " UNION DISTINCT".join(parts)
    return sql, {
        "ids": _bare_list(node_ids),
        "lo": int(block_lo),
        "hi": int(block_hi),
        "t0": TRANSFER_TOPIC0,
    }


def build_data_horizon_sql() -> tuple[str, dict[str, Any]]:
    """Independent block-timestamp watermark for each discovery relation.

    "Empty" must never be reported as "nothing happened" when it actually means
    the window sits past the ingest horizon.  Returning the two clocks instead
    of only their maximum also prevents both source records being stamped with
    a synthetic shared horizon.
    """
    parts = [
        f"SELECT '{rel}' AS relation, max(block_timestamp) AS horizon, "
        f"max(block_number) AS block_horizon FROM {rel}"
        f" WHERE block_timestamp >= now() - INTERVAL 2 DAY"
        for rel in (*CHAIN_LOG_RELATIONS, *CHAIN_TRANSACTION_RELATIONS)
    ]
    return " UNION ALL ".join(parts), {}
