"""Quarterly Review mini-app — a Hex-like canvas bound to one ResearchProject.

The app pairs a ClickHouse-backed dashboard with the durable
``ResearchProjectState`` store. Each saved analysis becomes a
``ResearchFinding`` with ``EvidenceRef(kind="chart")`` per pinned chart; each
note is a ``ResearchMemoryEntry``; publish terminates the project and writes
the standard HTML report artifact.

Tools exposed:

* ``open_quarterly_review``      — ``INITIAL_LOAD`` with four KPI families.
* ``update_quarterly_review_focus`` — scoped PATCH (tab/family/filters/quarter).
* ``add_quarterly_analysis_template`` — drop a Tier-A analysis onto the draft.
* ``save_quarterly_analysis``    — flush draft to ``ResearchFinding``.
* ``record_quarterly_note``      — flush a note/priority/action to memory.
* ``publish_quarterly_review``   — assemble markdown and call
  ``create_report_artifact`` directly (bypasses peer-review gate — the
  quarterly flow isn't a research-grade publication).
"""

from __future__ import annotations

import importlib.resources
import logging
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.mini_app_models import MiniAppPayload, SummaryCard
from cerebro_mcp.research_models import (
    EvidenceRef,
    ResearchFinding,
    ResearchMemoryEntry,
    ResearchProjectState,
)
from cerebro_mcp.research_store import ResearchStore
from cerebro_mcp.research_workflow import PHASE_ORDER
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.quarterly_review_quarters import (
    enumerate_quarters,
    latest_complete_quarter,
    parse_quarter,
    resolve_compare,
)
from cerebro_mcp.tools.quarterly_review_templates import (
    BREAKDOWN_QUERIES,
    KPI_QUERIES,
    SCATTER_QUERIES,
    TEMPLATES,
    TREND_QUERIES,
)

logger = logging.getLogger(__name__)

QUARTERLY_REVIEW_APP_ID = "quarterly_review"
QUARTERLY_REVIEW_URI = "ui://cerebro/quarterly_review"

# Valid template identifiers for the executive QBR. Other templates
# (marketing_qbr, sales_qbr, product_qbr) seed different KPI families and
# different default narrative — all share the same UI shell so the frontend
# key-presence check is sufficient.
KPI_FAMILIES_BY_TEMPLATE: dict[str, list[str]] = {
    "executive_qbr": ["execution", "tvl_volume", "bridges", "consensus"],
    "marketing_qbr": ["execution", "bridges"],
    "sales_qbr": ["bridges", "tvl_volume"],
    "product_qbr": ["execution", "tvl_volume", "consensus"],
}


# =============================================================================
# HTML loading (single-file Vite bundle shipped under static/)
# =============================================================================

_BUNDLED_HTML: str | None = None


def get_quarterly_review_html() -> str:
    """Load the Vite-built single-file app from the packaged static dir."""
    global _BUNDLED_HTML
    if _BUNDLED_HTML is None:
        try:
            _BUNDLED_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/quarterly_review.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>quarterly_review.html not built — "
                "run `make build-ui-quarterly-review`</div>"
                "</body></html>"
            )
    return _BUNDLED_HTML


# =============================================================================
# Helpers — state model, SQL context, phase advancement, dataset loading
# =============================================================================


def _default_state(
    *,
    project_id: str,
    template: str,
    quarter: str,
    compare_quarter: str,
    compare_mode: str,
    available_quarters: list[str],
    families: list[str],
) -> dict[str, Any]:
    """Initial view_state shape. Mirrors the QuarterlyReviewState TS interface."""
    return {
        "project_id": project_id,
        "template": template,
        "current_quarter": quarter,
        "compare_quarter": compare_quarter,
        "compare_mode": compare_mode,
        "available_quarters": available_quarters,
        "active_tab": "overview",
        "kpi_families": families,
        "selected_family": families[0] if families else "execution",
        "filters": {},
        "saved_analyses": [],
        "draft_analysis": {"title": "", "conclusion": "", "chart_ids": []},
        "priorities": [],
        "action_items": [],
        "notes": [],
        "status_message": "0 analyses saved",
    }


