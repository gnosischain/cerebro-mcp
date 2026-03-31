from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


try:
    import igraph as ig
except ImportError:  # pragma: no cover - optional at runtime until installed
    ig = None


GraphType = Any if ig is None else ig.Graph


@dataclass(frozen=True)
class SemanticSnapshot:
    registry_hash: str
    manifest_hash: str
    catalog_hash: str
    docs_hash: str
    graph: GraphType
    vertex_ids: dict[str, int]
    synonym_index: dict[str, str]
    dimension_index: dict[str, list[dict[str, Any]]]
    metrics: dict[str, dict[str, Any]]
    models: dict[str, dict[str, Any]]
    relationships: list[dict[str, Any]]
    docs_index: dict[str, dict[str, Any]]
    loaded_at: float


class SemanticRetryTrace(BaseModel):
    attempt: int
    sql: str
    clickhouse_error: str = ""
    repair_action: str = ""
    success: bool


class MetricDiscoveryHit(BaseModel):
    name: str
    label: str = ""
    module: str = ""
    root_model: str = ""
    score: int
    quality_tier: str = ""


class MetricDiscoveryResult(BaseModel):
    query: str
    results: list[MetricDiscoveryHit]
    summary_markdown: str = ""


class MetricDetailsResult(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    module: str = ""
    root_model: str = ""
    allowed_dimensions: list[str] = Field(default_factory=list)
    supported_time_grains: list[str] = Field(default_factory=list)
    default_filters: list[dict[str, Any]] = Field(default_factory=list)
    question_synonyms: list[str] = Field(default_factory=list)
    semantic_status: str = ""
    summary_markdown: str = ""


class AnalyticsPreflightResult(BaseModel):
    query: str
    mode: str
    route: str
    hybrid_ready: bool = False
    covered_topics: list[str] = Field(default_factory=list)
    uncovered_topics: list[str] = Field(default_factory=list)
    recommended_metrics: list[str] = Field(default_factory=list)
    recommended_dimensions: list[str] = Field(default_factory=list)
    recommended_next_tool: str = ""
    fallback_reason: str = ""
    summary_markdown: str = ""


class MetricQueryExplanation(BaseModel):
    requested_metrics: list[str]
    resolved_metrics: list[str]
    requested_dimensions: list[str]
    resolved_dimensions: list[str]
    planner_mode: str
    root_models: list[str]
    selected_paths: list[dict[str, Any]]
    rejected_paths: list[dict[str, Any]]
    compiled_sql: str
    warnings: list[str] = Field(default_factory=list)
    repair_traces: list[SemanticRetryTrace] = Field(default_factory=list)
    summary_markdown: str


class SemanticQueryResult(BaseModel):
    sql: str
    database: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    rows_returned: int
    truncated: bool
    fetch_mode: str
    elapsed_seconds: float
    requested_metrics: list[str]
    resolved_metrics: list[str]
    requested_dimensions: list[str]
    resolved_dimensions: list[str]
    planner_mode: str
    root_models: list[str]
    warnings: list[str] = Field(default_factory=list)
    repair_traces: list[SemanticRetryTrace] = Field(default_factory=list)
    semantic_plan: dict[str, Any] = Field(default_factory=dict)
    result_ref_id: str | None = None
    summary_markdown: str = ""
