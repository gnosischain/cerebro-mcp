---
{
  "id": "sector_checkin_full",
  "label": "Sector deep-dive dashboard",
  "purpose": "A full analytical dashboard report on one sector: KPIs, trends, breakdowns, and statistical depth, assembled by the Data Science Lead persona.",
  "category": "sector_health",
  "tier": "full_report",
  "deliverable": "An interactive dashboard report artifact (openable file:// link) with KPI grid, trend, dimensional breakdown, and a relational chart.",
  "params": [
    {"name": "SECTOR", "description": "Sector or product area to analyze", "example": "lending"},
    {"name": "WINDOW_DAYS", "description": "Trailing window in days", "example": "90"}
  ],
  "personas": ["analytics_reporter"],
  "verify_personas": ["analytics_reporter"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1200, "budget_usd": 10.0, "verify": "report_file"}
}
---

Adopt the Data Science Lead persona first: call `get_agent_persona("analytics_reporter")` and follow its SOP.

Then produce a full analytical dashboard report on the {{SECTOR}} sector over the last {{WINDOW_DAYS}} days.

Execute the report fast path with no wasted calls:

1. `preflight_analytics_request(query, mode="report")` — this is the only tier that builds a report artifact.
2. `search_models` with a tight query and `limit=15` — every discovered model counts toward the coverage gate, so keep the set small.
3. `get_model_details` on the 3 models you will actually use, then `describe_table` on the primary fact table.
4. At least 2 `execute_query` EDA calls, including one statistical query (quantiles / stddev / corr). Prefer medians over means.
5. ONE coverage sweep for everything you will not query: `exclude_all_discovered_except(keep=[...], reason="...")` — never loop the singular exclusion tool.
6. ONE `generate_charts` batch with 5-7 charts: KPI numberDisplay cards (wrap them in {{grid:3}} in the report), the headline trend, at least one breakdown chart with `series_field` (or pie/treemap), and at least one scatter or heatmap for the relational gate.
7. `generate_report` with a clear title, short section commentary, and a key-takeaways section formatted as a three-column table (Takeaway | Evidence | Why it matters).
8. Include the report's file:// link in your reply and summarize the top findings in 3 bullets.

Do not repeat the report markdown in chat, and do not ask clarifying questions — make reasonable assumptions and state them.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
