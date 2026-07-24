---
{
  "id": "storyteller_decision_brief",
  "label": "Decision brief (storyteller)",
  "purpose": "A decision-driving narrative brief for a specific audience, through the full gated storytelling pipeline.",
  "category": "narrative",
  "tier": "persona_workflow",
  "deliverable": "A narrative report artifact built through the storyteller pipeline: context brief, big idea, storyboard, designed visuals, clarity and accessibility gates, then the story report.",
  "params": [
    {"name": "AUDIENCE", "description": "WHO the brief is for — must be specific (the context gate refuses vague audiences like 'stakeholders')", "example": "the Gnosis DAO treasury committee deciding next quarter's incentive budget"},
    {"name": "QUESTION", "description": "The decision question the brief must drive", "example": "should incentive spend shift from liquidity mining toward Gnosis Pay cashback"}
  ],
  "personas": ["storyteller_orchestrator"],
  "verify_personas": ["storyteller_orchestrator"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 2400, "budget_usd": 15.0, "verify": "report_file"}
}
---

Run the full Storytelling-with-Data pipeline. Adopt the orchestrator first — `get_agent_persona("storyteller_orchestrator")` — and let it drive the sub-agent chain in order; the pipeline's artifact gates are code-enforced, so do not skip steps.

Produce a decision brief for {{AUDIENCE}} on: {{QUESTION}}.

The canonical sequence:
1. `storyteller_start_session`, then the context agent (`get_agent_persona("storyteller_context")`) records the context brief via `storyteller_record_context_brief` — audience exactly as given above (it is deliberately specific), required action, mechanism. If anything is genuinely missing, make the most reasonable assumption and state it in the brief rather than stalling.
2. Explore the data with the cerebro tools (discover narrowly, verify columns, query with medians over means) — the evidence must exist before the narrative.
3. Narrative agent → `storyteller_record_big_idea` (one sentence, stakes) and `storyteller_record_storyboard`.
4. Visual designer → `storyteller_record_visual_spec` per scene; produce the charts with ONE `generate_charts` batch matching the specs.
5. Writer → `storyteller_record_final_story`.
6. Critic → `storyteller_run_clarity_checks`; fix and loop on any failure. Accessibility → `storyteller_record_accessibility_pass`.
7. `storyteller_generate_story_report(style="research")` — the artifact.

Reply with the file:// link, the big idea sentence, and the recommended action. No emojis.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
