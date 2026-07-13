import importlib.resources
import json
import logging
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from numbers import Number
from pathlib import Path
from urllib.parse import quote
import sys
if sys.version_info >= (3, 11):
    from typing import NotRequired, TypedDict
else:
    from typing_extensions import NotRequired, TypedDict

from mcp.types import Annotations, CallToolResult, TextContent

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.runtime.observability import (
    log_event,
    observe_semantic_bypass,
    observe_semantic_tool_call,
)
from cerebro_mcp.runtime.offload import offloaded as _offloaded
from cerebro_mcp.tools.analytics.query import truncate_response, _truncate_sql
from cerebro_mcp.tools.governance.reasoning import _extract_models_from_sql
from cerebro_mcp.tools.governance.session_state import state


def _single_source_model(sql: str) -> str | None:
    """Return the single dbt model referenced in `sql`, or None if 0 or >1."""
    models = {m for m in _extract_models_from_sql(sql or "") if m}
    if len(models) == 1:
        return next(iter(models))
    return None


logger = logging.getLogger(__name__)


class ChartSpec(TypedDict):
    """Typed specification for a single chart in a batch generate_charts call."""
    sql: str
    database: NotRequired[str]
    chart_type: NotRequired[str]
    x_field: NotRequired[str]
    y_field: NotRequired[str]
    change_field: NotRequired[str]
    series_field: NotRequired[str]
    title: NotRequired[str]
    max_rows: NotRequired[int]


class MetricChartSpec(TypedDict):
    """Typed specification for a single semantic chart in a batch call."""

    metrics: list[str]
    dimensions: NotRequired[list[str]]
    filters: NotRequired[list[dict]]
    order_by: NotRequired[list[str]]
    limit: NotRequired[int]
    chart_type: NotRequired[str]
    x_field: NotRequired[str]
    y_field: NotRequired[str]
    change_field: NotRequired[str]
    series_field: NotRequired[str]
    title: NotRequired[str]


_SEMANTIC_DIMENSION_ALIASES = {
    "date": "day",
}


# --- Bundled React UI (Vite single-file build) ---
_BUNDLED_REPORT_HTML: str | None = None

# Minimal shell used when the Vite build artifact (`make build-ui-report` ->
# static/report.html) is absent — e.g. a source checkout or CI that hasn't run
# the UI build. Report generation still embeds its data (as a <script> before
# </body>), so a report degrades gracefully instead of crashing with
# FileNotFoundError.
_FALLBACK_REPORT_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Cerebro Report</title></head><body><div id=\"root\">"
    "<noscript>Cerebro report — interactive UI bundle not built "
    "(run <code>make build-ui-report</code>). The report data is embedded below."
    "</noscript></div></body></html>"
)


def _get_report_html() -> str:
    """Load the Vite-built single-file React app from the static package.

    Falls back to a minimal shell when the build artifact is missing so the
    server (and the test suite) works without a UI build.
    """
    global _BUNDLED_REPORT_HTML
    if _BUNDLED_REPORT_HTML is None:
        try:
            _BUNDLED_REPORT_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/report.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, OSError):
            _BUNDLED_REPORT_HTML = _FALLBACK_REPORT_HTML
    return _BUNDLED_REPORT_HTML

# ECharts color palettes matching metrics-dashboard
ECHARTS_PALETTE_LIGHT = [
    "#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#3B82F6", "#EC4899", "#14B8A6", "#F97316", "#84CC16",
    "#06B6D4", "#A855F7", "#22C55E", "#FB7185", "#0EA5E9",
]
ECHARTS_PALETTE_DARK = [
    "#818CF8", "#34D399", "#FBBF24", "#F87171", "#A78BFA",
    "#60A5FA", "#F472B6", "#2DD4BF", "#FDBA74", "#A3E635",
    "#67E8F9", "#C4B5FD", "#4ADE80", "#FDA4AF", "#38BDF8",
]

# Canonical cerebro chart palette handed to the model for inline rendering, so
# a model-drawn visualization matches the native "Gnosis Terminal" report look.
# Mirrors ui/src/themes/echarts-dark.ts / echarts-light.ts.
CEREBRO_CHART_PALETTE_DARK = [
    "#B4F03C", "#7B61FF", "#FF7A9C", "#4DD0E1", "#C6A6FF", "#F5B14C",
]
CEREBRO_CHART_PALETTE_LIGHT = [
    "#5E7A0A", "#5B44E0", "#C11E5B", "#0E7C8B", "#6B3FD1", "#B25E00",
]
CEREBRO_CHART_FONT = "JetBrains Mono, ui-monospace, SFMono-Regular, monospace"


def _open_in_browser_async(url: str) -> None:
    """Open ``url`` in the default browser WITHOUT blocking the caller.

    On macOS ``webbrowser.open`` drives a blocking ``osascript``/``open
    location`` call; a cold browser launch takes seconds. Sync MCP tools run
    inline on the single asyncio event loop, so a synchronous open would freeze
    the whole server (and time out every concurrent tool). Running it on a
    daemon thread makes it truly fire-and-forget.
    """
    def _open() -> None:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            # Best-effort; never break the artifact.
            pass

    threading.Thread(target=_open, daemon=True).start()


# --- Chart Registry ---
_chart_registry: dict[str, dict] = {}
_chart_counter = 0
_chart_lock = threading.Lock()
_CHART_TTL = timedelta(hours=2)


def _next_chart_id() -> str:
    global _chart_counter
    with _chart_lock:
        _chart_counter += 1
        return f"chart_{_chart_counter}"


def _prune_chart_registry() -> None:
    """Remove expired charts. Must be called under _chart_lock."""
    now = datetime.now()
    expired = [
        k for k, v in _chart_registry.items()
        if now - v.get("created_at", now) > _CHART_TTL
    ]
    for k in expired:
        del _chart_registry[k]


# --- Report Cache ---
_REPORT_CACHE: dict[str, dict] = {}
_REPORT_LOCK = threading.Lock()
_REPORT_TTL = timedelta(hours=1)
_REPORT_MAX_ENTRIES = 20


def _prune_report_cache() -> None:
    """Remove expired/excess reports. Must be called under _REPORT_LOCK."""
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _REPORT_CACHE.items() if now > v["expires"]]
    for k in expired:
        del _REPORT_CACHE[k]
    # Evict oldest if still over limit
    while len(_REPORT_CACHE) > _REPORT_MAX_ENTRIES:
        oldest = min(_REPORT_CACHE, key=lambda k: _REPORT_CACHE[k]["expires"])
        del _REPORT_CACHE[oldest]


def get_chart_record(chart_id: str) -> dict | None:
    with _chart_lock:
        chart = _chart_registry.get(chart_id)
        return dict(chart) if chart else None


# --- Report Helpers ---


