"""Report Studio mini app — the report Archive (landing tab) plus a catalog
of benchmarked instruction templates whose agent runs produce those reports.

The template catalog itself is COMPILE-TIME data shipped inside the UI bundle
(``ui/src/mini-apps/report-studio/model/catalog.gen.json``, generated from
``catalog/templates/*.md`` by ``make gen-catalog``); the backend serves only
the archive surface. There is deliberately no construction/composer surface.

Archive source of truth: the flat HTML files in the report directory
(``CEREBRO_REPORT_DIR``, default ``~/.cerebro/reports``). The gallery lists
filename-derived metadata only (cheap — no multi-MB HTML reads); opening an
entry extracts the embedded ``<script id="report-data">`` payload for a
native in-app preview.

TRUST MODEL: report files are process-global with no per-user owner.
Mutations (delete/rename) are gated by
``settings.REPORT_STUDIO_ALLOW_MUTATIONS`` — shared SSE deployments should
turn that off.
"""

from __future__ import annotations

import importlib.resources
import logging
import os
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.models.mini_app import MiniAppPayload
from cerebro_mcp.tools.visualization import charts as _charts
from cerebro_mcp.tools.visualization import mini_apps, web_apps

logger = logging.getLogger(__name__)

REPORT_STUDIO_APP_ID = "report_studio"
REPORT_STUDIO_URI = "ui://cerebro/report_studio"
DEFAULT_TITLE = "Report Studio"

_ARCHIVE_MAX_LIMIT = 200
_MAX_TITLE = 200


# --- Bundled React UI ---
_BUNDLED_HTML: str | None = None

_FALLBACK_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Report Studio</title></head><body><div id=\"root\">"
    "<noscript>Report Studio — UI bundle not built "
    "(run <code>make build-ui-report-studio</code>).</noscript></div>"
    "</body></html>"
)


def get_report_studio_html() -> str:
    """Load the Vite-built single-file bundle from the static package."""
    global _BUNDLED_HTML
    if _BUNDLED_HTML is None:
        try:
            _BUNDLED_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/report_studio.html")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            logger.warning(
                "report_studio.html bundle missing — serving fallback shell"
            )
            _BUNDLED_HTML = _FALLBACK_HTML
    return _BUNDLED_HTML


# ---------------------------------------------------------------------------
# Archive helpers
# ---------------------------------------------------------------------------


def _report_dir_no_create() -> Path:
    """The configured report dir WITHOUT creating it — a read-only archive
    listing must not mkdir."""
    return Path(
        os.environ.get("CEREBRO_REPORT_DIR", "~/.cerebro/reports")
    ).expanduser()


def _title_hint(path: Path) -> str:
    """Lossy 3-word title HINT from the filename slug (parts[-2] — the only
    safe index: case_study stems have 6 segments). The full title lives in
    the embedded report-data and arrives with the preview."""
    parts = path.stem.split("_")
    return parts[-2].replace("-", " ") if len(parts) >= 5 else path.stem


