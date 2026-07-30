"""Engineering-knowledge tools for the two corpora this server can see.

TWO corpora, deliberately separate, because they answer different questions:

  - `search_dbt_knowledge` / `get_dbt_change_context` -> dbt-cerebro's lessons and
    per-model contracts, fetched as a REMOTE artifact. Use when changing a dbt
    MODEL.
  - `search_cerebro_knowledge` / `get_cerebro_change_context` -> THIS repo's own
    lessons, shipped as package data. Use when changing cerebro-mcp's own code.

Sending an agent to the wrong one is the failure this split exists to prevent: a
ClickHouse trap in a mini-app query plane is not a dbt model hazard, and until
these tools existed the repo could serve the former's lessons and none of its own.

Serves the agent-context artifact built in the dbt-cerebro repo (lesson
records with a status lifecycle + per-model resolved engineering contracts)
so any MCP agent can see a model's failure modes BEFORE changing, backfilling
or reviewing it. Read-only: these tools never trigger builds or warehouse
mutations.

Staleness: when the live manifest no longer matches the artifact's build
input, global lessons are still served (with a warning) but per-model
contract attachments are suppressed — a stale contract asserting the wrong
grain is worse than none.
"""

from __future__ import annotations

from typing import Optional

from cerebro_mcp.loaders.agent_context import agent_context
from cerebro_mcp.loaders.cerebro_lessons import cerebro_lessons
from cerebro_mcp.loaders.manifest import manifest

STALE_WARNING = (
    "NOTE: the agent-context artifact was built from a different manifest than "
    "the one currently loaded — model-specific contracts are suppressed until "
    "the artifact is rebuilt (dbt parse + build_agent_context.py). Lesson "
    "records below remain valid (they describe the repo, not one manifest)."
)


def _format_lesson(lesson: dict, full: bool = False) -> str:
    head = (
        f"### [{lesson.get('status', '?')}] {lesson.get('id', '?')}: "
        f"{lesson.get('title', '')}\n"
        f"- symptom: {lesson.get('symptom', '')}\n"
        f"- scope: {lesson.get('scope', '')}\n"
        f"- record: {lesson.get('path', '')} (last verified {lesson.get('last_verified', '?')})"
    )
    if not full:
        return head
    evidence = lesson.get("evidence") or []
    ev = "\n".join(f"  - {e}" for e in evidence)
    body = lesson.get("body", "")
    return f"{head}\n- evidence:\n{ev}\n\n{body}"


def _format_contract(name: str, entry: dict) -> str:
    c = entry.get("contract") or {}
    lines = [f"## {name}"]
    lines.append(f"path: {entry.get('path', '')}")
    mat = f"materialized: {entry.get('materialized')}"
    if entry.get("incremental_strategy"):
        mat += f" | strategy: {entry['incremental_strategy']}"
        if entry.get("strategy_expression"):
            mat += " (EXPRESSION - resolves per run vars; check the branch before running)"
    if entry.get("partition_by"):
        mat += f" | partition_by: {entry['partition_by']}"
    lines.append(mat)
    flags = []
    if entry.get("reads_this"):
        flags.append("CUMULATIVE (reads {{ this }} - backfill order matters)")
    if entry.get("has_meta_full_refresh"):
        flags.append("staged full_refresh")
    if entry.get("high_risk"):
        flags.append("high-risk class")
    if flags:
        lines.append("flags: " + "; ".join(flags))
    if c.get("grain"):
        lines.append(f"grain: {c['grain']}")
    if c.get("semantics"):
        lines.append(f"semantics: {c['semantics']}")
    if c.get("ground_truth"):
        lines.append(f"ground truth: {c['ground_truth']}")
    hazards = c.get("hazards") or []
    if hazards:
        lines.append("known hazards (docs/lessons/<id>.md in dbt-cerebro):")
        for h in hazards:
            lines.append(f"  - [{h.get('status')}] {h.get('id')}: {h.get('title')}")
    invariants = c.get("invariants") or []
    if invariants:
        lines.append("invariants:")
        for inv in invariants:
            if isinstance(inv, dict):
                lines.append(f"  - {inv.get('id', '')}: {inv.get('text', '')}")
            else:
                lines.append(f"  - {inv}")
    rules = c.get("rules") or []
    if rules:
        lines.append("rules:")
        for r in rules:
            text = r.get("text") if isinstance(r, dict) else str(r)
            lesson = f" [{r['lesson']}]" if isinstance(r, dict) and r.get("lesson") else ""
            lines.append(f"  - {' '.join(str(text).split())}{lesson}")
    if c.get("reprocess_runbook"):
        lines.append(f"reprocess: {c['reprocess_runbook']}")
    validation = c.get("validation") or []
    if validation:
        lines.append("validation:")
        for v in validation:
            lines.append(f"  - {v}")
    api_models = entry.get("downstream_api_models") or []
    api_count = entry.get("downstream_api_count", len(api_models))
    direct = entry.get("downstream_direct_count", entry.get("downstream_count", 0))
    lineage = f"downstream: {direct} direct child model(s)"
    if api_models:
        shown = ", ".join(api_models)
        more = f" (+{api_count - len(api_models)} more)" if api_count > len(api_models) else ""
        lineage += f"; api marts affected (transitive): {shown}{more}"
    elif api_count:
        lineage += f"; api marts affected (transitive): {api_count}"
    lines.append(lineage)
    return "\n".join(lines)


