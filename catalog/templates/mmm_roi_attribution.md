---
{
  "id": "mmm_roi_attribution",
  "label": "MMM incentive ROI attribution",
  "purpose": "Which incentives actually drove a KPI — marketing-mix modeling with the mandatory causal-review gate.",
  "category": "attribution",
  "tier": "persona_workflow",
  "deliverable": "An MMM report artifact: contribution decomposition, response curves, adstock decay, ROI with bootstrap intervals — published only after a causal-review PASS.",
  "params": [
    {"name": "KPI", "description": "Outcome the incentives are supposed to drive", "example": "TVL"},
    {"name": "SECTOR", "description": "Sector whose incentive programs to attribute", "example": "lending"}
  ],
  "personas": ["mmm_analyst", "mmm_causal_reviewer"],
  "verify_personas": ["mmm_analyst", "mmm_causal_reviewer"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 2400, "budget_usd": 15.0, "verify": "report_file"}
}
---

This is the gated MMM chain. The gate is behavioral, and this template is what upholds it: **do NOT call `generate_report` until the causal reviewer returns VERDICT: PASS.**

**Model.** Call `get_agent_persona("mmm_analyst")` and run its SOP for: which incentive/media variables drove {{KPI}} in the {{SECTOR}} sector. Discover and verify models first (`search_models`, `describe_table` — never trust illustrative names), build the continuous weekly spine, check multicollinearity, extract the baseline, fit adstock + both response-curve forms (concave and Hill — keep the lower-MAE fit), decompose contributions, and bootstrap confidence intervals for every ROI number. If the data gives fewer than 60 weekly rows or a structural break the persona's rules flag, downgrade the analysis to directional and say so.

**Causal gate.** Synthesize the model into a markdown DAG table — nodes = variables, edges = hypothesized causation, flags on co-launched or confounded pairs. Then call `get_agent_persona("mmm_causal_reviewer")` and, as the reviewer, run its three checks (chronological, non-inclusion, identifiability) against that DAG table verbatim, producing the verdict table and `VERDICT: PASS` or `VERDICT: BLOCK`. On BLOCK: apply the prescribed fix (intervention, segmentation, or front-door variable), refit what changed, and re-review. Only a PASS unlocks the next step.

**Report.** ONE `generate_charts` batch with the five required charts — contribution stacked-area over time (series_field = media), spend vs. effectiveness share (grouped bar), response curve per media (scatter + fitted line), adstock decay per media (bar), and the causal-review verdict table rendered in the report body. Coverage sweep (`exclude_all_discovered_except`), then `generate_report` including the PASS verdict, the ROI table with intervals, and a key-takeaways table (Takeaway | Evidence | Why it matters).

Reply with the file:// link, the verdict, and the top ROI finding with its interval.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
