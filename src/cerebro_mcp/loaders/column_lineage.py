"""On-demand column-level lineage via sqlglot.

dbt's ``manifest.json`` carries model-to-model edges but no column-to-column
edges. We derive them lazily by parsing each model's SQL with sqlglot
(ClickHouse dialect) and resolving the source columns a target column depends
on, mapping the referenced relations back to dbt model names.

We prefer a model's ``compiled_sql`` (pure SQL). A ``dbt parse``-only manifest,
however, carries ``raw_sql`` with Jinja (``{{ ref() }}``/``{{ source() }}``)
and an *empty* ``compiled_sql`` — the common case for the gnosis_dbt manifest.
For those we render the Jinja ourselves (best effort): drop ``config`` blocks
and ``{% %}`` statements, and rewrite ``ref``/``source``/``this`` to their
physical ``schema.relation``. The rewritten SQL parses cleanly for ordinary
``SELECT ... FROM {{ ref(...) }}`` models; anything with leftover macros falls
back gracefully.

This is intentionally *on demand* — we never precompute the full column graph
(too expensive for ~1000 models). Heavy macro-generated SQL can defeat the
parser; in that case we degrade gracefully to a model-level edge plus a
warning rather than failing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from cerebro_mcp.loaders.manifest import manifest

logger = logging.getLogger(__name__)

try:  # sqlglot is a hard dependency, but guard so import never breaks the server
    import sqlglot
    from sqlglot import exp
    from sqlglot.lineage import lineage as _sqlglot_lineage

    _SQLGLOT_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only if dep missing
    _SQLGLOT_AVAILABLE = False

_DIALECT = "clickhouse"


def _build_schema_map() -> dict[str, Any]:
    """Build a sqlglot MappingSchema-compatible dict from manifest columns.

    Shape: ``{schema: {alias: {column: data_type}}}`` — a uniform 3-level
    nesting (db -> table -> column). sqlglot's ``MappingSchema`` rejects mixed
    nesting depths, so we must NOT also add bare ``{alias: {...}}`` entries.
    dbt compiles ``ref()`` calls to fully schema-qualified relations
    (``schema.alias``), so qualified resolution covers the real SQL.
    """
    schema_map: dict[str, Any] = {}
    for name in manifest.get_all_model_names():
        node = manifest.get_model(name) or {}
        cols = node.get("columns", {}) or {}
        if not cols:
            continue
        col_types = {
            col: (meta or {}).get("data_type") or "UNKNOWN"
            for col, meta in cols.items()
        }
        db = node.get("schema", "dbt") or "dbt"
        alias = node.get("alias", name) or name
        schema_map.setdefault(db, {})[alias] = dict(col_types)
    return schema_map


# --- Jinja-light rendering for parse-only manifests -----------------------
#
# A ``dbt parse`` manifest leaves ``compiled_sql`` empty, so we reconstruct
# enough SQL from ``raw_sql`` to drive sqlglot. We are deliberately narrow:
# only ``config``/``{% %}`` removal and ``ref``/``source``/``this`` rewriting.
# Any other macro that survives will make sqlglot fail, which the caller
# already handles via graceful model-level fallback.

_CONFIG_RE = re.compile(r"\{\{\s*config\s*\(.*?\)\s*\}\}", re.DOTALL)
_STMT_RE = re.compile(r"\{%.*?%\}", re.DOTALL)
_REF_RE = re.compile(r"\{\{\s*ref\s*\((.*?)\)\s*\}\}", re.DOTALL)
_SOURCE_RE = re.compile(r"\{\{\s*source\s*\((.*?)\)\s*\}\}", re.DOTALL)
_THIS_RE = re.compile(r"\{\{\s*this\s*\}\}")
_QUOTED_RE = re.compile(r"""['"]([^'"]+)['"]""")


def _build_source_map() -> dict[tuple[str, str], str]:
    """Map ``(source_name, table_name)`` -> ``schema.identifier``."""
    out: dict[tuple[str, str], str] = {}
    for node in getattr(manifest, "_sources", {}).values() or {}:
        sn = node.get("source_name") or ""
        nm = node.get("name") or ""
        if not sn or not nm:
            continue
        schema = node.get("schema") or "dbt"
        ident = node.get("identifier") or nm
        out[(sn, nm)] = f"{schema}.{ident}"
    return out


def _relation_for_model(model_name: str) -> str:
    node = manifest.get_model(model_name) or {}
    schema = node.get("schema", "dbt") or "dbt"
    alias = node.get("alias", model_name) or model_name
    return f"{schema}.{alias}"


def _render_raw_sql(model_name: str, raw: str) -> str:
    """Best-effort Jinja -> SQL so sqlglot can parse a parse-only manifest."""
    src_map = _build_source_map()
    sql = _CONFIG_RE.sub(" ", raw)
    sql = _STMT_RE.sub(" ", sql)
    sql = _THIS_RE.sub(_relation_for_model(model_name), sql)

    def _ref(match: re.Match) -> str:
        args = _QUOTED_RE.findall(match.group(1))
        return _relation_for_model(args[-1]) if args else match.group(0)

    def _source(match: re.Match) -> str:
        args = _QUOTED_RE.findall(match.group(1))
        if len(args) < 2:
            return match.group(0)
        return src_map.get((args[0], args[1]), match.group(0))

    sql = _REF_RE.sub(_ref, sql)
    sql = _SOURCE_RE.sub(_source, sql)
    return sql


def _resolve_model_sql(node_details: Optional[dict[str, Any]], model_name: str) -> str:
    """Return parseable SQL: prefer compiled, else render raw Jinja."""
    details = node_details or {}
    compiled = (details.get("compiled_sql") or "").strip()
    if compiled:
        return compiled
    raw = details.get("raw_sql") or ""
    if not raw.strip():
        return ""
    return _render_raw_sql(model_name, raw)


def _leaf_nodes(node: Any) -> list[Any]:
    """Collect leaf nodes (source columns, no further downstream) of a lineage tree."""
    leaves: list[Any] = []
    seen: set[int] = set()
    stack = list(getattr(node, "downstream", []) or [])
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        children = list(getattr(cur, "downstream", []) or [])
        if children:
            stack.extend(children)
        else:
            leaves.append(cur)
    return leaves


def _resolve_table_and_column(node: Any) -> tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of (relation_ref, column) from a lineage leaf node."""
    column: Optional[str] = None
    table_ref: Optional[str] = None

    name = getattr(node, "name", "") or ""
    if "." in name:
        prefix, column = name.rsplit(".", 1)
        table_ref = prefix
    else:
        column = name or None

    # A Table source expression is the most reliable relation hint.
    source = getattr(node, "source", None)
    if source is not None and isinstance(source, exp.Table):
        parts = [p.name for p in (source.args.get("db"), source.this) if p]
        if parts:
            table_ref = ".".join(parts)
    return table_ref, column


