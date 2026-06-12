"""Shared fakes for the rpc_scan test suite."""
from __future__ import annotations

import threading
from typing import Any, Callable

from cerebro_mcp.rpc_scan.scratch import ScratchStore


class FakeQueryResult:
    def __init__(self, rows: list[list[Any]]):
        self.result_rows = rows


class FakeChWriteClient:
    """Stands in for a clickhouse_connect client inside ScratchStore.

    Records commands/inserts; scripted failures via ``fail_inserts`` (a
    countdown of inserts that raise before one succeeds).
    """

    def __init__(self):
        self.commands: list[str] = []
        self.inserts: list[tuple[str, list[str], list[list[Any]]]] = []
        self.fail_inserts = 0
        self.fail_uint256 = False
        self.query_handler: Callable[[str], list[list[Any]]] | None = None

    def command(self, sql: str):
        self.commands.append(sql)
        if self.fail_uint256 and "toUInt256" in sql:
            raise RuntimeError("Unknown function toUInt256")
        return None

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]):
        if self.fail_inserts > 0:
            self.fail_inserts -= 1
            raise RuntimeError("simulated insert failure")
        self.inserts.append((table, list(column_names), [list(r) for r in rows]))

    def query(self, sql: str):
        if self.query_handler:
            return FakeQueryResult(self.query_handler(sql))
        return FakeQueryResult([])

    def rows_for(self, table: str) -> list[list[Any]]:
        out: list[list[Any]] = []
        for t, _cols, rows in self.inserts:
            if t.endswith(table) or t.split(".")[-1] == table:
                out.extend(rows)
        return out


def make_store(client: FakeChWriteClient | None = None) -> tuple[ScratchStore, FakeChWriteClient]:
    client = client or FakeChWriteClient()
    store = ScratchStore(database="scratch", client_factory=lambda: client)
    return store, client


class InMemoryRegistryStore(ScratchStore):
    """ScratchStore with a dict-backed job registry (real validation/insert
    paths still exercised via the fake client; registry round-trips work)."""

    def __init__(self, client: FakeChWriteClient | None = None):
        self._fake_client = client or FakeChWriteClient()
        super().__init__(database="scratch", client_factory=lambda: self._fake_client)
        self.registry: dict[str, dict[str, Any]] = {}

    def upsert_job_row(self, row: dict[str, Any]) -> None:
        self.registry[row["job_id"]] = dict(row)

    def load_job_row(self, job_id: str) -> dict[str, Any] | None:
        row = self.registry.get(job_id)
        return dict(row) if row else None

    def list_job_rows(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in list(self.registry.values())[:limit]]

    def mark_orphans_on_startup(self) -> int:
        n = 0
        for row in self.registry.values():
            if row["status"] == "running":
                row["status"] = "partial"
                row["note"] = "server_restart"
                n += 1
        return n


class FakeRawClient:
    """Scripted raw JSON-RPC client. ``handler(method, params)`` returns the
    result or raises."""

    def __init__(self, handler: Callable[[str, list[Any]], Any]):
        self.handler = handler
        self.calls: list[tuple[str, list[Any]]] = []
        self.lock = threading.Lock()

    def request(self, method: str, params: list[Any]) -> Any:
        with self.lock:
            self.calls.append((method, params))
        return self.handler(method, params)


class FakeRouter:
    def __init__(self, handler: Callable[[str, list[Any]], Any],
                 latest: int = 1_000_000, archive: bool = True,
                 supported: dict[str, bool] | None = None):
        self.client = FakeRawClient(handler)
        self._latest = latest
        self._archive = archive
        self._supported = supported or {}

    @property
    def standard(self):
        return self.client

    @property
    def archive(self):
        if not self._archive:
            raise ValueError("This operation requires historical state: set GNOSIS_ARCHIVE_RPC_URL.")
        return self.client

    def has_archive(self) -> bool:
        return self._archive

    def for_capability(self, *, needs_archive: bool):
        if needs_archive and not self._archive:
            raise ValueError("This operation requires historical state: set GNOSIS_ARCHIVE_RPC_URL.")
        return self.client

    def supports(self, method: str) -> bool:
        return self._supported.get(method, True)

    def retry(self, fn, *, tries=None, base_sleep=0.25):
        return fn()

    def latest_block(self) -> int:
        return self._latest


class FakeCH:
    """Stands in for ClickHouseManager in engine address resolution."""

    def __init__(self, describe_rows: list[list[Any]] | None = None,
                 result_rows: list[list[Any]] | None = None):
        self.describe_rows = describe_rows if describe_rows is not None else [["address"]]
        self.result_rows = result_rows or []
        self.queries: list[str] = []

    def get_client(self, database: str):
        outer = self

        class _Client:
            def query(self, sql: str):
                outer.queries.append(sql)
                if sql.strip().upper().startswith("DESCRIBE"):
                    return FakeQueryResult(outer.describe_rows)
                return FakeQueryResult(outer.result_rows)

        return _Client()


def wait_for_terminal(job, timeout: float = 10.0) -> None:
    import time

    from cerebro_mcp.rpc_scan.jobs import TERMINAL_STATUSES

    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in TERMINAL_STATUSES:
            return
        time.sleep(0.02)
    raise AssertionError(f"job stuck in status {job.status!r}")
