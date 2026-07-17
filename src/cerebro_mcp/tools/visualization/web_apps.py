"""Standalone web-app delivery for the mini-apps.

The mini-apps already run in two modes: embedded inside an MCP host (via the
ext-apps bridge) and a dev-server mock. This module adds a third mode — a
plain browser URL — by:

  * ``GET  /app/{app_id}``               → serve the bundled single-file React
    app with the initial ``MiniAppPayload`` injected as
    ``<script id="mini-app-data">`` plus a ``window.__MINI_APP_API__`` pointer
    so the frontend's ``callTool`` HTTP fallback knows where to POST.
  * ``POST /app/{app_id}/api/tool/{tool}`` → dispatch a registered mini-app
    tool by name and return ``{structuredContent, isError, content}`` — the
    exact shape the frontend already expects from the ext-apps bridge.

No FastMCP internals are touched: each ``register_*_tools`` function registers
its plain callables here via :func:`register_web_app`, and the routes dispatch
through :data:`MINI_APP_TOOL_REGISTRY`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from mcp.types import CallToolResult
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger(__name__)

# tool name -> plain callable returning a CallToolResult
MINI_APP_TOOL_REGISTRY: dict[str, Callable[..., CallToolResult]] = {}


@dataclass(frozen=True)
class WebAppConfig:
    app_id: str
    open_tool: str
    html_loader: Callable[[], str]
    # Tool names this app may dispatch via POST /app/{app_id}/api/tool/{name}.
    # The registry is process-global; without this set any app iframe could
    # invoke any other app's tools (app-only metadata is visibility, NOT
    # authorization).
    allowed_tools: frozenset[str] = frozenset()


# app_id -> WebAppConfig
WEB_APP_CONFIGS: dict[str, WebAppConfig] = {}

# Shared mini-app infrastructure tools every app may call (row hydration /
# view state), registered via register_mini_app_tools rather than per-app.
_SHARED_INFRA_TOOLS = frozenset({"get_mini_app_rows", "get_mini_app_state"})

# Query-param aliases → candidate open-tool parameter names. The first param
# that actually exists on the open tool's signature wins.
_SEED_ALIASES = ("seed", "seed_model", "seed_node_id", "address")


def register_web_app(
    *,
    app_id: str,
    open_tool: str,
    html_loader: Callable[[], str],
    tools: dict[str, Callable[..., CallToolResult]],
) -> None:
    """Register a mini-app for standalone web delivery.

    Called from each ``register_*_tools`` function. Additive and idempotent —
    safe to call once per process boot.
    """
    WEB_APP_CONFIGS[app_id] = WebAppConfig(
        app_id=app_id,
        open_tool=open_tool,
        html_loader=html_loader,
        allowed_tools=frozenset(tools) | _SHARED_INFRA_TOOLS,
    )
    MINI_APP_TOOL_REGISTRY.update(tools)


def register_mini_app_tools(tools: dict[str, Callable[..., CallToolResult]]) -> None:
    """Register shared mini-app tools (pagination/state) into the registry."""
    MINI_APP_TOOL_REGISTRY.update(tools)


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def _coerce_arg(value: Any, annotation: Any) -> Any:
    """Best-effort coercion of a query-string value to the param annotation."""
    if annotation in (int, "int"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if annotation in (float, "float"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _filtered_kwargs(fn: Callable[..., Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only the kwargs that ``fn`` actually accepts, coercing scalars."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return dict(raw)
    params = sig.parameters
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key in params:
            out[key] = _coerce_arg(value, params[key].annotation)
    return out


def _open_kwargs_from_query(
    open_fn: Callable[..., Any], query: dict[str, str]
) -> dict[str, Any]:
    """Map query params to the open tool's signature, resolving the ``seed``
    alias to whichever seed-like parameter the open tool exposes."""
    try:
        params = inspect.signature(open_fn).parameters
    except (TypeError, ValueError):
        params = {}
    raw = dict(query)
    if "seed" in raw and "seed" not in params:
        seed_value = raw.pop("seed")
        for candidate in _SEED_ALIASES[1:]:
            if candidate in params:
                raw[candidate] = seed_value
                break
    return _filtered_kwargs(open_fn, raw)


def _json_safe_structured(result: CallToolResult) -> dict[str, Any] | None:
    """Return ``result.structuredContent`` with all values coerced to
    JSON-serializable primitives (``None`` when there is no content).

    Starlette's ``JSONResponse`` / stdlib ``json.dumps`` cannot serialize
    ``date``/``datetime`` objects, but mini-app payloads routinely carry them.
    The normal MCP bridge avoids this by serializing through Pydantic with
    ``mode="json"`` (which renders dates as ISO-8601 strings); we mirror that
    here so the HTTP web-app path behaves identically.
    """
    if not result.structuredContent:
        return None
    dumped = result.model_dump(mode="json", exclude_none=True)
    return dumped.get("structuredContent")


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Adapt a tool result to the JSON shape the frontend expects.

    A mini-app tool may return a ``CallToolResult`` OR a plain JSON-able value
    (dict/list) — the MCP bridge auto-wraps the latter as ``structuredContent``,
    so the HTTP web-app path must do the same. Without this, a plain-dict tool
    (e.g. search_graph_catalog / explore_neighborhood / calculate_flow_efficiency)
    500s here on ``result.content`` / ``result.model_dump``.
    """
    if not isinstance(result, CallToolResult):
        # Plain JSON-able value (dict/list). Coerce non-JSON primitives
        # (datetime/date from ClickHouse rows, Decimal, etc.) to strings so the
        # stdlib json.dumps in JSONResponse can't 500 — mirrors the date-safe
        # behavior the CallToolResult path gets from Pydantic mode="json".
        safe = json.loads(json.dumps(result, default=str))
        return {"structuredContent": safe, "isError": False, "content": []}
    content: list[Any] = []
    for item in result.content or []:
        try:
            content.append(item.model_dump(mode="json", exclude_none=True))
        except AttributeError:
            content.append(item)
    return {
        "structuredContent": _json_safe_structured(result),
        "isError": bool(result.isError),
        "content": content,
    }


