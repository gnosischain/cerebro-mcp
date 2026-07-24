---
{
  "id": "quick_scalar_answer",
  "label": "Quick scalar answer",
  "purpose": "Get one current number — balance, supply, TVL, active users — with its as-of date and source.",
  "category": "answer",
  "tier": "quick_answer",
  "deliverable": "A one-paragraph answer: the number, its as-of timestamp, and the source metric or model. No charts, no report.",
  "params": [
    {"name": "METRIC", "description": "The scalar to fetch (balance, supply, TVL, active users, holders, ...)", "example": "active users"},
    {"name": "SCOPE", "description": "Product, token, or sector the metric applies to", "example": "Gnosis Pay"}
  ],
  "personas": [],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 300, "budget_usd": 1.0, "verify": "answer"}
}
---

Using the cerebro tools, answer this and only this:

What is the current {{METRIC}} of {{SCOPE}}?

Follow the quick-answer path — it is the cheapest route and needs no preflight:

1. Call `find` with the question and `mode="answer"`. It routes to the right metric or model in one call and returns a pre-filled `recommended_action`.
2. Follow that recommendation directly: `query_metrics` when a governed metric covers it; otherwise ONE `execute_query` against the recommended model (verify exact column names with `describe_table` before writing SQL).
3. Reply with: the number, the as-of date of the underlying data, and the source metric or model name — AND make the metric's definition precise: state the grain and qualifier as documented in the source (e.g. "weekly wallet-distinct active users", never a bare metric name), and note the data's freshness (the latest ingested date and whether the most recent period may still be accumulating).

Do NOT generate charts, do NOT call `preflight_analytics_request`, do NOT build a report. One clearly-sourced, precisely-defined number, then stop.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
