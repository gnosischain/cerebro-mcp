from __future__ import annotations

import base64
import json
import uuid

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.models.research import (
    EvidenceRef,
    PeerReviewPacket,
    PeerReviewResult,
    PHASE_ORDER,
    PhaseName,
    PublishedResearchArtifact,
    ResearchEvidencePage,
    ResearchFinding,
    ResearchFindingPage,
    ResearchMemoryEntry,
    ResearchMemoryPage,
    ResearchPhaseDetail,
    ResearchProjectSummary,
    VerificationCheck,
    VerificationResult,
)
from cerebro_mcp.research.store import ResearchStore
from cerebro_mcp.workflow.event_store_sync import (
    record_research_evidence_attached,
    record_research_finding_recorded,
    record_research_memory_recorded,
    record_research_peer_review,
    record_research_phase_completed,
    record_research_phase_planned,
    record_research_published,
    record_research_started,
    record_research_verification,
)
from cerebro_mcp.research.workflow import (
    advance_phase,
    build_phase_detail,
    build_project_summary,
    ensure_current_phase,
    ensure_phase_not_future,
    ensure_phase_status,
)
from cerebro_mcp.runtime.tool_output import format_results_table, truncate_response
from cerebro_mcp.tools.analytics.schema import build_table_schema
from cerebro_mcp.tools.governance.session_state import state
from cerebro_mcp.tools.visualization.charts import (
    _resolve_report,
    create_report_artifact,
    get_chart_record,
)


def _cap_page_size(page_size: int) -> int:
    return min(
        max(page_size, 1),
        max(settings.RESEARCH_PAGE_SIZE_MAX, 1),
    )


