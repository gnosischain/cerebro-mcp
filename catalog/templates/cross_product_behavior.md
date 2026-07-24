---
{
  "id": "cross_product_behavior",
  "label": "Cross-product user behavior",
  "purpose": "What the users of one product do on another — wallet-cohort overlap and cross-activity, inline.",
  "category": "deep_dive",
  "tier": "lite_report",
  "deliverable": "Cohort-overlap KPIs plus 2-4 inline charts: share of product-A users active on product B, what they do there, and how their B-activity compares to the average B user.",
  "params": [
    {"name": "PRODUCT_A", "description": "The product whose users define the cohort", "example": "Gnosis Pay"},
    {"name": "PRODUCT_B", "description": "The product whose activity is analyzed for that cohort", "example": "Aave"},
    {"name": "WINDOW_DAYS", "description": "Activity window in days for both sides", "example": "30"}
  ],
  "personas": [],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 900, "budget_usd": 5.0, "verify": "charts"}
}
---

Using the cerebro tools, analyze what users of {{PRODUCT_A}} do on {{PRODUCT_B}} over the last {{WINDOW_DAYS}} days.

This is a wallet-cohort cross-product analysis. Follow the lite path with disciplined cohort mechanics:

1. `preflight_analytics_request(query, mode="answer")`.
2. Discover BOTH sides: `search_models` once for {{PRODUCT_A}} user/activity models and once for {{PRODUCT_B}} activity models (limit 15 each, tight queries). `get_model_details` + `describe_table` on the one model per side you will actually join.
3. Define the cohort explicitly: addresses active on {{PRODUCT_A}} within the window, at the address grain the A-side model provides. STATE the grain and its caveat (an address is not a person; smart-account/Safe owners may differ from signer EOAs).
4. Join in SQL, not in prose: cohort CTE from A ⋈ activity on B. Compute at minimum — cohort size, share of cohort active on B, what they do there (top action/market breakdown), and how their per-user B-activity compares to the average B user.
5. ONE `generate_charts` batch, 2-4 charts: the overlap share, a breakdown of the cohort's B-side actions (series_field or pie), and a comparison vs. all B users.
6. Close with 3-5 sentences: overlap size, dominant behavior, and one caveat about identity grain and window sensitivity.

Render charts inline with SQL in collapsible blocks. Do NOT call `generate_report`. If either product has no usable address-grain model, say so explicitly rather than approximating silently.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
