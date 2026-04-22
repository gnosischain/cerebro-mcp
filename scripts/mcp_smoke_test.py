"""End-to-end smoke test for the cerebro-mcp server.

Connects via the ``mcp`` Python SDK directly — NOT via Claude Desktop — and
walks through a fixed 12-step sequence that exercises the whole stack:

    initialize → tools/list → prompts/list → resources/list →
    search_models → list_databases → list_tables → describe_table →
    get_sample_data → execute_query → generate_charts → generate_report

Each step prints PASS / FAIL / SKIP with timing. Any step that fails does not
abort the suite; the final summary line shows how many passed. Exit code is
the number of failed steps (0 = all green).

Usage
-----

Local stdio transport (spawns ``uv run cerebro-mcp``)::

    uv run python scripts/mcp_smoke_test.py --transport local

Remote SSE transport (preview deployment)::

    uv run python scripts/mcp_smoke_test.py --transport remote --token-from-secret

Use ``--token $CEREBRO_TOKEN`` or the ``CEREBRO_TOKEN`` env var instead of
``--token-from-secret`` if you cannot reach kubectl.

Flags
-----

- ``--url URL``             override the remote SSE endpoint
- ``--token TOKEN``         pass the bearer token explicitly
- ``--token-from-secret``   read the token from the K8s secret
                            ``cerebro-mcp-auth`` via ``kubectl``
- ``--skip-charts``         skip ``generate_charts`` and ``generate_report``
- ``--skip-query``          skip everything that touches ClickHouse
- ``--json``                emit one NDJSON line per step + a summary object
- ``--verbose``             print full tool output on each step
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client


# ─────────────────────────────────────────────────────────────────────────────
# Step result bookkeeping
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StepResult:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    duration_ms: int
    detail: str = ""
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class StepRunner:
    def __init__(self, *, json_mode: bool, verbose: bool) -> None:
        self.results: list[StepResult] = []
        self.json_mode = json_mode
        self.verbose = verbose

    def _emit(self, result: StepResult) -> None:
        self.results.append(result)
        if self.json_mode:
            print(
                json.dumps(
                    {
                        "event": "step",
                        "name": result.name,
                        "status": result.status,
                        "duration_ms": result.duration_ms,
                        "detail": result.detail,
                        "error": result.error,
                        **result.extra,
                    },
                    default=str,
                ),
                flush=True,
            )
            return
        name_col = f"{result.name} ".ljust(42, ".")
        status_col = f"{result.status:<4}"
        timing = f"({result.duration_ms}ms)"
        line = f"[mcp-smoke] {name_col} {status_col} {timing}"
        if result.detail:
            line += f"  {result.detail}"
        print(line, flush=True)
        if result.error and self.verbose:
            for raw in result.error.splitlines():
                print(f"    ! {raw}", flush=True)

    async def run(
        self,
        name: str,
        coro_factory,
        *,
        skip: bool = False,
        skip_reason: str = "",
    ) -> StepResult:
        if skip:
            result = StepResult(
                name=name, status="SKIP", duration_ms=0, detail=skip_reason
            )
            self._emit(result)
            return result
        started = time.perf_counter()
        try:
            detail, extra = await coro_factory()
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = StepResult(
                name=name,
                status="PASS",
                duration_ms=duration_ms,
                detail=detail,
                extra=extra or {},
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = StepResult(
                name=name,
                status="FAIL",
                duration_ms=duration_ms,
                error=_short_error(exc),
            )
        self._emit(result)
        return result


def _short_error(exc: BaseException, limit: int = 400) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Token helpers
# ─────────────────────────────────────────────────────────────────────────────


def _token_from_kube_secret() -> str:
    cmd = [
        "kubectl",
        "-n",
        "analytics-preview",
        "get",
        "secret",
        "cerebro-mcp-auth",
        "-o",
        "jsonpath={.data.authToken}",
    ]
    raw = subprocess.run(
        cmd, check=True, capture_output=True, text=True
    ).stdout.strip()
    return base64.b64decode(raw).decode("ascii").strip()


def _resolve_token(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if args.token_from_secret:
        return _token_from_kube_secret()
    env_token = os.environ.get("CEREBRO_TOKEN", "").strip()
    if env_token:
        return env_token
    raise SystemExit(
        "No bearer token available. Use --token, --token-from-secret, "
        "or set CEREBRO_TOKEN in the environment."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool-result helpers
# ─────────────────────────────────────────────────────────────────────────────


def _tool_text(result: Any) -> str:
    """Concatenate all text blocks from a CallToolResult."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _tool_is_error(result: Any) -> bool:
    return bool(getattr(result, "isError", False))


