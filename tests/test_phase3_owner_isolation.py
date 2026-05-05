"""Phase 3 — multi-tenant `owner` column + identity propagation.

Two layers:

1. `identity` module: contextvar + hashing semantics. Pure unit tests,
   no DB.
2. `EventStore` + `event_store_sync` filters: writes stamp the owner,
   reads scope by owner with the NULL-fallback rule, MCP tool wrappers
   deny cross-owner recompute.

Privacy assertion: plaintext identifiers ("alice@gnosis.io") MUST NEVER
appear in the contextvar, the SQLite columns, or the JSON payloads. The
suite asserts that explicitly so a future change that "helpfully" stores
the plaintext for debugging gets caught.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio

from cerebro_mcp import config as cerebro_config
from cerebro_mcp.event_store import EventStore
from cerebro_mcp.workflow_payloads import (
    WORKFLOW_RUNNING,
)


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Identity module — pure unit tests
# ---------------------------------------------------------------------------


class TestIdentityHashing:
    async def test_set_returns_hash_not_plaintext(self):
        from cerebro_mcp.identity import (
            get_current_owner,
            reset_current_owner,
            set_current_owner,
        )
        tok = set_current_owner("alice@gnosis.io")
        try:
            stored = get_current_owner()
            assert stored is not None
            assert len(stored) == 64  # sha256 hex
            assert "alice" not in stored
            assert "@" not in stored
        finally:
            reset_current_owner(tok)

    async def test_reset_restores_previous_state(self):
        from cerebro_mcp.identity import (
            get_current_owner,
            reset_current_owner,
            set_current_owner,
        )
        assert get_current_owner() is None
        tok = set_current_owner("alice@gnosis.io")
        assert get_current_owner() is not None
        reset_current_owner(tok)
        assert get_current_owner() is None

    async def test_empty_inputs_clear_owner(self):
        from cerebro_mcp.identity import (
            get_current_owner,
            reset_current_owner,
            set_current_owner,
        )
        for sentinel in (None, "", "   "):
            tok = set_current_owner(sentinel)
            try:
                assert get_current_owner() is None
            finally:
                reset_current_owner(tok)

    async def test_same_input_produces_stable_hash(self):
        from cerebro_mcp.identity import _hash_owner
        assert _hash_owner("alice@gnosis.io") == _hash_owner("alice@gnosis.io")

    async def test_different_inputs_distinct_hashes(self):
        from cerebro_mcp.identity import _hash_owner
        assert _hash_owner("alice@gnosis.io") != _hash_owner("bob@gnosis.io")

    async def test_salt_changes_hash(self, monkeypatch):
        from cerebro_mcp.identity import _hash_owner
        monkeypatch.delenv("CEREBRO_OWNER_HASH_SALT", raising=False)
        unsalted = _hash_owner("alice@gnosis.io")
        monkeypatch.setenv("CEREBRO_OWNER_HASH_SALT", "deployment-prod")
        salted = _hash_owner("alice@gnosis.io")
        assert unsalted != salted

    async def test_initial_stdio_owner_reads_env(self, monkeypatch):
        from cerebro_mcp.identity import initial_stdio_owner
        monkeypatch.setenv("CEREBRO_OWNER", "hugo@gnosis.io")
        assert initial_stdio_owner() == "hugo@gnosis.io"

    async def test_initial_stdio_owner_unset_returns_none(self, monkeypatch):
        from cerebro_mcp.identity import initial_stdio_owner
        monkeypatch.delenv("CEREBRO_OWNER", raising=False)
        assert initial_stdio_owner() is None

    async def test_initial_stdio_owner_empty_returns_none(self, monkeypatch):
        from cerebro_mcp.identity import initial_stdio_owner
        monkeypatch.setenv("CEREBRO_OWNER", "   ")
        assert initial_stdio_owner() is None


# ---------------------------------------------------------------------------
# EventStore — owner stamping + filtering
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> EventStore:
    s = EventStore(db_path=tmp_path / "owner_isolation.db",
                   compression_threshold=2_000_000)
    await s.init()
    return s


def _hash(s: str) -> str:
    """Local helper so tests can compare DB rows to expected hashes
    without importing the private function from identity."""
    from cerebro_mcp.identity import _hash_owner
    return _hash_owner(s)


class TestOwnerStamping:
    async def test_create_workflow_stamps_owner(self, store):
        await store.create_workflow("wid_a", "test", owner=_hash("alice"))
        wf = await store.get_workflow("wid_a")
        assert wf["owner"] == _hash("alice")

    async def test_create_workflow_without_owner_stays_null(self, store):
        await store.create_workflow("wid_n", "test")
        wf = await store.get_workflow("wid_n")
        assert wf["owner"] is None

    async def test_db_does_not_store_plaintext(self, tmp_path):
        """The DB must not contain `alice@gnosis.io` as plaintext."""
        s = EventStore(db_path=tmp_path / "leak_test.db")
        await s.init()
        from cerebro_mcp.identity import (
            get_current_owner,
            reset_current_owner,
            set_current_owner,
        )
        tok = set_current_owner("alice@gnosis.io")
        try:
            await s.create_workflow(
                "wid_x", "test",
                metadata={"hypothesis": "Alice's research"},
                owner=get_current_owner(),
            )
        finally:
            reset_current_owner(tok)
        # Read raw bytes from the DB. 'alice@gnosis.io' as a string must
        # not appear (the metadata blob about "Alice's research" is fine
        # — that's a hypothesis, not the identity).
        raw = (tmp_path / "leak_test.db").read_bytes()
        assert b"alice@gnosis.io" not in raw


class TestOwnerFiltering:
    async def test_get_workflow_blocks_cross_owner(self, store):
        await store.create_workflow("alice_wf", "test", owner=_hash("alice"))
        # Alice can read her own row.
        assert await store.get_workflow(
            "alice_wf", requesting_owner=_hash("alice"),
        ) is not None
        # Bob cannot.
        assert await store.get_workflow(
            "alice_wf", requesting_owner=_hash("bob"),
        ) is None

    async def test_get_workflow_null_owner_visible_to_all(self, store):
        await store.create_workflow("legacy_wf", "test", owner=None)
        for who in (_hash("alice"), _hash("bob"), None):
            wf = await store.get_workflow("legacy_wf", requesting_owner=who)
            assert wf is not None, f"NULL-owned row should be visible to {who!r}"

    async def test_list_workflows_filters_by_owner(self, store):
        await store.create_workflow("a1", "test", owner=_hash("alice"))
        await store.create_workflow("a2", "test", owner=_hash("alice"))
        await store.create_workflow("b1", "test", owner=_hash("bob"))
        await store.create_workflow("legacy", "test", owner=None)

        # Alice sees her two rows + the legacy NULL row.
        alice_view = await store.list_workflows(owner=_hash("alice"))
        ids = {w["id"] for w in alice_view}
        assert ids == {"a1", "a2", "legacy"}

        # Bob sees his row + the legacy NULL row.
        bob_view = await store.list_workflows(owner=_hash("bob"))
        ids = {w["id"] for w in bob_view}
        assert ids == {"b1", "legacy"}

        # No owner filter (admin / boot sweep) sees everything.
        admin_view = await store.list_workflows(owner=None)
        ids = {w["id"] for w in admin_view}
        assert ids == {"a1", "a2", "b1", "legacy"}

    async def test_list_workflows_strict_isolation_excludes_null(self, store):
        await store.create_workflow("a1", "test", owner=_hash("alice"))
        await store.create_workflow("legacy", "test", owner=None)
        view = await store.list_workflows(
            owner=_hash("alice"), include_unowned=False,
        )
        ids = {w["id"] for w in view}
        assert ids == {"a1"}

    async def test_list_workflows_no_owner_param_unchanged(self, store):
        """Backward compat: callers that don't pass `owner` see all rows
        (matches pre-Phase-3 behavior)."""
        await store.create_workflow("a1", "test", owner=_hash("alice"))
        await store.create_workflow("b1", "test", owner=_hash("bob"))
        await store.create_workflow("legacy", "test", owner=None)
        view = await store.list_workflows()  # no owner arg
        assert {w["id"] for w in view} == {"a1", "b1", "legacy"}


# ---------------------------------------------------------------------------
# Sync helper (event_store_sync.create_workflow_safe) reads contextvar
# ---------------------------------------------------------------------------


class TestSyncHelperReadsContextvar:
    async def test_create_workflow_safe_default_owner_from_contextvar(
        self, tmp_path, monkeypatch,
    ):
        """`create_workflow_safe` with no explicit `owner` should pick up
        the current contextvar — that's how research/quarterly/storyteller
        helpers automatically inherit the caller's owner."""
        monkeypatch.setattr(
            cerebro_config.settings, "EVENT_STORE_PATH",
            str(tmp_path / "sync_owner.db"),
            raising=True,
        )
        # Reset the bootstrap cache so the new path triggers DDL.
        from cerebro_mcp import event_store_sync as evs
        evs._reset_bootstrap_cache()

        from cerebro_mcp.identity import (
            reset_current_owner,
            set_current_owner,
        )
        tok = set_current_owner("alice@gnosis.io")
        try:
            ok = evs.create_workflow_safe("wid_a", "test")
        finally:
            reset_current_owner(tok)
        assert ok

        # Read back via async store on the same path.
        s = EventStore(db_path=tmp_path / "sync_owner.db")
        wf = await s.get_workflow("wid_a")
        assert wf["owner"] == _hash("alice@gnosis.io")

    async def test_explicit_owner_arg_overrides_contextvar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            cerebro_config.settings, "EVENT_STORE_PATH",
            str(tmp_path / "sync_explicit.db"),
            raising=True,
        )
        from cerebro_mcp import event_store_sync as evs
        evs._reset_bootstrap_cache()
        from cerebro_mcp.identity import (
            reset_current_owner,
            set_current_owner,
        )
        tok = set_current_owner("alice@gnosis.io")
        try:
            evs.create_workflow_safe(
                "wid_explicit", "test", owner=_hash("system"),
            )
        finally:
            reset_current_owner(tok)
        s = EventStore(db_path=tmp_path / "sync_explicit.db")
        wf = await s.get_workflow("wid_explicit")
        assert wf["owner"] == _hash("system")
