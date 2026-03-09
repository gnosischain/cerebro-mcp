from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.manifest_loader import manifest
from cerebro_mcp.safety import validate_identifier
from cerebro_mcp.tools.query import format_results_table, truncate_response


def register_schema_tools(mcp, ch: ClickHouseManager):
    @mcp.tool()
    def list_tables(
        database: str,
        name_pattern: str = "",
    ) -> str:
        """List all tables in a ClickHouse database with engine and row counts.

        Args:
            database: Database to list tables from. One of: execution,
                      consensus, crawlers_data, nebula, dbt.
            name_pattern: Optional LIKE pattern to filter table names
                          (e.g., 'stg_%', '%validators%').

        Returns:
            Table listing with name, engine, total_rows, and size.
        """
        try:
            valid, err = validate_identifier(database)
            if not valid:
                return f"Error: {err}"

            sql = (
                "SELECT name, engine, total_rows, "
                "formatReadableSize(total_bytes) AS size "
                "FROM system.tables "
                "WHERE database = {db:String}"
            )
            params = {"db": database}
            if name_pattern:
                sql += " AND name LIKE {pat:String}"
                params["pat"] = name_pattern
            sql += " ORDER BY name"

            cache_key = f"tables:{database}:{name_pattern}"
            result = ch.execute_raw_cached(
                sql, database, cache_key, parameters=params
            )
            if not result["rows"]:
                return f"No tables found in database '{database}'."

            return format_results_table(result["columns"], result["rows"])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def describe_table(
        table: str,
        database: str = "dbt",
    ) -> str:
        """Get the column schema for a specific table.

        Args:
            table: Table name (e.g., 'blocks', 'api_execution_transactions_7d').
            database: Database containing the table. Default: dbt.

        Returns:
            Column listing with name, type, default kind, and description.
            Includes dbt model descriptions if available.
        """
        try:
            valid, err = validate_identifier(table)
            if not valid:
                return f"Error: {err}"
            valid, err = validate_identifier(database)
            if not valid:
                return f"Error: {err}"

            sql = (
                "SELECT name, type, default_kind, comment "
                "FROM system.columns "
                "WHERE database = {db:String} AND table = {tbl:String} "
                "ORDER BY position"
            )
            cache_key = f"columns:{database}.{table}"
            result = ch.execute_raw_cached(
                sql, database, cache_key,
                parameters={"db": database, "tbl": table},
            )

            if not result["rows"]:
                return f"Table '{database}.{table}' not found or has no columns."

            output_parts = [f"## {database}.{table}\n"]

            # Add dbt description if available
            model = manifest.get_model(table)
            if model:
                desc = model.get("description", "")
                if desc:
                    output_parts.append(f"**Description:** {desc}\n")
                mat = model.get("config", {}).get("materialized", "")
                if mat:
                    output_parts.append(f"**Materialization:** {mat}\n")

            # Build column table with dbt descriptions
            columns = ["name", "type", "default_kind", "description"]
            enriched_rows = []
            dbt_columns = {}
            if model:
                dbt_columns = {
                    k.lower(): v
                    for k, v in model.get("columns", {}).items()
                }

            for row in result["rows"]:
                col_name = row[0] if row[0] else ""
                col_type = row[1] if row[1] else ""
                default = row[2] if row[2] else ""
                # Prefer dbt description over ClickHouse comment
                dbt_col = dbt_columns.get(col_name.lower(), {})
                description = (
                    dbt_col.get("description", "")
                    or (row[3] if len(row) > 3 and row[3] else "")
                )
                enriched_rows.append([col_name, col_type, default, description])

            output_parts.append(format_results_table(columns, enriched_rows))
            return truncate_response("\n".join(output_parts))
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_sample_data(
        table: str,
        database: str = "dbt",
        limit: int = 5,
    ) -> str:
        """Get sample rows from a table to understand data shape and values.

        Args:
            table: Table name to sample from.
            database: Database containing the table. Default: dbt.
            limit: Number of sample rows (1-20). Default: 5.

        Returns:
            Sample rows formatted as a table.
        """
        try:
            valid, err = validate_identifier(table)
            if not valid:
                return f"Error: {err}"
            valid, err = validate_identifier(database)
            if not valid:
                return f"Error: {err}"

            capped = min(max(limit, 1), 20)

            # Verify table exists
            check_sql = (
                "SELECT count() FROM system.tables "
                "WHERE database = {db:String} AND name = {tbl:String}"
            )
            cache_key = f"exists:{database}.{table}"
            check = ch.execute_raw_cached(
                check_sql, database, cache_key,
                parameters={"db": database, "tbl": table},
            )
            if not check["rows"] or check["rows"][0][0] == 0:
                return f"Table '{database}.{table}' not found."

            sql = f"SELECT * FROM `{database}`.`{table}` LIMIT {capped}"
            result = ch.execute_raw(sql, database)
            if not result["rows"]:
                return f"Table '{database}.{table}' is empty."

            return format_results_table(result["columns"], result["rows"])
        except Exception as e:
            return f"Error: {e}"