def _archive_page(
    *,
    query: str = "",
    kind: str = "",
    sort: str = "newest",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), _ARCHIVE_MAX_LIMIT))
    offset = max(0, int(offset))
    q = (query or "").strip().lower()

    report_dir = _report_dir_no_create()
    entries: list[dict[str, Any]] = []
    warning_count = 0
    if report_dir.exists():
        for path in _charts._iter_report_files(report_dir):
            if not _charts._is_managed_report_file(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                warning_count += 1
                continue
            rid = _charts._extract_report_id_from_path(path)
            entry = {
                "id": rid,
                "short_id": rid[:8],
                "kind": _charts._report_kind_from_path(path),
                # Lossy filename hint — NOT the full title (see _title_hint).
                "title_hint": _title_hint(path),
                "created_utc": stat.st_mtime,
                "size_kb": round(stat.st_size / 1024, 1),
                "filename": path.name,
                "link": _charts._get_report_link(path),
            }
            entries.append(entry)

    if kind:
        entries = [e for e in entries if e["kind"] == kind]
    if q:
        entries = [
            e
            for e in entries
            if q in e["title_hint"].lower()
            or q in e["filename"].lower()
            or q in e["id"]
        ]

    if sort == "oldest":
        entries.sort(key=lambda e: (e["created_utc"], e["filename"]))
    elif sort == "title":
        entries.sort(key=lambda e: (e["title_hint"], -e["created_utc"]))
    else:  # newest
        sort = "newest"
        entries.sort(key=lambda e: (-e["created_utc"], e["filename"]))

    total = len(entries)
    page = entries[offset : offset + limit]
    return {
        "reports": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "query": query,
        "kind": kind,
        "sort": sort,
        "warning_count": warning_count,
    }


def _entry_payload(report_id: str, path: Path) -> dict[str, Any]:
    """Full structured payload of one report for the native preview."""
    try:
        stat = path.stat()
        html = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"Could not read report file: {exc}"}
    file_info = {
        "path": str(path),
        "filename": path.name,
        "size_kb": round(stat.st_size / 1024, 1),
        "created_utc": stat.st_mtime,
        "link": _charts._get_report_link(path),
    }
    structured = _charts._extract_structured_from_html(html)
    if not structured:
        return {
            "ok": False,
            "error": "unreadable report data (embedded payload missing or corrupt)",
            "id": report_id,
            "file": file_info,
        }
    return {
        "ok": True,
        "id": report_id,
        "kind": _charts._report_kind_from_path(path),
        "title": structured.get("title", ""),
        "subtitle": structured.get("subtitle", ""),
        "timestamp": structured.get("timestamp", ""),
        "presentation_mode": structured.get("presentation_mode", "report"),
        "charts": structured.get("charts", {}),
        "sections_html": structured.get("sections_html", ""),
        "queries": structured.get("queries", {}),
        "file": file_info,
    }


def _mutations_blocked() -> dict[str, Any] | None:
    if not settings.REPORT_STUDIO_ALLOW_MUTATIONS:
        return {
            "ok": False,
            "error": (
                "Report Studio mutations are disabled on this server "
                "(REPORT_STUDIO_ALLOW_MUTATIONS=false)."
            ),
        }
    return None


# ---------------------------------------------------------------------------
# Composer helpers (shared across report | research | case_study kinds)
# ---------------------------------------------------------------------------

_MAX_DECK = 240
_MIN_HIGHLIGHTS = 3  # key_takeaways (research) / key_points (case study)
_MAX_HIGHLIGHTS = 6


