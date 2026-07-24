---
{
  "id": "tokenomics_concentration",
  "label": "Holder concentration report",
  "purpose": "How concentrated a token's holder base is — HHI, Gini, Nakamoto coefficient, top-holder shares — by the tokenomics specialist.",
  "category": "deep_dive",
  "tier": "lite_report",
  "deliverable": "Inline charts of holder distribution and concentration indices plus a short concentration read.",
  "params": [
    {"name": "TOKEN", "description": "Token to analyze", "example": "GNO"}
  ],
  "personas": ["tokenomics_analyst"],
  "verify_personas": ["tokenomics_analyst"],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 900, "budget_usd": 5.0, "verify": "charts"}
}
---

Adopt the tokenomics specialist first: call `get_agent_persona("tokenomics_analyst")` and follow its SOP — correct HHI/Gini/Nakamoto formulas, 1e18 decimal normalization, and grouping conventions matter here.

Then analyze holder concentration for {{TOKEN}}.

Important: the persona's SQL toolkit contains ILLUSTRATIVE table names that are not in the live catalog — always run `search_models` and `describe_table` first and use only verified model/column names.

1. `preflight_analytics_request(query, mode="answer")`.
2. `search_models` for {{TOKEN}} holder/balance models (limit 15) → `get_model_details` → `describe_table`. Use the current holder snapshot (a stock measure — never sum balances over a date range; take the latest snapshot or argMax).
3. Query: top-10/top-100 holder shares, the full distribution by balance decile, and the concentration indices (HHI, Gini, Nakamoto coefficient) computed with the persona's formulas. Disclose whether contracts/bridges/treasury addresses are included or excluded — and if excluded, say so in the chart subtitle.
4. ONE `generate_charts` batch, 2-4 charts: top-holder share (bar or treemap), the decile distribution, and the concentration summary as KPI cards.
5. Close with a short read: how concentrated vs. typical governance tokens, which single entities dominate, and the exclusion caveats.

Render charts inline with SQL in collapsible blocks. Do NOT call `generate_report`.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