def _sql_context(quarter: str, compare: str) -> dict[str, str]:
    q_start, q_end = parse_quarter(quarter)
    c_start, c_end = parse_quarter(compare)
    return {
        "quarter": quarter,
        "compare": compare,
        "quarter_start": q_start.isoformat(),
        "quarter_end": q_end.isoformat(),
        "prior_start": c_start.isoformat(),
        "prior_end": c_end.isoformat(),
    }


def _load_family_datasets(
    ch: ClickHouseManager,
    family: str,
    ctx: dict[str, str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Load the four (KPI, trend, breakdown, scatter) datasets for a family.

    Individual datasets can legitimately fail (missing model in some
    environments, schema drift, etc.). We log the error and, when a
    ``warnings`` list is passed, append a short human-readable summary so the
    UI surfaces the failure rather than silently rendering "No data".
    """
    out: dict[str, Any] = {}
    queries = {
        f"kpi_{family}_qoq": KPI_QUERIES[family],
        f"trend_{family}": TREND_QUERIES[family],
        f"breakdown_{family}": BREAKDOWN_QUERIES[family],
        f"scatter_{family}": SCATTER_QUERIES[family],
    }
    for key, sql_template in queries.items():
        sql = sql_template.format(**ctx)
        try:
            out[key] = mini_apps.load_bounded_dataset(ch, sql, database="dbt")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "quarterly_review: dataset %s failed: %s", key, exc
            )
            if warnings is not None:
                # Keep the message compact — ClickHouse errors can be huge.
                msg = str(exc).split("\n")[0][:200]
                warnings.append(f"{key}: {msg}")
    return out


def _descriptors_for(datasets: dict[str, Any]) -> dict[str, Any]:
    return {
        key: mini_apps.build_dataset_descriptor(key=key, dataset=ds, title=key)
        for key, ds in datasets.items()
    }


def _summary_cards(
    datasets: dict[str, Any], families: list[str]
) -> list[SummaryCard]:
    """One SummaryCard per family, headline metric + delta_pct -> tone."""
    cards: list[SummaryCard] = []
    for fam in families:
        ds = datasets.get(f"kpi_{fam}_qoq")
        if not ds or not ds.rows:
            cards.append(SummaryCard(label=fam, value="—", tone="neutral"))
            continue
        # Row shape: (metric, current, prior, delta_pct). Use first row.
        row = ds.rows[0]
        metric_name = str(row[0])
        current = row[1]
        delta_pct = row[3] if len(row) > 3 else None
        value_str = _format_number(current)
        delta_str: str | None = None
        tone = "neutral"
        if delta_pct is not None and delta_pct == delta_pct:  # not NaN
            try:
                pct = float(delta_pct)
                delta_str = f"{pct:+.1%}"
                if pct >= 0.05:
                    tone = "positive"
                elif pct <= -0.05:
                    tone = "negative"
                else:
                    tone = "warning"
            except (TypeError, ValueError):
                delta_str = None
        cards.append(
            SummaryCard(
                label=f"{fam} · {metric_name}",
                value=value_str,
                delta=delta_str,
                tone=tone,
            )
        )
    return cards


def _format_number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f != f:  # NaN
        return "—"
    absf = abs(f)
    if absf >= 1e9:
        return f"{f / 1e9:.2f}B"
    if absf >= 1e6:
        return f"{f / 1e6:.2f}M"
    if absf >= 1e3:
        return f"{f / 1e3:.2f}k"
    return f"{f:,.2f}"


def _ensure_phase(store: ResearchStore, project_id: str, target: str) -> None:
    """Walk the project forward through phases until it reaches ``target``.

    Idempotent — safe to call on every save. Without this advance, attaching
    evidence under ``phase="execution"`` on a freshly-created project (which
    starts in ``mapping``) would fail ``ensure_current_phase``.
    """
    project = store.load_project(project_id)
    try:
        cur_idx = PHASE_ORDER.index(project.current_phase)
        target_idx = PHASE_ORDER.index(target)
    except ValueError:
        return
    while cur_idx < target_idx:
        phase = PHASE_ORDER[cur_idx]
        record = project.phases.get(phase)
        if record is None:
            break
        if record.status != "planned":
            record.plan_markdown = (
                record.plan_markdown
                or f"Auto-planned on quarterly-review advance ({phase})."
            )
            record.status = "planned"
        record.status = "completed"
        # advance_phase logic: current_phase moves to next phase name.
        if cur_idx + 1 < len(PHASE_ORDER):
            project.current_phase = PHASE_ORDER[cur_idx + 1]  # type: ignore[assignment]
        cur_idx += 1
    store.save_project(project)


# =============================================================================
# Publish — assemble QBR markdown + create report artifact
# =============================================================================


def _assemble_qbr_markdown(
    state: dict[str, Any],
    findings: list[ResearchFinding],
    priorities: list[dict[str, Any]],
    action_items: list[dict[str, Any]],
    extra: str,
) -> str:
    quarter = state.get("current_quarter", "Unknown")
    compare = state.get("compare_quarter", "Unknown")
    compare_mode = state.get("compare_mode", "prior_quarter")
    families: list[str] = state.get("kpi_families", [])

    parts: list[str] = [f"# {quarter} — Quarterly Review"]
    parts.append(f"_Comparison: **{compare}** ({compare_mode})_\n")

    parts.append("## Executive summary")
    if findings:
        top = max(findings, key=lambda f: f.confidence)
        parts.append(top.conclusion)
    else:
        parts.append("_No findings recorded._")

    # Required 3-column Key-takeaways table (CLAUDE.md mandate).
    parts.append("\n## Key takeaways\n")
    parts.append("| Takeaway | Evidence | Why it matters |")
    parts.append("|---|---|---|")
    for f in findings:
        evidence = ", ".join(e.ref_id for e in f.evidence_refs[:3]) or "—"
        why = (f.conclusion or "").replace("\n", " ")[:140]
        parts.append(f"| {f.title} | {evidence} | {why} |")

    # Per-family sections with trend + relational charts.
    for fam in families:
        parts.append(f"\n## {fam.replace('_', ' ').title()}")
        # Findings whose text mentions this family go here.
        fam_key = fam.lower()
        for f in findings:
            if fam_key in (f.title + " " + f.conclusion).lower():
                parts.append(f"**{f.title}.** {f.conclusion}\n")

    if priorities:
        parts.append("\n## Next-quarter priorities")
        for p in priorities:
            parts.append(f"- {p.get('statement', '')}")

    if action_items:
        parts.append("\n## Action items\n")
        parts.append("| Owner | Due | Action |")
        parts.append("|---|---|---|")
        for a in action_items:
            parts.append(
                f"| {a.get('owner') or '—'} | {a.get('due_date') or '—'} | {a.get('statement', '')} |"
            )

    if extra.strip():
        parts.append(f"\n---\n{extra.strip()}")

    return "\n".join(parts)


# =============================================================================
# Registration
# =============================================================================


def register_quarterly_review_tools(
    mcp,
    ch: ClickHouseManager,
    store: ResearchStore,
) -> None:
    """Register Quarterly Review launcher + delta tools with the MCP server."""

    mini_apps.register_app(
        QUARTERLY_REVIEW_APP_ID,
        title="Quarterly Review",
        resource_uri=QUARTERLY_REVIEW_URI,
    )

    @mcp.resource(
        QUARTERLY_REVIEW_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_quarterly_review_app() -> str:
        """Serve the Vite-bundled single-file app at the Quarterly Review URI."""
        return get_quarterly_review_html()

    # ---- open_quarterly_review -------------------------------------------

    @mcp.tool(
        meta={
            "ui": {"resourceUri": QUARTERLY_REVIEW_URI},
            "ui/resourceUri": QUARTERLY_REVIEW_URI,
        }
    )
    def open_quarterly_review(
        quarter: str = "",
        compare: str = "",
        compare_mode: str = "prior_quarter",
        project_id: str = "",
        template: str = "executive_qbr",
    ) -> CallToolResult:
        """Launch the Quarterly Review mini-app for ``quarter`` (defaults to latest complete).

        If ``project_id`` is empty a new ResearchProject is created and its ID
        embedded in the view_state so subsequent ``save_*`` / ``record_*``
        / ``publish_*`` calls can locate it. Re-passing a ``project_id`` lets
        the user reopen a past review and see their saved analyses.
        """
        families = KPI_FAMILIES_BY_TEMPLATE.get(
            template, KPI_FAMILIES_BY_TEMPLATE["executive_qbr"]
        )
        quarter_label = quarter or latest_complete_quarter()
        compare_label = compare or resolve_compare(quarter_label, compare_mode)
        available = enumerate_quarters(ch, limit=12)

        # Create project if not reopening.
        if project_id:
            try:
                project = store.load_project(project_id)
            except Exception as exc:  # noqa: BLE001
                return mini_apps.error_call_tool_result(
                    f"Unknown project_id: {project_id} ({exc})"
                )
        else:
            project = store.create_project(
                hypothesis=f"Quarterly Review {quarter_label}",
                scope="quarterly_review",
                target_models=[
                    "api_execution_transactions_daily",
                    "api_execution_dau_daily",
                    "api_execution_pools_tvl_daily",
                    "api_execution_pools_volume_daily",
                    "api_bridges_volume_daily",
                    "api_consensus_validators_active_daily",
                    "api_consensus_staked_daily",
                ],
            )
            project_id = project.project_id

        ctx = _sql_context(quarter_label, compare_label)

        # Load datasets for every family. SQL errors collect into `warn_list`
        # and bubble up via the payload so the UI surfaces them.
        all_datasets: dict[str, Any] = {}
        warn_list: list[str] = []
        for fam in families:
            all_datasets.update(_load_family_datasets(ch, fam, ctx, warn_list))

        # Create + attach view.
        view_id = mini_apps.create_view(
            QUARTERLY_REVIEW_APP_ID,
            f"Quarterly Review — {quarter_label}",
        )
        mini_apps.replace_view_datasets(view_id, all_datasets)

        # Hydrate saved analyses from disk (they persist per-project).
        saved = _restored_saved_analyses(store, project_id)

        state = _default_state(
            project_id=project_id,
            template=template,
            quarter=quarter_label,
            compare_quarter=compare_label,
            compare_mode=compare_mode,
            available_quarters=available,
            families=families,
        )
        state["saved_analyses"] = saved
        state["notes"] = _restored_notes(store, project_id)
        state["priorities"] = _restored_memory_by_kind(store, project_id, "priority")
        state["action_items"] = _restored_memory_by_kind(store, project_id, "action")
        state["status_message"] = (
            f"{len(saved)} analyses saved"
            if saved
            else "0 analyses saved — open Deep Dive to add one"
        )
        mini_apps.patch_view_state(view_id, state)

        payload = MiniAppPayload(
            type="INITIAL_LOAD",
            view_id=view_id,
            app_id=QUARTERLY_REVIEW_APP_ID,
            title=f"Quarterly Review — {quarter_label}",
            status="ready",
            summary_cards=_summary_cards(all_datasets, families),
            datasets=_descriptors_for(all_datasets),
            view_state=state,
            provenance={
                "project_id": project_id,
                "template": template,
                "quarter": quarter_label,
                "compare": compare_label,
            },
            warnings=[
                *mini_apps.collect_dataset_warnings(*all_datasets.values()),
                *warn_list,
            ],
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Quarterly Review {quarter_label} ready — "
                f"{len(all_datasets)} datasets · project={project_id[:8]}"
                + (f" · {len(warn_list)} load errors" if warn_list else "")
            ),
        )

    # ---- update_quarterly_review_focus ----------------------------------

    @mcp.tool(
        meta={
            "ui": {"resourceUri": QUARTERLY_REVIEW_URI},
            "ui/resourceUri": QUARTERLY_REVIEW_URI,
        }
    )
    def update_quarterly_review_focus(
        view_id: str,
        quarter: str = "",
        compare: str = "",
        compare_mode: str = "",
        selected_family: str = "",
        active_tab: str = "",
        filters: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """PATCH_VIEW_STATE with minimal dataset refresh.

        * Tab change only → no SQL.
        * ``selected_family`` or ``filters`` → refresh that family's breakdown+scatter.
        * ``quarter`` / ``compare`` / ``compare_mode`` → reload every family.
        """
        record = mini_apps.get_view(view_id)
        if not record or record.app_id != QUARTERLY_REVIEW_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown quarterly review view: {view_id}"
            )
        prev = dict(record.view_state)
        new_state = dict(prev)

        # Apply the patch to state.
        if active_tab:
            new_state["active_tab"] = active_tab
        if selected_family:
            new_state["selected_family"] = selected_family
        if filters is not None:
            new_state["filters"] = filters
        if quarter:
            new_state["current_quarter"] = quarter
        if compare:
            new_state["compare_quarter"] = compare
        if compare_mode:
            new_state["compare_mode"] = compare_mode

        refresh_all = bool(
            (quarter and quarter != prev.get("current_quarter"))
            or (compare and compare != prev.get("compare_quarter"))
            or (compare_mode and compare_mode != prev.get("compare_mode"))
        )
        refresh_family = (
            not refresh_all
            and (
                (
                    selected_family
                    and selected_family != prev.get("selected_family")
                )
                or (filters is not None and filters != prev.get("filters"))
            )
        )

        # If quarter/compare changed, derive compare too.
        if refresh_all:
            eff_compare = (
                compare
                or (
                    resolve_compare(
                        new_state["current_quarter"],
                        new_state.get("compare_mode", "prior_quarter"),
                    )
                    if compare_mode or quarter
                    else new_state.get("compare_quarter", "")
                )
            )
            new_state["compare_quarter"] = eff_compare

        refreshed: dict[str, Any] = {}
        patch_warnings: list[str] = []
        if refresh_all:
            ctx = _sql_context(
                new_state["current_quarter"], new_state["compare_quarter"]
            )
            for fam in new_state.get("kpi_families", []):
                refreshed.update(
                    _load_family_datasets(ch, fam, ctx, patch_warnings)
                )
            mini_apps.replace_view_datasets(view_id, refreshed)
        elif refresh_family:
            ctx = _sql_context(
                new_state["current_quarter"], new_state["compare_quarter"]
            )
            fam = new_state["selected_family"]
            refreshed = _load_family_datasets(ch, fam, ctx, patch_warnings)
            for key, ds in refreshed.items():
                mini_apps.attach_dataset(view_id, key, ds)

        mini_apps.patch_view_state(view_id, new_state)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=QUARTERLY_REVIEW_APP_ID,
            title=record.title,
            patch=new_state,
            datasets=_descriptors_for(refreshed),
            warnings=patch_warnings,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Focus updated · refresh={'all' if refresh_all else 'family' if refresh_family else 'none'}"
                + (f" · {len(patch_warnings)} errors" if patch_warnings else "")
            ),
        )

    # ---- add_quarterly_analysis_template --------------------------------

    @mcp.tool(
        meta={
            "ui": {"resourceUri": QUARTERLY_REVIEW_URI},
            "ui/resourceUri": QUARTERLY_REVIEW_URI,
        }
    )
    def add_quarterly_analysis_template(
        view_id: str,
        template_id: str,
    ) -> CallToolResult:
        """Instantiate a Tier-A analysis template and push its charts into the draft.

        Charts are registered in the in-process chart registry via the same
        helper ``generate_charts`` uses — so they're available to
        ``save_quarterly_analysis`` and the final ``publish_*`` markdown.
        """
        record = mini_apps.get_view(view_id)
        if not record or record.app_id != QUARTERLY_REVIEW_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown quarterly review view: {view_id}"
            )
        template = TEMPLATES.get(template_id)
        if template is None:
            return mini_apps.error_call_tool_result(
                f"Unknown template_id: {template_id}. "
                f"Valid: {', '.join(TEMPLATES.keys())}"
            )
        state = dict(record.view_state)
        ctx = _sql_context(state["current_quarter"], state["compare_quarter"])

        # Lazy import avoids circular deps — visualization imports session_state.
        from cerebro_mcp.tools.visualization import _build_and_register_chart

        chart_ids: list[str] = []
        for spec in template.chart_specs:
            rendered = spec.render(ctx)
            result = _build_and_register_chart(
                sql=rendered["sql"],
                database=rendered["database"],
                chart_type=rendered["chart_type"],
                x_field=rendered["x_field"],
                y_field=rendered["y_field"],
                change_field=rendered.get("change_field", ""),
                series_field=rendered.get("series_field", ""),
                title=rendered["title"],
                max_rows=rendered["max_rows"],
                return_metadata_only=True,
            )
            # Result is "OK|chart_id|..." on success, error string otherwise.
            if isinstance(result, str) and result.startswith("OK|"):
                chart_ids.append(result.split("|")[1])
            else:
                logger.warning(
                    "quarterly_review template %s chart failed: %s",
                    template_id,
                    result,
                )

        state["draft_analysis"] = {
            "title": template.default_title,
            "conclusion": template.default_conclusion_hint,
            "chart_ids": chart_ids,
        }
        mini_apps.patch_view_state(view_id, state)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=QUARTERLY_REVIEW_APP_ID,
            title=record.title,
            patch=state,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Added template '{template_id}' · {len(chart_ids)} charts pinned to draft"
            ),
        )

    # ---- save_quarterly_analysis ----------------------------------------

    @mcp.tool(
        meta={
            "ui": {"resourceUri": QUARTERLY_REVIEW_URI},
            "ui/resourceUri": QUARTERLY_REVIEW_URI,
        }
    )
    def save_quarterly_analysis(
        view_id: str,
        title: str,
        conclusion: str,
        chart_ids: list[str],
        confidence: float = 0.7,
    ) -> CallToolResult:
        """Persist the current draft as a ResearchFinding + evidence refs."""
        record = mini_apps.get_view(view_id)
        if not record or record.app_id != QUARTERLY_REVIEW_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown quarterly review view: {view_id}"
            )
        state = dict(record.view_state)
        project_id = state.get("project_id", "")
        if not project_id:
            return mini_apps.error_call_tool_result(
                "View is missing project_id — reopen quarterly review."
            )

        # Advance phases so evidence can attach to "execution".
        _ensure_phase(store, project_id, "execution")

        from cerebro_mcp.tools.visualization import get_chart_record

        evidence_refs: list[EvidenceRef] = []
        for cid in chart_ids:
            rec = get_chart_record(cid)
            chart_title = (rec or {}).get("title") or cid
            summary = (rec or {}).get("sql", "")[:200]
            evidence = EvidenceRef(
                kind="chart",
                ref_id=cid,
                phase="execution",
                title=chart_title,
                summary=summary,
            )
            store.append_evidence(project_id, evidence)
            evidence_refs.append(evidence)

        finding = ResearchFinding(
            title=title,
            conclusion=conclusion,
            confidence=confidence,
            evidence_refs=evidence_refs,
        )
        store.append_finding(project_id, finding)

        saved = list(state.get("saved_analyses", []))
        saved.append(
            {
                "finding_id": finding.id,
                "title": title,
                "conclusion": conclusion,
                "chart_ids": chart_ids,
                "quarter": state.get("current_quarter", ""),
            }
        )
        state["saved_analyses"] = saved
        state["draft_analysis"] = {
            "title": "",
            "conclusion": "",
            "chart_ids": [],
        }
        state["status_message"] = f"{len(saved)} analyses saved"
        mini_apps.patch_view_state(view_id, state)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=QUARTERLY_REVIEW_APP_ID,
            title=record.title,
            patch=state,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Saved analysis: {title}",
        )

    # ---- record_quarterly_note ------------------------------------------

    @mcp.tool(
        meta={
            "ui": {"resourceUri": QUARTERLY_REVIEW_URI},
            "ui/resourceUri": QUARTERLY_REVIEW_URI,
        }
    )
    def record_quarterly_note(
        view_id: str,
        kind: str,
        statement: str,
        owner: str = "",
        due_date: str = "",
    ) -> CallToolResult:
        """Append a ResearchMemoryEntry.

        ``kind`` is one of:
        - ``"observation"`` / ``"assumption"`` → appears in the notes side panel
        - ``"priority"`` → appears in Publish tab "Next-quarter priorities"
        - ``"action"`` → appears in Publish tab "Action items" (uses owner + due_date)
        """
        record = mini_apps.get_view(view_id)
        if not record or record.app_id != QUARTERLY_REVIEW_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown quarterly review view: {view_id}"
            )
        state = dict(record.view_state)
        project_id = state.get("project_id", "")
        if not project_id:
            return mini_apps.error_call_tool_result("Missing project_id.")

        # Encode owner/due_date into statement for action items since
        # ResearchMemoryEntry doesn't have dedicated fields.
        full_statement = statement
        if kind == "action" and (owner or due_date):
            meta_parts = []
            if owner:
                meta_parts.append(f"owner={owner}")
            if due_date:
                meta_parts.append(f"due={due_date}")
            full_statement = f"{statement} [{', '.join(meta_parts)}]"

        entry = ResearchMemoryEntry(
            kind=kind,
            statement=full_statement,
            confidence=0.5,
            applies_to=[],
            evidence_refs=[],
        )
        store.append_memory(project_id, entry)

        # Refresh the matching list in view_state.
        if kind == "priority":
            state["priorities"] = _restored_memory_by_kind(
                store, project_id, "priority"
            )
        elif kind == "action":
            state["action_items"] = _restored_memory_by_kind(
                store, project_id, "action"
            )
        else:
            state["notes"] = _restored_notes(store, project_id)

        mini_apps.patch_view_state(view_id, state)
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=QUARTERLY_REVIEW_APP_ID,
            title=record.title,
            patch=state,
        )
        return mini_apps.payload_to_call_tool_result(
            payload, summary_text=f"Recorded {kind}: {statement[:60]}"
        )

    # ---- publish_quarterly_review ---------------------------------------

    @mcp.tool(
        meta={
            "ui": {"resourceUri": QUARTERLY_REVIEW_URI},
            "ui/resourceUri": QUARTERLY_REVIEW_URI,
        }
    )
    def publish_quarterly_review(
        view_id: str,
        title: str = "",
        extra_markdown: str = "",
    ) -> CallToolResult:
        """Assemble QBR markdown, write the HTML report, link file:// URI.

        Bypasses the full research peer-review gate (``publish_research_report``
        requires peer review) because quarterly reviews are analyst-driven
        rather than research-grade publications. We advance phases, append a
        report EvidenceRef, mark the project completed, and reuse
        ``create_report_artifact`` — the same engine ``generate_report`` uses.
        """
        record = mini_apps.get_view(view_id)
        if not record or record.app_id != QUARTERLY_REVIEW_APP_ID:
            return mini_apps.error_call_tool_result(
                f"Unknown quarterly review view: {view_id}"
            )
        state = dict(record.view_state)
        project_id = state.get("project_id", "")
        if not project_id:
            return mini_apps.error_call_tool_result("Missing project_id.")

        # Pull durable data from disk so we publish what's actually saved.
        findings = store.list_findings(project_id)
        memory = store.list_memory(project_id)
        priorities = [
            {"id": m.id, "statement": m.statement}
            for m in memory
            if m.kind == "priority"
        ]
        action_items = [
            {"id": m.id, "statement": m.statement}
            for m in memory
            if m.kind == "action"
        ]

        report_title = (
            title
            or f"Quarterly Review — {state.get('current_quarter', 'Unknown')}"
        )
        markdown = _assemble_qbr_markdown(
            state, findings, priorities, action_items, extra_markdown
        )

        # Advance to publication so attach_research_evidence below is valid.
        _ensure_phase(store, project_id, "publication")

        from cerebro_mcp.tools.visualization import create_report_artifact

        try:
            report = create_report_artifact(
                report_title,
                markdown,
                enforce_quality_gate=False,
                reset_session_state=False,
            )
        except Exception as exc:  # noqa: BLE001
            return mini_apps.error_call_tool_result(
                f"Report generation failed: {exc}"
            )

        evidence = EvidenceRef(
            kind="report",
            ref_id=report["report_id"],
            phase="publication",
            title=report_title,
            summary="Quarterly Review publication",
        )
        store.append_evidence(project_id, evidence)

        # Mark project complete.
        project = store.load_project(project_id)
        project.phases["publication"].status = "completed"
        project.status = "completed"
        store.save_project(project)

        state["status_message"] = f"Published: {report['report_id'][:8]}"
        mini_apps.patch_view_state(view_id, state)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=QUARTERLY_REVIEW_APP_ID,
            title=record.title,
            patch=state,
            provenance={
                "report_id": report["report_id"],
                "file_uri": report["file_uri"],
            },
        )
        # Include the file:// URI in the summary so the CLAUDE.md "always
        # include the link in your reply" contract is satisfied.
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Published {report_title}\n\n"
                f"Report: {report['file_uri']}\n"
                f"Project: `{project_id}`"
            ),
        )


# =============================================================================
# View-state restoration helpers (hydrate from disk on re-open)
# =============================================================================


def _restored_saved_analyses(
    store: ResearchStore, project_id: str
) -> list[dict[str, Any]]:
    try:
        findings = store.list_findings(project_id)
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "finding_id": f.id,
            "title": f.title,
            "conclusion": f.conclusion,
            "chart_ids": [e.ref_id for e in f.evidence_refs if e.kind == "chart"],
            "quarter": "",
        }
        for f in findings
    ]


def _restored_notes(
    store: ResearchStore, project_id: str
) -> list[dict[str, Any]]:
    try:
        memory = store.list_memory(project_id)
    except Exception:  # noqa: BLE001
        return []
    return [
        {"id": m.id, "kind": m.kind, "statement": m.statement}
        for m in memory
        if m.kind not in ("priority", "action")
    ]


def _restored_memory_by_kind(
    store: ResearchStore, project_id: str, kind: str
) -> list[dict[str, Any]]:
    try:
        memory = store.list_memory(project_id)
    except Exception:  # noqa: BLE001
        return []
    return [
        {"id": m.id, "statement": m.statement} for m in memory if m.kind == kind
    ]


# Silence the unused-variable warning for ResearchProjectState — it's imported
# purely for type-checker context (the store's methods return it).
_ = ResearchProjectState
