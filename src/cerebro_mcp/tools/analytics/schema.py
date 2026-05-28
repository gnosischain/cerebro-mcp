import base64
import hashlib
import json

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.loaders.manifest import manifest
from cerebro_mcp.safety import validate_identifier
from cerebro_mcp.models.tool import ColumnSchema, TableListPage, TableSchema, TableSummary
from cerebro_mcp.runtime.tool_output import (
    build_query_summary,
    format_results_table,
    normalize_value,
    truncate_response,
)


def _encode_page_token(database: str, pattern: str, last_name: str) -> str:
    payload = {"db": database, "pat": pattern, "last": last_name}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_token(
    page_token: str | None,
    *,
    database: str,
    pattern: str,
) -> str:
    if not page_token:
        return ""

    padding = "=" * (-len(page_token) % 4)
    raw = base64.urlsafe_b64decode(page_token + padding)
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("db") != database or payload.get("pat", "") != pattern:
        raise ValueError("page_token does not match the requested database/filter")
    last_name = payload.get("last", "")
    valid, err = validate_identifier(last_name) if last_name else (True, "")
    if not valid:
        raise ValueError(err)
    return last_name


def build_table_schema(
    ch: ClickHouseManager,
    *,
    table: str,
    database: str = "dbt",
    record_state: bool = True,
) -> TableSchema:
    sql = (
        "SELECT name, type, default_kind, comment "
        "FROM system.columns "
        "WHERE database = {db:String} AND table = {tbl:String} "
        "ORDER BY position"
    )
    cache_key = f"columns:{database}.{table}"
    result = ch.execute_raw_cached(
        sql,
        database,
        cache_key,
        parameters={"db": database, "tbl": table},
    )

    if not result["rows"]:
        raise ValueError(f"Table '{database}.{table}' not found or has no columns.")

    model = manifest.get_model(table)
    model_description = ""
    materialization = ""
    if model:
        model_description = model.get("description", "")
        materialization = model.get("config", {}).get("materialized", "")

    dbt_columns = {}
    if model:
        dbt_columns = {
            key.lower(): value
            for key, value in model.get("columns", {}).items()
        }

    columns = []
    enriched_rows = []
    for row in result["rows"]:
        col_name = str(row[0] or "")
        col_type = str(row[1] or "")
        default = str(row[2] or "")
        dbt_col = dbt_columns.get(col_name.lower(), {})
        description = str(
            dbt_col.get("description", "")
            or (row[3] if len(row) > 3 and row[3] else "")
        )
        columns.append(
            ColumnSchema(
                name=col_name,
                type=col_type,
                default_kind=default,
                description=description,
            )
        )
        enriched_rows.append([col_name, col_type, default, description])

    summary_parts = [f"## {database}.{table}\n"]
    if model_description:
        summary_parts.append(f"**Description:** {model_description}")
    if materialization:
        summary_parts.append(f"**Materialization:** {materialization}")
    summary_parts.append(
        format_results_table(
            ["name", "type", "default_kind", "description"],
            enriched_rows,
        )
    )

    # Phase 1.5 nudge: wide tables blow up LLM context if every column is
    # carried into the SQL-author prompt. The new `get_relevant_columns`
    # tool returns a BM25-scoped block (top-K columns + always-keep keys
    # and date columns). Hint at it from the describe_table response so
    # agents discover it without needing a persona prompt rewrite.
    threshold = getattr(settings, "SQL_COMPILER_FULL_SCHEMA_THRESHOLD", 30)
    if len(columns) > threshold:
        summary_parts.append(
            f"\n> **Wide table ({len(columns)} columns).** Before writing SQL, "
            f"prefer `get_relevant_columns(model_name=\"{table}\", "
            f"query=\"<your question>\")` to get a focused schema block "
            "scoped to the columns you actually need (always includes join "
            "keys and date columns)."
        )

    if record_state:
        from cerebro_mcp.tools.governance.session_state import state

        state.record_describe_table(table)

    return TableSchema(
        database=database,
        table=table,
        model_description=model_description,
        materialization=materialization,
        columns=columns,
        summary_markdown=truncate_response("\n\n".join(summary_parts)),
    )


