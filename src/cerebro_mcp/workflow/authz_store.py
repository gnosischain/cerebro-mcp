"""Authoritative authorization store (connector plan R10 §4.4 / C6).

SEPARATE from the best-effort workflow event store by design: that log may
drop writes without breaking analysis; THIS store answers "may this caller
see this report?" and "is this subject revoked?", where a dropped write is
an authorization hole. Consequently everything here fails CLOSED:

- open/migrate errors raise — the HTTP server must not boot without it;
- ``synchronous=FULL`` (the event store's NORMAL is fine for telemetry,
  not for authorization state);
- a missing metadata row DENIES — never "legacy fallback";
- synchronous ``sqlite3`` (not aiosqlite): the hot-path reads are indexed
  point lookups consulted by token verification, and the fail-closed
  semantics must not depend on an event-loop thread hop.

Schema (STRICT, versioned):
    reports(report_id PK, auth_id UNIQUE NOT NULL, owner_hash, filename
            UNIQUE, kind, created_at, status)
    subject_revocations(owner_hash PK, min_iat)
    denied_subjects(owner_hash PK, denied_at, reason, unblocked_at,
            unblocked_by)          -- current STATE
    denial_events(id PK, owner_hash, action, actor, reason, ts)
                                    -- append-only AUDIT LOG (never updated)
    owner_key_meta(version PK, fingerprint)
    schema_version(version)

``auth_id`` is the immutable per-report authorization identity capabilities
sign (128-bit CSPRNG): owner-hash migrations may rewrite ``owner_hash``
freely without invalidating one live capability link (R10 P0-10).

Publication protocol (R10 C6.8):
    insert pending row -> write temp -> fsync file -> atomic rename ->
    fsync parent dir -> mark ready
A published file without a ``ready`` row stays inaccessible; startup
reconciliation resolves the four crash divergences explicitly.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

REPORT_KINDS = ("report", "research", "case_study", "story")
REPORT_STATUSES = ("pending", "ready", "missing")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS reports (
  report_id  TEXT PRIMARY KEY NOT NULL,
  auth_id    TEXT NOT NULL UNIQUE,
  owner_hash TEXT,
  filename   TEXT NOT NULL UNIQUE,
  kind       TEXT NOT NULL CHECK (kind IN {REPORT_KINDS!r}),
  created_at INTEGER NOT NULL,
  status     TEXT NOT NULL CHECK (status IN {REPORT_STATUSES!r})
) STRICT;
CREATE INDEX IF NOT EXISTS idx_reports_owner  ON reports(owner_hash);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE TABLE IF NOT EXISTS subject_revocations (
  owner_hash TEXT PRIMARY KEY NOT NULL,
  min_iat    INTEGER NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS denied_subjects (
  owner_hash   TEXT PRIMARY KEY NOT NULL,
  denied_at    INTEGER NOT NULL,
  reason       TEXT NOT NULL,
  unblocked_at INTEGER,
  unblocked_by TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS denial_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_hash TEXT NOT NULL,
  action     TEXT NOT NULL CHECK (action IN ('deny', 'unblock')),
  actor      TEXT NOT NULL,
  reason     TEXT NOT NULL,
  ts         INTEGER NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS owner_key_meta (
  version     TEXT PRIMARY KEY NOT NULL,
  fingerprint TEXT NOT NULL
) STRICT;
"""


class AuthzUnavailable(Exception):
    """The store cannot be consulted — callers must treat this as DENY."""


@dataclass(frozen=True)
class ReportRow:
    report_id: str
    auth_id: str
    owner_hash: str | None
    filename: str
    kind: str
    created_at: int
    status: str


def mint_auth_id() -> str:
    """128-bit CSPRNG hex — the immutable capability identity (C6.7)."""
    return secrets.token_hex(16)