def _tool_structured(result: Any) -> Any:
    return getattr(result, "structuredContent", None)


def _require_not_error(name: str, result: Any) -> str:
    text = _tool_text(result)
    if _tool_is_error(result):
        raise RuntimeError(f"{name} returned isError=True: {text[:400]}")
    # Cerebro tools sometimes return "Error: ..." in a plain text block
    # without setting isError. Catch that too.
    stripped = text.lstrip()
    if stripped.startswith("Error:") or stripped.startswith("Query rejected:"):
        raise RuntimeError(f"{name} returned error text: {text[:400]}")
    return text


def _unwrap_structured(result: Any) -> Any:
    """Return the structured payload, stripping the optional {'result': ...} wrapper."""
    sc = _tool_structured(result)
    if isinstance(sc, dict) and "result" in sc and len(sc) == 1:
        return sc["result"]
    return sc


# ─────────────────────────────────────────────────────────────────────────────
# Test steps
# ─────────────────────────────────────────────────────────────────────────────


CORE_TOOLS = {
    "execute_query",
    "search_models",
    "discover_models",
    "describe_table",
    "list_tables",
    "list_databases",
    "get_sample_data",
    "get_model_details",
    "search_docs",
    "explain_query",
}
VIZ_TOOLS = {
    "generate_chart",
    "generate_charts",
    "generate_report",
    "list_reports",
    "open_report",
    "export_report",
    "quick_chart",
    "list_charts",
    "generate_metric_charts",
    "quick_metric_chart",
}
METRIC_TOOLS = {
    "discover_metrics",
    "get_metric_details",
    "query_metrics",
    "explain_metric_query",
    "preflight_analytics_request",
    "get_clickhouse_query_rules",
}


def _bucket_tools(names: list[str]) -> dict[str, int]:
    buckets = {
        "total": len(names),
        "core": 0,
        "viz": 0,
        "metric": 0,
        "storyteller": 0,
        "research": 0,
        "custom_get": 0,
        "other": 0,
    }
    for n in names:
        if n in CORE_TOOLS:
            buckets["core"] += 1
        elif n in VIZ_TOOLS:
            buckets["viz"] += 1
        elif n in METRIC_TOOLS:
            buckets["metric"] += 1
        elif n.startswith("storyteller_"):
            buckets["storyteller"] += 1
        elif "research" in n or n.startswith("plan_") or n.startswith(
            "record_"
        ) or n.startswith("publish_"):
            buckets["research"] += 1
        elif n.startswith("get_") and "research" not in n:
            buckets["custom_get"] += 1
        else:
            buckets["other"] += 1
    return buckets


_NUMERIC_CH_TYPES = (
    "UInt8", "UInt16", "UInt32", "UInt64", "UInt128", "UInt256",
    "Int8", "Int16", "Int32", "Int64", "Int128", "Int256",
    "Float32", "Float64", "Decimal",
)


def _pick_date_column(columns: list[dict]) -> str:
    for c in columns:
        ctype = str(c.get("type", ""))
        if ctype.startswith(("Date", "DateTime")):
            return c.get("name", "")
    return ""


def _pick_numeric_column(columns: list[dict], exclude: set[str]) -> str:
    for c in columns:
        name = c.get("name", "")
        if name in exclude:
            continue
        ctype = str(c.get("type", ""))
        # Strip LowCardinality(), Nullable() wrappers
        inner = ctype
        for wrapper in ("LowCardinality(", "Nullable("):
            if inner.startswith(wrapper) and inner.endswith(")"):
                inner = inner[len(wrapper):-1]
        if any(inner.startswith(t) for t in _NUMERIC_CH_TYPES):
            return name
    return ""


