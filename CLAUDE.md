# Cerebro MCP — Project Instructions

## Start here: Cerebro Dispatcher

For any non-trivial user request (anything beyond a single scalar question, "open report 3", or an explicit specialist invocation), start by calling:

    get_agent_persona("cerebro_dispatcher")

The dispatcher classifies intent, runs `preflight_analytics_request`, picks the specialist chain, and emits a **dispatch manifest**. All subsequent workflow rules in this file (Report Workflow, MMM Workflow, Data Query SOP) are subordinate to that manifest — if the dispatcher says `mmm_causal_reviewer` must PASS before `generate_report`, that's binding.

Skip the dispatcher only for:
- Trivial turns ("hi", "thanks", "list reports", "open report 3")
- Explicit specialist invocations by the user ("use mmm_analyst on DEXes")
- Follow-up turns inside an already-dispatched workflow — the manifest from turn 1 still applies

The dispatcher is a router only. It does not query the DB, write SQL, or produce analysis. It names *who* should act and *in what order*.

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

Use `list_reports()` and `open_report(id)` to reopen past reports. The list
includes both dashboard reports and research-essay reports (`kind` column).

### When to use `generate_research_report` instead of `generate_report`

Both tools share the chart pipeline and enforcement gates. Pick based on the
artifact the user is asking for:

- **`generate_report`** — analytical dashboard. Dense charts, grids, KPIs,
  short commentary between chart groups. Default choice.
- **`generate_research_report`** — long-form research essay in the
  Anthropic-style layout. Use when the user asks for a whitepaper, research
  report, research essay, narrative analysis, thesis piece, or "long-form"
  article with an argument. Requires `deck` (sub-headline, ≤240 chars) and
  `key_takeaways` (3–6 items). Supports extra markdown directives:
  `{{figure:CHART_ID caption="..." source="..."}}`, `{{pullquote}}`,
  `{{callout kind=...}}`, `{{sidebar title="..."}}`, and `[^fnid]` footnotes.
  Standard `{{chart:ID}}` and `{{grid:N}}` still work inline.
- **`storyteller_generate_story_report`** — final step of the storyteller
  pipeline (memos, decision briefs, investor updates, narrative-first
  deliverables). Defaults to the **research-essay layout**, automatically
  populating `deck` from the recorded big idea, `key_takeaways` from
  storyboard scene intents, and `category` from the context-brief mechanism.
  Pass `style="dashboard"` to opt back into the original dashboard layout
  for back-compat.

**Key takeaways formatting:**
- Key takeaways / summary sections in reports MUST use a 3-column table:

| Takeaway | Evidence | Why it matters |
|----------|----------|----------------|
| ... | ... | ... |

Do NOT use bullet lists for key takeaways. Always use the table format above.

## Data Query SOP

1. DISCOVER: `search_models` — find models across ALL tiers (api_*, fct_*, int_*), not just the first match
2. EXPLORE: `get_model_details` for top 3-5 models — map lineage, identify all dimensions (token, action, segment)
3. VERIFY: `describe_table` for exact column names
4. EDA: Quick distribution check — `quantiles`, `stddevPop`, `min/max`, `count` to assess data shape and outliers
5. QUERY: `execute_query` with date filters, LIMIT, and statistical functions (medians over means). Include correlation queries (corr/covarPop/simpleLinearRegression).
6. VISUALIZE: `generate_charts` (batch) — all charts in ONE call. Include dimensional breakdowns (series_field) and scatter/heatmap charts.
7. REPORT: `generate_report` with {{chart:CHART_ID}} placeholders

## MMM Workflow (sector contribution / ROI analysis)

Use this workflow when a user asks for contribution attribution, ROI across incentive programs, budget optimisation, or "which emissions / rewards actually drove TVL / volume / users".

1. `get_agent_persona("mmm_analyst")` — adopt the MMM SOP. The analyst runs: spine-fill → multicollinearity check → baseline extraction → adstock + response-curve fit (both concave and Hill; pick lower MAE) → contribution decomposition → SQL bootstrap for credibility intervals.
2. **Orchestration handoff (explicit, no inter-agent calls).** Synthesize `mmm_analyst`'s output into a markdown DAG table (nodes = variables, edges = hypothesized causation, flags = co-launched / confounded pairs). THEN call `get_agent_persona("mmm_causal_reviewer")` and pass that table verbatim in the next user message. The reviewer returns a pass/fail verdict.
3. **Do NOT call `generate_report` until the verdict is PASS.** On BLOCK, apply the reviewer's prescribed fixes (intervention, segmentation, or front-door variable) and re-submit.
4. If the user asks "what should we do next?": `get_agent_persona("mmm_simulator")`, passing the fitted `(β, r, λ, current_spend, baseline_kpi)` per media. The simulator bounds shifts at ±30% per period and returns marginal-ROI + reallocation charts.

Required charts in the final MMM report (on top of the standard `generate_report` gates):
- Contribution stacked-area over time (series_field = media)
- Spend vs. effectiveness share (grouped bar)
- Response curve per media (scatter + fitted line)
- Adstock decay (bar, per media, showing λ)
- Causal-review table (from `mmm_causal_reviewer`)
