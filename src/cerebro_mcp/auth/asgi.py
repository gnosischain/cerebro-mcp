"""Per-endpoint ASGI auth gates (R10 C1-C2, R9 P0-1/P0-2).

Two gates, selected by ENDPOINT — deterministic before any principal
exists (that was R8's fatal flaw: routing the challenge style by a claim
that does not exist yet on first contact):

``TransportAuthGate`` (``/mcp`` — Claude Team / Claude Code / Codex):
    every HTTP request requires a valid token; missing/invalid/malformed
    produce the transport 401/400 forms; a valid-but-under-scoped
    ``tools/call`` produces the transport 403 with the COMPLETE union —
    all BEFORE the MCP SDK executes (the SDK converts in-handler
    exceptions into MCP error results, so an outer middleware is the only
    place a real HTTP 403 can be born — R9 P0-3).

``OpenAIAuthGate`` (``/openai/mcp`` — hosted ChatGPT / plugin):
    POST and OPTIONS only (GET/DELETE 405 — an unauthenticated caller
    must never hold a stream); anonymous initialize/tools-list pass;
    an absent-token or under-scoped ``tools/call`` is answered with the
    IN-BAND CallToolResult carrying ``_meta["mcp/www_authenticate"]``
    (the only signal that triggers ChatGPT's linking UI — recorded
    deviation from MCP's preferred 403); a PRESENT-but-invalid token is
    HTTP 401 (C1: missing and invalid are different cases). The
    ``initialize`` RESPONSE is filtered to a tools-only capability
    object — FastMCP registers resource/prompt handlers unconditionally,
    so capability stripping must happen on the wire.

Body handling is bounded (content-type enforced, byte cap, replayed via a
buffered ``receive``), never logged, and disconnect-safe.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cerebro_mcp.auth import challenge
from cerebro_mcp.auth.clients import EndpointPolicy
from cerebro_mcp.auth.jwt_verifier import (
    CerebroTokenVerifier,
    InvalidToken,
    MalformedCredential,
    extract_bearer,
)
from cerebro_mcp.runtime.identity import (
    CerebroPrincipal,
    reset_current_owner,
    set_current_owner_prehashed,
)
from cerebro_mcp.tools.tool_policy import SCOPE_DISCOVER, TOOL_POLICY

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_048_576  # bounded buffer (R10 C2): reject bigger bodies


class _BodyTooLarge(Exception):
    pass


async def _buffer_body(receive: Receive) -> tuple[bytes, Receive]:
    """Read the request body (bounded) and return a replaying receive."""
    chunks: list[bytes] = []
    total = 0
    more = True
    while more:
        message = await receive()
        if message["type"] == "http.disconnect":
            # Client went away mid-body: propagate the disconnect to the
            # downstream app via the replay and stop buffering.
            async def _disconnected() -> Message:
                return {"type": "http.disconnect"}

            return b"", _disconnected
        body = message.get("body", b"")
        total += len(body)
        if total > MAX_BODY_BYTES:
            raise _BodyTooLarge()
        chunks.append(body)
        more = message.get("more_body", False)
    payload = b"".join(chunks)

    sent = False

    async def _replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        # After the buffered body is replayed, DELEGATE to the real
        # receive. Synthesizing a disconnect here told the SDK the client
        # had gone away, tearing down an in-flight SSE POST response
        # (HTTP 200 with zero bytes) whenever json_response was off.
        return await receive()

    return payload, _replay


def _parse_jsonrpc(body: bytes) -> tuple[str | None, str | None, Any]:
    """(method, tool_name, request_id) — Nones when unparseable. The gate
    treats an unparseable body as method None (default deny); the SDK
    produces the protocol-level parse error for well-formed transports."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None
    method = payload.get("method")
    request_id = payload.get("id")
    tool = None
    if method == "tools/call":
        params = payload.get("params")
        if isinstance(params, dict):
            name = params.get("name")
            tool = name if isinstance(name, str) else None
    return (method if isinstance(method, str) else None), tool, request_id


def _required_scopes_for(method: str, tool: str | None) -> frozenset[str] | None:
    """The scope union a method requires, or None for DEFAULT DENY.

    Baseline protocol methods need cerebro:discover; tools/call needs the
    tool's complete union; prompts/* and anything unknown are denied (the
    method matrix is exhaustive by construction — R9-audit blocker 2).
    """
    from cerebro_mcp.tools.tool_policy import SCOPE_DISCOVER

    baseline = frozenset({SCOPE_DISCOVER})
    if method in (
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
        "resources/list",
        "resources/templates/list",
        "resources/read",
        "completion/complete",
    ):
        return baseline
    if method == "tools/call":
        if tool is None:
            return None
        policy = TOOL_POLICY.get(tool)
        # Excluded tools are rejected later by CerebroFastMCP.call_tool;
        # scope-wise they need at least the baseline to get that far.
        return policy.scopes if policy else baseline
    if method and method.startswith("notifications/"):
        return baseline
    return None


