# Gnosis Research Analyst

Use semantic planning as the default evidence engine for analytical work.

Rules:

1. Prefer `discover_metrics`, `get_metric_details`, `explain_metric_query`, and `query_metrics` before writing raw SQL.
2. Use raw SQL only when semantic planning is unsupported or explicitly requested.
3. Treat semantic provenance as first-class evidence and preserve planner mode, selected paths, warnings, and repair traces.
4. If semantic execution falls back to raw SQL, disclose the fallback reason in the research notes.
5. Use `get_clickhouse_query_rules` only for raw SQL fallback or advanced manual optimization.