def register_schema_tools(mcp, ch: ClickHouseManager):
    @mcp.tool()
    def list_tables(
        database: str,
        name_pattern: str = "",
        like: str = "",
        page_size: int = 50,
        page_token: str | None = None,
        include_detailed_columns: bool = False,
    ) -> TableListPage | str:
        """List tables in a ClickHouse database with cursor pagination."""
        try:
            valid, err = validate_identifier(database)
            if not valid:
                return f"Error: {err}"
            if database not in settings.ALLOWED_DATABASES:
                return (
                    f"Error: Database '{database}' is not allowed. "
                    f"Allowed: {', '.join(settings.ALLOWED_DATABASES)}"
                )

            pattern = like or name_pattern
            capped_page_size = min(max(page_size, 1), 200)
            warnings: list[str] = []
            if page_size != capped_page_size:
                warnings.append("page_size_capped")
            if include_detailed_columns:
                warnings.append(
                    "include_detailed_columns_ignored_use_describe_table"
                )

            last_name = _decode_page_token(
                page_token,
                database=database,
                pattern=pattern,
            )

            sql = """
SELECT name, engine, total_rows, formatReadableSize(total_bytes) AS size
FROM system.tables
WHERE database = {db:String}
  AND ({pat:String} = '' OR name LIKE {pat:String})
  AND ({last:String} = '' OR name > {last:String})
ORDER BY name
LIMIT {limit:UInt32}
"""
            params = {
                "db": database,
                "pat": pattern,
                "last": last_name,
                "limit": capped_page_size + 1,
            }

            cursor_key = hashlib.sha256((page_token or "").encode()).hexdigest()[:12]
            cache_key = (
                f"tables_page:{database}:{pattern}:{capped_page_size}:{cursor_key}"
            )
            result = ch.execute_raw_cached(
                sql,
                database,
                cache_key,
                parameters=params,
                page_cache=True,
            )

            rows = result["rows"]
            has_next = len(rows) > capped_page_size
            page_rows = rows[:capped_page_size]
            next_token = (
                _encode_page_token(database, pattern, str(page_rows[-1][0]))
                if has_next and page_rows
                else None
            )

            tables = [
                TableSummary(
                    name=str(row[0]),
                    engine=str(row[1]),
                    total_rows=normalize_value(row[2]),
                    size=str(row[3]),
                )
                for row in page_rows
            ]

            table_rows = [
                [table.name, table.engine, table.total_rows, table.size]
                for table in tables
            ]
            summary_parts = [f"## {database} tables\n"]
            if tables:
                summary_parts.append(
                    format_results_table(
                        ["name", "engine", "total_rows", "size"],
                        table_rows,
                    )
                )
            else:
                summary_parts.append(f"No tables found in database '{database}'.")

            if warnings:
                summary_parts.append(
                    "\n".join(["**Warnings:**", *[f"- {w}" for w in warnings]])
                )
            if next_token:
                summary_parts.append(
                    "More tables are available. Call `list_tables` again with "
                    "`page_token` to continue."
                )

            return TableListPage(
                database=database,
                name_pattern=pattern,
                page_size=capped_page_size,
                include_detailed_columns=include_detailed_columns,
                tables=tables,
                next_page_token=next_token,
                warnings=warnings,
                summary_markdown=truncate_response("\n\n".join(summary_parts)),
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def record_model_exclusion(
        model_name: str,
        reason: str = "",
    ) -> str:
        """Mark a model surfaced by `search_models` / `discover_models` as
        deliberately excluded from the current analysis. This suppresses the
        discovered-model coverage gate for that model so it does not block
        `generate_report` / `generate_research_report` /
        `generate_case_study_report`.

        Use when a discovered model is out of scope for the current report
        (wrong topic, downstream of the chosen lineage, or covered by a
        sibling model that was queried). Always pass a short `reason` for
        audit trail.

        For more than one model, use `record_model_exclusion_batch`,
        `exclude_models_by_prefix`, `exclude_module`, or
        `exclude_all_discovered_except` — calling this tool in a loop
        is the slowest possible path.
        """
        from cerebro_mcp.tools.governance.session_state import state

        if not model_name or not isinstance(model_name, str):
            return "Error: model_name is required"
        state.record_model_exclusion(model_name.strip(), reason.strip())
        excluded_count = len(state.excluded_models)
        return (
            f"Excluded '{model_name}' from coverage gate "
            f"(reason: {reason or 'unspecified'}). "
            f"Total excluded this cycle: {excluded_count}."
        )

    @mcp.tool()
    def record_model_exclusion_batch(
        model_names: list[str],
        reason: str = "",
    ) -> str:
        """Exclude multiple discovered models from the coverage gate in
        a single call. The `reason` applies to all entries.

        Prefer this over calling `record_model_exclusion` in a loop —
        the singular tool has the same gate semantics but costs one MCP
        round-trip per model. Use when you have an explicit list of
        out-of-scope model names; otherwise reach for
        `exclude_models_by_prefix`, `exclude_module`, or
        `exclude_all_discovered_except`.
        """
        from cerebro_mcp.tools.governance.session_state import state

        if not isinstance(model_names, list):
            return "Error: model_names must be a list of strings"
        cleaned = [
            n.strip() for n in model_names
            if isinstance(n, str) and n.strip()
        ]
        if not cleaned:
            return "Error: model_names is empty"
        reason_clean = reason.strip()
        for name in cleaned:
            state.record_model_exclusion(name, reason_clean)
        excluded_count = len(state.excluded_models)
        return (
            f"Excluded {len(cleaned)} model(s) from coverage gate "
            f"(reason: {reason_clean or 'unspecified'}). "
            f"Total excluded this cycle: {excluded_count}."
        )

    @mcp.tool()
    def exclude_models_by_prefix(
        prefix: str,
        reason: str = "",
    ) -> str:
        """Exclude every discovered model whose name starts with `prefix`.

        Resolves against the in-session ``discovered_models`` set
        (populated by ``search_models`` / ``discover_models`` /
        ``discover_metrics``), so callers do not have to enumerate
        names. Useful for sweeps like
        ``exclude_models_by_prefix("api_execution_circles_v2_",
        reason="trust network out of scope for referral analysis")``.
        """
        from cerebro_mcp.tools.governance.session_state import state

        if not isinstance(prefix, str) or not prefix.strip():
            return "Error: prefix is required"
        prefix_clean = prefix.strip()
        reason_clean = reason.strip()
        # Snapshot the set under the lock so the iteration is consistent.
        with state.lock:
            matches = sorted(
                m for m in state.discovered_models
                if m.startswith(prefix_clean)
            )
        for name in matches:
            state.record_model_exclusion(name, reason_clean)
        excluded_count = len(state.excluded_models)
        return (
            f"Excluded {len(matches)} model(s) matching prefix "
            f"'{prefix_clean}' (reason: "
            f"{reason_clean or 'unspecified'}). "
            f"Total excluded this cycle: {excluded_count}."
        )

    @mcp.tool()
    def exclude_module(
        module: str,
        reason: str = "",
    ) -> str:
        """Exclude every discovered model belonging to a dbt module.

        Reuses the manifest's module index (the same lookup that backs
        ``search_models(module=...)``). Only discovered models are
        excluded — models in the module that were never surfaced by a
        search are left untouched.

        Common modules: ``execution``, ``consensus``, ``contracts``,
        ``bridges``, ``p2p``, ``circles``, ``ESG``.
        """
        from cerebro_mcp.loaders.manifest import manifest
        from cerebro_mcp.tools.governance.session_state import state

        if not isinstance(module, str) or not module.strip():
            return "Error: module is required"
        module_clean = module.strip()
        reason_clean = reason.strip()
        if not manifest.is_loaded:
            return "Error: manifest not loaded — cannot resolve module"
        module_models = set(manifest.model_names_in_module(module_clean))
        if not module_models:
            return (
                f"No models found for module '{module_clean}'. "
                f"Available modules: "
                f"{', '.join(sorted(manifest.get_modules().keys()))}."
            )
        with state.lock:
            matches = sorted(state.discovered_models & module_models)
        for name in matches:
            state.record_model_exclusion(name, reason_clean)
        excluded_count = len(state.excluded_models)
        return (
            f"Excluded {len(matches)} discovered model(s) in module "
            f"'{module_clean}' (reason: "
            f"{reason_clean or 'unspecified'}). "
            f"Total excluded this cycle: {excluded_count}."
        )

    @mcp.tool()
    def exclude_all_discovered_except(
        keep: list[str],
        reason: str = "",
    ) -> str:
        """Inverse exclusion: keep the named discovered models in scope,
        exclude every other discovered model in one call.

        Use when discovery returned many models but only a handful are
        relevant. Pass the names you intend to query / explore as
        ``keep``; everything else surfaced by ``search_models`` /
        ``discover_models`` / ``discover_metrics`` this cycle is
        excluded with the shared ``reason``.
        """
        from cerebro_mcp.tools.governance.session_state import state

        if not isinstance(keep, list):
            return "Error: keep must be a list of model names"
        keep_clean = {
            n.strip() for n in keep
            if isinstance(n, str) and n.strip()
        }
        reason_clean = reason.strip()
        with state.lock:
            targets = sorted(state.discovered_models - keep_clean)
            kept_present = len(keep_clean & state.discovered_models)
        for name in targets:
            state.record_model_exclusion(name, reason_clean)
        excluded_count = len(state.excluded_models)
        return (
            f"Excluded {len(targets)} discovered model(s); kept "
            f"{kept_present} (reason: "
            f"{reason_clean or 'unspecified'}). "
            f"Total excluded this cycle: {excluded_count}."
        )

    @mcp.tool()
    def describe_table(
        table: str,
        database: str = "dbt",
    ) -> TableSchema | str:
        """Get the column schema for a specific table."""
        try:
            valid, err = validate_identifier(table)
            if not valid:
                return f"Error: {err}"
            valid, err = validate_identifier(database)
            if not valid:
                return f"Error: {err}"
            return build_table_schema(
                ch,
                table=table,
                database=database,
                record_state=True,
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_sample_data(
        table: str,
        database: str = "dbt",
        limit: int = 5,
    ) -> str:
        """Get sample rows from a table to understand data shape and values."""
        try:
            valid, err = validate_identifier(table)
            if not valid:
                return f"Error: {err}"
            valid, err = validate_identifier(database)
            if not valid:
                return f"Error: {err}"

            capped = min(max(limit, 1), 20)

            check_sql = (
                "SELECT count() FROM system.tables "
                "WHERE database = {db:String} AND name = {tbl:String}"
            )
            cache_key = f"exists:{database}.{table}"
            check = ch.execute_raw_cached(
                check_sql,
                database,
                cache_key,
                parameters={"db": database, "tbl": table},
            )
            if not check["rows"] or check["rows"][0][0] == 0:
                return f"Table '{database}.{table}' not found."

            executed = ch.run_query(
                f"SELECT * FROM `{database}`.`{table}`",
                database,
                requested_max_rows=capped,
                audience="tool",
            )
            result = ch.build_query_result(executed, max_rows=capped)
            return build_query_summary(
                columns=result.columns,
                rows=result.rows,
                row_count=result.row_count,
                rows_returned=result.rows_returned,
                elapsed_seconds=result.elapsed_seconds,
                database=result.database,
                sql=result.sql,
                warnings=result.warnings,
            )
        except Exception as e:
            return f"Error: {e}"
