---
{
  "id": "defi_protocol_deep_dive",
  "label": "DeFi protocol deep-dive",
  "purpose": "TVL, utilization, and activity for one DeFi protocol from decoded on-chain events, by the DeFi specialist.",
  "category": "deep_dive",
  "tier": "full_report",
  "deliverable": "An interactive dashboard report artifact: TVL trend, utilization, market/asset breakdowns, activity KPIs.",
  "params": [
    {"name": "PROTOCOL", "description": "Protocol to analyze", "example": "Aave on Gnosis"},
    {"name": "WINDOW_DAYS", "description": "Trailing window in days", "example": "90"}
  ],
  "personas": ["defi_analyst"],
  "verify_personas": ["defi_analyst"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1200, "budget_usd": 10.0, "verify": "report_file"}
}
---

Adopt the DeFi specialist first: call `get_agent_persona("defi_analyst")` and follow its SOP — decoded `contracts_*` events over raw logs, stated TVL methodology, normalized decimals.

Then produce a deep-dive dashboard report on {{PROTOCOL}} over the last {{WINDOW_DAYS}} days.

Important: the persona's SQL toolkit contains ILLUSTRATIVE table names that are not in the live catalog — always run `search_models` and `describe_table` first and use only verified model/column names.

Execute the report fast path:

1. `preflight_analytics_request(query, mode="report")`.
2. `search_models` for the protocol's models (limit 15, tight query).
3. `get_model_details` on the 3 models you will use → `describe_table` the primary fact table.
4. At least 2 `execute_query` EDA calls incl. one statistical (quantiles/stddev/corr). TVL is a STOCK measure: never sum it over a date range — use point-in-time snapshots (argMax or latest-date) and say which methodology you used.
5. ONE coverage sweep: `exclude_all_discovered_except(keep=[...], reason="...")`.
6. ONE `generate_charts` batch, 5-7 charts: KPI cards ({{grid:3}}), the TVL trend, a per-market/per-asset breakdown with `series_field`, utilization over time, and one scatter or heatmap (e.g. utilization vs. size per market).
7. `generate_report` with sections (state of the protocol, markets, risks/watchlist) and a key-takeaways table (Takeaway | Evidence | Why it matters).
8. Reply with the file:// link and a 3-bullet summary.

State the TVL methodology explicitly in the report. Do not ask clarifying questions — state assumptions instead.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