def _get_report_dir() -> Path:
    """Resolve and ensure the report directory exists."""
    d = Path(os.environ.get("CEREBRO_REPORT_DIR", "~/.cerebro/reports")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


_REPORT_FILENAME_GLOBS = (
    "cerebro_report_*.html",
    "cerebro_research_*.html",
    "cerebro_case_study_*.html",
)


def _report_filename(report_id: str, title: str, kind: str = "report") -> str:
    """Build a durable report filename.

    kind="report"     -> cerebro_report_<UTC>_<slug>_<full-id>.html
    kind="research"   -> cerebro_research_<UTC>_<slug>_<full-id>.html
    kind="case_study" -> cerebro_case_study_<UTC>_<slug>_<full-id>.html
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Slug: first 3 words of title, lowercased, non-alpha stripped, joined by hyphen
    words = re.sub(r"[^a-zA-Z0-9 ]", "", title).split()[:3]
    slug = "-".join(w.lower() for w in words) if words else kind
    if kind == "research":
        prefix = "cerebro_research"
    elif kind == "case_study":
        prefix = "cerebro_case_study"
    else:
        prefix = "cerebro_report"
    return f"{prefix}_{ts}_{slug}_{report_id}.html"


def _iter_report_files(report_dir: Path):
    """Yield every saved report file (dashboard + research) in report_dir."""
    if not report_dir.exists():
        return
    for pattern in _REPORT_FILENAME_GLOBS:
        yield from report_dir.glob(pattern)


def _get_report_link(path: Path) -> str:
    """Get the best available URL for a report file."""
    report_id = _extract_report_id_from_path(path)
    url = _get_report_download_url(report_id)
    if url:
        return url
    return path.resolve().as_uri()  # file:// fallback


def _find_report_on_disk(report_ref: str) -> Path | None:
    """Find a report file by full UUID or 8-char prefix."""
    report_dir = _get_report_dir()
    if not report_dir.exists():
        return None
    # Try exact match first (full UUID in filename) across all patterns
    for pattern in (
        f"cerebro_report_*_{report_ref}.html",
        f"cerebro_research_*_{report_ref}.html",
        f"cerebro_case_study_*_{report_ref}.html",
    ):
        for f in report_dir.glob(pattern):
            return f
    # Try 8-char prefix match
    matches = [f for f in _iter_report_files(report_dir) if report_ref in f.name]
    if len(matches) == 1:
        return matches[0]
    return None


def _extract_report_id_from_path(path: Path) -> str:
    """Extract the full report UUID from a filename."""
    # Format: cerebro_report_<ts>_<slug>_<uuid>.html
    #     or: cerebro_research_<ts>_<slug>_<uuid>.html
    name = path.stem  # drop .html
    parts = name.split("_")
    # UUID is the last part (may contain hyphens)
    if len(parts) >= 5:
        return parts[-1]
    return name


def _report_kind_from_path(path: Path) -> str:
    """Return 'research', 'case_study', or 'report' based on filename prefix."""
    if path.name.startswith("cerebro_research_"):
        return "research"
    if path.name.startswith("cerebro_case_study_"):
        return "case_study"
    return "report"


def _resolve_report(
    report_ref: str,
) -> tuple[str | None, str | None, Path | None]:
    """Resolve a report reference to (html, report_id, disk_path).

    Lookup order: in-memory cache (exact) -> cache (prefix) -> disk.
    Returns (None, None, None) if not found.
    Raises ValueError on ambiguous prefix.
    """
    if report_ref:
        # Exact cache match
        with _REPORT_LOCK:
            cached = _REPORT_CACHE.get(report_ref)
        if cached and cached.get("html"):
            return cached["html"], report_ref, cached.get("path")

        # Prefix match in cache
        with _REPORT_LOCK:
            prefix_matches = [
                (rid, data)
                for rid, data in _REPORT_CACHE.items()
                if rid.startswith(report_ref)
            ]
        if len(prefix_matches) == 1:
            rid, data = prefix_matches[0]
            return data["html"], rid, data.get("path")
        if len(prefix_matches) > 1:
            ids = ", ".join(f"`{rid[:8]}`" for rid, _ in prefix_matches)
            raise ValueError(
                f"Ambiguous report reference `{report_ref}`. Matches: {ids}"
            )

        # Disk fallback
        disk_path = _find_report_on_disk(report_ref)
        if disk_path:
            html = disk_path.read_text(encoding="utf-8")
            rid = _extract_report_id_from_path(disk_path)
            return html, rid, disk_path

        return None, None, None

    # No ref -> latest from cache or disk
    with _REPORT_LOCK:
        if _REPORT_CACHE:
            latest_id = max(
                _REPORT_CACHE,
                key=lambda k: _REPORT_CACHE[k].get(
                    "expires", datetime.min.replace(tzinfo=timezone.utc)
                ),
            )
            latest = _REPORT_CACHE[latest_id]
            if latest.get("html"):
                return latest["html"], latest_id, latest.get("path")

    # Disk: most recent file
    report_dir = _get_report_dir()
    if report_dir.exists():
        files = sorted(
            _iter_report_files(report_dir),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if files:
            html = files[0].read_text(encoding="utf-8")
            rid = _extract_report_id_from_path(files[0])
            return html, rid, files[0]

    return None, None, None


def _append_report_token(url: str) -> str:
    """Append ``?token=<MCP_AUTH_TOKEN>`` so the report route authorizes.

    The download route (``server.download_report``) accepts the shared token as
    either an ``Authorization: Bearer`` header or a ``?token=`` query param. A
    plain browser click on the report link carries no header, so without the
    query param the route answers 401 whenever ``MCP_AUTH_TOKEN`` is set. No-op
    when the token is unset.
    """
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}token={quote(token, safe='')}"


def _get_report_download_url(report_id: str) -> str | None:
    """Build the HTTP download URL for a report, or None if unavailable.

    Prefers the deployment's public ``REPORT_BASE_URL``. In SSE mode without a
    public base, an http link built from the bind host only resolves when the
    client shares this host (local dev); for a remote client (e.g. Claude
    Desktop via an ``mcp-remote`` bridge) it would be a dead link, so we return
    None and let ``_get_report_link`` fall back to a ``file://`` path — valid
    when the client shares the filesystem — while warning the operator to set
    ``REPORT_BASE_URL`` for reachable remote report links. Any URL we do return
    carries the auth token when ``MCP_AUTH_TOKEN`` is set.
    """
    from cerebro_mcp.config import settings

    if settings.REPORT_BASE_URL:
        return _append_report_token(
            f"{settings.REPORT_BASE_URL.rstrip('/')}/{report_id}"
        )

    # SSE mode exposes an HTTP server, but only a non-loopback bind host can be
    # reached by a remote client.
    if os.environ.get("CEREBRO_TRANSPORT") == "sse":
        host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        port = os.environ.get("FASTMCP_PORT", "8000")
        if host in ("0.0.0.0", "localhost", "127.0.0.1"):
            logger.warning(
                "Report link unavailable: SSE mode without REPORT_BASE_URL. "
                "Reports are not reachable by remote clients via the loopback "
                "bind host %r. Set REPORT_BASE_URL to the server's public "
                "reports base (e.g. https://host/reports) to enable links.",
                host,
            )
            return None
        return _append_report_token(
            f"http://{host}:{port}/reports/{report_id}"
        )

    return None


def _render_summary_numbers_html(summary_numbers: list[dict]) -> str:
    """Render a leading KPI strip from a list of {label, value, hint} dicts.

    Emits the Gnosis 3-up card layout: groups of three .kpi-cell wrapped in
    .report-kpi-strip blocks. The .kpi-table fallback CSS keeps any archived
    reports rendered with the legacy markup readable.
    """
    items = []
    for item in summary_numbers[:6]:
        label = str(item.get("label", "")).strip()
        value = str(item.get("value", "")).strip()
        hint = str(item.get("hint", "")).strip()
        if not label and not value:
            continue
        items.append((label, value, hint))
    if not items:
        return ""

    def _delta_class(text: str) -> str:
        t = text.strip()
        if not t:
            return "nd-delta flat"
        head = t.lstrip()
        if head.startswith(("-", "↓", "▼")):
            return "kpi-delta neg"
        if head.startswith(("+", "↑", "▲")):
            return "kpi-delta"
        if head.startswith(("→", "~", "≈")):
            return "kpi-delta flat"
        return "kpi-delta"

    def _render_strip(group: list[tuple[str, str, str]]) -> str:
        cells = []
        for label, value, hint in group:
            delta = (
                f'<span class="{_delta_class(hint)}">{hint}</span>' if hint else ""
            )
            cells.append(
                '<div class="kpi-cell">'
                f'<span class="kpi-label">{label}</span>'
                f'<span class="kpi-value">{value}</span>'
                f"{delta}"
                "</div>"
            )
        return f'<div class="report-kpi-strip">{"".join(cells)}</div>'

    blocks = [_render_strip(items[i : i + 3]) for i in range(0, len(items), 3)]
    return "".join(blocks)


def create_report_artifact(
    title: str,
    content_markdown: str,
    *,
    enforce_quality_gate: bool = True,
    reset_session_state: bool = True,
    presentation_mode: str | None = None,
    research_metadata: dict | None = None,
    case_study_metadata: dict | None = None,
    subtitle: str | None = None,
    summary_numbers: list[dict] | None = None,
    explain_context: bool = False,
) -> dict:
    from cerebro_mcp.tools.governance.session_state import state

    if presentation_mode is None:
        semantic_mode = state.semantic_summary().get("semantic_mode_last", "")
        presentation_mode = (
            "visual_answer" if semantic_mode in {"answer", "chart"} else "report"
        )
    research_mode = presentation_mode == "research"
    case_study_mode = presentation_mode == "scrollytelling"

    chart_ids_in_content = re.findall(
        r"\{\{chart:(\w+)\}\}",
        content_markdown,
    )
    if research_mode:
        # Research layout also uses {{figure:chart_id ...}}
        figure_ids = re.findall(
            r"\{\{figure:(\w+)(?:\s+[^}]*)?\}\}",
            content_markdown,
        )
        seen = set(chart_ids_in_content)
        for fid in figure_ids:
            if fid not in seen:
                chart_ids_in_content.append(fid)
                seen.add(fid)
    if case_study_mode:
        # Scrollytelling layout references charts via {{scene chart="..."}}
        # and {{step chart="..."}} attributes.
        scene_ids = re.findall(
            r'\{\{scene\b[^}]*?chart\s*=\s*"(\w+)"',
            content_markdown,
        )
        step_ids = re.findall(
            r'\{\{step\b[^}]*?chart\s*=\s*"(\w+)"',
            content_markdown,
        )
        seen = set(chart_ids_in_content)
        for cid in (*scene_ids, *step_ids):
            if cid not in seen:
                chart_ids_in_content.append(cid)
                seen.add(cid)

    if enforce_quality_gate:
        # Scope the gate to charts actually referenced by this report's
        # markdown. The global _chart_registry persists across sessions and
        # turns, so unreferenced legacy charts must not block a clean report.
        scoped_registry = {
            cid: _chart_registry[cid]
            for cid in chart_ids_in_content
            if cid in _chart_registry
        }
        passed, reason, _warnings = state.check_report_preconditions(
            scoped_registry
        )
        if not passed:
            raise ValueError(f"Report quality gate failed: {reason}")

    has_grid = "{{grid:" in content_markdown
    kpi_count = sum(
        1 for cid in chart_ids_in_content
        if _chart_registry.get(cid, {}).get("chart_type") == "numberDisplay"
    )
    if not has_grid and kpi_count >= 2:
        raise ValueError(
            f"Layout rejected: Found {kpi_count} KPI/counter charts but no "
            "{{grid:N}} directives. KPI counters must be grouped in a grid row."
        )

    chart_specs: dict = {}
    chart_queries: dict = {}
    missing = []
    for cid in chart_ids_in_content:
        if cid in _chart_registry:
            chart_specs[cid] = _chart_registry[cid]["option"]
            chart_queries[cid] = {
                "sql": _chart_registry[cid].get("sql", ""),
                "database": _chart_registry[cid].get("database", "dbt"),
                "title": _chart_registry[cid].get("title", ""),
                "source": _chart_registry[cid].get("source", "raw"),
            }
            source_model = _chart_registry[cid].get("source_model")
            if source_model:
                chart_queries[cid]["source_model"] = source_model
            rationale = _chart_registry[cid].get("rationale")
            if not rationale and explain_context:
                from cerebro_mcp.runtime.context_enrichment import (
                    build_sql_context_block,
                )

                chart_sql = _chart_registry[cid].get("sql", "")
                if chart_sql:
                    rationale = build_sql_context_block(chart_sql)
            if rationale:
                chart_queries[cid]["rationale"] = rationale
        else:
            missing.append(cid)

    if missing:
        raise ValueError(
            f"Chart IDs not found in registry: {', '.join(missing)}. "
            f"Available: {', '.join(_chart_registry.keys()) or 'none'}."
        )

    rendered_html = _markdown_to_html(
        content_markdown,
        research_mode=research_mode,
        case_study_mode=case_study_mode,
    )
    if summary_numbers and not research_mode and not case_study_mode:
        kpi_html = _render_summary_numbers_html(summary_numbers)
        if kpi_html:
            rendered_html = kpi_html + rendered_html
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    structured = {
        "title": title,
        "timestamp": timestamp,
        "presentation_mode": presentation_mode,
        "charts": chart_specs,
        "sections_html": rendered_html,
        "queries": chart_queries,
        "analysis_path": state.analysis_path,
    }
    if subtitle:
        structured["subtitle"] = subtitle
    if research_metadata:
        structured["research_metadata"] = research_metadata
    if case_study_metadata:
        structured["case_study_metadata"] = case_study_metadata

    html = _build_standalone_html(
        title,
        timestamp,
        chart_specs,
        rendered_html,
        chart_queries,
        presentation_mode=presentation_mode,
        research_metadata=research_metadata,
        case_study_metadata=case_study_metadata,
        subtitle=subtitle,
    )

    report_id = str(uuid.uuid4())
    report_dir = _get_report_dir()
    if research_mode:
        kind = "research"
    elif case_study_mode:
        kind = "case_study"
    else:
        kind = "report"
    report_path = report_dir / _report_filename(report_id, title, kind=kind)
    report_path.write_text(html, encoding="utf-8")

    file_uri = _get_report_link(report_path)
    structured["file_uri"] = file_uri

    with _REPORT_LOCK:
        _prune_report_cache()
        _REPORT_CACHE[report_id] = {
            "html": html,
            "structured": structured,
            "expires": datetime.now(timezone.utc) + _REPORT_TTL,
            "path": report_path,
            "title": title,
        }

    # Opt-in (REPORT_AUTO_OPEN, default off): pop the rendered artifact in the
    # user's default browser. Local stdio only — never on SSE (the browser
    # would open on the SERVER host). Fired on a daemon thread: on macOS
    # webbrowser.open() drives a blocking `osascript`/`open location` call, and
    # because sync MCP tools run inline on the single event loop, doing it
    # synchronously here would freeze the whole server (and time out every
    # concurrent tool) on a cold browser launch. Off-thread it can never block.
    from cerebro_mcp.config import settings as _settings
    if (
        _settings.REPORT_AUTO_OPEN
        and os.environ.get("CEREBRO_TRANSPORT", "stdio") != "sse"
    ):
        _open_in_browser_async(report_path.resolve().as_uri())

    # Telemetry: record time-to-first-report on the first successful
    # report of a session, retry counter on every subsequent call.
    # Done before reset_session_state so it captures the cycle that
    # actually produced the report.
    try:
        from cerebro_mcp.tools.governance.reasoning import record_report_generation
        if research_mode:
            _report_kind = "research"
        elif case_study_mode:
            _report_kind = "case_study"
        else:
            _report_kind = "report"
        record_report_generation(_report_kind)
    except Exception:
        # Telemetry must never break a successful report.
        pass

    if reset_session_state:
        state.reset()

    link_line = f"\n\n[Open Report]({file_uri})" if file_uri else ""

    if presentation_mode == "visual_answer":
        reply_text = (
            f"**Visualization:** {title}{link_line}\n\n"
            f"View ID: `{report_id[:8]}` | "
            f"Charts: {len(chart_specs)}\n\n"
            f"To reopen: `open_report(\"{report_id[:8]}\")`"
        )
    elif presentation_mode == "research":
        reply_text = (
            f"**Research report:** {title}{link_line}\n\n"
            f"Report ID: `{report_id[:8]}` | "
            f"Charts: {len(chart_specs)}\n\n"
            f"To reopen: `open_report(\"{report_id[:8]}\")`\n"
            f"To export HTML: `export_report(\"{report_id[:8]}\")`\n\n"
            f"_Summarize the headline finding in 2–3 sentences, then offer docx/pdf conversion._"
        )
    elif presentation_mode == "scrollytelling":
        reply_text = (
            f"**Case study:** {title}{link_line}\n\n"
            f"Report ID: `{report_id[:8]}` | "
            f"Charts: {len(chart_specs)}\n\n"
            f"To reopen: `open_report(\"{report_id[:8]}\")`\n"
            f"To export HTML: `export_report(\"{report_id[:8]}\")`\n\n"
            f"_Summarize the headline narrative in 2–3 sentences and offer docx/pdf conversion._"
        )
    else:
        reply_text = (
            f"**Report:** {title}{link_line}\n\n"
            f"Report ID: `{report_id[:8]}` | "
            f"Charts: {len(chart_specs)}\n\n"
            f"To reopen: `open_report(\"{report_id[:8]}\")`\n"
            f"To export HTML: `export_report(\"{report_id[:8]}\")`\n\n"
            f"_Ask if they want the HTML exported or conversion to docx/pdf/pptx._"
        )

    return {
        "report_id": report_id,
        "report_path": report_path,
        "file_uri": file_uri,
        "structured": structured,
        "reply_text": reply_text,
        "chart_count": len(chart_specs),
    }


def _report_instance_uri(report_id: str) -> str:
    """Per-report UI resource URI serving standalone HTML with EMBEDDED data.

    Unlike the static ``ui://cerebro/report`` bundle (which waits for the
    ext-apps handshake to receive its data), the per-report resource returns
    the same self-contained HTML that is written to disk — the React app reads
    the embedded ``<script id="report-data">`` blob and renders with zero
    handshake. Attached to tool results as result-level ``_meta`` so hosts
    that honor per-call UI metadata can render inline in any client.
    """
    return f"{REPORT_URI}/{report_id}"


def _result_ui_meta(report_id: str) -> dict | None:
    """Result-level MCP-UI meta pointing at the per-report resource.

    Returns None when ``MCP_UI_INLINE_ENABLED`` is off (the default) so the
    host does not try to mount the native report iframe — Claude Desktop /
    claude.ai currently negotiate the protocol but never create the iframe
    (ext-apps #671), which would leave a blank/"couldn't load" panel. With it
    off, chart/answer results deliver model-rendered inline charts + a link.
    """
    from cerebro_mcp.config import settings
    if not settings.MCP_UI_INLINE_ENABLED:
        return None
    return {"ui": {"resourceUri": _report_instance_uri(report_id)}}


def _ui_tool_meta(resource_uri: str) -> dict | None:
    """Tool-level MCP-UI meta, or None when inline UI is disabled.

    Evaluated at tool-registration time. When off (default) the tool carries no
    UI resource pointer, so hosts render nothing server-side and the chart
    tools' model-inline payload is the deliverable. See ``_result_ui_meta``.
    """
    from cerebro_mcp.config import settings
    if not settings.MCP_UI_INLINE_ENABLED:
        return None
    return {"ui": {"resourceUri": resource_uri}, "ui/resourceUri": resource_uri}


def _chart_mode_active() -> bool:
    """True when the request was routed as a chart/answer (light) tier.

    In these tiers `generate_report` is hard-blocked, so the chart tools
    themselves must deliver the rendered visualization.
    """
    return state.semantic_summary().get("semantic_mode_last", "") in {
        "answer",
        "chart",
    }


def _model_inline_block(chart_ids: list[str]) -> str:
    """Everything the model needs to render the charts INLINE itself.

    Model-authored inline visuals are the only inline path that works in
    Claude Desktop today (server MCP-UI iframes don't mount — ext-apps #671),
    so instead of relying on the panel we hand the assistant the per-chart data
    + SQL + the cerebro palette and tell it to draw the charts in its reply.
    """
    charts: list[dict] = []
    with _chart_lock:
        for cid in chart_ids:
            entry = _chart_registry.get(cid)
            if not entry:
                continue
            option = entry.get("option", {}) or {}
            xaxis = option.get("xAxis")
            x = None
            if isinstance(xaxis, dict):
                x = xaxis.get("data")
            elif isinstance(xaxis, list) and xaxis and isinstance(xaxis[0], dict):
                x = xaxis[0].get("data")
            series = [
                {"name": s.get("name"), "type": s.get("type"), "data": s.get("data")}
                for s in (option.get("series") or [])
                if isinstance(s, dict)
            ]
            charts.append({
                "chart_id": cid,
                "title": entry.get("title"),
                "chart_type": entry.get("chart_type"),
                "x": x,
                "series": series,
                "sql": entry.get("sql", ""),
                "source_model": entry.get("source_model"),
            })

    payload = {
        "palette_dark": CEREBRO_CHART_PALETTE_DARK,
        "palette_light": CEREBRO_CHART_PALETTE_LIGHT,
        "font": CEREBRO_CHART_FONT,
        "charts": charts,
    }
    instructions = (
        "RENDER THESE CHARTS INLINE in your reply now — draw them yourself as an "
        "inline visualization (this client renders model-authored visuals "
        "inline; it does not mount a server UI panel). One chart per spec, in "
        "order, matching the cerebro look: use the palette colors (one per "
        "series) and a monospace font. Beneath each chart put its `sql` in a "
        "collapsed/expandable block labeled with the `source_model`. Keep prose "
        "to a one-line takeaway per chart. Then STOP — do not call "
        "`generate_report`. A full-fidelity native report is also linked below."
    )
    return f"{instructions}\n\n```json\n{json.dumps(payload, default=str)}\n```"


def _render_visual_answer(chart_ids: list[str], title: str) -> dict:
    """Render registered charts as a lightweight visual-answer artifact.

    Internal to the chart tools: builds the ``{{chart:ID}}`` markdown,
    bypasses the report-quality gate (this is NOT a report), and preserves
    session state so a follow-up "now make that a report" still has the
    charts and discovery evidence available.
    """
    content_markdown = "\n\n".join(f"{{{{chart:{cid}}}}}" for cid in chart_ids)
    report = create_report_artifact(
        title or "Visualization",
        content_markdown,
        enforce_quality_gate=False,
        reset_session_state=False,
        presentation_mode="visual_answer",
    )
    with _REPORT_LOCK:
        _LAST_VISUAL["report_id"] = report["report_id"]
        _LAST_VISUAL["created_at"] = datetime.now(timezone.utc)
    return report


def _estimate_reading_minutes(text: str) -> int:
    """Rough reading-time estimate at ~220 words/min, minimum 1 minute."""
    word_count = len(re.findall(r"\b\w+\b", text))
    return max(1, round(word_count / 220))


def create_research_report_artifact(
    title: str,
    deck: str,
    content_markdown: str,
    *,
    authors: list[str] | None = None,
    published_date: str | None = None,
    category: str | None = None,
    key_takeaways: list[str] | None = None,
    footnotes: list[dict] | None = None,
    enforce_quality_gate: bool = True,
    reset_session_state: bool = True,
) -> dict:
    """Build a long-form research-essay report (Anthropic-style layout).

    Reuses create_report_artifact's chart registry and gate logic, but emits
    presentation_mode="research" and carries essay metadata so the React layer
    can render the editorial layout (header deck, TOC, figures, footnotes).
    """
    deck = (deck or "").strip()
    if not deck:
        raise ValueError("Research reports require a non-empty `deck` (sub-headline).")
    if len(deck) > 240:
        raise ValueError(
            f"Deck is too long ({len(deck)} chars). Keep it under 240 characters."
        )

    takeaways = [t.strip() for t in (key_takeaways or []) if t and t.strip()]
    if not 3 <= len(takeaways) <= 6:
        raise ValueError(
            "Research reports require 3–6 `key_takeaways` items "
            f"(got {len(takeaways)})."
        )

    authors = [a.strip() for a in (authors or []) if a and a.strip()]
    published = published_date or date.today().isoformat()
    reading_minutes = _estimate_reading_minutes(content_markdown)

    # Normalize footnotes to [{id, text}] with stable ids
    normalized_footnotes: list[dict] = []
    for idx, fn in enumerate(footnotes or [], start=1):
        if isinstance(fn, str):
            normalized_footnotes.append({"id": str(idx), "text": fn})
        elif isinstance(fn, dict):
            fid = str(fn.get("id") or idx)
            text_val = str(fn.get("text") or "").strip()
            if text_val:
                normalized_footnotes.append({"id": fid, "text": text_val})

    research_metadata = {
        "deck": deck,
        "authors": authors,
        "published_date": published,
        "category": (category or "").strip() or None,
        "key_takeaways": takeaways,
        "footnotes": normalized_footnotes,
        "reading_minutes": reading_minutes,
    }

    return create_report_artifact(
        title,
        content_markdown,
        enforce_quality_gate=enforce_quality_gate,
        reset_session_state=reset_session_state,
        presentation_mode="research",
        research_metadata=research_metadata,
    )


def create_case_study_artifact(
    title: str,
    deck: str,
    content_markdown: str,
    *,
    hero_image: str | None = None,
    hero_chart_id: str | None = None,
    authors: list[str] | None = None,
    published_date: str | None = None,
    category: str | None = None,
    cta: dict | None = None,
    key_points: list[str] | None = None,
    enforce_quality_gate: bool = True,
    reset_session_state: bool = True,
) -> dict:
    """Build a scrollytelling case-study report (Anthropic customer-story style).

    Reuses the same chart registry and quality gates as `create_report_artifact`
    but emits `presentation_mode="scrollytelling"` with case-study metadata so
    the React layer renders the sticky-visual + scrolling-narrative layout.
    """
    deck = (deck or "").strip()
    if not deck:
        raise ValueError("Case studies require a non-empty `deck` (sub-headline).")
    if len(deck) > 240:
        raise ValueError(
            f"Deck is too long ({len(deck)} chars). Keep it under 240 characters."
        )

    points = [p.strip() for p in (key_points or []) if p and p.strip()]
    if not 3 <= len(points) <= 6:
        raise ValueError(
            "Case studies require 3–6 `key_points` items "
            f"(got {len(points)})."
        )

    authors = [a.strip() for a in (authors or []) if a and a.strip()]
    published = published_date or date.today().isoformat()
    reading_minutes = _estimate_reading_minutes(content_markdown)

    cta_clean: dict | None = None
    if cta:
        label = str(cta.get("label") or "").strip()
        href = str(cta.get("href") or "").strip()
        if label and href:
            cta_clean = {"label": label, "href": href}

    case_study_metadata = {
        "deck": deck,
        "authors": authors,
        "published_date": published,
        "category": (category or "").strip() or None,
        "hero_image": (hero_image or "").strip() or None,
        "hero_chart_id": (hero_chart_id or "").strip() or None,
        "cta": cta_clean,
        "key_points": points,
        "reading_minutes": reading_minutes,
    }

    return create_report_artifact(
        title,
        content_markdown,
        enforce_quality_gate=enforce_quality_gate,
        reset_session_state=reset_session_state,
        presentation_mode="scrollytelling",
        case_study_metadata=case_study_metadata,
    )


def _extract_structured_from_html(html: str) -> dict | None:
    """Try to extract embedded report data from standalone HTML."""
    match = re.search(
        r'<script\s+id="report-data"\s+type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _serialize_value(val):
    """Convert ClickHouse values to JSON-serializable types."""
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


def _build_col_index(columns: list[str]) -> dict[str, int]:
    """Map column names to their indices."""
    return {name: i for i, name in enumerate(columns)}


def _extract_column(rows: list, index: int) -> list:
    """Extract a column from rows and serialize values."""
    return [_serialize_value(row[index]) for row in rows]


# Epoch-day offset: date.toordinal() for 1970-01-01
_EPOCH_ORDINAL = 719163


def _auto_format_dates(values: list) -> list:
    """Detect epoch-day or Unix-timestamp integers and convert to ISO dates.

    Handles two common ClickHouse patterns:
    - Date columns stored as UInt16 (epoch days since 1970-01-01): range ~17000-25000
    - DateTime columns returned as Unix timestamps: range ~1.4e9-2.2e9
    """
    if not values:
        return values
    nums = [v for v in values if isinstance(v, (int, float)) and v > 0]
    if len(nums) < len(values) * 0.8:
        return values  # not predominantly numeric

    sample = nums[:min(10, len(nums))]
    # Epoch days: ~17000 (2016-07) to ~25000 (2038-06)
    if all(17000 <= v <= 25000 for v in sample):
        return [
            date.fromordinal(int(v) + _EPOCH_ORDINAL).isoformat()
            if isinstance(v, (int, float))
            else v
            for v in values
        ]
    # Unix timestamps: ~1.4e9 (2014-05) to ~2.2e9 (2039-09)
    if all(1_400_000_000 <= v <= 2_200_000_000 for v in sample):
        return [
            datetime.utcfromtimestamp(int(v)).strftime("%Y-%m-%d")
            if isinstance(v, (int, float))
            else v
            for v in values
        ]
    return values


def _first_non_null_value(rows: list, index: int):
    """Return the first non-null value observed in a column."""
    for row in rows:
        if index >= len(row):
            continue
        value = row[index]
        if value is not None:
            return value
    return None


def _is_numeric_value(value) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool)


def _numeric_value_columns(
    columns: list[str],
    rows: list,
    *,
    exclude_fields: set[str],
) -> list[str]:
    """Infer numeric value columns from the returned result set."""
    numeric_columns: list[str] = []
    for index, name in enumerate(columns):
        if name in exclude_fields:
            continue
        sample = _first_non_null_value(rows, index)
        if _is_numeric_value(sample):
            numeric_columns.append(name)
    return numeric_columns


def _classify_change_direction(value) -> str:
    """Infer change direction from a numeric or signed string value."""
    serialized = _serialize_value(value)
    if _is_numeric_value(serialized):
        if serialized > 0:
            return "positive"
        if serialized < 0:
            return "negative"
        return "neutral"

    text = str(serialized).strip()
    if text.startswith("+"):
        return "positive"
    if text.startswith("-"):
        return "negative"
    return "neutral"


def _format_number_display_scalar(value, *, signed: bool = False) -> str:
    """Format scalar KPI values consistently for inline HTML rendering."""
    serialized = _serialize_value(value)
    if _is_numeric_value(serialized):
        if isinstance(serialized, float) and not serialized.is_integer():
            formatted = f"{serialized:,.2f}".rstrip("0").rstrip(".")
        else:
            formatted = f"{serialized:,.0f}"
        if signed and serialized > 0:
            return f"+{formatted}"
        return formatted
    return str(serialized)


def _resolve_number_display_fields(
    *,
    columns: list[str],
    rows: list,
    x_field: str,
    y_field: str,
    change_field: str,
    series_field: str,
) -> tuple[str, str, str]:
    """Resolve explicit value/change fields for KPI cards without guessing."""
    resolved_y = y_field.strip()
    resolved_change = change_field.strip()

    if resolved_y and "," in resolved_y:
        return "", "", (
            "Error: `numberDisplay` requires a single main KPI column. "
            "Use one `y_field` and optionally one `change_field`."
        )
    if resolved_change and "," in resolved_change:
        return "", "", (
            "Error: `numberDisplay` supports at most one `change_field`. "
            "Pass a single explicit delta column."
        )
    if resolved_change and resolved_change == resolved_y:
        return "", "", (
            "Error: `change_field` must differ from `y_field` for `numberDisplay`."
        )

    candidate_fields = [
        name for name in columns
        if name not in {x_field, series_field, resolved_change}
    ]
    numeric_columns = _numeric_value_columns(
        columns,
        rows,
        exclude_fields={x_field, series_field},
    )
    numeric_candidates = [
        name for name in numeric_columns
        if name != resolved_change
    ]

    if not resolved_y:
        if len(columns) == 1:
            resolved_y = columns[0]
        elif len(candidate_fields) == 1:
            resolved_y = candidate_fields[0]
        elif len(numeric_candidates) == 1 and len(candidate_fields) <= 2:
            resolved_y = numeric_candidates[0]
        else:
            available = ", ".join(columns)
            return "", "", (
                "Error: `numberDisplay` requires an explicit main KPI column when "
                "the query returns multiple fields. Set `y_field=\"...\"` for the "
                "main value and optionally `change_field=\"...\"` for the delta, "
                f"or reduce the query to a single KPI column. Available columns: {available}"
            )

    extra_numeric = [
        name for name in numeric_columns
        if name not in {resolved_y, resolved_change}
    ]
    if extra_numeric:
        available = ", ".join(numeric_columns)
        if resolved_change:
            extra = ", ".join(f"`{name}`" for name in extra_numeric)
            return "", "", (
                "Error: `numberDisplay` found extra numeric columns beyond the explicit "
                f"`y_field=\"{resolved_y}\"` and `change_field=\"{resolved_change}\"`: {extra}. "
                f"Available numeric columns: {available}. Reduce the query or choose a single value/change pair."
            )
        return "", "", (
            "Error: `numberDisplay` found multiple numeric columns "
            f"({available}) but no `change_field` was provided. Set `y_field=\"{resolved_y}\"` "
            "for the main KPI and `change_field=\"...\"` for the delta, or reduce the query "
            "to a single KPI column."
        )

    return resolved_y, resolved_change, ""


def _validate_chart_input_shape(
    *,
    chart_type: str,
    columns: list[str],
    rows: list,
    x_field: str,
    y_field: str,
    change_field: str,
    series_field: str,
) -> tuple[str | None, str]:
    """Validate chart/query contracts and classify the input shape."""
    if chart_type == "numberDisplay":
        if series_field:
            return (
                "Error: `numberDisplay` does not support `series_field`. "
                "Use a single-row query that returns exactly one KPI value.",
                "",
            )
        if "," in y_field:
            return (
                "Error: `numberDisplay` requires a single metric column. "
                "Use one `y_field` and return exactly one row.",
                "",
            )
        if change_field and "," in change_field:
            return (
                "Error: `numberDisplay` supports a single explicit `change_field`. "
                "Pass one delta column only.",
                "",
            )
        if len(rows) != 1:
            latest_hint = (
                f" For latest-period KPIs, use `ORDER BY {x_field} DESC LIMIT 1`."
                if x_field
                else ""
            )
            return (
                "Error: `numberDisplay` charts require a single-row query, "
                f"but this query returned {len(rows)} rows."
                f"{latest_hint} For aggregate KPIs, return one row with "
                "`sum(...)`, `count(...)`, or similar.",
                "",
            )
        return None, "scalar_kpi_input"

    if chart_type in {"line", "area", "bar"}:
        if series_field:
            return None, "long_format_series_input"

        y_fields = [field.strip() for field in y_field.split(",") if field.strip()]
        if len(y_fields) > 1:
            return None, "multi_series_wide_input"

        numeric_columns = _numeric_value_columns(
            columns,
            rows,
            exclude_fields={x_field, series_field},
        )
        extra_numeric = [
            name for name in numeric_columns if name not in set(y_fields)
        ]
        if extra_numeric and len(y_fields) == 1:
            available = ",".join(numeric_columns)
            x_example = x_field or "x"
            return (
                f"Error: `{chart_type}` chart queries do not auto-plot extra "
                "numeric columns. This query returned multiple numeric value "
                f"columns ({', '.join(f'`{name}`' for name in numeric_columns)}) "
                f"but the chart spec only selected `y_field=\"{y_fields[0]}\"` "
                "and no `series_field`.\n\n"
                f"Use `y_field=\"{available}\"` for a wide multi-series chart, "
                f"or reshape the query to long form like `({x_example}, series, value)` "
                "and set `series_field`.",
                "",
            )
        return None, "single_series_trend_input"

    return None, "other_chart_input"


def _build_line_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
    area: bool = False,
) -> dict:
    """Build ECharts option for line/area charts."""
    x_idx = col_index[x_field]

    # Dual y-axis: comma-separated y_field (e.g., "transactions,gas_price")
    y_fields = [f.strip() for f in y_field.split(",")]
    if len(y_fields) > 1:
        rows_sorted = sorted(rows, key=lambda r: r[x_idx])
        x_values = _auto_format_dates(_extract_column(rows_sorted, x_idx))
        series_list = []
        for i, yf in enumerate(y_fields):
            yi = col_index[yf]
            s: dict = {
                "name": yf,
                "type": "line",
                "data": _extract_column(rows_sorted, yi),
                "smooth": True,
                "symbolSize": 2,
            }
            if i > 0:
                s["yAxisIndex"] = 1
            if area:
                s["areaStyle"] = {"opacity": 0.3}
            series_list.append(s)
        return {
            "title": {},
            "tooltip": {"trigger": "axis"},
            "legend": {"data": y_fields, "top": 0, "type": "scroll"},
            "grid": {"left": "3%", "right": "6%", "bottom": "10%", "top": "40", "containLabel": True},
            "xAxis": {"type": "category", "data": x_values, "boundaryGap": False},
            "yAxis": [{"type": "value"}, {"type": "value"}],
            "series": series_list,
        }

    y_idx = col_index[y_field]

    if series_field and series_field in col_index:
        series_idx = col_index[series_field]
        # Group by series
        series_data: dict[str, dict] = {}
        x_values_set: list[str] = []
        for row in rows:
            x_val = _serialize_value(row[x_idx])
            s_val = str(_serialize_value(row[series_idx]))
            y_val = _serialize_value(row[y_idx])
            if x_val not in x_values_set:
                x_values_set.append(x_val)
            if s_val not in series_data:
                series_data[s_val] = {}
            series_data[s_val][x_val] = y_val

        series_list = []
        for s_name, data_map in series_data.items():
            s = {
                "name": s_name,
                "type": "line",
                "data": [data_map.get(x, None) for x in x_values_set],
                "smooth": True,
                "symbolSize": 2,
            }
            if area:
                s["areaStyle"] = {"opacity": 0.3}
            series_list.append(s)

        legend_data = list(series_data.keys())
        # Sort x-axis chronologically (ISO dates sort correctly as strings)
        x_values = _auto_format_dates(sorted(x_values_set))
    else:
        # Sort rows by x_field for chronological ordering
        rows = sorted(rows, key=lambda r: r[x_idx])
        x_values = _auto_format_dates(_extract_column(rows, x_idx))
        y_values = _extract_column(rows, y_idx)
        s = {
            "name": y_field,
            "type": "line",
            "data": y_values,
            "smooth": True,
            "symbolSize": 2,
        }
        if area:
            s["areaStyle"] = {"opacity": 0.3}
        series_list = [s]
        legend_data = [y_field]

    return {
        "title": {},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend_data, "top": 0, "type": "scroll"},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "top": "40", "containLabel": True},
        "xAxis": {"type": "category", "data": x_values, "boundaryGap": False},
        "yAxis": {"type": "value"},
        "series": series_list,
    }


def _build_bar_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for bar charts."""
    x_idx = col_index[x_field]

    # Dual y-axis: comma-separated y_field
    y_fields = [f.strip() for f in y_field.split(",")]
    if len(y_fields) > 1:
        x_values = _auto_format_dates(_extract_column(rows, x_idx))
        series_list = []
        for i, yf in enumerate(y_fields):
            yi = col_index[yf]
            s: dict = {"name": yf, "type": "bar", "data": _extract_column(rows, yi)}
            if i > 0:
                s["yAxisIndex"] = 1
            series_list.append(s)
        return {
            "title": {},
            "tooltip": {"trigger": "axis"},
            "legend": {"data": y_fields, "top": 0, "type": "scroll"},
            "grid": {"left": "3%", "right": "6%", "bottom": "10%", "top": "40", "containLabel": True},
            "xAxis": {"type": "category", "data": x_values},
            "yAxis": [{"type": "value"}, {"type": "value"}],
            "series": series_list,
        }

    y_idx = col_index[y_field]

    if series_field and series_field in col_index:
        series_idx = col_index[series_field]
        series_data: dict[str, dict] = {}
        x_values_set: list[str] = []
        for row in rows:
            x_val = _serialize_value(row[x_idx])
            s_val = str(_serialize_value(row[series_idx]))
            y_val = _serialize_value(row[y_idx])
            if x_val not in x_values_set:
                x_values_set.append(x_val)
            if s_val not in series_data:
                series_data[s_val] = {}
            series_data[s_val][x_val] = y_val

        series_list = [
            {
                "name": s_name,
                "type": "bar",
                "data": [data_map.get(x, None) for x in x_values_set],
            }
            for s_name, data_map in series_data.items()
        ]
        legend_data = list(series_data.keys())
        x_values = _auto_format_dates(x_values_set)
    else:
        x_values = _auto_format_dates(_extract_column(rows, x_idx))
        y_values = _extract_column(rows, y_idx)
        series_list = [{"name": y_field, "type": "bar", "data": y_values}]
        legend_data = [y_field]

    return {
        "title": {},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend_data, "top": 0, "type": "scroll"},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "top": "40", "containLabel": True},
        "xAxis": {"type": "category", "data": x_values},
        "yAxis": {"type": "value"},
        "series": series_list,
    }


