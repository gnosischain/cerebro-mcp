import pytest
from cerebro_mcp.tools.query import format_results_table, truncate_response


class TestFormatResultsTableTruncation:
    """Test row-aware truncation in format_results_table."""

    def test_small_table_no_truncation(self):
        columns = ["id", "name"]
        rows = [[1, "alice"], [2, "bob"]]
        result = format_results_table(columns, rows, max_chars=10000)
        assert "alice" in result
        assert "bob" in result
        assert "truncated" not in result.lower()

    def test_large_table_truncates_with_row_count(self):
        columns = ["id", "value"]
        rows = [[i, f"data_{i:04d}_padding_to_make_rows_longer"] for i in range(1000)]
        result = format_results_table(columns, rows, max_chars=2000)
        assert "truncated" in result.lower()
        assert "1000 rows" in result
        # Should show fewer than 1000 rows
        assert "Showing" in result

    def test_truncated_table_has_valid_structure(self):
        """Truncated table should not have broken markdown rows."""
        columns = ["col_a", "col_b", "col_c"]
        rows = [[f"val_{i}", f"data_{i}", f"info_{i}"] for i in range(500)]
        result = format_results_table(columns, rows, max_chars=1000)
        lines = result.strip().split("\n")
        # First line is header, second is separator, rest are rows or truncation notice
        assert " | " in lines[0]  # header
        assert "-|-" in lines[1]  # separator
        # No line should be a partial row (all data lines should have correct pipe count)
        pipe_count = lines[0].count("|")
        for line in lines[2:]:
            if not line.strip() or "truncated" in line.lower():
                break
            assert line.count("|") == pipe_count

    def test_empty_rows_no_truncation(self):
        columns = ["id"]
        rows = []
        result = format_results_table(columns, rows, max_chars=1000)
        assert result == "No rows returned."

    def test_max_chars_zero_uses_default(self):
        """max_chars=0 should use the config default."""
        columns = ["id"]
        rows = [[i] for i in range(10)]
        result = format_results_table(columns, rows, max_chars=0)
        # Should not truncate with default 40k limit
        assert "truncated" not in result.lower()


class TestTruncateResponse:
    """Test generic string truncation for free-text output."""

    def test_short_text_passes_through(self):
        text = "Hello world"
        assert truncate_response(text, max_chars=1000) == text

    def test_long_text_truncated(self):
        text = "x" * 5000
        result = truncate_response(text, max_chars=100)
        assert len(result) < 5000
        assert result.startswith("x" * 100)
        assert "truncated" in result.lower()

    def test_truncation_notice_includes_char_count(self):
        text = "y" * 2000
        result = truncate_response(text, max_chars=500)
        assert "500" in result

    def test_exact_limit_no_truncation(self):
        text = "a" * 100
        result = truncate_response(text, max_chars=100)
        assert result == text
        assert "truncated" not in result.lower()

    def test_max_chars_zero_uses_default(self):
        text = "short"
        result = truncate_response(text, max_chars=0)
        assert result == text
