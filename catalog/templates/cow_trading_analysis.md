---
{
  "id": "cow_trading_analysis",
  "label": "CoW trading analysis",
  "purpose": "Trading activity on CoW Protocol for a pair, solver, or chain — by the CoW specialist over the curated cow_db.",
  "category": "deep_dive",
  "tier": "lite_report",
  "deliverable": "Inline charts of fills, volume proxy, and the requested breakdown plus a short read.",
  "params": [
    {"name": "FOCUS", "description": "Pair, solver, or chain to analyze", "example": "the WETH/USDC pair on Ethereum"},
    {"name": "WINDOW_DAYS", "description": "Trailing window in days", "example": "30"}
  ],
  "personas": ["cow_analyst"],
  "verify_personas": ["cow_analyst"],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 900, "budget_usd": 5.0, "verify": "charts"}
}
---

Adopt the CoW specialist first: call `get_agent_persona("cow_analyst")` and follow its rules — cow_db has NO semantic coverage, so `describe_table(database="cow_db")` IS the discovery step; skip `search_models`/`discover_metrics` entirely.

Then analyze CoW Protocol trading for {{FOCUS}} over the last {{WINDOW_DAYS}} days.

Non-negotiable data discipline from the persona:
- Deduplicate fills with `uniqExact((tx_hash, log_index, order_uid))` — recent rows sit in unmerged ReplacingMergeTree parts and double-count otherwise.
- Never conflate the three price series (execution, auction reference, native API) — label which one a chart shows.
- Counts first; only quote native-unit volume where the persona's rules allow it, and label units explicitly.

1. `describe_table` the cow_db tables you need (trades, orders as applicable) — verify exact columns.
2. Query: daily fills for the focus, the relevant breakdown (per solver / per pair / per chain depending on the focus), and one distribution stat (e.g. fill-size quantiles).
3. ONE `generate_charts` batch, 2-4 charts: the daily fills trend, the breakdown (series_field), and the distribution.
4. Close with a short read: activity direction, who/what dominates the breakdown, and any data caveat the persona's rules flag (e.g. BNB timestamp gaps if relevant).

Render charts inline with SQL in collapsible blocks. Do NOT call `generate_report`.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
