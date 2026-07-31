"""Authz store + v1 owner identity tests (connector plan R10 §4.4/5.2/C6).

Every STRICT-schema transition is exercised against the real schema — the
R6-era plan contradicted its own CHECK constraints precisely because no
test ran the transitions.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from cerebro_mcp.runtime import identity
from cerebro_mcp.workflow.authz_store import (
    AuthzStore,
    AuthzUnavailable,
    mint_auth_id,
    publish_file_atomically,
)


@pytest.fixture
def store(tmp_path):
    s = AuthzStore(tmp_path / "authz.db")
    yield s
    s.close()


@pytest.fixture
def owner_key(monkeypatch):
    monkeypatch.setenv("CEREBRO_OWNER_KEY_V1", "k" * 32)
    identity.reset_owner_key_cache_for_tests()
    yield
    identity.reset_owner_key_cache_for_tests()


# -- schema and publication -------------------------------------------------


def test_every_kind_and_status_transition(store, tmp_path):
    """'report' is the actual normal kind; 'missing' a legal status."""
    for kind in ("report", "research", "case_study", "story"):
        rid = f"id_{kind}"
        auth_id = store.begin_publication(
            report_id=rid, owner_hash="v1:abc", filename=f"{rid}.html", kind=kind
        )
        assert len(auth_id) == 32  # 128-bit hex
        store.mark_ready(rid)
        assert store.get_report(rid).status == "ready"


def test_unknown_kind_rejected_by_schema(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.begin_publication(
            report_id="x", owner_hash=None, filename="x.html", kind="dashboard"
        )


def test_mark_ready_requires_pending(store):
    with pytest.raises(AuthzUnavailable, match="publication protocol"):
        store.mark_ready("never_began")


def test_abort_publication_removes_pending_only(store):
    store.begin_publication(
        report_id="a", owner_hash=None, filename="a.html", kind="report"
    )
    store.abort_publication("a")
    assert store.get_report("a") is None


def test_auth_id_unique_and_immutable(store):
    a1 = store.begin_publication(
        report_id="r1", owner_hash="v1:x", filename="r1.html", kind="report"
    )
    a2 = store.begin_publication(
        report_id="r2", owner_hash="v1:x", filename="r2.html", kind="report"
    )
    assert a1 != a2
    assert store.get_report("r1").auth_id == a1


def test_owner_scoped_listing(store):
    for rid, owner in (("o1", "v1:alice"), ("o2", "v1:bob"), ("o3", None)):
        store.begin_publication(
            report_id=rid, owner_hash=owner, filename=f"{rid}.html", kind="report"
        )
        store.mark_ready(rid)
    alice = {r.report_id for r in store.list_reports_for_owner("v1:alice")}
    assert alice == {"o1"}
    with_unowned = {
        r.report_id
        for r in store.list_reports_for_owner("v1:alice", include_unowned=True)
    }
    assert with_unowned == {"o1", "o3"}
    # None = stdio/single-tenant fallback sees everything
    assert len(store.list_reports_for_owner(None)) == 3


def test_publish_file_atomically_writes_and_replaces(tmp_path):
    target = tmp_path / "reports" / "r.html"
    publish_file_atomically(b"v1", target)
    assert target.read_bytes() == b"v1"
    publish_file_atomically(b"v2", target)
    assert target.read_bytes() == b"v2"
    assert not target.with_suffix(".html.tmp").exists()


# -- the four crash divergences (R10 §5.4) ---------------------------------


def test_reconcile_four_divergences(store, tmp_path):
    rdir = tmp_path / "reports"
    rdir.mkdir()

    # 1. pending row, no file -> row deleted
    store.begin_publication(
        report_id="p_nofile", owner_hash=None, filename="p_nofile.html", kind="report"
    )
    # 2. stale pending pair (row + file older than grace) -> both deleted
    store.begin_publication(
        report_id="p_stale", owner_hash=None, filename="p_stale.html", kind="report"
    )
    (rdir / "p_stale.html").write_text("stale")
    store._conn.execute(  # age the row past the grace window
        "UPDATE reports SET created_at = ? WHERE report_id = 'p_stale'",
        (int(time.time()) - 7200,),
    )
    # 3. file, no row -> quarantined, NOT adopted
    (rdir / "cerebro_report_20260101_orphan_deadbeef.html").write_text("orphan")
    # 4. ready row, no file -> status='missing'
    store.begin_publication(
        report_id="r_lost", owner_hash=None, filename="r_lost.html", kind="report"
    )
    store.mark_ready("r_lost")

    summary = store.reconcile(rdir)

    assert store.get_report("p_nofile") is None
    assert summary["pending_deleted"] == 1
    assert store.get_report("p_stale") is None
    assert not (rdir / "p_stale.html").exists()
    assert summary["stale_pair_deleted"] == 1
    assert summary["quarantined"] == [
        "cerebro_report_20260101_orphan_deadbeef.html"
    ]
    assert store.get_report("r_lost").status == "missing"
    # missing rows deny: they are not in any ready listing
    assert store.list_reports_for_owner(None) == []


def test_reconcile_leaves_healthy_pairs_alone(store, tmp_path):
    rdir = tmp_path / "reports"
    rdir.mkdir()
    store.begin_publication(
        report_id="ok", owner_hash="v1:a", filename="ok.html", kind="report"
    )
    (rdir / "ok.html").write_text("x")
    store.mark_ready("ok")
    summary = store.reconcile(rdir)
    assert store.get_report("ok").status == "ready"
    assert summary == {
        "pending_deleted": 0,
        "stale_pair_deleted": 0,
        "quarantined": [],
        "marked_missing": 0,
    }


# -- backfill ---------------------------------------------------------------


def _kind_parser(name: str):
    for kind in ("case_study", "research", "report", "story"):
        if name.startswith(f"cerebro_{kind}_"):
            return kind
    return None


def test_backfill_idempotent_and_rejecting(store, tmp_path):
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "cerebro_report_20260101_x_aaaa1111.html").write_text("a")
    (rdir / "cerebro_case_study_20260101_y_bbbb2222.html").write_text("b")
    (rdir / "unclassifiable.html").write_text("c")
    link = rdir / "cerebro_report_20260101_link_cccc3333.html"
    link.symlink_to(rdir / "cerebro_report_20260101_x_aaaa1111.html")

    first = store.backfill_legacy(rdir, kind_parser=_kind_parser)
    assert first["added"] == 2
    assert ("unclassifiable.html", "unparseable kind None") in first["rejected"]
    assert any(name == link.name and why == "symlink" for name, why in first["rejected"])

    second = store.backfill_legacy(rdir, kind_parser=_kind_parser)
    assert second["added"] == 0 and second["skipped"] == 2

    rows = store.list_reports_for_owner(None)
    assert all(r.owner_hash is None and r.status == "ready" for r in rows)


# -- tombstones, watermarks, audit -----------------------------------------


def test_deny_unblock_audit_trail(store):
    store.deny_subject("v1:mallory", actor="ops@x", reason="incident-42")
    assert store.is_denied("v1:mallory")
    store.unblock_subject("v1:mallory", actor="lead@x", reason="cleared")
    assert not store.is_denied("v1:mallory")
    # re-deny after unblock re-arms the tombstone
    store.deny_subject("v1:mallory", actor="ops@x", reason="again")
    assert store.is_denied("v1:mallory")
    audit = store.denial_audit("v1:mallory")
    assert [(a, actor) for a, actor, *_ in audit] == [
        ("deny", "ops@x"),
        ("unblock", "lead@x"),
        ("deny", "ops@x"),
    ]


def test_is_denied_fails_closed_on_store_error(store):
    store._conn.close()
    with pytest.raises(AuthzUnavailable):
        store.is_denied("v1:anyone")


def test_revocation_watermark_roundtrip(store):
    assert store.revocation_watermark("v1:a") is None
    store.set_revocation_watermark("v1:a", 1000)
    store.set_revocation_watermark("v1:a", 2000)  # upsert wins
    assert store.revocation_watermark("v1:a") == 2000


# -- owner key (C6.5 / A5) --------------------------------------------------


def test_owner_key_required_and_min_length(monkeypatch):
    monkeypatch.delenv("CEREBRO_OWNER_KEY_V1", raising=False)
    identity.reset_owner_key_cache_for_tests()
    with pytest.raises(identity.OwnerKeyError):
        identity.owner_hash_v1("https://iss", "sub")
    monkeypatch.setenv("CEREBRO_OWNER_KEY_V1", "short")
    identity.reset_owner_key_cache_for_tests()
    with pytest.raises(identity.OwnerKeyError):
        identity.owner_hash_v1("https://iss", "sub")
    identity.reset_owner_key_cache_for_tests()


def test_owner_hash_v1_shape_and_determinism(owner_key):
    h1 = identity.owner_hash_v1("https://iss", "auth0|alice")
    h2 = identity.owner_hash_v1("https://iss", "auth0|alice")
    assert h1 == h2 and h1.startswith("v1:") and len(h1) == 3 + 64
    # issuer NUL subject framing: moving a char across the boundary differs
    assert identity.owner_hash_v1("https://issx", "alice") != identity.owner_hash_v1(
        "https://iss", "xalice"
    )


def test_fingerprint_mismatch_fails_boot(store, owner_key):
    store.check_owner_key_fingerprint("v1", identity.owner_key_fingerprint())
    # same key again: fine
    store.check_owner_key_fingerprint("v1", identity.owner_key_fingerprint())
    with pytest.raises(AuthzUnavailable, match="fingerprint mismatch"):
        store.check_owner_key_fingerprint("v1", "f" * 16)


def test_prehashed_bridge_does_not_double_hash(owner_key):
    h = identity.owner_hash_v1("https://iss", "alice")
    token = identity.set_current_owner_prehashed(h)
    try:
        assert identity.get_current_owner() == h  # NOT hash(h)
    finally:
        identity.reset_current_owner(token)


def test_legacy_setter_still_hashes(owner_key):
    token = identity.set_current_owner("alice@gnosis.io")
    try:
        got = identity.get_current_owner()
        assert got != "alice@gnosis.io" and not got.startswith("v1:")
    finally:
        identity.reset_current_owner(token)
