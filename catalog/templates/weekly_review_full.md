---
{
  "id": "weekly_review_full",
  "label": "Last-week review (dashboard)",
  "purpose": "Last week as a shareable dashboard artifact: WoW KPIs, trend, movers, and breakdowns, built by the Data Science Lead.",
  "category": "sector_health",
  "tier": "full_report",
  "deliverable": "An interactive dashboard report artifact (file:// link): WoW KPI grid, 14-day trends, mover breakdowns.",
  "params": [
    {"name": "SCOPE", "description": "Product or sector to review", "example": "Gnosis Pay"}
  ],
  "personas": ["analytics_reporter"],
  "verify_personas": ["analytics_reporter"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1200, "budget_usd": 10.0, "verify": "report_file"}
}
---

Adopt the Data Science Lead persona first: call `get_agent_persona("analytics_reporter")` and follow its SOP.

Build a last-week review DASHBOARD for {{SCOPE}}. "Last week" = the last 7 full UTC days, compared against the 7 days before.

Execute the report fast path with no wasted calls:

1. `preflight_analytics_request(query, mode="report")`.
2. `search_models` (tight query, limit 15) — keep the discovered set small.
3. `get_model_details` on the 3 models you will use → `describe_table` the primary fact table.
4. At least 2 `execute_query` EDA calls covering both weeks, including one statistical query (quantiles or stddev of the daily values — it also tells you whether the WoW change exceeds normal variance).
5. ONE coverage sweep: `exclude_all_discovered_except(keep=[...], reason="...")`.
6. ONE `generate_charts` batch, 5-7 charts: WoW KPI numberDisplay cards (wrap in {{grid:3}}), the 14-day daily trend, a mover breakdown with `series_field`, and one scatter or heatmap (e.g. daily values week-vs-week) for the relational gate.
7. `generate_report` titled "{{SCOPE}} — weekly review": short sections (headline, movers, watchlist) and a key-takeaways table (Takeaway | Evidence | Why it matters). Call the change trend vs. noise explicitly, using the variance from step 4.
8. Reply with the file:// link and a 3-bullet summary.

Do not repeat the report markdown in chat; do not ask clarifying questions — state assumptions instead.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
