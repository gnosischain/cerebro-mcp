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
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote

from mcp.types import CallToolResult
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger(__name__)

PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# tool name -> plain callable returning a CallToolResult
MINI_APP_TOOL_REGISTRY: dict[str, Callable[..., CallToolResult]] = {}


@dataclass(frozen=True)
class WebAppConfig:
    app_id: str
    open_tool: str
    html_loader: Callable[[], str]
    # Catalog metadata — what the landing page shows on each card. Optional so
    # a registration that predates the catalog still works (falls back to a
    # title derived from app_id).
    title: str = ""
    description: str = ""
    icon: str = "▦"
    # Tool names this app may dispatch via POST /app/{app_id}/api/tool/{name}.
    # The registry is process-global; without this set any app iframe could
    # invoke any other app's tools (app-only metadata is visibility, NOT
    # authorization).
    allowed_tools: frozenset[str] = frozenset()
    # Optional build/source identity for local diagnostics and forensic exports.
    # Kept app-specific because split and single-file bundles have different
    # fingerprinting rules.
    diagnostics_loader: Callable[[], dict[str, Any]] | None = None


# app_id -> WebAppConfig
WEB_APP_CONFIGS: dict[str, WebAppConfig] = {}

# Shared mini-app infrastructure tools every app may call (row hydration /
# view state), registered via register_mini_app_tools rather than per-app.
_SHARED_INFRA_TOOLS = frozenset({"get_mini_app_rows", "get_mini_app_state"})

# Query-param aliases → candidate open-tool parameter names. The first param
# that actually exists on the open tool's signature wins.
_SEED_ALIASES = ("seed", "seed_model", "seed_node_id", "address")
_QUERY_PARAM_ALIASES = {
    "scope": "environment_scope",
    "chain": "chain_id",
    "base": "base_token",
    "quote": "quote_token",
    "start": "start_at",
    "end": "end_at",
    "entity": "entity_type",
    "id": "identifier",
}


