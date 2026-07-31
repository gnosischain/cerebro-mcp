#!/usr/bin/env python
"""Run the connector profile locally with a throwaway in-process IdP.

The connector profile (`MCP_SURFACE_PROFILE=team_analytics_v1`) is
OAuth-only, so exercising it normally needs Auth0. This harness stands up
everything locally instead:

  * generates a throwaway RSA keypair,
  * serves its JWKS at /dev-idp/jwks.json ON THE SAME APP, so the server's
    JWKS fetch resolves without any external service,
  * mints ready-to-use access tokens and prints curl commands,
  * boots the real `build_connector_app` — the same code path production
    runs, gates included.

DEVELOPMENT ONLY. It self-signs tokens for an issuer it also hosts, which
is exactly what you must never do in production; it refuses to run unless
CEREBRO_DEV_CONNECTOR=1 is set.

Usage:
    CEREBRO_DEV_CONNECTOR=1 .venv/bin/python scripts/dev/connector_local.py
    CEREBRO_DEV_CONNECTOR=1 .venv/bin/python scripts/dev/connector_local.py --scopes discover
    CEREBRO_DEV_CONNECTOR=1 .venv/bin/python scripts/dev/connector_local.py --print-token
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

PORT = int(os.environ.get("CEREBRO_DEV_PORT", "8931"))
BASE = f"http://127.0.0.1:{PORT}"
ISSUER = f"{BASE}/dev-idp"
KID = "dev-key-1"


def _fail(msg: str) -> None:
    print(f"\n  ERROR: {msg}\n", file=sys.stderr)
    raise SystemExit(2)


def _configure_env() -> None:
    """Set every variable the profile's fail-closed boot checks require."""
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env", override=False)

    manifest_path = None
    for candidate in (
        os.environ.get("DBT_MANIFEST_PATH"),
        REPO / "target" / "manifest.json",
    ):
        if candidate and Path(candidate).is_file():
            manifest_path = Path(candidate)
            break
    if manifest_path is None:
        _fail(
            "no local manifest.json found. The profile pins the manifest "
            "SHA (one authorization version), so a local run needs one. "
            "Set DBT_MANIFEST_PATH=/path/to/manifest.json."
        )
    sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    ch_user = os.environ.get("CLICKHOUSE_USER", "")
    ch_pass = os.environ.get("CLICKHOUSE_PASSWORD", "")

    os.environ.update({
        "MCP_SURFACE_PROFILE": "team_analytics_v1",
        "DBT_MANIFEST_PATH": str(manifest_path),
        "MCP_EXPECTED_MANIFEST_SHA256": sha,
        # Local-only secrets. Real deployments read these from Parameter Store.
        "CEREBRO_OWNER_KEY_V1": os.environ.get(
            "CEREBRO_OWNER_KEY_V1", "dev-owner-key-" + "0" * 20
        ),
        "CEREBRO_SIGNING_KEY": os.environ.get(
            "CEREBRO_SIGNING_KEY", "dev-signing-key-" + "0" * 20
        ),
        "CEREBRO_AUTHZ_DB_PATH": os.environ.get(
            "CEREBRO_AUTHZ_DB_PATH", str(REPO / ".cerebro-dev" / "authz.db")
        ),
        # The profile demands the RESTRICTED identity. Locally you probably
        # have only the broad one; reuse it and say so loudly rather than
        # letting the boot check fail with no explanation.
        "CONNECTOR_CLICKHOUSE_USER": os.environ.get(
            "CONNECTOR_CLICKHOUSE_USER", ch_user
        ),
        "CONNECTOR_CLICKHOUSE_PASSWORD": os.environ.get(
            "CONNECTOR_CLICKHOUSE_PASSWORD", ch_pass
        ),
        # These must be on for the 44-tool set to register.
        "CUSTOM_TOOLS_ENABLED": "true",
        "CUSTOM_TOOLS_PATH": os.environ.get(
            "CUSTOM_TOOLS_PATH", str(REPO / "custom_tools.yaml")
        ),
        "SEMANTIC_ENABLED": "true",
        "STREAMABLE_HTTP_STATELESS": "true",
        "RPC_SCAN_ENABLED": "false",
        "LEAN_CORE_ENABLED": "false",
        # Local IdP wiring.
        "OAUTH_ISSUER_URL": ISSUER,
        "OAUTH_JWKS_URL": f"{ISSUER}/jwks.json",
        "OAUTH_RESOURCE_BASE": BASE,
        "OAUTH_CLIENT_CLAUDE_TEAM": "dev-claude-team",
        "OAUTH_CLIENT_CLAUDE_CODE": "dev-claude-code",
        "OAUTH_CLIENT_CODEX": "dev-codex",
        "OAUTH_CLIENT_CHATGPT": "dev-chatgpt",
    })
    if not os.environ["CONNECTOR_CLICKHOUSE_USER"]:
        _fail(
            "no ClickHouse credentials. Set CLICKHOUSE_USER/PASSWORD in "
            ".env (or CONNECTOR_CLICKHOUSE_USER/PASSWORD directly)."
        )