def _auth_header(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            return value.decode("latin-1")
    return None


def _plain(status: int, www_authenticate: str | None = None) -> Response:
    headers = {"WWW-Authenticate": www_authenticate} if www_authenticate else {}
    return JSONResponse(
        {"error": "unauthorized" if status in (400, 401, 403) else "error"},
        status_code=status,
        headers=headers,
    )


async def _run_with_principal(
    app: ASGIApp,
    principal: CerebroPrincipal | None,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """Principal bridge: scope state + owner contextvar, set/reset."""
    scope.setdefault("state", {})["cerebro_principal"] = principal
    token = set_current_owner_prehashed(principal.owner if principal else None)
    try:
        await app(scope, receive, send)
    finally:
        reset_current_owner(token)


class TransportAuthGate:
    """/mcp: verify -> scope-gate -> SDK. Transport challenge forms only."""

    def __init__(
        self, app: ASGIApp, policy: EndpointPolicy, verifier: CerebroTokenVerifier
    ):
        self._app = app
        self._policy = policy
        self._verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method = scope["method"]
        if method == "OPTIONS":
            await self._app(scope, receive, send)
            return

        resource = self._policy.resource
        try:
            bearer = extract_bearer(_auth_header(scope))
        except MalformedCredential as exc:
            status, header = challenge.challenge_malformed(str(exc))
            await _plain(status, header)(scope, receive, send)
            return
        if bearer is None:
            status, header = challenge.challenge_missing(resource)
            await _plain(status, header)(scope, receive, send)
            return
        try:
            principal = self._verifier.verify(bearer)
        except MalformedCredential as exc:
            status, header = challenge.challenge_malformed(str(exc))
            await _plain(status, header)(scope, receive, send)
            return
        except InvalidToken as exc:
            status, header = challenge.challenge_invalid(resource, str(exc))
            await _plain(status, header)(scope, receive, send)
            return

        if method == "POST":
            try:
                body, replay = await _buffer_body(receive)
            except _BodyTooLarge:
                await _plain(413)(scope, receive, send)
                return
            rpc_method, tool, _ = _parse_jsonrpc(body)
            required = _required_scopes_for(rpc_method or "", tool)
            if required is None:
                await _plain(403)(scope, receive, send)
                return
            missing = required - principal.scopes
            if missing:
                status, header = challenge.challenge_insufficient(
                    resource, required
                )
                await _plain(status, header)(scope, receive, send)
                return
            await _run_with_principal(self._app, principal, scope, replay, send)
            return

        # GET (stream) / DELETE (session teardown) carry no JSON-RPC body,
        # so there is no per-method union to resolve — but they still need
        # the BASELINE scope. Without this a zero-scope token (valid, but
        # granted nothing) could open and hold an SSE stream.
        baseline = frozenset({SCOPE_DISCOVER})
        if baseline - principal.scopes:
            status, header = challenge.challenge_insufficient(resource, baseline)
            await _plain(status, header)(scope, receive, send)
            return
        await _run_with_principal(self._app, principal, scope, receive, send)


def _inband_envelope(request_id: Any, meta: dict, text: str) -> JSONResponse:
    """A complete JSON-RPC response carrying the in-band challenge result.

    Synthesized at the gate (json_response mode is on, so a plain JSON
    body is the wire format): the SDK never sees the call, which is the
    point — no principal exists yet, and the SDK's own auth would have
    401'd before call_tool (R9 P0-1).
    """
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id if request_id is not None else 0,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": True,
                "_meta": meta,
            },
        }
    )


async def _noop_receive() -> Message:
    return {"type": "http.disconnect"}


def _rewrite_initialize_payload(payload: dict) -> dict:
    caps = payload.get("result", {}).get("capabilities")
    if isinstance(caps, dict):
        caps.pop("resources", None)
        caps.pop("prompts", None)
    return payload