def register_web_app(
    *,
    app_id: str,
    open_tool: str,
    html_loader: Callable[[], str],
    tools: dict[str, Callable[..., CallToolResult]],
    title: str = "",
    description: str = "",
    icon: str = "▦",
    diagnostics_loader: Callable[[], dict[str, Any]] | None = None,
) -> None:
    """Register a mini-app for standalone web delivery.

    Called from each ``register_*_tools`` function. Additive and idempotent —
    safe to call once per process boot.

    ``title`` / ``description`` / ``icon`` feed the app catalog at ``/``. They
    are optional so the catalog stays driven by this one registry — an app is
    listed because it registered, never because a landing page hardcoded it
    (dev-gated apps therefore appear only when they are actually registered).
    """
    WEB_APP_CONFIGS[app_id] = WebAppConfig(
        app_id=app_id,
        open_tool=open_tool,
        html_loader=html_loader,
        allowed_tools=frozenset(tools) | _SHARED_INFRA_TOOLS,
        title=title,
        description=description,
        icon=icon,
        diagnostics_loader=diagnostics_loader,
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
    for public_name, tool_name in _QUERY_PARAM_ALIASES.items():
        if public_name in raw and tool_name in params and tool_name not in raw:
            raw[tool_name] = raw.pop(public_name)
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


def _encode_tool_result(result: Any) -> bytes:
    """CPU-bound JSON conversion kept out of the async request loop."""
    return json.dumps(_result_to_dict(result), default=str).encode("utf-8")


def _gzip_bytes(body: bytes) -> bytes:
    import gzip

    return gzip.compress(body, compresslevel=6)


def _inject_payload(
    html: str,
    payload_json: str,
    app_id: str,
    token: str = "",
    diagnostics: dict[str, Any] | None = None,
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
    diagnostics_line = (
        "window.__MINI_APP_DIAGNOSTICS__="
        f"{json.dumps(diagnostics or {}, default=str)};"
    )
    snippet = (
        f'<script id="mini-app-data" type="application/json">{safe_json}</script>'
        f"<script>window.__MINI_APP_API__={json.dumps(api_base)};"
        f"{apps_line}{diagnostics_line}{token_line}</script>"
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
        result = await asyncio.to_thread(open_fn, **kwargs)
    except Exception as exc:  # noqa: BLE001 — surface as a 500 page, don't crash
        logger.exception("web app %s open failed", app_id)
        return JSONResponse({"error": str(exc)}, status_code=500)

    payload_json = await asyncio.to_thread(
        lambda: json.dumps(_json_safe_structured(result) or {})
    )
    # Forward whatever token the client already presented so the frontend can
    # authenticate its HTTP tool calls and cross-app nav links. We echo the
    # client's own credential — never more than it already had.
    token = request.query_params.get("token", "")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :]
    diagnostics: dict[str, Any] = {}
    if config.diagnostics_loader is not None:
        try:
            diagnostics = await asyncio.to_thread(config.diagnostics_loader)
        except Exception as exc:  # diagnostics must never take down the app
            logger.warning("web app %s diagnostics failed: %s", app_id, exc)
            diagnostics = {"status": "error", "error": str(exc)}
    shell = await asyncio.to_thread(config.html_loader)
    html = await asyncio.to_thread(
        _inject_payload, shell, payload_json, app_id, token, diagnostics
    )
    # gzip the (large, ~2.9MB single-file) bundle when the client accepts it —
    # ~40% wire cut. Scoped to this HTML route only; never touches the SSE
    # transport (response-buffering middleware would break the long-poll GET).
    # The HTML embeds a per-request token → never cache the shell (only the
    # hashed /assets/* are cacheable). For split-bundle apps the shell is tiny;
    # for single-file apps this preserves prior behavior (just adds no-store).
    accept = request.headers.get("accept-encoding", "").lower()
    if "gzip" in accept:
        body = await asyncio.to_thread(_gzip_bytes, html.encode("utf-8"))
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


async def serve_app_health(request: Request) -> JSONResponse:
    """Return process and bundle identity for one registered mini app."""
    denied = _check_auth(request)
    if denied is not None:
        return denied
    app_id = request.path_params["app_id"]
    config = WEB_APP_CONFIGS.get(app_id)
    if config is None:
        return JSONResponse({"error": f"Unknown app: {app_id}"}, status_code=404)
    diagnostics: dict[str, Any] = {}
    if config.diagnostics_loader is not None:
        try:
            diagnostics = await asyncio.to_thread(config.diagnostics_loader)
        except Exception as exc:
            logger.exception("web app %s health diagnostics failed", app_id)
            return JSONResponse(
                {
                    "status": "error",
                    "app_id": app_id,
                    "pid": os.getpid(),
                    "started_at": PROCESS_STARTED_AT,
                    "error": str(exc),
                },
                status_code=503,
            )
    return JSONResponse(
        {
            "status": "ok",
            "app_id": app_id,
            "pid": os.getpid(),
            "started_at": PROCESS_STARTED_AT,
            **diagnostics,
        },
        headers={"Cache-Control": "no-store"},
    )


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
        result = await asyncio.to_thread(fn, **kwargs)
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
    body = await asyncio.to_thread(_encode_tool_result, result)
    accept = request.headers.get("accept-encoding", "").lower()
    if "gzip" in accept and len(body) > 1024:
        return Response(
            content=await asyncio.to_thread(_gzip_bytes, body),
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
    # Hashed assets are flat filenames inside an app-specific namespace under
    # static/assets/<app_id>/ — reject any traversal.
    if "/" in asset_path or "\\" in asset_path or ".." in asset_path or not asset_path:
        return Response("bad path", status_code=400, media_type="text/plain")
    try:
        import importlib.resources as _res

        asset = (
            _res.files("cerebro_mcp")
            .joinpath("static/assets")
            .joinpath(app_id)
            .joinpath(asset_path)
        )
        data = await asyncio.to_thread(asset.read_bytes)
    except (FileNotFoundError, ModuleNotFoundError, OSError, NotADirectoryError):
        return Response("not found", status_code=404, media_type="text/plain")

    ext = os.path.splitext(asset_path)[1].lower()
    media = _ASSET_MEDIA.get(ext) or "application/octet-stream"
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    accept = request.headers.get("accept-encoding", "").lower()
    if ext in _GZIP_ASSET_EXT and "gzip" in accept:
        data = await asyncio.to_thread(_gzip_bytes, data)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
    return Response(content=data, media_type=media, headers=headers)


_CATALOG_CSS = """
/* EXACT copy of the `.mini-app-scope` token override in
 * ui/src/themes/tokens.css (NOT the :root block — the mini apps override it to
 * a cool near-black + sky-cyan palette). Keep these in sync with that block. */
:root{
  --bg:#0b0e12;--surface:#12161c;--surface-2:#1a1f26;--surface-3:#232932;
  --text-primary:#e6e9ee;--text-secondary:#aab3be;--text-muted:#868e9b;
  --border:rgba(255,255,255,0.12);--border-hover:rgba(255,255,255,0.22);
  --primary:#67e8f9;--primary-hover:#7dd3fc;
  --accent-bg:rgba(103,232,249,0.14);--accent-border:rgba(103,232,249,0.35);
  --accent-text:#a5e8f5;
  --surface-translucent:rgba(255,255,255,0.045);
  --surface-translucent-strong:rgba(255,255,255,0.08);
  --font-body:"Inter",system-ui,-apple-system,sans-serif;
  --font-mono:"JetBrains Mono",ui-monospace,Menlo,monospace;
  color-scheme:dark;
}
[data-theme="light"]{
  --bg:#ffffff;--surface:#ffffff;--surface-2:#f4f6f8;--surface-3:#e8ecef;
  --text-primary:#111418;--text-secondary:#404a59;--text-muted:#5b6473;
  --border:rgba(15,23,42,0.18);--border-hover:rgba(15,23,42,0.30);
  --primary:#0891b2;--primary-hover:#0e7490;
  --accent-bg:rgba(8,145,178,0.12);--accent-border:rgba(8,145,178,0.35);
  --accent-text:#0e7490;
  --surface-translucent:rgba(15,23,42,0.04);
  --surface-translucent-strong:rgba(15,23,42,0.07);
  color-scheme:light;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text-primary);
  font-family:var(--font-body);-webkit-font-smoothing:antialiased}

/* Chrome bar — same construction as .ma-bar in mini-app-chrome.css */
.bar{display:flex;align-items:center;border-bottom:1px solid var(--border);
  background:var(--surface)}
.brand{padding:5px 12px;font-family:var(--font-mono);font-size:10.5px;
  letter-spacing:.06em;color:var(--primary);border-right:1px solid var(--border);
  white-space:nowrap;display:inline-flex;align-items:center}
.bar-right{margin-left:auto;padding:5px 12px;border-left:1px solid var(--border);
  display:flex;align-items:center;gap:8px}
.tbtn{appearance:none;background:transparent;border:1px solid var(--border-hover);
  color:var(--text-secondary);border-radius:999px;width:24px;height:24px;
  display:inline-flex;align-items:center;justify-content:center;cursor:pointer;
  font-size:.85rem;line-height:1}
.tbtn:hover{background:var(--surface-translucent);color:var(--text-primary)}

.wrap{max-width:1100px;margin:0 auto;padding:36px 24px 64px}
h1{font-size:1.7rem;font-weight:650;margin:0 0 8px;letter-spacing:-.02em}
.sub{color:var(--text-secondary);font-size:.9rem;margin:0 0 30px;max-width:62ch;line-height:1.55}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card{display:flex;flex-direction:column;gap:9px;padding:18px;border-radius:12px;
  border:1px solid var(--border);background:var(--surface);text-decoration:none;
  color:inherit;transition:border-color .15s,background .15s,transform .15s}
.card:hover{border-color:var(--primary);background:var(--surface-2);transform:translateY(-2px)}
.card:focus-visible{outline:1px solid var(--primary);outline-offset:2px}
.top{display:flex;align-items:center;gap:11px}
.icon{width:36px;height:36px;flex:0 0 auto;display:grid;place-items:center;
  border-radius:9px;background:var(--accent-bg);border:1px solid var(--border-hover);
  font-size:1.1rem;color:var(--accent-text)}
.name{font-size:1rem;font-weight:600;letter-spacing:-.01em}
.desc{color:var(--text-secondary);font-size:.84rem;line-height:1.5;margin:0}
.id{margin-top:auto;padding-top:8px;font-family:var(--font-mono);font-size:.68rem;
  color:var(--text-muted);letter-spacing:.02em}
.empty{color:var(--text-secondary);border:1px dashed var(--border);border-radius:12px;
  padding:28px;text-align:center}
footer{margin-top:36px;color:var(--text-muted);font-size:.75rem;font-family:var(--font-mono)}
"""

# The mini apps do NOT persist theme — useTheme() just reads the `data-theme`
# attribute that each entry HTML declares (`<html data-theme="dark">`) and
# toggles it in place. The catalog mirrors that exactly: default dark, toggle
# flips the attribute, nothing stored.
_CATALOG_JS = """
(function(){
  var b=document.getElementById('theme');
  if(!b) return;
  b.addEventListener('click',function(){
    var el=document.documentElement;
    var dark=el.getAttribute('data-theme')!=='light';
    el.setAttribute('data-theme', dark?'light':'dark');
    b.textContent = dark?'\\u263D':'\\u2600';
  });
})();
"""


def _catalog_html(apps: list[WebAppConfig], token: str) -> str:
    """Server-rendered app catalog. Driven entirely by WEB_APP_CONFIGS so it
    can never drift from what is actually registered/served."""
    from html import escape

    suffix = f"?token={quote(token)}" if token else ""
    if apps:
        cards = "\n".join(
            f'<a class="card" href="/app/{escape(a.app_id)}{suffix}">'
            f'<div class="top"><div class="icon" aria-hidden="true">{escape(a.icon or "▦")}</div>'
            f'<div class="name">{escape(a.title or a.app_id.replace("_", " ").title())}</div></div>'
            f'<p class="desc">{escape(a.description or "Open this mini app.")}</p>'
            f'<div class="id">/app/{escape(a.app_id)}</div>'
            f"</a>"
            for a in apps
        )
        body = f'<div class="grid">{cards}</div>'
    else:
        body = '<div class="empty">No mini apps are registered in this server process.</div>'
    return (
        # data-theme="dark" mirrors every mini-app entry HTML.
        "<!doctype html><html lang=\"en\" data-theme=\"dark\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Cerebro · Mini Apps</title>"
        f"<style>{_CATALOG_CSS}</style></head><body>"
        # Same chrome bar the mini apps render, so entering/leaving an app is
        # visually continuous.
        '<div class="bar"><span class="brand">CEREBRO ◇ GNOSIS</span>'
        '<span class="bar-right">'
        '<button id="theme" class="tbtn" type="button" title="Toggle theme"'
        ' aria-label="Toggle theme">☀</button>'
        "</span></div>"
        '<div class="wrap">'
        "<h1>Mini apps</h1>"
        '<p class="sub">Interactive analysis surfaces served by this Cerebro MCP server. '
        "Each one opens standalone in the browser and talks to the same warehouse and tools "
        "the agent uses.</p>"
        f"{body}"
        f"<footer>{len(apps)} app{'' if len(apps) == 1 else 's'} registered</footer>"
        f"</div><script>{_CATALOG_JS}</script></body></html>"
    )


async def serve_app_catalog(request: Request) -> Response:
    """GET / — card catalog of every registered mini app."""
    denied = _check_auth(request)
    if denied is not None:
        return denied
    token = request.query_params.get("token", "")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :]
    apps = sorted(
        WEB_APP_CONFIGS.values(),
        key=lambda a: (a.title or a.app_id).lower(),
    )
    return Response(
        content=_catalog_html(apps, token),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},  # embeds a per-request token
    )


def register_web_app_routes(mcp) -> None:
    """Register the web-app routes on the FastMCP Starlette app."""
    # Assets first (more specific) so it wins over the bare /app/{app_id} route.
    mcp.custom_route(
        "/app/{app_id}/assets/{path:path}", methods=["GET"]
    )(serve_app_asset)
    mcp.custom_route("/app/{app_id}/health", methods=["GET"])(serve_app_health)
    mcp.custom_route("/app/{app_id}", methods=["GET"])(serve_app)
    mcp.custom_route(
        "/app/{app_id}/api/tool/{tool_name}", methods=["POST"]
    )(dispatch_app_tool)
    # Landing page: the catalog of everything registered above.
    mcp.custom_route("/", methods=["GET"])(serve_app_catalog)
    mcp.custom_route("/apps", methods=["GET"])(serve_app_catalog)
