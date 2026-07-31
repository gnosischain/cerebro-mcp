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

import logging
import re
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.models.storyteller import (
    BigIdea,
    ClarityCheck,
    ContextBrief,
    ReviewReport,
    Storyboard,
    StoryboardScene,
    StorytellerSnapshot,
    VisualSpec,
)
from cerebro_mcp.storyteller.state import storyteller_state

logger = logging.getLogger(__name__)


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

    # ──────────────────────────────────────────────────────────────────
    # Phase 3 / Sprint 3 — observability layer.
    #
    # Storyteller has only one active session per process (the
    # `storyteller_state` singleton). We mint a session_id on
    # `start_session`, hold it in this closure, use it as the workflow_id
    # for every event we emit during the session, and clear it on
    # `end_session`. The state machine itself is unchanged — we just
    # record what's happening.
    # ──────────────────────────────────────────────────────────────────

    import uuid as _uuid
    from cerebro_mcp.storyteller.state import _PHASE_ORDER as _ST_PHASE_ORDER
    from cerebro_mcp.workflow.event_store_sync import (
        record_storyteller_big_idea_recorded,
        record_storyteller_context_brief_recorded,
        record_storyteller_final_story_recorded,
        record_storyteller_gate_failed,
        record_storyteller_handoff_completed,
        record_storyteller_phase_advanced,
        list_storyteller_sessions,
        load_latest_storyteller_snapshot,
        record_storyteller_session_started,
        record_storyteller_state_snapshot,
        record_storyteller_storyboard_recorded,
        record_storyteller_visual_spec_recorded,
    )

    # Closure-local mutable holder so the inner event helpers all see
    # the same active session_id without passing it explicitly.
    _session = {"id": None}

    def _persist_state() -> None:
        """Write the full state after a mutation so it survives a restart.

        Separate from `_emit_phase_event_if_changed`, which fires only on a
        phase CHANGE: recording visual spec 3 of 8 does not advance the phase,
        yet it is exactly the work that must not be lost. This runs after every
        successful mutation instead.

        Best-effort by construction — `record_storyteller_state_snapshot` goes
        through the event store's write deadline, so a wedged filesystem drops
        the snapshot rather than blocking the tool.
        """
        sid = _session["id"]
        if sid is None:
            return
        try:
            record_storyteller_state_snapshot(sid, storyteller_state.to_payload())
        except Exception:  # pragma: no cover - defensive
            logger.exception("storyteller state snapshot failed")

    def _emit_phase_event_if_changed(
        before: str, gate_name: str | None = None,
    ) -> None:
        """Compare phase before / after a state mutation; emit
        `phase_advanced` on a forward move, `gate_failed` on a rollback,
        nothing on a no-op (e.g. visual_spec adding one of N scenes
        without advancing the phase yet)."""
        sid = _session["id"]
        if sid is None:
            return
        after = storyteller_state.phase
        if after == before:
            return
        try:
            before_idx = _ST_PHASE_ORDER.index(before)
            after_idx = _ST_PHASE_ORDER.index(after)
        except ValueError:
            # Unknown phase — emit a phase_advanced anyway so the trail
            # captures the transition.
            record_storyteller_phase_advanced(sid, before, after)
            return
        if after_idx < before_idx:
            record_storyteller_gate_failed(
                sid,
                gate=gate_name or f"{before}_check",
                blocking_phase=after,
            )
        else:
            record_storyteller_phase_advanced(sid, before, after)

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
            # Sprint 3 — register a new workflow in the event log. Failures
            # in the event_store_sync helper are silenced; storyteller
            # operation must never fail because observability did.
            #
            # Hold the id in a local and pass THAT, rather than re-reading
            # `_session["id"]`: tool bodies run concurrently on worker threads
            # (runtime/offload.py), so a second start_session landing between
            # the write and the read would make this call register the other
            # session's id.
            session_id = _uuid.uuid4().hex[:16]
            _session["id"] = session_id
            record_storyteller_session_started(session_id)
            return _ok(snap, "Storyteller session started.")
        except Exception as exc:  # pragma: no cover - defensive
            return _err(exc)

    @mcp.tool()
    def storyteller_end_session() -> CallToolResult:
        """End the current storyteller session and clear all artifacts."""
        try:
            snap = storyteller_state.end_session()
            _session["id"] = None
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

    @mcp.tool()
    def storyteller_resume_session(session_id: str = "") -> CallToolResult:
        """Recover a storyteller session from durable state.

        Call with no argument to list recoverable sessions; call with a
        `session_id` to rehydrate one into the live session.

        Why this exists: storyteller state is an in-process singleton, so a
        restart or a stuck gate used to make finished work unreachable. A
        pipeline that had passed every gate — storyboard, visual specs, final
        story, clarity review — was stranded because one boolean could not be
        recorded, and restarting to clear it would have destroyed the rest.
        Artifacts are now snapshotted after every mutation; this is the read
        side of that.

        Args:
            session_id: The session to restore. Omit to list candidates.
        """
        try:
            if not session_id:
                sessions = list_storyteller_sessions()
                if not sessions:
                    return _ok(
                        storyteller_state.snapshot(),
                        "No recoverable storyteller sessions found.",
                    )
                lines = [
                    f"- `{s['session_id']}` ({s['status']})" for s in sessions
                ]
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=(
                            "Recoverable storyteller sessions, most recent "
                            "first. Re-run with `session_id` to restore:\n"
                            + "\n".join(lines)
                        ),
                    )],
                    structuredContent={
                        "sessions": sessions,
                        "count": len(sessions),
                    },
                )

            payload = load_latest_storyteller_snapshot(session_id)
            if payload is None:
                raise RuntimeError(
                    f"No durable snapshot for session '{session_id}'. Call "
                    "`storyteller_resume_session` with no argument to list "
                    "what is recoverable."
                )

            storyteller_state.restore_from_payload(payload)
            # Adopt the id, or subsequent mutations would snapshot to a NEW
            # workflow and silently fork the trail we just recovered from.
            _session["id"] = session_id
            snap = storyteller_state.snapshot()
            return _ok(
                snap,
                f"Restored storyteller session `{session_id}` at phase "
                f"'{snap.phase}'.",
            )
        except Exception as exc:
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
            before = storyteller_state.phase
            snap = storyteller_state.record_context_brief(brief)
            _emit_phase_event_if_changed(before)
            _persist_state()
            # Step 1 content event — capture the brief's key signals so
            # resume sees audience / mechanism / required_action.
            sid = _session["id"]
            if sid is not None:
                record_storyteller_context_brief_recorded(
                    sid,
                    audience=audience,
                    mechanism=mechanism,
                    required_action=required_action,
                )
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
            before = storyteller_state.phase
            snap = storyteller_state.record_big_idea(idea)
            _emit_phase_event_if_changed(before)
            _persist_state()
            sid = _session["id"]
            if sid is not None:
                record_storyteller_big_idea_recorded(
                    sid, sentence=sentence, stakes=stakes,
                )
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
            before = storyteller_state.phase
            snap = storyteller_state.record_storyboard(storyboard)
            _emit_phase_event_if_changed(before)
            _persist_state()
            sid = _session["id"]
            if sid is not None:
                record_storyteller_storyboard_recorded(
                    sid,
                    scene_count=len(parsed_scenes),
                    narrative_order=narrative_order,
                    rationale=rationale,
                )
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
            before = storyteller_state.phase
            snap = storyteller_state.record_visual_spec(spec)
            # Visual specs are incremental: phase only advances once all
            # scenes are filled. Helper no-ops on equal phases.
            _emit_phase_event_if_changed(before)
            _persist_state()
            sid = _session["id"]
            if sid is not None:
                record_storyteller_visual_spec_recorded(
                    sid,
                    scene_index=scene_index,
                    chart_family=chart_family,
                    relationship=relationship,
                    action_title=action_title,
                )
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
            before = storyteller_state.phase
            snap = storyteller_state.record_final_story(title, content_markdown)
            _emit_phase_event_if_changed(before)
            _persist_state()
            sid = _session["id"]
            if sid is not None:
                record_storyteller_final_story_recorded(
                    sid, title=title, content_length=len(content_markdown or ""),
                )
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
            before = storyteller_state.phase
            snap = storyteller_state.record_review(report)
            # Clarity review: PASS advances to `accessibility`; FAIL rolls
            # back to the earliest failing phase. The helper emits the
            # appropriate phase_advanced or gate_failed event.
            _emit_phase_event_if_changed(before, gate_name="clarity_review")
            _persist_state()
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
            before = storyteller_state.phase
            snap = storyteller_state.record_accessibility_pass(passed, notes)
            # Accessibility: PASS → handoff, FAIL → back to write.
            _emit_phase_event_if_changed(before, gate_name="accessibility")
            _persist_state()
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
        from cerebro_mcp.tools.visualization.charts import create_report_artifact

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

            # Default explicitly rather than letting `create_report_artifact`
            # derive it from the process-global `semantic_mode_last`: that
            # singleton is shared by every concurrent client, so a chart-mode
            # request in another conversation could file this finished story as
            # a throwaway "visual_answer". The research and scrollytelling
            # branches below override it.
            extra_kwargs: dict[str, Any] = {"presentation_mode": "report"}
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
            # Sprint 3 — terminal event. Marks the storyteller workflow
            # complete in the event log; the resume handler will see
            # `handoff_completed` and return action=complete.
            sid = _session["id"]
            if sid is not None:
                record_storyteller_handoff_completed(
                    sid,
                    report_id=str(report.get("report_id", "")),
                    style=normalized_style,
                )
                _session["id"] = None
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
