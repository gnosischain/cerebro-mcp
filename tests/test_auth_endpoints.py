"""Dual-endpoint auth composition tests (R10 C1-C3, R9 P0-1..P0-5).

Real RS256 tokens minted with a test keypair; the JWKS cache is seeded
directly (no network). Gates are driven over real HTTP via Starlette's
TestClient with a recording stub as the inner app, so what is asserted is
the WIRE behavior of each challenge form.
"""

from __future__ import annotations

import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from cerebro_mcp.auth import challenge
from cerebro_mcp.auth.asgi import OpenAIAuthGate, TransportAuthGate
from cerebro_mcp.auth.clients import endpoint_policies
from cerebro_mcp.auth.jwks_cache import JwksCache
from cerebro_mcp.auth.jwt_verifier import (
    CerebroTokenVerifier,
    InvalidToken,
    VerifierConfig,
)
from cerebro_mcp.config import settings
from cerebro_mcp.runtime import identity
from cerebro_mcp.workflow.authz_store import reset_authz_store_for_tests

ISSUER = "https://gnosis.eu.auth0.com/"
BASE = "https://mcp.analytics.gnosis.io"
KID = "test-key-1"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWK = json.loads(RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key()))
_JWK["kid"] = KID


def _seeded_jwks() -> JwksCache:
    cache = JwksCache("https://gnosis.eu.auth0.com/.well-known/jwks.json")
    cache._keys_by_kid = {KID: _JWK}
    cache._fetched_at = time.monotonic()
    return cache


