import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


REQUEST_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
)
ROWS_RETURNED_BUCKETS = (1, 10, 50, 100, 500, 1000, 5000, 10000)
RESERVED_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}

cerebro_http_requests_total = Counter(
    "cerebro_http_requests_total",
    "Total HTTP requests handled by the SSE app",
    ("method", "path", "status"),
)

cerebro_http_request_duration_seconds = Histogram(
    "cerebro_http_request_duration_seconds",
    "HTTP request latency for the SSE app",
    ("method", "path"),
    buckets=REQUEST_DURATION_BUCKETS,
)

cerebro_http_requests_in_progress = Gauge(
    "cerebro_http_requests_in_progress",
    "HTTP requests currently in progress",
    ("method", "path"),
)

cerebro_mcp_requests_total = Counter(
    "cerebro_mcp_requests_total",
    "Total MCP protocol requests handled",
    ("method", "status"),
)

cerebro_mcp_request_duration_seconds = Histogram(
    "cerebro_mcp_request_duration_seconds",
    "Latency of MCP protocol requests",
    ("method",),
    buckets=REQUEST_DURATION_BUCKETS,
)

cerebro_mcp_tool_calls_total = Counter(
    "cerebro_mcp_tool_calls_total",
    "Total MCP tool invocations",
    ("tool_name", "status"),
)

cerebro_mcp_tool_duration_seconds = Histogram(
    "cerebro_mcp_tool_duration_seconds",
    "Latency of MCP tool invocations",
    ("tool_name",),
    buckets=REQUEST_DURATION_BUCKETS,
)

cerebro_clickhouse_query_duration_seconds = Histogram(
    "cerebro_clickhouse_query_duration_seconds",
    "Latency of ClickHouse queries",
    ("database", "audience", "fetch_mode", "status"),
    buckets=REQUEST_DURATION_BUCKETS,
)

cerebro_clickhouse_query_errors_total = Counter(
    "cerebro_clickhouse_query_errors_total",
    "Total failed ClickHouse queries",
    ("database", "audience"),
)

cerebro_clickhouse_rows_returned = Histogram(
    "cerebro_clickhouse_rows_returned",
    "Rows returned by ClickHouse queries",
    ("database", "audience"),
    buckets=ROWS_RETURNED_BUCKETS,
)

semantic_tool_calls_total = Counter(
    "semantic_tool_calls_total",
    "Total semantic tool invocations",
    ("tool_name", "status", "agent_role", "entrypoint"),
)

semantic_query_attempts_total = Counter(
    "semantic_query_attempts_total",
    "Semantic query attempts by planner mode and retry state",
    ("planner_mode", "attempt", "result", "agent_role"),
)

semantic_query_repairs_total = Counter(
    "semantic_query_repairs_total",
    "Semantic query repair attempts by action and error class",
    ("repair_action", "error_class", "agent_role"),
)

semantic_planner_failures_total = Counter(
    "semantic_planner_failures_total",
    "Semantic planner failures",
    ("reason", "planner_mode", "agent_role"),
)

semantic_fallback_total = Counter(
    "semantic_fallback_total",
    "Semantic fallbacks into other execution modes",
    ("fallback_target", "reason", "agent_role"),
)

semantic_route_total = Counter(
    "semantic_route_total",
    "Semantic route decisions made during analytics preflight",
    ("route", "mode"),
)

semantic_bypass_total = Counter(
    "semantic_bypass_total",
    "Raw analytical paths blocked or redirected by semantic routing",
    ("stage", "reason"),
)

semantic_snapshot_reload_total = Counter(
    "semantic_snapshot_reload_total",
    "Semantic snapshot reloads",
    ("status",),
)

semantic_snapshot_stale_total = Counter(
    "semantic_snapshot_stale_total",
    "Semantic snapshot stale events",
    ("reason",),
)

semantic_docs_generation_total = Counter(
    "semantic_docs_generation_total",
    "Semantic docs generation events",
    ("status",),
)

semantic_docs_resource_reads_total = Counter(
    "semantic_docs_resource_reads_total",
    "Semantic docs resource reads",
    ("resource_type", "agent_role"),
)

research_semantic_evidence_total = Counter(
    "research_semantic_evidence_total",
    "Research semantic evidence artifacts written",
    ("phase", "agent_role"),
)

semantic_registry_build_total = Counter(
    "semantic_registry_build_total",
    "Semantic registry build events",
    ("status",),
)

semantic_planner_latency_seconds = Histogram(
    "semantic_planner_latency_seconds",
    "Latency of semantic planning",
    ("planner_mode",),
    buckets=REQUEST_DURATION_BUCKETS,
)

semantic_sql_compile_latency_seconds = Histogram(
    "semantic_sql_compile_latency_seconds",
    "Latency of semantic SQL compilation",
    ("planner_mode",),
    buckets=REQUEST_DURATION_BUCKETS,
)

semantic_query_end_to_end_latency_seconds = Histogram(
    "semantic_query_end_to_end_latency_seconds",
    "End-to-end latency of semantic query execution",
    ("planner_mode", "repair_state"),
    buckets=REQUEST_DURATION_BUCKETS,
)