def _build_pie_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    title: str,
) -> dict:
    """Build ECharts option for pie charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = [
        {"name": _serialize_value(row[x_idx]), "value": _serialize_value(row[y_idx])}
        for row in rows
    ]

    return {
        "title": {},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"orient": "vertical", "left": "left", "top": 0, "type": "scroll"},
        "series": [
            {
                "type": "pie",
                "radius": "60%",
                "data": data,
                "emphasis": {"itemStyle": {"shadowBlur": 10}},
            }
        ],
    }


def _build_number_display(
    rows: list,
    col_index: dict[str, int],
    y_field: str,
    title: str,
    change_field: str = "",
) -> dict:
    """Build a KPI number display spec."""
    y_idx = col_index[y_field]
    value = _serialize_value(rows[0][y_idx]) if rows else 0

    option = {
        "type": "numberDisplay",
        "title": title,
        "value": value,
    }
    if _is_numeric_value(value):
        option["format"] = "formatNumber"

    if change_field:
        change_idx = col_index[change_field]
        change_value = _serialize_value(rows[0][change_idx]) if rows else 0
        change_payload = {
            "value": change_value,
            "direction": _classify_change_direction(change_value),
        }
        if _is_numeric_value(change_value):
            change_payload["format"] = "formatNumber"
        option["change"] = change_payload

    return option


def _build_scatter_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for scatter charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    if series_field and series_field in col_index:
        series_idx = col_index[series_field]
        series_data: dict[str, list] = {}
        for row in rows:
            s_val = str(_serialize_value(row[series_idx]))
            if s_val not in series_data:
                series_data[s_val] = []
            series_data[s_val].append([
                _serialize_value(row[x_idx]),
                _serialize_value(row[y_idx]),
            ])
        series_list = [
            {"name": s_name, "type": "scatter", "data": data, "symbolSize": 6}
            for s_name, data in series_data.items()
        ]
        legend_data = list(series_data.keys())
    else:
        data = [
            [_serialize_value(row[x_idx]), _serialize_value(row[y_idx])]
            for row in rows
        ]
        series_list = [{"name": y_field, "type": "scatter", "data": data, "symbolSize": 6}]
        legend_data = [y_field]

    return {
        "title": {},
        "tooltip": {"trigger": "item"},
        "legend": {"data": legend_data, "top": 0, "type": "scroll"},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "top": "40", "containLabel": True},
        "xAxis": {"type": "value", "name": x_field},
        "yAxis": {"type": "value", "name": y_field},
        "series": series_list,
    }


def _build_heatmap_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for grid heatmap charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]
    # Value comes from series_field or the 3rd column
    if series_field and series_field in col_index:
        v_idx = col_index[series_field]
    else:
        remaining = [i for i in range(len(col_index)) if i != x_idx and i != y_idx]
        v_idx = remaining[0] if remaining else y_idx

    x_cats = list(dict.fromkeys(_serialize_value(row[x_idx]) for row in rows))
    y_cats = list(dict.fromkeys(_serialize_value(row[y_idx]) for row in rows))
    x_map = {v: i for i, v in enumerate(x_cats)}
    y_map = {v: i for i, v in enumerate(y_cats)}

    data = []
    values = []
    for row in rows:
        xv = _serialize_value(row[x_idx])
        yv = _serialize_value(row[y_idx])
        val = _serialize_value(row[v_idx])
        data.append([x_map[xv], y_map[yv], val])
        if isinstance(val, (int, float)):
            values.append(val)

    return {
        "title": {},
        "tooltip": {"position": "top"},
        "grid": {"left": "3%", "right": "4%", "bottom": "15%", "top": "10%", "containLabel": True},
        "xAxis": {"type": "category", "data": [str(c) for c in x_cats], "splitArea": {"show": True}},
        "yAxis": {"type": "category", "data": [str(c) for c in y_cats], "splitArea": {"show": True}},
        "visualMap": {
            "min": min(values) if values else 0,
            "max": max(values) if values else 1,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": "0%",
        },
        "series": [{"type": "heatmap", "data": data, "label": {"show": True}}],
        "_cerebro_height": "400px",
    }


def _build_calendar_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for calendar heatmap charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = []
    values = []
    dates = []
    for row in rows:
        d = str(_serialize_value(row[x_idx]))[:10]  # YYYY-MM-DD
        val = _serialize_value(row[y_idx])
        data.append([d, val])
        dates.append(d)
        if isinstance(val, (int, float)):
            values.append(val)

    if not dates:
        return {"title": {"text": "No data"}}

    date_min = min(dates)
    date_max = max(dates)
    # Calculate number of years for height
    year_min = int(date_min[:4])
    year_max = int(date_max[:4])
    num_years = max(1, year_max - year_min + 1)
    height = f"{180 * num_years}px"

    calendars = []
    series_list = []
    for i, year in enumerate(range(year_min, year_max + 1)):
        calendars.append({
            "top": 60 + i * 160,
            "range": str(year),
            "cellSize": ["auto", 15],
            "left": 80,
            "right": 30,
        })
        year_data = [d for d in data if d[0].startswith(str(year))]
        series_list.append({
            "type": "heatmap",
            "coordinateSystem": "calendar",
            "calendarIndex": i,
            "data": year_data,
        })

    return {
        "tooltip": {"position": "top", "formatter": "{c}"},
        "visualMap": {
            "min": min(values) if values else 0,
            "max": max(values) if values else 1,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "top": 0,
        },
        "calendar": calendars,
        "series": series_list,
        "_cerebro_height": height,
    }


def _build_gauge_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for gauge charts."""
    y_idx = col_index[y_field]
    value = _serialize_value(rows[0][y_idx]) if rows else 0

    return {
        "tooltip": {"formatter": "{b}: {c}"},
        "series": [{
            "type": "gauge",
            "data": [{"value": value, "name": title or y_field}],
            "detail": {"formatter": "{value}"},
            "title": {"fontSize": 14},
        }],
        "_cerebro_height": "250px",
    }


