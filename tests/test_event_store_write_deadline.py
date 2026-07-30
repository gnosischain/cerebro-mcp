"""The event-store write must never block a tool call.

Regression guard for a stranded storyteller pipeline: every gate had passed
(12 charts, storyboard, 8 visual specs, final story, clarity review) but
`storyteller_record_accessibility_pass` never returned. That tool is a bool
assignment plus one event write; `storyteller_status` — same lock, zero
filesystem calls — stayed instant throughout.

The module contract is that event-log writes are observability and must never
break a tool. Catching exceptions delivers half of it: a write that BLOCKS is
not an exception. `sqlite3.connect(timeout=...)` bounds only SQLite's BUSY
handler, not mkdir/stat/open, fsync, WAL setup, or the close-time checkpoint,
and `runtime/offload.py` adds no deadline either.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time

import pytest

from cerebro_mcp.config import settings
from cerebro_mcp.workflow import event_store_sync as es


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "EVENT_STORE_PATH", str(tmp_path / "state.db"), raising=False
    )
    monkeypatch.setattr(
        settings, "EVENT_STORE_WRITE_TIMEOUT_SECONDS", 0.3, raising=False
    )
    monkeypatch.setattr(
        settings, "EVENT_STORE_DEGRADED_COOLDOWN_SECONDS", 1.0, raising=False
    )
    es._reset_write_state()
    es._reset_bootstrap_cache()
    yield es
    es._reset_write_state()
    es._reset_bootstrap_cache()


def _wedge(monkeypatch, seconds: float = 30.0):
    """Make the connection step block, as a stalled filesystem would."""
    monkeypatch.setattr(es, "_connect", lambda: time.sleep(seconds))


def test_healthy_write_succeeds(store):
    assert store.create_workflow_safe("wf", "storyteller") is True
    assert store.append_event_safe("wf", "phase_advanced", {"a": 1}) == 1
    assert store.event_store_stats()["degraded"] is False


def test_wedged_write_returns_within_the_deadline(store, monkeypatch):
    store.create_workflow_safe("wf", "storyteller")
    _wedge(monkeypatch)

    started = time.monotonic()
    result = store.append_event_safe("wf", "phase_advanced", {"a": 1})
    elapsed = time.monotonic() - started

    assert result is None
    # The whole point: the caller walks away instead of hanging for minutes.
    assert elapsed < 2.0, f"write blocked for {elapsed:.1f}s"

    stats = store.event_store_stats()
    assert stats["degraded"] is True
    assert stats["timeouts"] == 1
    assert "deadline" in (stats["last_error"] or "")


def test_degraded_store_short_circuits_instead_of_paying_the_deadline(
    store, monkeypatch
):
    store.create_workflow_safe("wf", "storyteller")
    _wedge(monkeypatch)
    store.append_event_safe("wf", "phase_advanced", {})

    started = time.monotonic()
    for _ in range(5):
        store.append_event_safe("wf", "phase_advanced", {})
    elapsed = time.monotonic() - started

    # A wedged filesystem degrades observability; it must not tax every call.
    assert elapsed < 0.2, f"5 degraded calls took {elapsed:.2f}s"
    assert store.event_store_stats()["skipped_while_degraded"] == 5


def test_store_recovers_once_the_filesystem_heals(store, monkeypatch):
    store.create_workflow_safe("wf", "storyteller")
    real_connect = es._connect
    _wedge(monkeypatch)
    store.append_event_safe("wf", "phase_advanced", {})
    assert store.event_store_stats()["degraded"] is True

    monkeypatch.setattr(es, "_connect", real_connect)
    time.sleep(settings.EVENT_STORE_DEGRADED_COOLDOWN_SECONDS + 0.1)

    assert store.append_event_safe("wf", "phase_advanced", {"a": 2}) is not None
    assert store.event_store_stats()["degraded"] is False


def test_owner_contextvar_survives_the_worker_thread(store):
    """The write runs on a worker; contextvars do not cross threads.

    Without `contextvars.copy_context()`, every workflow row silently loses
    its owner — the write still 'succeeds', so nothing surfaces it.
    """
    from cerebro_mcp.runtime.identity import (
        reset_current_owner,
        set_current_owner,
    )

    token = set_current_owner("alice@gnosis.io")
    try:
        assert store.create_workflow_safe("wf_owner", "storyteller") is True
    finally:
        reset_current_owner(token)

    conn = es._connect()
    row = conn.execute(
        "SELECT owner FROM workflows WHERE id = ?", ("wf_owner",)
    ).fetchone()
    assert row is not None and row[0], "owner was dropped crossing the thread"


def test_wedged_worker_does_not_block_process_exit():
    """A daemon worker, not a ThreadPoolExecutor.

    `concurrent.futures` registers an atexit hook that JOINS its workers, so a
    wedged pool thread delays interpreter exit even after
    `shutdown(wait=False)`. That would turn a tool hang into a shutdown hang:
    SIGTERM ignored until the pod's grace period expires, which with a
    ReadWriteOnce PVC and `strategy = "Recreate"` stalls the whole rollout.
    """
    script = textwrap.dedent(
        """
        import tempfile, time
        from cerebro_mcp.config import settings
        settings.EVENT_STORE_PATH = tempfile.mkdtemp() + "/s.db"
        settings.EVENT_STORE_WRITE_TIMEOUT_SECONDS = 0.3
        from cerebro_mcp.workflow import event_store_sync as es
        es._connect = lambda: time.sleep(30)
        es.append_event_safe("wf", "phase_advanced", {})
        print("done")
        """
    )
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=25
    )
    elapsed = time.monotonic() - started

    assert "done" in proc.stdout
    assert elapsed < 15, (
        f"process took {elapsed:.1f}s to exit with a 30s-wedged write thread"
    )


def test_worker_thread_is_daemon(store):
    store.create_workflow_safe("wf", "storyteller")
    workers = [t for t in threading.enumerate() if t.name == "cerebro-event-store"]
    assert workers, "no event-store worker thread running"
    assert all(t.daemon for t in workers)


def test_probe_reports_unwritable_path(tmp_path, monkeypatch):
    """A bad EVENT_STORE_PATH should be answerable at boot, not surface as a
    hang on the first storyteller write."""
    monkeypatch.setattr(
        settings,
        "EVENT_STORE_PATH",
        "/proc/definitely-not-writable/state.db",
        raising=False,
    )
    ok, err = es.probe_event_store_writable()
    assert ok is False
    assert err
