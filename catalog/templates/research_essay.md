---
{
  "id": "research_essay",
  "label": "Research report (essay)",
  "purpose": "A long-form research essay on one product — narrative argument, figures, key takeaways — in the whitepaper layout.",
  "category": "narrative",
  "tier": "full_report",
  "deliverable": "A research-essay report artifact (deck, 3-6 key takeaways, numbered sections with figures, methodology note).",
  "params": [
    {"name": "PRODUCT", "description": "Product or protocol to research", "example": "Gnosis Pay"},
    {"name": "WINDOW_DAYS", "description": "Analysis window in days", "example": "180"}
  ],
  "personas": ["gnosis_research_analyst"],
  "verify_personas": ["gnosis_research_analyst"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1800, "budget_usd": 12.0, "verify": "report_file"}
}
---

Adopt the research lead first: call `get_agent_persona("gnosis_research_analyst")` — semantic-first discipline: prefer `discover_metrics`/`query_metrics` over raw SQL wherever coverage exists, and preserve the provenance of every number.

Write a research report on {{PRODUCT}}, covering the last {{WINDOW_DAYS}} days.

Data phase (report fast path):
1. `preflight_analytics_request(query, mode="report")`.
2. Discover semantically first (`discover_metrics`), then `search_models` (limit 15) for anything uncovered.
3. `get_model_details` on 3 models → `describe_table` → at least 2 `execute_query`/`query_metrics` calls incl. one statistical (quantiles/stddev/corr).
4. ONE coverage sweep (`exclude_all_discovered_except`).
5. ONE `generate_charts` batch, 4-6 charts that will serve as FIGURES: adoption trend, usage breakdown (`series_field`), a distribution view, and one relational chart (scatter/heatmap).

Writing phase — use `generate_research_report` (NOT generate_report):
- `deck`: a sub-headline of at most 240 characters stating the thesis.
- `key_takeaways`: 3-6 items, each a finding not a topic.
- Body: numbered sections building an argument (adoption, usage economics, risks, outlook), figures embedded via `{{figure:CHART_ID caption="..." source="..."}}`, at least one `{{callout kind=note}}` for a methodology caveat, and a closing methodology section naming models, windows, and exclusions.
- Every quantitative claim must trace to a figure or a stated query result. No causal language without design; correlational statements need the stationarity caveat.

Reply with the file:// link and the deck + takeaways as text. Do not paste the full essay into chat.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
