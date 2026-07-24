---
{
  "id": "case_study_growth_story",
  "label": "Growth case study",
  "purpose": "A persuasive scrollytelling growth story for an external audience, with every number co-signed by the statistical reviewer.",
  "category": "narrative",
  "tier": "full_report",
  "deliverable": "A scrollytelling case-study artifact (hero, scenes with sticky visuals, progressive reveals, CTA) whose claims passed a statistical co-sign.",
  "params": [
    {"name": "PRODUCT", "description": "Product whose growth story to tell", "example": "Gnosis Pay"},
    {"name": "AUDIENCE", "description": "Who the story is for", "example": "prospective ecosystem partners"}
  ],
  "personas": ["marketing_analyst", "statistical_reviewer"],
  "verify_personas": ["marketing_analyst", "statistical_reviewer"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1800, "budget_usd": 12.0, "verify": "report_file"}
}
---

This is a two-persona chain — external-audience framing with a mandatory numbers co-sign.

**Frame.** Call `get_agent_persona("marketing_analyst")` and follow its rules for {{AUDIENCE}}: zero-baseline axes, no cherry-picked timeframes, every metric contextualized.

**Data.** Report fast path: `preflight_analytics_request(query, mode="report")` for "{{PRODUCT}} growth story"; `search_models` (limit 15); `get_model_details` on 3 models; `describe_table`; at least 2 `execute_query` EDA calls incl. one statistical; ONE coverage sweep (`exclude_all_discovered_except`); ONE `generate_charts` batch of 4-6 charts telling the arc — adoption trend, usage mix (`series_field`), a milestone/inflection view, and one relational chart.

**Co-sign.** Call `get_agent_persona("statistical_reviewer")` and audit every number the story will state: window fairness, variance vs. claimed growth, no causal language without design. The dispatcher's rule applies — a marketing deliverable ships only with this co-sign. Fix what fails; keep a list of what changed.

**Publish.** `generate_case_study_report`:
- `deck` (≤240 chars) and `key_points` (3-6 concrete proof points).
- Scenes via `{{scene chart="CHART_ID" side="left"}}...{{/scene}}` — narrative scrolling past sticky visuals; use `{{reveal}}` bullet reveals for the proof points and a closing `{{cta label="..." href="..."}}` appropriate to {{AUDIENCE}}.
- Honest framing: include one "what could slow this" beat — credibility sells.

Reply with the file:// link, the deck, and the key points. No emojis anywhere.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
