"""Signed report capabilities (R10 C6.7/A7, browser-plane decision §6).

A capability is a deliberately TRANSFERABLE single-object grant:

    cap = v1.<kid>.<exp>.<b64url(HMAC-SHA256(purpose_key, payload))>
    payload = b"v1\\x00" + report_id + b"\\x00" + auth_id + b"\\x00" + str(exp)

- It signs the report's immutable ``auth_id`` (authz store), NOT the owner
  hash — an owner-hash migration can rewrite ownership without breaking a
  single live link (R10 P0-10).
- Keys derive per PURPOSE from one root ``CEREBRO_SIGNING_KEY`` via
  HKDF-SHA256, so a report signature is structurally unreplayable as any
  future cookie/session token, while ops rotate ONE secret.
- ``kid`` (8 hex of the root key's digest) selects from a ring of at most
  two roots — current plus ``CEREBRO_SIGNING_KEY_PREVIOUS`` — retired
  after the 7-day capability TTL so rotation never orphans a live link
  and no retired key lingers (A7).
- Verification is constant-time (``hmac.compare_digest``) and returns the
  parsed (report_id-bound) claims; the CALLER must still recheck the authz
  row is ``ready`` and re-authorize on any cache hit.

Not RFC 6750 bearer-token-in-URL: that prohibition covers ACCESS tokens;
this is a scoped one-report capability (cf. S3 presigned URLs). Residual
risk (URL in history/logs) is bounded to one report, one expiry — and the
report host keeps ALB access logging disabled for exactly this reason.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

CAP_VERSION = "v1"
CAP_TTL_SECONDS = 7 * 24 * 3600
_PURPOSE_REPORT = b"cerebro:report-capability"


class CapabilityError(Exception):
    """Invalid/expired/tampered capability — always a deny, never a fallback."""


def _root_keys() -> dict[str, bytes]:
    """kid -> root key. Ring of at most two (current + previous)."""
    ring: dict[str, bytes] = {}
    for env in ("CEREBRO_SIGNING_KEY", "CEREBRO_SIGNING_KEY_PREVIOUS"):
        raw = (os.environ.get(env) or "").strip()
        if not raw:
            continue
        key = raw.encode("utf-8")
        if len(key) < 32:
            raise CapabilityError(f"{env} must be at least 32 bytes")
        ring[hashlib.sha256(key).hexdigest()[:8]] = key
    if not ring:
        raise CapabilityError("CEREBRO_SIGNING_KEY is not configured")
    return ring


def _current_kid() -> str:
    raw = (os.environ.get("CEREBRO_SIGNING_KEY") or "").strip().encode("utf-8")
    if len(raw) < 32:
        raise CapabilityError("CEREBRO_SIGNING_KEY must be at least 32 bytes")
    return hashlib.sha256(raw).hexdigest()[:8]


def _purpose_key(root: bytes) -> bytes:
    """HKDF-SHA256(extract+expand) with a fixed per-purpose info string."""
    prk = hmac.new(b"cerebro-hkdf-salt-v1", root, hashlib.sha256).digest()
    return hmac.new(prk, _PURPOSE_REPORT + b"\x01", hashlib.sha256).digest()


def _payload(report_id: str, auth_id: str, exp: int) -> bytes:
    return b"\x00".join(
        (CAP_VERSION.encode(), report_id.encode(), auth_id.encode(), str(exp).encode())
    )


def mint_report_capability(
    report_id: str, auth_id: str, *, ttl_seconds: int = CAP_TTL_SECONDS
) -> str:
    exp = int(time.time()) + ttl_seconds
    kid = _current_kid()
    key = _purpose_key(_root_keys()[kid])
    sig = hmac.new(key, _payload(report_id, auth_id, exp), hashlib.sha256).digest()
    b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{CAP_VERSION}.{kid}.{exp}.{b64}"


def verify_report_capability(cap: str, report_id: str, auth_id: str) -> None:
    """Raises CapabilityError unless ``cap`` grants THIS report now.

    Binding to ``auth_id`` (not just report_id) means a row that was
    deleted and re-created — even with the same id — invalidates old links.
    """
    parts = (cap or "").split(".")
    if len(parts) != 4 or parts[0] != CAP_VERSION:
        raise CapabilityError("malformed capability")
    _, kid, exp_raw, sig_b64 = parts
    try:
        exp = int(exp_raw)
    except ValueError as exc:
        raise CapabilityError("malformed expiry") from exc
    if time.time() > exp:
        raise CapabilityError("capability expired")
    root = _root_keys().get(kid)
    if root is None:
        raise CapabilityError("unknown signing key (rotated out)")
    key = _purpose_key(root)
    expected = hmac.new(
        key, _payload(report_id, auth_id, exp), hashlib.sha256
    ).digest()
    try:
        supplied = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except Exception as exc:  # noqa: BLE001
        raise CapabilityError("malformed signature") from exc
    if not hmac.compare_digest(expected, supplied):
        raise CapabilityError("signature mismatch")
