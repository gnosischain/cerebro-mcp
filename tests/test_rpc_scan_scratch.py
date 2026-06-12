"""ScratchStore hardening and BatchInserter durability contract."""
import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.rpc_scan.scratch import BatchInserter, ScratchStore
from tests.rpc_scan_fakes import FakeChWriteClient, make_store


def test_database_identifier_validated():
    with pytest.raises(ValueError, match="invalid scratch database name"):
        ScratchStore(database="scratch; DROP TABLE x")


def test_table_name_regex_rejects_arbitrary_tables():
    store, _ = make_store()
    with pytest.raises(ValueError, match="illegal scratch table name"):
        store.create_scan_table("users", ["`a` String"], "ORDER BY (a)")
    with pytest.raises(ValueError, match="illegal scratch table name"):
        store.insert_rows("rpc_logs_zzzzzzzz; DROP", ["a"], [[1]])


def test_valid_scan_table_names_accepted():
    store, client = make_store()
    store.create_scan_table(
        "rpc_logs_a1b2c3d4", ["`a` String", "`_scanned_at` DateTime DEFAULT now()"],
        "ORDER BY (a)",
    )
    assert any("rpc_logs_a1b2c3d4" in c for c in client.commands)
    assert any("ReplacingMergeTree(_scanned_at)" in c for c in client.commands)


def test_insert_initializes_client_without_prior_ensure_ready():
    store, client = make_store()
    store.insert_rows("rpc_logs_a1b2c3d4", ["a"], [["x"]])
    assert client.inserts and client.inserts[0][0] == "scratch.rpc_logs_a1b2c3d4"


def test_insert_retries_and_reconnects(monkeypatch):
    monkeypatch.setattr("cerebro_mcp.rpc_scan.scratch.time.sleep", lambda s: None)
    factory_calls = []
    client = FakeChWriteClient()
    client.fail_inserts = 1

    def factory():
        factory_calls.append(1)
        return client

    store = ScratchStore(database="scratch", client_factory=factory)
    store.insert_rows("rpc_logs_a1b2c3d4", ["a"], [["x"]])
    assert len(client.inserts) == 1
    assert len(factory_calls) == 2  # reconnected after the failed attempt


def test_uint256_probe_falls_back_to_decimal():
    store, client = make_store()
    client.fail_uint256 = True
    assert store.uint256_type() == "Decimal(76, 0)"

    store2, _ = make_store()
    assert store2.uint256_type() == "UInt256"


def test_ensure_ready_grant_error_names_the_grant():
    class DenyingClient(FakeChWriteClient):
        def command(self, sql):
            raise RuntimeError("Not enough privileges")

    store = ScratchStore(database="scratch", client_factory=DenyingClient)
    with pytest.raises(RuntimeError, match="GRANT CREATE DATABASE, CREATE TABLE, INSERT"):
        store.ensure_ready()


def test_batch_inserter_keeps_rows_on_flush_failure(monkeypatch):
    monkeypatch.setattr("cerebro_mcp.rpc_scan.scratch.time.sleep", lambda s: None)
    monkeypatch.setattr(settings, "RPC_SCAN_INSERT_MAX_RETRIES", 1)
    store, client = make_store()
    flushed = []
    inserter = BatchInserter(store, "rpc_logs_a1b2c3d4", ["a"],
                             on_flush=lambda n: flushed.append(n))
    inserter.add(["row1"])
    client.fail_inserts = 1
    with pytest.raises(RuntimeError, match="simulated insert failure"):
        inserter.flush()
    assert flushed == []  # on_flush never fired
    # Retry re-inserts the SAME retained rows.
    assert inserter.flush() == 1
    assert flushed == [1]
    assert client.rows_for("rpc_logs_a1b2c3d4") == [["row1"]]


def test_batch_inserter_auto_flushes_at_batch_rows(monkeypatch):
    monkeypatch.setattr(settings, "RPC_SCAN_INSERT_BATCH_ROWS", 3)
    store, client = make_store()
    inserter = BatchInserter(store, "rpc_logs_a1b2c3d4", ["a"])
    for i in range(3):
        inserter.add([i])
    assert client.rows_for("rpc_logs_a1b2c3d4") == [[0], [1], [2]]


def test_registry_roundtrip_and_orphan_marking():
    store, client = make_store()
    captured: dict[str, list[list]] = {}

    def handler(sql):
        if "WHERE status = 'running'" in sql:
            return captured.get("running", [])
        if "WHERE job_id" in sql:
            return captured.get("load", [])
        return []

    client.query_handler = handler
    store.upsert_job_row({
        "job_id": "ab12cd34", "kind": "logs", "label": "x",
        "table_name": "rpc_logs_ab12cd34", "spec_json": "{}",
        "status": "running", "cursor_json": "{}", "rows_written": 5,
        "note": "", "created_at": 1.0, "updated_at": None,
    })
    inserted = client.inserts[-1]
    assert inserted[0] == "scratch.rpc_scan_jobs"
    row = dict(zip(inserted[1], inserted[2][0]))
    assert row["job_id"] == "ab12cd34" and row["status"] == "running"

    # Orphan marking flips running -> partial with a note.
    captured["running"] = [inserted[2][0]]
    assert store.mark_orphans_on_startup() == 1
    flipped = dict(zip(client.inserts[-1][1], client.inserts[-1][2][0]))
    assert flipped["status"] == "partial" and flipped["note"] == "server_restart"


def test_load_job_row_rejects_malformed_ids():
    store, _ = make_store()
    assert store.load_job_row("nope'; DROP --") is None
