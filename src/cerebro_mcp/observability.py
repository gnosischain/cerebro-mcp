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
