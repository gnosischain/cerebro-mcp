---
{
  "id": "growth_retention_check",
  "label": "Growth & retention check",
  "purpose": "Acquisition, activity, and retention for one product — DAU/WAU/MAU and a cohort read, by the growth specialist.",
  "category": "deep_dive",
  "tier": "lite_report",
  "deliverable": "Inline charts of DAU/WAU/MAU and cohort retention plus a short growth-accounting read.",
  "params": [
    {"name": "PRODUCT", "description": "Product or protocol to analyze", "example": "Gnosis Pay"},
    {"name": "WINDOW_DAYS", "description": "Trailing window in days", "example": "90"}
  ],
  "personas": ["growth_analyst"],
  "verify_personas": ["growth_analyst"],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 900, "budget_usd": 5.0, "verify": "charts"}
}
---

Adopt the growth specialist first: call `get_agent_persona("growth_analyst")` and follow its SOP.

Then give me a growth and retention check for {{PRODUCT}} over the last {{WINDOW_DAYS}} days.

Important: the persona's SQL toolkit contains ILLUSTRATIVE table names that are not in the live catalog — always run `search_models` and `describe_table` first and use only verified model/column names.

Execute as a lite analysis (inline charts, no report artifact):

1. `preflight_analytics_request(query, mode="answer")`.
2. `search_models` (tight query, limit 15) → `get_model_details` on the models you will use → `describe_table` before any SQL.
3. Define "user" explicitly (address grain; EOA vs contract caveat) as the persona requires.
4. Query: DAU/WAU/MAU together (never one alone), new-vs-returning split, and a weekly cohort retention matrix if the data supports it.
5. ONE `generate_charts` batch, 3-4 charts: the DAU/WAU/MAU trend, a new-vs-returning breakdown (series_field), and the retention view (heatmap if the cohort matrix exists, otherwise a retention-curve line).
6. Close with a growth-accounting read: is growth coming from acquisition or retention, and is the trend accelerating or decaying? Note stickiness (DAU/MAU).

Render charts inline with SQL in collapsible blocks. Do NOT call `generate_report`.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
