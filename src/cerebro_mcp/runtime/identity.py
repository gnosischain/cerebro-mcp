"""Per-request caller identity for workflow ownership.

Cerebro authenticates connections (bearer token, env-var-gated stdio)
but never knew *who* a connection represented. This module adds a
`current_owner` contextvar that:

  - is set by the SSE middleware (from the `X-Cerebro-Owner` header) or
    the stdio bootstrap (from the `CEREBRO_OWNER` env var),
  - is read by event-log writes (to stamp the workflow row's `owner`
    column) and event-log reads (to filter by caller),
  - holds **only the hashed identifier**, never the plaintext.

Privacy property
================

The plaintext identifier (e.g. `alice@gnosis.io`) never persists. It is
hashed at the `set_current_owner()` boundary; downstream callers,
contextvar reads, the SQLite event log, and any logs all see only the
SHA-256 digest. If the cerebro_state.db file is exfiltrated, an
attacker sees opaque hex strings rather than email addresses.

If `CEREBRO_OWNER_HASH_SALT` is set in the environment, the salt is
prepended before hashing so two cerebro-mcp deployments produce
different hashes for the same person — defeats pre-computed rainbow
tables across deployments.

Trade-off: ops can't read "whose workflow is this?" directly from the
DB. If you need that, keep a separate plaintext→hash map outside the
event log; don't try to recover it from the hash.
"""

from __future__ import annotations

import contextvars
import hashlib
import os


_current_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_owner", default=None,
)


def _hash_owner(plaintext: str) -> str:
    """SHA-256 of `salt + plaintext`, hex-encoded.

    Salt is read from `CEREBRO_OWNER_HASH_SALT` per call (cheap; lets
    operators rotate without restarting). Empty salt = stable across
    deployments, which is fine for single-deployment cases.
    """
    salt = os.environ.get("CEREBRO_OWNER_HASH_SALT", "")
    h = hashlib.sha256()
    if salt:
        h.update(salt.encode("utf-8"))
    h.update(plaintext.encode("utf-8"))
    return h.hexdigest()


def set_current_owner(
    plaintext: str | None,
) -> contextvars.Token[str | None]:
    """Set the owner for the current scope from a plaintext identifier.

    The plaintext is hashed before storage. Returns a `Token` so callers
    in middleware can `Token.reset()` in a try/finally to scope the
    owner to a single request:

        token = set_current_owner(header_value)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_owner(token)

    None / empty / whitespace-only input clears the owner (becomes the
    "no owner set" / single-tenant fallback).
    """
    if plaintext is None or not str(plaintext).strip():
        return _current_owner.set(None)
    return _current_owner.set(_hash_owner(str(plaintext).strip()))


def reset_current_owner(token: contextvars.Token[str | None]) -> None:
    """Restore the previous owner. Pair with `set_current_owner` in a
    try/finally inside request middleware."""
    _current_owner.reset(token)


def get_current_owner() -> str | None:
    """Return the hashed owner for the current scope, or None if unset.

    Downstream code (event-log writers, MCP tool wrappers) calls this to
    stamp / filter workflows by caller. NULL is the legitimate
    single-tenant fallback — never raise on missing owner.
    """
    return _current_owner.get()


def initial_stdio_owner() -> str | None:
    """Read `CEREBRO_OWNER` once at stdio process start.

    Empty / unset returns None (no owner stamping; single-tenant
    fallback). Whitespace is stripped.
    """
    val = (os.environ.get("CEREBRO_OWNER") or "").strip()
    return val or None


# ---------------------------------------------------------------------------
# v1 owner identity (connector plan R10 §5.2 / A5-A6)
# ---------------------------------------------------------------------------
#
# The legacy path above (salted SHA-256 of a SELF-ATTESTED header) stays for
# stdio and the documented internal telemetry use. Verified OAuth principals
# use the keyed v1 scheme below instead:
#
#   owner = "v1:" + hex(HMAC-SHA256(CEREBRO_OWNER_KEY_V1, issuer || NUL || subject))
#
# Differences from the legacy hash, each deliberate:
# - KEYED (HMAC), not salted-hash: the legacy salt may be EMPTY and is read
#   per request, so a hot change silently re-keys every owner (R9 P0-7).
#   The v1 key is mandatory (>= 32 bytes), read once, and IMMUTABLE for the
#   deployment; rotation is lazy aliasing at reauthentication, never an
#   in-place rewrite (issuer/subject are not persisted, so old hashes are
#   unrecoverable by construction).
# - VERSion-prefixed ("v1:") so a future key version can coexist during a
#   lazy migration and tombstones can be checked across aliases.
# - The (version, sha256-fingerprint) pair is persisted in the authz store
#   at boot; a mismatch fails startup (C6.5).

import hmac as _hmac
from dataclasses import dataclass

OWNER_KEY_VERSION = "v1"

_owner_key_cache: bytes | None = None


class OwnerKeyError(RuntimeError):
    """Missing/short owner key — HTTP boot must fail, never degrade."""


def _owner_key() -> bytes:
    global _owner_key_cache
    if _owner_key_cache is None:
        raw = (os.environ.get("CEREBRO_OWNER_KEY_V1") or "").strip()
        if len(raw.encode("utf-8")) < 32:
            raise OwnerKeyError(
                "CEREBRO_OWNER_KEY_V1 must be set and at least 32 bytes — "
                "verified-principal ownership refuses to run keyless."
            )
        _owner_key_cache = raw.encode("utf-8")
    return _owner_key_cache


def reset_owner_key_cache_for_tests() -> None:
    global _owner_key_cache
    _owner_key_cache = None


def owner_key_fingerprint() -> str:
    """Non-secret fingerprint persisted in the authz store (C6.5)."""
    return hashlib.sha256(_owner_key()).hexdigest()[:16]


def owner_hash_v1(issuer: str, subject: str) -> str:
    """The v1 owner identifier for a VERIFIED principal."""
    msg = issuer.encode("utf-8") + b"\x00" + subject.encode("utf-8")
    digest = _hmac.new(_owner_key(), msg, hashlib.sha256).hexdigest()
    return f"{OWNER_KEY_VERSION}:{digest}"


@dataclass(frozen=True)
class CerebroPrincipal:
    """The verified caller identity carried through a request (A6)."""

    kind: str            # "oauth" | "stdio"
    issuer: str
    subject: str
    client_id: str
    scopes: frozenset[str]
    owner: str           # owner_hash_v1(...) — already hashed


def set_current_owner_prehashed(
    owner_hash: str | None,
) -> contextvars.Token[str | None]:
    """Set an ALREADY-HASHED owner (v1 principal bridge).

    Distinct from `set_current_owner`, which hashes its input: passing a
    v1 owner hash through the legacy setter would hash it a second time
    and silently partition that caller's data from itself (R9-audit P1).
    """
    if owner_hash is None or not str(owner_hash).strip():
        return _current_owner.set(None)
    return _current_owner.set(str(owner_hash).strip())