class _CapabilityFilter:
    """Rewrites the initialize RESPONSE to a tools-only capability object.

    FastMCP registers resource and prompt handlers unconditionally in
    ``_setup_handlers``, so the advertised capabilities always include
    ``resources``/``prompts`` — the only place a tools-only object can be
    produced is on the wire (R9-audit blocker 2, adapted to the shared-
    instance composition).
    """

    def __init__(self, send: Send):
        self._send = send
        self._start: Message | None = None
        self._chunks: list[bytes] = []

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self._start = message
            return
        if message["type"] == "http.response.body":
            self._chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            await self._flush()
            return
        await self._send(message)

    async def _flush(self) -> None:
        assert self._start is not None
        body = b"".join(self._chunks)
        rewritten = self._strip_capabilities(body)
        if rewritten is None:
            # Could not parse the initialize response, so could not prove
            # the advertised capabilities are tools-only. Passing it
            # through would re-advertise `resources`/`prompts` that the
            # endpoint refuses to serve — deny instead of guessing.
            logger.error(
                "unparseable initialize response on /openai/mcp; refusing "
                "to forward an unverified capability object"
            )
            await _plain(502)(
                {"type": "http"}, _noop_receive, self._send
            )
            return
        body = rewritten
        headers = [
            (k, v)
            for k, v in self._start.get("headers", [])
            if k.lower() != b"content-length"
        ]  # noqa: E501
        headers.append((b"content-length", str(len(body)).encode()))
        await self._send({**self._start, "headers": headers})
        await self._send(
            {"type": "http.response.body", "body": body, "more_body": False}
        )

    @staticmethod
    def _strip_capabilities(body: bytes) -> bytes | None:
        """Remove resources/prompts from an initialize response.

        Handles BOTH wire formats, because ``STREAMABLE_HTTP_JSON_RESPONSE``
        is configurable: a plain JSON body, and an SSE-framed body whose
        payload rides in ``data:`` lines. Returns None when neither parses —
        the caller must then DENY rather than forward a capability object it
        could not verify (an unparsed pass-through re-advertised resources
        and prompts the endpoint refuses to serve).
        """
        try:
            return json.dumps(
                _rewrite_initialize_payload(json.loads(body))
            ).encode()
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass
        try:
            text = body.decode()
        except UnicodeDecodeError:
            return None
        if "data:" not in text:
            return None
        out_lines, rewrote = [], False
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("data:"):
                raw = stripped[len("data:"):].strip()
                try:
                    payload = _rewrite_initialize_payload(json.loads(raw))
                except ValueError:
                    return None
                newline = line[len(line.rstrip("\r\n")):]
                out_lines.append(f"data: {json.dumps(payload)}{newline}")
                rewrote = True
            else:
                out_lines.append(line)
        return "".join(out_lines).encode() if rewrote else None


class OpenAIAuthGate:
    """/openai/mcp: optional-bearer, in-band challenges, POST/OPTIONS only."""

    def __init__(
        self, app: ASGIApp, policy: EndpointPolicy, verifier: CerebroTokenVerifier
    ):
        self._app = app
        self._policy = policy
        self._verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method = scope["method"]
        if method == "OPTIONS":
            await self._app(scope, receive, send)
            return
        if method != "POST":
            # An unauthenticated caller must never open or hold a stream.
            await Response(status_code=405, headers={"Allow": "POST, OPTIONS"})(
                scope, receive, send
            )
            return

        resource = self._policy.resource
        # C1: three states — absent, present-and-invalid, present-and-valid.
        principal: CerebroPrincipal | None = None
        try:
            bearer = extract_bearer(_auth_header(scope))
        except MalformedCredential as exc:
            status, header = challenge.challenge_malformed(str(exc))
            await _plain(status, header)(scope, receive, send)
            return
        if bearer is not None:
            try:
                principal = self._verifier.verify(bearer)
            except MalformedCredential as exc:
                status, header = challenge.challenge_malformed(str(exc))
                await _plain(status, header)(scope, receive, send)
                return
            except InvalidToken as exc:
                # PRESENT but unacceptable -> HTTP 401 (MCP token handling;
                # OpenAI's instruction for failed verification).
                status, header = challenge.challenge_invalid(resource, str(exc))
                await _plain(status, header)(scope, receive, send)
                return

        try:
            body, replay = await _buffer_body(receive)
        except _BodyTooLarge:
            await _plain(413)(scope, receive, send)
            return
        rpc_method, tool, request_id = _parse_jsonrpc(body)

        if rpc_method not in self._policy.public_methods and principal is None:
            # Everything non-public needs a principal; non-tools/call gets
            # a transport 401 (no linking UI applies to it).
            status, header = challenge.challenge_missing(resource)
            await _plain(status, header)(scope, receive, send)
            return

        if rpc_method == "tools/call":
            required = _required_scopes_for(rpc_method, tool)
            if principal is None:
                meta = challenge.inband_challenge_meta(
                    resource,
                    error="invalid_token",
                    description="authentication required",
                    required_scopes=required,
                )
                await _inband_envelope(
                    request_id, meta, "Authentication required."
                )(scope, receive, send)
                return
            if required is None:
                await _plain(403)(scope, receive, send)
                return
            missing = required - principal.scopes
            if missing:
                meta = challenge.inband_challenge_meta(
                    resource,
                    error="insufficient_scope",
                    description="additional authorization required",
                    required_scopes=required,
                )
                await _inband_envelope(
                    request_id, meta, "Additional authorization required."
                )(scope, receive, send)
                return
        elif rpc_method not in self._policy.public_methods:
            # authenticated but non-public, non-tools/call: default deny
            await _plain(403)(scope, receive, send)
            return

        wrapped_send: Send | Callable[[Message], Awaitable[None]] = send
        if rpc_method == "initialize":
            wrapped_send = _CapabilityFilter(send)
        await _run_with_principal(
            self._app, principal, scope, replay, wrapped_send
        )
