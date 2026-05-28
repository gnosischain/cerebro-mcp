"""Tests for the verify_numbers tool and formula evaluation."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from cerebro_mcp.tools.governance.cross_check import (
    _eval_formula,
    _verify_one_claim,
    _first_numeric,
)
from cerebro_mcp.models.custom_tool import VerificationClaim


# ---------------------------------------------------------------------------
# Formula evaluation tests
# ---------------------------------------------------------------------------


class TestEvalFormula:
    def test_simple_subtraction(self):
        result = _eval_formula("received - sent", {"received": 100.0, "sent": 60.0})
        assert result == 40.0

    def test_three_terms(self):
        result = _eval_formula("a + b + c", {"a": 1.0, "b": 2.0, "c": 3.0})
        assert result == 6.0

    def test_division_and_multiply(self):
        result = _eval_formula("a / b * c", {"a": 50.0, "b": 200.0, "c": 100.0})
        assert result == 25.0

    def test_missing_component(self):
        result = _eval_formula("x - y", {"x": 10.0})
        assert isinstance(result, str)
        assert "unknown component" in result

    def test_empty_formula(self):
        result = _eval_formula("", {})
        assert result == "empty formula"

    def test_single_value(self):
        result = _eval_formula("total", {"total": 42.0})
        assert result == 42.0

    def test_division_by_zero(self):
        result = _eval_formula("a / b", {"a": 10.0, "b": 0.0})
        assert isinstance(result, str)
        assert "division by zero" in result


# ---------------------------------------------------------------------------
# Claim verification tests
# ---------------------------------------------------------------------------


class TestVerifyOneClaim:
    def test_correct_arithmetic_passes(self):
        claim = VerificationClaim(
            label="net flow",
            value=350.0,
            formula="received - sent",
            components={"received": 9352.5, "sent": 9002.5},
        )
        output, passed = _verify_one_claim(claim, ch=None)
        assert passed
        assert "ARITHMETIC PASS" in output

    def test_wrong_arithmetic_fails(self):
        claim = VerificationClaim(
            label="net GNO inflow",
            value=1360.5,
            formula="received - sent",
            components={"received": 9352.5, "sent": 9002.9},
        )
        output, passed = _verify_one_claim(claim, ch=None)
        assert not passed
        assert "ARITHMETIC FAIL" in output
        assert "289" in output  # ~289% difference

    def test_no_formula_no_check(self):
        claim = VerificationClaim(label="some number", value=42.0)
        output, passed = _verify_one_claim(claim, ch=None)
        assert passed  # Can't verify = default pass
        assert "cannot verify" in output.lower()

    def test_formula_error_reported(self):
        claim = VerificationClaim(
            label="bad formula",
            value=10.0,
            formula="unknown_var - y",
            components={"y": 5.0},
        )
        output, passed = _verify_one_claim(claim, ch=None)
        assert not passed
        assert "FORMULA ERROR" in output

    def test_check_query_pass(self):
        """Mock CH to return a matching value."""
        ch = MagicMock()
        mock_result = MagicMock()
        mock_result.columns = ["balance"]
        mock_result.rows = [[349.6]]
        mock_result.row_count = 1
        mock_result.rows_returned = 1
        mock_result.elapsed_seconds = 0.1
        mock_result.database = "dbt"
        mock_result.sql = "SELECT ..."
        mock_result.warnings = []
        ch.run_query.return_value = MagicMock()
        ch.build_query_result.return_value = mock_result

        claim = VerificationClaim(
            label="balance check",
            value=349.6,
            check_query="SELECT balance FROM ...",
        )

        with patch("cerebro_mcp.tools.governance.cross_check.format_results_table", return_value="| balance |\n| 349.6 |"):
            output, passed = _verify_one_claim(claim, ch)

        assert passed
        assert "CHECK PASS" in output

    def test_check_query_mismatch(self):
        ch = MagicMock()
        mock_result = MagicMock()
        mock_result.columns = ["balance"]
        mock_result.rows = [[349.6]]
        mock_result.row_count = 1
        mock_result.rows_returned = 1
        mock_result.elapsed_seconds = 0.1
        mock_result.database = "dbt"
        mock_result.sql = "SELECT ..."
        mock_result.warnings = []
        ch.run_query.return_value = MagicMock()
        ch.build_query_result.return_value = mock_result

        claim = VerificationClaim(
            label="bad claim",
            value=1360.0,
            check_query="SELECT balance FROM ...",
        )

        with patch("cerebro_mcp.tools.governance.cross_check.format_results_table", return_value="| balance |\n| 349.6 |"):
            output, passed = _verify_one_claim(claim, ch)

        assert not passed
        assert "CHECK MISMATCH" in output

    def test_check_query_failure(self):
        ch = MagicMock()
        ch.run_query.side_effect = Exception("Connection timeout")

        claim = VerificationClaim(
            label="timeout test",
            value=100.0,
            check_query="SELECT ...",
        )
        output, passed = _verify_one_claim(claim, ch)
        # Check query failure doesn't fail the claim if no arithmetic check
        assert "CHECK FAILED" in output


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestFirstNumeric:
    def test_finds_first_number(self):
        assert _first_numeric(["a", "b"], [[349.6, "hello"]]) == 349.6

    def test_skips_strings(self):
        assert _first_numeric(["a", "b"], [["hello", 42]]) == 42.0

    def test_empty_rows(self):
        assert _first_numeric(["a"], []) is None

    def test_no_numerics(self):
        assert _first_numeric(["a"], [["hello"]]) is None


# ---------------------------------------------------------------------------
# Integration test (mocked)
# ---------------------------------------------------------------------------


class TestMultipleClaims:
    def test_mixed_pass_fail(self):
        claims = [
            VerificationClaim(
                label="good math",
                value=40.0,
                formula="a - b",
                components={"a": 100.0, "b": 60.0},
            ),
            VerificationClaim(
                label="bad math",
                value=999.0,
                formula="x + y",
                components={"x": 1.0, "y": 2.0},
            ),
        ]
        results = []
        for c in claims:
            _, ok = _verify_one_claim(c, ch=None)
            results.append(ok)

        assert results == [True, False]


class TestVerificationClaimModel:
    def test_defaults(self):
        claim = VerificationClaim(label="test", value=42.0)
        assert claim.formula == ""
        assert claim.components == {}
        assert claim.check_query == ""
        assert claim.check_database == "dbt"
        assert claim.tolerance_pct == 0.01