class AuthzStore:
    """Fail-closed SQLite authorization store."""

    def __init__(self, path: str | os.PathLike[str]):
        self._path = Path(path)
        self._lock = threading.Lock()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._path), check_same_thread=False
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()
        except Exception as exc:  # noqa: BLE001 — any failure is fatal here
            raise AuthzUnavailable(
                f"authz store at {self._path} cannot be opened/migrated: {exc}"
            ) from exc

    def _migrate(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif row[0] > SCHEMA_VERSION:
                raise AuthzUnavailable(
                    f"authz schema version {row[0]} is newer than this "
                    f"build ({SCHEMA_VERSION}) — refusing to run against a "
                    "future schema."
                )

    def close(self) -> None:
        self._conn.close()

    # -- owner-key fingerprint (C6.5) -----------------------------------

    def check_owner_key_fingerprint(self, version: str, fingerprint: str) -> None:
        """Persist on first sight; fail HARD on mismatch afterwards.

        An accidentally swapped owner key would silently re-key every owner
        and orphan reports, tombstones and watermarks — mismatch is a boot
        error, never a warning.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT fingerprint FROM owner_key_meta WHERE version = ?",
                (version,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO owner_key_meta(version, fingerprint) VALUES (?, ?)",
                    (version, fingerprint),
                )
                return
            if row[0] != fingerprint:
                raise AuthzUnavailable(
                    f"owner key {version} fingerprint mismatch: store has "
                    f"{row[0][:8]}…, environment supplies {fingerprint[:8]}… "
                    "— refusing to start with a different key (it would "
                    "silently re-key every owner)."
                )

    # -- reports ---------------------------------------------------------

    def begin_publication(
        self,
        *,
        report_id: str,
        owner_hash: str | None,
        filename: str,
        kind: str,
    ) -> str:
        """Insert the pending row; returns the minted auth_id."""
        auth_id = mint_auth_id()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO reports(report_id, auth_id, owner_hash, filename,"
                " kind, created_at, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (report_id, auth_id, owner_hash, filename, kind, int(time.time())),
            )
        return auth_id

    def mark_ready(self, report_id: str) -> None:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE reports SET status = 'ready' "
                "WHERE report_id = ? AND status = 'pending'",
                (report_id,),
            )
            if cur.rowcount != 1:
                raise AuthzUnavailable(
                    f"mark_ready({report_id!r}): no pending row — the "
                    "publication protocol was violated."
                )

    def abort_publication(self, report_id: str) -> None:
        """Failed creation: remove the pending row (file was never published)."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM reports WHERE report_id = ? AND status = 'pending'",
                (report_id,),
            )

    def get_report(self, report_id: str) -> ReportRow | None:
        row = self._conn.execute(
            "SELECT report_id, auth_id, owner_hash, filename, kind,"
            " created_at, status FROM reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        return ReportRow(*row) if row else None

    def list_reports_for_owner(
        self, owner_hash: str | None, *, include_unowned: bool = False
    ) -> list[ReportRow]:
        """Owner-scoped listing. ``owner_hash=None`` (stdio / single-tenant
        fallback) sees everything — mirrors the event store's contract."""
        if owner_hash is None:
            rows = self._conn.execute(
                "SELECT report_id, auth_id, owner_hash, filename, kind,"
                " created_at, status FROM reports WHERE status = 'ready'"
                " ORDER BY created_at DESC"
            ).fetchall()
        elif include_unowned:
            rows = self._conn.execute(
                "SELECT report_id, auth_id, owner_hash, filename, kind,"
                " created_at, status FROM reports WHERE status = 'ready'"
                " AND (owner_hash = ? OR owner_hash IS NULL)"
                " ORDER BY created_at DESC",
                (owner_hash,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT report_id, auth_id, owner_hash, filename, kind,"
                " created_at, status FROM reports WHERE status = 'ready'"
                " AND owner_hash = ? ORDER BY created_at DESC",
                (owner_hash,),
            ).fetchall()
        return [ReportRow(*r) for r in rows]

    # -- crash reconciliation (R10 §5.4) --------------------------------

    def reconcile(self, report_dir: Path, *, pending_grace_s: int = 3600) -> dict:
        """Resolve the four startup divergences. Returns a summary dict.

        | divergence                         | action                      |
        |------------------------------------|-----------------------------|
        | pending row, no file               | delete row                  |
        | file + pending row older than 1h   | delete file AND row (WARN)  |
        | file, no row                       | QUARANTINE (report only)    |
        | ready row, no file                 | status='missing' (ERROR)    |
        """
        summary = {"pending_deleted": 0, "stale_pair_deleted": 0,
                   "quarantined": [], "marked_missing": 0}
        now = int(time.time())
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT report_id, filename, created_at, status FROM reports"
            ).fetchall()
            known_files = set()
            for report_id, filename, created_at, status in rows:
                known_files.add(filename)
                file_path = report_dir / filename
                exists = file_path.is_file()
                if status == "pending" and not exists:
                    self._conn.execute(
                        "DELETE FROM reports WHERE report_id = ?", (report_id,)
                    )
                    summary["pending_deleted"] += 1
                elif status == "pending" and exists and (
                    now - created_at > pending_grace_s
                ):
                    file_path.unlink(missing_ok=True)
                    self._conn.execute(
                        "DELETE FROM reports WHERE report_id = ?", (report_id,)
                    )
                    summary["stale_pair_deleted"] += 1
                    logger.warning(
                        "authz reconcile: stale pending pair removed: %s",
                        report_id,
                    )
                elif status == "ready" and not exists:
                    self._conn.execute(
                        "UPDATE reports SET status = 'missing' WHERE report_id = ?",
                        (report_id,),
                    )
                    summary["marked_missing"] += 1
                    logger.error(
                        "authz reconcile: ready report lost its file: %s",
                        report_id,
                    )
            if report_dir.is_dir():
                for f in report_dir.iterdir():
                    if f.is_file() and f.suffix == ".html" and f.name not in known_files:
                        # Do NOT auto-adopt: an unknown file has no owner and
                        # no auth_id; adoption would mint authorization for
                        # content nobody vouched for.
                        summary["quarantined"].append(f.name)
        if summary["quarantined"]:
            logger.warning(
                "authz reconcile: %d unknown report file(s) quarantined "
                "(visible to no one until backfilled): %s",
                len(summary["quarantined"]),
                summary["quarantined"][:10],
            )
        return summary

    # -- backfill (idempotent operator command) --------------------------

    def backfill_legacy(self, report_dir: Path, *, kind_parser) -> dict:
        """Register legacy files as owner_hash=NULL, status='ready'.

        Idempotent: files already indexed are skipped. Rejects symlinks and
        names ``kind_parser`` cannot classify. Runs BEFORE deny takes
        effect in the rollout, so no legitimate report vanishes.
        """
        added, skipped, rejected = 0, 0, []
        for f in sorted(report_dir.iterdir()) if report_dir.is_dir() else []:
            if not f.name.endswith(".html"):
                continue
            if f.is_symlink():
                rejected.append((f.name, "symlink"))
                continue
            kind = kind_parser(f.name)
            if kind not in REPORT_KINDS:
                rejected.append((f.name, f"unparseable kind {kind!r}"))
                continue
            report_id = f.stem.rsplit("_", 1)[-1]
            with self._lock, self._conn:
                dup = self._conn.execute(
                    "SELECT 1 FROM reports WHERE report_id = ? OR filename = ?",
                    (report_id, f.name),
                ).fetchone()
                if dup:
                    skipped += 1
                    continue
                self._conn.execute(
                    "INSERT INTO reports(report_id, auth_id, owner_hash,"
                    " filename, kind, created_at, status)"
                    " VALUES (?, ?, NULL, ?, ?, ?, 'ready')",
                    (
                        report_id,
                        mint_auth_id(),
                        f.name,
                        kind,
                        int(f.stat().st_mtime),
                    ),
                )
                added += 1
        return {"added": added, "skipped": skipped, "rejected": rejected}

    # -- revocation and tombstones (R10 P0-4 / C6) -----------------------

    def set_revocation_watermark(self, owner_hash: str, min_iat: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO subject_revocations(owner_hash, min_iat)"
                " VALUES (?, ?) ON CONFLICT(owner_hash)"
                " DO UPDATE SET min_iat = excluded.min_iat",
                (owner_hash, min_iat),
            )

    def revocation_watermark(self, owner_hash: str) -> int | None:
        try:
            row = self._conn.execute(
                "SELECT min_iat FROM subject_revocations WHERE owner_hash = ?",
                (owner_hash,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise AuthzUnavailable(str(exc)) from exc
        return row[0] if row else None

    def deny_subject(self, owner_hash: str, *, actor: str, reason: str) -> None:
        """Tombstone: written FIRST in the emergency sequence — this alone
        achieves the ≤60 s local guarantee because it rejects both existing
        tokens and freshly refreshed ones."""
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO denied_subjects(owner_hash, denied_at, reason)"
                " VALUES (?, ?, ?) ON CONFLICT(owner_hash) DO UPDATE SET"
                " denied_at = excluded.denied_at, reason = excluded.reason,"
                " unblocked_at = NULL, unblocked_by = NULL",
                (owner_hash, now, reason),
            )
            self._conn.execute(
                "INSERT INTO denial_events(owner_hash, action, actor, reason, ts)"
                " VALUES (?, 'deny', ?, ?, ?)",
                (owner_hash, actor, reason, now),
            )

    def unblock_subject(self, owner_hash: str, *, actor: str, reason: str) -> None:
        """Separately audited operation — a second operator action, never a
        side effect (state row updated; audit row appended)."""
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE denied_subjects SET unblocked_at = ?, unblocked_by = ?"
                " WHERE owner_hash = ?",
                (now, actor, owner_hash),
            )
            self._conn.execute(
                "INSERT INTO denial_events(owner_hash, action, actor, reason, ts)"
                " VALUES (?, 'unblock', ?, ?, ?)",
                (owner_hash, actor, reason, now),
            )

    def is_denied(self, owner_hash: str) -> bool:
        """Hot-path point lookup. Raises AuthzUnavailable on ANY store error
        — the verifier must convert that into a denial (fail closed)."""
        try:
            row = self._conn.execute(
                "SELECT 1 FROM denied_subjects WHERE owner_hash = ?"
                " AND unblocked_at IS NULL",
                (owner_hash,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise AuthzUnavailable(str(exc)) from exc
        return row is not None

    def denial_audit(self, owner_hash: str) -> list[tuple]:
        return self._conn.execute(
            "SELECT action, actor, reason, ts FROM denial_events"
            " WHERE owner_hash = ? ORDER BY id",
            (owner_hash,),
        ).fetchall()


_store: AuthzStore | None = None
_store_lock = threading.Lock()


def get_authz_store() -> AuthzStore:
    """Process-wide store singleton. Raises AuthzUnavailable on failure —
    callers on the auth hot path convert that into a DENY."""
    global _store
    with _store_lock:
        if _store is None:
            from cerebro_mcp.config import settings

            _store = AuthzStore(Path(settings.CEREBRO_AUTHZ_DB_PATH).expanduser())
        return _store


def reset_authz_store_for_tests() -> None:
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = None


def publish_file_atomically(content: bytes, final_path: Path) -> None:
    """temp write -> file fsync -> atomic rename -> PARENT-DIR fsync.

    The parent-directory fsync is the step naive implementations drop: the
    rename itself is metadata, and without fsyncing the directory a crash
    can lose the rename while the row says 'ready'.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = final_path.with_suffix(final_path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, final_path)
    dir_fd = os.open(final_path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
