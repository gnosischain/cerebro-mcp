import base64
import hashlib
import json
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cerebro_mcp.clients.clickhouse import ClickHouseManager, ExecutedQuery
from cerebro_mcp.config import settings
from cerebro_mcp.safety import validate_query
from cerebro_mcp.models.tool import AsyncQueryStatus, QueryResult
from cerebro_mcp.runtime.tool_output import build_query_summary, normalize_rows


@dataclass
class StoredAsyncResult:
    sql: str
    database: str
    columns: list[str]
    row_count: int
    page_size: int
    elapsed_seconds: float
    fetch_mode: Literal["rows", "arrow"]
    warnings: list[str]
    storage: Literal["memory", "disk"]
    page_count: int
    pages: list[list[list]] | None = None
    result_dir: str | None = None


@dataclass
class QueryJob:
    id: str
    sql: str
    database: str
    max_rows: int
    dedup_key: str = ""
    explain_context: bool = False
    status: str = "pending"  # pending, running, completed, failed
    stored_result: StoredAsyncResult | None = None
    error: str | None = None
    submitted_at: float = field(default_factory=time.time)
    completed_at: float | None = None


_pending_queries: dict[str, QueryJob] = {}
_executor = ThreadPoolExecutor(max_workers=3)
_CLEANUP_AFTER_SECONDS = 600


def _query_dedup_key(sql: str, database: str, max_rows: int) -> str:
    """Deterministic hash key for deduplicating identical async queries."""
    raw = f"{database}\n{max_rows}\n{sql.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result_root_dir() -> Path:
    return Path(settings.ASYNC_RESULT_DIR)