def _format_cerebro_lesson(lesson: dict, full: bool = False) -> str:
    head = (
        f"### [{lesson.get('status', '?')}] {lesson.get('id', '?')}: "
        f"{lesson.get('title', '')}\n"
        f"- layer: {lesson.get('layer', '?')}\n"
        f"- symptom: {lesson.get('symptom', '')}\n"
        f"- scope: {lesson.get('scope', '')}\n"
        f"- record: {lesson.get('path', '')} (last verified {lesson.get('last_verified', '?')})"
    )
    if not full:
        return head
    evidence = "\n".join(f"  - {e}" for e in (lesson.get("evidence") or []))
    return f"{head}\n- evidence:\n{evidence}\n\n{lesson.get('body', '')}"


def register_agent_knowledge_tools(mcp):
    @mcp.tool()
    def search_dbt_knowledge(query: str, model_name: Optional[str] = None, limit: int = 5) -> str:
        """Search the dbt repo's engineering knowledge: lesson records for known
        mistake classes (wipes, backfill ordering, watermark drops, ClickHouse
        gotchas) with symptoms, root causes and safe remediation.

        Args:
            query: Symptom or topic, e.g. "negative balances", "insert_overwrite wipe",
                   "model empty after refresh".
            model_name: Optional dbt model name — lessons in that model's blast
                        radius rank higher.
            limit: Max lessons returned (default 5; the top hit includes its full body).

        Returns:
            Ranked lesson records: status, symptom, scope, evidence, and (for the
            top hit) the full record with detection + safe remediation.
        """
        agent_context.maybe_refresh()
        if not agent_context.is_loaded:
            return (
                "Agent-context artifact not available"
                + (f" ({agent_context.last_error})" if agent_context.last_error else "")
                + ". Knowledge lives in dbt-cerebro under docs/lessons/ if you have the checkout."
            )
        results = agent_context.search(query, model_name=model_name, limit=limit)
        if not results:
            return (
                f"No lesson matches '{query}'. Try symptom words (wipe, duplicate, "
                "negative, empty, stale, OOM) or list the model via get_dbt_change_context."
            )
        parts = [f"# Lessons matching '{query}'\n"]
        for i, lesson in enumerate(results):
            parts.append(_format_lesson(lesson, full=(i == 0)))
            parts.append("")
        if len(results) > 1:
            parts.append(
                "(Only the top hit includes its full body — re-query with the exact "
                "lesson id to read another in full.)"
            )
        return "\n".join(parts)

    @mcp.tool()
    def get_dbt_change_context(models: str, task: str = "change", lineage_depth: int = 1) -> str:
        """Get the engineering change packet for dbt model(s) BEFORE changing,
        backfilling, or reviewing them: resolved contract (grain, invariants),
        known hazards with status, lineage impact, safe reprocess runbook, and
        validation commands. Call this first for any dbt model work.

        Args:
            models: Comma-separated dbt model names.
            task: One of change | backfill | review (tunes the guidance line).
            lineage_depth: Reserved; direct downstream impact is always included.

        Returns:
            One packet per model plus task guidance. Read-only — cannot trigger
            builds or warehouse mutations.
        """
        agent_context.maybe_refresh()
        if not agent_context.is_loaded:
            return (
                "Agent-context artifact not available"
                + (f" ({agent_context.last_error})" if agent_context.last_error else "")
                + "."
            )
        agent_context.change_packets += 1

        stale = agent_context.is_stale_for(manifest)
        parts = []
        if stale:
            agent_context.stale_serves += 1
            parts.append(STALE_WARNING)
            parts.append("")

        names = [m.strip() for m in models.split(",") if m.strip()]
        hazard_ids: set[str] = set()
        for name in names:
            entry = agent_context.get_model(name)
            if entry is None:
                parts.append(f"## {name}\nNOT FOUND in agent context (new/renamed model, "
                             "or excluded from the published artifact).")
            elif stale:
                parts.append(f"## {name}\n(contract suppressed — stale artifact; see note above)")
            else:
                parts.append(_format_contract(name, entry))
                for h in (entry.get("contract") or {}).get("hazards", []):
                    hazard_ids.add(h.get("id", ""))
            parts.append("")

        guidance = {
            "backfill": (
                "BACKFILL ORDER: models flagged CUMULATIVE need history backfilled "
                "chronologically BEFORE they advance. Pick the lever from dbt-cerebro "
                "AGENTS.md (gap_window_refresh.py for backfilled months in decode "
                "chains; staged refresh.py for full history; never the daily runner "
                "for history). Check target/refresh_state/ for pending runs first."
            ),
            "review": (
                "REVIEW: check the diff against each hazard above and the repo rules "
                "(strategy/partition grain, hook pairing, meta/tag contract, semantic "
                "authoring for renames)."
            ),
            "change": (
                "Before editing: respect the invariants and hazards above; run the "
                "listed validation before handing back."
            ),
        }
        parts.append(guidance.get(task, guidance["change"]))
        if hazard_ids and not stale:
            parts.append(
                "\nHazard details: search_dbt_knowledge(\"<lesson id>\") returns the "
                "full record (detection + safe remediation)."
            )
        return "\n".join(parts)

    @mcp.tool()
    def search_cerebro_knowledge(query: str, path: Optional[str] = None, limit: int = 5) -> str:
        """Search THIS repo's own lesson records — mistake classes cerebro-mcp has
        already paid for (ClickHouse traps in the query planes, mini-app bundle
        staleness, gates that silently stopped guarding, silent-empty-result SQL).

        For a dbt MODEL use get_dbt_change_context instead; that is a different
        corpus describing a different repo.

        Args:
            query: Symptom words, e.g. "returns zero rows no error",
                   "memory limit exceeded", "ui change not showing up".
            path: Optional repo-relative path — lessons that apply to that layer
                  rank higher.
            limit: Max lessons returned (default 5; the top hit includes its full body).

        Returns:
            Ranked records with status, layer, symptom, evidence and — for the top
            hit — root cause, forbidden action, detection and safe remediation.
        """
        results = cerebro_lessons.search(query, path=path, limit=limit)
        if not results:
            return (
                f"No cerebro-mcp lesson matches '{query}'. Try symptom words (empty, "
                "silently, memory, stale, nondeterministic, wrong column), or browse "
                "src/cerebro_mcp/prompts/lessons/INDEX.md. If this is a NEW mistake "
                "class, record it with the /incident workflow "
                "(docs/workflows/incident.md)."
            )
        parts = [f"# cerebro-mcp lessons matching '{query}'\n"]
        for i, lesson in enumerate(results):
            parts.append(_format_cerebro_lesson(lesson, full=(i == 0)))
            parts.append("")
        if len(results) > 1:
            parts.append(
                "(Only the top hit includes its full body — re-query with the exact "
                "lesson id to read another in full.)"
            )
        return "\n".join(parts)

    @mcp.tool()
    def get_cerebro_change_context(paths: str, task: str = "change") -> str:
        """Get the change packet for cerebro-mcp's OWN code BEFORE editing it: the
        rules and known hazards for that layer, which guides to read, and how to
        validate. Call this first when changing this repo.

        For a dbt model use get_dbt_change_context; this covers cerebro-mcp itself.

        Args:
            paths: Comma-separated repo-relative paths or directories, e.g.
                   "src/cerebro_mcp/tools/visualization/queries/cow/open_orders.sql".
            task: One of change | debug | review (tunes the guidance line).

        Returns:
            One packet per path: applicable rules, hazards (id + status + title),
            validation commands and guides. Read-only.
        """
        cerebro_lessons.change_packets += 1
        wanted = [p.strip() for p in (paths or "").split(",") if p.strip()]
        if not wanted:
            return "Pass at least one repo-relative path."
        blocks = []
        for path in wanted:
            resolved = cerebro_lessons.resolve(path)
            lines = [f"## {path}"]
            lines.append(
                "profiles: " + (", ".join(resolved["profiles"]) or "(global only)")
            )
            if resolved["guides"]:
                lines.append("read first: " + ", ".join(resolved["guides"]))
            if resolved["rules"]:
                lines.append("\nrules:")
                for rule in resolved["rules"]:
                    ref = f"  [lesson: {rule['lesson']}]" if rule.get("lesson") else ""
                    text = " ".join(str(rule.get("text", "")).split())
                    lines.append(f"- {text}{ref}")
            if resolved["hazards"]:
                lines.append("\nhazards:")
                for hid in resolved["hazards"]:
                    lesson = cerebro_lessons.get(hid) or {}
                    lines.append(
                        f"- [{lesson.get('status', '?')}] {hid}: {lesson.get('title', '')}"
                    )
                lines.append(
                    "  (full record: search_cerebro_knowledge(\"<lesson id>\"))"
                )
            if resolved["validation"]:
                lines.append("\nvalidate with:")
                lines.extend(f"- {v}" for v in resolved["validation"])
            blocks.append("\n".join(lines))

        guidance = {
            "change": (
                "Read the guides above, then check each hazard applies before you "
                "edit. Widen an existing guard rather than adding a parallel one."
            ),
            "debug": (
                "Match the symptom against the hazards above BEFORE forming a "
                "hypothesis — over half of these were originally misdiagnosed as "
                "something else."
            ),
            "review": (
                "Check the diff against each hazard, and whether a new mistake "
                "class needs recording (/incident)."
            ),
        }.get(task, "")
        out = "\n\n".join(blocks)
        return f"{out}\n\n---\n{guidance}" if guidance else out