def _model_level_fallback(
    model_name: str, direction: str, warning: str
) -> dict[str, Any]:
    """Degrade to manifest model-level edges when column parsing is unavailable."""
    lineage_res = manifest.get_lineage(model_name, direction=direction, depth=1)
    edges: list[dict[str, Any]] = []
    for key, neighbors in (
        ("upstream", lineage_res.get("upstream", [])),
        ("downstream", lineage_res.get("downstream", [])),
    ):
        for nb in neighbors:
            other = nb.get("name", "")
            if not other:
                continue
            if key == "upstream":
                src, dst = other, model_name
            else:
                src, dst = model_name, other
            edges.append(
                {
                    "id": f"{src}::*->{dst}::*",
                    "source_model": src,
                    "source_column": None,
                    "target_model": dst,
                    "target_column": None,
                    "level": "model",
                }
            )
    return {
        "model": model_name,
        "direction": direction,
        "level": "model",
        "edges": edges,
        "warnings": [warning],
    }


def get_column_lineage(
    model_name: str,
    column: str,
    direction: str = "upstream",
    depth: int = 1,
) -> dict[str, Any]:
    """Trace column-level lineage for ``model_name.column``.

    Args:
        model_name: dbt model short name.
        column: Column within the model to trace.
        direction: "upstream" (default). Downstream column lineage requires
            parsing every consumer and falls back to model-level edges.
        depth: Upstream hops to follow (default 1).

    Returns:
        ``{model, column, direction, level, edges[], warnings[]}`` where each
        edge is ``{id, source_model, source_column, target_model,
        target_column, level}``. ``level`` is "column" or "model" (fallback).
    """
    if not manifest.is_loaded:
        return {
            "model": model_name,
            "column": column,
            "direction": direction,
            "level": "model",
            "edges": [],
            "warnings": ["dbt manifest not loaded; column lineage unavailable"],
        }

    details = manifest.get_model_details(model_name)
    if details is None:
        return {
            "model": model_name,
            "column": column,
            "direction": direction,
            "level": "model",
            "edges": [],
            "warnings": [f"Model '{model_name}' not found"],
        }

    # Catch a wrong/typo'd column up front so the user gets a precise message
    # instead of a misleading "could not be parsed" fallback. Only enforced
    # when the model actually declares columns in the manifest.
    model_cols = details.get("columns", {}) or {}
    if model_cols and column not in model_cols:
        available = ", ".join(sorted(model_cols)[:20])
        return {
            "model": model_name,
            "column": column,
            "direction": direction,
            "level": "model",
            "edges": [],
            "warnings": [
                f"Column '{column}' not found in model '{model_name}'. "
                f"Available columns: {available}"
            ],
        }

    # Downstream column lineage is not derivable from a single model's SQL;
    # fall back to model-level consumers.
    if direction == "downstream":
        return _model_level_fallback(
            model_name,
            "downstream",
            "Downstream column lineage is approximate; showing model-level edges.",
        )

    if not _SQLGLOT_AVAILABLE:
        return _model_level_fallback(
            model_name, "upstream", "sqlglot unavailable; showing model-level edges."
        )

    schema_map = _build_schema_map()
    edges: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_edges: set[str] = set()
    depth = max(1, int(depth))

    # BFS over (model, column) upstream.
    visited: set[tuple[str, str]] = set()
    stack: list[tuple[str, str, int]] = [(model_name, column, 0)]
    parsed_any = False

    while stack:
        mname, col, d = stack.pop()
        if (mname, col) in visited or d >= depth:
            continue
        visited.add((mname, col))

        node_details = manifest.get_model_details(mname)
        sql = _resolve_model_sql(node_details, mname)
        if not sql:
            continue

        try:
            root = _sqlglot_lineage(col, sql, schema=schema_map, dialect=_DIALECT)
            parsed_any = True
        except Exception as err:  # noqa: BLE001 - parser is best-effort
            logger.debug("sqlglot lineage failed for %s.%s: %s", mname, col, err)
            warnings.append(
                f"Could not parse column lineage for {mname}.{col}; "
                "skipped (model SQL may use heavy macros)."
            )
            continue

        for leaf in _leaf_nodes(root):
            table_ref, leaf_col = _resolve_table_and_column(leaf)
            if not table_ref or not leaf_col:
                continue
            upstream_model = manifest.get_model_by_table(table_ref)
            if not upstream_model or upstream_model == mname:
                continue
            edge_id = f"{upstream_model}::{leaf_col}->{mname}::{col}"
            if edge_id in seen_edges:
                continue
            seen_edges.add(edge_id)
            edges.append(
                {
                    "id": edge_id,
                    "source_model": upstream_model,
                    "source_column": leaf_col,
                    "target_model": mname,
                    "target_column": col,
                    "level": "column",
                }
            )
            stack.append((upstream_model, leaf_col, d + 1))

    if not edges and not parsed_any:
        return _model_level_fallback(
            model_name,
            "upstream",
            "Column lineage could not be parsed; showing model-level edges.",
        )

    return {
        "model": model_name,
        "column": column,
        "direction": "upstream",
        "level": "column",
        "edges": edges,
        "warnings": warnings,
    }
