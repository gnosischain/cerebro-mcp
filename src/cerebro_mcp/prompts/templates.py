def register_prompts(mcp):
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