async def run_suite(
    session: ClientSession,
    *,
    runner: StepRunner,
    skip_query: bool,
    skip_charts: bool,
    search_query: str,
) -> None:
    # Holders passed between steps
    discovered: dict[str, Any] = {
        "models": [],          # list of model names from search_models
        "target_model": "",    # model used for describe/execute_query/charts
        "date_col": "",
        "num_col": "",
        "num_col_2": "",       # second numeric column for correlation/scatter
        "columns": [],         # all columns of target_model
    }

    # Step 1 — initialize
    async def step_init():
        init_result = await session.initialize()
        name = getattr(init_result.serverInfo, "name", "?")
        version = getattr(init_result.serverInfo, "version", "?")
        proto = getattr(init_result, "protocolVersion", "?")
        instr = getattr(init_result, "instructions", "") or ""
        detail = (
            f"server={name} v={version} "
            f"proto={proto} instructions={len(instr)}ch"
        )
        return detail, {
            "server_name": name,
            "server_version": version,
            "protocol_version": str(proto),
            "instructions_len": len(instr),
        }

    await runner.run("initialize", step_init)

    # Step 2 — tools/list
    async def step_tools_list():
        resp = await session.list_tools()
        names = [t.name for t in resp.tools]
        buckets = _bucket_tools(names)
        detail = (
            f"total={buckets['total']} core={buckets['core']} "
            f"viz={buckets['viz']} metric={buckets['metric']} "
            f"storyteller={buckets['storyteller']} "
            f"research={buckets['research']} custom_get={buckets['custom_get']} "
            f"other={buckets['other']}"
        )
        if buckets["total"] < 70:
            raise RuntimeError(f"Too few tools: got {buckets['total']}, expected ≥70")
        missing = [n for n in ("execute_query", "search_models", "generate_charts", "generate_report") if n not in names]
        if missing:
            raise RuntimeError(f"Missing essential tools: {missing}")
        return detail, {"tool_count": len(names), "buckets": buckets}

    await runner.run("tools/list", step_tools_list)

    # Step 3 — prompts/list
    async def step_prompts_list():
        try:
            resp = await session.list_prompts()
        except Exception as exc:
            raise RuntimeError(f"list_prompts failed: {exc}") from exc
        names = [p.name for p in resp.prompts]
        detail = f"total={len(names)}"
        return detail, {"prompt_count": len(names), "names": names[:8]}

    await runner.run("prompts/list", step_prompts_list)

    # Step 4 — resources/list
    async def step_resources_list():
        try:
            resp = await session.list_resources()
        except Exception as exc:
            raise RuntimeError(f"list_resources failed: {exc}") from exc
        uris = [str(r.uri) for r in resp.resources]
        detail = f"total={len(uris)}"
        return detail, {"resource_count": len(uris), "uris": uris}

    await runner.run("resources/list", step_resources_list)

    # Everything below touches ClickHouse. Skip if requested.

    # Step 5a — preflight_analytics_request FIRST (semantic enabled → required
    # before search_models / generate_charts). Non-fatal if it errors.
    async def step_preflight():
        result = await session.call_tool(
            "preflight_analytics_request",
            {
                "query": "mixpanel_ga events overview 30 days for smoke test",
                "mode": "report",
            },
        )
        text = _tool_text(result)
        if _tool_is_error(result):
            return f"preflight error (continuing): {text[:60]}", {}
        return "ok", {}

    await runner.run(
        "preflight_analytics_request",
        step_preflight,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 5b — search_models — DYNAMIC discovery of api_*_daily models
    async def step_search_models():
        result = await session.call_tool(
            "search_models", {"query": search_query, "limit": 80}
        )
        text = _require_not_error("search_models", result)
        import re

        # Pull every distinct api_*_daily model mentioned in the response
        hits = re.findall(r"\bapi_[A-Za-z0-9_]*_daily\b", text)
        hits = [h for h in dict.fromkeys(hits)]  # preserve order, dedupe
        if len(hits) < 3:
            raise RuntimeError(
                f"search_models(query={search_query!r}) returned fewer than 3 "
                f"'api_*_daily' models. Got: {hits}. First 400 chars: {text[:400]}"
            )
        discovered["models"] = hits
        discovered["target_model"] = hits[0]
        detail = (
            f"api_*_daily matches={len(hits)} "
            f"target={hits[0]}"
        )
        return detail, {"models": hits[:10]}

    await runner.run(
        f"search_models(query={search_query!r})",
        step_search_models,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 6 — get_model_details on 3 discovered models (gate requires ≥3)
    async def step_get_model_details():
        targets = discovered["models"][:3]
        if len(targets) < 3:
            raise RuntimeError(
                f"Need 3 models from search, got {len(targets)}: {targets}"
            )
        for m in targets:
            result = await session.call_tool(
                "get_model_details", {"model_name": m}
            )
            _require_not_error(f"get_model_details({m})", result)
        return f"detailed={targets}", {"models": targets}

    await runner.run(
        "get_model_details x3",
        step_get_model_details,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 7 — list_databases
    async def step_list_databases():
        result = await session.call_tool("list_databases", {})
        text = _require_not_error("list_databases", result)
        has_dbt = "dbt" in text.lower()
        if not has_dbt:
            raise RuntimeError(f"'dbt' database not in list. First 400 chars: {text[:400]}")
        return "has_dbt=True", {}

    await runner.run(
        "list_databases",
        step_list_databases,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 8 — list_tables(database=dbt, name_pattern=<search_query>)
    # NOTE: cerebro's list_tables queries the ClickHouse system catalog
    # which may return 0 rows for dbt views even though they're queryable.
    # We only assert the tool does not error.
    async def step_list_tables():
        result = await session.call_tool(
            "list_tables",
            {"database": "dbt", "name_pattern": search_query, "page_size": 50},
        )
        _require_not_error("list_tables", result)
        structured = _unwrap_structured(result) or {}
        tables_count = len(structured.get("tables", [])) if isinstance(
            structured, dict
        ) else 0
        return f"tables={tables_count}", {"tables": tables_count}

    await runner.run(
        f"list_tables(dbt, {search_query})",
        step_list_tables,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 9 — describe_table on the target model discovered in step 5b
    async def step_describe_table():
        target = discovered["target_model"]
        if not target:
            raise RuntimeError("no target model from search_models step")
        result = await session.call_tool(
            "describe_table", {"database": "dbt", "table": target}
        )
        text = _require_not_error(f"describe_table({target})", result)
        structured = _unwrap_structured(result) or {}
        columns = structured.get("columns", []) if isinstance(
            structured, dict
        ) else []
        if len(columns) < 2:
            raise RuntimeError(
                f"describe_table({target}) returned {len(columns)} columns. "
                f"First 400 chars: {text[:400]}"
            )
        discovered["columns"] = columns
        date_col = _pick_date_column(columns)
        num_col = _pick_numeric_column(columns, exclude=set())
        num_col_2 = _pick_numeric_column(
            columns, exclude={num_col} if num_col else set()
        )
        if not date_col:
            raise RuntimeError(
                f"no Date column found in {target}: "
                f"{[c.get('name') for c in columns]}"
            )
        if not num_col:
            raise RuntimeError(
                f"no numeric column found in {target}: "
                f"{[(c.get('name'), c.get('type')) for c in columns]}"
            )
        discovered["date_col"] = date_col
        discovered["num_col"] = num_col
        discovered["num_col_2"] = num_col_2 or num_col
        detail = (
            f"columns={len(columns)} date={date_col} "
            f"num={num_col} num2={discovered['num_col_2']}"
        )
        return detail, {
            "columns": len(columns),
            "date_col": date_col,
            "num_col": num_col,
            "num_col_2": discovered["num_col_2"],
        }

    await runner.run(
        "describe_table(target)",
        step_describe_table,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 10 — get_sample_data
    async def step_sample():
        target = discovered["target_model"]
        if not target:
            raise RuntimeError("no target model from search_models step")
        result = await session.call_tool(
            "get_sample_data",
            {"database": "dbt", "table": target, "limit": 3},
        )
        _require_not_error("get_sample_data", result)
        return "rows<=3", {}

    await runner.run(
        "get_sample_data",
        step_sample,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 11 — execute_query #1 (statistical: quantiles) — uses the
    # dynamically-discovered target_model, date column, and numeric column.
    exec_query_count = 0

    async def step_execute_query_stats():
        nonlocal exec_query_count
        target = discovered["target_model"]
        date_col = discovered["date_col"]
        num_col = discovered["num_col"]
        sql = (
            f"SELECT count() AS n, "
            f"quantiles(0.5, 0.9, 0.99)(toFloat64({num_col})) AS p "
            f"FROM dbt.{target} "
            f"WHERE {date_col} >= today() - INTERVAL 90 DAY"
        )
        result = await session.call_tool(
            "execute_query", {"sql": sql, "database": "dbt", "max_rows": 5}
        )
        _require_not_error("execute_query(stats)", result)
        structured = _unwrap_structured(result) or {}
        rows = 0
        if isinstance(structured, dict):
            rows = (
                structured.get("rows_returned")
                or structured.get("row_count")
                or 0
            )
        exec_query_count += 1
        return f"rows={rows}", {"exec_query_count": exec_query_count}

    await runner.run(
        "execute_query(quantiles 90d)",
        step_execute_query_stats,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 12 — execute_query #2 (correlation)
    async def step_execute_query_corr():
        nonlocal exec_query_count
        target = discovered["target_model"]
        date_col = discovered["date_col"]
        num_col = discovered["num_col"]
        num_col_2 = discovered["num_col_2"]
        sql = (
            f"SELECT corr(toFloat64({num_col}), toFloat64({num_col_2})) AS c "
            f"FROM dbt.{target} "
            f"WHERE {date_col} >= today() - INTERVAL 90 DAY"
        )
        result = await session.call_tool(
            "execute_query", {"sql": sql, "database": "dbt", "max_rows": 5}
        )
        _require_not_error("execute_query(corr)", result)
        exec_query_count += 1
        return "ok", {"exec_query_count": exec_query_count}

    await runner.run(
        "execute_query(corr)",
        step_execute_query_corr,
        skip=skip_query,
        skip_reason="--skip-query",
    )

    # Step 13 — generate_charts (batch of 4 — satisfies diversity +
    #           dimensional + relational gates)
    chart_ids_holder: dict[str, list[str]] = {"ids": []}

    async def step_generate_charts():
        target = discovered["target_model"]
        date_col = discovered["date_col"]
        num_col = discovered["num_col"]
        num_col_2 = discovered["num_col_2"]
        base_where = f"{date_col} >= today() - INTERVAL 90 DAY"
        specs = [
            {
                "chart_type": "line",
                "sql": (
                    f"SELECT {date_col} AS day, "
                    f"sum(toFloat64({num_col})) AS y "
                    f"FROM dbt.{target} WHERE {base_where} "
                    f"GROUP BY day ORDER BY day"
                ),
                "title": f"Daily {num_col} trend (90d)",
                "x_field": "day",
                "y_field": "y",
            },
            {
                "chart_type": "bar",
                "sql": (
                    f"SELECT toString({date_col}) AS day, "
                    f"sum(toFloat64({num_col})) AS y "
                    f"FROM dbt.{target} WHERE {base_where} "
                    f"GROUP BY day ORDER BY day DESC LIMIT 14"
                ),
                "title": f"Last 14 days of {num_col}",
                "x_field": "day",
                "y_field": "y",
                "series_field": "day",
            },
            {
                "chart_type": "scatter",
                "sql": (
                    f"SELECT toFloat64({num_col}) AS x, "
                    f"toFloat64({num_col_2}) AS y "
                    f"FROM dbt.{target} WHERE {base_where} LIMIT 200"
                ),
                "title": f"{num_col} vs {num_col_2}",
                "x_field": "x",
                "y_field": "y",
            },
            {
                "chart_type": "line",
                "sql": (
                    f"SELECT {date_col} AS day, "
                    f"sum(toFloat64({num_col_2})) AS y "
                    f"FROM dbt.{target} WHERE {base_where} "
                    f"GROUP BY day ORDER BY day"
                ),
                "title": f"Daily {num_col_2} trend (90d)",
                "x_field": "day",
                "y_field": "y",
            },
        ]
        result = await session.call_tool("generate_charts", {"charts": specs})
        text = _require_not_error("generate_charts", result)
        # Parse the chart IDs out of the markdown table the tool returns
        import re

        ids = re.findall(r"chart_\d+", text)
        ids = sorted(set(ids), key=lambda s: int(s.split("_")[1]))
        if len(ids) < 3:
            raise RuntimeError(
                f"generate_charts returned only {len(ids)} chart IDs. "
                f"First 800 chars: {text[:800]}"
            )
        chart_ids_holder["ids"] = ids
        return f"chart_ids={ids[:6]}", {"chart_ids": ids}

    await runner.run(
        "generate_charts(4 specs)",
        step_generate_charts,
        skip=skip_query or skip_charts,
        skip_reason="--skip-charts",
    )

    # Step 14 — generate_report
    async def step_generate_report():
        ids = chart_ids_holder["ids"]
        if len(ids) < 3:
            raise RuntimeError(
                f"cannot build report: only {len(ids)} chart IDs"
            )
        chart_refs = [f"{{{{chart:{cid}}}}}" for cid in ids[:4]]
        content = (
            "## Mixpanel GA smoke report\n\n"
            "Auto-generated by `scripts/mcp_smoke_test.py`. Four charts: "
            "top events, daily trend, self-scatter, top browsers.\n\n"
            "{{grid:2}}\n"
            f"{chart_refs[0]}\n"
            f"{chart_refs[1]}\n"
            "{{/grid}}\n\n"
            "{{grid:2}}\n"
            f"{chart_refs[2]}\n"
            f"{chart_refs[3] if len(chart_refs) > 3 else chart_refs[0]}\n"
            "{{/grid}}\n"
        )
        result = await session.call_tool(
            "generate_report",
            {
                "title": (
                    f"Cerebro smoke test — {discovered.get('target_model', 'unknown')}"
                ),
                "content_markdown": content,
            },
        )
        text = _require_not_error("generate_report", result)

        import re

        # Text format is: "Report ID: `<id>` | Charts: N ..."
        m = re.search(r"Report ID:\s*`([0-9a-f]{6,})`", text)
        report_id = m.group(1) if m else ""
        m = re.search(r"Charts:\s*(\d+)", text)
        chart_count = int(m.group(1)) if m else 0
        title = ""
        m = re.search(r"\*\*Report:\*\*\s*(.+)", text)
        if m:
            title = m.group(1).strip().splitlines()[0]

        structured = _tool_structured(result)
        has_charts = (
            isinstance(structured, dict) and bool(structured.get("charts"))
        )
        if not report_id:
            raise RuntimeError(
                f"generate_report did not surface a Report ID. "
                f"First 400 chars: {text[:400]}"
            )
        detail = (
            f"report_id={report_id} charts={chart_count} "
            f"has_structured={has_charts}"
        )
        return detail, {
            "report_id": report_id,
            "chart_count": chart_count,
            "title": title,
        }

    await runner.run(
        "generate_report",
        step_generate_report,
        skip=skip_query or skip_charts,
        skip_reason="--skip-charts",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transport drivers
# ─────────────────────────────────────────────────────────────────────────────


async def run_local(args: argparse.Namespace, runner: StepRunner) -> None:
    params = StdioServerParameters(
        command=args.local_command,
        args=args.local_args,
        env=None,
        cwd=args.local_cwd or None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await run_suite(
                session,
                runner=runner,
                skip_query=args.skip_query,
                skip_charts=args.skip_charts,
                search_query=args.query,
            )


async def run_remote(args: argparse.Namespace, runner: StepRunner) -> None:
    token = _resolve_token(args)
    headers = {"Authorization": f"Bearer {token}"}
    async with sse_client(
        url=args.url,
        headers=headers,
        timeout=10,
        sse_read_timeout=120,
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await run_suite(
                session,
                runner=runner,
                skip_query=args.skip_query,
                skip_charts=args.skip_charts,
                search_query=args.query,
            )


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry
# ─────────────────────────────────────────────────────────────────────────────


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="End-to-end smoke test for cerebro-mcp (local or remote).",
    )
    p.add_argument(
        "--transport",
        choices=("local", "remote"),
        required=True,
        help="local → stdio subprocess; remote → SSE over HTTPS",
    )
    p.add_argument(
        "--url",
        default="https://mcp.analytics.gnosis.io/sse",
        help="Remote SSE URL (default: preview deployment)",
    )
    p.add_argument("--token", default=None, help="Bearer token (remote)")
    p.add_argument(
        "--token-from-secret",
        action="store_true",
        help=(
            "Pull token via `kubectl -n analytics-preview get secret "
            "cerebro-mcp-auth -o jsonpath='{.data.authToken}' | base64 -d`"
        ),
    )
    p.add_argument(
        "--local-command",
        default="uv",
        help="Command to run the local stdio server (default: uv)",
    )
    p.add_argument(
        "--local-args",
        nargs="*",
        default=["run", "cerebro-mcp"],
        help='Args to the local stdio command (default: run cerebro-mcp)',
    )
    p.add_argument(
        "--local-cwd",
        default=None,
        help="Working directory for the local subprocess (default: current dir)",
    )
    p.add_argument(
        "--query",
        default="daily",
        help=(
            "Search term for search_models (default: 'daily'). The test "
            "picks the first 3 'api_*_daily' model names from the response "
            "and uses the first as the target for describe_table / "
            "execute_query / generate_charts."
        ),
    )
    p.add_argument(
        "--skip-query",
        action="store_true",
        help="Skip everything that touches ClickHouse (handshake-only)",
    )
    p.add_argument(
        "--skip-charts",
        action="store_true",
        help="Skip generate_charts and generate_report",
    )
    p.add_argument("--json", action="store_true", help="Emit NDJSON output")
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print full error traces on failing steps",
    )
    return p


def _print_header(args: argparse.Namespace, json_mode: bool) -> None:
    if json_mode:
        print(
            json.dumps(
                {
                    "event": "start",
                    "transport": args.transport,
                    "url": args.url if args.transport == "remote" else None,
                    "skip_query": args.skip_query,
                    "skip_charts": args.skip_charts,
                }
            ),
            flush=True,
        )
        return
    dst = args.url if args.transport == "remote" else (
        f"{args.local_command} {' '.join(args.local_args)}"
    )
    print(f"[mcp-smoke] transport={args.transport} target={dst}", flush=True)


def _print_summary(runner: StepRunner) -> int:
    total = len(runner.results)
    passed = sum(1 for r in runner.results if r.status == "PASS")
    failed = sum(1 for r in runner.results if r.status == "FAIL")
    skipped = sum(1 for r in runner.results if r.status == "SKIP")
    if runner.json_mode:
        print(
            json.dumps(
                {
                    "event": "summary",
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                }
            ),
            flush=True,
        )
    else:
        print("[mcp-smoke] " + "-" * 40, flush=True)
        print(
            f"[mcp-smoke] PASSED {passed}/{total}  "
            f"(failed={failed}, skipped={skipped})  exit={failed}",
            flush=True,
        )
    return failed


def main() -> int:
    args = _build_argparser().parse_args()
    runner = StepRunner(json_mode=args.json, verbose=args.verbose)
    _print_header(args, args.json)

    try:
        if args.transport == "local":
            asyncio.run(run_local(args, runner))
        else:
            asyncio.run(run_remote(args, runner))
    except Exception as exc:  # noqa: BLE001
        # Connection-level failure before any step — emit a synthetic step
        runner.results.append(
            StepResult(
                name="connect",
                status="FAIL",
                duration_ms=0,
                error=_short_error(exc),
            )
        )
        if args.verbose and not args.json:
            import traceback

            traceback.print_exc()
        else:
            print(
                f"[mcp-smoke] connect ............................. FAIL     "
                f"{_short_error(exc)}",
                flush=True,
            )
    return _print_summary(runner)


if __name__ == "__main__":
    sys.exit(main())