semantic_snapshot_reload_latency_seconds = Histogram(
    "semantic_snapshot_reload_latency_seconds",
    "Latency of semantic snapshot reloads",
    ("status",),
    buckets=REQUEST_DURATION_BUCKETS,
)

semantic_registry_build_seconds = Histogram(
    "semantic_registry_build_seconds",
    "Latency of semantic registry builds",
    ("status",),
    buckets=REQUEST_DURATION_BUCKETS,
)

semantic_docs_generation_seconds = Histogram(
    "semantic_docs_generation_seconds",
    "Latency of semantic docs generation",
    ("status",),
    buckets=REQUEST_DURATION_BUCKETS,
)

semantic_snapshot_age_seconds = Gauge(
    "semantic_snapshot_age_seconds",
    "Age in seconds of the current semantic snapshot",
)

semantic_registry_models_total = Gauge(
    "semantic_registry_models_total",
    "Count of registry models by semantic status",
    ("semantic_status",),
)

semantic_registry_metrics_total = Gauge(
    "semantic_registry_metrics_total",
    "Count of registry metrics by quality tier",
    ("quality_tier",),
)

semantic_registry_relationships_total = Gauge(
    "semantic_registry_relationships_total",
    "Count of registry relationships by quality tier",
    ("quality_tier",),
)

semantic_semantic_enabled = Gauge(
    "semantic_semantic_enabled",
    "Whether semantic execution is enabled",
    ("state",),
)

SEMANTIC_ENABLED_STATES = (
    "disabled",
    "unavailable",
    "docs_only",
    "execution_available",
)

SEMANTIC_MODEL_STATUSES = (
    "approved",
    "candidate",
    "docs_only",
    "deprecated",
)