def _keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return key, jwk


def mint_token(key, *, scopes: str, client_id: str, subject: str, aud: str) -> str:
    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode(
        {
            "iss": ISSUER,
            "sub": subject,
            "aud": aud,
            "client_id": client_id,
            "iat": now,
            "exp": now + 8 * 3600,
            "scope": scopes,
        },
        key,
        algorithm="RS256",
        headers={"kid": KID},
    )


def main() -> int:
    if os.environ.get("CEREBRO_DEV_CONNECTOR") != "1":
        _fail(
            "refusing to run without CEREBRO_DEV_CONNECTOR=1. This harness "
            "self-signs tokens for an issuer it also hosts — development only."
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scopes", default="discover query artifact",
        help="space-separated scope short names (default: all three)",
    )
    parser.add_argument("--subject", default="dev@gnosis.io")
    parser.add_argument(
        "--print-token", action="store_true",
        help="print a token and exit (do not start the server)",
    )
    args = parser.parse_args()

    _configure_env()
    key, jwk = _keypair()
    scopes = " ".join(f"cerebro:{s}" for s in args.scopes.split())

    claude_tok = mint_token(
        key, scopes=scopes, client_id="dev-claude-team",
        subject=args.subject, aud=f"{BASE}/mcp",
    )
    openai_tok = mint_token(
        key, scopes=scopes, client_id="dev-chatgpt",
        subject=args.subject, aud=f"{BASE}/openai/mcp",
    )
    if args.print_token:
        print(claude_tok)
        return 0

    import uvicorn
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from cerebro_mcp.auth import jwks_cache as jwks_mod

    # Chicken-and-egg: build_connector_app warms the JWKS at boot, but the
    # server that would serve it is not listening yet. Pre-seed the cache
    # with the dev key so the warm is a no-op. Only the FETCH is stubbed —
    # signature verification, kid lookup and every gate run for real.
    _seed = jwk

    class _SeededCache(jwks_mod.JwksCache):
        def warm(self) -> None:
            self._keys_by_kid = {KID: _seed}
            self._fetched_at = time.monotonic()

        def _refresh_locked(self, *, force: bool = False) -> None:
            self.warm()

    jwks_mod.JwksCache = _SeededCache

    from cerebro_mcp.auth.app import build_connector_app
    from cerebro_mcp.server import mcp

    async def jwks_route(request):  # noqa: ANN001
        return JSONResponse({"keys": [_seed]})

    app = build_connector_app(mcp)
    # Also serve it over HTTP so an external MCP client doing real OAuth
    # discovery against this harness can fetch the key.
    app.router.routes.append(
        Route("/dev-idp/jwks.json", jwks_route, methods=["GET"])
    )

    env_file = REPO / ".cerebro-dev" / "tokens.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        f"export CEREBRO_DEV_BASE='{BASE}'\n"
        f"export CLAUDE_TOKEN='{claude_tok}'\n"
        f"export OPENAI_TOKEN='{openai_tok}'\n"
    )
    env_file.chmod(0o600)

    print(f"""
================= cerebro connector — LOCAL DEV =================
profile        : team_analytics_v1  (OAuth-only, 44 tools)
listening      : {BASE}
issuer (fake)  : {ISSUER}
scopes minted  : {scopes}
ClickHouse as  : {os.environ['CONNECTOR_CLICKHOUSE_USER']}
  {'*** NOT a restricted identity — dev only ***'
     if os.environ['CONNECTOR_CLICKHOUSE_USER'] == os.environ.get('CLICKHOUSE_USER')
     else ''}

TRY IT:

# 1. protected-resource metadata (no auth)
curl -s {BASE}/.well-known/oauth-protected-resource/mcp | jq

# 2. no token -> 401 with a compliant challenge
curl -si -X POST {BASE}/mcp -H 'content-type: application/json' \\
  -d '{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}' | head -5

# 3. WITH a token -> the 44-tool surface
curl -s -X POST {BASE}/mcp \\
  -H "Authorization: Bearer $CLAUDE_TOKEN" \\
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \\
  -d '{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}' | jq '.result.tools | length'

# 4. an EXCLUDED tool is rejected at invocation
curl -s -X POST {BASE}/mcp -H "Authorization: Bearer $CLAUDE_TOKEN" \\
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \\
  -d '{{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{{"name":"rpc_scan_logs","arguments":{{}}}}}}' | jq -r '.result.content[0].text'

# 5. the OpenAI endpoint answers IN-BAND when unauthenticated
curl -s -X POST {BASE}/openai/mcp -H 'content-type: application/json' \\
  -H 'accept: application/json, text/event-stream' \\
  -d '{{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{{"name":"find","arguments":{{"query":"tvl"}}}}}}' \\
  | jq '.result._meta'

Tokens (8h) are also written to {env_file} — source it:
    source {env_file}

Ctrl-C to stop.
================================================================
""", flush=True)  # flush: uvicorn.run() blocks, so a redirected stdout
    # would otherwise hold this banner (and the tokens) in the buffer.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
