from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PhaseName = Literal[
    "mapping",
    "hypothesis",
    "execution",
    "verification",
    "publication",
]
PhaseStatus = Literal["pending", "planned", "running", "completed", "failed"]
ReviewDecision = Literal["accepted", "accepted_with_warnings", "rejected"]

PHASE_ORDER: tuple[PhaseName, ...] = (
    "mapping",
    "hypothesis",
    "execution",
    "verification",
    "publication",
)


class EvidenceRef(BaseModel):
    kind: Literal[
        "query_result",
        "chart",
        "report",
        "schema_snapshot",
        "semantic_query_result",
    ]
    ref_id: str
    phase: PhaseName | None = None
    title: str = ""
    summary: str = ""


class ResearchMemoryEntry(BaseModel):
    id: str
    kind: str
    statement: str
    confidence: float = 0.5
    applies_to: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ResearchFinding(BaseModel):
    id: str
    title: str
    conclusion: str
    confidence: float = 0.5
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ResearchPhaseRecord(BaseModel):
    phase: PhaseName
    status: PhaseStatus = "pending"
    plan_markdown: str = ""
    execution_summary: str = ""
    verification_summary: str = ""


class ResearchProjectState(BaseModel):
    project_id: str
    hypothesis: str
    scope: str
    target_models: list[str] = Field(default_factory=list)
    status: str = "active"
    current_phase: PhaseName = "mapping"
    phases: dict[str, ResearchPhaseRecord]


class ResearchProjectSummary(BaseModel):
    project_id: str
    hypothesis: str
    scope: str
    status: str
    current_phase: PhaseName
    phase_statuses: dict[str, PhaseStatus]
    evidence_count: int
    memory_count: int
    findings_count: int
    peer_review_status: str
    artifact_count: int
    summary_markdown: str = ""


class ResearchPhaseDetail(BaseModel):
    project_id: str
    phase: PhaseName
    status: PhaseStatus
    current_phase: PhaseName
    next_phase: PhaseName | None = None
    plan_markdown: str = ""
    execution_summary: str = ""
    verification_summary: str = ""
    summary_markdown: str = ""


class ResearchMemoryPage(BaseModel):
    project_id: str
    page_size: int
    memory: list[ResearchMemoryEntry]
    next_page_token: str | None = None
    summary_markdown: str = ""


class ResearchEvidencePage(BaseModel):
    project_id: str
    phase: str = ""
    page_size: int
    evidence: list[EvidenceRef]
    next_page_token: str | None = None
    summary_markdown: str = ""


class ResearchFindingPage(BaseModel):
    project_id: str
    page_size: int
    findings: list[ResearchFinding]
    next_page_token: str | None = None
    summary_markdown: str = ""


class VerificationCheck(BaseModel):
    name: str
    status: Literal["passed", "warning", "failed"]
    details: str = ""


class VerificationResult(BaseModel):
    project_id: str
    checks: list[VerificationCheck]
    overall_status: Literal["passed", "warning", "failed"]
    summary_markdown: str = ""


class PeerReviewPacket(BaseModel):
    project_id: str
    hypothesis: str
    scope: str
    findings: list[ResearchFinding]
    evidence_summaries: list[EvidenceRef]
    unresolved_assumptions: list[str] = Field(default_factory=list)
    verification_summary: str = ""


class PeerReviewResult(BaseModel):
    project_id: str
    overall_decision: ReviewDecision
    accepted_claims: list[str] = Field(default_factory=list)
    challenged_claims: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    follow_up_actions: list[str] = Field(default_factory=list)
    summary_markdown: str = ""


class PublishedResearchArtifact(BaseModel):
    project_id: str
    report_id: str
    title: str
    file_uri: str = ""
    summary_markdown: str = ""
