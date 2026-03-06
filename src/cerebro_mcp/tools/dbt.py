import json
from typing import Optional

from cerebro_mcp.manifest_loader import manifest


def register_dbt_tools(mcp):
    @mcp.tool()
    def search_models(
        query: str = "",
        tags: Optional[list[str]] = None,
        module: Optional[str] = None,
    ) -> str:
        """Search dbt models by name, description, or tags.

        Args:
            query: Search term to match against model name or description.
                   Case-insensitive substring match.
            tags: Optional list of tags to filter by (e.g., ['execution', 'production']).
            module: Optional module filter (e.g., 'execution', 'consensus', 'contracts',
                    'p2p', 'bridges', 'ESG', 'probelab', 'crawlers_data').

        Returns:
            Matching models with name, description, materialization, and tags.
        """
        if not manifest.is_loaded:
            return "Error: dbt manifest not loaded. dbt context is unavailable."

        results = manifest.search_models(query=query, tags=tags, module=module)
        if not results:
            return f"No models found matching query='{query}', tags={tags}, module={module}."

        lines = [f"Found {len(results)} model(s):\n"]
        for m in results:
            tags_str = ", ".join(m["tags"]) if m["tags"] else ""
            lines.append(
                f"- **{m['name']}** ({m['materialized']})\n"
                f"  {m['description'][:200]}\n"
                f"  Tags: {tags_str} | Path: {m['path']}"
            )

        return "\n".join(lines)

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
        if not manifest.is_loaded:
            return "Error: dbt manifest not loaded."

        details = manifest.get_model_details(model_name)
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

        # Columns
        if details["columns"]:
            parts.append("\n## Columns\n")
            for col_name, col_info in details["columns"].items():
                dtype = col_info["data_type"] or "?"
                desc = col_info["description"]
                parts.append(f"- `{col_name}` ({dtype}): {desc}")
        else:
            parts.append("\n*No column documentation available.*\n")

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

        return "\n".join(parts)