def mint(
    *,
    aud="https://mcp.analytics.gnosis.io/mcp",
    client_id="client_claude_team",
    sub="auth0|alice",
    scope="cerebro:discover cerebro:query cerebro:artifact",
    iss=ISSUER,
    alg="RS256",
    kid=KID,
    exp_delta=600,
    iat_delta=-10,
    extra: dict | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    claims = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "client_id": client_id,
        "iat": now + iat_delta,
        "exp": now + exp_delta,
        "scope": scope,
    }
    claims.update(extra or {})
    for key in drop:
        claims.pop(key, None)
    if alg == "none":
        return pyjwt.encode(claims, key=None, algorithm="none")
    if alg.startswith("HS"):
        return pyjwt.encode(claims, "secret", algorithm=alg, headers={"kid": kid})
    return pyjwt.encode(
        claims, _PRIVATE_KEY, algorithm=alg, headers={"kid": kid}
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBRO_OWNER_KEY_V1", "k" * 32)
    monkeypatch.setattr(settings, "CEREBRO_AUTHZ_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setattr(settings, "OAUTH_ISSUER_URL", ISSUER)
    monkeypatch.setattr(settings, "OAUTH_RESOURCE_BASE", BASE)
    monkeypatch.setattr(settings, "OAUTH_CLIENT_CLAUDE_TEAM", "client_claude_team")
    monkeypatch.setattr(settings, "OAUTH_CLIENT_CLAUDE_CODE", "client_claude_code")
    monkeypatch.setattr(settings, "OAUTH_CLIENT_CODEX", "client_codex")
    monkeypatch.setattr(settings, "OAUTH_CLIENT_CHATGPT", "client_chatgpt")
    identity.reset_owner_key_cache_for_tests()
    reset_authz_store_for_tests()
    yield
    identity.reset_owner_key_cache_for_tests()
    reset_authz_store_for_tests()


def _verifier(path="/mcp") -> CerebroTokenVerifier:
    policies = endpoint_policies()
    policy = policies[path]
    return CerebroTokenVerifier(
        VerifierConfig(
            issuer=ISSUER,
            resource=policy.resource,
            all_resources=frozenset(p.resource for p in policies.values()),
            allowed_client_ids=policy.allowed_client_ids,
        ),
        _seeded_jwks(),
    )


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def test_valid_token_yields_principal():
    p = _verifier().verify(mint())
    assert p.owner.startswith("v1:")
    assert p.scopes == {
        "cerebro:discover",
        "cerebro:query",
        "cerebro:artifact",
    }
    assert p.client_id == "client_claude_team"


def test_scp_array_normalized():
    tok = mint(scope="", extra={"scp": ["cerebro:discover"]}, drop=("scope",))
    assert _verifier().verify(tok).scopes == {"cerebro:discover"}


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_algorithm_confusion_rejected(alg):
    with pytest.raises(InvalidToken, match="alg"):
        _verifier().verify(mint(alg=alg))


def test_wrong_issuer_rejected():
    with pytest.raises(InvalidToken):
        _verifier().verify(mint(iss="https://evil.example/"))


def test_wrong_audience_rejected():
    with pytest.raises(InvalidToken, match="audience"):
        _verifier().verify(mint(aud="https://other.api/"))


def test_userinfo_second_audience_tolerated():
    """Auth0 appends /userinfo when openid is requested — aud is
    MEMBERSHIP, never len()==1 (the launch-day break the audits killed)."""
    tok = mint(aud=[f"{BASE}/mcp", f"{ISSUER}userinfo"])
    assert _verifier().verify(tok).owner.startswith("v1:")


def test_dual_cerebro_audience_rejected():
    tok = mint(aud=[f"{BASE}/mcp", f"{BASE}/openai/mcp"])
    with pytest.raises(InvalidToken, match="another Cerebro resource"):
        _verifier().verify(tok)


def test_cross_endpoint_token_rejected():
    """A token minted for /openai/mcp is invalid on /mcp and vice versa."""
    openai_tok = mint(aud=f"{BASE}/openai/mcp", client_id="client_chatgpt")
    with pytest.raises(InvalidToken):
        _verifier("/mcp").verify(openai_tok)
    claude_tok = mint()
    with pytest.raises(InvalidToken):
        _verifier("/openai/mcp").verify(claude_tok)


def test_unknown_client_rejected():
    """Correctly signed, right audience, WRONG application: still invalid."""
    with pytest.raises(InvalidToken, match="client"):
        _verifier().verify(mint(client_id="some_other_tenant_app"))


def test_expired_rejected():
    with pytest.raises(InvalidToken):
        _verifier().verify(mint(exp_delta=-120))


def test_future_iat_rejected():
    with pytest.raises(InvalidToken, match="iat"):
        _verifier().verify(mint(iat_delta=+600))


def test_missing_sub_rejected():
    with pytest.raises(InvalidToken):
        _verifier().verify(mint(drop=("sub",)))


def test_tombstone_denies_within_token_lifetime():
    v = _verifier()
    tok = mint()
    owner = v.verify(tok).owner
    from cerebro_mcp.workflow.authz_store import get_authz_store

    get_authz_store().deny_subject(owner, actor="ops", reason="test")
    with pytest.raises(InvalidToken, match="denied"):
        v.verify(tok)


def test_authz_store_outage_fails_closed():
    v = _verifier()
    tok = mint()
    v.verify(tok)
    from cerebro_mcp.workflow.authz_store import get_authz_store

    get_authz_store()._conn.close()
    with pytest.raises(InvalidToken, match="authorization store"):
        v.verify(tok)


def test_watermark_rejects_older_tokens():
    v = _verifier()
    tok = mint(iat_delta=-300)
    owner = v.verify(tok).owner
    from cerebro_mcp.workflow.authz_store import get_authz_store

    get_authz_store().set_revocation_watermark(owner, int(time.time()) - 60)
    with pytest.raises(InvalidToken, match="watermark"):
        v.verify(tok)
    # a freshly issued token passes the watermark
    assert v.verify(mint()).owner == owner


# ---------------------------------------------------------------------------
# Gates over real HTTP
# ---------------------------------------------------------------------------

_seen: dict = {}


async def _stub_app(scope, receive, send):
    """Inner-app stand-in: records the principal, echoes a JSON body."""
    _seen["principal"] = scope.get("state", {}).get("cerebro_principal")
    _seen["owner_ctx"] = identity.get_current_owner()
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {},
                    "prompts": {},
                }
            },
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _client(path: str) -> TestClient:
    policies = endpoint_policies()
    gate_cls = TransportAuthGate if path == "/mcp" else OpenAIAuthGate
    gate = gate_cls(_stub_app, policies[path], _verifier(path))
    app = Starlette(
        routes=[Route(path, gate, methods=["POST", "GET", "DELETE", "OPTIONS"])]
    )
    return TestClient(app, raise_server_exceptions=False)


def _rpc(method: str, tool: str | None = None) -> dict:
    payload: dict = {"jsonrpc": "2.0", "id": 7, "method": method}
    if tool is not None:
        payload["params"] = {"name": tool, "arguments": {}}
    return payload


def test_mcp_missing_token_401_with_scope_and_metadata():
    r = _client("/mcp").post("/mcp", json=_rpc("initialize"))
    assert r.status_code == 401
    www = r.headers["WWW-Authenticate"]
    assert 'scope="cerebro:discover"' in www
    assert "/.well-known/oauth-protected-resource/mcp" in www


def test_mcp_malformed_credential_400():
    r = _client("/mcp").post(
        "/mcp", json=_rpc("ping"), headers={"Authorization": "Bearer"}
    )
    assert r.status_code == 400
    assert 'error="invalid_request"' in r.headers["WWW-Authenticate"]


def test_mcp_invalid_token_401():
    r = _client("/mcp").post(
        "/mcp",
        json=_rpc("ping"),
        headers={"Authorization": f"Bearer {mint(iss='https://evil/')}"},
    )
    assert r.status_code == 401
    assert 'error="invalid_token"' in r.headers["WWW-Authenticate"]


