"""SQL-text heuristics for report-quality enforcement.

Pure text functions over SQL strings. No AST, no warehouse round-trip.
Conservative by design: false positives are preferred over false negatives,
because the agent must explicitly justify a flagged chart or rewrite the SQL.

All patterns target ClickHouse SQL (the only dialect cerebro speaks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Default stock-measure column names
# ---------------------------------------------------------------------------

DEFAULT_STOCK_COLUMNS: frozenset[str] = frozenset({
    "tvl_usd",
    "tvl",
    "balance",
    "balance_usd",
    "supply",
    "total_supply",
    "circulating_supply",
    "outstanding",
    "debt_outstanding",
    "borrow_outstanding",
    "deposit_outstanding",
    "cumulative_accounts",
    "cumulative_users",
    "cumulative_volume",
    "snapshot_value",
    "mau_snapshot",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_comments(sql: str) -> str:
    """Return sql with -- and /* */ comments removed (preserves layout enough
    for line/word matching but removes commented-out code)."""
    return _COMMENT_RE.sub(" ", sql or "")


def _normalize(sql: str) -> str:
    """Lower-cased, whitespace-collapsed view of the SQL (no comments)."""
    return _WHITESPACE_RE.sub(" ", _strip_comments(sql).lower()).strip()


def _column_token_pattern(col: str) -> re.Pattern:
    """Match a column name as a whole token (boundaries on both sides)."""
    return re.compile(rf"(?<![a-z0-9_]){re.escape(col)}(?![a-z0-9_])")


# ---------------------------------------------------------------------------
# Heuristic 1: stock measure aggregated over time
# ---------------------------------------------------------------------------

# Patterns indicating point-in-time constraint: GROUP BY date/day, single date filter, argMax wrapping.
_POINT_IN_TIME_HINTS_RE = re.compile(
    r"\bgroup\s+by\b[^()]*?\b(date|day|toStartOfDay|toDate)\b"
    r"|\bwhere\b[^()]*?\b(date|day)\b\s*=\s*"
    r"|\bargmax\s*\(",
    re.IGNORECASE,
)


def aggregates_stock_measure_over_time(
    sql: str,
    stock_columns: frozenset[str] = DEFAULT_STOCK_COLUMNS,
) -> tuple[bool, str]:
    """Detect SUM(stock_col) without a single-date constraint.

    Returns (is_violation, message). is_violation=True means the SQL
    appears to sum a stock measure over a date range, which is wrong.
    """
    n = _normalize(sql)
    if not n:
        return False, ""

    for col in stock_columns:
        # Look for SUM(<col>) anywhere
        sum_pattern = re.compile(
            rf"\bsum\s*\(\s*{re.escape(col)}\s*\)",
            re.IGNORECASE,
        )
        if not sum_pattern.search(n):
            continue

        # Permit if a point-in-time hint is present in the SAME statement.
        # (Conservative: we treat any hint as exonerating.)
        if _POINT_IN_TIME_HINTS_RE.search(n):
            continue

        # Permit if the SUM is wrapped in argMax (e.g., sum(argMax(tvl_usd, date)))
        # — already detected by the hints regex via 'argmax', but double-check.
        if re.search(r"\bsum\s*\(\s*argmax\s*\(", n):
            continue

        return True, (
            f"Chart SQL aggregates stock measure `{col}` over time without a "
            f"point-in-time constraint. TVL / balance / supply are stock "
            f"measures; summing them across a date range produces a "
            f"meaningless number. Use `argMax({col}, date)` per entity, then "
            f"sum at a single date; or constrain with "
            f"`WHERE date = (SELECT max(date) FROM ...)`; or use a canonical "
            f"snapshot model (e.g. `fct_execution_pools_snapshots`)."
        )

    return False, ""


# ---------------------------------------------------------------------------
# Heuristic 2: dimension used without residual bucket
# ---------------------------------------------------------------------------

# Filters that exclude the residual bucket without acknowledging it
_RESIDUAL_FILTER_RE = re.compile(
    r"\b(\w+)\s*(?:!=|<>)\s*''"
    r"|\b(\w+)\s+is\s+not\s+null"
    r"|\b(\w+)\s*not\s+in\s*\(\s*'unknown'\s*\)",
    re.IGNORECASE,
)

_RESIDUAL_DISCLOSURE_HINTS = (
    "share of labelled",
    "share of labeled",
    "labelled fraction",
    "labeled fraction",
    "excluding unlabelled",
    "excluding unlabeled",
    "excluding unknown",
    "excluding null",
    "of labelled",
    "of labeled",
    "of known",
    "denominator-honest",
)


def uses_dimension_without_residual(
    sql: str,
    chart_metadata: dict | None = None,
) -> tuple[bool, str]:
    """Detect a residual-bucket exclusion filter without disclosure.

    `chart_metadata` may carry `title`, `subtitle`, `description`, or
    `override_reason` fields; if any of them mention the labelled-fraction
    explicitly, the violation is suppressed.
    """
    n = _normalize(sql)
    if not n:
        return False, ""

    matches = list(_RESIDUAL_FILTER_RE.finditer(n))
    if not matches:
        return False, ""

    # Allow if SQL itself says GROUP BY <dim>; existence of GROUP BY without
    # a residual filter would mean the residual is included — but here we
    # already matched a residual-exclusion filter, so check disclosure.
    blob_parts = []
    if isinstance(chart_metadata, dict):
        for key in ("title", "subtitle", "description", "override_reason"):
            v = chart_metadata.get(key)
            if isinstance(v, str):
                blob_parts.append(v.lower())
    blob = " | ".join(blob_parts)

    for hint in _RESIDUAL_DISCLOSURE_HINTS:
        if hint in blob:
            return False, ""

    # Identify the column from the first match
    m = matches[0]
    col = next((g for g in m.groups() if g), "<dim>")
    return True, (
        f"Chart SQL filters out the residual bucket on column `{col}` "
        f"(via `!= ''`, `IS NOT NULL`, or similar) without acknowledging "
        f"the exclusion in the chart title/subtitle/description. "
        f"Either include the residual in the chart, or label the chart "
        f"explicitly as 'share of labelled X' and report the labelled "
        f"fraction in the surrounding narrative."
    )


# ---------------------------------------------------------------------------
# Heuristic 3: Pearson correlation on time series without stationarity context
# ---------------------------------------------------------------------------

_CORR_CALL_RE = re.compile(
    r"\bcorr\s*\(|\bcovar(?:Pop|Samp)?\s*\(|\bsimpleLinearRegression\s*\(",
    re.IGNORECASE,
)
_TIME_COLUMN_RE = re.compile(
    r"\b(date|day|month|week|year|hour|minute|block_timestamp|ts|timestamp)\b",
    re.IGNORECASE,
)
_STATIONARITY_HINTS = (
    "first-difference",
    "first difference",
    "differenced",
    "diff_corr",
    "spearman",
    "rank()",
    "rank_x",
    "rank_y",
    "adf",
    "stationarity",
    "cointegrat",
    "lag(",
    "laginframe",
)


def pearson_correlation_on_time_series(
    sql: str,
    chart_metadata: dict | None = None,
) -> tuple[bool, str]:
    """Detect corr() over a series with a time column and no stationarity hint.

    Stationarity hints are accepted from either the SQL itself or the chart
    metadata (override_reason / description).
    """
    n = _normalize(sql)
    if not n:
        return False, ""

    if not _CORR_CALL_RE.search(n):
        return False, ""
    if not _TIME_COLUMN_RE.search(n):
        return False, ""

    # Check both SQL and metadata for stationarity hints
    hint_blob = n
    if isinstance(chart_metadata, dict):
        for key in ("title", "subtitle", "description", "override_reason"):
            v = chart_metadata.get(key)
            if isinstance(v, str):
                hint_blob = hint_blob + " " + v.lower()

    for hint in _STATIONARITY_HINTS:
        if hint in hint_blob:
            return False, ""

    return True, (
        "Pearson correlation computed on a time series without a "
        "stationarity check or non-parametric alternative. Pearson `corr` "
        "on two non-stationary series is almost always spurious. Run the "
        "correlation on first-differenced series (`x - lagInFrame(x) OVER "
        "(ORDER BY date)`), or use a Spearman rank correlation (`corr("
        "rank(x) OVER (), rank(y) OVER ())`), or report ADF results "
        "alongside. See `statistical_reviewer.md` for ClickHouse "
        "templates."
    )


# ---------------------------------------------------------------------------
# Heuristic 4: aggregator-style volume sum without dedup
# ---------------------------------------------------------------------------

_AGGREGATOR_VOLUME_TARGETS = (
    "fct_execution_pools_daily",
    "fct_execution_trades_by_protocol_daily",
    "fct_execution_trades_by_token_daily",
)
_DEDUP_HINTS = (
    "dedup",
    "first-hop",
    "first hop",
    "single-hop",
    "single hop",
    "distinct tx_hash",
    "distinct transaction_hash",
    "argmax(",
)


def aggregator_volume_double_count_risk(
    sql: str,
    chart_metadata: dict | None = None,
) -> tuple[bool, str]:
    """Detect SUM(volume_usd*) over an aggregator-prone source without
    a deduplication signal."""
    n = _normalize(sql)
    if not n:
        return False, ""

    if not re.search(r"\bsum\s*\(\s*volume[a-z_]*\s*\)", n):
        return False, ""

    if not any(t in n for t in _AGGREGATOR_VOLUME_TARGETS):
        return False, ""

    hint_blob = n
    if isinstance(chart_metadata, dict):
        for key in ("title", "subtitle", "description", "override_reason"):
            v = chart_metadata.get(key)
            if isinstance(v, str):
                hint_blob = hint_blob + " " + v.lower()

    for hint in _DEDUP_HINTS:
        if hint in hint_blob:
            return False, ""

    return True, (
        "Volume sum over an aggregator-prone source without a "
        "deduplication signal. Multi-hop trades route through several "
        "pools and inflate naive `SUM(volume_usd)`. Deduplicate to "
        "first-hop or transaction-hash grain (e.g., add a CTE that picks "
        "one row per `(date, transaction_hash)`), or document the "
        "double-counting explicitly in the chart description."
    )


# ---------------------------------------------------------------------------
# Aggregate dispatcher
# ---------------------------------------------------------------------------

@dataclass
class HeuristicViolation:
    rule: str
    chart_id: str
    message: str


def evaluate_all(
    chart_id: str,
    sql: str,
    chart_metadata: dict | None,
    enabled: dict[str, bool],
    stock_columns: frozenset[str] = DEFAULT_STOCK_COLUMNS,
) -> list[HeuristicViolation]:
    """Run every enabled heuristic against a chart's SQL.

    `enabled` is a dict of rule keys -> bool (one per heuristic).
    Returns a list of violations (empty if all clean).
    """
    out: list[HeuristicViolation] = []

    if enabled.get("stock_flow", True):
        bad, msg = aggregates_stock_measure_over_time(sql, stock_columns)
        if bad:
            out.append(HeuristicViolation(
                rule="stock_flow_discipline",
                chart_id=chart_id,
                message=msg,
            ))

    if enabled.get("residual_bucket", True):
        bad, msg = uses_dimension_without_residual(sql, chart_metadata)
        if bad:
            out.append(HeuristicViolation(
                rule="residual_bucket_disclosure",
                chart_id=chart_id,
                message=msg,
            ))

    if enabled.get("stationarity", True):
        bad, msg = pearson_correlation_on_time_series(sql, chart_metadata)
        if bad:
            out.append(HeuristicViolation(
                rule="stationarity_on_correlations",
                chart_id=chart_id,
                message=msg,
            ))

    if enabled.get("aggregator_dedup", True):
        bad, msg = aggregator_volume_double_count_risk(sql, chart_metadata)
        if bad:
            out.append(HeuristicViolation(
                rule="aggregator_volume_dedup",
                chart_id=chart_id,
                message=msg,
            ))

    return out
