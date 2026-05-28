from types import SimpleNamespace
from unittest.mock import patch

from cerebro_mcp.clients.clickhouse import ClickHouseManager, ExecutedQuery


def test_get_client_uses_tls_and_timeout_settings():
    manager = ClickHouseManager()

    with patch("cerebro_mcp.clients.clickhouse.clickhouse_connect.get_client") as mock_get_client:
        mock_get_client.return_value = object()
        manager.get_client("dbt")

    kwargs = mock_get_client.call_args.kwargs
    assert kwargs["verify"] is True
    assert kwargs["connect_timeout"] > 0
    assert kwargs["send_receive_timeout"] > 0
    assert kwargs["settings"]["readonly"] == 1
    assert kwargs["settings"]["max_execution_time"] > 0


def test_run_query_caps_tool_rows_and_wraps_existing_limit():
    manager = ClickHouseManager()
    rows = [[idx] for idx in range(500)]
    fake_result = SimpleNamespace(column_names=["id"], result_rows=rows)
    fake_client = SimpleNamespace(
        query_arrow=lambda sql, parameters=None: (_ for _ in ()).throw(RuntimeError("no arrow")),
        query=lambda sql, parameters=None: fake_result,
    )

    with patch.object(manager, "get_client", return_value=fake_client):
        executed = manager.run_query(
            "SELECT * FROM t LIMIT 1000",
            database="dbt",
            requested_max_rows=500,
            audience="tool",
        )

    assert executed.executed_sql.startswith("SELECT * FROM (SELECT * FROM t LIMIT 1000)")
    assert executed.executed_sql.endswith("LIMIT 200")
    assert executed.row_count == 200
    assert "tool_row_cap_applied" in executed.warnings
    assert "limit_applied" in executed.warnings


def test_build_query_result_normalizes_and_truncates():
    manager = ClickHouseManager()
    executed = ExecutedQuery(
        sql="SELECT value FROM t",
        executed_sql="SELECT value FROM t LIMIT 200",
        database="dbt",
        columns=["value"],
        rows=[[2**60], [1.5], [None]],
        row_count=3,
        elapsed_seconds=0.1,
        fetch_mode="rows",
        warnings=["limit_applied"],
    )

    result = manager.build_query_result(executed, max_rows=2)

    assert result.rows == [[str(2**60)], [1.5]]
    assert result.rows_returned == 2
    assert result.row_count == 3
