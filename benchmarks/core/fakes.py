"""Deterministic in-process server + ClickHouse stand-in for the benchmarks.

``BenchClickHouse`` merges the two fake idioms already used by the test suite
(``tests/rpc_scan_fakes.FakeCH`` and ``tests/test_semantic_tools.FakeClickHouse``)
into one scriptable class implementing the surface the benchmarked tools
actually touch: ``run_query`` / ``build_query_result`` / ``execute_raw`` /
``execute_raw_cached``. Gate mechanics never read query *results* (they regex
the SQL text after a successful call), so canned rows are sufficient for the
governance/workflow benchmarks; the canned shapes are realistic enough for the
chart pipeline's input-shape validation.

All ``cerebro_mcp`` imports are lazy (env-first discipline, see
``benchmarks/__init__``).
"""

from __future__ import annotations

import re
from typing import Any
from cerebro_mcp.runtime.mcp_server import CerebroFastMCP

_DATE_TOKENS = ("day", "date", "week", "month")
_CATEGORY_TOKENS = ("sector", "bridge", "token", "label", "pool", "client", "category", "symbol")
_CATEGORIES = ["defi", "bridge", "stable"]

_SELECT_RE = re.compile(r"\bselect\b(.*?)\bfrom\b", re.IGNORECASE | re.DOTALL)
_LIMIT_1_RE = re.compile(r"\blimit\s+1\b", re.IGNORECASE)
_LIMIT_N_RE = re.compile(r"\blimit\s+(\d+)", re.IGNORECASE)


def _split_top_level(select_list: str) -> list[str]:
    parts, depth, buf = [], 0, []
    for ch in select_list:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_select_columns(sql: str) -> list[str]:
    """Best-effort column names from a SELECT list (for canned result shaping).

    Falls back to a generic time-series shape on ``*`` or unparseable SQL —
    the benchmark's pinned SQL always uses simple aliases, so this only has to
    be right for those.
    """
    m = _SELECT_RE.search(sql)
    if not m:
        return ["day", "value", "sector"]
    cols: list[str] = []
    for part in _split_top_level(m.group(1)):
        if part == "*" or part.endswith(".*"):
            return ["day", "value", "sector"]
        alias_match = re.search(r"\bas\s+([`\"\w]+)\s*$", part, re.IGNORECASE)
        if alias_match:
            name = alias_match.group(1)
        else:
            name = re.split(r"[\s()]+", part)[-1] or part
            name = name.split(".")[-1]
        cols.append(name.strip('`"') or "col")
    return cols or ["day", "value", "sector"]


def _value_for(column: str, i: int) -> Any:
    lowered = column.lower()
    if any(tok in lowered for tok in _DATE_TOKENS):
        return f"2026-06-{(i % 30) + 1:02d}"
    if any(tok in lowered for tok in _CATEGORY_TOKENS):
        return _CATEGORIES[i % len(_CATEGORIES)]
    return round(1000.0 + i * 13.7 + (i % 7) * 3.1, 2)


