from __future__ import annotations

import json
import math
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from cerebro_mcp.config import settings


_SQL_DISPLAY_LIMIT = 2000


def format_results_table(
    columns: list,
    rows: list,
    max_col_width: int = 60,
    max_chars: int | None = None,
) -> str:
    """Format tabular results as markdown with a bounded character budget."""
    if not rows:
        return "No rows returned."

    if max_chars is None or max_chars <= 0:
        max_chars = settings.effective_summary_max_chars

    widths = [len(str(c)) for c in columns]
    str_rows = []
    for row in rows:
        str_row = []
        for i, val in enumerate(row):
            s = str(val) if val is not None else "NULL"
            if len(s) > max_col_width:
                s = s[: max_col_width - 3] + "..."
            str_row.append(s)
            widths[i] = max(widths[i], len(s))
        str_rows.append(str_row)

    header = " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(columns))
    separator = "-|-".join("-" * w for w in widths)

    lines = [header, separator]
    current_chars = len(header) + len(separator) + 2

    for row in str_rows:
        row_str = " | ".join(val.ljust(widths[i]) for i, val in enumerate(row))
        if current_chars + len(row_str) + 1 > max_chars:
            lines.append(
                f"\n[Table truncated at ~{current_chars:,} chars. "
                f"Showing {len(lines) - 2} of {len(str_rows)} rows. "
                "Use more specific filters or add LIMIT to reduce output.]"
            )
            break
        lines.append(row_str)
        current_chars += len(row_str) + 1

    return "\n".join(lines)


def truncate_sql(sql: str, limit: int = _SQL_DISPLAY_LIMIT) -> str:
    """Truncate SQL for display in summaries."""
    return sql if len(sql) <= limit else sql[:limit] + "\n-- [SQL truncated]"


def truncate_response(text: str, max_chars: int | None = None) -> str:
    """Truncate free-text responses to the configured response budget."""
    if max_chars is None or max_chars <= 0:
        max_chars = settings.effective_tool_result_max_chars
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n[Response truncated at {max_chars:,} chars. "
        "Use more specific filters or add LIMIT to reduce output.]"
    )


def normalize_value(value):
    """Convert ClickHouse values into strict JSON-safe values."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > 2**53 - 1 else value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (datetime, date, time, UUID)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, (list, tuple)):
        return [normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    return str(value)


def normalize_rows(rows: list) -> list[list]:
    return [[normalize_value(value) for value in row] for row in rows]


def fit_rows_to_budget(
    columns: list[str],
    rows: list[list],
    max_rows: int,
    max_chars: int | None = None,
) -> tuple[list[list], bool]:
    """Trim rows so the serialized structured payload fits the response budget."""
    if max_chars is None or max_chars <= 0:
        max_chars = settings.effective_tool_result_max_chars

    preview: list[list] = []
    truncated = False
    for row in rows[:max_rows]:
        preview.append(row)
        payload = {"columns": columns, "rows": preview}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded) > max_chars:
            preview.pop()
            truncated = True
            break

    if len(rows) > len(preview):
        truncated = True
    return preview, truncated


def format_warnings(warnings: list[str]) -> str:
    if not warnings:
        return ""
    lines = ["**Warnings:**"]
    lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def build_query_summary(
    *,
    columns: list[str],
    rows: list[list],
    row_count: int,
    rows_returned: int,
    elapsed_seconds: float,
    database: str,
    sql: str,
    warnings: list[str] | None = None,
    extra_notes: list[str] | None = None,
) -> str:
    table = format_results_table(columns, rows)
    meta = (
        f"\n\n---\n"
        f"Rows: {rows_returned} of {row_count} | "
        f"Time: {elapsed_seconds}s | "
        f"Database: {database}"
    )
    sections = [table + meta]
    warning_text = format_warnings(warnings or [])
    if warning_text:
        sections.append(warning_text)
    if extra_notes:
        sections.extend(extra_notes)
    sections.append(f"### SQL\n```sql\n{truncate_sql(sql)}\n```")
    return truncate_response("\n\n".join(sections))


def build_explain_summary(
    *,
    sql: str,
    lines: list[str],
    warnings: list[str] | None = None,
) -> str:
    body = "\n".join(lines) if lines else "No explain output returned."
    sections = [truncate_response(body)]
    warning_text = format_warnings(warnings or [])
    if warning_text:
        sections.append(warning_text)
    sections.append(f"### SQL\n```sql\n{truncate_sql(sql)}\n```")
    return truncate_response("\n\n".join(sections))
