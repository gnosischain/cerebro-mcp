# Cerebro MCP — Project Instructions

## Report Workflow (CRITICAL)

When a user asks for a report, trends, or visual analysis using cerebro:

1. Use `generate_charts` (batch) with ALL chart specs in ONE call (minimum 3 charts)
2. Do NOT use individual `generate_chart` calls for reports — use the batch tool
3. Use `generate_report` to assemble the final interactive report
4. The report renders as a native UI in GUI clients; opens in browser for terminal clients
5. After the report is generated, ask if the user wants conversion to docx/pdf/pptx
6. If yes, use Claude Code's built-in file skills to convert

Never skip the `generate_charts` -> `generate_report` pipeline.

**Report enforcement gates (generate_report will REJECT without):**
- At least 1 chart with `series_field` or pie/treemap/heatmap/sankey type (dimensional breakdown)
- At least 1 scatter/heatmap chart OR correlation query (relational analysis)
- At least 1 statistical query (quantiles/stddev/corr)
- At least 2 exploratory queries

**After `generate_report` or `open_report` succeeds:**
- ALWAYS include the file:// link from the tool response in your reply
- Do NOT repeat the markdown content or {{chart:CHART_ID}} placeholders as text
- Summarize key insights and ask about format conversion
- SQL queries are embedded in the report UI (click `</>` on each chart card)

Use `list_reports()` and `open_report(id)` to reopen past reports.

## Data Query SOP

1. DISCOVER: `search_models` — find models across ALL tiers (api_*, fct_*, int_*), not just the first match
2. EXPLORE: `get_model_details` for top 3-5 models — map lineage, identify all dimensions (token, action, segment)
3. VERIFY: `describe_table` for exact column names
4. EDA: Quick distribution check — `quantiles`, `stddevPop`, `min/max`, `count` to assess data shape and outliers
5. QUERY: `execute_query` with date filters, LIMIT, and statistical functions (medians over means). Include correlation queries (corr/covarPop/simpleLinearRegression).
6. VISUALIZE: `generate_charts` (batch) — all charts in ONE call. Include dimensional breakdowns (series_field) and scatter/heatmap charts.
7. REPORT: `generate_report` with {{chart:CHART_ID}} placeholders
