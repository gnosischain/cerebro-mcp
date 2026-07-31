"""The complete challenge contract (R10 C1-C3, R9 P0-3).

Five response forms, byte-stable because real clients key on them:

| condition                          | form                                    |
|------------------------------------|-----------------------------------------|
| missing token                      | 401 + baseline scope + resource_metadata|
| present but invalid/expired/wrong  | 401 error="invalid_token" (+ metadata — |
|   audience/client                  |   recovery needs the PRM pointer too)   |
| malformed / duplicate credential   | 400 error="invalid_request"             |
| valid, insufficient scope          | 403 error="insufficient_scope" +        |
|                                    |   COMPLETE union + resource_metadata    |
| valid, insufficient, OpenAI客户端  | in-band CallToolResult with             |
|                                    |   _meta["mcp/www_authenticate"]         |

The in-band form is what actually triggers ChatGPT's linking UI (transport
403 is ignored there; codex#20518 keeps step-up unimplemented), and it is a
RECORDED DEVIATION from MCP's preferred transport 403. The header value is
a BARE string — the single quotes in OpenAI's docs example are prose, not
part of the value.
"""

from __future__ import annotations

from cerebro_mcp.tools.tool_policy import SCOPE_DISCOVER


def _header(**params: str) -> str:
    inner = ", ".join(f'{k}="{v}"' for k, v in params.items() if v)
    return f"Bearer {inner}"


def prm_url(resource: str) -> str:
    """RFC 9728 path-inserted metadata URL for a resource URI."""
    scheme_host, _, path = resource.partition("://")[2].partition("/")
    scheme = resource.split("://", 1)[0]
    return f"{scheme}://{scheme_host}/.well-known/oauth-protected-resource/{path}"


def challenge_missing(resource: str) -> tuple[int, str]:
    """No Authorization header: advertise the baseline scope. The scope
    parameter is REQUIRED house policy — omitting it triggers the client
    fallback of requesting every scope in scopes_supported (a maximal
    grant, the inverse of progressive scopes)."""
    return 401, _header(
        realm="cerebro",
        scope=SCOPE_DISCOVER,
        resource_metadata=prm_url(resource),
    )


def challenge_invalid(resource: str, description: str) -> tuple[int, str]:
    return 401, _header(
        error="invalid_token",
        error_description=description,
        resource_metadata=prm_url(resource),
    )


def challenge_malformed(description: str) -> tuple[int, str]:
    return 400, _header(error="invalid_request", error_description=description)


def challenge_insufficient(
    resource: str, required_scopes: frozenset[str]
) -> tuple[int, str]:
    """COMPLETE required union, sorted for determinism — never just the
    first missing scope (the pinned SDK's middleware gets this wrong,
    which is why it is not used)."""
    return 403, _header(
        error="insufficient_scope",
        scope=" ".join(sorted(required_scopes)),
        resource_metadata=prm_url(resource),
    )


def inband_challenge_meta(
    resource: str,
    *,
    error: str,
    description: str,
    required_scopes: frozenset[str] | None = None,
) -> dict:
    """`_meta` payload for the in-band CallToolResult (OpenAI contract).

    Requires per-tool securitySchemes to be advertised as well — without
    BOTH halves ChatGPT shows no linking UI.
    """
    params: dict[str, str] = {
        "error": error,
        "error_description": description,
        "resource_metadata": prm_url(resource),
    }
    if required_scopes:
        params["scope"] = " ".join(sorted(required_scopes))
    return {"mcp/www_authenticate": _header(**params)}
