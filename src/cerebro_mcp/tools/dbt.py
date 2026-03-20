import json
import time
from typing import Optional

from cerebro_mcp.config import settings
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.tools.query import truncate_response

_last_manifest_check: float = 0.0


def _maybe_refresh_manifest():
    """Lazily refresh manifest if enough time has elapsed."""
    global _last_manifest_check
    now = time.time()
    if now - _last_manifest_check > settings.MANIFEST_REFRESH_INTERVAL_SECONDS:
        _last_manifest_check = now
        manifest.reload_if_changed()


def register_dbt_tools(mcp):
    @mcp.tool()
    def search_models(
        query: str = "",
        tags: Optional[list[str]] = None,
        module: Optional[str] = None,
        limit: int = 50,
    ) -> str:
        """Search dbt models by name, description, or tags.

        Args:
            query: Search term to match against model name or description.
                   Case-insensitive substring match.
                   Supports multi-word queries — each word is matched independently.
                   Use short keywords like 'bridge', 'transactions', 'validator'.
                   Model names use underscores (e.g., api_execution_transactions_daily).
            tags: Optional list of tags to filter by (e.g., ['execution', 'production']).
            module: Optional module filter (e.g., 'execution', 'consensus', 'contracts',
                    'p2p', 'bridges', 'ESG', 'probelab', 'crawlers_data').
            limit: Maximum number of results to return (1-200). Default: 50.

        Returns:
            Matching models with name, description, materialization, and tags.
        """
        _maybe_refresh_manifest()

        if not manifest.is_loaded:
            return "Error: dbt manifest not loaded. dbt context is unavailable."

        capped_limit = min(max(limit, 1), 200)
        results = manifest.search_models(
            query=query, tags=tags, module=module, limit=capped_limit
        )
        if not results:
            return (
                f"No models found matching query='{query}', "
                f"tags={tags}, module={module}.\n\n"
                "**Tips:** Use short single keywords (e.g., 'bridge', "
                "'transactions', 'validator', 'gas'). "
                "Try module filter: 'execution', 'consensus', 'contracts', "
                "'bridges', 'p2p', 'ESG'. "
                "Or call `list_tables(database='dbt')` to browse all tables."
            )

        lines = [f"Found {len(results)} model(s):\n"]
        for m in results:
            tags_str = ", ".join(m["tags"]) if m["tags"] else ""
            owner_str = f" | Owner: {m['owner']}" if m.get("owner") else ""
            test_str = ""
            if m.get("test_count", 0) > 0:
                test_str = f" | Tests: {m['test_count']}"
                if m.get("elementary_test_count", 0) > 0:
                    test_str += f" ({m['elementary_test_count']} elementary)"
            lines.append(
                f"- **{m['name']}** ({m['materialized']})\n"
                f"  {m['description'][:200]}\n"
                f"  Tags: {tags_str}{owner_str}{test_str} | Path: {m['path']}"
            )

        result = truncate_response("\n".join(lines))

        from cerebro_mcp.tools.session_state import state

        state.record_search_models(query, len(results))

        if len(results) >= 5:
            result += (
                "\n\n> **Next steps (enforced by report charting):**\n"
                "> 1. Call `get_model_details` for the top models "
                "(minimum 3-5, ideally 10+ if available).\n"
                "> 2. Identify dimensions: token, action, user segment, "
                "time grain.\n"
                "> 3. Run EDA with quantiles/stddev before charting.\n"
                "> 4. For KPI cards, use single-row SQL. For trend charts, "
                "use separate time-series queries."
            )

        # Append report workflow hint for report-oriented queries
        _report_keywords = {
            "report", "trend", "weekly", "daily", "monthly",
            "summary", "overview", "highlights",
        }
        if query and any(kw in query.lower() for kw in _report_keywords):
            result += (
                "\n\n> **Workflow:** query data → `generate_charts([...])` in one batch call → "
                "`generate_report` for interactive report.\n"
                "> Use single-row SQL for `numberDisplay` KPI charts "
                "(for monthly summaries: `ORDER BY month DESC LIMIT 1`) and "
                "use separate time-series queries for trend charts."
            )

        return result

    @mcp.tool()
    def discover_models(
        query: str = "",
        tags: Optional[list[str]] = None,
        module: Optional[str] = None,
        detail_top_n: int = 5,
    ) -> str:
        """Search models AND return full details for top N matches in one call.

        Equivalent to calling search_models + get_model_details for each top result,
        but uses only ONE tool call instead of N+1. Prefer this over separate calls.

        Args:
            query: Search term to match against model name or description.
                   Case-insensitive substring match.
                   Supports multi-word queries — each word is matched independently.
                   Use short keywords like 'bridge', 'transactions', 'validator'.
                   Model names use underscores (e.g., api_execution_transactions_daily).
            tags: Optional list of tags to filter by (e.g., ['execution', 'production']).
            module: Optional module filter (e.g., 'execution', 'consensus', 'contracts',
                    'p2p', 'bridges', 'ESG', 'probelab', 'crawlers_data').
            detail_top_n: How many top results to expand with full details (default 5).
        """
        _maybe_refresh_manifest()

        if not manifest.is_loaded:
            return "Error: dbt manifest not loaded. dbt context is unavailable."

        from cerebro_mcp.tools.session_state import state

        # Phase 1: Search
        results = manifest.search_models(
            query=query, tags=tags, module=module, limit=50
        )
        state.record_search_models(query, len(results))

        if not results:
            return (
                f"No models found matching query='{query}', "
                f"tags={tags}, module={module}.\n\n"
                "**Tips:** Use short single keywords (e.g., 'bridge', "
                "'transactions', 'validator', 'gas'). "
                "Try module filter: 'execution', 'consensus', 'contracts', "
                "'bridges', 'p2p', 'ESG'. "
                "Or call `list_tables(database='dbt')` to browse all tables."
            )

        # Summary list of all results
        lines = [f"Found {len(results)} model(s):\n"]
        for m in results:
            tags_str = ", ".join(m["tags"]) if m["tags"] else ""
            lines.append(
                f"- **{m['name']}** ({m['materialized']})\n"
                f"  {m['description'][:200]}\n"
                f"  Tags: {tags_str}"
            )

        # Phase 2: Expand top N with full details
        capped_n = min(detail_top_n, len(results))
        expanded_names = []

        for m in results[:capped_n]:
            name = m["name"]
            details = manifest.get_model_details(name)
            if not details:
                continue

            state.record_get_model_details(name)
            expanded_names.append(name)

            lines.append(f"\n---\n## {details['name']}")
            lines.append(f"**Description:** {details['description']}")
            lines.append(f"**Table:** `{details['table_name']}`")
            lines.append(
                f"**Materialization:** {details['materialized']} | "
                f"**Tags:** {', '.join(details['tags'])}"
            )

            # Meta
            meta = details.get("meta", {})
            if meta:
                meta_parts = []
                if meta.get("owner"):
                    meta_parts.append(f"Owner: {meta['owner']}")
                if meta.get("full_refresh"):
                    meta_parts.append("Has full_refresh config")
                if meta_parts:
                    lines.append(f"**Meta:** {' | '.join(meta_parts)}")

            if details["columns"]:
                lines.append("\n**Columns:**")
                for col_name, col_info in details["columns"].items():
                    dtype = col_info["data_type"] or "?"
                    desc = col_info["description"]
                    lines.append(f"- `{col_name}` ({dtype}): {desc}")

            # Tests summary
            tests = details.get("tests", [])
            if tests:
                elem = [t for t in tests if t.get("test_type") == "elementary"]
                lines.append(
                    f"**Tests:** {len(tests)} total"
                    + (f" ({len(elem)} elementary)" if elem else "")
                )

            if details["raw_sql"]:
                # Truncate very long SQL
                sql = details["raw_sql"]
                if len(sql) > 1000:
                    sql = sql[:1000] + "\n-- [truncated]"
                lines.append(f"\n**SQL:**\n```sql\n{sql}\n```")

            if details["upstream"]:
                lines.append(
                    "**Upstream:** "
                    + ", ".join(details["upstream"][:15])
                )
            if details["downstream"]:
                lines.append(
                    "**Downstream:** "
                    + ", ".join(details["downstream"][:15])
                )

        lines.append(
            f"\n---\n*Expanded {len(expanded_names)} model(s) with full details: "
            f"{', '.join(expanded_names)}*"
        )
        lines.append(
            "\n> **Next:** Call `describe_table` on the most relevant table "
            "to verify exact column names, then run EDA queries."
        )

        return truncate_response("\n".join(lines))

    @mcp.tool()
    def get_model_details(model_name: str) -> str:
        """Get comprehensive details about a dbt model including SQL, columns, and lineage.

        Args:
            model_name: Exact model name (e.g., 'int_execution_blocks_clients_version_daily',
                        'api_consensus_validators_active_daily').

        Returns:
            Model description, table name, columns with types/descriptions,
            raw SQL code, and upstream/downstream dependencies.
        """
        _maybe_refresh_manifest()

        if not manifest.is_loaded:
            return "Error: dbt manifest not loaded."

        details = manifest.get_model_details(model_name)
        if details:
            from cerebro_mcp.tools.session_state import state

            state.record_get_model_details(model_name)

        if not details:
            # Try fuzzy match
            suggestions = manifest.search_models(query=model_name, limit=5)
            if suggestions:
                names = [s["name"] for s in suggestions]
                return (
                    f"Model '{model_name}' not found. Did you mean:\n"
                    + "\n".join(f"  - {n}" for n in names)
                )
            return f"Model '{model_name}' not found."

        parts = [
            f"# {details['name']}\n",
            f"**Description:** {details['description']}\n",
            f"**Table:** `{details['table_name']}`\n",
            f"**Materialization:** {details['materialized']}\n",
            f"**Tags:** {', '.join(details['tags'])}\n",
            f"**Path:** {details['path']}\n",
        ]

        # Meta (owner, authoritative, full_refresh)
        meta = details.get("meta", {})
        if meta:
            meta_parts = []
            if meta.get("owner"):
                meta_parts.append(f"Owner: {meta['owner']}")
            if "authoritative" in meta:
                meta_parts.append(
                    f"Authoritative: {meta['authoritative']}"
                )
            if meta.get("full_refresh"):
                fr = meta["full_refresh"]
                fr_desc = f"start={fr.get('start_date', '?')}"
                if fr.get("batch_months"):
                    fr_desc += f", batch={fr['batch_months']}mo"
                if fr.get("stages"):
                    fr_desc += f", {len(fr['stages'])} stages"
                meta_parts.append(f"Full refresh: {fr_desc}")
            if meta_parts:
                parts.append(f"**Meta:** {' | '.join(meta_parts)}\n")

        # Columns
        if details["columns"]:
            parts.append("\n## Columns\n")
            for col_name, col_info in details["columns"].items():
                dtype = col_info["data_type"] or "?"
                desc = col_info["description"]
                parts.append(f"- `{col_name}` ({dtype}): {desc}")
        else:
            parts.append("\n*No column documentation available.*\n")

        # Tests
        tests = details.get("tests", [])
        if tests:
            parts.append(f"\n## Tests ({len(tests)})\n")
            elementary_tests = [
                t for t in tests if t.get("test_type") == "elementary"
            ]
            other_tests = [
                t for t in tests if t.get("test_type") != "elementary"
            ]
            if elementary_tests:
                parts.append("**Elementary observability:**")
                for t in elementary_tests:
                    col = f" on `{t['column_name']}`" if t.get("column_name") else ""
                    sev = t.get("severity", "warn")
                    ts = f", ts={t['timestamp_column']}" if t.get("timestamp_column") else ""
                    parts.append(
                        f"- `{t['test_name']}`{col} (severity={sev}{ts})"
                    )
            if other_tests:
                parts.append("**Standard tests:**")
                for t in other_tests:
                    col = f" on `{t['column_name']}`" if t.get("column_name") else ""
                    parts.append(f"- `{t['test_name']}`{col}")

        # SQL
        if details["raw_sql"]:
            parts.append(f"\n## SQL (raw)\n```sql\n{details['raw_sql']}\n```\n")

        # Lineage
        if details["upstream"]:
            parts.append("\n## Upstream Dependencies")
            for dep in details["upstream"][:20]:
                parts.append(f"- {dep}")

        if details["downstream"]:
            parts.append("\n## Downstream Consumers")
            for dep in details["downstream"][:20]:
                parts.append(f"- {dep}")

        return truncate_response("\n".join(parts))