def _build_treemap_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for treemap charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = [
        {"name": str(_serialize_value(row[x_idx])), "value": _serialize_value(row[y_idx])}
        for row in rows
    ]

    return {
        "tooltip": {"formatter": "{b}: {c}"},
        "series": [{
            "type": "treemap",
            "data": data,
            "label": {"show": True, "formatter": "{b}"},
            "breadcrumb": {"show": False},
        }],
    }


def _build_sankey_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for sankey flow diagrams."""
    src_idx = col_index[x_field]
    tgt_idx = col_index[y_field]
    val_idx = col_index[series_field] if series_field and series_field in col_index else None

    nodes_set: set[str] = set()
    links = []
    for row in rows:
        src = str(_serialize_value(row[src_idx]))
        tgt = str(_serialize_value(row[tgt_idx]))
        val = _serialize_value(row[val_idx]) if val_idx is not None else 1
        nodes_set.add(src)
        nodes_set.add(tgt)
        links.append({"source": src, "target": tgt, "value": val})

    return {
        "tooltip": {"trigger": "item"},
        "series": [{
            "type": "sankey",
            "data": [{"name": n} for n in sorted(nodes_set)],
            "links": links,
            "emphasis": {"focus": "adjacency"},
            "lineStyle": {"color": "gradient", "curveness": 0.5},
        }],
        "_cerebro_height": "450px",
    }


def _build_graph_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for force-directed graph charts."""
    src_idx = col_index[x_field]
    tgt_idx = col_index[y_field]
    val_idx = col_index[series_field] if series_field and series_field in col_index else None

    degree: dict[str, int] = {}
    links = []
    for row in rows:
        src = str(_serialize_value(row[src_idx]))
        tgt = str(_serialize_value(row[tgt_idx]))
        val = _serialize_value(row[val_idx]) if val_idx is not None else 1
        degree[src] = degree.get(src, 0) + 1
        degree[tgt] = degree.get(tgt, 0) + 1
        links.append({"source": src, "target": tgt, "value": val})

    max_deg = max(degree.values(), default=1)
    nodes = [
        {"name": n, "symbolSize": 10 + 30 * (d / max_deg)}
        for n, d in degree.items()
    ]

    return {
        "tooltip": {},
        "series": [{
            "type": "graph",
            "layout": "force",
            "data": nodes,
            "links": links,
            "roam": True,
            "label": {"show": True, "position": "right", "fontSize": 10},
            "force": {"repulsion": 200, "edgeLength": [50, 200]},
            "emphasis": {"focus": "adjacency"},
            "lineStyle": {"opacity": 0.6},
        }],
        "_cerebro_height": "500px",
    }


def _build_funnel_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for funnel charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = sorted(
        [
            {"name": str(_serialize_value(row[x_idx])), "value": _serialize_value(row[y_idx])}
            for row in rows
        ],
        key=lambda d: d["value"] if isinstance(d["value"], (int, float)) else 0,
        reverse=True,
    )

    return {
        "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
        "legend": {"data": [d["name"] for d in data], "top": 0, "type": "scroll"},
        "series": [{
            "type": "funnel",
            "left": "10%",
            "width": "80%",
            "top": 40,
            "bottom": 20,
            "data": data,
            "label": {"show": True, "position": "inside"},
            "emphasis": {"label": {"fontSize": 14}},
        }],
    }


CHART_BUILDERS = {
    "line": lambda rows, ci, xf, yf, sf, t: _build_line_chart(rows, ci, xf, yf, sf, t, area=False),
    "area": lambda rows, ci, xf, yf, sf, t: _build_line_chart(rows, ci, xf, yf, sf, t, area=True),
    "bar": _build_bar_chart,
    "pie": lambda rows, ci, xf, yf, sf, t: _build_pie_chart(rows, ci, xf, yf, t),
    "numberDisplay": lambda rows, ci, xf, yf, sf, t: _build_number_display(rows, ci, yf, t),
    "scatter": _build_scatter_chart,
    "heatmap": _build_heatmap_chart,
    "calendar": _build_calendar_chart,
    "gauge": _build_gauge_chart,
    "treemap": _build_treemap_chart,
    "sankey": _build_sankey_chart,
    "graph": _build_graph_chart,
    "funnel": _build_funnel_chart,
}


# --- Markdown to HTML Converter ---

_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([A-Za-z0-9_-]+)\]:\s*(.+)$")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([A-Za-z0-9_-]+)\]")
_RESEARCH_FIGURE_RE = re.compile(
    r"^\{\{figure:(\w+)(?:\s+([^}]*))?\}\}\s*$"
)
_RESEARCH_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _slugify_heading(text: str) -> str:
    """Turn heading text into a URL-safe anchor id."""
    plain = re.sub(r"<[^>]+>", "", text)  # strip any HTML
    plain = re.sub(r"[^a-zA-Z0-9\s-]", "", plain).strip().lower()
    return re.sub(r"\s+", "-", plain) or "section"


