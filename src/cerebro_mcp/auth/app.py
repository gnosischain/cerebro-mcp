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

    async def session_app(scope, receive, send):
        """Minimal ASGI adapter over the session manager.

        Deliberately NOT `StreamableHTTPASGIApp`: that class lives in
        `mcp.server.fastmcp.server` (not the manager module) and is an
        unexported internal, so importing it couples this file to a symbol
        the `mcp[cli]>=1.26,<2` pin lets move on any minor bump — the same
        class of breakage the pin comment already records. `handle_request`
        is the session manager's documented ASGI entry point and is all the
        wrapper needs.
        """
        await mcp.session_manager.handle_request(scope, receive, send)

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
    ]

    # Carry over the REAL custom routes rather than re-implementing them.
    # Building a fresh Starlette discarded the FastMCP router entirely,
    # which (a) made /reports/{id} unreachable — the whole signed-capability
    # plane was dead code under the profile — and (b) replaced the genuine
    # ClickHouse-probing /health with a static 200 that would report a
    # healthy pod during a database outage. Allowlisted by path so the
    # browser mini-app plane cannot ride along.
    _CARRY_OVER = {"/reports/{report_id}", "/health", "/livez"}
    carried = {
        getattr(r, "path", None)
        for r in mcp._custom_starlette_routes
        if getattr(r, "path", None) in _CARRY_OVER
    }
    routes.extend(
        r for r in mcp._custom_starlette_routes
        if getattr(r, "path", None) in _CARRY_OVER
    )
    missing = _CARRY_OVER - carried
    if missing:
        # A renamed/removed custom route must not silently vanish from the
        # connector surface.
        raise RuntimeError(
            f"connector app expected custom route(s) {sorted(missing)} on the "
            "FastMCP app but they are not registered"
        )
    # /metrics is DELIBERATELY absent: it is unauthenticated (GDPR H4) and
    # the ALB does not forward it publicly. Scrape it on the internal
    # listener, never through the connector host.

    @contextlib.asynccontextmanager
    async def lifespan(app):  # noqa: ANN001
        async with mcp.session_manager.run():
            yield

    return Starlette(routes=routes, lifespan=lifespan)
