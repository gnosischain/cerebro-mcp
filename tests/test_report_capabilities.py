"""Signed report capabilities (R10 C6.7/A7): the full failure surface."""

from __future__ import annotations

import time

import pytest

from cerebro_mcp.auth import signing


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("CEREBRO_SIGNING_KEY", "current-key-" + "c" * 24)
    monkeypatch.delenv("CEREBRO_SIGNING_KEY_PREVIOUS", raising=False)


AUTH_ID = "a" * 32


def test_valid_roundtrip():
    cap = signing.mint_report_capability("r1", AUTH_ID)
    assert cap.startswith("v1.")
    signing.verify_report_capability(cap, "r1", AUTH_ID)  # no raise


def test_expired_denied():
    cap = signing.mint_report_capability("r1", AUTH_ID, ttl_seconds=-1)
    with pytest.raises(signing.CapabilityError, match="expired"):
        signing.verify_report_capability(cap, "r1", AUTH_ID)


def test_tampered_signature_denied():
    cap = signing.mint_report_capability("r1", AUTH_ID)
    head, sig = cap.rsplit(".", 1)
    bad = head + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(signing.CapabilityError):
        signing.verify_report_capability(bad, "r1", AUTH_ID)


def test_report_swap_denied():
    """A capability for report A grants nothing on report B."""
    cap = signing.mint_report_capability("r1", AUTH_ID)
    with pytest.raises(signing.CapabilityError, match="mismatch"):
        signing.verify_report_capability(cap, "r2", AUTH_ID)


def test_auth_id_binding_denies_recreated_report():
    """Same report_id, NEW auth_id (row deleted + re-created): old links die."""
    cap = signing.mint_report_capability("r1", AUTH_ID)
    with pytest.raises(signing.CapabilityError, match="mismatch"):
        signing.verify_report_capability(cap, "r1", "b" * 32)


def test_expiry_tamper_denied():
    """Stretching the expiry breaks the signature (exp is inside the payload)."""
    cap = signing.mint_report_capability("r1", AUTH_ID)
    v, kid, exp, sig = cap.split(".")
    stretched = ".".join((v, kid, str(int(exp) + 86400), sig))
    with pytest.raises(signing.CapabilityError, match="mismatch"):
        signing.verify_report_capability(stretched, "r1", AUTH_ID)


def test_key_ring_rotation(monkeypatch):
    """Links signed by the PREVIOUS key verify while it stays in the ring
    (A7: retired after the capability TTL), and die once it leaves."""
    old_cap = signing.mint_report_capability("r1", AUTH_ID)

    monkeypatch.setenv("CEREBRO_SIGNING_KEY", "rotated-key-" + "r" * 24)
    monkeypatch.setenv("CEREBRO_SIGNING_KEY_PREVIOUS", "current-key-" + "c" * 24)
    signing.verify_report_capability(old_cap, "r1", AUTH_ID)  # ring hit
    new_cap = signing.mint_report_capability("r1", AUTH_ID)
    signing.verify_report_capability(new_cap, "r1", AUTH_ID)
    assert old_cap.split(".")[1] != new_cap.split(".")[1]  # different kid

    monkeypatch.delenv("CEREBRO_SIGNING_KEY_PREVIOUS")
    with pytest.raises(signing.CapabilityError, match="rotated out"):
        signing.verify_report_capability(old_cap, "r1", AUTH_ID)


def test_short_key_refused(monkeypatch):
    monkeypatch.setenv("CEREBRO_SIGNING_KEY", "short")
    with pytest.raises(signing.CapabilityError, match="32 bytes"):
        signing.mint_report_capability("r1", AUTH_ID)


@pytest.mark.parametrize(
    "cap",
    ["", "v1", "v2.aa.1.sig", "v1.zz.notanint.sig", "v1.aa.99999999999.###"],
)
def test_malformed_denied(cap):
    with pytest.raises(signing.CapabilityError):
        signing.verify_report_capability(cap, "r1", AUTH_ID)


def test_purpose_separation():
    """The report key derives per purpose: a raw-HMAC forgery with the root
    key itself must not verify (HKDF separation is real, not cosmetic)."""
    import base64
    import hashlib
    import hmac as hmac_mod
    import os

    exp = int(time.time()) + 600
    root = os.environ["CEREBRO_SIGNING_KEY"].encode()
    payload = b"\x00".join((b"v1", b"r1", AUTH_ID.encode(), str(exp).encode()))
    forged_sig = base64.urlsafe_b64encode(
        hmac_mod.new(root, payload, hashlib.sha256).digest()
    ).decode().rstrip("=")
    kid = hashlib.sha256(root).hexdigest()[:8]
    forged = f"v1.{kid}.{exp}.{forged_sig}"
    with pytest.raises(signing.CapabilityError, match="mismatch"):
        signing.verify_report_capability(forged, "r1", AUTH_ID)
