"""Fund-flow forensics SQL builders (Flows mode).

Domain-specific by design — the relations below are Gnosis warehouse models
(graph_profiles.py's charter forbids per-domain knowledge, so this lives in
its own module). All queries are fully parameterized; addresses are
lowercased as PARAMETERS (columns are stored lowercase — never wrap them in
``lower()``, it defeats indexes).

Base model: ``int_execution_transfers_whitelisted_daily`` — DAILY-grain
whitelisted transfers, one row per (date, token, from, to) with ``amount_raw``
and ``transfer_count``. USD and human amounts are derived here via the token
metadata + price join, because the model carries neither.

Why not the transaction-grain sibling: ``int_execution_transfers_whitelisted_raw``
was RETIRED (renamed ``.sqlx``, absent from the manifest) during OOM
remediation, froze ~12 days behind the chain, and its physical table has since
been DROPPED — queries against it now fail outright. ``_daily`` is live, in the
manifest, 25x smaller, and already at exactly the grain a hop aggregation
needs.

GRAIN CONSEQUENCE: ``first_seen``/``last_seen`` are DATES, not timestamps. A
same-day ordering question cannot be answered from this relation — use
Transactions mode, which reads the chain per transfer leg.

Coverage limit: the effective-dated warehouse token whitelist.  Its size is a
property of the applied scope, never a hardcoded UI constant, and wholly
unpriced rows remain structurally visible when the USD minimum is zero.

Bridge attribution: ``int_execution_bridges_address_flows_daily`` is derived
from the same transfer population.  It may annotate an admitted user→bridge
transfer but must never create a second movement or imply a destination-chain
receipt. Reversed bridge→user edges are never synthesized.
"""

from __future__ import annotations

from typing import Any

from cerebro_mcp.semantic.address_semantics import STRUCTURAL_TERMINALS

FLOWS_RELATION = "int_execution_transfers_whitelisted_daily"
# Enrichment only — these supply decimals/symbol/price, never whether an edge
# exists.
TOKENS_META_RELATION = "stg_pools__tokens_meta"
PRICES_RELATION = "int_execution_token_prices_daily"
# Per-transaction evidence comes from the CHAIN: the daily model has no
# transaction_hash, and a forensic drill-down without one is useless.
CHAIN_LOG_RELATIONS = ("execution.logs", "execution_live.logs")
TRANSFER_TOPIC0 = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Human amount and USD, derived once so every builder agrees.
_AMOUNT_EXPR = "toFloat64(d.amount_raw) / pow(10, coalesce(m.decimals, 18))"
_PRICED_USD_EXPR = f"{_AMOUNT_EXPR} * p.price"
# ``sum(Nullable)`` is not a sufficient unknown marker: aggregate defaults
# can collapse an all-NULL group to zero.  Explicitly distinguish "no priced
# observations" from a measured zero while retaining the known subtotal for
# mixed-price groups.
_AGG_USD_EXPR = (
    "if(countIf(coalesce(p.price_found, 0) = 1) = 0, "
    "CAST(NULL AS Nullable(Float64)), "
    f"sumIf({_PRICED_USD_EXPR}, p.price_found = 1))"
)
_UNKNOWN_USD_ROWS_EXPR = "countIf(coalesce(p.price_found, 0) = 0)"
# The minimum is meaningful only for rows with a measured USD value. Wholly
# unpriced aggregates remain admissible in their own categorical lane at every
# threshold; excluding them would turn missing enrichment into hidden topology.
# Keep this predicate shared by Money Trail and Direct activity over time.
_USD_ELIGIBILITY = "amount_usd >= {min_usd:Float64} OR isNull(amount_usd)"
_ENRICH_JOINS = f"""
        LEFT JOIN {TOKENS_META_RELATION} AS m ON m.token_address = d.token_address
        LEFT JOIN (
            SELECT symbol, date, price, toUInt8(1) AS price_found
            FROM {PRICES_RELATION}
        ) AS p
               ON p.symbol = m.token AND p.date = d.date"""
BRIDGES_RELATION = "int_execution_bridges_address_flows_daily"
LABELS_RELATION = "int_crawlers_data_labels"
GP_CANONICAL_RELATION = "int_execution_gpay_safe_canonical"
GP_REFUNDS_RELATION = "int_execution_gpay_refunds"


def _norm_ids(ids: list[str]) -> list[str]:
    return [str(s).strip().lower() for s in ids if s and str(s).strip()]


