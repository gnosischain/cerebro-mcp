---
{
  "id": "single_chart",
  "label": "Single chart",
  "purpose": "One clean chart of a metric over time, rendered inline. The fastest visual answer.",
  "category": "chart",
  "tier": "single_chart",
  "deliverable": "One inline chart (with its SQL in a collapsible block) and a one-line takeaway. No report.",
  "params": [
    {"name": "METRIC", "description": "What to plot", "example": "daily transactions"},
    {"name": "SCOPE", "description": "Product, token, or sector", "example": "Gnosis Chain"},
    {"name": "WINDOW_DAYS", "description": "Trailing window in days", "example": "30"}
  ],
  "personas": [],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 600, "budget_usd": 2.0, "verify": "charts"}
}
---

Using the cerebro tools, plot {{METRIC}} for {{SCOPE}} over the last {{WINDOW_DAYS}} days.

Follow the single-chart path — this is a chart request, not a report:

1. `preflight_analytics_request(query, mode="chart")` for this exact ask.
2. Route to data: prefer a governed metric (`discover_metrics` → `quick_metric_chart`) when one covers it; otherwise `search_models` (limit 15) → `describe_table` on the one model you need → ONE `generate_charts` call with a single time-series chart.
3. Render the chart inline in your reply with a one-line takeaway and the SQL in a collapsible block labeled with the source model.

Then STOP. Do not add more charts, do not call `generate_report`, do not expand the scope.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