class BenchClickHouse:
    """ClickHouseManager stand-in with canned, SQL-shaped results.

    Scriptable failure (Suite 5 repair-path cases): the first ``fail_times``
    ``run_query`` calls whose SQL matches ``fail_pattern`` (default: any)
    raise ``RuntimeError(fail_error)``.
    """

    def __init__(
        self,
        *,
        rows: int = 30,
        fail_times: int = 0,
        fail_error: str = "UNKNOWN_IDENTIFIER: sector",
        fail_pattern: str | None = None,
        overrides: list[tuple[str, tuple[list[str], list[list[Any]]]]] | None = None,
        table_columns: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.default_rows = rows
        self.fail_times = fail_times
        self.fail_error = fail_error
        self.fail_pattern = re.compile(fail_pattern, re.IGNORECASE) if fail_pattern else None
        self.overrides = [(re.compile(p, re.IGNORECASE | re.DOTALL), payload)
                          for p, payload in (overrides or [])]
        # table -> {column: data_type}; sourced from the recorded corpus so
        # describe_table returns realistic schemas.
        self.table_columns = table_columns or {}
        self.calls = 0
        self.queries: list[str] = []

    # -- data shaping -------------------------------------------------------

    def _canned(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        for pattern, (columns, rows) in self.overrides:
            if pattern.search(sql):
                return list(columns), [list(r) for r in rows]
        columns = parse_select_columns(sql)
        n = self.default_rows
        if _LIMIT_1_RE.search(sql) or (re.search(r"\bcount\(", sql, re.IGNORECASE)
                                       and not re.search(r"\bgroup by\b", sql, re.IGNORECASE)):
            n = 1
        else:
            limit = _LIMIT_N_RE.search(sql)
            if limit:
                n = min(n, int(limit.group(1)))
        rows = [[_value_for(c, i) for c in columns] for i in range(n)]
        return columns, rows

    # -- ClickHouseManager surface ------------------------------------------

    def run_query(
        self,
        sql: str,
        database: str = "dbt",
        requested_max_rows: int = 100,
        audience: str = "tool",
        fetch_mode: str = "auto",
        parameters: dict | None = None,
    ):
        from cerebro_mcp.clients.clickhouse import ExecutedQuery

        self.calls += 1
        self.queries.append(sql)
        if self.fail_times > 0 and (self.fail_pattern is None or self.fail_pattern.search(sql)):
            self.fail_times -= 1
            raise RuntimeError(self.fail_error)
        columns, rows = self._canned(sql)
        rows = rows[: max(1, requested_max_rows)]
        return ExecutedQuery(
            sql=sql,
            executed_sql=sql,
            database=database,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            elapsed_seconds=0.001,
            fetch_mode="rows",
            warnings=[],
        )

    def build_query_result(self, executed, *, max_rows: int | None = None):
        # Delegate to the REAL payload shaping (row/char budgets, warnings) so
        # fake mode exercises the production truncation path on canned data.
        from cerebro_mcp.clients.clickhouse import ClickHouseManager

        return ClickHouseManager.build_query_result(self, executed, max_rows=max_rows)

    @staticmethod
    def _dedupe_warnings(warnings: list[str]) -> list[str]:
        from cerebro_mcp.clients.clickhouse import ClickHouseManager

        return ClickHouseManager._dedupe_warnings(warnings)

    def execute_raw(self, sql: str, database: str = "dbt", parameters: dict | None = None) -> dict:
        params = parameters or {}
        if "system.columns" in sql:
            table = str(params.get("tbl", ""))
            cols = self.table_columns.get(table) or {
                "day": "Date", "value": "Float64", "sector": "String",
            }
            return {
                "columns": ["name", "type", "default_kind", "comment"],
                "rows": [[name, dtype or "String", "", ""] for name, dtype in cols.items()],
            }
        if "count() FROM system.tables" in sql:
            return {"columns": ["count()"], "rows": [[len(self.table_columns) or 42]]}
        if "FROM system.tables" in sql:
            names = sorted(self.table_columns) or ["api_bench_model_daily"]
            pattern = str(params.get("pat", "")).strip("%")
            last = str(params.get("last", ""))
            if pattern:
                names = [n for n in names if pattern in n]
            if last:
                names = [n for n in names if n > last]
            limit = int(params.get("limit", 50))
            return {
                "columns": ["name", "engine", "total_rows", "size"],
                "rows": [[n, "MergeTree", 1000, "1.00 MiB"] for n in names[:limit]],
            }
        columns, rows = self._canned(sql)
        return {"columns": columns, "rows": rows}

    def execute_raw_cached(self, sql: str, database: str, cache_key: str,
                           parameters: dict | None = None, *, page_cache: bool = False) -> dict:
        return self.execute_raw(sql, database, parameters=parameters)

    def get_client(self, database: str):
        raise RuntimeError(
            f"BenchClickHouse.get_client({database!r}) is not scripted — "
            "this tool needs a real ClickHouse (mark the case needs_clickhouse)"
        )


def bench_clickhouse_from_corpus(corpus: dict[str, dict], **kwargs: Any) -> BenchClickHouse:
    """BenchClickHouse whose describe/list surfaces mirror the recorded corpus."""
    return BenchClickHouse(
        table_columns={name: dict(m.get("columns") or {}) for name, m in corpus.items()},
        **kwargs,
    )


def build_bench_server(ch, *, tracing: bool = False):
    """Fresh FastMCP with the benchmarked tool families registered in
    server.py order (``register_find_tool`` second-to-last, ``load_tools``
    after it — mirrors ``src/cerebro_mcp/server.py``).

    The caller is responsible for loading/patching the manifest and semantic
    runtime first (see ``corpus_fixtures.install_fixture_manifest`` and
    ``semantic_env.deterministic_semantic_runtime``).
    """
    from types import SimpleNamespace

    from mcp.server.fastmcp import FastMCP

    from cerebro_mcp.tools.analytics.dbt import register_dbt_tools
    from cerebro_mcp.tools.analytics.lineage_graph import register_lineage_graph_tools
    from cerebro_mcp.tools.analytics.list_unifier import register_list_tool
    from cerebro_mcp.tools.analytics.metadata import register_metadata_tools
    from cerebro_mcp.tools.analytics.query import register_query_tools
    from cerebro_mcp.tools.analytics.schema import register_schema_tools
    from cerebro_mcp.tools.governance.cross_check import register_cross_check_tools
    from cerebro_mcp.tools.semantic.find import register_find_tool
    from cerebro_mcp.tools.semantic.graph_explorer import register_graph_explorer_tools
    from cerebro_mcp.tools.semantic.semantic import register_semantic_tools
    from cerebro_mcp.tools.visualization.charts import register_visualization_tools
    from cerebro_mcp.tools.visualization.mini_apps import (
        register_load_tools_tool,
        register_mini_app_infra,
    )

    mcp = CerebroFastMCP("bench")
    research_store = SimpleNamespace()
    register_query_tools(mcp, ch, research_store)
    register_schema_tools(mcp, ch)
    register_dbt_tools(mcp)
    register_lineage_graph_tools(mcp)
    register_metadata_tools(mcp, ch)
    register_visualization_tools(mcp, ch)
    register_semantic_tools(mcp, ch, research_store)
    register_cross_check_tools(mcp, ch)
    register_mini_app_infra(mcp, ch)
    register_graph_explorer_tools(mcp, ch)
    register_list_tool(mcp, ch)
    register_find_tool(mcp)
    register_load_tools_tool(mcp)

    if tracing:
        from cerebro_mcp.tools.governance.reasoning import install_auto_tool_tracing

        install_auto_tool_tracing(mcp)
    return mcp


def reset_server_state() -> None:
    """Clear the process-global state a workflow/latency case can leave behind
    (the ``tests/test_visualization.py`` isolation pattern, minus pytest)."""
    from cerebro_mcp.tools.governance.session_state import state
    from cerebro_mcp.tools.semantic.find import _reset_tool_corpus
    import cerebro_mcp.tools.analytics.query as query_mod
    import cerebro_mcp.tools.visualization.charts as viz

    state.reset()
    viz._chart_registry.clear()
    if hasattr(viz, "_REPORT_CACHE"):
        viz._REPORT_CACHE.clear()
    if hasattr(viz, "_chart_counter"):
        viz._chart_counter = 0
    if hasattr(viz, "_LAST_VISUAL"):
        viz._LAST_VISUAL["report_id"] = None
        viz._LAST_VISUAL["created_at"] = None
    if hasattr(query_mod, "_query_count"):
        query_mod._query_count = 0
    if hasattr(query_mod, "_last_nudge_time"):
        query_mod._last_nudge_time = 0.0
    _reset_tool_corpus()
