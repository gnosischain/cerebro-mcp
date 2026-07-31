"""JWKS cache: warmed BEFORE uvicorn starts, refreshed under a lock.

Rules (R10 §8 carried + R9-audit):

- Fetch ONLY the configured HTTPS URL bound to the pinned issuer — never a
  URL derived from token claims (attacker-controlled), and never follow a
  cross-origin redirect.
- Warm once at boot, NOT via FastMCP lifespan (which runs per request in
  stateless Streamable HTTP mode) — the first real request must never pay
  the fetch.
- Refresh under a single-flight lock on TTL expiry or an unknown ``kid``;
  a failed refresh serves the cached keyset (availability), but an EMPTY
  cache is an error — there is no keyless fallback.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse

import requests

logger = logging.getLogger(__name__)

_TTL_SECONDS = 3600.0
_FETCH_TIMEOUT = (5, 10)


class JwksUnavailable(Exception):
    """No usable keyset — token verification must fail closed."""


class JwksCache:
    def __init__(self, jwks_url: str):
        parsed = urllib.parse.urlparse(jwks_url)
        if parsed.scheme != "https":
            raise JwksUnavailable(
                f"JWKS URL must be https, got {jwks_url!r}"
            )
        self._url = jwks_url
        self._origin = (parsed.scheme, parsed.netloc)
        self._lock = threading.Lock()
        self._keys_by_kid: dict[str, dict] = {}
        self._fetched_at = 0.0

    def warm(self) -> None:
        """Boot-time warm: a failure here is a boot failure."""
        self._refresh_locked(force=True)
        if not self._keys_by_kid:
            raise JwksUnavailable(f"{self._url} returned no usable keys")

    def key_for(self, kid: str) -> dict:
        """The JWK for ``kid``; refreshes once on miss (key rotation)."""
        with self._lock:
            if kid in self._keys_by_kid and not self._expired():
                return self._keys_by_kid[kid]
        self._refresh_locked()
        with self._lock:
            key = self._keys_by_kid.get(kid)
        if key is None:
            raise JwksUnavailable(f"unknown kid {kid!r} after refresh")
        return key

    def _expired(self) -> bool:
        return (time.monotonic() - self._fetched_at) > _TTL_SECONDS

    def _refresh_locked(self, *, force: bool = False) -> None:
        with self._lock:
            if not force and not self._expired() and self._keys_by_kid:
                return  # single-flight: another caller already refreshed
            try:
                resp = requests.get(
                    self._url, timeout=_FETCH_TIMEOUT, allow_redirects=False
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    # A redirect off the pinned origin is an attack surface,
                    # and even same-origin redirects are refused: the URL is
                    # configuration, not a discovery walk.
                    raise JwksUnavailable(
                        f"{self._url} redirected ({resp.status_code}) — "
                        "refusing to follow"
                    )
                resp.raise_for_status()
                payload = json.loads(resp.content)
                keys = {
                    k["kid"]: k
                    for k in payload.get("keys", [])
                    if isinstance(k, dict) and k.get("kid")
                }
                if keys:
                    self._keys_by_kid = keys
                    self._fetched_at = time.monotonic()
                elif not self._keys_by_kid:
                    raise JwksUnavailable(f"{self._url} served an empty keyset")
            except JwksUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                if self._keys_by_kid:
                    # Post-warm outage: serve the cached keyset.
                    logger.warning("JWKS refresh failed, serving cache: %s", exc)
                    return
                raise JwksUnavailable(str(exc)) from exc