def _inject_payload(
    html: str, payload_json: str, app_id: str, token: str = ""
) -> str:
    """Inject the initial payload + API pointer before the first script tag so
    ``loadEmbedded()`` finds the data when the app module executes.

    When ``token`` is set (the auth token the client presented), it is also
    injected as ``window.__MINI_APP_TOKEN__`` so the frontend can authenticate
    its HTTP tool calls and cross-app navigation links.
    """
    # Escape `<` so a `</script>` substring inside SQL/text can't break out.
    safe_json = payload_json.replace("<", "\\u003c")
    api_base = f"/app/{app_id}/api/tool"
    token_line = (
        f"window.__MINI_APP_TOKEN__={json.dumps(token)};" if token else ""
    )
    # Registered-app list: the chrome filters its cross-app tabs on this so
    # tabs for unregistered (e.g. dev-only) apps never render in standalone
    # mode. Dev (`npm run dev`) has no injection and keeps the static list.
    apps_line = f"window.__MINI_APP_APPS__={json.dumps(sorted(WEB_APP_CONFIGS))};"
    snippet = (
        f'<script id="mini-app-data" type="application/json">{safe_json}</script>'
        f"<script>window.__MINI_APP_API__={json.dumps(api_base)};"
        f"{apps_line}{token_line}</script>"
    )
    lower = html.lower()
    idx = lower.find("<script")
    if idx == -1:
        idx = lower.find("</body>")
    if idx == -1:
        return html + snippet
    return html[:idx] + snippet + html[idx:]


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _check_auth(request: Request) -> JSONResponse | None:
    """Mirror the /reports auth: optional bearer header or ?token= param."""
    auth_token = os.environ.get("MCP_AUTH_TOKEN")
    if not auth_token:
        return None
    auth_header = request.headers.get("Authorization", "")
    query_token = request.query_params.get("token", "")
    if auth_header == f"Bearer {auth_token}" or query_token == auth_token:
        return None
    return JSONResponse({"error": "unauthorized"}, status_code=401)


async def serve_app(request: Request) -> Response:
    """GET /app/{app_id} — render the bundled app with initial payload."""
    denied = _check_auth(request)
    if denied is not None:
        return denied

    app_id = request.path_params["app_id"]
    config = WEB_APP_CONFIGS.get(app_id)
    if config is None:
        return JSONResponse(
            {"error": f"Unknown app: {app_id}", "available": sorted(WEB_APP_CONFIGS)},
            status_code=404,
        )

    open_fn = MINI_APP_TOOL_REGISTRY.get(config.open_tool)
    if open_fn is None:
        return JSONResponse(
            {"error": f"Open tool not registered: {config.open_tool}"},
            status_code=500,
        )

    query = {k: v for k, v in request.query_params.items() if k != "token"}
    kwargs = _open_kwargs_from_query(open_fn, query)
    try:
        result = open_fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 — surface as a 500 page, don't crash
        logger.exception("web app %s open failed", app_id)
        return JSONResponse({"error": str(exc)}, status_code=500)

    payload_json = json.dumps(_json_safe_structured(result) or {})
    # Forward whatever token the client already presented so the frontend can
    # authenticate its HTTP tool calls and cross-app nav links. We echo the
    # client's own credential — never more than it already had.
    token = request.query_params.get("token", "")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :]
    html = _inject_payload(config.html_loader(), payload_json, app_id, token)
    # gzip the (large, ~2.9MB single-file) bundle when the client accepts it —
    # ~40% wire cut. Scoped to this HTML route only; never touches the SSE
    # transport (response-buffering middleware would break the long-poll GET).
    # The HTML embeds a per-request token → never cache the shell (only the
    # hashed /assets/* are cacheable). For split-bundle apps the shell is tiny;
    # for single-file apps this preserves prior behavior (just adds no-store).
    accept = request.headers.get("accept-encoding", "").lower()
    if "gzip" in accept:
        import gzip as _gzip

        body = _gzip.compress(html.encode("utf-8"), compresslevel=6)
        return Response(
            content=body,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Encoding": "gzip",
                "Vary": "Accept-Encoding",
                "Cache-Control": "no-store",
            },
        )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


