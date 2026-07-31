"""Connector root application: two exact routes, one session manager.

Composition (R9 P0-4, adapted): one FastMCP instance serves BOTH endpoints
— all per-endpoint differences (audience, client allowlist, challenge
style, method matrix, capability shape) live in the gates and the authored
PRM routes, not in the SDK, so the two-instance split R9 demanded to escape
the SDK's auth coupling is unnecessary: that coupling is not used at all
(no ``auth=``/``token_verifier=`` — stock ``RequireAuthMiddleware`` would
401 before ``call_tool`` and the in-band challenge could never exist).

Exact routes, no Mount prefixes (no ``/mcp/mcp``, no shadowing):

    POST/GET/DELETE/OPTIONS  /mcp             TransportAuthGate
    POST/OPTIONS             /openai/mcp      OpenAIAuthGate (405 otherwise)
    GET  /.well-known/oauth-protected-resource/mcp          authored PRM
    GET  /.well-known/oauth-protected-resource/openai/mcp   authored PRM
    GET  /health /livez                                     probes

The PRM documents are AUTHORED (not SDK-generated): the pinned SDK couples
``required_scopes`` to both enforcement and metadata, which cannot express
progressive scopes (R9 P0-3). ``scopes_supported`` lists only
``cerebro:discover`` — the minimal-functionality set; ``query`` and
``artifact`` arrive via step-up challenges.

The root lifespan enters the session manager's ``run()`` context —
mounted-child lifespans are NOT automatically entered, and the manager
refuses to serve outside it.
"""

from __future__ import annotations

import contextlib
import logging

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from cerebro_mcp.auth.clients import all_cerebro_resources, endpoint_policies
from cerebro_mcp.auth.jwks_cache import JwksCache
from cerebro_mcp.auth.jwt_verifier import CerebroTokenVerifier, VerifierConfig

logger = logging.getLogger(__name__)


def _prm_endpoint(resource: str, issuer: str, scopes: list[str]):
    payload = {
        "resource": resource,
        "authorization_servers": [issuer],
        "scopes_supported": scopes,
        "bearer_methods_supported": ["header"],
    }

    async def endpoint(request):  # noqa: ANN001 — Starlette signature
        return JSONResponse(payload)

    return endpoint


def build_connector_app(mcp) -> Starlette:
    """The OAuth-only connector application (team_analytics_v1)."""
    from cerebro_mcp.config import settings
    from cerebro_mcp.tools.tool_policy import SCOPE_DISCOVER

    issuer = settings.OAUTH_ISSUER_URL
    if not issuer or not settings.OAUTH_JWKS_URL:
        raise RuntimeError(
            "connector app requires OAUTH_ISSUER_URL and OAUTH_JWKS_URL"
        )

    # Warm BEFORE uvicorn starts — never via FastMCP lifespan, which runs
    # per request in stateless mode. A failed warm is a boot failure.
    jwks = JwksCache(settings.OAUTH_JWKS_URL)
    jwks.warm()

    policies = endpoint_policies()
    resources = all_cerebro_resources()

    # Build the session manager with the configured stateless/json flags
    # (streamable_http_app() reads them at first call), then serve the SAME
    # manager on both exact routes through the per-endpoint gates.
    mcp.settings.stateless_http = settings.STREAMABLE_HTTP_STATELESS
    mcp.settings.json_response = settings.STREAMABLE_HTTP_JSON_RESPONSE
    mcp.streamable_http_app()  # instantiates mcp.session_manager
    from mcp.server.streamable_http_manager import StreamableHTTPASGIApp

    session_app = StreamableHTTPASGIApp(mcp.session_manager)

    from cerebro_mcp.auth.asgi import OpenAIAuthGate, TransportAuthGate

    def _verifier(path: str) -> CerebroTokenVerifier:
        policy = policies[path]
        return CerebroTokenVerifier(
            VerifierConfig(
                issuer=issuer,
                resource=policy.resource,
                all_resources=resources,
                allowed_client_ids=policy.allowed_client_ids,
            ),
            jwks,
        )

    claude_gate = TransportAuthGate(session_app, policies["/mcp"], _verifier("/mcp"))
    openai_gate = OpenAIAuthGate(
        session_app, policies["/openai/mcp"], _verifier("/openai/mcp")
    )

    async def health(request):  # noqa: ANN001
        return JSONResponse({"status": "ok"})

    routes = [
        Route("/mcp", claude_gate, methods=["POST", "GET", "DELETE", "OPTIONS"]),
        Route("/openai/mcp", openai_gate, methods=["POST", "GET", "DELETE", "OPTIONS"]),
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            _prm_endpoint(policies["/mcp"].resource, issuer, [SCOPE_DISCOVER]),
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/openai/mcp",
            _prm_endpoint(
                policies["/openai/mcp"].resource, issuer, [SCOPE_DISCOVER]
            ),
            methods=["GET"],
        ),
        Route("/livez", health, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(app):  # noqa: ANN001
        async with mcp.session_manager.run():
            yield

    return Starlette(routes=routes, lifespan=lifespan)