def _enrichment_parts(
    *, metadata_available: bool, prices_available: bool
) -> dict[str, str]:
    """SQL fragments that never reference an unavailable enrichment relation.

    Transfer topology comes exclusively from ``FLOWS_RELATION``. Metadata and
    prices may enrich that topology, but their absence must leave a query that
    still returns every raw transfer group with nullable amount/USD fields.
    """

    use_metadata = bool(metadata_available)
    use_prices = bool(prices_available and use_metadata)
    joins = ""
    if use_metadata:
        joins += (
            f"\n        LEFT JOIN {TOKENS_META_RELATION} AS m "
            "ON m.token_address = d.token_address"
        )
    if use_prices:
        joins += f"""
        LEFT JOIN (
            SELECT symbol, date, price, toUInt8(1) AS price_found
            FROM {PRICES_RELATION}
        ) AS p
               ON p.symbol = m.token AND p.date = d.date"""

    if use_metadata:
        missing_metadata = "empty(coalesce(m.token_address, ''))"
        normalized = (
            "if(countIf(" + missing_metadata + ") > 0, "
            "CAST(NULL AS Nullable(Float64)), "
            f"sum({_AMOUNT_EXPR}))"
        )
        symbol = "any(m.token)"
        unknown_decimals = f"countIf({missing_metadata})"
    else:
        normalized = "CAST(NULL AS Nullable(Float64))"
        symbol = "CAST('' AS String)"
        unknown_decimals = "count()"

    if use_prices:
        known_usd = _AGG_USD_EXPR
        priced_rows = (
            "countIf(p.price_found = 1 "
            "AND notEmpty(coalesce(m.token_address, '')))"
        )
        unknown_price = (
            "countIf(coalesce(p.price_found, 0) = 0 "
            "AND notEmpty(coalesce(m.token_address, '')))"
        )
    else:
        known_usd = "CAST(NULL AS Nullable(Float64))"
        priced_rows = "toUInt64(0)"
        # If metadata is present, every source row lacks price enrichment. If
        # metadata itself is absent, unknown-decimal rows already express why
        # quantitative enrichment is unavailable and must not be double-counted.
        unknown_price = "count()" if use_metadata else "toUInt64(0)"

    return {
        "joins": joins,
        "symbol": symbol,
        "normalized": normalized,
        "known_usd": known_usd,
        "priced_rows": priced_rows,
        "unknown_price_rows": unknown_price,
        "unknown_decimals_rows": unknown_decimals,
        "unknown_usd_rows": f"({unknown_price}) + ({unknown_decimals})",
    }


