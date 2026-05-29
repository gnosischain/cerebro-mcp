"""Build compact "what this means" context blocks from in-process dbt/semantic
metadata, to make tool results self-explanatory.

Opt-in per call: callers pass `explain_context=True`, the assembly helpers in
`tool_output.py` (and the chart/report tools) invoke these functions, and the
returned markdown is appended to the result.

Everything here is defensive: any failure (manifest not loaded, unparseable
SQL, missing model) yields an empty string rather than raising, so enrichment
can never break a result. Output is bounded by an internal char budget so it
cannot dominate the response.
"""
from __future__ import annotations

import re

# Module-level char budget for an enrichment block. Kept small on purpose —
# this is supplementary context, not the payload.
_BLOCK_CHAR_BUDGET = 1200
_MAX_MODELS = 3
_MAX_COLS_PER_MODEL = 4

# Match the relation following FROM / JOIN. Captures an optional schema, the
# table name, allowing backticks/quotes. Skips opening parens (subqueries/CTEs).
_RELATION_RE = re.compile(
    r"\b(?:from|join)\s+(`?\"?[A-Za-z_][\w]*`?\"?(?:\.`?\"?[A-Za-z_][\w]*`?\"?)?)",
    re.IGNORECASE,
)


def _first_sentence(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    # Split on the first period that ends a sentence; keep it readable.
    head = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(head) > limit:
        head = head[: limit - 1].rstrip() + "…"
    return head


def _extract_relations(sql: str) -> list[str]:
    """Return distinct table relations referenced by FROM/JOIN in `sql`.

    CTE names and subqueries resolve to nothing in the manifest and are simply
    dropped downstream, so we don't need to special-case them here.
    """
    seen: list[str] = []
    for match in _RELATION_RE.finditer(sql or ""):
        rel = match.group(1).replace("`", "").replace('"', "").strip()
        if rel and rel.lower() not in {r.lower() for r in seen}:
            seen.append(rel)
    return seen


def build_sql_context_block(sql: str, shown_columns: list[str] | None = None) -> str:
    """Compact markdown explaining the dbt models behind a SQL result.

    Resolves FROM/JOIN relations to dbt models, then for each model emits a
    one-line rationale plus a few of the most relevant column descriptions
    (ranked against the columns actually shown). Returns "" if nothing resolves.
    """
    try:
        from cerebro_mcp.loaders.manifest import manifest

        relations = _extract_relations(sql)
        if not relations:
            return ""

        shown = [c for c in (shown_columns or []) if c]
        rank_query = " ".join(shown) if shown else ""

        resolved: list[str] = []
        for rel in relations:
            model_name = manifest.get_model_by_table(rel)
            if model_name and model_name not in resolved:
                resolved.append(model_name)
            if len(resolved) >= _MAX_MODELS:
                break

        if not resolved:
            return ""

        lines: list[str] = ["### What this shows"]
        for model_name in resolved:
            details = manifest.get_model_details(model_name) or {}
            desc = _first_sentence(details.get("description", ""))
            lines.append(f"- **{model_name}** — {desc}" if desc else f"- **{model_name}**")

            columns = details.get("columns", {}) or {}
            # Prefer columns that were actually shown; fall back to BM25 ranking.
            shown_lower = {c.lower() for c in shown}
            documented_shown = [
                c for c in columns if c.lower() in shown_lower and columns[c].get("description")
            ]
            if documented_shown:
                picks = documented_shown[:_MAX_COLS_PER_MODEL]
            else:
                ranked = manifest.top_columns_for_model(
                    model_name, rank_query, top_k=_MAX_COLS_PER_MODEL
                ) if rank_query else []
                picks = [c for c in ranked if columns.get(c, {}).get("description")][
                    :_MAX_COLS_PER_MODEL
                ]
            for col in picks:
                col_desc = _first_sentence(columns[col].get("description", ""), limit=140)
                if col_desc:
                    lines.append(f"  - `{col}`: {col_desc}")

        block = "\n".join(lines)
        if len(block) > _BLOCK_CHAR_BUDGET:
            block = block[:_BLOCK_CHAR_BUDGET].rstrip() + "\n  - …"
        # Only return if we produced more than the bare heading.
        return block if "\n" in block else ""
    except Exception:
        return ""


def build_metric_context_block(metric_names: list[str] | None = None) -> str:
    """Compact markdown explaining semantic metrics by name.

    Pulls label/description/root_model from the semantic snapshot. Returns "" if
    the registry is unavailable or no metric resolves.
    """
    try:
        if not metric_names:
            return ""
        from cerebro_mcp.loaders.semantic import semantic_runtime

        snapshot = semantic_runtime.snapshot
        metrics = getattr(snapshot, "metrics", {}) or {}
        if not metrics:
            return ""

        lines: list[str] = ["### What this measures"]
        for name in metric_names[:_MAX_MODELS]:
            meta = metrics.get(name)
            if not meta:
                continue
            label = meta.get("label") or name
            desc = _first_sentence(meta.get("description", ""))
            root = meta.get("root_model", "")
            suffix = f" (from `{root}`)" if root else ""
            lines.append(f"- **{label}** — {desc}{suffix}" if desc else f"- **{label}**{suffix}")

        block = "\n".join(lines)
        if len(block) > _BLOCK_CHAR_BUDGET:
            block = block[:_BLOCK_CHAR_BUDGET].rstrip() + "\n  - …"
        return block if "\n" in block else ""
    except Exception:
        return ""
