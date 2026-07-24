---
{
  "id": "forecast_outlook",
  "label": "Forecast outlook",
  "purpose": "Where a metric is heading: decomposition, seasonality, and a bounded forecast with holdout validation, by the forecasting specialist.",
  "category": "forecast",
  "tier": "persona_workflow",
  "deliverable": "A dashboard report artifact: history + decomposition, the forecast with uncertainty bounds, and validation evidence.",
  "params": [
    {"name": "METRIC", "description": "Metric to forecast (needs a long daily history)", "example": "daily transactions on Gnosis Chain"},
    {"name": "HORIZON_DAYS", "description": "Forecast horizon in days", "example": "30"}
  ],
  "personas": ["forecasting_analyst"],
  "verify_personas": ["forecasting_analyst"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1500, "budget_usd": 10.0, "verify": "report_file"}
}
---

Adopt the forecasting specialist first: call `get_agent_persona("forecasting_analyst")` and follow its SOP — never a point forecast without uncertainty bounds, decomposition before forecasting, holdout validation always.

Then forecast {{METRIC}} {{HORIZON_DAYS}} days ahead.

Important: the persona's SQL toolkit contains ILLUSTRATIVE table names that are not in the live catalog — always run `search_models` and `describe_table` first and use only verified model/column names.

1. `preflight_analytics_request(query, mode="report")`.
2. `search_models` (limit 15) → `get_model_details` on the models you will use → `describe_table`.
3. Pull the full daily history with `execute_query`. The persona refuses to forecast on under 60 points or across a known structural break — check both and say what you found.
4. Decompose: trend + weekly seasonality (the persona's SQL decomposition approach); quantify variance (a statistical query — also satisfies the report gate).
5. Validate: hold out the last {{HORIZON_DAYS}} days, fit on the rest, report the holdout error (MAPE or MAE) honestly.
6. Forecast {{HORIZON_DAYS}} days with ±1.96σ bounds. ONE coverage sweep (`exclude_all_discovered_except`).
7. ONE `generate_charts` batch, 4-6 charts: history + trend, the seasonality profile (series_field or heatmap by weekday), holdout fit vs. actuals, and the forecast with bounds. KPI cards in {{grid:3}} for current level, trend slope, and holdout error.
8. `generate_report` with a key-takeaways table (Takeaway | Evidence | Why it matters); the outlook section must present the bounds as the finding, not the point estimate.
9. Reply with the file:// link and a 3-bullet summary.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
