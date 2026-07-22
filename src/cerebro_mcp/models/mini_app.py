"""Shared pydantic models for the cerebro mini-app platform.

These types describe the wire format passed between mini-app launcher tools
(e.g. ``open_metric_lab_from_sql``, ``open_contract_explorer``) and their
React frontends via ``CallToolResult.structuredContent``.

Pure data — no I/O, no MCP imports — so this module is safe to import from
anywhere in the codebase, including tests.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DatasetMode = Literal[
    "exact_bounded",
    "exact_capped",
    "random_sample",
    "preview_only",
]
PayloadType = Literal["INITIAL_LOAD", "PATCH_VIEW_STATE", "SHOW_WARNING"]


class DatasetStats(BaseModel):
    """Sizing and provenance metadata for a single dataset attached to a view."""

    row_count: int
    rows_returned: int
    mode: DatasetMode
    sample_source_rows: int | None = None
    source_rows: int | None = None
    row_cap: int | None = None
    truncated: bool | None = None
    fetched_at: str | None = None
    elapsed_seconds: float | None = None
    warnings: list[str] = Field(default_factory=list)


class DatasetSchemaColumn(BaseModel):
    name: str
    type: str = "Unknown"


class DatasetDescriptor(BaseModel):
    """Lightweight dataset metadata embedded in a launch payload.

    Carries the dataset schema, stats, and the *first page* of rows
    (hard-capped to ``preview_rows``). Subsequent pages are fetched via
    ``get_mini_app_rows`` using ``page_token``.
    """

    key: str
    title: str = ""
    sql: str = ""
    database: str = "dbt"
    columns: list[DatasetSchemaColumn] = Field(default_factory=list)
    stats: DatasetStats
    preview_rows: list[list[Any]] = Field(default_factory=list)
    page_token: str | None = None
    # Optional dataset-local forensic attribution. Generic mini apps may omit
    # it; forensic surfaces attach the exact scope that answered this dataset
    # so provenance/freshness cannot be separated from the rows in transit.
    scope_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class SummaryCard(BaseModel):
    label: str
    value: str
    delta: str | None = None
    tone: Literal["neutral", "positive", "negative", "warning"] = "neutral"


class MiniAppPayload(BaseModel):
    """Top-level wire payload for every mini-app message.

    The ``type`` discriminates how the frontend should react:
      * ``INITIAL_LOAD``     — replace the view with this snapshot
      * ``PATCH_VIEW_STATE`` — apply ``patch`` to the existing view state
      * ``SHOW_WARNING``     — push the entries in ``warnings`` to the banner
    """

    type: PayloadType
    view_id: str
    app_id: str
    title: str
    status: Literal["ready", "loading", "error"] = "ready"
    summary_cards: list[SummaryCard] = Field(default_factory=list)
    datasets: dict[str, DatasetDescriptor] = Field(default_factory=dict)
    view_state: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    patch: dict[str, Any] | None = None


__all__ = [
    "DatasetMode",
    "PayloadType",
    "DatasetStats",
    "DatasetSchemaColumn",
    "DatasetDescriptor",
    "SummaryCard",
    "MiniAppPayload",
]
