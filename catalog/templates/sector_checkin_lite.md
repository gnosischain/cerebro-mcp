---
{
  "id": "sector_checkin_lite",
  "label": "Sector check-in (lite)",
  "purpose": "A fast health check of one sector: a few inline charts plus a short written read.",
  "category": "sector_health",
  "tier": "lite_report",
  "deliverable": "2-4 charts rendered inline (headline trend + at least one breakdown) and a 3-5 sentence narrative. No report artifact.",
  "params": [
    {"name": "SECTOR", "description": "Sector or product area to review", "example": "stablecoins"},
    {"name": "WINDOW_DAYS", "description": "Trailing window in days", "example": "30"}
  ],
  "personas": [],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 600, "budget_usd": 4.0, "verify": "charts"}
}
---

Using the cerebro tools, give me a light health check of the {{SECTOR}} sector over the last {{WINDOW_DAYS}} days.

Follow the lite-report path exactly — inline charts, no report artifact:

1. `preflight_analytics_request(query, mode="answer")` for this question.
2. Discover narrowly: `search_models` with a tight query and `limit=15`; keep the discovered set small.
3. `get_model_details` on the 1-2 models you will actually use, then `describe_table` on the primary fact table to verify exact column names.
4. Run 2-3 `execute_query` calls: the headline trend, one dimensional split, using medians/quantiles rather than plain averages where distribution matters.
5. ONE `generate_charts` batch with 2-4 charts: the headline trend over time plus at least one breakdown (a `series_field` split or a pie). Render the charts inline in your reply — one-line takeaway per chart, each chart's SQL in a collapsible block labeled with its source model.
6. Close with a 3-5 sentence read: what grew, what shrank, what to watch next.

Do NOT call `generate_report` — present the charts inline and stop.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