SEMANTIC_QUALITY_TIERS = (
    "approved",
    "candidate",
    "draft",
    "blocked",
    "deprecated",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = str(record.getMessage())
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        for key, value in record.__dict__.items():
            if key in RESERVED_LOG_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def log_event(
    logger: logging.Logger,
    event: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    safe_fields = {
        (
            key
            if key not in RESERVED_LOG_RECORD_KEYS and key != "event"
            else f"field_{key}"
        ): value
        for key, value in fields.items()
        if value is not None
    }
    logger.log(level, str(event), extra={"event": str(event), **safe_fields})


def normalize_http_path(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path:
        return str(route_path)

    path = request.url.path
    if path.startswith("/messages/"):
        return "/messages/*"
    if path.startswith("/reports/"):
        return "/reports/*"
    return path


def observe_http_request(
    method: str,
    path: str,
    status: str,
    elapsed_seconds: float,
) -> None:
    cerebro_http_requests_total.labels(
        method=method,
        path=path,
        status=status,
    ).inc()
    cerebro_http_request_duration_seconds.labels(
        method=method,
        path=path,
    ).observe(elapsed_seconds)


def observe_mcp_request(
    method: str,
    status: str,
    duration_ms: int,
) -> None:
    cerebro_mcp_requests_total.labels(method=method, status=status).inc()
    cerebro_mcp_request_duration_seconds.labels(method=method).observe(
        duration_ms / 1000
    )


def observe_tool_call(
    tool_name: str,
    status: str,
    duration_ms: int,
) -> None:
    cerebro_mcp_tool_calls_total.labels(
        tool_name=tool_name,
        status=status,
    ).inc()
    cerebro_mcp_tool_duration_seconds.labels(tool_name=tool_name).observe(
        duration_ms / 1000
    )


def observe_clickhouse_query(
    *,
    database: str,
    audience: str,
    fetch_mode: str,
    status: str,
    elapsed_seconds: float,
    row_count: int | None = None,
) -> None:
    cerebro_clickhouse_query_duration_seconds.labels(
        database=database,
        audience=audience,
        fetch_mode=fetch_mode,
        status=status,
    ).observe(elapsed_seconds)

    if status == "error":
        cerebro_clickhouse_query_errors_total.labels(
            database=database,
            audience=audience,
        ).inc()

    if row_count is not None:
        cerebro_clickhouse_rows_returned.labels(
            database=database,
            audience=audience,
        ).observe(row_count)


def observe_semantic_tool_call(
    *,
    tool_name: str,
    status: str,
    agent_role: str,
    entrypoint: str,
) -> None:
    semantic_tool_calls_total.labels(
        tool_name=tool_name,
        status=status,
        agent_role=agent_role,
        entrypoint=entrypoint,
    ).inc()


def observe_semantic_query_attempt(
    *,
    planner_mode: str,
    attempt: int,
    result: str,
    agent_role: str,
) -> None:
    semantic_query_attempts_total.labels(
        planner_mode=planner_mode,
        attempt=str(attempt),
        result=result,
        agent_role=agent_role,
    ).inc()


def observe_semantic_query_repair(
    *,
    repair_action: str,
    error_class: str,
    agent_role: str,
) -> None:
    semantic_query_repairs_total.labels(
        repair_action=repair_action,
        error_class=error_class,
        agent_role=agent_role,
    ).inc()


def observe_semantic_planner_failure(
    *,
    reason: str,
    planner_mode: str,
    agent_role: str,
) -> None:
    semantic_planner_failures_total.labels(
        reason=reason,
        planner_mode=planner_mode,
        agent_role=agent_role,
    ).inc()


def observe_semantic_fallback(
    *,
    fallback_target: str,
    reason: str,
    agent_role: str,
) -> None:
    semantic_fallback_total.labels(
        fallback_target=fallback_target,
        reason=reason,
        agent_role=agent_role,
    ).inc()


def observe_semantic_route(*, route: str, mode: str) -> None:
    semantic_route_total.labels(route=route, mode=mode).inc()


def observe_semantic_bypass(*, stage: str, reason: str) -> None:
    semantic_bypass_total.labels(stage=stage, reason=reason).inc()


def observe_semantic_docs_read(*, resource_type: str, agent_role: str) -> None:
    semantic_docs_resource_reads_total.labels(
        resource_type=resource_type,
        agent_role=agent_role,
    ).inc()


def observe_research_semantic_evidence(*, phase: str, agent_role: str) -> None:
    research_semantic_evidence_total.labels(
        phase=phase,
        agent_role=agent_role,
    ).inc()


def observe_semantic_snapshot_reload(*, status: str, elapsed_seconds: float) -> None:
    semantic_snapshot_reload_total.labels(status=status).inc()
    semantic_snapshot_reload_latency_seconds.labels(status=status).observe(
        elapsed_seconds
    )


def observe_semantic_snapshot_stale(*, reason: str) -> None:
    semantic_snapshot_stale_total.labels(reason=reason).inc()


def observe_semantic_registry_build(*, status: str, elapsed_seconds: float) -> None:
    semantic_registry_build_total.labels(status=status).inc()
    semantic_registry_build_seconds.labels(status=status).observe(
        elapsed_seconds
    )


def observe_semantic_docs_generation(*, status: str, elapsed_seconds: float) -> None:
    semantic_docs_generation_total.labels(status=status).inc()
    semantic_docs_generation_seconds.labels(status=status).observe(
        elapsed_seconds
    )


def observe_semantic_planner_latency(*, planner_mode: str, elapsed_seconds: float) -> None:
    semantic_planner_latency_seconds.labels(planner_mode=planner_mode).observe(
        elapsed_seconds
    )


def observe_semantic_sql_compile_latency(*, planner_mode: str, elapsed_seconds: float) -> None:
    semantic_sql_compile_latency_seconds.labels(planner_mode=planner_mode).observe(
        elapsed_seconds
    )


def observe_semantic_query_latency(
    *,
    planner_mode: str,
    repair_state: str,
    elapsed_seconds: float,
) -> None:
    semantic_query_end_to_end_latency_seconds.labels(
        planner_mode=planner_mode,
        repair_state=repair_state,
    ).observe(elapsed_seconds)


def set_semantic_snapshot_age(age_seconds: float) -> None:
    semantic_snapshot_age_seconds.set(age_seconds)


def set_semantic_registry_totals(
    *,
    model_status_counts: dict[str, int],
    metric_quality_counts: dict[str, int],
    relationship_quality_counts: dict[str, int],
) -> None:
    for status in SEMANTIC_MODEL_STATUSES:
        semantic_registry_models_total.labels(semantic_status=status).set(
            model_status_counts.get(status, 0)
        )
    for status, count in model_status_counts.items():
        if status in SEMANTIC_MODEL_STATUSES:
            continue
        semantic_registry_models_total.labels(semantic_status=status).set(count)
    for quality in SEMANTIC_QUALITY_TIERS:
        semantic_registry_metrics_total.labels(quality_tier=quality).set(
            metric_quality_counts.get(quality, 0)
        )
        semantic_registry_relationships_total.labels(quality_tier=quality).set(
            relationship_quality_counts.get(quality, 0)
        )
    for quality, count in metric_quality_counts.items():
        if quality in SEMANTIC_QUALITY_TIERS:
            continue
        semantic_registry_metrics_total.labels(quality_tier=quality).set(count)
    for quality, count in relationship_quality_counts.items():
        if quality in SEMANTIC_QUALITY_TIERS:
            continue
        semantic_registry_relationships_total.labels(quality_tier=quality).set(count)


def set_semantic_enabled(state: str) -> None:
    for known_state in SEMANTIC_ENABLED_STATES:
        semantic_semantic_enabled.labels(state=known_state).set(0)
    semantic_semantic_enabled.labels(state=state).set(1)


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = normalize_http_path(request)
        method = request.method
        started = time.perf_counter()
        status = "500"
        cerebro_http_requests_in_progress.labels(
            method=method,
            path=path,
        ).inc()
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            elapsed_seconds = time.perf_counter() - started
            cerebro_http_requests_in_progress.labels(
                method=method,
                path=path,
            ).dec()
            observe_http_request(
                method=method,
                path=path,
                status=status,
                elapsed_seconds=elapsed_seconds,
            )