def _markdown_to_html(
    text: str,
    *,
    research_mode: bool = False,
    case_study_mode: bool = False,
) -> str:
    """Convert markdown to HTML. Handles headers, bold, tables, lists, code, and chart placeholders.

    When research_mode=True, additionally parses research-layout directives:
      {{figure:chart_id caption="..." source="..."}}
      {{pullquote}} ... {{/pullquote}}
      {{callout kind=...}} ... {{/callout}}
      {{sidebar title="..."}} ... {{/sidebar}}
      [^id] inline + [^id]: text at end-of-doc footnotes
    All h2 headings get slug anchors for the floating TOC.

    When case_study_mode=True, additionally parses scrollytelling directives:
      {{scene chart="ID" | image="url" side="left|right"}} ... {{/scene}}
        - sticky visual + scrolling narrative; inner content is markdown
      {{step chart="ID" state="..."}} narrative {{/step}}
        - inside a scene, defines ordered chart states as the reader scrolls
      {{reveal}} - bullet\n - bullet {{/reveal}}
        - bullet list with progressive fade-in via IntersectionObserver
      {{image src="..." caption="..." full_bleed=true}}
        - hero/full-bleed image
      {{cta label="..." href="..."}}
        - call-to-action block
    """
    lines = text.split("\n")
    html_lines: list[str] = []
    in_list = False
    in_code_block = False
    in_table = False
    table_header_done = False
    in_content_card = False
    in_grid = False
    grid_cols = 0
    grid_chart_ids: list[str] = []
    # Research-only block state
    research_block: str | None = None  # "pullquote" | "callout" | "sidebar"
    research_block_buffer: list[str] = []
    research_block_attrs: dict[str, str] = {}

    def _close_content_card():
        nonlocal in_content_card
        if in_content_card:
            html_lines.append("</div>")  # close .content-card
            in_content_card = False

    # Case-study-only block state
    cs_block: str | None = None  # "scene" | "step" | "reveal"
    cs_block_buffer: list[str] = []
    cs_block_attrs: dict[str, str] = {}
    cs_step_counter = 0  # resets inside each scene

    def _flush_research_block() -> None:
        nonlocal research_block, research_block_buffer, research_block_attrs
        if research_block is None:
            return
        inner_html = _markdown_to_html(
            "\n".join(research_block_buffer),
            research_mode=research_mode,
            case_study_mode=case_study_mode,
        )
        if research_block == "pullquote":
            html_lines.append(f'<blockquote class="rr-pullquote">{inner_html}</blockquote>')
        elif research_block == "callout":
            kind = research_block_attrs.get("kind", "note")
            safe_kind = re.sub(r"[^a-zA-Z0-9_-]", "", kind) or "note"
            html_lines.append(
                f'<aside class="rr-callout rr-callout--{safe_kind}">{inner_html}</aside>'
            )
        elif research_block == "sidebar":
            title_attr = research_block_attrs.get("title", "")
            title_html = (
                f'<div class="rr-sidebar-title">{_escape_html(title_attr)}</div>'
                if title_attr
                else ""
            )
            html_lines.append(
                f'<aside class="rr-sidebar">{title_html}{inner_html}</aside>'
            )
        research_block = None
        research_block_buffer = []
        research_block_attrs = {}

    def _render_cs_visual(attrs: dict[str, str], *, hero: bool = False) -> str:
        """Render the sticky visual side of a scene as HTML."""
        chart_id = attrs.get("chart", "").strip()
        image_src = attrs.get("image", "").strip()
        caption = attrs.get("caption", "")
        alt = attrs.get("alt", caption)
        if chart_id:
            if chart_id not in _chart_registry:
                return (
                    f'<div class="cs-visual cs-visual--missing">'
                    f'Missing chart: {_escape_html(chart_id)}</div>'
                )
            return (
                f'<div class="cs-visual cs-visual--chart" data-cs-chart="{_escape_html(chart_id)}">'
                f'<div id="chart-{chart_id}" class="chart-container"></div>'
                f'</div>'
            )
        if image_src:
            cap_html = (
                f'<figcaption class="cs-visual-caption">{_inline_format(caption)}</figcaption>'
                if caption
                else ""
            )
            return (
                f'<figure class="cs-visual cs-visual--image">'
                f'<img src="{_escape_html(image_src)}" alt="{_escape_html(alt)}" />'
                f'{cap_html}'
                f'</figure>'
            )
        return '<div class="cs-visual cs-visual--empty"></div>'

    def _flush_cs_block() -> None:
        nonlocal cs_block, cs_block_buffer, cs_block_attrs
        nonlocal cs_step_counter
        if cs_block is None:
            return
        inner_html = _markdown_to_html(
            "\n".join(cs_block_buffer),
            research_mode=research_mode,
            case_study_mode=case_study_mode,
        )
        if cs_block == "scene":
            side = cs_block_attrs.get("side", "left").strip().lower()
            side_class = "cs-scene--right" if side == "right" else "cs-scene--left"
            visual_html = _render_cs_visual(cs_block_attrs)
            html_lines.append(
                f'<section class="cs-scene {side_class}">'
                f'<div class="cs-scene-visual">{visual_html}</div>'
                f'<div class="cs-scene-narrative">{inner_html}</div>'
                f'</section>'
            )
        elif cs_block == "step":
            idx = cs_block_attrs.get("_idx", "0")
            state_attr = cs_block_attrs.get("state", "")
            chart_id = cs_block_attrs.get("chart", "")
            html_lines.append(
                f'<div class="cs-step" data-step-index="{_escape_html(idx)}"'
                f' data-step-state="{_escape_html(state_attr)}"'
                f' data-step-chart="{_escape_html(chart_id)}">'
                f'{inner_html}</div>'
            )
        elif cs_block == "reveal":
            # inner_html contains a <ul> / <ol>; wrap with cs-reveal class.
            # If the caller put bullets inside, there will be a <ul>…</ul>.
            html_lines.append(
                f'<div class="cs-reveal" data-cs-reveal="true">{inner_html}</div>'
            )
        cs_block = None
        cs_block_buffer = []
        cs_block_attrs = {}

    footnote_defs: list[tuple[str, str]] = []

    for line in lines:
        # While inside a case-study block, buffer until matching close tag.
        # Scenes may contain nested steps; handle that by stripping only the
        # *outermost* matching close tag.
        if cs_block is not None and case_study_mode:
            stripped_line = line.strip()
            if cs_block == "scene":
                # Allow {{step ...}} / {{/step}} to be buffered as-is and
                # recursively parsed when the scene is flushed.
                if stripped_line == "{{/scene}}":
                    _flush_cs_block()
                    cs_step_counter = 0
                    continue
            else:
                close_tag = "{{/" + cs_block + "}}"
                if stripped_line == close_tag:
                    _flush_cs_block()
                    continue
            cs_block_buffer.append(line)
            continue

        # While inside a research block, buffer until matching close tag
        if research_block is not None and research_mode:
            stripped_line = line.strip()
            close_tag = "{{/" + research_block + "}}"
            if stripped_line == close_tag:
                _flush_research_block()
                continue
            research_block_buffer.append(line)
            continue

        # Case-study-only block openers and inline directives
        if case_study_mode:
            stripped_line = line.strip()
            # {{scene ...}}
            scene_open = re.match(r"^\{\{scene\b([^}]*)\}\}$", stripped_line)
            if scene_open:
                _close_content_card()
                attrs = {
                    k: v for k, v in _RESEARCH_ATTR_RE.findall(scene_open.group(1))
                }
                cs_block = "scene"
                cs_block_attrs = attrs
                cs_block_buffer = []
                cs_step_counter = 0
                continue
            # {{step ...}} — only at top-level buffer gets recursed when scene flushes;
            # when _markdown_to_html is called on scene inner content, we hit this path
            step_open = re.match(r"^\{\{step\b([^}]*)\}\}$", stripped_line)
            if step_open:
                _close_content_card()
                cs_step_counter += 1
                attrs = {
                    k: v for k, v in _RESEARCH_ATTR_RE.findall(step_open.group(1))
                }
                attrs.setdefault("_idx", str(cs_step_counter))
                cs_block = "step"
                cs_block_attrs = attrs
                cs_block_buffer = []
                continue
            # {{reveal}}
            if stripped_line == "{{reveal}}":
                _close_content_card()
                cs_block = "reveal"
                cs_block_attrs = {}
                cs_block_buffer = []
                continue
            # {{image src="..." caption="..." full_bleed=true}}
            image_open = re.match(r"^\{\{image\b([^}]*)\}\}$", stripped_line)
            if image_open:
                _close_content_card()
                raw_attrs = image_open.group(1) or ""
                attrs = {
                    k: v for k, v in _RESEARCH_ATTR_RE.findall(raw_attrs)
                }
                src = attrs.get("src", "").strip()
                caption = attrs.get("caption", "")
                alt = attrs.get("alt", caption)
                # full_bleed can be quoted ("true") or bare (true). Check both.
                full_bleed_bare = re.search(
                    r"full_bleed\s*=\s*([A-Za-z0-9]+)", raw_attrs
                )
                full_bleed_val = (
                    attrs.get("full_bleed")
                    or (full_bleed_bare.group(1) if full_bleed_bare else "")
                )
                full_bleed = str(full_bleed_val).lower() in {
                    "1", "true", "yes",
                }
                fb_class = " cs-image--full" if full_bleed else ""
                if not src:
                    html_lines.append(
                        '<figure class="cs-image cs-image--missing">'
                        'Missing image src</figure>'
                    )
                    continue
                cap_html = (
                    f'<figcaption class="cs-image-caption">{_inline_format(caption)}</figcaption>'
                    if caption
                    else ""
                )
                html_lines.append(
                    f'<figure class="cs-image{fb_class}">'
                    f'<img src="{_escape_html(src)}" alt="{_escape_html(alt)}" />'
                    f'{cap_html}'
                    f'</figure>'
                )
                continue
            # {{cta label="..." href="..."}}
            cta_open = re.match(r"^\{\{cta\b([^}]*)\}\}$", stripped_line)
            if cta_open:
                _close_content_card()
                attrs = {
                    k: v for k, v in _RESEARCH_ATTR_RE.findall(cta_open.group(1))
                }
                label = attrs.get("label", "Learn more")
                href = attrs.get("href", "#")
                html_lines.append(
                    f'<div class="cs-cta">'
                    f'<a class="cs-cta-btn" href="{_escape_html(href)}" '
                    f'target="_blank" rel="noopener noreferrer">'
                    f'{_inline_format(label)}</a>'
                    f'</div>'
                )
                continue

        # Research-only block openers
        if research_mode:
            stripped_line = line.strip()
            # {{pullquote}}
            if stripped_line == "{{pullquote}}":
                _close_content_card()
                research_block = "pullquote"
                research_block_attrs = {}
                research_block_buffer = []
                continue
            # {{callout kind=...}}
            callout_open = re.match(
                r"^\{\{callout\b([^}]*)\}\}$", stripped_line
            )
            if callout_open:
                _close_content_card()
                research_block = "callout"
                research_block_attrs = {
                    k: v for k, v in _RESEARCH_ATTR_RE.findall(callout_open.group(1))
                }
                # Also accept bare kind=... without quotes
                kind_bare = re.search(r"kind\s*=\s*([A-Za-z0-9_-]+)", callout_open.group(1))
                if kind_bare and "kind" not in research_block_attrs:
                    research_block_attrs["kind"] = kind_bare.group(1)
                research_block_buffer = []
                continue
            # {{sidebar title="..."}}
            sidebar_open = re.match(
                r"^\{\{sidebar\b([^}]*)\}\}$", stripped_line
            )
            if sidebar_open:
                _close_content_card()
                research_block = "sidebar"
                research_block_attrs = {
                    k: v for k, v in _RESEARCH_ATTR_RE.findall(sidebar_open.group(1))
                }
                research_block_buffer = []
                continue
            # {{figure:chart_id caption="..." source="..."}}
            figure_match = _RESEARCH_FIGURE_RE.match(stripped_line)
            if figure_match:
                _close_content_card()
                cid = figure_match.group(1)
                attrs = {
                    k: v
                    for k, v in _RESEARCH_ATTR_RE.findall(figure_match.group(2) or "")
                }
                caption = attrs.get("caption", "")
                source = attrs.get("source", "")
                if cid not in _chart_registry:
                    html_lines.append(
                        f'<figure class="rr-figure rr-figure--missing">'
                        f'<div class="rr-figure-missing">Missing chart: {_escape_html(cid)}</div>'
                        f'</figure>'
                    )
                    continue
                caption_html = (
                    f'<figcaption class="rr-figure-caption">'
                    f'{_inline_format(caption)}'
                    + (
                        f'<span class="rr-figure-source">Source: '
                        f'{_inline_format(source)}</span>'
                        if source
                        else ""
                    )
                    + "</figcaption>"
                    if caption or source
                    else ""
                )
                html_lines.append(
                    f'<figure class="rr-figure">'
                    f'<div class="chart-card rr-figure-chart">'
                    f'<div id="chart-{cid}" class="chart-container"></div>'
                    f'</div>'
                    f'{caption_html}'
                    f'</figure>'
                )
                continue
            # Footnote definition: [^id]: text  (collect, render at end)
            fn_def = _FOOTNOTE_DEF_RE.match(stripped_line)
            if fn_def:
                footnote_defs.append((fn_def.group(1), fn_def.group(2)))
                continue

        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
            else:
                lang = line.strip()[3:].strip()
                cls = f' class="language-{lang}"' if lang else ""
                html_lines.append(f"<pre><code{cls}>")
                in_code_block = True
            continue

        if in_code_block:
            html_lines.append(_escape_html(line))
            continue

        # Close list if needed
        if in_list and not line.strip().startswith("- "):
            html_lines.append("</ul>")
            in_list = False

        # Close table if needed
        if in_table and not line.strip().startswith("|"):
            html_lines.append("</tbody></table>")
            in_table = False
            table_header_done = False

        stripped = line.strip()

        # Grid open: {{grid:N}}
        grid_open = re.match(r"\{\{grid:(\d+)\}\}", stripped)
        if grid_open:
            _close_content_card()
            in_grid = True
            grid_cols = int(grid_open.group(1))
            grid_chart_ids = []
            continue

        # Grid close: {{/grid}} — emit combined grid element with data attribute
        if stripped == "{{/grid}}" and in_grid:
            ids_str = ",".join(grid_chart_ids)
            html_lines.append(
                f'<div class="chart-grid chart-grid-{grid_cols}" '
                f'data-grid-charts="{ids_str}"></div>'
            )
            in_grid = False
            grid_chart_ids = []
            continue

        # Chart placeholders
        chart_match = re.match(r"\{\{chart:(\w+)\}\}", stripped)
        if chart_match:
            chart_id = chart_match.group(1)
            # Inside grid: collect IDs, don't emit individual cards
            if in_grid:
                grid_chart_ids.append(chart_id)
                continue
            # Outside grid: emit standalone card.
            _close_content_card()
            # In case-study / research modes, omit the server-rendered title
            # div — the React ChartCard portal renders its own title, and
            # emitting both produces duplicates.
            if case_study_mode or research_mode:
                html_lines.append(
                    f'<div id="chart-{chart_id}" class="chart-container"></div>'
                )
            else:
                chart_title = _chart_registry.get(chart_id, {}).get("title", "")
                title_html = (
                    f'<div class="chart-title">{_escape_html(chart_title)}</div>'
                    if chart_title
                    else ""
                )
                html_lines.append(
                    f'<div class="chart-card">'
                    f'{title_html}'
                    f'<div id="chart-{chart_id}" class="chart-container"></div>'
                    f'</div>'
                )
            continue

        # Headers
        if stripped.startswith("### "):
            _close_content_card()
            in_content_card = True
            html_lines.append(f'<div class="content-card">')
            html_lines.append(f"<h3>{_inline_format(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            _close_content_card()
            heading_text = stripped[3:]
            if research_mode:
                anchor = _slugify_heading(heading_text)
                html_lines.append(
                    f'<h2 id="{anchor}" class="rr-section-heading">'
                    f'{_inline_format(heading_text)}</h2>'
                )
            else:
                html_lines.append(f"<h2>{_inline_format(heading_text)}</h2>")
            continue
        if stripped.startswith("# "):
            _close_content_card()
            html_lines.append(f"<h1>{_inline_format(stripped[2:])}</h1>")
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            _close_content_card()
            html_lines.append("<hr>")
            continue

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            # Skip separator rows
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                continue
            if not in_table:
                html_lines.append('<table>')
                html_lines.append("<thead><tr>")
                for cell in cells:
                    html_lines.append(f"<th>{_inline_format(cell)}</th>")
                html_lines.append("</tr></thead><tbody>")
                in_table = True
                table_header_done = True
                continue
            html_lines.append("<tr>")
            for cell in cells:
                cell_chart = re.match(
                    r"\{\{chart:(\w+)\}\}", cell.strip()
                )
                if cell_chart:
                    cid = cell_chart.group(1)
                    chart_data = _chart_registry.get(cid, {})
                    chart_opt = chart_data.get("option", {})
                    # Render numberDisplay values inline in table cells
                    if chart_opt.get("type") == "numberDisplay":
                        val = chart_opt.get("value", "")
                        formatted = _format_number_display_scalar(val)
                        change = chart_opt.get("change") if isinstance(chart_opt.get("change"), dict) else None
                        change_html = ""
                        if change:
                            change_value = _format_number_display_scalar(
                                change.get("value", ""),
                                signed=True,
                            )
                            change_label = change.get("label", "")
                            change_direction = change.get("direction", "neutral")
                            label_html = (
                                f'<span class="kpi-change-label">{_escape_html(str(change_label))}</span>'
                                if change_label
                                else ""
                            )
                            change_html = (
                                f'<span class="kpi-change number-change {change_direction}">'
                                f'{_escape_html(change_value)}{label_html}'
                                f'</span>'
                            )
                        html_lines.append(
                            f'<td class="kpi-cell">'
                            f'<div class="kpi-metric">'
                            f'<span class="kpi-value">{_escape_html(formatted)}</span>'
                            f'{change_html}'
                            f'</div>'
                            f'</td>'
                        )
                    else:
                        # Non-number charts: emit chart container div
                        html_lines.append(
                            f'<td><div id="chart-{cid}" '
                            f'class="chart-container"></div></td>'
                        )
                else:
                    html_lines.append(
                        f"<td>{_inline_format(cell)}</td>"
                    )
            html_lines.append("</tr>")
            continue

        # List items
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_format(stripped[2:])}</li>")
            continue

        # Blockquote
        if stripped.startswith("> "):
            html_lines.append(
                f"<blockquote><p>{_inline_format(stripped[2:])}</p></blockquote>"
            )
            continue

        # Empty line
        if not stripped:
            html_lines.append("")
            continue

        # Paragraph
        html_lines.append(f"<p>{_inline_format(stripped)}</p>")

    # Close any open tags
    if in_list:
        html_lines.append("</ul>")
    if in_table:
        html_lines.append("</tbody></table>")
    if in_code_block:
        html_lines.append("</code></pre>")
    _close_content_card()
    if research_mode and research_block is not None:
        _flush_research_block()
    if case_study_mode and cs_block is not None:
        _flush_cs_block()

    output = "\n".join(html_lines)

    if research_mode:
        # Assign numeric order based on first appearance in prose
        ref_order: dict[str, int] = {}

        def _ref_sub(m: "re.Match[str]") -> str:
            fid = m.group(1)
            if fid not in ref_order:
                ref_order[fid] = len(ref_order) + 1
            num = ref_order[fid]
            return (
                f'<sup class="rr-footnote-ref">'
                f'<a id="fnref-{fid}" href="#fn-{fid}">{num}</a></sup>'
            )

        output = _FOOTNOTE_REF_RE.sub(_ref_sub, output)

        if footnote_defs:
            footnote_items: list[str] = []
            # Order footnotes by order of reference, fall back to def order
            def _sort_key(item: tuple[str, str]) -> int:
                fid = item[0]
                return ref_order.get(fid, 10_000 + footnote_defs.index(item))

            for fid, body in sorted(footnote_defs, key=_sort_key):
                num = ref_order.get(fid, list(dict.fromkeys(f for f, _ in footnote_defs)).index(fid) + 1)
                footnote_items.append(
                    f'<li id="fn-{fid}" class="rr-footnote-item">'
                    f'<span class="rr-footnote-num">{num}.</span> '
                    f'{_inline_format(body)} '
                    f'<a class="rr-footnote-back" href="#fnref-{fid}" aria-label="Back to text">↩</a>'
                    f'</li>'
                )
            output += (
                '\n<section class="rr-footnotes" aria-label="Footnotes">'
                '<h2 class="rr-footnotes-heading">Footnotes</h2>'
                f'<ol class="rr-footnote-list">{"".join(footnote_items)}</ol>'
                '</section>'
            )

    return output


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting: bold, code, links, and value coloring."""
    text = _escape_html(text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Inline code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # Positive numeric changes: +1,234.56% or +1234.56 etc.
    text = re.sub(
        r"(\+\d[\d,]*\.?\d*%?)",
        r'<span class="number-change positive">\1</span>',
        text,
    )
    # Negative numeric changes: -1,234.56% or -1234.56 etc.
    text = re.sub(
        r"(-\d[\d,]*\.?\d*%?)",
        r'<span class="number-change negative">\1</span>',
        text,
    )
    return text


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# --- MCP App Resource URI ---

REPORT_URI = "ui://cerebro/report"
VISUALIZATION_URI = "ui://cerebro/visualization"

# The most recent visual-answer artifact rendered by the chart tools.
# `ui://cerebro/visualization` (tool-level meta on generate_charts /
# quick_chart) serves this artifact's standalone HTML — data EMBEDDED — so
# hosts that render tool-level UI resources but ignore result-level `_meta`
# and never complete the ext-apps handshake (e.g. Claude Desktop) still show
# the actual charts inline. Freshness-gated so a report-tier batch call does
# not surface a stale visualization.
_LAST_VISUAL: dict = {"report_id": None, "created_at": None}
_LAST_VISUAL_FRESH_SECONDS = 600


def _build_standalone_html(
    title: str,
    timestamp: str,
    charts: dict,
    sections_html: str,
    queries: dict | None = None,
    *,
    presentation_mode: str | None = None,
    research_metadata: dict | None = None,
    case_study_metadata: dict | None = None,
    subtitle: str | None = None,
) -> str:
    """Build self-contained HTML with embedded data for disk saves / direct file access.

    Injects a <script id="report-data"> tag into the Vite-built React app.
    The React app detects this tag and renders the report in standalone mode.
    """
    data_dict = {
        "title": title,
        "timestamp": timestamp,
        "charts": charts,
        "sections_html": sections_html,
    }
    if queries:
        data_dict["queries"] = queries
    if presentation_mode:
        data_dict["presentation_mode"] = presentation_mode
    if research_metadata:
        data_dict["research_metadata"] = research_metadata
    if case_study_metadata:
        data_dict["case_study_metadata"] = case_study_metadata
    if subtitle:
        data_dict["subtitle"] = subtitle
    data = json.dumps(data_dict, default=str)

    html = _get_report_html()
    data_tag = f'<script id="report-data" type="application/json">{data}</script>'
    # Use rfind to target only the LAST </body> tag (not one inside minified JS)
    insert_pos = html.rfind("</body>")
    if insert_pos == -1:
        return html + data_tag
    return html[:insert_pos] + data_tag + "\n" + html[insert_pos:]


def list_charts_impl() -> str:
    """Shared implementation for the ``list_charts`` tool and the ``list``
    unifier (``kind="charts"``). Byte-identical output for both callers."""
    if not _chart_registry:
        return (
            "No charts registered. Use `generate_charts` for report batches "
            "or `generate_chart` / `quick_chart` for one-off charts first."
        )

    lines = ["# Registered Charts\n"]
    lines.append("| Chart ID | Title | Type | Data Points |")
    lines.append("|----------|-------|------|-------------|")

    for chart_id, info in _chart_registry.items():
        lines.append(
            f"| {chart_id} | {info['title']} | {info['chart_type']} "
            f"| {info['data_points']} |"
        )

    lines.append(
        f"\nTotal: {len(_chart_registry)} charts. "
        f"Use `{{{{chart:CHART_ID}}}}` placeholders in `generate_report`."
    )
    return "\n".join(lines)


def list_reports_impl(limit: int = 20) -> str:
    """Shared implementation for the ``list_reports`` tool and the ``list``
    unifier (``kind="reports"``). Byte-identical output for both callers."""
    report_dir = Path(
        os.environ.get("CEREBRO_REPORT_DIR", "~/.cerebro/reports")
    ).expanduser()
    if not report_dir.exists():
        return "No report directory found. Generate a report first with `generate_report`."

    html_files = sorted(
        _iter_report_files(report_dir),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not html_files:
        return "No saved reports found. Generate a report first with `generate_report` or `generate_research_report`."

    lines = ["# Saved Reports\n"]
    lines.append("| # | Report ID | Kind | Title | Created (UTC) | Size | Link |")
    lines.append("|---|-----------|------|-------|---------------|------|------|")

    for i, f in enumerate(html_files[:limit], 1):
        stat = f.stat()
        modified = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")
        size_kb = stat.st_size / 1024
        file_uri = _get_report_link(f)
        full_id = _extract_report_id_from_path(f)
        short_id = full_id[:8]
        kind = _report_kind_from_path(f)
        # Slug is the 4th segment in both filename schemes (0-indexed)
        parts = f.stem.split("_")
        slug = parts[3] if len(parts) >= 5 else ""
        title_hint = slug.replace("-", " ").title() if slug else "—"
        lines.append(
            f"| {i} | `{short_id}` | {kind} | {title_hint} | {modified} "
            f"| {size_kb:.0f} KB | {file_uri} |"
        )

    if len(html_files) > limit:
        lines.append(f"\n_Showing {limit} of {len(html_files)} reports._")

    lines.append(f"\nReport directory: `{report_dir}`")
    lines.append("\nTo reopen: `open_report(\"<report_id>\")`")
    return "\n".join(lines)


def register_visualization_tools(mcp, ch: ClickHouseManager):
    """Register chart generation and report tools."""

    def _register_chart_from_dataset(
        *,
        columns: list[str],
        rows: list[list],
        sql: str,
        database: str,
        chart_type: str,
        x_field: str,
        y_field: str,
        change_field: str,
        series_field: str,
        title: str,
        elapsed_seconds: float,
        source: str,
        return_metadata_only: bool = False,
        explain_context: bool = False,
    ) -> str:
        """Build ECharts spec from tabular data and register the chart.

        When return_metadata_only=True, returns only a compact metadata line
        (chart ID, type, title, data points, query time) without the full
        ECharts JSON or SQL echo. Used by batch chart tools.

        When explain_context=True, a "What this shows" rationale derived from
        the dbt model/column docs is stored on the chart and shown on the card.
        """
        from cerebro_mcp.tools.governance.session_state import state

        if chart_type not in CHART_BUILDERS:
            supported = ", ".join(CHART_BUILDERS.keys())
            return f"Error: Unknown chart type '{chart_type}'. Supported: {supported}"

        try:
            if not rows:
                return "Error: Query returned no data. Cannot generate chart."

            col_index = _build_col_index(columns)

            # Auto-detect fields if not specified
            if chart_type == "numberDisplay":
                y_field, change_field, number_display_error = _resolve_number_display_fields(
                    columns=columns,
                    rows=rows,
                    x_field=x_field,
                    y_field=y_field,
                    change_field=change_field,
                    series_field=series_field,
                )
                if number_display_error:
                    return number_display_error
            else:
                if not x_field and columns:
                    x_field = columns[0]
                if not y_field and len(columns) > 1:
                    y_field = columns[1]
                if chart_type in {"line", "area", "bar"} and not y_field:
                    available = ", ".join(columns)
                    return (
                        f"Error: `{chart_type}` charts require a dimension column and at least one "
                        f"value column. This query returned columns: {available}. Use "
                        "`numberDisplay` for a single aggregated metric or include a dimension such "
                        "as `day`."
                    )

            # Validate fields exist
            if x_field and x_field not in col_index:
                available = ", ".join(columns)
                return f"Error: x_field '{x_field}' not found in columns: {available}"
            if y_field:
                requested_y_fields = [field.strip() for field in y_field.split(",") if field.strip()]
                missing_y_fields = [field for field in requested_y_fields if field not in col_index]
                if missing_y_fields:
                    available = ", ".join(columns)
                    missing = ", ".join(missing_y_fields)
                    return (
                        f"Error: y_field '{missing}' not found in columns: {available}"
                    )
            if change_field and change_field not in col_index:
                available = ", ".join(columns)
                return f"Error: change_field '{change_field}' not found in columns: {available}"
            if series_field and series_field not in col_index:
                available = ", ".join(columns)
                return f"Error: series_field '{series_field}' not found in columns: {available}"

            shape_error, input_shape = _validate_chart_input_shape(
                chart_type=chart_type,
                columns=columns,
                rows=rows,
                x_field=x_field,
                y_field=y_field,
                change_field=change_field,
                series_field=series_field,
            )
            if shape_error:
                return shape_error

            builder = CHART_BUILDERS[chart_type]
            if chart_type == "numberDisplay":
                option = _build_number_display(
                    rows,
                    col_index,
                    y_field,
                    title,
                    change_field,
                )
            else:
                option = builder(rows, col_index, x_field, y_field, series_field, title)
            state.record_generate_chart(chart_type, sql, series_field, source=source)

            rationale = ""
            if explain_context:
                from cerebro_mcp.runtime.context_enrichment import (
                    build_sql_context_block,
                )

                rationale = build_sql_context_block(sql, columns)

            # Register chart in registry (with TTL tracking)
            chart_id = _next_chart_id()
            with _chart_lock:
                _prune_chart_registry()
                _chart_registry[chart_id] = {
                    "option": option,
                    "title": title or chart_type,
                    "chart_type": chart_type,
                    "data_points": len(rows),
                    "created_at": datetime.now(),
                    "sql": sql,
                    "database": database,
                    "series_field": series_field,
                    "change_field": change_field,
                    "input_shape": input_shape,
                    "source": source,
                    "source_model": _single_source_model(sql),
                    "rationale": rationale,
                }

            # Metadata-only mode: compact single line for batch tool
            if return_metadata_only:
                series_tag = f" | series: {series_field}" if series_field else ""
                return (
                    f"OK|{chart_id}|{chart_type}|{title or chart_type}"
                    f"|{len(rows)}|{elapsed_seconds}s{series_tag}"
                )

            output = json.dumps(option, default=str, indent=2)
            metadata = (
                f"\n\n---\n"
                f"Chart ID: **{chart_id}** (use in reports with "
                f"`{{{{chart:{chart_id}}}}}`) | "
                f"Type: {chart_type} | "
                f"Data points: {len(rows)} | "
                f"Query time: {elapsed_seconds}s"
            )

            if rationale:
                metadata += f"\n\n{rationale}"

            metadata += f"\n\n### SQL\n```sql\n{_truncate_sql(sql)}\n```"

            # Workflow next-step with registered charts summary
            total_charts = len(_chart_registry)
            chart_list = ", ".join(_chart_registry.keys())
            metadata += (
                f"\n\n**Registered charts ({total_charts}):** {chart_list}\n"
                "**Next step:** When all charts are ready, call "
                "`generate_report(title, content_markdown)` with "
                "`{{chart:ID}}` placeholders to produce an interactive report."
            )

            return truncate_response(output + metadata)

        except Exception as e:
            error_msg = str(e)
            if "UNKNOWN_IDENTIFIER" in error_msg or "Unknown expression" in error_msg:
                return (
                    f"Error: {error_msg}\n\n"
                    "**Hint**: Wrong column name in the SQL query. "
                    "Use `describe_table` to verify exact column names before writing SQL. "
                    "Do NOT guess — most tables use generic names like `value`, `cnt`, `date`."
                )
            available = ", ".join(columns) if "columns" in locals() else ""
            selected = (
                f"chart_type={chart_type}, x_field={x_field or '<auto>'}, "
                f"y_field={y_field or '<auto>'}, series_field={series_field or '<none>'}"
            )
            detail = error_msg or e.__class__.__name__
            return (
                f"Error: Chart rendering failed ({e.__class__.__name__}): {detail}\n\n"
                f"Context: {selected}\n"
                f"Available columns: {available or '<unknown>'}"
            )

    def _build_and_register_chart(
        sql: str,
        database: str,
        chart_type: str,
        x_field: str,
        y_field: str,
        change_field: str,
        series_field: str,
        title: str,
        max_rows: int,
        return_metadata_only: bool = False,
        explain_context: bool = False,
    ) -> str:
        """Internal helper: execute SQL, build ECharts spec, register chart."""
        try:
            executed = ch.run_query(
                sql,
                database,
                requested_max_rows=max_rows,
                audience="internal",
            )
            return _register_chart_from_dataset(
                columns=executed.columns,
                rows=executed.rows,
                sql=sql,
                database=database,
                chart_type=chart_type,
                x_field=x_field,
                y_field=y_field,
                change_field=change_field,
                series_field=series_field,
                title=title,
                elapsed_seconds=executed.elapsed_seconds,
                source="raw",
                return_metadata_only=return_metadata_only,
                explain_context=explain_context,
            )
        except Exception as e:
            error_msg = str(e)
            if "UNKNOWN_IDENTIFIER" in error_msg or "Unknown expression" in error_msg:
                return (
                    f"Error: {error_msg}\n\n"
                    "**Hint**: Wrong column name in the SQL query. "
                    "Use `describe_table` to verify exact column names before writing SQL. "
                    "Do NOT guess — most tables use generic names like `value`, `cnt`, `date`."
                )
            return f"Error: {error_msg or e.__class__.__name__}"

    def _semantic_gate_error(stage: str, reason: str) -> str:
        observe_semantic_bypass(stage=stage, reason=reason)
        log_event(
            logger,
            "semantic_bypass",
            stage=stage,
            reason=reason,
        )
        return reason

    def _reason_mentions_semantic_routing(reason: str) -> bool:
        # A gate reason may now bundle several prerequisites in one message, so
        # match the semantic-routing phrases as substrings, not a prefix.
        return (
            "Semantic preflight required" in reason
            or "Semantic charting requires" in reason
            or "Approved semantic coverage" in reason
        )

    def _check_raw_chart_gate(stage: str) -> str:
        from cerebro_mcp.tools.governance.session_state import state

        passed, reason = state.check_chart_preconditions(raw_path=True)
        if passed:
            return ""
        if _reason_mentions_semantic_routing(reason):
            return _semantic_gate_error(stage, reason)
        return reason

    def _format_raw_chart_gate_failure(reason: str) -> str:
        if _reason_mentions_semantic_routing(reason):
            return (
                f"**Semantic routing check failed:** {reason}\n\n"
                "Call `preflight_analytics_request` first, then use "
                "`quick_metric_chart` / `generate_metric_charts` when the "
                "route is `semantic_ready`, or retry raw charting after an "
                "explicit semantic fallback."
            )
        return (
            f"**Chart workflow check failed:** {reason}\n\n"
            "Verify the required schema or discovery context, then retry "
            "`quick_chart`."
        )

    def _check_semantic_chart_gate(stage: str, *, require_common_depth: bool) -> str:
        if require_common_depth:
            passed, reason = state.check_chart_preconditions(raw_path=False)
            if passed:
                return ""
            if _reason_mentions_semantic_routing(reason):
                return _semantic_gate_error(stage, reason)
            return reason

        if not (state.semantic_preflight_ran or state.semantic_find_ran):
            return _semantic_gate_error(
                stage,
                "Semantic preflight required: call `find(query, mode=\"chart\")` or `preflight_analytics_request(query, mode=\"chart\")` before semantic charting.",
            )
        if state.semantic_route_last not in ("semantic_ready", "hybrid_ready"):
            return _semantic_gate_error(
                stage,
                "Semantic charting requires a `semantic_ready` or `hybrid_ready` route from `preflight_analytics_request`.",
            )
        return ""

    def _normalize_semantic_field_name(field_name: str) -> str:
        cleaned = (field_name or "").strip()
        if not cleaned:
            return ""
        return _SEMANTIC_DIMENSION_ALIASES.get(cleaned.lower(), cleaned)

    def _validate_semantic_chart_request(
        *,
        chart_type: str,
        dimensions: list[str] | None,
    ) -> str:
        if chart_type in {"line", "area", "bar"} and not dimensions:
            return (
                f"Error: Semantic `{chart_type}` charts require at least one dimension for the X axis. "
                "Add `dimensions=['day']` (or another allowed dimension) or use "
                "`chart_type='numberDisplay'` for a single aggregated metric."
            )
        return ""

    def _resolve_semantic_chart_fields(
        *,
        chart_type: str,
        result,
        requested_dimensions: list[str] | None,
        x_field: str,
        y_field: str,
        change_field: str,
    ) -> tuple[str, str, str]:
        normalized_dimensions = [
            _normalize_semantic_field_name(name)
            for name in (requested_dimensions or result.resolved_dimensions or [])
        ]
        resolved_x = _normalize_semantic_field_name(x_field)
        resolved_y = y_field.strip()
        resolved_change = change_field.strip()

        if chart_type in {"line", "area", "bar"}:
            if not resolved_x:
                resolved_x = normalized_dimensions[0] if normalized_dimensions else ""
            if not resolved_x:
                return "", "", (
                    f"Error: Semantic `{chart_type}` charts require a real X-axis dimension. "
                    "Add `dimensions=['day']` or use `chart_type='numberDisplay'`."
                )
            if resolved_x not in result.columns:
                available = ", ".join(result.columns)
                return "", "", (
                    f"Error: Semantic `{chart_type}` charts require a real X-axis dimension. "
                    f"Requested `{resolved_x}`, available columns: {available}."
                )
            if len(result.columns) == 1:
                return "", "", (
                    f"Error: Semantic `{chart_type}` charts cannot render a single aggregated metric "
                    f"without a dimension. Use `chart_type='numberDisplay'` or add `dimensions=['day']`."
                )
            if not resolved_y:
                candidate_metrics = [
                    column_name
                    for column_name in result.columns
                    if column_name != resolved_x
                ]
                if not candidate_metrics:
                    return "", "", (
                        f"Error: Semantic `{chart_type}` charts require at least one numeric value column "
                        "in addition to the X-axis dimension."
                    )
                resolved_y = (
                    ",".join(candidate_metrics)
                    if len(candidate_metrics) > 1
                    else candidate_metrics[0]
                )
        elif chart_type == "numberDisplay":
            resolved_x = ""
            if resolved_change and resolved_change not in result.columns:
                available = ", ".join(result.columns)
                return "", "", (
                    f"Error: change_field '{resolved_change}' not found in semantic result columns: {available}"
                )
            if not resolved_y:
                candidate_metrics = [
                    column_name
                    for column_name in result.columns
                    if column_name not in normalized_dimensions and column_name != resolved_change
                ]
                if len(candidate_metrics) == 1:
                    resolved_y = candidate_metrics[0]
                else:
                    available = ", ".join(candidate_metrics or result.columns)
                    return "", "", (
                        "Error: Semantic `numberDisplay` requires an explicit main value column when the "
                        f"result contains multiple fields. Set `y_field` and optionally `change_field`. Available columns: {available}."
                    )

        return resolved_x, resolved_y, ""

    # ── Gated chart tool (for reports) ──────────────────────────────

    @mcp.tool()
    @_offloaded
    def generate_chart(
        sql: str,
        database: str = "dbt",
        chart_type: str = "line",
        x_field: str = "",
        y_field: str = "",
        change_field: str = "",
        series_field: str = "",
        title: str = "",
        max_rows: int = 500,
        explain_context: bool = False,
    ) -> str:
        """Generate a single ad-hoc chart. For reports, use `generate_charts` instead.

        This tool creates ONE chart at a time. If you are building a report,
        DO NOT call this tool repeatedly — use `generate_charts` (batch) to
        create all charts in a single call. Calling this tool multiple times
        wastes steps and context.

        Use this tool ONLY for:
        - Adding a single extra chart after a batch
        - Quick one-off visualizations outside of a report workflow

        Supported chart types: line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel.

        Chart/query contract:
        - `numberDisplay` requires a single-row SQL result.
        - KPI cards do not guess value vs. delta columns. Use `y_field` for the
          main KPI and optional `change_field` for the delta.
        - For latest-period KPIs, use queries such as `ORDER BY month DESC LIMIT 1`.
        - `line`, `area`, and `bar` charts do NOT auto-plot extra numeric
          columns. If your query returns multiple metrics, either set
          `y_field="metric_a,metric_b"` or reshape to long form and provide
          `series_field`.

        Args:
            sql: SQL query to execute for chart data. Only use column names
                 verified via `describe_table` or `get_model_details`.
            database: Target database. Default: dbt.
            chart_type: Chart type (line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel). Default: line.
            x_field: Column name for the X axis (categories/dates).
            y_field: Column name for the Y axis (values).
            change_field: Optional explicit delta/change column for `numberDisplay`.
            series_field: Optional column name to split data into multiple series.
            title: Chart title.
            max_rows: Maximum data points. Default: 500.
            explain_context: When True, append a "What this shows" rationale
                derived from the dbt model/column docs. Default: False.

        Returns:
            ECharts option JSON string. Render with: echarts.setOption(JSON.parse(result))
        """
        reason = _check_raw_chart_gate("generate_chart")
        if reason:
            return (
                f"**Analysis depth check failed:** {reason}\n\n"
                "Complete the missing steps, then retry `generate_chart`."
            )

        result = _build_and_register_chart(
            sql, database, chart_type, x_field, y_field, change_field,
            series_field, title, max_rows, explain_context=explain_context,
        )

        # Nudge LLM toward batch tool if calling repeatedly
        with state.lock:
            chart_count = state.generate_chart_count
        if chart_count >= 2:
            result += (
                f"\n\n**Warning:** You have called `generate_chart` "
                f"{chart_count} times individually. For reports, use "
                f"`generate_charts` (batch) to create all remaining "
                f"charts in ONE call — this saves steps and context."
            )

        return result

    # ── Quick chart tool (no gates) ─────────────────────────────────

    @mcp.tool(meta=_ui_tool_meta(VISUALIZATION_URI))
    @_offloaded
    def quick_chart(
        sql: str,
        database: str = "dbt",
        chart_type: str = "line",
        x_field: str = "",
        y_field: str = "",
        change_field: str = "",
        series_field: str = "",
        title: str = "",
        max_rows: int = 500,
        explain_context: bool = False,
    ) -> CallToolResult:
        """Generate a quick ad-hoc chart for a one-off plot request.

        Use this for simple "show me / plot X" asks. This is the lightest
        charting path, but note that when the semantic layer is enabled (the
        deployed default) it still requires `preflight_analytics_request` first,
        like the other chart tools — it does NOT bypass that gate. The chart is
        the deliverable: present it and stop; do not follow up with
        `generate_report` unless the user asks for a report.

        For a multi-chart report, use `generate_charts` instead so all report
        charts are created in one batch call.

        Supported chart types: line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel.

        Chart/query contract:
        - `numberDisplay` requires a single-row SQL result. For latest-period
          KPIs, use queries such as `ORDER BY month DESC LIMIT 1`.
        - `line`, `area`, and `bar` charts do NOT auto-plot extra numeric
          columns. If your query returns multiple metrics, either set
          `y_field="metric_a,metric_b"` or reshape to long form and provide
          `series_field`.

        Args:
            sql: SQL query to execute for chart data.
            database: Target database. Default: dbt.
            chart_type: Chart type (line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel). Default: line.
            x_field: Column name for the X axis (categories/dates).
            y_field: Column name for the Y axis (values).
            series_field: Optional column name to split data into multiple series.
            title: Chart title.
            max_rows: Maximum data points. Default: 500.

        Returns:
            In chart/answer mode: the rendered visualization (inline UI where
            supported, plus an Open link). Otherwise: the ECharts option JSON
            string.
        """
        reason = _check_raw_chart_gate("quick_chart")
        if reason:
            return _text_result(_format_raw_chart_gate_failure(reason))

        if _chart_mode_active():
            meta_result = _build_and_register_chart(
                sql, database, chart_type, x_field, y_field, change_field,
                series_field, title, max_rows, return_metadata_only=True,
                explain_context=explain_context,
            )
            if meta_result.startswith("OK|"):
                chart_id = meta_result.split("|")[1]
                try:
                    report = _render_visual_answer(
                        [chart_id], title or "Visualization"
                    )
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=_model_inline_block([chart_id]),
                                annotations=Annotations(
                                    audience=["assistant"],
                                    priority=1.0,
                                ),
                            ),
                            TextContent(
                                type="text",
                                text=(
                                    f"Chart `{chart_id}` created.\n\n"
                                    + report["reply_text"]
                                ),
                            ),
                        ],
                        structuredContent=report["structured"],
                        _meta=_result_ui_meta(report["report_id"]),
                    )
                except Exception:  # pragma: no cover - fall back to JSON
                    pass
            else:
                return _text_result(meta_result)

        return _text_result(
            _build_and_register_chart(
                sql, database, chart_type, x_field, y_field, change_field,
                series_field, title, max_rows, explain_context=explain_context,
            )
        )

    # ── Batch chart tool (for reports) ──────────────────────────────

    def _text_result(text: str, *, is_error: bool = False) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            isError=is_error,
        )

    @mcp.tool(meta=_ui_tool_meta(VISUALIZATION_URI))
    @_offloaded
    def generate_charts(
        charts: list[ChartSpec], explain_context: bool = False
    ) -> CallToolResult:
        """Create multiple charts in ONE tool call.

        PREFERRED over generate_chart whenever you need more than one chart.
        For a standalone chart request (routed mode="chart"/"answer") this tool
        ALSO RENDERS the charts as an inline visualization — the result carries
        the rendered visual and an Open link; present it and stop, no report
        needed. For a report (mode="report"), this batches all charts in one
        call and `generate_report` renders them afterwards.

        Runs the same precondition checks as generate_chart but only once,
        then creates all charts in sequence.

        Each chart spec must have at least `sql`. All other fields are optional
        with sensible defaults.

        Chart/query contract:
        - Every `numberDisplay` chart must come from a single-row query.
        - KPI cards do not guess value vs. delta columns. Use `y_field` for the
          main KPI and optional `change_field` for the delta.
          Do not pass raw time series into KPI cards.
        - For latest-period monthly or weekly KPIs, use queries such as
          `ORDER BY month DESC LIMIT 1`.
        - For `line`, `area`, and `bar` charts, extra numeric columns are not
          auto-plotted. Use comma-separated `y_field` values for wide input or
          reshape to long form and provide `series_field`.

        Choosing the chart type — match it to the data shape, not the default:
        - Trend over time: `line` (or `area` only when the total itself is the
          story). Weekly/monthly-by-dimension → `line` with `series_field`, one
          line per dimension. Do NOT stack an `area` when you want to compare
          series against each other.
        - Composition at a point in time: `bar` (grouped) or `pie`/`treemap`.
        - Two measures' relationship: `scatter` (+ trendline) or `heatmap`.
        - Dominant-category skew (one bucket, e.g. `unknown`/residual, is >70%
          of the total) flattens every other series into an unreadable sliver
          and makes real movement look "flat" or "weak". Handle it explicitly:
          plot the residual bucket on its own axis/chart, exclude it (and say so
          in the title/subtitle — the residual_bucket_disclosure gate requires
          the acknowledgment), index each series to its own start (=100), or use
          a log y-axis. Never let a giant residual bucket decide the y-scale for
          the series the user actually asked about.

        Reports MUST include:
        - At least 1 chart with series_field (dimensional breakdown)
        - At least 1 scatter/heatmap chart OR correlation query

        Args:
            charts: List of chart specifications. Each spec has:
                sql (required), database (default "dbt"),
                chart_type (default "line"), x_field, y_field, change_field,
                series_field, title, max_rows (default 500).
            explain_context: When True, store a "What this shows" rationale
                (from dbt docs) on each chart so reports can display it.
                Default: False.

        Returns:
            In chart/answer mode: the rendered visualization (inline UI where
            supported, plus an Open link). In report mode: a summary table
            mapping input index to chart IDs for report placement.
        """
        if not charts:
            return _text_result(
                "Error: No chart specs provided. Pass a non-empty list.",
                is_error=True,
            )

        reason = _check_raw_chart_gate("generate_charts")
        if reason:
            return _text_result(
                f"**Analysis depth check failed:** {reason}\n\n"
                "Complete the missing steps, then retry `generate_charts`."
            )

        succeeded = []
        failed = []

        for i, spec in enumerate(charts, 1):
            sql = spec.get("sql", "")
            if not sql:
                failed.append((i, spec.get("title", "untitled"), "No SQL provided"))
                continue

            result = _build_and_register_chart(
                sql=sql,
                database=spec.get("database", "dbt"),
                chart_type=spec.get("chart_type", "line"),
                x_field=spec.get("x_field", ""),
                y_field=spec.get("y_field", ""),
                change_field=spec.get("change_field", ""),
                series_field=spec.get("series_field", ""),
                title=spec.get("title", ""),
                max_rows=spec.get("max_rows", 500),
                return_metadata_only=True,
                explain_context=explain_context,
            )

            if result.startswith("OK|"):
                parts = result.split("|")
                succeeded.append({
                    "index": i,
                    "chart_id": parts[1],
                    "chart_type": parts[2],
                    "title": parts[3],
                    "data_points": parts[4],
                    "query_time": parts[5],
                })
            else:
                failed.append((i, spec.get("title", "untitled"), result))

        # Build output
        total = len(charts)
        ok_count = len(succeeded)
        lines = [f"Generated {ok_count}/{total} charts:\n"]

        lines.append("| # | Chart ID | Title | Type | Points | Time |")
        lines.append("|---|----------|-------|------|--------|------|")
        for s in succeeded:
            lines.append(
                f"| {s['index']} | {s['chart_id']} | {s['title']} "
                f"| {s['chart_type']} | {s['data_points']} "
                f"| {s['query_time']} |"
            )

        if failed:
            lines.append(f"\nFailed ({len(failed)}):")
            for idx, title, err in failed:
                lines.append(f"- Input #{idx} (\"{title}\"): {err}")

        # Chart/answer tier: the charts ARE the deliverable, and
        # generate_report is hard-blocked — so render them right here as a
        # lightweight visualization (inline UI where the host supports it,
        # plus an always-working Open link).
        if ok_count >= 1 and _chart_mode_active():
            try:
                visual_title = succeeded[0]["title"] or "Visualization"
                chart_ids = [s["chart_id"] for s in succeeded]
                report = _render_visual_answer(chart_ids, visual_title)
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=_model_inline_block(chart_ids),
                            annotations=Annotations(
                                audience=["assistant"],
                                priority=1.0,
                            ),
                        ),
                        TextContent(
                            type="text",
                            text="\n".join(lines) + "\n\n" + report["reply_text"],
                        ),
                    ],
                    structuredContent=report["structured"],
                    _meta=_result_ui_meta(report["report_id"]),
                )
            except Exception as exc:  # pragma: no cover - render must not
                # break chart creation; fall through to the text summary.
                lines.append(f"\n(Inline render unavailable: {exc})")

        # Report tier (or non-semantic): summary only; generate_report
        # renders the charts.
        chart_list = ", ".join(_chart_registry.keys())
        lines.append(
            f"\n**Registered charts ({len(_chart_registry)}):** {chart_list}\n"
            "**Next step:** If the user asked for a report/dashboard/analysis, "
            "call `generate_report(title, content_markdown)` with `{{chart:ID}}` "
            "placeholders. For a plain chart request these charts are the "
            "deliverable — present them and stop."
        )

        return _text_result("\n".join(lines))

    @mcp.tool()
    def quick_metric_chart(
        metrics: list[str],
        dimensions: list[str] | None = None,
        filters: list[dict] | None = None,
        order_by: list[str] | None = None,
        limit: int = 100,
        chart_type: str = "line",
        x_field: str = "",
        y_field: str = "",
        change_field: str = "",
        series_field: str = "",
        title: str = "",
        agent_role: str = "",
    ) -> str:
        """Generate a one-off semantic chart without writing SQL."""
        reason = _check_semantic_chart_gate("quick_metric_chart", require_common_depth=False)
        if reason:
            return f"Error: {reason}"
        request_error = _validate_semantic_chart_request(
            chart_type=chart_type,
            dimensions=dimensions,
        )
        if request_error:
            return request_error

        from cerebro_mcp.tools.semantic.semantic import execute_metric_query

        role = agent_role or "unknown"
        state.record_semantic_tool_call("quick_metric_chart", execution=True)

        result = execute_metric_query(
            ch=ch,
            research_store=None,
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            order_by=order_by,
            limit=limit,
            agent_role=role,
        )
        if isinstance(result, str):
            observe_semantic_tool_call(
                tool_name="quick_metric_chart",
                status="error",
                agent_role=role,
                entrypoint="semantic_chart",
            )
            return result

        resolved_x_field, resolved_y_field, field_error = _resolve_semantic_chart_fields(
            chart_type=chart_type,
            result=result,
            requested_dimensions=dimensions,
            x_field=x_field,
            y_field=y_field,
            change_field=change_field,
        )
        if field_error:
            observe_semantic_tool_call(
                tool_name="quick_metric_chart",
                status="error",
                agent_role=role,
                entrypoint="semantic_chart",
            )
            return field_error

        observe_semantic_tool_call(
            tool_name="quick_metric_chart",
            status="success",
            agent_role=role,
            entrypoint="semantic_chart",
        )

        return _register_chart_from_dataset(
            columns=result.columns,
                rows=result.rows,
                sql=result.sql,
                database=result.database,
                chart_type=chart_type,
                x_field=resolved_x_field,
                y_field=resolved_y_field,
                change_field=change_field,
                series_field=series_field,
                title=title,
                elapsed_seconds=result.elapsed_seconds,
                source="semantic",
            )

    @mcp.tool()
    def generate_metric_charts(charts: list[MetricChartSpec], agent_role: str = "") -> str:
        """Create multiple semantic charts in one batch call."""
        if not charts:
            return "Error: No semantic chart specs provided. Pass a non-empty list."

        reason = _check_semantic_chart_gate("generate_metric_charts", require_common_depth=True)
        if reason:
            return (
                f"**Semantic routing check failed:** {reason}\n\n"
                "Complete the required semantic discovery steps, then retry "
                "`generate_metric_charts`."
            )

        from cerebro_mcp.tools.semantic.semantic import execute_metric_query

        role = agent_role or "unknown"
        state.record_semantic_tool_call("generate_metric_charts", execution=True)
        succeeded = []
        failed = []

        for i, spec in enumerate(charts, 1):
            metric_names = spec.get("metrics", [])
            if not metric_names:
                failed.append((i, spec.get("title", "untitled"), "No metrics provided"))
                continue
            request_error = _validate_semantic_chart_request(
                chart_type=spec.get("chart_type", "line"),
                dimensions=spec.get("dimensions"),
            )
            if request_error:
                failed.append((i, spec.get("title", "untitled"), request_error))
                continue

            result = execute_metric_query(
                ch=ch,
                research_store=None,
                metrics=metric_names,
                dimensions=spec.get("dimensions"),
                filters=spec.get("filters"),
                order_by=spec.get("order_by"),
                limit=spec.get("limit", 100),
                agent_role=role,
            )
            if isinstance(result, str):
                failed.append((i, spec.get("title", "untitled"), result))
                continue

            resolved_x_field, resolved_y_field, field_error = _resolve_semantic_chart_fields(
                chart_type=spec.get("chart_type", "line"),
                result=result,
                requested_dimensions=spec.get("dimensions"),
                x_field=spec.get("x_field", ""),
                y_field=spec.get("y_field", ""),
                change_field=spec.get("change_field", ""),
            )
            if field_error:
                failed.append((i, spec.get("title", "untitled"), field_error))
                continue

            chart_result = _register_chart_from_dataset(
                columns=result.columns,
                rows=result.rows,
                sql=result.sql,
                database=result.database,
                chart_type=spec.get("chart_type", "line"),
                x_field=resolved_x_field,
                y_field=resolved_y_field,
                change_field=spec.get("change_field", ""),
                series_field=spec.get("series_field", ""),
                title=spec.get("title", ""),
                elapsed_seconds=result.elapsed_seconds,
                source="semantic",
                return_metadata_only=True,
            )
            if chart_result.startswith("OK|"):
                parts = chart_result.split("|")
                succeeded.append({
                    "index": i,
                    "chart_id": parts[1],
                    "chart_type": parts[2],
                    "title": parts[3],
                    "data_points": parts[4],
                    "query_time": parts[5],
                })
            else:
                failed.append((i, spec.get("title", "untitled"), chart_result))

        observe_semantic_tool_call(
            tool_name="generate_metric_charts",
            status="error" if failed and not succeeded else "success",
            agent_role=role,
            entrypoint="semantic_chart",
        )

        total = len(charts)
        ok_count = len(succeeded)
        lines = [f"Generated {ok_count}/{total} semantic charts:\n"]
        lines.append("| # | Chart ID | Title | Type | Points | Time |")
        lines.append("|---|----------|-------|------|--------|------|")
        for s in succeeded:
            lines.append(
                f"| {s['index']} | {s['chart_id']} | {s['title']} "
                f"| {s['chart_type']} | {s['data_points']} "
                f"| {s['query_time']} |"
            )

        if failed:
            lines.append(f"\nFailed ({len(failed)}):")
            for idx, title, err in failed:
                lines.append(f"- Input #{idx} (\"{title}\"): {err}")

        chart_list = ", ".join(_chart_registry.keys())
        lines.append(
            f"\n**Registered charts ({len(_chart_registry)}):** {chart_list}\n"
            "**Next step:** If the user asked for a report/dashboard/analysis, "
            "call `generate_report(title, content_markdown)` with `{{chart:ID}}` "
            "placeholders. For a plain chart request these charts are the "
            "deliverable — present them and stop."
        )
        return "\n".join(lines)

    @mcp.tool()
    def list_charts() -> str:
        """List all charts in the registry with IDs, titles, and types.

        Deprecated: use `list(kind="charts")`.

        Returns:
            Table of registered charts available for use in generate_report.
        """
        return list_charts_impl()

    @mcp.tool(meta=_ui_tool_meta(REPORT_URI))
    @_offloaded
    def generate_report(
        title: str,
        content_markdown: str,
        subtitle: str | None = None,
        summary_numbers: list[dict] | None = None,
        explain_context: bool = False,
    ) -> CallToolResult:
        """Create an interactive report rendered as a native UI in the chat client.

        Use this ONLY when the user explicitly asked for a report, dashboard,
        deep-dive, write-up, or written analysis. Do NOT call it for a plain
        "show me / plot X" chart request — return the chart(s) from
        `generate_charts` / `quick_chart` and stop. When a report IS wanted,
        call `generate_charts` (batch) first to create all charts in one call,
        then call this tool with markdown containing {{chart:CHART_ID}}
        placeholders.

        For GUI clients (Claude Desktop, VS Code): renders as interactive iframe.
        For terminal clients (Claude Code): opens report in default browser.

        LAYOUT RULES (ENFORCED — report will be rejected without proper layout):
        - KPI/counter charts (numberDisplay) MUST be in {{grid:3}} or {{grid:4}} rows
        - Breakdown charts (bar/pie) should pair in {{grid:2}}
        - Trend charts (line/area) go full-width between grid groups
        - Text commentary goes BETWEEN chart groups, not lumped at the end

        Example layout:
            ## Key Metrics
            {{grid:3}}
            {{chart:chart_1}}
            {{chart:chart_2}}
            {{chart:chart_3}}
            {{/grid}}

            Commentary about the KPI trends.

            {{chart:chart_4}}

            ## Breakdown
            {{grid:2}}
            {{chart:chart_5}}
            {{chart:chart_6}}
            {{/grid}}

        After this tool returns, summarize key insights and ask if the user
        wants the HTML exported (via `export_report`) or converted to docx/pdf/pptx.
        Do NOT echo the report markdown or {{chart:...}} placeholders.
        SQL queries are embedded in the report UI (click </> on each chart).

        Args:
            title: Report title displayed in the header.
            content_markdown: Markdown content with {{chart:CHART_ID}} placeholders.
            subtitle: Optional one-line subtitle shown under the title in the header.
            summary_numbers: Optional list of up to 6 leading KPIs. Each entry is
                {"label": "...", "value": "...", "hint": "..."}. Rendered as a
                table at the top of the report body (Metric | Value | Change).
            explain_context: When True, each chart card carries a "What this shows"
                block (dbt model rationale + key column meanings) backfilled from
                the chart's SQL for any chart that lacks one.

        Returns:
            Interactive UI resource rendered natively in the chat client.
        """
        try:
            report = create_report_artifact(
                title,
                content_markdown,
                enforce_quality_gate=True,
                reset_session_state=True,
                subtitle=subtitle,
                summary_numbers=summary_numbers,
                explain_context=explain_context,
            )

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=report["reply_text"],
                        annotations=Annotations(
                            audience=["assistant"],
                            priority=1.0,
                        ),
                    ),
                    TextContent(
                        type="text",
                        text=(
                            f"Report generated: {title} "
                            f"({report['chart_count']} charts). "
                            f"Report ID: `{report['report_id'][:8]}`"
                        ),
                    ),
                ],
                structuredContent=report["structured"],
                _meta=_result_ui_meta(report["report_id"]),
            )

        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error: {e}")],
                isError=True,
            )

    @mcp.tool(meta=_ui_tool_meta(REPORT_URI))
    @_offloaded
    def generate_research_report(
        title: str,
        deck: str,
        content_markdown: str,
        authors: list[str] | None = None,
        published_date: str | None = None,
        category: str | None = None,
        key_takeaways: list[str] | None = None,
        footnotes: list[dict] | None = None,
    ) -> CallToolResult:
        """Create a long-form research report in the Anthropic-essay style.

        Use this (instead of `generate_report`) when the user asks for a
        whitepaper, research essay, narrative analysis, or long-form article
        rather than an analytical dashboard. Reuses the same chart registry
        and enforcement gates — call `generate_charts` (batch) first.

        The layout adds: display title + deck (sub-headline), authors, date,
        reading time, key-takeaways callout, floating TOC from `##` headings,
        full-bleed figures with captions, pull-quotes, sidebars, and footnotes.

        Extra markdown directives (only parsed in this tool):
            {{figure:chart_id caption="..." source="..."}}
            {{pullquote}} ... {{/pullquote}}
            {{callout kind=key_takeaway}} ... {{/callout}}
            {{sidebar title="..."}} ... {{/sidebar}}
            [^fnid] inline reference, then at end of doc:
            [^fnid]: footnote text

        Standard `{{chart:CHART_ID}}` and `{{grid:N}}` still work for inline
        dashboard-style chart groupings inside the essay.

        Args:
            title: Report title (display serif).
            deck: Sub-headline / abstract (1 sentence, ≤240 chars).
            content_markdown: Essay body. Use `##` for section headings.
            authors: Author names (optional).
            published_date: ISO date (YYYY-MM-DD). Defaults to today.
            category: Category chip (e.g. "DeFi Research").
            key_takeaways: 3–6 bullet-style takeaways rendered at the top.
            footnotes: [{id, text}] list, or plain [text, text, ...].

        Returns:
            Interactive UI resource rendered as a long-form research essay.
        """
        try:
            report = create_research_report_artifact(
                title=title,
                deck=deck,
                content_markdown=content_markdown,
                authors=authors,
                published_date=published_date,
                category=category,
                key_takeaways=key_takeaways,
                footnotes=footnotes,
                enforce_quality_gate=True,
                reset_session_state=True,
            )

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=report["reply_text"],
                        annotations=Annotations(
                            audience=["assistant"],
                            priority=1.0,
                        ),
                    ),
                    TextContent(
                        type="text",
                        text=(
                            f"Research report generated: {title} "
                            f"({report['chart_count']} charts). "
                            f"Report ID: `{report['report_id'][:8]}`"
                        ),
                    ),
                ],
                structuredContent=report["structured"],
                _meta=_result_ui_meta(report["report_id"]),
            )

        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error: {e}")],
                isError=True,
            )

    @mcp.tool(meta=_ui_tool_meta(REPORT_URI))
    @_offloaded
    def generate_case_study_report(
        title: str,
        deck: str,
        content_markdown: str,
        key_points: list[str],
        authors: list[str] | None = None,
        published_date: str | None = None,
        category: str | None = None,
        hero_image: str | None = None,
        hero_chart_id: str | None = None,
        cta: dict | None = None,
    ) -> CallToolResult:
        """Create a scrollytelling case-study report (marketing / growth pitch style).

        Use this (instead of `generate_report` or `generate_research_report`)
        when the deliverable is a marketing case study, customer story,
        growth pitch, or narrative-first investor update — i.e. when the
        goal is *persuasion* with scroll-triggered visuals, not a whitepaper
        or analytical dashboard. Reuses the same chart registry and
        enforcement gates — call `generate_charts` (batch) first.

        Layout features: a hero header (chart or image), sticky visuals with
        scrolling narrative (`{{scene}}`), stepped chart animations
        (`{{step}}`), progressive bullet reveals (`{{reveal}}`), full-bleed
        imagery (`{{image}}`), and an end-of-page CTA (`{{cta}}` or the
        structured `cta` arg).

        Extra markdown directives (only parsed in this tool):

            {{scene chart="chart_1" side="left"}}
            Narrative paragraphs...

            {{step chart="chart_1" state="highlight=GNO"}}
            Beat 1 narrative.
            {{/step}}

            {{step chart="chart_1" state="highlight=ETH"}}
            Beat 2 narrative.
            {{/step}}
            {{/scene}}

            {{reveal}}
            - First bullet
            - Second bullet
            {{/reveal}}

            {{image src="https://..." caption="..." full_bleed=true}}

            {{cta label="Book a call" href="https://..."}}

        Standard `{{chart:CHART_ID}}` and `{{grid:N}}` still work for inline
        chart placements between scenes.

        Args:
            title: Report title.
            deck: Sub-headline / one-line thesis (≤240 chars).
            content_markdown: Narrative body. Use `##` for section headings.
            key_points: 3–6 punchline takeaways rendered at the top.
            authors: Author names (optional).
            published_date: ISO date (YYYY-MM-DD). Defaults to today.
            category: Category chip (e.g. "Customer Story").
            hero_image: Absolute URL or data URI for a hero image in the header.
            hero_chart_id: Chart ID to render in the header (mutually exclusive with hero_image).
            cta: {"label": "...", "href": "..."} for the footer CTA.

        Returns:
            Interactive UI resource rendered as a scrollytelling case study.
        """
        try:
            report = create_case_study_artifact(
                title=title,
                deck=deck,
                content_markdown=content_markdown,
                hero_image=hero_image,
                hero_chart_id=hero_chart_id,
                authors=authors,
                published_date=published_date,
                category=category,
                cta=cta,
                key_points=key_points,
                enforce_quality_gate=True,
                reset_session_state=True,
            )

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=report["reply_text"],
                        annotations=Annotations(
                            audience=["assistant"],
                            priority=1.0,
                        ),
                    ),
                    TextContent(
                        type="text",
                        text=(
                            f"Case study generated: {title} "
                            f"({report['chart_count']} charts). "
                            f"Report ID: `{report['report_id'][:8]}`"
                        ),
                    ),
                ],
                structuredContent=report["structured"],
                _meta=_result_ui_meta(report["report_id"]),
            )

        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error: {e}")],
                isError=True,
            )

    # --- Report Reopen & List ---

    @mcp.tool(meta=_ui_tool_meta(REPORT_URI))
    @_offloaded
    def open_report(report_ref: str) -> CallToolResult:
        """Reopen a previously generated report by its ID.

        Accepts the full UUID or the 8-character short ID shown in report
        summaries. Returns the same interactive UI resource as generate_report.

        CRITICAL: After this tool returns, your reply MUST include:
        1. The file:// report link (copy it verbatim from the response)
        2. A brief summary
        Do NOT echo the report markdown.

        Args:
            report_ref: Full report UUID or 8-character prefix.

        Returns:
            Interactive UI resource of the saved report.
        """
        def _build_result(title: str, file_uri: str, report_id: str,
                          structured: dict | None, extra: str = "") -> CallToolResult:
            content_items = []
            if file_uri:
                content_items.append(TextContent(
                    type="text",
                    text=f"**Report:** {title}\n\n**Report link:** [Open Report]({file_uri})",
                    annotations=Annotations(
                        audience=["assistant"],
                        priority=1.0,
                    ),
                ))
                content_items.append(TextContent(type="text", text=file_uri))
                if structured is not None:
                    structured["file_uri"] = file_uri
            metadata = (
                f"Report ID: `{report_id[:8]}`"
                + (f"\n\n{extra}" if extra else "")
            )
            content_items.append(TextContent(type="text", text=metadata))
            return CallToolResult(
                content=content_items,
                structuredContent=structured,
                _meta=_result_ui_meta(report_id),
            )

        # Try in-memory cache first (full UUID)
        with _REPORT_LOCK:
            cached = _REPORT_CACHE.get(report_ref)
        if cached:
            file_uri = _get_report_link(cached["path"]) if cached.get("path") else ""
            return _build_result(
                cached.get("title", "Report"), file_uri, report_ref,
                cached.get("structured"),
            )

        # Try cache by prefix
        with _REPORT_LOCK:
            prefix_matches = [
                (rid, data) for rid, data in _REPORT_CACHE.items()
                if rid.startswith(report_ref)
            ]
        if len(prefix_matches) == 1:
            rid, data = prefix_matches[0]
            file_uri = _get_report_link(data["path"]) if data.get("path") else ""
            return _build_result(
                data.get("title", "Report"), file_uri, rid,
                data.get("structured"),
            )
        if len(prefix_matches) > 1:
            ids = ", ".join(f"`{rid[:8]}`" for rid, _ in prefix_matches)
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Ambiguous report reference `{report_ref}`. Matches: {ids}",
                )],
            )

        # Fallback: disk lookup
        disk_path = _find_report_on_disk(report_ref)
        if disk_path:
            html = disk_path.read_text(encoding="utf-8")
            full_id = _extract_report_id_from_path(disk_path)
            file_uri = _get_report_link(disk_path)
            # Try to extract structured data from embedded JSON
            structured = _extract_structured_from_html(html)
            return _build_result(
                structured.get("title", "Report") if structured else "Report",
                file_uri, full_id, structured,
                extra=f"Reopened from disk: `{disk_path.name}`",
            )

        return CallToolResult(
            content=[TextContent(
                type="text",
                text=f"Report `{report_ref}` not found in cache or on disk. Use `list_reports` to see available reports.",
            )],
        )

    @mcp.tool()
    def list_reports(limit: int = 20) -> str:
        """List previously generated reports saved on disk.

        Deprecated: use `list(kind="reports")`.

        Returns a table of saved reports sorted newest-first with file:// links.
        Use `open_report(report_id)` to reopen any report.

        Args:
            limit: Maximum number of reports to show (default 20).

        Returns:
            Table of saved reports with IDs, dates, sizes, and links.
        """
        return list_reports_impl(limit)

    # --- Export Report as HTML ---

    @mcp.tool()
    def export_report(report_ref: str = "") -> str:
        """Export a report as standalone HTML that can be saved and opened in any browser.

        When the server runs in SSE mode (or REPORT_BASE_URL is configured),
        returns a download URL instead of the raw HTML. Otherwise returns
        the file path for local access.

        Args:
            report_ref: Report ID (full UUID or 8-char prefix). Empty = latest report.

        Returns:
            Download URL, file path, or full HTML string of the standalone report.
        """
        try:
            html, report_id, disk_path = _resolve_report(report_ref)
        except ValueError as exc:
            return str(exc)

        if html is None:
            if report_ref:
                return (
                    f"Report `{report_ref}` not found. "
                    f"Use `list_reports` to see available reports."
                )
            return "No reports found. Generate a report first with `generate_report`."

        # If HTTP endpoint is available, return a download URL
        download_url = _get_report_download_url(report_id)
        if download_url:
            size_kb = len(html) / 1024
            lines = [
                f"Report ready for download ({size_kb:.0f} KB):\n",
                f"**Download URL:** {download_url}\n",
                "Open this URL in a browser to view the full interactive report.",
                f"Report ID: `{report_id[:8]}`",
            ]
            if disk_path:
                lines.append(f"Server path: `{disk_path}`")
            return "\n".join(lines)

        # stdio fallback: return disk path (HTML is too large for tool response)
        from cerebro_mcp.config import settings

        if disk_path:
            size_kb = len(html) / 1024
            if len(html) > settings.effective_tool_result_max_chars:
                return (
                    f"Report is {size_kb:,.0f} KB (exceeds tool response limit). "
                    f"Saved at: `{disk_path}`\n\n"
                    f"Run the server in SSE mode (`--sse`) or set "
                    f"`REPORT_BASE_URL` to enable HTTP downloads."
                )
            return html

        return html

    # --- MCP App Resource ---

    @mcp.resource(
        REPORT_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_report_app() -> str:
        """Serves the MCP App HTML for interactive report rendering.

        Prefers the LATEST generated report's standalone HTML — with its data
        EMBEDDED — so hosts that render this resource but never complete the
        ext-apps handshake (e.g. Claude Desktop) still display real charts.
        The React app reads the embedded `<script id="report-data">` blob
        first; handshake-capable hosts lose nothing. Falls back to the
        data-less Vite bundle when no report exists yet.
        """
        try:
            html, _rid, _path = _resolve_report("")
        except ValueError:
            html = None
        if html:
            return html
        return _get_report_html()

    @mcp.resource(
        VISUALIZATION_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_latest_visualization() -> str:
        """Serve the visualization rendered by the LAST chart-tool call.

        `generate_charts` / `quick_chart` carry this URI as tool-level meta.
        When the last call auto-rendered a visual answer (chart/answer tier),
        this returns its standalone HTML with the data EMBEDDED — actual
        charts, zero handshake. Freshness-gated so a report-tier batch call
        (which renders nothing itself) shows a neutral placeholder instead of
        a stale visualization.
        """
        with _REPORT_LOCK:
            rid = _LAST_VISUAL.get("report_id")
            created = _LAST_VISUAL.get("created_at")
        if rid and created:
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age <= _LAST_VISUAL_FRESH_SECONDS:
                try:
                    html, _r, _p = _resolve_report(rid)
                except ValueError:
                    html = None
                if html:
                    return html
        return (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"color-scheme\" content=\"light dark\"></head>"
            "<body style=\"font-family:system-ui,sans-serif;display:flex;"
            "align-items:center;justify-content:center;min-height:120px;"
            "margin:0;\"><p style=\"font-size:14px;opacity:.7;max-width:44em;"
            "text-align:center;\">Charts are registered but no standalone "
            "visualization was rendered by this call. For a report-tier flow "
            "the report renders next; otherwise use the Open link in the "
            "chat message.</p></body></html>"
        )

    @mcp.resource(
        REPORT_URI + "/{report_id}",
        mime_type="text/html;profile=mcp-app",
    )
    def serve_report_instance(report_id: str) -> str:
        """Serve ONE report as standalone HTML with its data EMBEDDED.

        Unlike the data-less `ui://cerebro/report` bundle (which depends on
        the ext-apps handshake to receive `structuredContent`), this returns
        the same self-contained HTML that is written to disk: the React app
        reads the embedded `<script id="report-data">` blob and renders with
        zero handshake — so it works in hosts where the handshake never
        completes. Tool results reference it via result-level `_meta`
        (`ui.resourceUri`). Accepts a full UUID or 8-char prefix.
        """
        try:
            html, _rid, _path = _resolve_report(report_id)
        except ValueError:
            html = None
        if html:
            return html
        # Unknown/expired id: fall back to the generic bundle (its own
        # timeout fallback explains how to open the report).
        return _get_report_html()
