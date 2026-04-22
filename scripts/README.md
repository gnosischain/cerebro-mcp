# scripts/

Operational scripts for cerebro-mcp. These are not part of the packaged
server; they are here for one-off maintenance and diagnostics.

## `mcp_smoke_test.py`

End-to-end smoke test for the cerebro-mcp server. Connects directly via the
`mcp` Python SDK (stdio for local, SSE for remote) and walks through a fixed
15-step sequence that exercises the whole stack:

```
1.  initialize
2.  tools/list
3.  prompts/list
4.  resources/list
5.  preflight_analytics_request      (clears SEMANTIC_ENABLED gate)
6.  search_models(query=...)         → discover target api_*_daily model
7.  get_model_details x3             (satisfies MIN_MODELS_DETAILED)
8.  list_databases
9.  list_tables(dbt, <query>)
10. describe_table(<target>)         → pick date + numeric columns
11. get_sample_data(<target>)
12. execute_query(quantiles)         (MIN_STATISTICAL_QUERIES)
13. execute_query(corr)               (MIN_CORRELATION_QUERIES, exploratory=2)
14. generate_charts (4 specs)        (line + bar w/ series_field + scatter)
15. generate_report                  → returns Report ID
```

The test **dynamically discovers** a target model at runtime — it calls
`search_models(query="daily")`, picks the first `api_*_daily` model name
from the response, then `describe_table`s it to find a Date column and two
numeric columns. All subsequent queries and charts are built from those
dynamically-discovered column names, so the test works against any
cerebro-mcp deployment regardless of which dbt manifest it has loaded.

Runs **without** Claude Desktop, so it bypasses any Connectors filter state
or per-tool enable/disable settings. Use it to verify the server itself is
healthy and can produce a report end-to-end.

### Local (stdio against the current working tree)

```bash
uv run python scripts/mcp_smoke_test.py --transport local
```

Spawns `uv run cerebro-mcp` as a subprocess and talks to it over stdio. No
token needed. Exercises whatever is in your working tree.

### Remote (SSE against the preview cluster)

```bash
uv run python scripts/mcp_smoke_test.py --transport remote --token-from-secret
```

`--token-from-secret` pulls the live bearer token via
`kubectl -n analytics-preview get secret cerebro-mcp-auth`. You can also
pass `--token <value>` explicitly or set `CEREBRO_TOKEN` in the environment.

### Fast handshake-only check

Skip everything that touches ClickHouse:

```bash
uv run python scripts/mcp_smoke_test.py --transport remote --token-from-secret --skip-query
```

This runs `initialize`, `tools/list`, `prompts/list`, `resources/list` only.
Takes < 1 second and verifies the transport, auth, and tool registration.

### Skip chart/report generation

Useful if you want to test ClickHouse connectivity without waiting on chart
rendering:

```bash
uv run python scripts/mcp_smoke_test.py --transport local --skip-charts
```

### Output modes

- Default: human-readable table, one line per step, with PASS/FAIL/SKIP and
  timing in milliseconds.
- `--json`: NDJSON — one JSON object per step plus a final `summary` object.
  Useful for piping into `jq` or a CI system.
- `--verbose`: print full error messages on failing steps.

### Exit code

Equal to the number of failed steps. Zero means everything passed. Any
non-zero value means one or more steps failed; the script does NOT abort on
the first failure, it runs the whole sequence so you can see everything at
once.

### Override the discovered model

If you want to target a specific model family instead of the default
`"daily"` discovery query, pass `--query`:

```bash
uv run python scripts/mcp_smoke_test.py --transport local --query mixpanel_ga
```

This makes `search_models` search for `mixpanel_ga` and the test picks the
first `api_*_daily` match from the results. Useful when you have specific
dbt models you want to exercise.

### Interpreting failures

- `initialize` fails → transport is broken (bad URL, wrong token, pod down,
  ingress misconfigured).
- `tools/list` has too few tools → tool registration failed server-side;
  check `cerebro-mcp` startup logs.
- `search_models` returns fewer than 3 `api_*_daily` models → the dbt
  manifest loaded into the server doesn't contain `api_*_daily` matches
  for your query. Try a different `--query` value or check the
  `DBT_MANIFEST_URL` environment the server is using.
- `list_databases` / `describe_table` / `get_sample_data` fails →
  ClickHouse connectivity / credentials broken.
- `list_tables` returns 0 is NOT a failure — cerebro's list_tables queries
  the ClickHouse system catalog which often misses dbt-generated views.
  The test only asserts the tool doesn't error.
- `execute_query(quantiles)` fails → statistical function failed on the
  target column. Usually means the auto-picked numeric column has a weird
  type; pick a different model with `--query`.
- `generate_charts` fails with a "gate" error → one of the chart
  precondition gates in `src/cerebro_mcp/tools/session_state.py` is failing.
  Read the error message; it tells you which gate. The test is designed to
  satisfy all of them in order.
- `generate_report` fails with a "gate" error → the report-level gates
  (`MIN_CHARTS_FOR_REPORT`, `REQUIRE_DIMENSIONAL_BREAKDOWN`,
  `REQUIRE_RELATIONAL_CHART`, `MIN_EXPLORATORY_QUERIES`) are not satisfied.
  The smoke test generates 4 charts covering line (trend), bar (breakdown
  with series_field), scatter (relational), and a second line — which
  satisfies all the current gates. A failure here means the gate logic
  changed.

## `sync_clickhouse_skills.py`

(Pre-existing — unrelated to the smoke test.)