def build_flows_sql(
    *,
    frontier_ids: list[str],
    direction: str,  # "out" | "in"  ("both" is the CALLER's job — two queries)
    t0: str,
    t1_exclusive: str,
    min_usd: float,
    tokens: list[str] | None,
    limit: int,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Aggregated value-flow edges for one directional hop leg.

    Locked semantics (each pinned by a test):
      * Grain: one row per (source, target, token_address); weights summed.
      * Half-open time range ``[t0, t1_exclusive)`` on ``block_timestamp``.
      * Self-loops excluded.
      * ``min_usd`` applies to the AGGREGATED edge (HAVING), not per-tx rows.
      * Token filter emitted only when ``tokens`` is non-empty.
      * Deterministic ordering, ``LIMIT limit+1`` — the extra row is the
        exact truncation signal (caller drops it).
    """
    if direction not in ("out", "in"):
        raise ValueError(f"direction must be 'out' or 'in', got {direction!r}")
    where_col = "`from`" if direction == "out" else "`to`"
    params: dict[str, Any] = {
        "ids": _norm_ids(frontier_ids),
        "t0": t0,
        "t1": t1_exclusive,
        "min_usd": float(min_usd),
        "lim": int(limit) + 1,
    }
    token_clause = ""
    if tokens:
        token_clause = " AND d.token_address IN {tokens:Array(String)}"
        params["tokens"] = _norm_ids(tokens)
    enrich = _enrichment_parts(
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    # Columns are qualified `d.` throughout: the SELECT aliases (`symbol`,
    # `amount_usd`, …) would otherwise shadow the joined columns of the same
    # name, which ClickHouse resolves to the ALIAS and then rejects or, worse,
    # silently matches nothing.
    sql = f"""
        SELECT
            d.`from` AS source_id,
            d.`to` AS target_id,
            d.token_address AS token_address,
            {enrich["symbol"]} AS symbol,
            {enrich["normalized"]} AS amount,
            {enrich["known_usd"]} AS amount_usd,
            sum(d.transfer_count) AS transfer_count,
            min(d.date) AS first_seen,
            max(d.date) AS last_seen,
            {enrich["unknown_usd_rows"]} AS unknown_usd_rows
        FROM {FLOWS_RELATION} AS d{enrich["joins"]}
        WHERE d.{where_col} IN {{ids:Array(String)}}
          AND d.date >= toDate({{t0:DateTime}}) AND d.date < toDate({{t1:DateTime}})
          AND d.`from` != d.`to`{token_clause}
        GROUP BY source_id, target_id, token_address
        HAVING {_USD_ELIGIBILITY}
        ORDER BY isNull(amount_usd), amount_usd DESC, transfer_count DESC,
                 source_id, target_id, token_address
        LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_flows_coverage_sql(
    *,
    frontier_ids: list[str],
    direction: str,
    t0: str,
    t1_exclusive: str,
    min_usd: float,
    tokens: list[str] | None,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Exact pre-budget coverage for one directional hop query.

    This companion query deliberately repeats ``build_flows_sql``'s edge
    eligibility contract, but removes both the UI admission budget and the
    query row limit.  The single aggregate row is therefore safe to return
    even when the eligible edge population is large.  ``total_usd`` is NULL
    whenever any eligible edge lacks a price; ``known_usd`` remains useful in
    that case without pretending it is a complete total.
    """
    if direction not in ("out", "in"):
        raise ValueError(f"direction must be 'out' or 'in', got {direction!r}")
    where_col = "`from`" if direction == "out" else "`to`"
    counterparty = "target_id" if direction == "out" else "source_id"
    params: dict[str, Any] = {
        "ids": _norm_ids(frontier_ids),
        "t0": t0,
        "t1": t1_exclusive,
        "min_usd": float(min_usd),
        "structural_terminals": sorted(STRUCTURAL_TERMINALS),
    }
    token_clause = ""
    if tokens:
        token_clause = " AND d.token_address IN {tokens:Array(String)}"
        params["tokens"] = _norm_ids(tokens)
    enrich = _enrichment_parts(
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    total_counterparties = (
        f"""uniqExactIf(
                {counterparty},
                NOT has({{structural_terminals:Array(String)}}, {counterparty})
                AND {counterparty} NOT IN (
                    SELECT token_address FROM {TOKENS_META_RELATION}
                )
            )"""
        if metadata_available
        else "CAST(NULL AS Nullable(UInt64))"
    )
    contract_endpoint_edges = (
        f"""countIf(
                {counterparty} IN (
                    SELECT token_address FROM {TOKENS_META_RELATION}
                )
            )"""
        if metadata_available
        else "CAST(NULL AS Nullable(UInt64))"
    )
    sql = f"""
        WITH candidate_edges AS (
            SELECT
                d.`from` AS source_id,
                d.`to` AS target_id,
                d.token_address AS token_address,
                {enrich["known_usd"]} AS amount_usd,
                {enrich["unknown_usd_rows"]} AS unknown_usd_rows
            FROM {FLOWS_RELATION} AS d{enrich["joins"]}
            WHERE d.{where_col} IN {{ids:Array(String)}}
              AND d.date >= toDate({{t0:DateTime}})
              AND d.date < toDate({{t1:DateTime}})
              AND d.`from` != d.`to`{token_clause}
            GROUP BY source_id, target_id, token_address
        ),
        eligible_edges AS (
            SELECT *
            FROM candidate_edges
            WHERE {_USD_ELIGIBILITY}
        )
        SELECT
            {total_counterparties} AS total_counterparties,
            count() AS total_edges,
            sumIf(amount_usd, isNotNull(amount_usd)) AS known_usd,
            if(
                countIf(unknown_usd_rows > 0) = 0
                AND (
                    SELECT count()
                    FROM candidate_edges
                    WHERE isNull(amount_usd)
                      AND {{min_usd:Float64}} > 0
                ) = 0,
                toNullable(sum(coalesce(amount_usd, 0))),
                CAST(NULL AS Nullable(Float64))
            ) AS total_usd,
            countIf(unknown_usd_rows > 0) AS unknown_usd_edges,
            toUInt64(0) AS excluded_unknown_usd_edges,
            countIf(
                has({{structural_terminals:Array(String)}}, {counterparty})
            ) AS supply_event_edges,
            {contract_endpoint_edges} AS contract_endpoint_edges
        FROM eligible_edges
    """
    return sql, params


def _timeline_bucket_expr(grain: str, column: str = "d.date") -> str:
    """ClickHouse bucket expression shared by every Over-time query."""
    if grain == "day":
        return f"toDate({column})"
    if grain == "week":
        return f"toStartOfWeek({column}, 1)"
    if grain == "month":
        return f"toStartOfMonth({column})"
    raise ValueError(f"grain must be 'day', 'week', or 'month', got {grain!r}")


def _timeline_filters(
    *, direction: str, tokens: list[str] | None
) -> tuple[str, str]:
    """Return the seed-direction and optional token predicates.

    Over time intentionally uses the exact same directional interpretation as
    Money Trail: ``out`` admits transfers sent by a seed, ``in`` transfers
    received by a seed, and ``both`` is their union.  Keeping this in one
    helper prevents the universe, buckets, and coverage queries from drifting.
    """
    if direction == "out":
        direction_clause = "d.`from` IN {seed_ids:Array(String)}"
    elif direction == "in":
        direction_clause = "d.`to` IN {seed_ids:Array(String)}"
    elif direction == "both":
        direction_clause = (
            "(d.`from` IN {seed_ids:Array(String)} "
            "OR d.`to` IN {seed_ids:Array(String)})"
        )
    else:
        raise ValueError(
            f"direction must be 'out', 'in', or 'both', got {direction!r}"
        )
    token_clause = (
        " AND d.token_address IN {tokens:Array(String)}" if tokens else ""
    )
    return direction_clause, token_clause


def _timeline_params(
    *,
    seed_ids: list[str],
    t0: str,
    t1_exclusive: str,
    min_usd: float,
    tokens: list[str] | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "seed_ids": _norm_ids(seed_ids),
        "structural_terminals": sorted(STRUCTURAL_TERMINALS),
        "t0": t0,
        "t1": t1_exclusive,
        "min_usd": float(min_usd),
    }
    if tokens:
        params["tokens"] = _norm_ids(tokens)
    return params


def _timeline_eligible_pairs_cte(
    *,
    direction: str,
    tokens: list[str] | None,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> str:
    """Full-range eligible token edges, matching Money Trail's HAVING rule."""
    direction_clause, token_clause = _timeline_filters(
        direction=direction, tokens=tokens
    )
    enrich = _enrichment_parts(
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    return f"""
        SELECT
            d.`from` AS source_id,
            d.`to` AS target_id,
            d.token_address AS token_address,
            {enrich["symbol"]} AS symbol,
            {enrich["known_usd"]} AS amount_usd,
            sum(d.transfer_count) AS transfer_count,
            {enrich["unknown_usd_rows"]} AS unknown_usd_rows
        FROM {FLOWS_RELATION} AS d{enrich["joins"]}
        WHERE {direction_clause}
          AND d.date >= toDate({{t0:DateTime}})
          AND d.date < toDate({{t1:DateTime}})
          AND d.`from` != d.`to`{token_clause}
        GROUP BY source_id, target_id, token_address
        HAVING {_USD_ELIGIBILITY}
    """


def _timeline_candidate_pairs_cte(
    *,
    direction: str,
    tokens: list[str] | None,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> str:
    """Full-range token edges before the USD eligibility decision.

    Coverage queries need this population to disclose wholly unpriced groups
    that a positive minimum necessarily excludes.
    """
    direction_clause, token_clause = _timeline_filters(
        direction=direction, tokens=tokens
    )
    enrich = _enrichment_parts(
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    return f"""
        SELECT
            d.`from` AS source_id,
            d.`to` AS target_id,
            d.token_address AS token_address,
            {enrich["symbol"]} AS symbol,
            {enrich["known_usd"]} AS amount_usd,
            sum(d.transfer_count) AS transfer_count,
            {enrich["unknown_usd_rows"]} AS unknown_usd_rows
        FROM {FLOWS_RELATION} AS d{enrich["joins"]}
        WHERE {direction_clause}
          AND d.date >= toDate({{t0:DateTime}})
          AND d.date < toDate({{t1:DateTime}})
          AND d.`from` != d.`to`{token_clause}
        GROUP BY source_id, target_id, token_address
    """


def build_timeline_universe_sql(
    *,
    seed_ids: list[str],
    direction: str,
    t0: str,
    t1_exclusive: str,
    min_usd: float,
    tokens: list[str] | None,
    limit: int,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Rank counterparties once over the *entire* applied Money Trail range.

    The result is one row per counterparty, not one per bucket.  The caller
    admits the top ``limit`` rows and freezes that universe for every bucket.
    ``LIMIT n+1`` is the exact policy-cap signal.
    """
    eligible = _timeline_eligible_pairs_cte(
        direction=direction,
        tokens=tokens,
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    # The direction predicate guarantees at least one endpoint is a seed.  In
    # the both-direction case choose the non-seed endpoint deterministically.
    counterparty = (
        "if(has({seed_ids:Array(String)}, source_id), target_id, source_id)"
    )
    params = _timeline_params(
        seed_ids=seed_ids,
        t0=t0,
        t1_exclusive=t1_exclusive,
        min_usd=min_usd,
        tokens=tokens,
    )
    params["lim"] = int(limit) + 1
    sql = f"""
        WITH eligible_pairs AS ({eligible})
        SELECT
            {counterparty} AS counterparty_id,
            if(
                countIf(isNotNull(amount_usd)) = 0,
                CAST(NULL AS Nullable(Float64)),
                sumIf(amount_usd, isNotNull(amount_usd))
            ) AS amount_usd,
            sum(transfer_count) AS transfer_count,
            count() AS eligible_edge_count
        FROM eligible_pairs
        WHERE NOT has({{seed_ids:Array(String)}}, {counterparty})
          AND NOT has({{structural_terminals:Array(String)}}, {counterparty})
        GROUP BY counterparty_id
        ORDER BY isNull(amount_usd), amount_usd DESC, transfer_count DESC,
                 counterparty_id
        LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_timeline_global_coverage_sql(
    *,
    seed_ids: list[str],
    direction: str,
    t0: str,
    t1_exclusive: str,
    min_usd: float,
    tokens: list[str] | None,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Exact pre-budget global totals for the full applied range."""
    candidates = _timeline_candidate_pairs_cte(
        direction=direction,
        tokens=tokens,
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    counterparty = (
        "if(has({seed_ids:Array(String)}, source_id), target_id, source_id)"
    )
    params = _timeline_params(
        seed_ids=seed_ids,
        t0=t0,
        t1_exclusive=t1_exclusive,
        min_usd=min_usd,
        tokens=tokens,
    )
    sql = f"""
        WITH candidate_pairs AS ({candidates}),
        eligible_pairs AS (
            SELECT *
            FROM candidate_pairs
            WHERE {_USD_ELIGIBILITY}
        )
        SELECT
            uniqExactIf(
                {counterparty},
                NOT has({{seed_ids:Array(String)}}, {counterparty})
                AND NOT has(
                    {{structural_terminals:Array(String)}}, {counterparty}
                )
            ) AS total_counterparties,
            count() AS total_edges,
            if(
                countIf(unknown_usd_rows > 0) = 0,
                toNullable(sum(coalesce(amount_usd, 0))),
                CAST(NULL AS Nullable(Float64))
            ) AS total_usd,
            sum(transfer_count) AS total_transfers,
            countIf(unknown_usd_rows > 0) AS unknown_usd_edges,
            toUInt64(0) AS excluded_unknown_usd_edges,
            countIf(
                has({{structural_terminals:Array(String)}}, {counterparty})
            ) AS supply_event_edges
        FROM eligible_pairs
    """
    return sql, params


def _timeline_bucketed_cte(
    *,
    direction: str,
    tokens: list[str] | None,
    grain: str,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> str:
    """Eligible full-range pairs re-aggregated at the requested bucket grain."""
    eligible = _timeline_eligible_pairs_cte(
        direction=direction,
        tokens=tokens,
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    direction_clause, token_clause = _timeline_filters(
        direction=direction, tokens=tokens
    )
    bucket = _timeline_bucket_expr(grain)
    enrich = _enrichment_parts(
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    return f"""
        WITH eligible_pairs AS ({eligible}),
        bucketed AS (
            SELECT
                d.`from` AS source_id,
                d.`to` AS target_id,
                d.token_address AS token_address,
                {enrich["symbol"]} AS symbol,
                {bucket} AS bucket_start,
                toString(sum(d.amount_raw)) AS raw_amount,
                {enrich["normalized"]} AS normalized_amount,
                {enrich["known_usd"]} AS known_usd,
                sum(d.transfer_count) AS transfer_count,
                {enrich["priced_rows"]} AS priced_source_rows,
                count() AS source_rows,
                {enrich["unknown_price_rows"]} AS unknown_price_rows,
                {enrich["unknown_decimals_rows"]} AS unknown_decimals_rows
            FROM {FLOWS_RELATION} AS d{enrich["joins"]}
            INNER JOIN eligible_pairs AS e
                    ON e.source_id = d.`from`
                   AND e.target_id = d.`to`
                   AND e.token_address = d.token_address
            WHERE {direction_clause}
              AND d.date >= toDate({{t0:DateTime}})
              AND d.date < toDate({{t1:DateTime}})
              AND d.`from` != d.`to`{token_clause}
            GROUP BY source_id, target_id, token_address, bucket_start
        )
    """


def build_timeline_bucket_edges_sql(
    *,
    seed_ids: list[str],
    universe_ids: list[str],
    direction: str,
    t0: str,
    t1_exclusive: str,
    grain: str,
    min_usd: float,
    tokens: list[str] | None,
    limit: int,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Bucket edges restricted to one fixed, full-range-ranked universe."""
    bucketed = _timeline_bucketed_cte(
        direction=direction,
        tokens=tokens,
        grain=grain,
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    params = _timeline_params(
        seed_ids=seed_ids,
        t0=t0,
        t1_exclusive=t1_exclusive,
        min_usd=min_usd,
        tokens=tokens,
    )
    params["universe_ids"] = _norm_ids(universe_ids)
    params["lim"] = int(limit) + 1
    sql = f"""
        {bucketed}
        SELECT source_id, target_id, token_address, symbol, bucket_start,
               raw_amount, normalized_amount, known_usd, transfer_count,
               priced_source_rows, source_rows, unknown_price_rows,
               unknown_decimals_rows
        FROM bucketed
        WHERE (
                source_id IN {{universe_ids:Array(String)}}
                OR source_id IN {{structural_terminals:Array(String)}}
              )
          AND (
                target_id IN {{universe_ids:Array(String)}}
                OR target_id IN {{structural_terminals:Array(String)}}
              )
        ORDER BY bucket_start, isNull(known_usd), known_usd DESC,
                 source_id, target_id, token_address
        LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_timeline_bucket_coverage_sql(
    *,
    seed_ids: list[str],
    direction: str,
    t0: str,
    t1_exclusive: str,
    grain: str,
    min_usd: float,
    tokens: list[str] | None,
    metadata_available: bool = True,
    prices_available: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Exact pre-universe totals for every bucket in the applied window."""
    bucketed = _timeline_bucketed_cte(
        direction=direction,
        tokens=tokens,
        grain=grain,
        metadata_available=metadata_available,
        prices_available=prices_available,
    )
    counterparty = (
        "if(has({seed_ids:Array(String)}, source_id), target_id, source_id)"
    )
    params = _timeline_params(
        seed_ids=seed_ids,
        t0=t0,
        t1_exclusive=t1_exclusive,
        min_usd=min_usd,
        tokens=tokens,
    )
    sql = f"""
        {bucketed}
        SELECT
            bucket_start,
            uniqExactIf(
                {counterparty},
                NOT has({{seed_ids:Array(String)}}, {counterparty})
                AND NOT has(
                    {{structural_terminals:Array(String)}}, {counterparty}
                )
            ) AS total_counterparties,
            count() AS total_edges,
            if(
                countIf(isNotNull(known_usd)) = 0,
                CAST(NULL AS Nullable(Float64)),
                sumIf(known_usd, isNotNull(known_usd))
            ) AS known_usd,
            sum(unknown_price_rows + unknown_decimals_rows) AS unknown_usd_rows,
            countIf(
                has({{structural_terminals:Array(String)}}, {counterparty})
            ) AS supply_event_edges
        FROM bucketed
        GROUP BY bucket_start
        ORDER BY bucket_start
    """
    return sql, params


def build_bridge_flows_sql(
    *,
    frontier_ids: list[str],
    t0: str,
    t1_exclusive: str,
    limit: int,
) -> tuple[str, dict[str, Any]]:
    """Candidate bridge annotations for admitted user→bridge transfers.

    Out-leg only: the live model records user→bridge candidates.  These rows
    do not independently establish value movement; the walker matches them to
    already-admitted primary transfer edges and ignores unmatched rows.
    """
    params: dict[str, Any] = {
        "ids": _norm_ids(frontier_ids),
        "t0": t0[:10],
        "t1": t1_exclusive[:10],
        "lim": int(limit) + 1,
    }
    sql = f"""
        SELECT
            user_address AS source_id,
            bridge_contract AS target_id,
            token_address,
            any(symbol) AS symbol,
            any(bridge_name) AS bridge_name,
            sum(amount_raw_sum) AS amount_raw,
            sum(transfer_count) AS transfer_count,
            min(date) AS first_seen,
            max(date) AS last_seen
        FROM {BRIDGES_RELATION}
        WHERE direction = 'out'
          AND user_address IN {{ids:Array(String)}}
          AND notEmpty(user_address)
          AND notEmpty(bridge_contract)
          AND user_address != bridge_contract
          AND date >= {{t0:Date}} AND date < {{t1:Date}}
        GROUP BY source_id, target_id, token_address
        ORDER BY transfer_count DESC, source_id, target_id, token_address
        LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_bridge_safety_gate_sql() -> tuple[str, dict[str, Any]]:
    """Bounded, full-relation quality gate for optional bridge attribution.

    The deployed bridge model is incremental, so validating only the requested
    time window can hide polluted historical partitions left behind by an old
    model definition.  This query therefore scans the *current full relation*
    and returns exactly one aggregate row. The caller must execute it with the
    internal contract-probe ``QueryBudget``: embedding a ``SETTINGS`` clause
    here collides with the shared guarded-query wrapper. A timeout or resource
    failure means cleanliness could not be proven and callers must leave
    bridge enrichment disabled.

    The materialized schema cannot reconstruct both original transfer
    endpoints.  It can, however, conservatively identify endpoint ambiguity
    when the alleged ``user_address`` is also present in the relation's set of
    bridge contracts (including the degenerate user == bridge case).
    """
    sql = f"""
        /* graph_explorer_bridge_safety_gate: full deployed relation */
        WITH bridge_addresses AS (
            SELECT DISTINCT bridge_contract
            FROM {BRIDGES_RELATION}
            WHERE notEmpty(trimBoth(ifNull(bridge_contract, '')))
        )
        SELECT
            count() AS rows_checked,
            countIf(
                NOT notEmpty(trimBoth(ifNull(bridge_contract, '')))
            ) AS blank_bridge_contract_rows,
            countIf(
                NOT notEmpty(trimBoth(ifNull(bridge_name, '')))
            ) AS blank_bridge_name_rows,
            countIf(
                NOT notEmpty(trimBoth(ifNull(user_address, '')))
            ) AS blank_user_address_rows,
            countIf(
                trimBoth(ifNull(direction, '')) NOT IN ('out', 'in')
            ) AS invalid_direction_rows,
            countIf(
                notEmpty(trimBoth(ifNull(user_address, '')))
                AND notEmpty(trimBoth(ifNull(bridge_contract, '')))
                AND user_address IN (
                    SELECT bridge_contract FROM bridge_addresses
                )
            ) AS endpoint_ambiguity_rows,
            min(date) AS first_date,
            max(date) AS last_date
        FROM {BRIDGES_RELATION}
        LIMIT 1
    """
    return sql, {}


def build_flow_evidence_sql(
    *,
    source_id: str,
    target_id: str,
    token_address: str,
    edge_class: str,  # "transfer" | "bridge"
    t0: str,
    t1_exclusive: str,
    limit: int = 25,
) -> tuple[str, dict[str, Any]]:
    """Per-edge drilldown.

    Transfer edges: TRANSACTION-level rows read from the chain (the daily
    aggregate has no transaction_hash). Bridge edges: per-day rows, the bridges
    model's own grain.
    """
    params: dict[str, Any] = {
        "src": str(source_id).lower(),
        "tgt": str(target_id).lower(),
        "token": str(token_address).lower(),
        "lim": int(limit),
    }
    if edge_class == "bridge":
        params["t0"] = t0[:10]
        params["t1"] = t1_exclusive[:10]
        sql = f"""
            SELECT date, any(symbol) AS symbol,
                   sum(amount_raw_sum) AS amount_raw,
                   sum(transfer_count) AS transfer_count
            FROM {BRIDGES_RELATION}
            WHERE direction = 'out'
              AND user_address = {{src:String}}
              AND bridge_contract = {{tgt:String}}
              AND token_address = {{token:String}}
              AND date >= {{t0:Date}} AND date < {{t1:Date}}
            GROUP BY date
            ORDER BY date DESC
            LIMIT {{lim:UInt32}}
        """
        return sql, params
    # TRANSFER evidence comes from the CHAIN, not from FLOWS_RELATION: the
    # daily model has no transaction_hash, and "which transactions made up this
    # edge" is the entire point of the drill-down. Bounded by block_timestamp
    # (the sort prefix) so the scan stays cheap, and unioned with the live tail
    # then de-duplicated on the natural key.
    #
    # Hex is stored WITHOUT the 0x prefix, an address occupies the last 40 of a
    # 64-char topic word, and topic3 IS NULL excludes 4-topic ERC-721 Transfers.
    #
    # Token metadata is a ONE-ROW CTE, CROSS JOINed. Two dead ends got here:
    # joining tokens_meta directly on the constant token fails with
    # INVALID_JOIN_ON_EXPRESSION (no join key on the left), and a scalar
    # subquery referencing legs.block_timestamp fails as a CORRELATED subquery.
    # The CROSS JOIN materialises symbol/decimals as real columns, which the
    # price join can then key on alongside the date.
    params["t0"] = t0
    params["t1"] = t1_exclusive
    params["topic0"] = TRANSFER_TOPIC0
    params["src_topic"] = "0" * 24 + params["src"].removeprefix("0x")
    params["tgt_topic"] = "0" * 24 + params["tgt"].removeprefix("0x")
    params["token_bare"] = params["token"].removeprefix("0x")
    legs = []
    for rel in CHAIN_LOG_RELATIONS:
        legs.append(f"""
        SELECT block_number, transaction_index, log_index, transaction_hash,
               block_timestamp, data
        FROM {rel}
        WHERE block_timestamp >= {{t0:DateTime}} AND block_timestamp < {{t1:DateTime}}
          AND topic0 = {{topic0:String}}
          AND topic3 IS NULL
          AND address = {{token_bare:String}}
          AND topic1 = {{src_topic:String}}
          AND topic2 = {{tgt_topic:String}}""")
    union = "\n        UNION ALL".join(legs)
    sql = f"""
    WITH meta AS (
        SELECT coalesce(any(token), '') AS symbol,
               coalesce(any(decimals), 18) AS decimals
        FROM {TOKENS_META_RELATION}
        WHERE token_address = {{token:String}}
    ),
    raw AS ({union}
    ),
    legs AS (
        SELECT block_number, transaction_index, log_index, transaction_hash,
               any(block_timestamp) AS block_timestamp, any(data) AS data
        FROM raw
        GROUP BY block_number, transaction_index, log_index, transaction_hash
    )
    SELECT concat('0x', legs.transaction_hash) AS transaction_hash,
           legs.block_timestamp AS block_timestamp,
           meta.symbol AS symbol,
           toFloat64(reinterpretAsUInt256(reverse(unhex(legs.data))))
               / pow(10, meta.decimals) AS amount,
           if(p.price_found = 0, CAST(NULL AS Nullable(Float64)),
              round(toFloat64(reinterpretAsUInt256(reverse(unhex(legs.data))))
                  / pow(10, meta.decimals) * p.price, 6)) AS amount_usd
    FROM legs
    CROSS JOIN meta
    LEFT JOIN (
        SELECT symbol, date, price, toUInt8(1) AS price_found
        FROM {PRICES_RELATION}
    ) AS p
           ON p.symbol = meta.symbol AND p.date = toDate(legs.block_timestamp)
    ORDER BY legs.block_number DESC, legs.transaction_index DESC, legs.log_index DESC
    LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_flow_labels_sql(node_ids: list[str]) -> tuple[str, dict[str, Any]]:
    """Latest label per address (project + sector) for attribution."""
    sql = f"""
        SELECT address,
               argMax(project, introduced_at) AS project,
               argMax(sector, introduced_at) AS sector
        FROM {LABELS_RELATION}
        WHERE address IN {{ids:Array(String)}}
        GROUP BY address
    """
    return sql, {"ids": _norm_ids(node_ids)}


def build_active_token_universe_sql(
    *, t0: str, t1_exclusive: str, limit: int = 1000
) -> tuple[str, dict[str, Any]]:
    """Exact effective-dated token-address universe for an applied window.

    A token is in scope when its whitelist interval overlaps ``[t0, t1)``.
    ``limit + 1`` makes an unexpectedly large/corrupt universe detectable
    rather than silently hashing a truncated address set.
    """

    sql = f"""
        SELECT token_address, any(token) AS symbol
        FROM {TOKENS_META_RELATION}
        WHERE date_start < toDate({{t1:DateTime}})
          AND (date_end IS NULL OR date_end > toDate({{t0:DateTime}}))
          AND notEmpty(token_address)
        GROUP BY token_address
        ORDER BY token_address
        LIMIT {{lim:UInt32}}
    """
    return sql, {
        "t0": t0,
        "t1": t1_exclusive,
        "lim": max(1, int(limit)) + 1,
    }


def build_token_contract_sql(node_ids: list[str]) -> tuple[str, dict[str, Any]]:
    """Which of these addresses are themselves ERC-20 CONTRACTS?

    A transfer whose recipient is a token/vault contract is a PROTOCOL
    interaction (deposit / burn / redeem), not value sent to a counterparty —
    rendering it as a peer edge misleads an investigator, and traversing
    *through* it drags in every holder of that token. So we detect them,
    mark them, and treat them as terminal.

    Indexed equality lookup restricted to the traced node set (a bare
    ``SELECT DISTINCT token_address`` over the whole relation OOMs the
    server, so never do that).
    """
    # Alias must NOT be `token_address`: an alias shadows the column, so the
    # WHERE would compare the projected value against itself and match nothing.
    sql = f"""
        SELECT DISTINCT d.token_address AS token_contract
        FROM {FLOWS_RELATION} AS d
        WHERE d.token_address IN {{ids:Array(String)}}
    """
    return sql, {"ids": _norm_ids(node_ids)}


def build_gp_flags_sqls(node_ids: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """GP-case flag lookups: [(flag_query_kind, sql, params), ...].

    Live columns are ``address``/``canonical_address`` — aliased explicitly.
    ``refunded_safe`` marks a migrated NEW safe that received an
    exploit-recovery refund.
    """
    ids = _norm_ids(node_ids)
    return [
        (
            "canonical",
            f"""
            SELECT address AS old_safe, canonical_address AS new_safe
            FROM {GP_CANONICAL_RELATION}
            WHERE address IN {{ids:Array(String)}}
               OR canonical_address IN {{ids:Array(String)}}
            """,
            {"ids": ids},
        ),
        (
            "refunded",
            f"""
            SELECT DISTINCT new_safe
            FROM {GP_REFUNDS_RELATION}
            WHERE new_safe IN {{ids:Array(String)}}
            """,
            {"ids": ids},
        ),
    ]


def flow_edge_id(
    source_id: str, target_id: str, token_address: str, edge_class: str
) -> str:
    scheme = "bridge" if edge_class == "bridge" else "flow"
    return f"{scheme}:{source_id}->{target_id}:{token_address}"


def parse_flow_edge_id(edge_id: str) -> tuple[str, str, str, str] | None:
    """``(edge_class, source, target, token)`` or None. Addresses never
    contain ``:`` so partition/rpartition is unambiguous."""
    scheme, _, rest = edge_id.partition(":")
    if scheme not in ("flow", "bridge") or not rest:
        return None
    endpoints, _, token = rest.rpartition(":")
    if not endpoints or not token:
        return None
    src, sep, tgt = endpoints.partition("->")
    if not sep or not src or not tgt:
        return None
    edge_class = "bridge" if scheme == "bridge" else "transfer"
    return edge_class, src, tgt, token
