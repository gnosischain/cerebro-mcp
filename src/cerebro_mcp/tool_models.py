from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryResult(BaseModel):
    sql: str
    database: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    rows_returned: int
    truncated: bool
    fetch_mode: Literal["rows", "arrow"]
    elapsed_seconds: float
    result_ref_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    summary_markdown: str = ""


class ExplainResult(BaseModel):
    sql: str
    database: str
    lines: list[str]
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    summary_markdown: str = ""


class TableSummary(BaseModel):
    name: str
    engine: str
    total_rows: Any = None
    size: str


class TableListPage(BaseModel):
    database: str
    name_pattern: str = ""
    page_size: int
    include_detailed_columns: bool = False
    tables: list[TableSummary]
    next_page_token: str | None = None
    warnings: list[str] = Field(default_factory=list)
    summary_markdown: str = ""


class ColumnSchema(BaseModel):
    name: str
    type: str
    default_kind: str = ""
    description: str = ""


class TableSchema(BaseModel):
    database: str
    table: str
    model_description: str = ""
    materialization: str = ""
    columns: list[ColumnSchema]
    summary_markdown: str = ""


class AsyncQueryStatus(BaseModel):
    query_id: str
    status: Literal["pending", "running", "completed", "failed"]
    elapsed_seconds: float
    next_page_token: str | None = None
    result: QueryResult | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    summary_markdown: str = ""
