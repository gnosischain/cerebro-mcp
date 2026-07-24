---
{
  "id": "mta_journey_attribution",
  "label": "MTA journey attribution",
  "purpose": "Which touchpoints precede a conversion — multi-touch attribution with methods side-by-side and a statistical co-sign. Observational, never causal.",
  "category": "attribution",
  "tier": "persona_workflow",
  "deliverable": "Inline attribution analysis: journey coverage, funnel, at least three attribution methods compared, statistical review notes — with the observational disclaimer.",
  "params": [
    {"name": "CONVERSION", "description": "The conversion event to attribute (be precise)", "example": "a wallet's first Gnosis Pay transaction"},
    {"name": "IDENTITY_GRAIN", "description": "The identity joining touchpoints to conversions", "example": "wallet address"},
    {"name": "LOOKBACK_DAYS", "description": "Attribution lookback window in days", "example": "30"}
  ],
  "personas": ["mta_analyst", "statistical_reviewer"],
  "verify_personas": ["mta_analyst", "statistical_reviewer"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1800, "budget_usd": 12.0, "verify": "charts"}
}
---

This is the MTA chain with a statistical co-sign. MTA output is OBSERVATIONAL — no causal claims, ever.

**Attribute.** Call `get_agent_persona("mta_analyst")` and run its SOP with these inputs it hard-requires:
- Conversion definition: {{CONVERSION}}
- Identity grain: {{IDENTITY_GRAIN}} (state its limits — an address is not a person)
- Lookback: {{LOOKBACK_DAYS}} days

Discovery is mandatory EVERY run: `search_models` / `discover_models`, then `describe_table` on every model you use — the persona's context examples are illustrative, not a contract. Build the journey spine at the stated grain, enforce `touch_ts <= conversion_ts` (post-conversion leakage is the classic bug), and disclose coverage (share of conversions with any observed touchpoint). Respect the volume gates: under 30 conversions → descriptive only; under 500 → rule-based + funnel only, no Markov/Shapley. When volume permits, compare at least three attribution methods side-by-side (e.g. last-touch, time-decay, Markov removal).

**Co-sign.** Call `get_agent_persona("statistical_reviewer")` and audit the shares: sample sizes per touchpoint, stability across the methods, and language — every attribution statement must read as correlation ("preceded", "is credited"), never causation ("drove", "caused"). Fix what fails.

**Deliver inline.** ONE `generate_charts` batch, 3-4 charts: the funnel, per-touchpoint credit across methods (grouped bar, series_field = method), and the coverage disclosure. Close with the method-comparison table, the observational disclaimer verbatim, and what an experiment would need to test causally. Do NOT call `generate_report`.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
