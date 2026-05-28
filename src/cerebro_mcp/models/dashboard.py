from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Allowed chart types
# ---------------------------------------------------------------------------

ChartType = Literal[
    "line",
    "area",
    "bar",
    "pie",
    "numberDisplay",
    "sankey",
    "heatmap",
    "radar",
    "boxplot",
    "table",
    "text",
    "scatter",
    "quantilebands",
    "wordcloud",
]

_QUERY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# MetricPlacement — grid position for a single metric card (12-col CSS grid)
# ---------------------------------------------------------------------------


class MetricPlacement(BaseModel):
    """Grid position for a single metric card in the 12-column CSS grid."""

    id: str = Field(..., description="Metric ID (must match JS query filename)")
    grid_row: str = Field(..., description='CSS grid-row value, e.g. "1" or "2 / span 3"')
    grid_column: str = Field(
        ...,
        description='CSS grid-column value, e.g. "1 / span 6". '
        "Start + span - 1 must be <= 12.",
    )
    min_height: str = Field(default="450px")

    @field_validator("grid_column")
    @classmethod
    def _validate_grid_column(cls, value: str) -> str:
        """Parse ``N / span M`` syntax and ensure it fits within 12 columns.

        Accepted forms:
        - ``"N / span M"``  — explicit start + span
        - ``"span M"``      — span without explicit start (always valid if M <= 12)
        - ``"N"``           — single column position (valid if 1 <= N <= 12)
        """
        raw = value.strip()

        # Form: "N / span M"
        m = re.match(r"^(\d+)\s*/\s*span\s+(\d+)$", raw)
        if m:
            start, span = int(m.group(1)), int(m.group(2))
            if start < 1:
                raise ValueError(
                    f"grid_column start must be >= 1, got {start}"
                )
            if span < 1:
                raise ValueError(
                    f"grid_column span must be >= 1, got {span}"
                )
            if start + span - 1 > 12:
                raise ValueError(
                    f"grid_column overflow: start({start}) + span({span}) - 1 "
                    f"= {start + span - 1}, which exceeds 12"
                )
            return raw

        # Form: "span M"
        m = re.match(r"^span\s+(\d+)$", raw)
        if m:
            span = int(m.group(1))
            if span < 1 or span > 12:
                raise ValueError(
                    f"grid_column span must be between 1 and 12, got {span}"
                )
            return raw

        # Form: plain column number "N"
        m = re.match(r"^(\d+)$", raw)
        if m:
            col = int(m.group(1))
            if col < 1 or col > 12:
                raise ValueError(
                    f"grid_column must be between 1 and 12, got {col}"
                )
            return raw

        # Fall-through: accept raw CSS value (e.g. "1 / -1") without strict
        # validation so advanced users aren't blocked.
        return raw


# ---------------------------------------------------------------------------
# QuerySpec — JS query file specification
# ---------------------------------------------------------------------------


class QuerySpec(BaseModel):
    """Specification for a single JS query file powering a dashboard metric."""

    # Identity
    id: str = Field(
        ...,
        description="Unique query identifier (lowercase, underscores allowed)",
    )
    name: str
    description: str = ""
    metric_description: str = ""

    # Chart
    chart_type: ChartType

    # SQL
    query: str = Field(..., description="SQL query string")

    # Field mappings
    x_field: str | None = None
    y_field: str | None = None
    series_field: str | None = None
    value_field: str | None = None
    source_field: str | None = None
    target_field: str | None = None
    label_field: str | None = None

    # Booleans
    is_time_series: bool = False
    enable_zoom: bool = False
    enable_filtering: bool = False
    stacked: bool = False
    show_total: bool = False

    # Format
    format: str | None = None

    # Resolution
    resolutions: list[str] | None = None
    default_resolution: str | None = None

    # Text content (for chart_type == "text")
    content: str | None = None

    # Escape hatch for advanced / custom chart config
    extra_properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _QUERY_ID_RE.match(value):
            raise ValueError(
                f"QuerySpec.id must match ^[a-z][a-z0-9_]*$, got {value!r}"
            )
        return value


# ---------------------------------------------------------------------------
# TabSpec — dashboard tab definition
# ---------------------------------------------------------------------------


class TabSpec(BaseModel):
    """Specification for a single dashboard tab."""

    name: str
    order: int
    icon: str = ""
    icon_class: str = ""

    # Optional controls
    time_ranges: bool = False
    default_time_range: str = ""
    resolution_toggle: bool = False
    default_resolution: str = ""
    global_filter_field: str = ""
    global_filter_label: str = ""
    searchable: bool = False
    search_placeholder: str = ""
    unit_toggle: bool = False
    default_unit: str = ""

    # Metric placements (at least one required)
    metrics: list[MetricPlacement] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# DashboardBlueprint — top-level input model
# ---------------------------------------------------------------------------


class DashboardBlueprint(BaseModel):
    """Top-level input for the Dashboard Tab Factory."""

    dashboard_id: str
    tab: TabSpec
    queries: list[QuerySpec] = Field(default_factory=list)
    run_build_check: bool = False
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Output models — discovery results
# ---------------------------------------------------------------------------


class DashboardMetricCandidate(BaseModel):
    """A candidate dbt model surfaced by the discovery step."""

    model_name: str
    module: str
    description: str
    columns: list[dict[str, str]]
    quality_tier: str
    table_name: str
    has_time_dimension: bool
    has_series_dimension: bool
    suggested_chart_type: str
    suggested_query: str


class DashboardDiscoveryResult(BaseModel):
    """Aggregated result from the dashboard metric discovery tool."""

    query: str
    module_filter: str
    quality_filter: str
    results: list[DashboardMetricCandidate]
    total_available: int
    summary_markdown: str = ""
