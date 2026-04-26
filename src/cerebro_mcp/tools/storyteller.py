"""MCP tool surface for Storyteller mode.

Storyteller is an opt-in, multi-agent pipeline layered on top of the standard
Cerebro report pipeline. Standard mode (`generate_charts` → `generate_report`)
is unchanged; these tools only activate when the user explicitly starts a
storyteller session.

The tools are thin wrappers around `storyteller_state` and
`storyteller_models`. The calling LLM adopts one of the storyteller agent
personas (via `get_agent_persona`) and records each artifact through these
tools, which enforce the cross-agent gates.

Standard-mode users should never need any of these tools.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.storyteller_models import (
    BigIdea,
    ClarityCheck,
    ContextBrief,
    ReviewReport,
    Storyboard,
    StoryboardScene,
    StorytellerSnapshot,
    VisualSpec,
)
from cerebro_mcp.storyteller_state import storyteller_state


def _research_metadata_from_snapshot(snap: StorytellerSnapshot) -> dict[str, Any]:
    """Map a fully-baked storyteller snapshot to research-layout metadata.

    Field mapping:
      - deck            ← big_idea.sentence (the one declarative POV sentence)
      - key_takeaways   ← storyboard scene intents (3–6 items, padded with
                          big_idea sentence + stakes if fewer than 3 scenes)
      - category        ← context_brief.mechanism (e.g. 'memo', 'deck')
      - footnotes       ← context_brief.weakens_case (kept visible per the
                          storyteller "honesty" rule)
    Authors and published_date are left to defaults (none / today).
    """
    big_idea = snap.big_idea
    storyboard = snap.storyboard
    brief = snap.context_brief

    # Deck: prefer the big idea sentence; truncate to research-layout's 240 cap.
    deck_raw = (big_idea.sentence if big_idea else "").strip()
    if len(deck_raw) > 240:
        deck_raw = deck_raw[:237].rstrip() + "…"

    # Takeaways: storyboard scene intents, padded if needed.
    takeaways: list[str] = []
    if storyboard is not None:
        for scene in storyboard.scenes:
            intent = (scene.intent or "").strip()
            if intent:
                takeaways.append(intent)
    # Pad with big_idea sentence + stakes if we don't have ≥3.
    if big_idea is not None:
        if len(takeaways) < 3 and big_idea.sentence:
            sentence = big_idea.sentence.strip()
            if sentence and sentence not in takeaways:
                takeaways.append(sentence)
        if len(takeaways) < 3 and big_idea.stakes:
            stakes = big_idea.stakes.strip()
            if stakes and stakes not in takeaways:
                takeaways.append(stakes)
    # Cap at 6 to satisfy the research gate.
    takeaways = takeaways[:6]

    category = None
    if brief is not None:
        mechanism = getattr(brief, "mechanism", None)
        if mechanism:
            category = f"Storyteller · {str(mechanism).title()}"

    footnotes: list[dict[str, str]] = []
    if brief is not None and getattr(brief, "weakens_case", "").strip():
        footnotes.append(
            {"id": "weakens", "text": f"Counter-evidence: {brief.weakens_case.strip()}"}
        )

    return {
        "deck": deck_raw,
        "key_takeaways": takeaways,
        "category": category,
        "footnotes": footnotes,
    }


def _case_study_metadata_from_snapshot(snap: StorytellerSnapshot) -> dict[str, Any]:
    """Map a fully-baked storyteller snapshot to case-study metadata.

    Field mapping:
      - deck           ← big_idea.sentence (declarative POV, capped at 240 chars)
      - key_points     ← storyboard scene intents (3–6 items, padded with
                         big_idea sentence + stakes if fewer than 3 scenes)
      - category       ← context_brief.mechanism, title-cased
      - cta            ← derived from context_brief.required_action if it
                         contains a URL; else left None
    """
    big_idea = snap.big_idea
    storyboard = snap.storyboard
    brief = snap.context_brief

    deck_raw = (big_idea.sentence if big_idea else "").strip()
    if len(deck_raw) > 240:
        deck_raw = deck_raw[:237].rstrip() + "…"

    points: list[str] = []
    if storyboard is not None:
        for scene in storyboard.scenes:
            intent = (scene.intent or "").strip()
            if intent:
                points.append(intent)
    if big_idea is not None:
        if len(points) < 3 and big_idea.sentence:
            sentence = big_idea.sentence.strip()
            if sentence and sentence not in points:
                points.append(sentence)
        if len(points) < 3 and big_idea.stakes:
            stakes = big_idea.stakes.strip()
            if stakes and stakes not in points:
                points.append(stakes)
    points = points[:6]

    category = None
    if brief is not None:
        mechanism = getattr(brief, "mechanism", None)
        if mechanism:
            category = str(mechanism).replace("_", " ").title()

    cta: dict[str, str] | None = None
    if brief is not None:
        action = (getattr(brief, "required_action", "") or "").strip()
        url_match = re.search(r"https?://\S+", action)
        if url_match:
            url = url_match.group(0)
            label = action.replace(url, "").strip(" -:—") or "Learn more"
            cta = {"label": label, "href": url}

    return {
        "deck": deck_raw,
        "key_points": points,
        "category": category,
        "cta": cta,
    }


def _ok(snapshot: StorytellerSnapshot, heading: str) -> CallToolResult:
    """Serialize a snapshot into a structured tool result with a human hint."""
    next_step = snapshot.next_step or ""
    phase = snapshot.phase
    summary = f"{heading}\n\nPhase: {phase}\nNext: {next_step}"
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structuredContent={
            "active": snapshot.active,
            "phase": phase,
            "next_step": next_step,
            "has_context_brief": snapshot.context_brief is not None,
            "has_big_idea": snapshot.big_idea is not None,
            "has_storyboard": snapshot.storyboard is not None,
            "visual_spec_count": len(snapshot.visual_specs),
            "review_ready_for_handoff": (
                snapshot.review_report.ready_for_handoff
                if snapshot.review_report
                else False
            ),
        },
    )


def _err(exc: Exception) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Storyteller error: {exc}")],
        isError=True,
    )


def register_storyteller_tools(mcp, ch=None) -> None:
    """Register all storyteller MCP tools on the given FastMCP instance.

    `ch` (ClickHouseManager) is accepted for signature symmetry with other
    registrars but is not used: storyteller tools are pure state and
    persona routing; data access happens through the standard Cerebro tools.
    """

    # ── Session lifecycle ───────────────────────────────────────────

    @mcp.tool()
    def storyteller_start_session() -> CallToolResult:
        """Begin a new storyteller session.

        Call this when the user has explicitly asked for a story, narrative,
        memo, executive brief, pitch, recommendation, or decision artifact —
        or when you have confirmed with the user that they want storyteller
        mode rather than a standard report.

        Clears any prior storyteller state. Does NOT affect the standard
        Cerebro session state (search_models, generate_charts, etc.).

        After calling this, fetch the Orchestrator persona via
        `get_agent_persona("storyteller_orchestrator")` and start the context
        phase with `get_agent_persona("storyteller_context")`.

        Returns:
            The current storyteller snapshot with the next suggested step.
        """
        try:
            snap = storyteller_state.start_session()
            return _ok(snap, "Storyteller session started.")
        except Exception as exc:  # pragma: no cover - defensive
            return _err(exc)

    @mcp.tool()
    def storyteller_end_session() -> CallToolResult:
        """End the current storyteller session and clear all artifacts."""
        try:
            snap = storyteller_state.end_session()
            return _ok(snap, "Storyteller session ended.")
        except Exception as exc:  # pragma: no cover
            return _err(exc)

    @mcp.tool()
    def storyteller_status() -> CallToolResult:
        """Return a snapshot of the current storyteller session state.

        Useful mid-workflow to inspect the current phase, check which gates
        have passed, and see what the next expected action is.
        """
        try:
            snap = storyteller_state.snapshot()
            return _ok(snap, "Storyteller status.")
        except Exception as exc:  # pragma: no cover
            return _err(exc)

    # ── Context phase ───────────────────────────────────────────────

    @mcp.tool()
    def storyteller_record_context_brief(
        audience: str,
        required_action: str,
        mechanism: str,
        tone: str = "neutral",
        background: str = "",
        biases: str = "",
        weakens_case: str = "",
        constraints: str = "",
        success_definition: str = "",
    ) -> CallToolResult:
        """Record the context brief that gates the whole pipeline.

        The audience MUST be specific — a named decision-maker or a concrete
        scoped group. Vague values like "stakeholders" or "leadership" are
        rejected. The required action MUST be articulable; if you cannot
        name a concrete thing the audience should know or do, the
        communication should not exist.

        Args:
            audience: Specific audience (reject "stakeholders").
            required_action: What the audience needs to know or do.
            mechanism: One of live_presentation, slide_deck_leave_behind,
                emailed_deck, memo, brief, dashboard_excerpt, script.
            tone: neutral, celebratory, urgent, cautionary, exploratory, recommendation.
            background: Context the audience may or may not already hold.
            biases: Known biases supporting or resisting the message.
            weakens_case: Opposing evidence (kept visible, never hidden).
            constraints: Time, brand, accessibility constraints.
            success_definition: What a successful outcome looks like.
        """
        try:
            brief = ContextBrief(
                audience=audience,
                required_action=required_action,
                mechanism=mechanism,  # type: ignore[arg-type]
                tone=tone,  # type: ignore[arg-type]
                background=background,
                biases=biases,
                weakens_case=weakens_case,
                constraints=constraints,
                success_definition=success_definition,
            )
            snap = storyteller_state.record_context_brief(brief)
            return _ok(snap, "Context brief recorded.")
        except Exception as exc:
            return _err(exc)

    # ── Narrative phase ─────────────────────────────────────────────

    @mcp.tool()
    def storyteller_record_big_idea(
        sentence: str,
        stakes: str = "",
    ) -> CallToolResult:
        """Record the single governing takeaway as one declarative sentence.

        Must satisfy three criteria (Duarte, via Knaflic ch. 1):
        (1) articulate a point of view;
        (2) convey what is at stake;
        (3) be a complete sentence.

        Labels are rejected. "Q3 revenue" is not a big idea.
        "Q3 revenue missed plan by 12% — cut the bottom two SKUs" is.

        Args:
            sentence: One complete declarative sentence with POV and stakes.
            stakes: Optional explicit stakes statement for the Writer to reuse.
        """
        try:
            idea = BigIdea(sentence=sentence, stakes=stakes)
            snap = storyteller_state.record_big_idea(idea)
            return _ok(snap, "Big idea recorded.")
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    def storyteller_record_storyboard(
        scenes: list[dict[str, Any]],
        narrative_order: str,
        rationale: str = "",
    ) -> CallToolResult:
        """Record the low-fidelity storyboard before any chart is rendered.

        The storyboard is built conceptually: each scene has an intended
        takeaway and a role in the setup → tension → resolution arc. Tension
        is mandatory — flat "everything is fine" narratives are rejected.

        Args:
            scenes: List of scene dicts, each with keys `index` (int),
                `intent` (str, the takeaway), `role` (one of setup, tension,
                evidence, resolution), and optional `notes`.
            narrative_order: "chronological" (builds trust, shows process) or
                "lead_with_ending" (busy executives, trust assumed).
            rationale: Why this narrative order was chosen for this audience.
        """
        try:
            parsed_scenes = [
                StoryboardScene(
                    index=s["index"],
                    intent=s["intent"],
                    role=s["role"],
                    notes=s.get("notes", ""),
                )
                for s in scenes
            ]
            storyboard = Storyboard(
                scenes=parsed_scenes,
                narrative_order=narrative_order,  # type: ignore[arg-type]
                rationale=rationale,
            )
            snap = storyteller_state.record_storyboard(storyboard)
            return _ok(snap, f"Storyboard recorded with {len(parsed_scenes)} scenes.")
        except Exception as exc:
            return _err(exc)

    # ── Visual design phase ─────────────────────────────────────────

    @mcp.tool()
    def storyteller_record_visual_spec(
        scene_index: int,
        relationship: str,
        chart_family: str,
        focal_element: str,
        action_title: str,
        deemphasize: str = "",
        annotations: list[str] | None = None,
        justification: str = "",
        chart_id: str | None = None,
    ) -> CallToolResult:
        """Record the design rationale for one storyboard scene's visual.

        One spec per scene. The `chart_id` is optional and can be attached
        later once the chart is rendered via the existing `generate_charts`
        tool.

        Chart family must match the relationship default unless a written
        justification is supplied. Banned families (pie, donut, 3d, dual_axis)
        are rejected at the model level.

        Args:
            scene_index: Matches the index of a scene in the recorded storyboard.
            relationship: One of single_value, category_comparison, composition,
                trend, distribution, correlation, geographic, start_vs_end,
                running_total.
            chart_family: One of the ~12 workhorse families (simple_text,
                table, heatmap, line, slopegraph, bar_vertical, bar_horizontal,
                stacked_bar_vertical, stacked_bar_horizontal, stacked_bar_100,
                waterfall, scatter, square_area).
            focal_element: The one thing the audience should see first.
            action_title: Sentence title stating the takeaway, not a label.
            deemphasize: What goes grey or to appendix.
            annotations: On-chart callouts.
            justification: Required when chart_family deviates from default.
            chart_id: Optional chart registry id from generate_charts.
        """
        try:
            spec = VisualSpec(
                scene_index=scene_index,
                relationship=relationship,  # type: ignore[arg-type]
                chart_family=chart_family,  # type: ignore[arg-type]
                focal_element=focal_element,
                action_title=action_title,
                deemphasize=deemphasize,
                annotations=annotations or [],
                justification=justification,
                chart_id=chart_id,
            )
            snap = storyteller_state.record_visual_spec(spec)
            return _ok(snap, f"Visual spec recorded for scene {scene_index}.")
        except Exception as exc:
            return _err(exc)

    # ── Writing phase ───────────────────────────────────────────────

    @mcp.tool()
    def storyteller_record_final_story(
        title: str,
        content_markdown: str,
    ) -> CallToolResult:
        """Record the assembled final story (title + markdown with chart placeholders).

        Requires every storyboard scene to have a recorded visual spec. The
        markdown should follow the same `{{chart:CHART_ID}}` and `{{grid:N}}`
        conventions as the standard `generate_report` tool, since
        `storyteller_generate_story_report` renders the final artifact
        through the same pipeline.

        Does NOT render the report. Call `storyteller_run_clarity_checks`
        next, then `storyteller_record_accessibility_pass`, then
        `storyteller_generate_story_report` to render.

        Args:
            title: Story title.
            content_markdown: Markdown body with chart placeholders and
                grid directives.
        """
        try:
            snap = storyteller_state.record_final_story(title, content_markdown)
            return _ok(snap, "Final story recorded.")
        except Exception as exc:
            return _err(exc)

    # ── Critique phase ──────────────────────────────────────────────

    @mcp.tool()
    def storyteller_run_clarity_checks(
        checks: list[dict[str, Any]],
        assumptions_surfaced: list[str] | None = None,
        weak_evidence: list[str] | None = None,
        alternative_interpretations: list[str] | None = None,
    ) -> CallToolResult:
        """Record the clarity review report from the Critic Agent.

        The Critic has already read the final story adversarially and run
        the four clarity tests (title-only readthrough, per-scene
        reinforcement, reverse storyboard, fresh-eye simulation) plus the
        four audits (emphasis alignment, chart-type audit, action-title
        audit, assumption surfacing).

        The tool computes `ready_for_handoff` (True iff every check passes)
        and `blocking_issues` (the failing-check findings) for the
        orchestrator to use when looping back.

        Args:
            checks: List of dicts with keys `test` (one of the clarity test
                names), `passed` (bool), and optional `finding`, `fix`.
            assumptions_surfaced: Assumptions the Critic wants on record.
            weak_evidence: Evidence the Critic judges weak.
            alternative_interpretations: Other readings the Critic noted.
        """
        try:
            parsed = [
                ClarityCheck(
                    test=c["test"],
                    passed=bool(c["passed"]),
                    finding=c.get("finding", ""),
                    fix=c.get("fix", ""),
                )
                for c in checks
            ]
            blocking = [
                f"{c.test}: {c.finding or 'failed'}"
                for c in parsed
                if not c.passed
            ]
            report = ReviewReport(
                checks=parsed,
                assumptions_surfaced=assumptions_surfaced or [],
                weak_evidence=weak_evidence or [],
                alternative_interpretations=alternative_interpretations or [],
                ready_for_handoff=len(blocking) == 0,
                blocking_issues=blocking,
            )
            snap = storyteller_state.record_review(report)
            heading = (
                "Clarity review passed."
                if report.ready_for_handoff
                else f"Clarity review failed: {len(blocking)} blocking issue(s)."
            )
            return _ok(snap, heading + "\n\n" + report.summarize())
        except Exception as exc:
            return _err(exc)

    # ── Accessibility phase ─────────────────────────────────────────

    @mcp.tool()
    def storyteller_record_accessibility_pass(
        passed: bool,
        notes: str = "",
    ) -> CallToolResult:
        """Record the accessibility and tone review outcome.

        Hard failures (colorblind-hostile encoding, unreadable contrast,
        missing required titles, illegible typography) MUST set `passed`
        to False and provide notes. Soft failures (language complexity,
        tone drift, whitespace abuse) may set `passed` to True with notes.

        Args:
            passed: True if no hard failures. False blocks handoff.
            notes: Specific issues found. Required when passed=False.
        """
        try:
            snap = storyteller_state.record_accessibility_pass(passed, notes)
            heading = (
                "Accessibility passed."
                if passed
                else f"Accessibility failed: {notes or 'see notes'}"
            )
            return _ok(snap, heading)
        except Exception as exc:
            return _err(exc)

    # ── Handoff ─────────────────────────────────────────────────────

    @mcp.tool()
    def storyteller_generate_story_report(
        style: str = "research",
    ) -> CallToolResult:
        """Render the final story as an interactive report.

        Only runs after every upstream gate has passed: context brief, big
        idea, storyboard, visual specs for every scene, final story, clarity
        review (ready_for_handoff=True), and accessibility pass.

        Wraps the existing `create_report_artifact` used by the standard
        `generate_report` tool, but bypasses the standard-mode quality gate
        since the storyteller has its own gates upstream. The standard
        session state is NOT reset, so users can continue exploring after
        the story is rendered.

        Args:
            style: Layout style.
                "research" (default) — long-form Anthropic-style essay
                    layout. Best when the mechanism is memo / decision brief.
                "scrollytelling" — marketing / case-study / growth-pitch
                    layout with sticky visuals, stepped charts, and
                    progressive bullet reveals. Best when the mechanism is
                    pitch / customer-story / investor-update.
                "dashboard" — standard `generate_report` layout (back-compat).
        """
        # Import inside the function to avoid a circular import at module load.
        from cerebro_mcp.tools.visualization import create_report_artifact

        try:
            storyteller_state.require_ready_for_handoff()
            snap = storyteller_state.snapshot()
            assert snap.context_brief is not None  # checked by require_ready_for_handoff

            normalized_style = (style or "research").strip().lower()
            if normalized_style not in {"research", "dashboard", "scrollytelling"}:
                raise ValueError(
                    f"Unknown style '{style}'. "
                    "Use 'research', 'scrollytelling', or 'dashboard'."
                )

            extra_kwargs: dict[str, Any] = {}
            if normalized_style == "research":
                extra_kwargs["presentation_mode"] = "research"
                extra_kwargs["research_metadata"] = (
                    _research_metadata_from_snapshot(snap)
                )
            elif normalized_style == "scrollytelling":
                extra_kwargs["presentation_mode"] = "scrollytelling"
                extra_kwargs["case_study_metadata"] = (
                    _case_study_metadata_from_snapshot(snap)
                )

            report = create_report_artifact(
                title=storyteller_state.final_story_title,
                content_markdown=storyteller_state.final_story_markdown,
                enforce_quality_gate=False,  # storyteller has its own gates
                reset_session_state=False,   # keep standard state for the user
                **extra_kwargs,
            )
            # End the session after a successful handoff.
            storyteller_state.end_session()
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            f"Story report generated: "
                            f"{report.get('structured', {}).get('title', '')}\n"
                            f"Report ID: {report.get('report_id', '')[:8]}"
                        ),
                    ),
                ],
                structuredContent=report.get("structured"),
            )
        except Exception as exc:
            return _err(exc)
