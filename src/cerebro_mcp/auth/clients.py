"""The frozen client matrix (R10 C3) and endpoint policy.

Every OAuth client Cerebro accepts is enumerated HERE, per endpoint, with
its registration facts. A correctly signed token whose ``client_id`` names
any other application in the tenant is NOT authorization — the verifier
rejects it (R9 P0-5).

Endpoint model (R10 P0-2/P0-5): two resources, two client sets, two
challenge styles — routing decided by ENDPOINT, deterministically, before
any principal exists:

| endpoint      | audience                | clients               | style     |
|---------------|-------------------------|-----------------------|-----------|
| ``/mcp``      | ``<base>/mcp``          | Claude Team, Claude   | transport |
|               |                         | Code, Direct Codex    | 401/403   |
| ``/openai/mcp``| ``<base>/openai/mcp``  | hosted ChatGPT/plugin | in-band   |

A token minted for one Cerebro audience is rejected on the other endpoint
even though both audiences are configured globally (resource-specific
token requirement). ``aud`` may additionally contain non-Cerebro entries
(Auth0 appends ``/userinfo`` when ``openid`` is requested) — tolerated;
TWO Cerebro audiences in one token is not.

The concrete IDs are settings (they exist only after Auth0 registration);
this module supplies the structure and the checks. Registration facts that
CANNOT be known before registration (the ChatGPT ``callback_id``, exact
native-client callbacks captured from pinned versions) live in the deploy
runbook, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ChallengeStyle(Enum):
    TRANSPORT = "transport"   # HTTP 401/403 with WWW-Authenticate
    IN_BAND = "in_band"       # CallToolResult + _meta["mcp/www_authenticate"]


@dataclass(frozen=True)
class EndpointPolicy:
    path: str                     # exact route, no Mount prefixes (R9 P0-4)
    resource: str                 # the audience tokens must carry
    allowed_client_ids: frozenset[str]
    style: ChallengeStyle
    #: JSON-RPC methods servable WITHOUT a principal on this endpoint.
    public_methods: frozenset[str]


#: Methods the OPENAI endpoint serves unauthenticated (its documented
#: linking flow needs an anonymous initialize + tools/list, and the absent-
#: token tools/call must REACH the in-band challenge). tools/list public is
#: safe by design: listing is not a capability boundary — invocation is.
#: The /mcp endpoint has NO public methods: every HTTP request requires a
#: valid token (R10 C2), and Claude's lazy-auth flow starts from the 401.
_OPENAI_PUBLIC = frozenset(
    {"initialize", "notifications/initialized", "ping", "tools/list", "tools/call"}
)


def endpoint_policies() -> dict[str, EndpointPolicy]:
    """The two endpoint policies, built from live settings."""
    from cerebro_mcp.config import settings

    base = (settings.OAUTH_RESOURCE_BASE or "").rstrip("/")
    claude_clients = frozenset(
        c
        for c in (
            settings.OAUTH_CLIENT_CLAUDE_TEAM,
            settings.OAUTH_CLIENT_CLAUDE_CODE,
            settings.OAUTH_CLIENT_CODEX,
        )
        if c
    )
    openai_clients = frozenset(
        c for c in (settings.OAUTH_CLIENT_CHATGPT,) if c
    )
    return {
        "/mcp": EndpointPolicy(
            path="/mcp",
            resource=f"{base}/mcp",
            allowed_client_ids=claude_clients,
            style=ChallengeStyle.TRANSPORT,
            public_methods=frozenset(),  # every request needs a token (C2)
        ),
        "/openai/mcp": EndpointPolicy(
            path="/openai/mcp",
            resource=f"{base}/openai/mcp",
            allowed_client_ids=openai_clients,
            style=ChallengeStyle.IN_BAND,
            public_methods=_OPENAI_PUBLIC,
        ),
    }


def all_cerebro_resources() -> frozenset[str]:
    return frozenset(p.resource for p in endpoint_policies().values())
