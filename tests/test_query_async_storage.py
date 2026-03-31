from pathlib import Path

from cerebro_mcp.clickhouse_client import ExecutedQuery
from cerebro_mcp.tools import query_async


def test_store_async_result_keeps_small_payload_in_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(query_async.settings, "ASYNC_RESULT_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(query_async.settings, "ASYNC_RESULT_MEMORY_THRESHOLD_BYTES", 10_000)

    executed = ExecutedQuery(
        sql="SELECT * FROM t",
        executed_sql="SELECT * FROM t LIMIT 3",
        database="dbt",
        columns=["id"],
        rows=[[1], [2], [3]],
        row_count=3,
        elapsed_seconds=0.1,
        fetch_mode="rows",
        warnings=[],
    )

    stored = query_async._store_async_result("job1", executed)

    assert stored.storage == "memory"
    assert stored.pages is not None
    assert query_async._load_async_page(stored, 0) == [[1], [2], [3]]


def test_store_async_result_spills_large_payload_to_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(query_async.settings, "ASYNC_RESULT_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(query_async.settings, "ASYNC_RESULT_MEMORY_THRESHOLD_BYTES", 1)

    executed = ExecutedQuery(
        sql="SELECT * FROM t",
        executed_sql="SELECT * FROM t LIMIT 2",
        database="dbt",
        columns=["id", "payload"],
        rows=[[1, "x" * 200], [2, "y" * 200]],
        row_count=2,
        elapsed_seconds=0.1,
        fetch_mode="rows",
        warnings=[],
    )

    stored = query_async._store_async_result("job2", executed)

    assert stored.storage == "disk"
    assert stored.result_dir is not None
    result_dir = Path(stored.result_dir)
    assert result_dir.exists()
    assert (result_dir / "manifest.json").exists()
    assert query_async._load_async_page(stored, 0)
