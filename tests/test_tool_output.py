from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from cerebro_mcp.tool_output import fit_rows_to_budget, normalize_value


def test_normalize_value_handles_strict_json_edge_cases():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    uuid_value = UUID("12345678-1234-5678-1234-567812345678")

    assert normalize_value(float("nan")) == "nan"
    assert normalize_value(float("inf")) == "inf"
    assert normalize_value(float("-inf")) == "-inf"
    assert normalize_value(Decimal("12.34")) == "12.34"
    assert normalize_value(now) == now.isoformat()
    assert normalize_value(uuid_value) == str(uuid_value)
    assert normalize_value(2**60) == str(2**60)


def test_fit_rows_to_budget_truncates_before_json_overflow():
    columns = ["id", "payload"]
    rows = [[idx, "x" * 120] for idx in range(10)]

    preview, truncated = fit_rows_to_budget(columns, rows, max_rows=10, max_chars=220)

    assert truncated is True
    assert len(preview) < len(rows)
    assert preview