def test_mcp_underscoped_tools_call_403_with_full_union():
    tok = mint(scope="cerebro:discover")
    r = _client("/mcp").post(
        "/mcp",
        json=_rpc("tools/call", tool="generate_charts"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403
    www = r.headers["WWW-Authenticate"]
    assert 'error="insufficient_scope"' in www
    # COMPLETE union, sorted — not just the first missing scope
    assert 'scope="cerebro:artifact cerebro:discover cerebro:query"' in www


def test_mcp_scoped_call_reaches_app_with_principal():
    _seen.clear()
    r = _client("/mcp").post(
        "/mcp",
        json=_rpc("tools/call", tool="execute_query"),
        headers={"Authorization": f"Bearer {mint()}"},
    )
    assert r.status_code == 200
    assert _seen["principal"].subject == "auth0|alice"
    assert _seen["owner_ctx"] == _seen["principal"].owner  # bridge, no re-hash


def test_mcp_unknown_method_default_deny():
    r = _client("/mcp").post(
        "/mcp",
        json=_rpc("prompts/get"),
        headers={"Authorization": f"Bearer {mint()}"},
    )
    assert r.status_code == 403


def test_openai_get_and_delete_405():
    c = _client("/openai/mcp")
    assert c.get("/openai/mcp").status_code == 405
    assert c.delete("/openai/mcp").status_code == 405


def test_openai_anonymous_initialize_passes_and_strips_capabilities():
    _seen.clear()
    r = _client("/openai/mcp").post("/openai/mcp", json=_rpc("initialize"))
    assert r.status_code == 200
    assert _seen["principal"] is None
    caps = r.json()["result"]["capabilities"]
    assert "tools" in caps
    assert "resources" not in caps and "prompts" not in caps


def test_openai_absent_token_tools_call_inband_challenge():
    r = _client("/openai/mcp").post(
        "/openai/mcp", json=_rpc("tools/call", tool="execute_query")
    )
    assert r.status_code == 200  # in-band: a RESULT, not a transport error
    result = r.json()["result"]
    assert result["isError"] is True
    www = result["_meta"]["mcp/www_authenticate"]
    assert www.startswith("Bearer ")
    assert 'error="invalid_token"' in www
    assert "error_description=" in www
    assert "/.well-known/oauth-protected-resource/openai/mcp" in www


def test_openai_present_invalid_token_http_401():
    """C1: missing and invalid are DIFFERENT cases."""
    r = _client("/openai/mcp").post(
        "/openai/mcp",
        json=_rpc("tools/call", tool="execute_query"),
        headers={"Authorization": f"Bearer {mint(iss='https://evil/')}"},
    )
    assert r.status_code == 401


def test_openai_underscoped_inband_insufficient_scope():
    tok = mint(
        aud=f"{BASE}/openai/mcp",
        client_id="client_chatgpt",
        scope="cerebro:discover",
    )
    r = _client("/openai/mcp").post(
        "/openai/mcp",
        json=_rpc("tools/call", tool="execute_query"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    www = r.json()["result"]["_meta"]["mcp/www_authenticate"]
    assert 'error="insufficient_scope"' in www
    assert 'scope="cerebro:discover cerebro:query"' in www


def test_openai_nonpublic_method_denied_even_authenticated():
    tok = mint(aud=f"{BASE}/openai/mcp", client_id="client_chatgpt")
    r = _client("/openai/mcp").post(
        "/openai/mcp",
        json=_rpc("resources/list"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403


def test_capability_filter_handles_both_wire_formats():
    """The capability rewrite must work for JSON *and* SSE bodies, and DENY
    on anything it cannot parse — passing an unverified body through
    re-advertised resources/prompts the endpoint refuses to serve."""
    from cerebro_mcp.auth.asgi import _CapabilityFilter

    strip = _CapabilityFilter._strip_capabilities
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"capabilities": {"tools": {}, "resources": {}, "prompts": {}}},
    }
    plain = strip(json.dumps(payload).encode())
    caps = json.loads(plain)["result"]["capabilities"]
    assert "tools" in caps and "resources" not in caps and "prompts" not in caps

    sse = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()
    framed = strip(sse)
    assert framed is not None, "SSE-framed initialize was not rewritten"
    assert b"resources" not in framed and b"prompts" not in framed
    assert framed.startswith(b"event: message")

    assert strip(b"<html>not json</html>") is None
    assert strip(b"data: {not json}\n\n") is None


def test_get_requires_baseline_scope():
    """A valid token granted NOTHING must not open a stream."""
    tok = mint(scope="")
    r = _client("/mcp").get("/mcp", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    assert 'scope="cerebro:discover"' in r.headers["WWW-Authenticate"]
