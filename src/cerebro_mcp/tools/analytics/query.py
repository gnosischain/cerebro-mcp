from __future__ import annotations

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.research.store import ResearchStore
from cerebro_mcp.safety import validate_query
from cerebro_mcp.models.tool import ExplainResult, QueryResult
from cerebro_mcp.runtime.tool_output import (
    build_explain_summary,
    build_query_summary,
    format_results_table,
    normalize_rows,
    truncate_response,
    truncate_sql as _truncate_sql,
)

# Session query counter and nudge state for report workflow
_query_count = 0
_last_nudge_time: float = 0.0
_NUDGE_COOLDOWN = 300  # seconds between nudges (5 min)


def register_query_tools(
    mcp,
    ch: ClickHouseManager,
    research_store: ResearchStore | None = None,
):
    @mcp.tool()
    def execute_query(
        sql: str,
        database: str = "dbt",
        max_rows: int = 100,
        research_project_id: str = "",
        persist_result: bool = False,
        evidence_title: str = "",
        persist_max_rows: int | None = None,
        explain_context: bool = False,
    ) -> QueryResult | str:
        """Execute a read-only SQL query against a Gnosis Chain ClickHouse database.

        IMPORTANT: Before calling this tool, you MUST first call `describe_table`
        or `get_model_details` to verify exact column names. Column names are
        non-obvious (e.g., `value` not `staked_gno`, `cnt` not `count`,
        `txs` not `transactions`). Never guess column names.

        Set `explain_context=True` to append a "What this shows" section that
        explains the dbt models and key columns behind the result, sourced from
        the dbt documentation.
        """
        try:
            in_research = bool(research_project_id)
            if persist_result and not research_project_id:
                return (
                    "Error: `persist_result=True` requires `research_project_id` so "
                    "the query snapshot can be stored in a research project."
                )
            if research_project_id and research_store is None:
                return "Error: Research storage is not configured on this server."

            if research_project_id and research_store is not None:
                research_store.load_project(research_project_id)

            extra_notes: list[str] = []
            executed = ch.run_query(
                sql,
                database,
                requested_max_rows=(
                    min(
                        persist_max_rows or settings.MAX_ROWS,
                        settings.MAX_ROWS,
                    )
                    if persist_result
                    else max_rows
                ),
                audience="internal" if persist_result else "tool",
                fetch_mode="auto",
            )
            result = ch.build_query_result(executed, max_rows=max_rows)

            result_ref_id: str | None = None
            if persist_result and research_store is not None:
                persist_cap = min(
                    persist_max_rows or settings.MAX_ROWS,
                    settings.MAX_ROWS,
                )
                artifact_rows = normalize_rows(executed.rows[:persist_cap])
                result_ref_id = research_store.save_query_result_artifact(
                    project_id=research_project_id,
                    title=evidence_title.strip(),
                    sql=executed.sql,
                    database=executed.database,
                    columns=executed.columns,
                    rows=artifact_rows,
                    row_count=executed.row_count,
                )
                extra_notes.append(
                    f"**Research Snapshot:** `{result_ref_id}` stored for project "
                    f"`{research_project_id}`."
                )

            if not in_research:
                global _query_count
                _query_count += 1

                from cerebro_mcp.tools.governance.session_state import state

                state.record_execute_query(sql)

                if _query_count >= 2:
                    import time as _time

                    global _last_nudge_time
                    now = _time.monotonic()
                    if now - _last_nudge_time > _NUDGE_COOLDOWN:
                        nudge_text = state.suggest_statistical_functions(sql)
                        if nudge_text:
                            extra_notes.append(f"> **Tip:** {nudge_text}")

                        if _query_count >= 3:
                            from cerebro_mcp.tools.visualization.charts import _chart_registry

                            if _chart_registry:
                                extra_notes.append(
                                    f"> **Reminder:** You have {len(_chart_registry)} "
                                    "chart(s) registered. Call "
                                    "`generate_report(title, content_markdown)` with "
                                    "`{{chart:CHART_ID}}` placeholders to produce the "
                                    "interactive report."
                                )
                            else:
                                extra_notes.append(
                                    "> **Tip:** To create charts and a visual report, "
                                    "use `generate_charts([...])` in one batch call, "
                                    "use single-row SQL for `numberDisplay` KPI cards, "
                                    "use separate time-series queries for trend charts, "
                                    "then `generate_report(title, content_markdown)`."
                                )
                        if extra_notes and _query_count >= 3:
                            _last_nudge_time = now

            summary = build_query_summary(
                columns=result.columns,
                rows=result.rows,
                row_count=result.row_count,
                rows_returned=result.rows_returned,
                elapsed_seconds=result.elapsed_seconds,
                database=result.database,
                sql=result.sql,
                warnings=result.warnings,
                extra_notes=extra_notes,
                explain_context=explain_context,
            )
            # Step 1 expansion — record the query in the workflow event
            # log. Only fires when the agent passed `research_project_id`,
            # so non-research queries don't pollute the log. Captures sql
            # preview, row count, latency, evidence_title, and the
            # artifact_ref_id when persist_result was set. Resume can
            # then surface "agent ran 88 queries, 3 failed" instead of
            # nothing.
            if research_project_id:
                from cerebro_mcp.workflow.event_store_sync import (
                    record_research_query_executed,
                )
                record_research_query_executed(
                    project_id=research_project_id,
                    sql=executed.sql,
                    database=executed.database,
                    row_count=result.row_count,
                    elapsed_seconds=result.elapsed_seconds,
                    evidence_title=evidence_title,
                    artifact_ref_id=result_ref_id,
                )
            return result.model_copy(
                update={
                    "summary_markdown": summary,
                    "result_ref_id": result_ref_id,
                }
            )
        except Exception as e:
            error_msg = str(e)
            # Step 1 expansion — record the failed query too. The agent
            # on resume sees "queries that failed with what error_class"
            # so it doesn't blindly retry the same hallucinated SQL.
            if research_project_id:
                try:
                    from cerebro_mcp.workflow.event_store_sync import (
                        record_research_query_executed,
                    )
                    # Best-effort error class extraction: "Code: NN" comes
                    # from ClickHouse messages; otherwise use exception type.
                    import re as _re
                    m = _re.search(r"Code:\s*(\d+)", error_msg)
                    error_class = (
                        f"clickhouse_code_{m.group(1)}" if m
                        else type(e).__name__
                    )
                    record_research_query_executed(
                        project_id=research_project_id,
                        sql=sql, database=database,
                        row_count=0, elapsed_seconds=0.0,
                        evidence_title=evidence_title,
                        error_class=error_class,
                    )
                except Exception:
                    # Event-log failure must never break a tool response.
                    pass
            if "UNKNOWN_IDENTIFIER" in error_msg or "Unknown expression" in error_msg:
                import re

                table_match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
                table_hint = (
                    f" Use `describe_table` on '{table_match.group(1)}' "
                    "to see exact column names."
                    if table_match
                    else " Use `describe_table` to check exact column names."
                )
                return (
                    f"Error: {error_msg}\n\n"
                    f"**Hint**: Wrong column name.{table_hint} "
                    "Do NOT guess — verify the schema first."
                )
            return f"Error: {e}"

    @mcp.tool()
    def explain_query(
        sql: str,
        database: str = "dbt",
        explain_context: bool = False,
    ) -> ExplainResult | str:
        """Show the execution plan for a SQL query without running it.

        Set `explain_context=True` to append a "What this shows" section
        describing the dbt models referenced by the query.
        """
        try:
            is_valid, error = validate_query(sql)
            if not is_valid:
                return f"Query rejected: {error}"

            explain_sql = f"EXPLAIN {sql}"
            result = ch.execute_raw(explain_sql, database)
            lines = [str(row[0]) if row else "" for row in result["rows"]]
            raw_body = "\n".join(lines)
            summary = build_explain_summary(
                sql=sql, lines=lines, explain_context=explain_context
            )
            return ExplainResult(
                sql=sql,
                database=database,
                lines=lines,
                truncated=len(raw_body) > settings.effective_tool_result_max_chars,
                summary_markdown=summary,
            )
        except Exception as e:
            return f"Error: {e}"
