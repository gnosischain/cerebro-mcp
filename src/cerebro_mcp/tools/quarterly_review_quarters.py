"""Quarter-label arithmetic for the Quarterly Review mini-app.

Keeps the ``2026-Q1`` <-> ``(start_date, end_date)`` conversion, compare-mode
resolution, and the available-quarters enumeration in one place so the main
tool file stays readable.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from cerebro_mcp.clickhouse_client import ClickHouseManager

CompareMode = Literal["prior_quarter", "same_quarter_last_year", "trailing_4q_avg"]


def parse_quarter(label: str) -> tuple[date, date]:
    """``"2026-Q1"`` -> ``(date(2026, 1, 1), date(2026, 3, 31))``."""
    year_str, q_str = label.split("-Q")
    year = int(year_str)
    quarter = int(q_str)
    if quarter < 1 or quarter > 4:
        raise ValueError(f"Invalid quarter in label: {label!r}")
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    # End = first day of next quarter minus 1 day.
    if quarter == 4:
        next_q_start = date(year + 1, 1, 1)
    else:
        next_q_start = date(year, start_month + 3, 1)
    end = next_q_start - timedelta(days=1)
    return start, end


def quarter_label(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _add_months(d: date, months: int) -> date:
    """Month-addition that snaps to the first of the month."""
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    return date(year, month + 1, 1)


def prior_quarter(label: str) -> str:
    start, _ = parse_quarter(label)
    return quarter_label(_add_months(start, -3))


def same_quarter_last_year(label: str) -> str:
    start, _ = parse_quarter(label)
    return quarter_label(date(start.year - 1, start.month, 1))


def resolve_compare(label: str, mode: str) -> str:
    """Map ``compare_mode`` + current quarter to the compare-quarter label.

    ``trailing_4q_avg`` is rendered as the trailing four quarters in SQL but
    the UI still needs a single compare-label pointer — use the prior quarter
    as that anchor so quarter pickers stay consistent.
    """
    if mode == "same_quarter_last_year":
        return same_quarter_last_year(label)
    # prior_quarter | trailing_4q_avg | unknown → prior_quarter fallback
    return prior_quarter(label)


def latest_complete_quarter(today: date | None = None) -> str:
    """Return the label of the last fully-closed quarter on ``today``'s calendar."""
    today = today or date.today()
    current_q_start, _ = parse_quarter(quarter_label(today))
    last_day_prev_q = current_q_start - timedelta(days=1)
    return quarter_label(last_day_prev_q)


def enumerate_quarters(ch: ClickHouseManager, limit: int = 12) -> list[str]:
    """Enumerate available quarter labels descending, bounded by data coverage.

    Uses ``api_execution_transactions_daily`` as the coverage oracle — that
    model is the most broadly populated daily feed. If the query fails the
    function returns a generous synthetic list ending at the latest complete
    quarter so the UI still has something to render in degraded conditions.
    """
    try:
        result = ch.run_query(
            "SELECT min(day) AS mn, max(day) AS mx "
            "FROM dbt.api_execution_transactions_daily",
            "dbt",
            requested_max_rows=1,
            audience="internal",
        )
        if not result.rows:
            raise RuntimeError("empty coverage query")
        mn_raw, mx_raw = result.rows[0]
        mn = _coerce_date(mn_raw)
        mx = _coerce_date(mx_raw)
    except Exception:  # noqa: BLE001 — degraded fallback is intentional
        mx = date.today()
        mn = date(mx.year - 4, 1, 1)

    out: list[str] = []
    seen: set[str] = set()
    cursor = mx
    while cursor >= mn and len(out) < limit:
        label = quarter_label(cursor)
        if label not in seen:
            out.append(label)
            seen.add(label)
        # Step to the first day of the previous quarter.
        start, _ = parse_quarter(label)
        cursor = start - timedelta(days=1)
    return out


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"Cannot coerce {value!r} to date")
