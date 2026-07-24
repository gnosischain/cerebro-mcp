---
{
  "id": "reviewed_dashboard",
  "label": "Reviewed report (three-persona chain)",
  "purpose": "The most rigorous dashboard: produced by the Data Science Lead, numbers co-signed by the statistical reviewer, final QA by the reality checker.",
  "category": "deep_dive",
  "tier": "full_report",
  "deliverable": "A dashboard report artifact whose numeric claims survived an in-session statistical review and a final QA pass, with both review verdicts summarized in the report.",
  "params": [
    {"name": "TOPIC", "description": "Question or area to analyze", "example": "bridge inflows and outflows on Gnosis Chain"},
    {"name": "WINDOW_DAYS", "description": "Trailing window in days", "example": "90"}
  ],
  "personas": ["analytics_reporter", "statistical_reviewer", "reality_checker"],
  "verify_personas": ["analytics_reporter", "statistical_reviewer", "reality_checker"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1800, "budget_usd": 12.0, "verify": "report_file"}
}
---

This is a three-persona chain. Run it in this exact order:

**Produce.** Call `get_agent_persona("analytics_reporter")` and follow its SOP to analyze: {{TOPIC}}, over the last {{WINDOW_DAYS}} days. Execute the report fast path — `preflight_analytics_request(query, mode="report")`; `search_models` (limit 15); `get_model_details` on 3 models; `describe_table`; at least 2 `execute_query` EDA calls incl. one statistical (quantiles/stddev/corr); ONE coverage sweep (`exclude_all_discovered_except`); ONE `generate_charts` batch of 5-7 charts (KPI cards in {{grid:3}}, trend, `series_field` breakdown, scatter or heatmap). Do NOT call `generate_report` yet.

**Review the numbers.** Call `get_agent_persona("statistical_reviewer")` and, as that reviewer, audit every numeric claim you are about to publish: sample sizes, variance vs. claimed changes, correlation methodology (no time-series correlation without differencing or rank methods), causal language. Fix or soften anything that fails; list what changed.

**Final QA.** Call `get_agent_persona("reality_checker")` and run its pre-report checklist against your planned report (coverage, EDA depth, chart structure, no emojis). The reality checker reviews conversationally — there is NO approval tool to call; apply its verdict yourself.

**Publish.** Only after both reviews: `generate_report` with the analysis, a key-takeaways table (Takeaway | Evidence | Why it matters), and a short "Review notes" section summarizing what each reviewer changed or confirmed. Reply with the file:// link and a 3-bullet summary.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
