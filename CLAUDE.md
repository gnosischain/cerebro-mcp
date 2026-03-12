# Cerebro MCP — Project Instructions

## Report Workflow (CRITICAL)

When a user asks for a report, trends, or visual analysis using cerebro:

1. Use `generate_chart` for each metric (minimum 3 for full reports)
2. Use `generate_report` to assemble the final interactive report
3. The report renders as a native UI in GUI clients; opens in browser for terminal clients
4. After the report is generated, ask if the user wants conversion to docx/pdf/pptx
5. If yes, use Claude Code's built-in file skills to convert

Never skip the `generate_chart` -> `generate_report` pipeline.

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
5. QUERY: `execute_query` with date filters, LIMIT, and statistical functions (medians over means)
6. VISUALIZE: `generate_chart` per metric (min: KPIs + trends + breakdowns — never just KPI counters)
7. REPORT: `generate_report` with {{chart:CHART_ID}} placeholders
