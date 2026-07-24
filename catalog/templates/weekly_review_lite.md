---
{
  "id": "weekly_review_lite",
  "label": "Last-week review (lite)",
  "purpose": "What happened last week: week-over-week deltas of the core KPIs plus the notable movers, inline.",
  "category": "sector_health",
  "tier": "lite_report",
  "deliverable": "2-4 inline charts comparing last week to the prior week, plus a short WoW summary with the biggest movers.",
  "params": [
    {"name": "SCOPE", "description": "Product or sector to review", "example": "Gnosis Pay"}
  ],
  "personas": [],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 600, "budget_usd": 4.0, "verify": "charts"}
}
---

Using the cerebro tools, review last week for {{SCOPE}}.

Definitions first: "last week" = the last 7 full UTC days; compare against the 7 days before that. Follow the lite path — inline, no report artifact:

1. `preflight_analytics_request(query, mode="answer")`.
2. `search_models` (tight query, limit 15) → `get_model_details` on the 1-2 models you will use → `describe_table` the primary fact table.
3. Query the core KPIs for both weeks in as few `execute_query` calls as possible (daily series covering 14 days lets one query serve both the chart and the WoW math).
4. ONE `generate_charts` batch, 2-4 charts: the 14-day daily trend of the headline KPI, and at least one breakdown (series_field) showing where the change came from.
5. Summarize: WoW delta for each core KPI (absolute and %), the biggest positive and negative movers, and one sentence on whether the change looks like trend or noise (check against the prior weeks' variance before calling it a trend).

Render charts inline with their SQL in collapsible blocks. Do NOT call `generate_report` — this is a check-in, not a dashboard.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