def _encode_page_token(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_token(page_token: str | None, *, expected: dict | None = None) -> dict:
    if not page_token:
        return {"offset": 0}
    padding = "=" * (-len(page_token) % 4)
    payload = json.loads(base64.urlsafe_b64decode(page_token + padding).decode("utf-8"))
    if expected:
        for key, value in expected.items():
            if payload.get(key, "") != value:
                raise ValueError(f"page_token does not match the requested {key}")
    offset = int(payload.get("offset", 0))
    if offset < 0:
        raise ValueError("page_token is invalid")
    payload["offset"] = offset
    return payload


def _paginate(items: list, *, page_size: int, offset: int) -> tuple[list, str | None]:
    page = items[offset : offset + page_size]
    next_offset = offset + len(page)
    next_token = None
    if next_offset < len(items):
        next_token = _encode_page_token({"offset": next_offset})
    return page, next_token


def _build_memory_summary(project_id: str, items: list[ResearchMemoryEntry]) -> str:
    rows = [
        [item.id, item.kind, item.statement, item.confidence]
        for item in items
    ]
    body = format_results_table(
        ["id", "kind", "statement", "confidence"],
        rows,
    ) if rows else "No memory entries recorded."
    return truncate_response(f"## Research memory for `{project_id}`\n\n{body}")


def _build_evidence_summary(project_id: str, items: list[EvidenceRef]) -> str:
    rows = [
        [item.kind, item.ref_id, item.phase or "", item.title or "", item.summary or ""]
        for item in items
    ]
    body = format_results_table(
        ["kind", "ref_id", "phase", "title", "summary"],
        rows,
    ) if rows else "No evidence recorded."
    return truncate_response(f"## Research evidence for `{project_id}`\n\n{body}")


def _build_findings_summary(project_id: str, items: list[ResearchFinding]) -> str:
    rows = [
        [item.id, item.title, item.confidence, item.conclusion]
        for item in items
    ]
    body = format_results_table(
        ["id", "title", "confidence", "conclusion"],
        rows,
    ) if rows else "No findings recorded."
    return truncate_response(f"## Research findings for `{project_id}`\n\n{body}")


def _validate_project_phase(project, phase: str) -> None:
    if phase not in PHASE_ORDER:
        raise ValueError(
            f"Invalid phase '{phase}'. Valid phases: {', '.join(PHASE_ORDER)}"
        )
    ensure_phase_not_future(project, phase)


def _resolve_report_ref(ref_id: str) -> tuple[str, str]:
    html, resolved_id, _disk_path = _resolve_report(ref_id)
    if html is None or resolved_id is None:
        raise ValueError(f"Report '{ref_id}' not found.")
    return resolved_id, truncate_response(
        f"Report `{resolved_id[:8]}` is available for research evidence attachment."
    )


def _validate_evidence_ref(
    store: ResearchStore,
    project_id: str,
    kind: str,
    ref_id: str,
) -> tuple[str, str]:
    if kind == "query_result":
        artifact = store.load_query_result_artifact(project_id, ref_id)
        title = artifact.get("title") or ref_id
        summary = (
            f"{artifact.get('database', 'dbt')} | {artifact.get('row_count', 0)} rows"
        )
        return title, summary

    if kind == "semantic_query_result":
        artifact = store.load_query_result_artifact(project_id, ref_id)
        title = artifact.get("title") or ref_id
        planner_mode = artifact.get("semantic_plan", {}).get("planner_mode", "")
        summary = (
            f"{artifact.get('database', 'dbt')} | {artifact.get('row_count', 0)} rows"
            + (f" | planner={planner_mode}" if planner_mode else "")
        )
        return title, summary

    if kind == "schema_snapshot":
        if not store.artifact_exists(project_id, "schema_snapshot", ref_id):
            raise ValueError(
                f"Schema snapshot '{ref_id}' not found for project '{project_id}'."
            )
        return ref_id, "Schema snapshot"

    if kind == "chart":
        chart = get_chart_record(ref_id)
        if chart is None:
            raise ValueError(f"Chart '{ref_id}' not found in the current registry.")
        title = str(chart.get("title") or ref_id)
        summary = str(chart.get("chart_type") or "chart")
        return title, summary

    if kind == "report":
        resolved_id, summary = _resolve_report_ref(ref_id)
        return resolved_id, summary

    raise ValueError(f"Unsupported evidence kind '{kind}'.")


def _validate_evidence_refs(
    store: ResearchStore,
    project_id: str,
    evidence_refs: list[EvidenceRef],
) -> list[EvidenceRef]:
    validated: list[EvidenceRef] = []
    for evidence in evidence_refs:
        title, summary = _validate_evidence_ref(
            store,
            project_id,
            evidence.kind,
            evidence.ref_id,
        )
        payload = evidence.model_copy(
            update={
                "title": evidence.title or title,
                "summary": evidence.summary or summary,
            }
        )
        validated.append(payload)
    return validated


def _peer_review_status(store: ResearchStore, project_id: str) -> str:
    review = store.load_peer_review(project_id)
    return review.overall_decision if review else "not_started"


def _project_summary(store: ResearchStore, project_id: str) -> ResearchProjectSummary:
    project = store.load_project(project_id)
    evidence = store.list_evidence(project_id)
    return build_project_summary(
        project,
        evidence_count=len(evidence),
        memory_count=len(store.list_memory(project_id)),
        findings_count=len(store.list_findings(project_id)),
        peer_review_status=_peer_review_status(store, project_id),
        artifact_count=(
            store.artifact_count(project_id)
            + len([item for item in evidence if item.kind in {"chart", "report"}])
        ),
    )


def register_research_tools(mcp, ch: ClickHouseManager, store: ResearchStore):
    @mcp.tool()
    def start_research_project(
        hypothesis: str,
        scope: str,
        target_models: list[str] | None = None,
    ) -> ResearchProjectSummary | str:
        """Create a new durable research project with explicit workflow phases."""
        try:
            project = store.create_project(
                hypothesis=hypothesis,
                scope=scope,
                target_models=target_models or [],
            )
            # Phase 3: register the project as a workflow in the event log
            # so phase transitions and peer-review verdicts become
            # replayable. Failures here are intentionally swallowed —
            # event-log writes are observability, not correctness.
            record_research_started(project.project_id, hypothesis, scope)
            return _project_summary(store, project.project_id)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_research_project(project_id: str) -> ResearchProjectSummary | str:
        """Return a compact summary of a research project's state."""
        try:
            return _project_summary(store, project_id)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def plan_research_phase(
        project_id: str,
        phase: PhaseName,
        plan_markdown: str,
    ) -> ResearchPhaseDetail | str:
        """Record a structured plan for the current research phase."""
        try:
            project = store.load_project(project_id)
            ensure_current_phase(project, phase)
            ensure_phase_status(project, phase, "pending")
            project.phases[phase].plan_markdown = plan_markdown.strip()
            project.phases[phase].status = "planned"
            store.save_project(project)
            # Phase 3: emit the phase_planned event AFTER persistence so
            # the event log is consistent with the on-disk state.
            record_research_phase_planned(project_id, phase, plan_markdown)
            return build_phase_detail(project, phase)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def execute_research_phase(
        project_id: str,
        phase: PhaseName,
    ) -> ResearchPhaseDetail | str:
        """Advance a research phase after the user/client completes the planned work."""
        try:
            project = store.load_project(project_id)
            ensure_current_phase(project, phase)
            ensure_phase_status(project, phase, "planned")
            if phase == "verification":
                raise ValueError("Use `verify_research_phase` for verification.")
            if phase == "publication":
                raise ValueError("Use `publish_research_report` for publication.")

            project.phases[phase].status = "completed"
            project.phases[phase].execution_summary = (
                f"{phase.title()} phase completed. The project advanced to the next phase."
            )
            advance_phase(project)
            store.save_project(project)
            # Phase 3: record the completion + the new current_phase so
            # replay knows where to pick up.
            record_research_phase_completed(
                project_id, phase, advanced_to=project.current_phase,
            )
            return build_phase_detail(project, phase)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_research_memory(
        project_id: str,
        page_token: str | None = None,
        page_size: int = 20,
    ) -> ResearchMemoryPage | str:
        """List project memory entries with pagination."""
        try:
            capped_page_size = _cap_page_size(page_size)
            offset = _decode_page_token(page_token)["offset"]
            items = store.list_memory(project_id)
            page_items, next_token = _paginate(
                items,
                page_size=capped_page_size,
                offset=offset,
            )
            return ResearchMemoryPage(
                project_id=project_id,
                page_size=capped_page_size,
                memory=page_items,
                next_page_token=next_token,
                summary_markdown=_build_memory_summary(project_id, page_items),
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_research_evidence(
        project_id: str,
        phase: str = "",
        page_token: str | None = None,
        page_size: int = 20,
    ) -> ResearchEvidencePage | str:
        """List research evidence refs with optional phase filtering."""
        try:
            expected = {"phase": phase} if phase else None
            payload = _decode_page_token(page_token, expected=expected)
            capped_page_size = _cap_page_size(page_size)
            evidence = store.list_evidence(project_id)
            if phase:
                evidence = [item for item in evidence if item.phase == phase]
            page_items, next_token = _paginate(
                evidence,
                page_size=capped_page_size,
                offset=payload["offset"],
            )
            if next_token:
                next_token = _encode_page_token(
                    {"offset": payload["offset"] + len(page_items), "phase": phase}
                )
            return ResearchEvidencePage(
                project_id=project_id,
                phase=phase,
                page_size=capped_page_size,
                evidence=page_items,
                next_page_token=next_token,
                summary_markdown=_build_evidence_summary(project_id, page_items),
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_research_findings(
        project_id: str,
        page_token: str | None = None,
        page_size: int = 20,
    ) -> ResearchFindingPage | str:
        """List project findings with pagination."""
        try:
            capped_page_size = _cap_page_size(page_size)
            offset = _decode_page_token(page_token)["offset"]
            findings = store.list_findings(project_id)
            page_items, next_token = _paginate(
                findings,
                page_size=capped_page_size,
                offset=offset,
            )
            return ResearchFindingPage(
                project_id=project_id,
                page_size=capped_page_size,
                findings=page_items,
                next_page_token=next_token,
                summary_markdown=_build_findings_summary(project_id, page_items),
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def attach_research_evidence(
        project_id: str,
        kind: str,
        ref_id: str,
        phase: PhaseName,
        title: str = "",
        summary: str = "",
    ) -> EvidenceRef | str:
        """Attach an existing query/chart/report/schema artifact to a research project."""
        try:
            project = store.load_project(project_id)
            _validate_project_phase(project, phase)
            resolved_title, resolved_summary = _validate_evidence_ref(
                store,
                project_id,
                kind,
                ref_id,
            )
            final_ref_id = ref_id
            if kind == "report":
                final_ref_id, _ = _resolve_report_ref(ref_id)
            evidence = EvidenceRef(
                kind=kind,  # type: ignore[arg-type]
                ref_id=final_ref_id,
                phase=phase,
                title=title or resolved_title,
                summary=summary or resolved_summary,
            )
            store.append_evidence(project_id, evidence)
            # Step 1 expansion — surface evidence attachments in the
            # workflow event stream so resume can list "3 execution-phase
            # query results attached" without rescanning the on-disk
            # research_store.
            record_research_evidence_attached(
                project_id, kind=kind, ref_id=final_ref_id,
                phase=str(phase), title=evidence.title,
            )
            return evidence
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def capture_schema_snapshot(
        project_id: str,
        database: str,
        table: str,
        phase: PhaseName = "mapping",
        title: str = "",
    ) -> EvidenceRef | str:
        """Persist a schema snapshot and register it as research evidence."""
        try:
            project = store.load_project(project_id)
            _validate_project_phase(project, phase)
            schema = build_table_schema(
                ch,
                table=table,
                database=database,
                record_state=False,
            )
            ref_id = store.save_schema_snapshot_artifact(
                project_id=project_id,
                database=database,
                table=table,
                payload=schema.model_dump(),
                title=title,
            )
            evidence = EvidenceRef(
                kind="schema_snapshot",
                ref_id=ref_id,
                phase=phase,
                title=title or f"{database}.{table}",
                summary=f"Schema snapshot for {database}.{table}",
            )
            store.append_evidence(project_id, evidence)
            record_research_evidence_attached(
                project_id, kind="schema_snapshot", ref_id=ref_id,
                phase=str(phase), title=evidence.title,
            )
            return evidence
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def record_research_memory(
        project_id: str,
        kind: str,
        statement: str,
        applies_to: list[str] | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
        confidence: float = 0.5,
    ) -> ResearchMemoryEntry | str:
        """Store a durable research memory entry linked to supporting evidence."""
        try:
            validated_refs = _validate_evidence_refs(
                store,
                project_id,
                evidence_refs or [],
            )
            entry = ResearchMemoryEntry(
                id=f"mem_{uuid.uuid4().hex[:12]}",
                kind=kind,
                statement=statement.strip(),
                confidence=confidence,
                applies_to=applies_to or [],
                evidence_refs=validated_refs,
            )
            store.append_memory(project_id, entry)
            # Step 1 expansion — surface the observation in the workflow
            # event stream. Critical for resume: lets `recompute_workflow_resume_hint`
            # show "agent recorded 3 observations including 'marketplace
            # has TWO distinct activity waves...'" instead of just the
            # phase name.
            record_research_memory_recorded(
                project_id, memory_id=entry.id, kind=entry.kind,
                statement=entry.statement, confidence=entry.confidence,
            )
            return entry
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def record_research_finding(
        project_id: str,
        title: str,
        conclusion: str,
        evidence_refs: list[EvidenceRef],
        confidence: float = 0.5,
    ) -> ResearchFinding | str:
        """Store a project-specific conclusion backed by evidence references."""
        try:
            validated_refs = _validate_evidence_refs(
                store,
                project_id,
                evidence_refs,
            )
            finding = ResearchFinding(
                id=f"find_{uuid.uuid4().hex[:12]}",
                title=title.strip(),
                conclusion=conclusion.strip(),
                confidence=confidence,
                evidence_refs=validated_refs,
            )
            store.append_finding(project_id, finding)
            # Step 1 expansion — surface the conclusion in the event log.
            record_research_finding_recorded(
                project_id, finding_id=finding.id,
                title=finding.title, confidence=finding.confidence,
                evidence_count=len(finding.evidence_refs),
            )
            return finding
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def verify_research_phase(project_id: str) -> VerificationResult | str:
        """Run structural validation checks before peer review/publication."""
        try:
            project = store.load_project(project_id)
            ensure_current_phase(project, "verification")

            evidence = store.list_evidence(project_id)
            execution_evidence = [item for item in evidence if item.phase == "execution"]
            checks: list[VerificationCheck] = []

            if execution_evidence:
                checks.append(
                    VerificationCheck(
                        name="execution_evidence",
                        status="passed",
                        details=f"{len(execution_evidence)} execution evidence item(s) attached.",
                    )
                )
            else:
                checks.append(
                    VerificationCheck(
                        name="execution_evidence",
                        status="failed",
                        details="No execution-phase evidence is attached.",
                    )
                )

            statistical_found = False
            for item in execution_evidence:
                if item.kind == "query_result":
                    artifact = store.load_query_result_artifact(project_id, item.ref_id)
                    if state.is_statistical_query(artifact.get("sql", "")):
                        statistical_found = True
                        break

            checks.append(
                VerificationCheck(
                    name="statistical_depth",
                    status="passed" if statistical_found else "failed",
                    details=(
                        "Statistical/distribution query evidence detected."
                        if statistical_found
                        else "No statistical/distribution query evidence detected."
                    ),
                )
            )

            broken_refs: list[str] = []
            for item in evidence:
                try:
                    _validate_evidence_ref(store, project_id, item.kind, item.ref_id)
                except Exception:
                    broken_refs.append(f"{item.kind}:{item.ref_id}")
            checks.append(
                VerificationCheck(
                    name="artifact_integrity",
                    status="passed" if not broken_refs else "failed",
                    details=(
                        "All evidence references resolved successfully."
                        if not broken_refs
                        else f"Broken evidence refs: {', '.join(broken_refs)}"
                    ),
                )
            )

            statuses = [check.status for check in checks]
            if "failed" in statuses:
                overall_status = "failed"
                project.phases["verification"].status = "failed"
            elif "warning" in statuses:
                overall_status = "warning"
                project.phases["verification"].status = "completed"
                advance_phase(project)
            else:
                overall_status = "passed"
                project.phases["verification"].status = "completed"
                advance_phase(project)

            summary_lines = [
                f"## Verification for `{project_id}`",
                f"- Overall status: {overall_status}",
            ]
            summary_lines.extend(
                f"- {check.name}: {check.status} — {check.details}"
                for check in checks
            )
            summary = truncate_response("\n".join(summary_lines))
            project.phases["verification"].verification_summary = summary
            store.save_project(project)

            # Phase 3: emit verification_completed + flip the gate. The
            # gate status drives the dispatcher's "no publication without
            # passing verification" rule once the dispatcher is consulted.
            record_research_verification(
                project_id, "verification",
                passed=(overall_status != "failed"),
                summary=summary,
            )

            return VerificationResult(
                project_id=project_id,
                checks=checks,
                overall_status=overall_status,
                summary_markdown=summary,
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def prepare_peer_review(project_id: str) -> PeerReviewPacket | str:
        """Build a compact review packet for the research peer-review prompt."""
        try:
            project = store.load_project(project_id)
            ensure_current_phase(project, "publication")
            findings = store.list_findings(project_id)
            evidence = store.list_evidence(project_id)
            verification_summary = project.phases["verification"].verification_summary
            unresolved_assumptions = [
                f"Finding '{finding.title}' has confidence {finding.confidence:.2f}."
                for finding in findings
                if finding.confidence < 0.75
            ]
            return PeerReviewPacket(
                project_id=project_id,
                hypothesis=project.hypothesis,
                scope=project.scope,
                findings=findings,
                evidence_summaries=evidence[:20],
                unresolved_assumptions=unresolved_assumptions,
                verification_summary=verification_summary,
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def record_peer_review(
        project_id: str,
        result: PeerReviewResult,
    ) -> PeerReviewResult | str:
        """Store the structured result of an adversarial peer review."""
        try:
            project = store.load_project(project_id)
            ensure_current_phase(project, "publication")
            if result.project_id != project_id:
                raise ValueError("project_id does not match the peer review payload.")
            store.save_peer_review(project_id, result)
            project.phases["publication"].status = (
                "failed" if result.overall_decision == "rejected" else "planned"
            )
            project.status = (
                "peer_review_rejected"
                if result.overall_decision == "rejected"
                else "reviewed"
            )
            store.save_project(project)
            # Phase 3: peer-review verdict flips the canonical gate the
            # dispatcher checks before allowing publication.
            record_research_peer_review(
                project_id,
                status=("approved" if result.overall_decision != "rejected"
                        else "rejected"),
                summary=getattr(result, "summary", "") or "",
            )
            return result
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def publish_research_report(
        project_id: str,
        title: str,
        content_markdown: str,
    ) -> PublishedResearchArtifact | str:
        """Publish a research report after verification and peer review."""
        try:
            project = store.load_project(project_id)
            ensure_current_phase(project, "publication")
            review = store.load_peer_review(project_id)
            if review is None:
                raise ValueError("Peer review has not been recorded yet.")
            if review.overall_decision == "rejected":
                raise ValueError("Peer review rejected the project. Publication is blocked.")

            report = create_report_artifact(
                title,
                content_markdown,
                enforce_quality_gate=False,
                reset_session_state=False,
            )
            evidence = EvidenceRef(
                kind="report",
                ref_id=report["report_id"],
                phase="publication",
                title=title,
                summary="Published research report",
            )
            store.append_evidence(project_id, evidence)
            project.phases["publication"].status = "completed"
            project.status = "completed"
            store.save_project(project)
            # Phase 3: workflow terminates — record the published artifact
            # and mark the workflow as completed in the event log.
            record_research_published(project_id, report["report_id"], title)
            summary = truncate_response(
                f"## Published report\n\n"
                f"- Project: `{project_id}`\n"
                f"- Report ID: `{report['report_id'][:8]}`\n"
                f"- Title: {title}\n"
                f"- File: {report['file_uri']}"
            )
            return PublishedResearchArtifact(
                project_id=project_id,
                report_id=report["report_id"],
                title=title,
                file_uri=report["file_uri"],
                summary_markdown=summary,
            )
        except Exception as e:
            return f"Error: {e}"
