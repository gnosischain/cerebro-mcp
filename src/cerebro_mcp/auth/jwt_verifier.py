"""CerebroTokenVerifier — token AUTHENTICATION only (R10 §5/8).

Per-tool authorization lives in the policy layer (``tools/tool_policy.py``
driven gates); this module answers exactly one question — "is this a valid
token for THIS endpoint, and who does it represent?" — with typed failure
categories so the challenge layer can pick the right response form:

    MALFORMED  -> 400 invalid_request
    INVALID    -> 401 invalid_token
    ok         -> CerebroPrincipal

Verification order (each step fail-closed, R10 C3 + carried corrections):

 1. header shape (exactly one Bearer credential)
 2. signature, RS256 ONLY (alg pinned; ``none``/HS* rejected by allowlist)
 3. exact issuer
 4. ``aud`` MEMBERSHIP after scalar-or-array normalization — never
    ``len(aud) == 1`` (Auth0 appends /userinfo when openid is requested)
 5. dual-Cerebro-audience rejection: this endpoint's resource must be the
    ONLY Cerebro resource present (resource-specific token requirement)
 6. client allowlist for THIS endpoint (``client_id``, RFC 9068 profile —
    Auth0's own profile uses ``azp``; the profile is pinned to RFC 9068 in
    the conformance spike). Never used as user identity.
 7. required claims, TYPE-checked (sub/iat/exp strings vs numbers matter)
 8. exp/nbf/iat with <=60 s skew
 9. tombstone (denied_subjects) — AuthzUnavailable => INVALID (fail closed)
10. revocation watermark (iat >= min_iat)

The verifier never logs token material and never returns the raw token.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import jwt as pyjwt
from jwt import algorithms as jwt_algorithms

from cerebro_mcp.auth.jwks_cache import JwksCache, JwksUnavailable
from cerebro_mcp.runtime.identity import CerebroPrincipal, owner_hash_v1

logger = logging.getLogger(__name__)

ALLOWED_ALGS = ("RS256",)
CLOCK_SKEW_S = 60


class TokenFailure(Exception):
    pass


class MalformedCredential(TokenFailure):
    """400 invalid_request — the credential is not even a parseable token."""


class InvalidToken(TokenFailure):
    """401 invalid_token — parseable but not acceptable."""


@dataclass(frozen=True)
class VerifierConfig:
    issuer: str
    resource: str                       # THIS endpoint's audience
    all_resources: frozenset[str]       # every Cerebro audience (step 5)
    allowed_client_ids: frozenset[str]


def extract_bearer(authorization: str | None) -> str | None:
    """None when absent; MalformedCredential on a broken header."""
    if authorization is None or not authorization.strip():
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise MalformedCredential("Authorization header is not a single Bearer credential")
    return parts[1]


class CerebroTokenVerifier:
    def __init__(self, config: VerifierConfig, jwks: JwksCache):
        self._config = config
        self._jwks = jwks

    def verify(self, token: str) -> CerebroPrincipal:
        cfg = self._config
        # -- 1/2: header + signature, alg pinned ------------------------
        try:
            header = pyjwt.get_unverified_header(token)
        except pyjwt.PyJWTError as exc:
            raise MalformedCredential(f"not a JWT: {exc}") from exc
        alg = header.get("alg")
        if alg not in ALLOWED_ALGS:
            raise InvalidToken(f"alg {alg!r} not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidToken("missing kid")
        try:
            jwk = self._jwks.key_for(kid)
            public_key = jwt_algorithms.RSAAlgorithm.from_jwk(jwk)
        except JwksUnavailable as exc:
            # Keyset outage => cannot verify => deny. Never fail open.
            raise InvalidToken(f"keyset unavailable: {exc}") from exc
        try:
            claims = pyjwt.decode(
                token,
                key=public_key,
                algorithms=list(ALLOWED_ALGS),
                issuer=cfg.issuer,
                leeway=CLOCK_SKEW_S,
                options={
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                    # Audience is checked manually below: pyjwt's built-in
                    # check is membership too, but we ALSO need the
                    # dual-Cerebro-audience rejection in the same place.
                    "verify_aud": False,
                },
            )
        except pyjwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc

        # -- 4/5: audience membership + single-Cerebro-resource ----------
        aud = claims.get("aud")
        aud_list = [aud] if isinstance(aud, str) else list(aud or [])
        if not all(isinstance(a, str) for a in aud_list):
            raise InvalidToken("aud entries must be strings")
        if cfg.resource not in aud_list:
            raise InvalidToken("token audience does not include this resource")
        cerebro_auds = set(aud_list) & cfg.all_resources
        if cerebro_auds != {cfg.resource}:
            raise InvalidToken(
                "token carries another Cerebro resource audience — "
                "resource-specific tokens only"
            )

        # -- 6: endpoint client allowlist -------------------------------
        client_id = claims.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise InvalidToken("missing client_id (RFC 9068 profile required)")
        if client_id not in cfg.allowed_client_ids:
            raise InvalidToken("client is not permitted for this resource")

        # -- 7: typed claims --------------------------------------------
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise InvalidToken("sub must be a non-empty string")
        iat = claims.get("iat")
        if not isinstance(iat, (int, float)) or isinstance(iat, bool):
            raise InvalidToken("iat must be numeric")
        nbf = claims.get("nbf")
        if nbf is not None and (
            not isinstance(nbf, (int, float)) or isinstance(nbf, bool)
        ):
            raise InvalidToken("nbf must be numeric when present")
        if iat > time.time() + CLOCK_SKEW_S:
            raise InvalidToken("iat is in the future")

        scopes = self._normalize_scopes(claims)

        owner = owner_hash_v1(cfg.issuer, sub)

        # -- 9: tombstone, fail closed ----------------------------------
        from cerebro_mcp.workflow.authz_store import (
            AuthzUnavailable,
            get_authz_store,
        )

        try:
            store = get_authz_store()
            if store.is_denied(owner):
                raise InvalidToken("subject is denied")
            # -- 10: revocation watermark -------------------------------
            watermark = store.revocation_watermark(owner)
        except AuthzUnavailable as exc:
            # The authorization store being down means we CANNOT check the
            # tombstone — that is a denial, not a pass (R10 C6/P0-8).
            raise InvalidToken(f"authorization store unavailable: {exc}") from exc
        if watermark is not None and iat < watermark:
            raise InvalidToken("token predates the revocation watermark")

        return CerebroPrincipal(
            kind="oauth",
            issuer=cfg.issuer,
            subject=sub,
            client_id=client_id,
            scopes=scopes,
            owner=owner,
        )

    @staticmethod
    def _normalize_scopes(claims: dict) -> frozenset[str]:
        """OAuth `scope` (space-delimited string) or Auth0/Okta `scp`
        (array). Unexpected shapes yield NO scopes rather than guessed
        ones."""
        scope = claims.get("scope")
        if isinstance(scope, str):
            return frozenset(s for s in scope.split() if s)
        scp = claims.get("scp")
        if isinstance(scp, list) and all(isinstance(s, str) for s in scp):
            return frozenset(scp)
        return frozenset()