def register_report_studio_tools(
    mcp: Any, ch: ClickHouseManager | None = None
) -> None:
    """Register the Report Studio mini app (resource + tools + web app)."""

    mini_apps.register_app(
        REPORT_STUDIO_APP_ID, title=DEFAULT_TITLE, resource_uri=REPORT_STUDIO_URI
    )

    @mcp.resource(REPORT_STUDIO_URI, mime_type="text/html;profile=mcp-app")
    def serve_report_studio_app() -> str:
        """Serve the bundled Report Studio HTML (MCP-App resource)."""
        return get_report_studio_html()

    @mcp.tool(
        meta={
            "ui": {"resourceUri": REPORT_STUDIO_URI},
            "ui/resourceUri": REPORT_STUDIO_URI,
        }
    )
    def open_report_studio(
        query: str = "", kind: str = "", report: str = ""
    ) -> CallToolResult:
        """Open the Report Studio: browse and manage the archive of generated
        reports, plus a catalog of benchmarked instruction templates
        (copyable prompts with measured delivery time, tokens, and cost).

        Call when the user asks for report/analysis templates, example
        prompts, "what can I ask", or to browse/manage their report archive.

        Args:
            query: Optional archive search (matches filename slug/id).
            kind: Optional filter — report | research | case_study.
            report: Optional report ref (UUID or 8+ hex prefix) to open
                straight into the preview.
        """
        view_id = mini_apps.create_view(REPORT_STUDIO_APP_ID, DEFAULT_TITLE)
        archive = _archive_page(query=query, kind=kind)
        entry = None
        warnings: list[str] = []
        if report:
            try:
                rid, path = _charts.resolve_report_id(report)
                entry = _entry_payload(rid, path)
                if not entry.get("ok"):
                    warnings.append(str(entry.get("error")))
            except _charts.ReportRefError as exc:
                warnings.append(str(exc))
        allow = settings.REPORT_STUDIO_ALLOW_MUTATIONS
        payload = MiniAppPayload(
            type="INITIAL_LOAD",
            view_id=view_id,
            app_id=REPORT_STUDIO_APP_ID,
            title=DEFAULT_TITLE,
            status="ready",
            datasets={},
            view_state={
                "screen": "preview" if entry and entry.get("ok") else "archive",
                "archive": archive,
                "selected_entry": entry,
                "report_dir": str(_report_dir_no_create()),
                "mutations_enabled": allow,
            },
            provenance={"source": "report_archive"},
            warnings=warnings,
        )
        mini_apps.set_view_state(view_id, payload.view_state)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Report Studio opened — {archive['total']} report(s) in the "
                "archive."
            ),
        )

    # ------------------------------------------------------------------
    # App-only: archive reads
    # ------------------------------------------------------------------

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def list_report_archive(
        query: str = "",
        kind: str = "",
        sort: str = "newest",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """[App-only] Page of the report archive (filename metadata only).

        Hidden from the model-facing tool list. ``sort`` ∈ newest|oldest|
        title; ``kind`` ∈ report|research|case_study. Titles are lossy
        filename hints — search matches slug/filename/id only.
        """
        return _archive_page(
            query=query, kind=kind, sort=sort, offset=offset, limit=limit
        )

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def get_report_archive_entry(report_ref: str) -> dict[str, Any]:
        """[App-only] Full structured payload of one report (native preview:
        title, charts as ECharts options, sections_html, queries, file)."""
        try:
            rid, path = _charts.resolve_report_id(report_ref)
        except _charts.ReportRefError as exc:
            return {"ok": False, "error": str(exc), "candidates": exc.candidates}
        return _entry_payload(rid, path)

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def get_report_export_info(report_ref: str) -> dict[str, Any]:
        """[App-only] Paths/URLs for exporting a report. Conversion itself
        (docx/pdf/pptx) is agent-side — the UI surfaces a copyable path and
        an 'ask the agent' hint."""
        try:
            rid, path = _charts.resolve_report_id(report_ref)
            stat = path.stat()
        except _charts.ReportRefError as exc:
            return {"ok": False, "error": str(exc), "candidates": exc.candidates}
        except OSError as exc:
            return {"ok": False, "error": f"Could not stat report file: {exc}"}
        return {
            "ok": True,
            "id": rid,
            "download_url": _charts._get_report_download_url(rid) or None,
            "path": str(path),
            "size_kb": round(stat.st_size / 1024, 1),
            "hint": (
                f'Ask the agent: export_report("{rid[:8]}") for the HTML, '
                "or ask it to convert the report to docx/pdf/pptx."
            ),
        }

    # ------------------------------------------------------------------
    # App-only: mutations (gated)
    # ------------------------------------------------------------------

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def delete_report_archive_entry(
        report_ref: str, confirm: bool = False
    ) -> dict[str, Any]:
        """[App-only] Delete a report file — two-step confirm.

        First call (confirm=False) resolves the ref and returns the FULL id
        + filename for the confirmation dialog; the UI re-calls with that
        full id and confirm=True.
        """
        if (blocked := _mutations_blocked()) is not None:
            return blocked
        try:
            rid, path = _charts.resolve_report_id(report_ref)
            if not confirm:
                return {
                    "ok": False,
                    "needs_confirm": True,
                    "id": rid,
                    "filename": path.name,
                    "title_hint": _title_hint(path),
                }
            with _charts._REPORT_FS_LOCK:
                if not _charts._is_managed_report_file(path):
                    return {
                        "ok": False,
                        "error": f"{path.name} is not a managed report file.",
                    }
                path.unlink()
        except _charts.ReportRefError as exc:
            return {"ok": False, "error": str(exc), "candidates": exc.candidates}
        except OSError as exc:
            return {"ok": False, "error": f"Delete failed: {exc}"}
        _charts.forget_report(rid)
        return {"ok": True, "id": rid, "deleted": path.name}

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def rename_report_archive_entry(
        report_ref: str, title: str
    ) -> dict[str, Any]:
        """[App-only] Retitle a report: rewrites the embedded report-data
        title AND the filename slug (atomic tmp+replace; UUID unchanged so
        old references keep resolving; mtime preserved)."""
        if (blocked := _mutations_blocked()) is not None:
            return blocked
        title = (title or "").strip()
        if not title or len(title) > _MAX_TITLE:
            return {
                "ok": False,
                "error": f"Title must be 1-{_MAX_TITLE} characters.",
            }
        try:
            rid, path = _charts.resolve_report_id(report_ref)
            with _charts._REPORT_FS_LOCK:
                if not _charts._is_managed_report_file(path):
                    return {
                        "ok": False,
                        "error": f"{path.name} is not a managed report file.",
                    }
                html = path.read_text(encoding="utf-8")
                structured = _charts._extract_structured_from_html(html)
                if not structured:
                    return {"ok": False, "error": "Report data block is unreadable."}
                structured["title"] = title
                new_blob = _charts._serialize_report_data(structured)
                new_html, n = _charts._REPORT_DATA_RE.subn(
                    lambda m: m.group(1) + new_blob + m.group(3), html, count=1
                )
                if n != 1:
                    return {"ok": False, "error": "Report data block is unreadable."}
                stat = path.stat()
                parts = path.stem.split("_")
                # parts[-2] is the ONLY safe slug index (case_study stems
                # have 6 segments — never index from the front).
                parts[-2] = _charts._slugify_title(
                    title, _charts._report_kind_from_path(path)
                )
                new_path = path.with_name("_".join(parts) + ".html")
                if new_path != path and new_path.exists():
                    parts[-2] += "-2"
                    new_path = path.with_name("_".join(parts) + ".html")
                _charts._atomic_write_report(new_path, new_html)
                if new_path != path:
                    path.unlink()  # only after the replacement landed
                os.utime(new_path, (stat.st_atime, stat.st_mtime))
        except _charts.ReportRefError as exc:
            return {"ok": False, "error": str(exc), "candidates": exc.candidates}
        except (OSError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": f"Rename failed: {exc}"}
        _charts.forget_report(rid)
        return {
            "ok": True,
            "id": rid,
            "title": title,
            "filename": new_path.name,
            "link": _charts._get_report_link(new_path),
        }

    for name in (
        "list_report_archive",
        "get_report_archive_entry",
        "get_report_export_info",
        "delete_report_archive_entry",
        "rename_report_archive_entry",
    ):
        mini_apps.mark_app_only(name)

    web_apps.register_web_app(
        app_id=REPORT_STUDIO_APP_ID,
        open_tool="open_report_studio",
        html_loader=get_report_studio_html,
        title="Report Studio",
        description=(
            "Browse benchmarked instruction templates — copy a ready-made "
            "prompt with measured delivery time, tokens, and cost, and hand "
            "it to the agent to execute. Includes the report archive."
        ),
        icon="▥",
        tools={
            "open_report_studio": open_report_studio,
            "list_report_archive": list_report_archive,
            "get_report_archive_entry": get_report_archive_entry,
            "get_report_export_info": get_report_export_info,
            "delete_report_archive_entry": delete_report_archive_entry,
            "rename_report_archive_entry": rename_report_archive_entry,
        },
    )


__all__ = [
    "REPORT_STUDIO_APP_ID",
    "REPORT_STUDIO_URI",
    "register_report_studio_tools",
    "get_report_studio_html",
]
