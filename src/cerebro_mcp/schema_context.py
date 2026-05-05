"""Column-scoped schema context builder.

Wide dbt tables (100+ columns) blow up LLM context windows when injected
verbatim into SQL-author prompts. This module produces a *scoped* schema
block per model that:

1. Injects every column for narrow tables (≤ `FULL_SCHEMA_THRESHOLD`).
2. For wide tables, BM25-ranks columns against the user's question and keeps
   the top-K, **plus** keys/dates/partition columns regardless of score so
   that JOINs and time filters always work.
3. Tells the LLM, via a comment, that the omission is intentional and that
   it can call `get_relevant_columns` (the MCP tool defined in
   `tools/dbt.py`) to request additional columns by name.

Used by:
- The `get_relevant_columns` MCP tool in `tools/dbt.py` (returns the scoped
  block directly to an analyst agent).
- Any future prompt-assembler in `semantic_sql_compiler.py` or
  `tools/research.py` that wants to give an LLM a model schema.

Determinism: the helper is pure-Python and side-effect-free. Same inputs ->
same output, important when this is called from a worker pool in Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from cerebro_mcp.config import settings

# Names that MUST stay in scope regardless of BM25 score. These are the
# typical join keys / partition columns / time grains in this codebase.
# Anything matching is kept; matching is by exact lowercase name.
_ALWAYS_KEEP_NAMES: frozenset[str] = frozenset(
    {
        "date",
        "day",
        "week",
        "month",
        "hour",
        "ts",
        "timestamp",
        "block_timestamp",
        "block_number",
        "tx_hash",
        "transaction_hash",
        "address",
        "from_address",
        "to_address",
        "token_address",
        "contract_address",
        "chain",
        "chain_id",
        "token_symbol",
        "validator_index",
        "id",
        "uuid",
    }
)


@dataclass(frozen=True)
class ScopedSchema:
    """Result of `build_scoped_schema_block`.

    `block` is the markdown-ready string to inject. `kept_columns` and
    `total_columns` let the caller decide whether to tag the model as
    "fully injected" vs "scoped" in higher-level reporting.
    """

    block: str
    kept_columns: list[str]
    total_columns: int
    was_scoped: bool


def _column_iter(model_columns: dict[str, dict]) -> list[tuple[str, str, str]]:
    """Normalize the manifest column dict to (name, type, description)."""
    out: list[tuple[str, str, str]] = []
    for name, meta in (model_columns or {}).items():
        dtype = (meta or {}).get("data_type", "") or "?"
        desc = (meta or {}).get("description", "") or ""
        out.append((name, dtype, desc))
    return out


def build_scoped_schema_block(
    model_name: str,
    model_columns: dict[str, dict],
    query: str,
    *,
    top_columns_for_model,
    full_schema_threshold: int | None = None,
    top_k: int | None = None,
) -> ScopedSchema:
    """Build a markdown schema block for `model_name` scoped to `query`.

    Args:
        model_name: dbt model short name (used as the section heading).
        model_columns: the `columns` dict from `manifest.get_model_details`.
        query: the user's free-text question used to rank columns by BM25.
        top_columns_for_model: callable `(model, query, top_k) -> list[str]`.
            Pass `manifest.top_columns_for_model` in normal use; tests can
            substitute a stub.
        full_schema_threshold: tables with ≤ this many columns get the full
            schema. Defaults to `settings.SQL_COMPILER_FULL_SCHEMA_THRESHOLD`
            (typically 30).
        top_k: max columns to inject for wide tables, before always-keep
            additions. Defaults to `settings.SQL_COMPILER_TOP_COLUMNS`
            (typically 20).

    Returns:
        `ScopedSchema` with the markdown block + bookkeeping. The block is
        always non-empty (at minimum it contains the model heading).
    """
    threshold = (
        full_schema_threshold
        if full_schema_threshold is not None
        else getattr(settings, "SQL_COMPILER_FULL_SCHEMA_THRESHOLD", 30)
    )
    k = top_k if top_k is not None else getattr(settings, "SQL_COMPILER_TOP_COLUMNS", 20)

    columns = _column_iter(model_columns)
    total = len(columns)

    # Narrow tables: inject everything.
    if total <= threshold:
        kept = [name for name, _, _ in columns]
        body = "\n".join(
            f"  - `{name}`: {dtype}  -- {desc}" for name, dtype, desc in columns
        )
        block = f"## {model_name}\n{body}" if body else f"## {model_name}\n  (no columns documented)"
        return ScopedSchema(
            block=block, kept_columns=kept, total_columns=total, was_scoped=False
        )

    # Wide tables: BM25 + always-keep.
    bm25_top = set(top_columns_for_model(model_name, query, k) or [])
    always_keep = {
        name for name, _, _ in columns if name.lower() in _ALWAYS_KEEP_NAMES
    }
    keep = bm25_top | always_keep

    # If the keep set is anaemic (BM25 returned nothing, or only a couple of
    # weak hits and few always-keep names matched), pad with the first K
    # columns so the LLM still gets a usable schema. Otherwise wide
    # tables with off-topic queries can yield a 4-of-85 block which is too
    # narrow to be useful.
    if len(keep) < k:
        keep |= {name for name, _, _ in columns[: max(k, len(keep) + 1)]}

    kept_columns_in_order = [name for name, _, _ in columns if name in keep]
    body = "\n".join(
        f"  - `{name}`: {dtype}  -- {desc}"
        for name, dtype, desc in columns
        if name in keep
    )
    omitted = total - len(kept_columns_in_order)
    block = (
        f"## {model_name}\n{body}\n"
        f"  -- ({omitted} more columns omitted; ask for them by name via "
        f"`get_relevant_columns(model_name, query=...)` if needed)"
    )
    return ScopedSchema(
        block=block,
        kept_columns=kept_columns_in_order,
        total_columns=total,
        was_scoped=True,
    )
