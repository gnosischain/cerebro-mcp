"""MCP tools for publishing Grafana dashboards backed by ClickHouse.

Registration is gated by `settings.GRAFANA_TOOLS_ENABLED`: when off, no tools
are registered. The LLM declares a `GrafanaDashboardDef` (intent); the compiler
produces deterministic Grafana JSON; publishing is idempotent by UID and
tag-guarded so we never silently clobber a human-edited dashboard.

Sync (`def`, not `async def`) per repo convention — the codebase uses
`requests`, not an async HTTP client.
"""
from __future__ import annotations

import re
import time

import requests

from cerebro_mcp.config import settings
from cerebro_mcp.grafana.compiler import (
    build_layout_sketch,
    compile_grafana_dashboard,
    sql_for_validation,
)
from cerebro_mcp.grafana.models import GrafanaDashboardDef
from cerebro_mcp.safety import validate_query

# Substrings to redact from any Grafana response body before returning it.
_SECRET_RE = re.compile(
    r"(Authorization:\s*\S+|Bearer\s+\S+|glsa_\S+)", re.IGNORECASE
)


def _scrub(body: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", body or "")[:500]


def _base_url() -> str:
    return settings.GRAFANA_URL.rstrip("/")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.GRAFANA_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _fully_configured() -> bool:
    return bool(
        settings.GRAFANA_URL
        and settings.GRAFANA_API_TOKEN
        and settings.GRAFANA_CLICKHOUSE_DATASOURCE_UID
    )


def _validate(dashboard: GrafanaDashboardDef) -> tuple[bool, str]:
    """Validate every panel's SQL (macro-substituted) and collect warnings.

    Returns (ok, message). On failure, message is the first hard error. On
    success, message holds any non-fatal warnings (role/format notes).
    """
    warnings: list[str] = []
    for panel in dashboard.panels:
        try:
            check_sql = sql_for_validation(panel.sql_query)
        except ValueError as exc:
            return False, f"Panel '{panel.title}': {exc}"
        ok, err = validate_query(check_sql, max_length=settings.MAX_QUERY_LENGTH)
        if not ok:
            return False, f"Panel '{panel.title}': SQL rejected — {err}"

    return True, "; ".join(warnings)


def _fetch_dashboard(uid: str) -> dict | None:
    """GET a dashboard by UID. Returns parsed JSON, or None on 404."""
    resp = requests.get(
        f"{_base_url()}/api/dashboards/uid/{uid}",
        headers=_headers(),
        timeout=settings.GRAFANA_REQUEST_TIMEOUT_SECONDS,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _post_dashboard(payload: dict, uid: str) -> str:
    try:
        resp = requests.post(
            f"{_base_url()}/api/dashboards/db",
            headers=_headers(),
            json=payload,
            timeout=settings.GRAFANA_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        body = _scrub(exc.response.text if exc.response is not None else "")
        return f"Grafana returned {status}: {body}"
    except requests.RequestException as exc:
        # No response object — never str(exc) (can carry URL + headers).
        return f"Grafana request failed: {type(exc).__name__}"

    data = resp.json()
    url = data.get("url", "")
    version = data.get("version", "?")
    full = f"{_base_url()}{url}" if url else f"{_base_url()}/d/{uid}"
    return (
        f"Published dashboard '{uid}' (version {version}). "
        f"View: {full}"
    )


def _frame_rows(frames: list) -> int:
    """Count rows across Grafana data frames in a /api/ds/query result."""
    total = 0
    for frame in frames or []:
        data = (frame or {}).get("data") or {}
        values = data.get("values") or []
        if values and isinstance(values[0], list):
            total += len(values[0])
    return total


def _verify_panels(dashboard: GrafanaDashboardDef) -> list[dict]:
    """Run every panel's SQL through Grafana's datasource and report results.

    Uses POST /api/ds/query, which executes the *rendered* query (Grafana
    macros like $__timeFilter resolved against the time window) through the
    same datasource the dashboard uses. This catches malformed query JSON,
    SQL that errors against the live datasource, and panels that return no
    rows — none of which the local SELECT-only lint can see.

    Returns one dict per panel: {title, ok, rows, error}. May raise
    requests exceptions (callers scrub and format them).
    """
    compiled = compile_grafana_dashboard(dashboard)
    now_ms = int(time.time() * 1000)
    # Wide, fixed window so macro-driven panels have data to find; panels
    # using explicit date predicates ignore it.
    frm = str(now_ms - 365 * 24 * 3600 * 1000)
    to = str(now_ms)

    ref_to_title: dict[str, str] = {}
    queries = []
    for panel in compiled["panels"]:
        # Section dividers are Grafana "row" panels with no query.
        if panel.get("type") == "row" or not panel.get("targets"):
            continue
        target = panel["targets"][0]
        ref = f"P{len(queries)}"
        ref_to_title[ref] = panel["title"]
        queries.append({
            "refId": ref,
            "datasource": target["datasource"],
            "rawSql": target["rawSql"],
            "format": target["format"],
            "queryType": target["queryType"],
            "editorType": "sql",
            "intervalMs": 60000,
            "maxDataPoints": 1000,
        })

    resp = requests.post(
        f"{_base_url()}/api/ds/query",
        headers=_headers(),
        json={"queries": queries, "from": frm, "to": to},
        timeout=settings.GRAFANA_REQUEST_TIMEOUT_SECONDS,
    )
    # /api/ds/query returns 200 with per-query errors; only raise on a
    # genuine transport/HTTP failure of the whole request.
    if resp.status_code >= 400:
        resp.raise_for_status()
    results = (resp.json() or {}).get("results", {})

    report = []
    for ref, title in ref_to_title.items():
        r = results.get(ref, {}) or {}
        error = r.get("error") or ""
        status = r.get("status")
        if not error and isinstance(status, int) and status >= 400:
            error = f"datasource status {status}"
        rows = _frame_rows(r.get("frames"))
        report.append({
            "title": title,
            "ok": not error,
            "rows": rows,
            "error": _scrub(str(error)) if error else "",
        })
    return report


def _format_verify_report(report: list[dict]) -> str:
    lines = []
    for p in report:
        if not p["ok"]:
            lines.append(f"  - FAIL '{p['title']}': {p['error']}")
        elif p["rows"] == 0:
            lines.append(f"  - EMPTY '{p['title']}': query ok but 0 rows")
        else:
            lines.append(f"  - OK '{p['title']}': {p['rows']} row(s)")
    return "\n".join(lines)


def register_grafana_tools(mcp, ch) -> None:
    """Register Grafana dashboard tools (no-op unless the feature flag is on)."""
    if not settings.GRAFANA_TOOLS_ENABLED:
        return

    @mcp.tool()
    def preview_grafana_dashboard(dashboard: GrafanaDashboardDef) -> str:
        """Return an ASCII sketch of the dashboard layout plus the metric each
        card shows — WITHOUT publishing.

        ALWAYS call this and present the sketch to the user for approval before
        `publish_grafana_dashboard`. The user may want a different layout,
        different metrics, units, or section order. Widths in the sketch are
        proportional to the real 24-column grid, so what you show is what gets
        published.
        """
        return build_layout_sketch(dashboard)

    @mcp.tool()
    def validate_grafana_dashboard(dashboard: GrafanaDashboardDef) -> str:
        """Validate a dashboard spec without publishing.

        Two layers: (1) every panel's SQL (with Grafana macros substituted) is
        checked against the same read-only SQL guards as `execute_query`, and
        role/viz/shape compatibility is confirmed; (2) if Grafana is fully
        configured, every panel is executed against the live datasource via
        /api/ds/query and the per-panel row counts / errors are reported, so
        you can confirm all cards have data before publishing.
        """
        ok, msg = _validate(dashboard)
        if not ok:
            return f"INVALID: {msg}"
        n = len(dashboard.panels)
        head = (
            f"SQL VALID: {n} panel(s) across roles "
            f"{sorted({p.role for p in dashboard.panels})}."
        )
        if not _fully_configured():
            return head + " (datasource not configured — skipped live data check)"
        try:
            report = _verify_panels(dashboard)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            return head + f"\nLive check failed: Grafana returned {status}: {_scrub(exc.response.text if exc.response is not None else '')}"
        except requests.RequestException as exc:
            return head + f"\nLive check failed: Grafana request failed: {type(exc).__name__}"
        failed = [p for p in report if not p["ok"]]
        empty = [p for p in report if p["ok"] and p["rows"] == 0]
        verdict = "ALL PANELS RETURN DATA" if not failed and not empty else "ISSUES FOUND"
        return f"{head}\nLive data check — {verdict}:\n{_format_verify_report(report)}"

    @mcp.tool()
    def verify_grafana_dashboard(dashboard: GrafanaDashboardDef) -> str:
        """Run every panel against the live Grafana datasource and report
        per-panel row counts and errors (no publish). Use to confirm all
        cards have data."""
        if not _fully_configured():
            return "Error: Grafana not fully configured (URL / token / datasource UID)."
        try:
            report = _verify_panels(dashboard)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            return f"Grafana returned {status}: {_scrub(exc.response.text if exc.response is not None else '')}"
        except requests.RequestException as exc:
            return f"Grafana request failed: {type(exc).__name__}"
        return _format_verify_report(report)

    @mcp.tool()
    def publish_grafana_dashboard(dashboard: GrafanaDashboardDef) -> str:
        """Compile and publish a dashboard to Grafana (idempotent by UID).

        Refuses to overwrite an existing dashboard that lacks the `cerebro-mcp`
        tag unless `force_overwrite=true` is set on the spec — this guards
        against clobbering human-edited dashboards.
        """
        if not _fully_configured():
            return (
                "Error: Grafana publishing not fully configured. Set "
                "GRAFANA_URL, GRAFANA_API_TOKEN, and "
                "GRAFANA_CLICKHOUSE_DATASOURCE_UID."
            )

        ok, msg = _validate(dashboard)
        if not ok:
            return f"Error: {msg}"

        # Tag-guarded overwrite.
        try:
            existing = _fetch_dashboard(dashboard.uid)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            return f"Grafana returned {status}: {_scrub(exc.response.text if exc.response is not None else '')}"
        except requests.RequestException as exc:
            return f"Grafana request failed: {type(exc).__name__}"

        if existing and not dashboard.force_overwrite:
            tags = (existing.get("dashboard") or {}).get("tags") or []
            if "cerebro-mcp" not in tags:
                return (
                    f"Error: dashboard '{dashboard.uid}' exists and was not "
                    f"created by cerebro-mcp. Re-run with force_overwrite=true "
                    f"to clobber the human-edited dashboard."
                )

        # Live data verification: run every panel against the datasource and
        # refuse to publish a dashboard with broken or (unless allowed) empty
        # panels. This is what guarantees all cards render with data.
        try:
            report = _verify_panels(dashboard)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            return f"Grafana returned {status}: {_scrub(exc.response.text if exc.response is not None else '')}"
        except requests.RequestException as exc:
            return f"Grafana request failed: {type(exc).__name__}"

        failed = [p for p in report if not p["ok"]]
        empty = [p for p in report if p["ok"] and p["rows"] == 0]
        if failed or (empty and not dashboard.allow_empty):
            hint = (
                "" if not empty or dashboard.allow_empty
                else " (set allow_empty=true to publish anyway)"
            )
            return (
                f"Error: not publishing — panel verification failed{hint}:\n"
                f"{_format_verify_report(report)}"
            )

        compiled = compile_grafana_dashboard(dashboard)
        payload: dict = {
            "dashboard": compiled,
            "overwrite": True,
            "message": "Published by cerebro-mcp",
        }
        if settings.GRAFANA_FOLDER_UID:
            payload["folderUid"] = settings.GRAFANA_FOLDER_UID

        result = _post_dashboard(payload, dashboard.uid)
        if result.startswith("Published"):
            empty_note = f", {len(empty)} empty (allowed)" if empty else ""
            result += f" Verified {len(report)} panel(s) return data{empty_note}."
        return result

    @mcp.tool()
    def get_grafana_dashboard(uid: str) -> str:
        """Fetch metadata for a published dashboard by UID.

        Returns title, tags, version, and folder, or a not-found message.
        """
        if not (settings.GRAFANA_URL and settings.GRAFANA_API_TOKEN):
            return "Error: Grafana not configured (GRAFANA_URL / GRAFANA_API_TOKEN)."
        try:
            data = _fetch_dashboard(uid)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            return f"Grafana returned {status}: {_scrub(exc.response.text if exc.response is not None else '')}"
        except requests.RequestException as exc:
            return f"Grafana request failed: {type(exc).__name__}"

        if data is None:
            return f"Dashboard '{uid}' not found."
        db = data.get("dashboard") or {}
        meta = data.get("meta") or {}
        return (
            f"Dashboard '{uid}': title={db.get('title')!r}, "
            f"tags={db.get('tags')}, version={db.get('version')}, "
            f"folder={meta.get('folderTitle')!r}, url={_base_url()}{meta.get('url', '')}"
        )