def _encode_page_token(page_index: int) -> str:
    raw = json.dumps({"page": page_index}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_token(page_token: str | None) -> int:
    if not page_token:
        return 0
    padding = "=" * (-len(page_token) % 4)
    payload = json.loads(base64.urlsafe_b64decode(page_token + padding).decode("utf-8"))
    page_index = int(payload.get("page", 0))
    if page_index < 0:
        raise ValueError("Invalid page token")
    return page_index


def _chunk_rows_for_async(columns: list[str], rows: list[list]) -> list[list[list]]:
    pages: list[list[list]] = []
    current: list[list] = []
    max_chars = settings.effective_tool_result_max_chars
    max_rows = settings.ASYNC_RESULT_PAGE_SIZE

    for row in rows:
        candidate = current + [row]
        encoded = json.dumps(
            {"columns": columns, "rows": candidate},
            ensure_ascii=False,
            allow_nan=False,
        )
        if current and (len(candidate) > max_rows or len(encoded) > max_chars):
            pages.append(current)
            current = [row]
        else:
            current = candidate

    if current or not pages:
        pages.append(current)
    return pages


def _estimate_pages_bytes(columns: list[str], pages: list[list[list]]) -> int:
    encoded = json.dumps(
        {"columns": columns, "pages": pages},
        ensure_ascii=False,
        allow_nan=False,
    )
    return len(encoded.encode("utf-8"))


def _write_pages_to_disk(job_id: str, columns: list[str], pages: list[list[list]]) -> str:
    result_dir = _result_root_dir() / job_id
    result_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"page_count": len(pages), "columns": columns}
    (result_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    for idx, page in enumerate(pages):
        (result_dir / f"page-{idx:04d}.json").write_text(
            json.dumps(page, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
    return str(result_dir)


def _store_async_result(job_id: str, executed: ExecutedQuery) -> StoredAsyncResult:
    normalized_rows = normalize_rows(executed.rows)
    pages = _chunk_rows_for_async(executed.columns, normalized_rows)
    estimated_bytes = _estimate_pages_bytes(executed.columns, pages)
    if estimated_bytes <= settings.ASYNC_RESULT_MEMORY_THRESHOLD_BYTES:
        return StoredAsyncResult(
            sql=executed.sql,
            database=executed.database,
            columns=executed.columns,
            row_count=executed.row_count,
            page_size=settings.ASYNC_RESULT_PAGE_SIZE,
            elapsed_seconds=executed.elapsed_seconds,
            fetch_mode=executed.fetch_mode,
            warnings=list(executed.warnings),
            storage="memory",
            page_count=len(pages),
            pages=pages,
        )

    result_dir = _write_pages_to_disk(job_id, executed.columns, pages)
    return StoredAsyncResult(
        sql=executed.sql,
        database=executed.database,
        columns=executed.columns,
        row_count=executed.row_count,
        page_size=settings.ASYNC_RESULT_PAGE_SIZE,
        elapsed_seconds=executed.elapsed_seconds,
        fetch_mode=executed.fetch_mode,
        warnings=list(executed.warnings),
        storage="disk",
        page_count=len(pages),
        result_dir=result_dir,
    )


def _load_async_page(store: StoredAsyncResult, page_index: int) -> list[list]:
    if page_index >= store.page_count:
        raise ValueError("page_token is out of range")
    if store.storage == "memory":
        assert store.pages is not None
        return store.pages[page_index]
    assert store.result_dir is not None
    page_file = Path(store.result_dir) / f"page-{page_index:04d}.json"
    return json.loads(page_file.read_text(encoding="utf-8"))


def _cleanup_job_artifacts(job: QueryJob) -> None:
    if job.stored_result and job.stored_result.storage == "disk" and job.stored_result.result_dir:
        shutil.rmtree(job.stored_result.result_dir, ignore_errors=True)


def _cleanup_old_jobs():
    now = time.time()
    expired = [
        qid
        for qid, job in _pending_queries.items()
        if job.completed_at and (now - job.completed_at) > _CLEANUP_AFTER_SECONDS
    ]
    for qid in expired:
        job = _pending_queries[qid]
        _cleanup_job_artifacts(job)
        del _pending_queries[qid]


def _run_query(job: QueryJob, ch: ClickHouseManager):
    try:
        job.status = "running"
        executed = ch.run_query(
            job.sql,
            job.database,
            requested_max_rows=job.max_rows,
            audience="internal",
            fetch_mode="auto",
        )
        job.stored_result = _store_async_result(job.id, executed)
        job.status = "completed"
    except Exception as e:
        job.error = str(e)
        job.status = "failed"
    finally:
        job.completed_at = time.time()


def _build_async_status(
    job: QueryJob,
    *,
    page_index: int = 0,
) -> AsyncQueryStatus:
    elapsed = (
        (job.completed_at or time.time()) - job.submitted_at
        if job.status in {"running", "completed", "failed"}
        else time.time() - job.submitted_at
    )

    if job.status in {"pending", "running"}:
        summary = (
            f"**Status:** {'Pending (queued)' if job.status == 'pending' else 'Running'}\n"
            f"**Elapsed:** {elapsed:.1f}s\n\n"
            "The query is still executing. Try again in a few seconds."
        )
        return AsyncQueryStatus(
            query_id=job.id,
            status=job.status,  # type: ignore[arg-type]
            elapsed_seconds=round(elapsed, 3),
            summary_markdown=summary,
        )

    if job.status == "failed":
        summary = (
            f"**Status:** Failed\n"
            f"**Error:** {job.error or 'Unknown error'}\n\n"
            f"### SQL\n```sql\n{job.sql}\n```"
        )
        return AsyncQueryStatus(
            query_id=job.id,
            status="failed",
            elapsed_seconds=round(elapsed, 3),
            error=job.error,
            summary_markdown=summary,
        )

    if job.stored_result is None:
        raise ValueError("Query completed without stored result")

    store = job.stored_result
    page_rows = _load_async_page(store, page_index)
    next_page_token = (
        _encode_page_token(page_index + 1)
        if page_index + 1 < store.page_count
        else None
    )
    warnings = list(store.warnings)
    if next_page_token:
        warnings.append("more_rows_available")

    query_result = QueryResult(
        sql=store.sql,
        database=store.database,
        columns=store.columns,
        rows=page_rows,
        row_count=store.row_count,
        rows_returned=len(page_rows),
        truncated=next_page_token is not None,
        fetch_mode=store.fetch_mode,
        elapsed_seconds=store.elapsed_seconds,
        warnings=warnings,
    )
    summary_notes = []
    if next_page_token:
        summary_notes.append(
            "More rows are available. Call `get_query_results` with "
            "`page_token` to continue."
        )
    summary = build_query_summary(
        columns=query_result.columns,
        rows=query_result.rows,
        row_count=query_result.row_count,
        rows_returned=query_result.rows_returned,
        elapsed_seconds=query_result.elapsed_seconds,
        database=query_result.database,
        sql=query_result.sql,
        warnings=query_result.warnings,
        extra_notes=summary_notes,
        explain_context=job.explain_context,
    )
    query_result = query_result.model_copy(update={"summary_markdown": summary})

    return AsyncQueryStatus(
        query_id=job.id,
        status="completed",
        elapsed_seconds=round(elapsed, 3),
        next_page_token=next_page_token,
        result=query_result,
        warnings=warnings,
        summary_markdown=summary,
    )


def register_async_query_tools(mcp, ch: ClickHouseManager):
    """Register async query execution tools."""

    @mcp.tool()
    def start_query(
        sql: str,
        database: str = "dbt",
        max_rows: int = 100,
        explain_context: bool = False,
    ) -> str:
        """Submit a long-running query for async execution. Returns a query ID to poll.

        Set `explain_context=True` to have the eventual result include a
        "What this shows" section explaining the dbt models and key columns.
        """
        try:
            _cleanup_old_jobs()

            is_valid, error = validate_query(sql, settings.MAX_QUERY_LENGTH)
            if not is_valid:
                return f"Error: Query rejected: {error}"
            if database not in settings.ALLOWED_DATABASES:
                return (
                    f"Error: Database '{database}' not allowed. "
                    f"Allowed: {', '.join(settings.ALLOWED_DATABASES)}"
                )

            capped_max = min(max_rows, settings.MAX_ROWS)

            # Dedup: reuse an existing in-flight or recently completed identical job
            dedup_key = _query_dedup_key(sql, database, capped_max)
            for existing_job in _pending_queries.values():
                if (
                    existing_job.dedup_key == dedup_key
                    and existing_job.status in ("pending", "running", "completed")
                ):
                    label = (
                        "running" if existing_job.status != "completed" else "completed"
                    )
                    return (
                        f"Query already {label} as query_id=`{existing_job.id}`.\n\n"
                        f"Use `get_query_results('{existing_job.id}')` to retrieve results."
                    )

            from cerebro_mcp.tools.governance.session_state import state

            state.record_execute_query(sql)

            query_id = str(uuid.uuid4())[:8]
            job = QueryJob(
                id=query_id,
                sql=sql,
                database=database,
                max_rows=capped_max,
                dedup_key=dedup_key,
                explain_context=explain_context,
            )
            _pending_queries[query_id] = job

            _executor.submit(_run_query, job, ch)

            return (
                f"Query submitted successfully.\n\n"
                f"- **Query ID:** `{query_id}`\n"
                f"- **Database:** {database}\n"
                f"- **Max rows:** {capped_max}\n\n"
                f"Use `get_query_results('{query_id}')` to check status and retrieve results."
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_query_results(
        query_id: str,
        page_token: str | None = None,
    ) -> AsyncQueryStatus | str:
        """Check status and retrieve paginated results of an async query."""
        try:
            job = _pending_queries.get(query_id)
            if job is None:
                return (
                    f"Query ID `{query_id}` not found. "
                    "The server may have restarted, or the query expired. "
                    "Please submit again via `start_query`."
                )

            page_index = _decode_page_token(page_token)
            return _build_async_status(job, page_index=page_index)
        except Exception as e:
            return f"Error: {e}"
