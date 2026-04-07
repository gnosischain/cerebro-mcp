"""Tests for ClickHouse parameterized query support in clickhouse_client.py."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cerebro_mcp.clickhouse_client import ClickHouseManager


class TestFetchRowsNativePassesParameters:
    def test_parameters_forwarded_to_client_query(self):
        manager = ClickHouseManager()
        mock_client = MagicMock()
        mock_client.query.return_value = SimpleNamespace(
            column_names=["token", "vol"],
            result_rows=[["GNO", 100]],
        )

        params = {"token": "GNO"}
        manager._fetch_rows_native(mock_client, "SELECT 1", parameters=params)

        mock_client.query.assert_called_once_with(
            "SELECT 1", parameters=params
        )


class TestFetchRowsSkipsArrowWithParameters:
    def test_arrow_skipped_when_parameters_present(self):
        manager = ClickHouseManager()
        mock_client = MagicMock()
        mock_client.query.return_value = SimpleNamespace(
            column_names=["id"],
            result_rows=[[1]],
        )

        columns, rows, mode, warnings = manager._fetch_rows(
            mock_client,
            "SELECT 1",
            fetch_mode="auto",
            parameters={"x": 1},
        )

        # Arrow path should NOT have been attempted
        mock_client.query_arrow.assert_not_called()
        # Native path should have been used
        mock_client.query.assert_called_once()
        assert mode == "rows"

    def test_arrow_attempted_when_no_parameters(self):
        manager = ClickHouseManager()
        mock_client = MagicMock()
        mock_client.query_arrow.side_effect = RuntimeError("no arrow")
        mock_client.query.return_value = SimpleNamespace(
            column_names=["id"],
            result_rows=[[1]],
        )

        columns, rows, mode, warnings = manager._fetch_rows(
            mock_client,
            "SELECT 1",
            fetch_mode="auto",
            parameters=None,
        )

        # Arrow was attempted (and failed, triggering fallback)
        mock_client.query_arrow.assert_called_once()
        assert "arrow_fallback_to_row_fetch" in warnings


class TestRunQuerySignature:
    def test_parameters_keyword_in_signature(self):
        sig = inspect.signature(ClickHouseManager.run_query)
        assert "parameters" in sig.parameters
        param = sig.parameters["parameters"]
        assert param.default is None
