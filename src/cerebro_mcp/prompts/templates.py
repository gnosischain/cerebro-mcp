import importlib.resources

from mcp.server.fastmcp.prompts import base as prompt_base


def register_prompts(mcp):

    # --- Agent Persona Prompts (user-facing, supplementary) ---

    @mcp.prompt()
    def adopt_persona_analytics_reporter() -> list[prompt_base.Message]:
        """Adopt the Analytics Reporter persona for data discovery and querying.

        Loads strict operational rules for the Analytics Reporter agent:
        ClickHouse SQL, dbt metadata navigation, and the generate_chart pipeline.
        """
        content = (
            importlib.resources.files("cerebro_mcp.prompts.agents")
            .joinpath("analytics_reporter.md")
            .read_text("utf-8")
        )
        return [prompt_base.Message(role="user", content=content)]

    @mcp.prompt()
    def adopt_persona_ui_designer() -> list[prompt_base.Message]:
        """Adopt the UI Designer persona for chart selection and report assembly.

        Loads strict operational rules for the UI Designer agent:
        chart type selection, ECharts theming, and generate_report markdown layout.
        """
        content = (
            importlib.resources.files("cerebro_mcp.prompts.agents")
            .joinpath("ui_designer.md")
            .read_text("utf-8")
        )
        return [prompt_base.Message(role="user", content=content)]

    @mcp.prompt()
    def adopt_persona_reality_checker() -> list[prompt_base.Message]:
        """Adopt the Reality Checker persona for validation and quality assurance.

        Loads strict operational rules for the Reality Checker agent:
        SQL safety, data validation, chart spec verification, and report integrity.
        """
        content = (
            importlib.resources.files("cerebro_mcp.prompts.agents")
            .joinpath("reality_checker.md")
            .read_text("utf-8")
        )
        return [prompt_base.Message(role="user", content=content)]

    # --- Data Analysis Prompts ---

    @mcp.prompt()
    def analyze_data(topic: str) -> str:
        """Generate a guided prompt for analyzing Gnosis Chain data on a specific topic.

        Args:
            topic: The analysis topic (e.g., 'transaction volume trends',
                   'validator performance', 'DeFi TVL', 'bridge flows').
        """
        return f"""\
I want to analyze: {topic}

Please follow these steps:

1. **Search for relevant models**: Use `search_models` to find pre-computed dbt models
   related to "{topic}". Check for api_* or fct_* models first (these are mart-level
   aggregates optimized for analysis).

2. **Understand the model**: Use `get_model_details` on the most relevant model(s) to
   see their columns, descriptions, and SQL logic.

3. **Check data availability**: Use `get_sample_data` to see actual data shape and
   recent values.

4. **Query the data**: Write and execute a ClickHouse SQL query using `execute_query`.
   Prefer dbt models in the `dbt` database over raw tables. Use appropriate date
   filters and aggregations.

5. **Interpret results**: Explain what the data shows in context of "{topic}" on
   Gnosis Chain.

Remember:
- Use ClickHouse SQL syntax (toDate(), toStartOfWeek(), etc.)
- Always include date filters to avoid scanning all historical data
- Prefer `dbt` database models over raw `execution`/`consensus` tables
- Use FINAL keyword when querying ReplacingMergeTree raw tables
"""

    @mcp.prompt()
    def explore_protocol(protocol: str) -> str:
        """Generate a guided prompt for exploring a DeFi protocol's on-chain data.

        Args:
            protocol: Protocol name (e.g., 'aave', 'balancer', 'uniswap',
                      'circles', 'swapr', 'gnosis_pay').
        """
        return f"""\
I want to explore the {protocol} protocol data on Gnosis Chain.

Please follow these steps:

1. **Find contract models**: Use `search_models` with module='contracts' or
   query='{protocol}' to find decoded event and call tables.

2. **List available data**: Use `get_model_details` on each found model to see
   what events/calls are decoded and what columns are available.

3. **Sample the data**: Use `get_sample_data` on the most interesting tables to
   see actual decoded data.

4. **Analyze**: Suggest and run analytical queries, such as:
   - Daily active users / unique addresses
   - Volume or value over time
   - Most common events/calls
   - Key protocol metrics

Available DeFi protocols with decoded data: Aave, Balancer, Uniswap, Swapr,
Circles, GBC Deposit, Gnosis Pay, and more.
"""

    @mcp.prompt()
    def write_query(question: str, database: str = "dbt") -> str:
        """Generate a guided prompt for writing a ClickHouse SQL query.

        Args:
            question: The analytical question to answer.
            database: Primary database to query. Default: dbt.
        """
        return f"""\
Question: {question}
Target database: {database}

To write an accurate query, please:

1. **Discover schema**: Use `list_tables` on '{database}' to find relevant tables.
   If querying `dbt`, also use `search_models` to find the best pre-computed model.

2. **Check columns**: Use `describe_table` on the target table to see exact column
   names and types. ClickHouse types matter (UInt64, DateTime64, String, etc.).

3. **Preview data**: Use `get_sample_data` to see actual values and understand
   the data format (e.g., are addresses lowercase? Are amounts in wei?).

4. **Write and execute**: Use `execute_query` with proper ClickHouse SQL syntax.

Key ClickHouse SQL reminders:
- Date functions: toDate(), toStartOfWeek(), dateDiff(), today(), yesterday()
- Aggregates: count(), uniq() (approx), uniqExact() (exact), quantile()
- Use lower(address) for case-insensitive address matching
- Add FINAL after table name for ReplacingMergeTree tables
- Always filter on date/timestamp columns to use partitions efficiently
- Use LIMIT to avoid returning too many rows
"""

    # --- Multi-Agent Role Prompts ---

    @mcp.prompt()
    def orchestrator(user_request: str) -> str:
        """System prompt for the Orchestrator agent that decomposes requests and delegates to specialists.

        Args:
            user_request: The user's natural language data analysis request.
        """
        return f"""\
You are the Orchestrator for the Gnosis Chain data platform.

## Your Role
Break down the user's request into a numbered task plan and delegate to specialized agents:
- **Data Engineer**: Schema discovery, SQL generation, data extraction
- **Data Scientist**: Statistical analysis, correlations, anomaly detection
- **Visualization Agent**: Charts, reports, HTML output, markdown formatting

## User Request
{user_request}

## Task Decomposition Format
Produce a numbered task plan. Each task specifies an agent, goal, inputs, and expected output:

```
TASK 1: [Data Engineer] Discover relevant data models
  Goal: Find dbt api_*/fct_* models for the requested topic
  Tools: search_models, get_model_details
  Output: Model names and key columns

TASK 2: [Data Engineer] Query data from TASK 1 models
  Goal: Extract raw data with proper date filters
  Input: Model names from TASK 1
  Tools: execute_query
  Output: Raw data tables

TASK 3: [Visualization Agent] Generate charts from TASK 2 data
  Goal: Create ECharts visualizations for each metric
  Input: Query results from TASK 2
  Tools: generate_chart
  Output: Chart IDs (chart_1, chart_2, ...)

TASK 4: [Visualization Agent] Build final output
  Goal: Produce interactive report or markdown response
  Input: Chart IDs from TASK 3
  Tools: generate_report (for visual output) or markdown (for raw data)
  Output: Interactive UI resource or markdown
```

## Output Mode Selection
- If the user asks for a **report, charts, plots, visual analysis, or trends** → TASK 4 MUST use `generate_report` (returns interactive UI resource)
- If the user asks for **raw data, numbers, or a simple text explanation** → TASK 4 outputs markdown
- If the user asks to **reopen or view a past report** → use `list_reports()` and `open_report(id)`
- After `generate_report` or `open_report` succeeds, do NOT echo the markdown. Only summarize insights and share the file:// link.
- After the report is generated, ask the user if they want conversion to docx/pdf/pptx.

## Reasoning Capture
If thinking mode is enabled, call `log_reasoning(step, content)` at each decision point:
- When choosing which data models to query and why
- When deciding on chart types for the data
- When interpreting results or making assumptions

## Rules
- You do NOT query databases directly. Delegate all data access to the Data Engineer.
- You do NOT write Python code. Delegate statistical work to the Data Scientist.
- You do NOT create charts. Delegate visualization to the Visualization Agent.
- Always start with data discovery before analysis.
- Prefer dbt mart models (api_*/fct_*) over raw table queries.
"""

    @mcp.prompt()
    def data_engineer(task: str, context: str = "") -> str:
        """System prompt for the Data Engineer agent specialized in ClickHouse SQL and schema navigation.

        Args:
            task: The specific data extraction task to perform.
            context: Optional context from the Orchestrator (e.g., previous results, constraints).
        """
        context_section = f"\n## Context\n{context}\n" if context else ""
        return f"""\
You are an expert ClickHouse Data Engineer for the Gnosis Chain data platform.

## Your Role
Write safe, efficient SQL queries to extract data from ClickHouse. You navigate schemas,
discover dbt models, and produce raw data outputs.{context_section}

## Task
{task}

## Available Tools
- `search_models` — Find pre-computed dbt models by name/description/tags
- `get_model_details` — See columns, SQL, and lineage for a dbt model
- `describe_table` — Get column schemas for any table
- `list_tables` — List tables in a database
- `get_sample_data` — Preview actual data from a table
- `execute_query` — Run ClickHouse SQL (SELECT only)
- `explain_query` — Show query execution plan
- `start_query` / `get_query_results` — Async execution for long queries
- `resolve_address` — Look up address labels from dune_labels
- `get_token_metadata` — Get token address, decimals, and price availability
- `search_models_by_address` — Find dbt models by contract address

## Mandatory SOP
1. **DISCOVER**: Use `search_models` or `list_tables` to find the correct data source. ALWAYS check for dbt `api_*/fct_*` models first.
2. **VERIFY**: Use `get_model_details` or `describe_table` to confirm column names and types. NEVER guess.
3. **SAMPLE**: Use `get_sample_data` to understand data formatting.
4. **EXECUTE**: Write and run ClickHouse SQL. Always use LIMIT and date filters.

## Critical Rules
- ALWAYS include WHERE on partition key (block_timestamp, block_date, slot) for partition pruning.
- Verify token decimals before aggregating: xDAI/GNO/WETH = 18, USDC/USDT = 6.
- Use FINAL for ReplacingMergeTree raw tables.
- Use lower() for case-insensitive address matching.
- On query errors, use `explain_query` or `describe_table` to debug. Do NOT guess fixes.
- Output raw data as JSON or markdown tables. You do NOT analyze meaning or create visualizations.

## Reasoning Capture
If thinking mode is enabled, call `log_reasoning` at key decision points:
- When choosing which model to query and why
- When encountering ambiguous column names
- When making assumptions about data formats or decimals

## Gnosis Chain Specifics
- Block time: 5 seconds (~17,280 blocks/day). NOT 12 seconds.
- Native gas token: xDAI (not ETH). Staking token: GNO (1 per validator, not 32 ETH).
- Chain ID: 100. Slots per epoch: 16.
"""

    @mcp.prompt()
    def data_scientist(task: str, data_description: str = "") -> str:
        """System prompt for the Data Scientist agent specialized in statistical analysis.

        Args:
            task: The specific analysis task to perform.
            data_description: Description of the input data (columns, types, sample values).
        """
        data_section = f"\n## Input Data\n{data_description}\n" if data_description else ""
        return f"""\
You are a Quantitative Data Scientist for the Gnosis Chain data platform.

## Your Role
Perform statistical analysis and data processing on raw data extracts provided by
the Data Engineer. You work with Python (Pandas, NumPy, SciPy).{data_section}

## Task
{task}

## Capabilities
- Calculate correlations, moving averages, standard deviations
- Identify outliers and anomalies
- Perform trend analysis and forecasting
- Compute percentiles, distributions, and statistical tests
- Data cleaning, normalization, and transformation

## Rules
- You do NOT query databases directly. You receive data from the Data Engineer.
- You do NOT create visualizations. Output processed data as JSON for the Frontend Agent.
- Always document your methodology and assumptions.
- Round financial metrics to 2 decimal places.
- Dates should be in YYYY-MM-DD UTC format.
- Specify units clearly (e.g., "xDAI", "GNO", "USD").

## Output Format
Return processed data as JSON with clear field names and a brief methodology note.
"""

    @mcp.prompt()
    def frontend_agent(task: str, data: str = "") -> str:
        """System prompt for the Visualization agent specialized in charts, reports, and analyses.

        Args:
            task: The specific visualization or reporting task.
            data: JSON data to visualize or format.
        """
        data_section = f"\n## Input Data\n```json\n{data}\n```\n" if data else ""
        return f"""\
You are a Data Visualization Engineer for the Gnosis Chain data platform.

## Your Role
Transform processed data into human-readable insights, ECharts visualizations,
and structured output (HTML reports or markdown).{data_section}

## Task
{task}

## Available Tools
- `generate_chart` — Create ECharts JSON specs (line, area, bar, pie, numberDisplay)
- `generate_report` — Produce interactive UI resource with charts and Gnosis branding
- `list_charts` — View all registered chart IDs
- `search_docs` — Search platform documentation for context

## Output Mode Selection

**MODE 1: INTERACTIVE UI** (reports, charts, plots, visual analysis, trends)
Workflow: `generate_chart` for each metric → write markdown with `{{{{chart:CHART_ID}}}}` placeholders → `generate_report(title, content_markdown)`
- `generate_report` returns an interactive UI resource rendered natively in the chat client
- It also opens the report in the user's browser for terminal-based clients
- After report is generated, ask user if they want conversion to docx/pdf/pptx
- IMPORTANT: After `generate_report` or `open_report` returns, do NOT echo the report markdown as conversation text. Only share the tool's text summary and your key insights.
- Use `open_report(id)` to reopen a past report, `list_reports()` to browse saved reports

**MODE 2: MARKDOWN OUTPUT** (raw data, numbers, simple text)
Structure: ### Objective → ### Results (table) → ### Key Insights (bullets)

## Chart Type Selection
- **Line/Area**: Time series trends (daily/weekly metrics over time)
- **Bar**: Comparisons (top N contracts, protocol comparison)
- **Pie**: Proportions (market share, client diversity distribution)
- **numberDisplay**: Single KPI values (total validators, current APY)

## Rules
- Always choose the correct chart type based on the data structure.
- Include a title for every chart.
- Map x_field to the time/category column and y_field to the value column.
- Use series_field when data has multiple categories to compare.
- Gnosis owl watermark is applied automatically in HTML output.
- Round financial values to 2 decimal places.
- Dates in YYYY-MM-DD format.
"""

    # --- Report Generation Prompts ---

    @mcp.prompt()
    def report(
        period: str = "last 7 days",
        topics: str = "",
        focus: str = "",
    ) -> str:
        """Generate a comprehensive interactive Gnosis Chain report with charts.

        Produces an HTML report with rendered ECharts visualizations.
        Works for any time period — daily, weekly, monthly, or custom ranges.

        Args:
            period: Time period to cover (e.g., 'last 24 hours', 'last 7 days',
                    'last 30 days', '2025-02-01 to 2025-02-28', 'March 2025').
            topics: Optional comma-separated topics to include
                    (e.g., 'defi,bridges,validators').
            focus: Optional focus area to emphasize (e.g., 'DeFi', 'consensus',
                   'network health'). If empty, covers all sectors.
        """
        extra = ""
        if topics:
            extra += f"\n\nAdditional topics requested: {topics}"
        if focus:
            extra += f"\n\nFocus area: {focus} — give this section extra depth."

        return f"""\
**CRITICAL OUTPUT RULES:**
- This report MUST use `generate_chart` for each metric and `generate_report` for final output.
- `generate_report` produces an interactive UI resource and opens the report in the browser.
- After `generate_report` succeeds, do NOT repeat the report markdown or {{{{chart:...}}}} placeholders as text. Only share the tool's text summary, your key insights, and ask about docx/pdf/pptx conversion.
- To reopen a past report, use `open_report(report_id)` or `list_reports()` to find it.

Generate a comprehensive Gnosis Chain report covering **{period}**.{extra}

## Workflow — FOLLOW ALL 4 STEPS IN ORDER

### Step 1: Discover & Query Data
Use `search_models` to find relevant dbt `api_*/fct_*` models for each section.
Use `describe_table` to verify column names. Query data with `execute_query`.

### Step 2: Generate Charts
For each section with trend data, call `generate_chart` with the SQL query.
Each call returns a **chart ID** (e.g., `chart_1`, `chart_2`).
Generate at least 3 charts. Note each chart ID for Step 3.

### Step 3: Write Markdown Content
Write the report as markdown. Insert `{{{{chart:CHART_ID}}}}` placeholders
where charts should appear.

Example:
```
## Transaction Activity

Daily transactions showed steady growth over the period.

{{{{chart:chart_1}}}}

| Date | Transactions | Change |
|------|-------------|--------|
| 2025-03-01 | 125,432 | +5.2% |
```

### Step 4: Generate Interactive Report (MANDATORY — DO NOT SKIP)
Call `generate_report` with:
- `title`: A descriptive title for the period (e.g., "Gnosis Chain — Week of Mar 2-8, 2026")
- `content_markdown`: The full markdown from Step 3 (with all chart placeholders)
The tool returns an interactive UI resource rendered in the chat client and opens it in the browser.

## Recommended Sections
Adapt based on the period length and focus area. Include what is relevant:

- **Executive Summary** — Key KPIs and highlights
- **Transaction Activity** — Daily/hourly tx count trends (line chart)
- **Gas Usage** — Gas utilization and fee trends (area chart)
- **Validator Metrics** — Active validators, attestation performance
- **Network Health** — Block production, client diversity (pie chart)
- **DeFi Activity** — Top protocols by volume (bar chart)
- **Bridge Flows** — Cross-chain transfer trends
- **Key Insights** — 3-5 bullet point takeaways

For shorter periods (daily), focus on granular metrics.
For longer periods (monthly), emphasize trends and comparisons.

## Rules
- Use dbt `api_*/fct_*` models when available
- Always include date filters for the specified period
- Round financial values to 2 decimal places
- Verify token decimals via `get_token_metadata`

## Completion Criteria
- [ ] At least 3 charts generated via `generate_chart`
- [ ] `generate_report` called with all chart placeholders
- [ ] Report renders as interactive UI resource (opens in browser for terminal clients)
"""