async def dispatch_app_tool(request: Request) -> JSONResponse:
    """POST /app/{app_id}/api/tool/{tool_name} — run a registered tool."""
    denied = _check_auth(request)
    if denied is not None:
        return denied

    app_id = request.path_params["app_id"]
    tool_name = request.path_params["tool_name"]
    config = WEB_APP_CONFIGS.get(app_id)
    if config is None:
        return JSONResponse({"error": f"Unknown app: {app_id}"}, status_code=404)
    if tool_name not in config.allowed_tools:
        return JSONResponse(
            {"error": f"Tool '{tool_name}' is not available for app '{app_id}'"},
            status_code=404,
        )

    fn = MINI_APP_TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return JSONResponse(
            {"error": f"Unknown tool: {tool_name}"}, status_code=404
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    arguments = body.get("arguments") if isinstance(body, dict) else None
    if not isinstance(arguments, dict):
        arguments = {}

    kwargs = _filtered_kwargs(fn, arguments)
    try:
        # Run the (synchronous) tool in a worker thread so a slow/unreachable
        # ClickHouse call can't block the event loop and freeze the whole server.
        # CH-free tools (search / entity / lineage / governance) then keep
        # responding even while a CH-touching tool (sample / stats) is stalled.
        result = await asyncio.get_event_loop().run_in_executor(None, lambda: fn(**kwargs))
    except Exception as exc:  # noqa: BLE001
        logger.exception("web app %s tool %s failed", app_id, tool_name)
        return JSONResponse(
            {"structuredContent": None, "isError": True, "content": [
                {"type": "text", "text": f"Error: {exc}"}
            ]},
            status_code=200,
        )

    # gzip large tool payloads (e.g. lineage subgraphs run to hundreds of KB)
    # when the client accepts it — same treatment the /assets route gets.
    body = json.dumps(_result_to_dict(result), default=str).encode("utf-8")
    accept = request.headers.get("accept-encoding", "").lower()
    if "gzip" in accept and len(body) > 1024:
        import gzip as _gzip

        return Response(
            content=_gzip.compress(body, compresslevel=6),
            media_type="application/json",
            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
        )
    return Response(content=body, media_type="application/json")


_ASSET_MEDIA = {
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
    ".svg": "image/svg+xml", ".json": "application/json", ".map": "application/json",
    ".png": "image/png", ".webp": "image/webp",
}
_GZIP_ASSET_EXT = {".js", ".mjs", ".css", ".svg", ".json", ".map"}


async def serve_app_asset(request: Request) -> Response:
    """GET /app/{app_id}/assets/{path} — serve hashed, immutable build assets
    (JS/CSS/fonts) for split-bundle apps with long-lived cache headers.

    These are public client assets (app code + fonts, no data); auth gates the
    tool-dispatch route, not the static code. Filenames are content-hashed by
    Vite, so ``immutable`` + a 1-year max-age is safe — a new build = a new name.
    """
    app_id = request.path_params["app_id"]
    asset_path = request.path_params["path"]
    if app_id not in WEB_APP_CONFIGS:
        return Response("unknown app", status_code=404, media_type="text/plain")
    # Hashed assets are flat filenames under static/assets/ — reject any traversal.
    if "/" in asset_path or "\\" in asset_path or ".." in asset_path or not asset_path:
        return Response("bad path", status_code=400, media_type="text/plain")
    try:
        import importlib.resources as _res

        data = (
            _res.files("cerebro_mcp")
            .joinpath("static/assets")
            .joinpath(asset_path)
            .read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError, NotADirectoryError):
        return Response("not found", status_code=404, media_type="text/plain")

    ext = os.path.splitext(asset_path)[1].lower()
    media = _ASSET_MEDIA.get(ext) or "application/octet-stream"
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    accept = request.headers.get("accept-encoding", "").lower()
    if ext in _GZIP_ASSET_EXT and "gzip" in accept:
        import gzip as _gzip

        data = _gzip.compress(data, compresslevel=6)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
    return Response(content=data, media_type=media, headers=headers)


def register_web_app_routes(mcp) -> None:
    """Register the web-app routes on the FastMCP Starlette app."""
    # Assets first (more specific) so it wins over the bare /app/{app_id} route.
    mcp.custom_route(
        "/app/{app_id}/assets/{path:path}", methods=["GET"]
    )(serve_app_asset)
    mcp.custom_route("/app/{app_id}", methods=["GET"])(serve_app)
    mcp.custom_route(
        "/app/{app_id}/api/tool/{tool_name}", methods=["POST"]
    )(dispatch_app_tool)
