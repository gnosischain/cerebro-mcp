from __future__ import annotations

from cerebro_mcp.research_models import (
    PHASE_ORDER,
    PhaseName,
    PhaseStatus,
    ResearchPhaseDetail,
    ResearchProjectState,
    ResearchProjectSummary,
)
from cerebro_mcp.tool_output import truncate_response


def empty_phase_records():
    return {
        phase: {
            "phase": phase,
            "status": "pending",
            "plan_markdown": "",
            "execution_summary": "",
            "verification_summary": "",
        }
        for phase in PHASE_ORDER
    }


def phase_statuses(project: ResearchProjectState) -> dict[str, PhaseStatus]:
    return {
        phase: project.phases[phase].status
        for phase in PHASE_ORDER
    }


def next_phase_name(phase: PhaseName) -> PhaseName | None:
    idx = PHASE_ORDER.index(phase)
    if idx >= len(PHASE_ORDER) - 1:
        return None
    return PHASE_ORDER[idx + 1]


def ensure_current_phase(project: ResearchProjectState, phase: str) -> None:
    if project.current_phase != phase:
        raise ValueError(
            f"Cannot operate on phase '{phase}'. Current phase is "
            f"'{project.current_phase}'."
        )


def ensure_phase_status(
    project: ResearchProjectState,
    phase: str,
    expected: str,
) -> None:
    actual = project.phases[phase].status
    if actual != expected:
        raise ValueError(
            f"Expected phase '{phase}' to be '{expected}', got '{actual}'."
        )


def ensure_phase_not_future(project: ResearchProjectState, phase: str) -> None:
    current_idx = PHASE_ORDER.index(project.current_phase)
    phase_idx = PHASE_ORDER.index(phase)
    if phase_idx > current_idx:
        raise ValueError(
            f"Cannot attach data to future phase '{phase}'. "
            f"Current phase is '{project.current_phase}'."
        )


def advance_phase(project: ResearchProjectState) -> None:
    upcoming = next_phase_name(project.current_phase)
    if upcoming is not None:
        project.current_phase = upcoming


def build_phase_detail(project: ResearchProjectState, phase: PhaseName) -> ResearchPhaseDetail:
    record = project.phases[phase]
    pieces = [
        f"## Phase: {phase}",
        f"- Status: {record.status}",
        f"- Current phase: {project.current_phase}",
    ]
    if record.plan_markdown:
        pieces.append("### Plan\n" + record.plan_markdown)
    if record.execution_summary:
        pieces.append("### Execution\n" + record.execution_summary)
    if record.verification_summary:
        pieces.append("### Verification\n" + record.verification_summary)
    return ResearchPhaseDetail(
        project_id=project.project_id,
        phase=phase,
        status=record.status,
        current_phase=project.current_phase,
        next_phase=next_phase_name(phase),
        plan_markdown=record.plan_markdown,
        execution_summary=record.execution_summary,
        verification_summary=record.verification_summary,
        summary_markdown=truncate_response("\n\n".join(pieces)),
    )


def build_project_summary(
    project: ResearchProjectState,
    *,
    evidence_count: int,
    memory_count: int,
    findings_count: int,
    peer_review_status: str,
    artifact_count: int,
) -> ResearchProjectSummary:
    pieces = [
        f"## Research Project `{project.project_id}`",
        f"- Hypothesis: {project.hypothesis}",
        f"- Scope: {project.scope}",
        f"- Status: {project.status}",
        f"- Current phase: {project.current_phase}",
        f"- Evidence items: {evidence_count}",
        f"- Memory entries: {memory_count}",
        f"- Findings: {findings_count}",
        f"- Peer review: {peer_review_status}",
        f"- Artifacts: {artifact_count}",
    ]
    return ResearchProjectSummary(
        project_id=project.project_id,
        hypothesis=project.hypothesis,
        scope=project.scope,
        status=project.status,
        current_phase=project.current_phase,
        phase_statuses=phase_statuses(project),
        evidence_count=evidence_count,
        memory_count=memory_count,
        findings_count=findings_count,
        peer_review_status=peer_review_status,
        artifact_count=artifact_count,
        summary_markdown=truncate_response("\n".join(pieces)),
    )
