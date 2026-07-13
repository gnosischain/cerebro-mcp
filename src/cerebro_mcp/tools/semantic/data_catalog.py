"""Data Catalog mini app — an OpenMetadata-style, search-first catalog.

This is the *catalog* surface of the semantic layer: a global search over every
entity the registry knows about (dbt models, semantic metrics, knowledge-graph
glossary terms) plus per-entity profile pages (schema, properties, lineage,
metrics, relationships). It deliberately mirrors OpenMetadata's information
architecture — search → entity profile with tabs → glossary — rather than the
graph-first explorer it supersedes.

The catalog is built on the semantic *registry snapshot* (the source of truth:
~1.1k models + ~1.4k metrics, each carrying fqn/module/tier/owner/tags/columns/
lineage/metric_names). A cached BM25 index over that snapshot powers unified,
ranked search across all entity types. The interactive Lineage tab is the only
place that reaches into the dbt ``manifest`` (for a bounded, depth-controlled
``get_subgraph``); everything else is registry-native.

Two structured tools are exposed both as MCP tools (agent-usable) and via the
web-app dispatch registry (browser/``--sse`` mode):

  * ``catalog_search``      — ``{hits[], facets{}, total}`` ranked + faceted.
  * ``get_catalog_entity``  — a full structured profile for one entity.

plus ``catalog_lineage`` (a plain web-only wrapper around the manifest subgraph)
and ``open_data_catalog`` (the mini-app open tool).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.config import settings
from cerebro_mcp.loaders.manifest import manifest
from cerebro_mcp.semantic.bm25 import BM25Doc, BM25Index
from cerebro_mcp.semantic.search import ModelSearchIndex
from cerebro_mcp.semantic.search import tokenize as search_tokenize
from cerebro_mcp.semantic.graph_profiles import current_snapshot
from cerebro_mcp.tools.visualization import web_apps
from cerebro_mcp.tools.visualization.mini_apps import run_structured_query

logger = logging.getLogger(__name__)

DATA_CATALOG_APP_ID = "data_catalog"
DATA_CATALOG_URI = "ui://cerebro/data_catalog"
DEFAULT_TITLE = "Data Catalog"

# Entity-profile lineage cap — mirrors get_model_subgraph so a hub model's
# depth-3 sweep can't blow up the renderer.
MAX_LINEAGE_NODES = 300

ENTITY_TYPES = ("model", "metric", "glossary")

# Tier ordinal for sorting/browse ranking (higher = more trusted).
_TIER_ORDINAL = {"approved": 3, "candidate": 2, "docs_only": 1, "": 0}

_BUNDLED_HTML: str | None = None


def get_data_catalog_html() -> str:
    """Load the Vite-built single-file Data Catalog app from the static package."""
    global _BUNDLED_HTML
    if _BUNDLED_HTML is None:
        try:
            import importlib.resources

            _BUNDLED_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/data_catalog.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>data_catalog.html not built — run "
                "<code>make build-ui-data-catalog</code></div>"
                "</body></html>"
            )
    return _BUNDLED_HTML


# ---------------------------------------------------------------------------
# Catalog index — a cached, unified BM25 over the registry snapshot.
# ---------------------------------------------------------------------------


@dataclass
class _Hit:
    """Flat, JSON-able catalog hit (search result + facet source row)."""

    id: str
    type: str
    name: str
    title: str
    fqn: str
    description: str
    module: str
    tier: str
    owner: str
    tags: list[str] = field(default_factory=list)
    score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "title": self.title,
            "fqn": self.fqn,
            "description": self.description,
            "module": self.module,
            "tier": self.tier,
            "owner": self.owner,
            "tags": list(self.tags),
            "score": self.score,
        }


class _CatalogIndex:
    """Unified BM25 index + hit metadata over one registry snapshot."""

    def __init__(self, snap: Any) -> None:
        self.hits: dict[str, _Hit] = {}
        docs: list[BM25Doc] = []

        for name, model in (getattr(snap, "models", {}) or {}).items():
            tags = list(model.get("tags", []) or [])
            hit = _Hit(
                id=f"model:{name}",
                type="model",
                name=name,
                title=name,
                fqn=_fqn_str(model.get("fqn"), fallback=name),
                description=(model.get("description", "") or "").strip(),
                module=model.get("module", "") or "",
                tier=model.get("semantic_status", "") or "",
                owner=model.get("owner", "") or "",
                tags=tags,
            )
            self.hits[hit.id] = hit
            # NOTE: model docs are deliberately NOT added to the local BM25 —
            # models rank through the canonical, field-weighted
            # ModelSearchIndex (semantic/search.py) so every tool sees the
            # same model ranking (incl. column-name recall this thin blob
            # never had). Metrics + glossary stay on the local index below.

        self.model_search = ModelSearchIndex.for_snapshot(snap)

        for name, metric in (getattr(snap, "metrics", {}) or {}).items():
            module = metric.get("module", "") or ""
            label = metric.get("label", "") or name
            synonyms = list(metric.get("question_synonyms", []) or [])
            hit = _Hit(
                id=f"metric:{name}",
                type="metric",
                name=name,
                title=label,
                fqn=f"metric.{module}.{name}" if module else f"metric.{name}",
                description=(metric.get("description", "") or "").strip(),
                module=module,
                tier=metric.get("quality_tier", "") or "",
                owner="",
                tags=[],
            )
            self.hits[hit.id] = hit
            docs.append(
                BM25Doc(
                    model_name=hit.id,
                    text=" ".join(
                        [name, label, hit.description, module, " ".join(synonyms)]
                    ),
                )
            )

        for profile in getattr(snap, "graph_profiles", ()) or ():
            pid = profile.profile
            hit = _Hit(
                id=f"glossary:{pid}",
                type="glossary",
                name=pid,
                title=pid,
                fqn=f"glossary.{pid}",
                description=(profile.description or "").strip(),
                module=profile.module or "",
                tier=profile.quality_tier or "",
                owner="",
                tags=list(profile.question_synonyms or []),
            )
            self.hits[hit.id] = hit
            docs.append(
                BM25Doc(
                    model_name=hit.id,
                    text=" ".join(
                        [
                            pid,
                            hit.description,
                            profile.source_kind or "",
                            profile.target_kind or "",
                            " ".join(profile.question_synonyms or []),
                        ]
                    ),
                )
            )

        self.bm25 = BM25Index(docs)
        # Token-overlap floor for the metric/glossary leg (shared tokenizer):
        # rank_bm25's IDF degenerates in tiny corpora (0 at df=N/2, epsilon-
        # negative at df=N), and with model docs gone the local corpus can be
        # small in dev/test registries. Same blend as _FieldBM25.
        self.doc_tokens: dict[str, set[str]] = {
            d.model_name: set(search_tokenize(d.text)) for d in docs
        }


_INDEX_CACHE: dict[str, _CatalogIndex] = {}


def _catalog_index(snap: Any) -> _CatalogIndex:
    """Return a BM25 catalog index for ``snap``, rebuilt only on reload.

    Keyed on the snapshot's ``registry_hash`` (stable until a registry reload),
    falling back to object identity. A single-entry cache so it never grows.
    """
    key = getattr(snap, "registry_hash", "") or f"id:{id(snap)}"
    cached = _INDEX_CACHE.get(key)
    if cached is None:
        _INDEX_CACHE.clear()
        cached = _CatalogIndex(snap)
        _INDEX_CACHE[key] = cached
    return cached


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _fqn_str(fqn: Any, fallback: str) -> str:
    """Render a dbt fqn (list of path parts) as a dotted string."""
    if isinstance(fqn, (list, tuple)) and fqn:
        return ".".join(str(p) for p in fqn)
    if isinstance(fqn, str) and fqn:
        return fqn
    return fallback


def _facet_counts(hits: list[_Hit]) -> dict[str, dict[str, int]]:
    """Count hits by type / module / tier / tags for the faceted sidebar."""
    facets: dict[str, dict[str, int]] = {
        "type": {},
        "module": {},
        "tier": {},
        "tags": {},
    }
    for h in hits:
        facets["type"][h.type] = facets["type"].get(h.type, 0) + 1
        if h.module:
            facets["module"][h.module] = facets["module"].get(h.module, 0) + 1
        if h.tier:
            facets["tier"][h.tier] = facets["tier"].get(h.tier, 0) + 1
        for tag in h.tags:
            facets["tags"][tag] = facets["tags"].get(tag, 0) + 1
    return facets


def _columns_list(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a registry model's ``columns`` (dict) into a sorted list."""
    cols = model.get("columns") or {}
    out: list[dict[str, Any]] = []
    if isinstance(cols, dict):
        for col_name, meta in cols.items():
            meta = meta or {}
            out.append(
                {
                    "name": meta.get("name", col_name),
                    "data_type": meta.get("data_type", ""),
                    "description": (meta.get("description", "") or "").strip(),
                }
            )
    elif isinstance(cols, list):
        for meta in cols:
            meta = meta or {}
            out.append(
                {
                    "name": meta.get("name", ""),
                    "data_type": meta.get("data_type", ""),
                    "description": (meta.get("description", "") or "").strip(),
                }
            )
    return out


# ---------------------------------------------------------------------------
# Privacy / safety helpers for live-data tools
# ---------------------------------------------------------------------------

import re as _re

# Tags that mark a model whose ROWS must never be sampled, even though the model
# itself is listable (it survived the loader's hard internal-only filter). The
# `privacy:` prefix + the mixpanel growth tables carry per-user PII.
_NO_SAMPLE_TAGS = {"mixpanel_ga"}
_IDENT_RE = _re.compile(r"^[A-Za-z0-9_]+$")

# Per-SUBJECT identity columns: a model carrying one of these has row-level data
# tied to a specific account/wallet/person (e.g. GPay per-wallet USD spend) and
# is refused as a raw sample even when it isn't privacy-tagged. EXACT names only
# (not substrings) so public on-chain primitives — from_address/to_address/
# holder/contract_address — stay samplable.
_PII_SUBJECT_COLUMNS = {
    "wallet_address", "user_address", "account_address", "signer_address",
    "user_id", "account_id", "email", "username",
}


def _model_column_names(model: dict[str, Any]) -> set[str]:
    cols = model.get("columns") or {}
    names: set[str] = set()
    if isinstance(cols, dict):
        for cn, meta in cols.items():
            names.add(str((meta or {}).get("name", cn)).lower())
    elif isinstance(cols, list):
        for meta in cols:
            names.add(str((meta or {}).get("name", "")).lower())
    return names


def _is_sampleable(model: dict[str, Any]) -> bool:
    """True only if a model's raw rows are safe to surface in the Data tab.

    Internal-only models are already gone (loader filter); this is the second
    gate. It refuses: privacy-tagged-but-listable models (mixpanel, ``privacy:*``,
    meta ``exclude_from_api`` / ``privacy_tier``) AND any model carrying a
    per-subject identity column (``_PII_SUBJECT_COLUMNS``) — the latter closes
    per-wallet/per-user financial-behavior tables (e.g. GPay activity) that are
    not privacy-tagged. Metadata (search/schema/profile) stays visible; only the
    raw row sample + physical stats are gated.
    """
    tags = model.get("tags") or []
    if any(t in _NO_SAMPLE_TAGS or str(t).startswith("privacy:") for t in tags):
        return False
    for holder in (model.get("meta"), (model.get("semantic") or {}).get("meta")):
        if not isinstance(holder, dict):
            continue
        if holder.get("exclude_from_api") is True or holder.get("privacy_tier"):
            return False
        api = holder.get("api")
        if isinstance(api, dict) and api.get("exclude_from_api") is True:
            return False
    if _model_column_names(model) & _PII_SUBJECT_COLUMNS:
        return False
    return True


def _db_in_scope(db: str) -> bool:
    """Privacy backstop: a relation whose physical database is outside the
    connector read-allowlist is treated as restricted rather than leaking an
    allowlist error from the query layer (e.g. an *untagged* table that still
    physically lives in ``mixpanel_ga``). Fails closed."""
    try:
        return db in settings.ALLOWED_DATABASES
    except Exception:  # noqa: BLE001 — a gate must never crash the caller
        return False


_RESTRICTED_REASON = "unavailable — privacy-restricted model"


def _parse_relation(relation_name: str, fallback_name: str) -> tuple[str, str]:
    """Parse a dbt ``relation_name`` (``\\`db\\`.\\`tbl\\``` or ``db.tbl``) into
    ``(database, table)``, stripping backtick/double-quote quoting."""
    parts = [
        p.strip().strip("`").strip('"')
        for p in (relation_name or "").split(".")
        if p.strip()
    ]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if len(parts) == 1:
        return "dbt", parts[0]
    return "dbt", fallback_name


def _safe_ident(value: str) -> bool:
    return bool(_IDENT_RE.match(value or ""))


def _catalog_sample_impl(ch, name: str, limit: int = 20) -> dict[str, Any]:
    """Return up to ``limit`` live sample rows for a model — privacy-gated.

    Refuses (without ever building SQL) for non-sampleable models. Degrades to
    ``{available: false, reason}`` on any error so the UI never 500s.
    """
    snap = current_snapshot()
    if snap is None:
        return {"available": False, "reason": "semantic snapshot unavailable", "name": name}
    model = (getattr(snap, "models", {}) or {}).get(name)
    if model is None:
        return {"available": False, "reason": f"model '{name}' not found", "name": name}
    if not _is_sampleable(model):
        return {
            "available": False,
            "restricted": True,
            "reason": "sample unavailable — privacy-restricted model",
            "name": name,
        }
    db, table = _parse_relation(model.get("relation_name", ""), name)
    if not _db_in_scope(db):
        return {"available": False, "restricted": True, "reason": "sample " + _RESTRICTED_REASON, "name": name}
    if not (_safe_ident(db) and _safe_ident(table)):
        return {"available": False, "reason": "unsafe relation identifier", "name": name}
    capped = max(1, min(int(limit or 20), 50))
    sql = f"SELECT * FROM {db}.{table} LIMIT {capped}"
    try:
        res = run_structured_query(ch, sql, database=db, requested_max_rows=capped)
    except Exception as exc:  # noqa: BLE001 — surface as graceful payload
        # Don't leak raw ClickHouse engine errors (e.g. AggregateFunction-state
        # deserialization, allowlist messages) to the browser — log, show clean copy.
        logger.info("catalog_sample failed for %s: %s", name, exc)
        return {
            "available": False,
            "reason": "Live sample isn't available for this model (it may use a non-readable column type).",
            "name": name,
        }

    # ClickHouse returns Date/DateTime columns as epoch ints (days / seconds) and
    # run_structured_query only infers the Python type ("int"), so the UI can't
    # tell a date from a number. Format them here using the model's authoritative
    # dbt column types (handles Nullable(...)/LowCardinality(...) wrappers).
    dbt_types: dict[str, str] = {}
    cols_meta = model.get("columns") or {}
    if isinstance(cols_meta, dict):
        for cn, meta in cols_meta.items():
            dbt_types[(meta or {}).get("name", cn)] = (meta or {}).get("data_type", "")
    col_type_list = [dbt_types.get(c, "") for c in res.columns]
    out_rows = [
        [_format_sample_cell(v, col_type_list[i], res.columns[i]) for i, v in enumerate(row)]
        for row in res.rows
    ]
    return {
        "available": True,
        "name": name,
        "columns": list(res.columns),
        "column_types": col_type_list,  # dbt types (Date/UInt64/…), useful in the UI
        "rows": out_rows,
        "row_count": res.row_count,
        "truncated": res.row_count >= capped,
        "materialization": model.get("materialized", ""),
        "sql": sql,
    }


_DATE_NAME_HINTS = ("date", "day", "week", "month")
_TIME_NAME_SUFFIXES = ("_at", "_ts", "_time")


def _format_sample_cell(value: Any, dbt_type: str, col_name: str = "") -> Any:
    """Render epoch-int Date/DateTime cells as ISO strings.

    A column may be *typed* ``Date`` yet physically store epoch **seconds** (a
    common modelling quirk) — interpreting that as epoch-days overflows and used
    to leak the raw integer. So we decide the unit by MAGNITUDE, not by the type
    label: days < 1e5, seconds < 1e11, millis < 1e14. The dbt type / column name
    only decide *whether* a numeric column is temporal at all. Non-temporal,
    non-numeric, and bool values pass through unchanged.
    """
    dt = dbt_type or ""
    # Int256/UInt256 (and other wide ints) arrive from ClickHouse as Python bytes
    # — or, once stringified upstream, as a ``b'\x..'`` repr. Decode to the actual
    # integer (returned as a STRING so values past 2^53 survive JSON/JS intact)
    # instead of leaking a binary blob on the highest-value financial columns.
    raw_bytes: bytes | None = None
    if isinstance(value, (bytes, bytearray)):
        raw_bytes = bytes(value)
    elif isinstance(value, str) and value[:2] in ("b'", 'b"') and "\\x" in value:
        try:
            import ast as _ast

            decoded = _ast.literal_eval(value)
            if isinstance(decoded, (bytes, bytearray)):
                raw_bytes = bytes(decoded)
        except (ValueError, SyntaxError):
            raw_bytes = None
    if raw_bytes is not None:
        try:
            signed = "Int" in dt and "UInt" not in dt
            return str(int.from_bytes(raw_bytes, "little", signed=signed))
        except (ValueError, OverflowError):
            return f"binary ({len(raw_bytes)} bytes)"

    if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    name = (col_name or "").lower()
    typed_temporal = "Date" in dt  # Date, DateTime, DateTime64(...), Nullable(Date), …
    numeric_typed = any(k in dt for k in ("Int", "Float", "Decimal", "Bool"))  # "Int" also matches UInt
    # Name-based inference fires ONLY when the type doesn't say "number" — a
    # numeric COUNT column like active_addresses_week / volume_month must NOT be
    # reinterpreted as an epoch date (that produced bogus 1970 values).
    name_temporal = (not numeric_typed) and (
        name in _DATE_NAME_HINTS
        or name in ("timestamp", "datetime")
        or name.endswith(("_date", "_day", "_week", "_month"))
        or name.endswith(_TIME_NAME_SUFFIXES)
    )
    if not (typed_temporal or name_temporal):
        return value
    import datetime as _dtmod

    av = abs(value)
    try:
        if av < 100_000:  # epoch-days (~year 2243 max)
            return (_dtmod.date(1970, 1, 1) + _dtmod.timedelta(days=int(value))).isoformat()
        if av < 1e11:  # epoch-seconds
            return _dtmod.datetime.fromtimestamp(int(value), tz=_dtmod.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if av < 1e14:  # epoch-milliseconds
            return _dtmod.datetime.fromtimestamp(int(value) / 1000, tz=_dtmod.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return value
    return value


def _catalog_table_stats_impl(ch, name: str) -> dict[str, Any]:
    """Row count / on-disk size for a model's physical table.

    Uses ``execute_raw_cached`` on ``system.tables`` (the validated-query path
    forbids the SYSTEM keyword). Views have no parts → ``n/a (computed on read)``.
    """
    snap = current_snapshot()
    if snap is None:
        return {"available": False, "reason": "semantic snapshot unavailable", "name": name}
    model = (getattr(snap, "models", {}) or {}).get(name)
    if model is None:
        return {"available": False, "reason": f"model '{name}' not found", "name": name}
    materialization = model.get("materialized", "") or ""
    # Row count / on-disk size are themselves sensitive: gate stats with the
    # same privacy predicate as the row sample so a restricted model never
    # leaks volume or storage metadata.
    if not _is_sampleable(model):
        return {"available": False, "restricted": True, "reason": "stats " + _RESTRICTED_REASON, "name": name}
    db, table = _parse_relation(model.get("relation_name", ""), name)
    if not _db_in_scope(db):
        return {"available": False, "restricted": True, "reason": "stats " + _RESTRICTED_REASON, "name": name}
    if not (_safe_ident(db) and _safe_ident(table)):
        return {"available": False, "reason": "unsafe relation identifier", "name": name}
    if materialization == "view":
        return {
            "available": True, "name": name, "materialization": materialization,
            "is_view": True, "row_count": None, "size_bytes": None,
            "note": "view — rows computed on read",
        }
    sql = (
        "SELECT engine, total_rows, total_bytes FROM system.tables "
        "WHERE database = {db:String} AND name = {tbl:String}"
    )
    try:
        result = ch.execute_raw_cached(
            sql, "dbt", f"catalog_tblstats:{db}.{table}",
            parameters={"db": db, "tbl": table},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("catalog_table_stats failed for %s: %s", name, exc)
        return {"available": False, "reason": "Table stats unavailable for this model.", "name": name}
    rows = (result or {}).get("rows") or []
    if not rows:
        return {
            "available": True, "name": name, "materialization": materialization,
            "is_view": False, "row_count": None, "size_bytes": None,
            "note": "no table parts found",
        }
    engine = str(rows[0][0] or "")
    is_view = "View" in engine
    return {
        "available": True, "name": name, "materialization": materialization,
        "engine": engine, "is_view": is_view,
        "row_count": None if is_view else rows[0][1],
        "size_bytes": None if is_view else rows[0][2],
    }


def catalog_run_config(name: str = "") -> dict[str, Any]:
    """Model run configuration (materialization / incremental strategy / unique
    key / partition / on-schema-change), read from the dbt manifest node config.
    Registry has no config keys, so this is manifest-only (CH-free)."""
    if not manifest.is_loaded:
        return {"available": False, "reason": "manifest not loaded", "name": name}
    node = manifest.get_model(name)
    if not node:
        return {"available": False, "reason": f"model '{name}' not found", "name": name}
    cfg = node.get("config", {}) or {}
    meta = cfg.get("meta") or {}
    return {
        "available": True,
        "name": name,
        "materialization": cfg.get("materialized", ""),
        "incremental_strategy": cfg.get("incremental_strategy"),
        "unique_key": cfg.get("unique_key"),
        "partition_by": cfg.get("partition_by") or (meta.get("partition_by") if isinstance(meta, dict) else None),
        "on_schema_change": cfg.get("on_schema_change"),
        "full_refresh": cfg.get("full_refresh"),
        "tags": list(node.get("tags", []) or []),
    }


def _diversify_by_module(pool: list[dict[str, Any]], per_module: int, total: int) -> list[dict[str, Any]]:
    """Pick up to ``total`` items, at most ``per_module`` per module (preserving
    the pool's order), then top up from the remainder if diversity left us short.
    Gives the home's curated lists cross-domain spread instead of one module."""
    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for it in pool:
        mod = it.get("module", "") or ""
        if counts.get(mod, 0) >= per_module:
            continue
        counts[mod] = counts.get(mod, 0) + 1
        picked.append(it)
        if len(picked) >= total:
            return picked
    if len(picked) < total:
        seen = {id(x) for x in picked}
        picked.extend(x for x in pool if id(x) not in seen)
    return picked[:total]


def catalog_overview() -> dict[str, Any]:
    """Platform overview — registry/manifest only (CH-free).

    Counts, ownership %, doc-coverage %, per-domain tier breakdown, and the
    most-connected datasets. Live health (fresh % / failing tests) is a
    separate Elementary-gated call (``catalog_health``)."""
    from collections import Counter

    snap = current_snapshot()
    if snap is None:
        return {"available": False, "reason": "semantic snapshot unavailable"}
    models = getattr(snap, "models", {}) or {}
    metrics = getattr(snap, "metrics", {}) or {}
    glossary = len(getattr(snap, "graph_profiles", ()) or ())

    tier_counts: Counter = Counter()
    owned = 0
    total_cols = 0
    documented = 0
    domains: dict[str, dict[str, Any]] = {}
    for m in models.values():
        st = m.get("semantic_status", "") or ""
        tier_counts[st] += 1
        if (m.get("owner") or "").strip():
            owned += 1
        for c in (m.get("columns") or {}).values():
            total_cols += 1
            if (c or {}).get("description"):
                documented += 1
        mod = m.get("module", "") or "unknown"
        d = domains.setdefault(
            mod, {"module": mod, "total": 0, "approved": 0, "candidate": 0, "docs_only": 0}
        )
        d["total"] += 1
        if st in ("approved", "candidate", "docs_only"):
            d[st] += 1

    connected = sorted(
        (
            {
                "name": n,
                "module": m.get("module", ""),
                "tier": m.get("semantic_status", ""),
                "materialized": m.get("materialized", "") or "",
                "downstream_count": len((m.get("lineage") or {}).get("downstream", []) or []),
            }
            for n, m in models.items()
        ),
        key=lambda x: -x["downstream_count"],
    )
    # Curated entry points: approved-first, most-connected curated assets — but
    # DIVERSIFIED across domains (≤2 per module) so the shelf isn't all-execution.
    # execution dominates raw downstream-count, which previously made every
    # entry point a single-domain candidate.
    _tier_rank = {"approved": 2, "candidate": 1}
    # Require >0 downstream so a "Key data products" row never shows "↓ 0"
    # (that contradicted the most-connected framing).
    _ep_pool = sorted(
        (
            c for c in connected
            if c["tier"] in ("approved", "candidate")
            and c["materialized"] != "source"
            and c["downstream_count"] > 0
        ),
        key=lambda c: (-_tier_rank.get(c["tier"], 0), -c["downstream_count"]),
    )
    entry_points = _diversify_by_module(_ep_pool, per_module=2, total=8)

    # Glossary terms (graph profiles) — small set, surface them all so the home
    # has a real entry point for the glossary, not just a counter.
    glossary_terms = [
        {"name": p.profile, "module": getattr(p, "module", "") or ""}
        for p in (getattr(snap, "graph_profiles", ()) or ())
    ]
    # Top metrics: approved-first, diversified across modules (≤2 per module) so
    # the surface isn't an all-execution alphabetical slice. (No usage signal is
    # available in the registry, so tier + domain spread is the best ranking.)
    _metric_pool = sorted(
        (
            {
                "name": n,
                "label": mm.get("label", n) or n,
                "module": mm.get("module", "") or "",
                "tier": mm.get("quality_tier", "") or "",
            }
            for n, mm in metrics.items()
        ),
        key=lambda x: (-_tier_rank.get(x["tier"], 0), x["module"], x["name"]),
    )
    top_metrics = _diversify_by_module(_metric_pool, per_module=2, total=8)

    n_models = len(models)
    return {
        "available": True,
        "stats": {
            "models": n_models,
            "metrics": len(metrics),
            "glossary": glossary,
            "domains": len(domains),
            "relationships": len(getattr(snap, "relationships", []) or []),
            "owned_pct": round(100 * owned / n_models, 1) if n_models else 0,
            "doc_coverage_pct": round(100 * documented / total_cols, 1) if total_cols else 0,
            "tier_counts": dict(tier_counts),
        },
        "domains": sorted(domains.values(), key=lambda d: -d["total"]),
        "entry_points": entry_points,
        "glossary_terms": glossary_terms,
        "top_metrics": top_metrics,
    }


def catalog_governance() -> dict[str, Any]:
    """Governance aggregates — registry/manifest only (CH-free, grant-free).

    Ownership breakdown, tier distribution, data classification (restricted vs
    public by privacy tag), and per-domain documentation coverage."""
    from collections import Counter

    snap = current_snapshot()
    if snap is None:
        return {"available": False, "reason": "semantic snapshot unavailable"}
    models = getattr(snap, "models", {}) or {}
    owners: Counter = Counter()
    tiers: Counter = Counter()
    restricted = 0
    dom_doc: dict[str, list[int]] = {}
    unowned: list[dict[str, str]] = []
    for name, m in models.items():
        raw_owner = (m.get("owner") or "").strip()
        if raw_owner:
            # Collapse alias drift (analytics-team vs analytics_team) onto one key.
            owners[raw_owner.replace("-", "_")] += 1
        else:
            owners["(unowned)"] += 1
            unowned.append({"name": name, "module": m.get("module", "") or ""})
        tiers[m.get("semantic_status", "") or "unknown"] += 1
        tags = m.get("tags") or []
        if any(str(t).startswith("privacy:") or t == "mixpanel_ga" for t in tags):
            restricted += 1
        mod = m.get("module", "") or "unknown"
        dd = dom_doc.setdefault(mod, [0, 0])
        for c in (m.get("columns") or {}).values():
            dd[1] += 1
            if (c or {}).get("description"):
                dd[0] += 1
    n = len(models)
    return {
        "available": True,
        "model_count": n,
        "ownership": [{"owner": o, "count": c} for o, c in owners.most_common()],
        "tiers": dict(tiers),
        "classification": {"restricted": restricted, "public": n - restricted},
        "unowned_count": len(unowned),
        "unowned_sample": sorted(unowned, key=lambda u: u["name"])[:12],
        "doc_coverage_by_module": sorted(
            (
                {
                    "module": k,
                    "documented": v[0],
                    "total": v[1],
                    "pct": round(100 * v[0] / v[1], 1) if v[1] else 0,
                }
                for k, v in dom_doc.items()
            ),
            key=lambda x: -x["total"],
        ),
    }


# ---------------------------------------------------------------------------
# Slice 2 — Elementary-gated observability (feature-flagged; degrades to
# {available:false} until `GRANT SELECT ON elementary.* TO mcp_reader` lands).
# Cross-db access: connect database='dbt', FROM elementary.<table>. SQL follows
# the feasibility-review recipe (reverse_index=1; rows_affected from
# dbt_run_results; test join on model_unique_id) and is exercised once the grant
# is in place; until then every call returns a graceful unavailable payload.
# ---------------------------------------------------------------------------

_ELEM_AVAILABLE: bool | None = None


def _elementary_available(ch) -> bool:
    global _ELEM_AVAILABLE
    if _ELEM_AVAILABLE is None:
        try:
            run_structured_query(
                ch, "SELECT 1 FROM elementary.dbt_models LIMIT 1",
                database="dbt", requested_max_rows=1,
            )
            _ELEM_AVAILABLE = True
        except Exception as exc:  # noqa: BLE001 — 497/absent both → unavailable
            logger.info("elementary not available: %s", exc)
            _ELEM_AVAILABLE = False
    return _ELEM_AVAILABLE


def _model_unique_id(name: str) -> str:
    return f"model.gnosis_dbt.{name}"


def _model_known(name: str) -> bool:
    """Whether ``name`` is a real model in the registry — lets the Elementary
    tools distinguish 'unknown entity' from 'real model, no run/test history'."""
    snap = current_snapshot()
    return bool(snap and (getattr(snap, "models", {}) or {}).get(name) is not None)


def _catalog_run_state_impl(ch, name: str, history: int = 10) -> dict[str, Any]:
    """Latest run + recent history for a model from Elementary."""
    if not _model_known(name):
        return {"available": False, "reason": f"model '{name}' not found", "not_found": True, "name": name}
    if not _elementary_available(ch):
        return {"available": False, "reason": "elementary not connected", "name": name}
    uid = _model_unique_id(name)
    hist = max(1, min(int(history or 10), 50))
    sql = (
        "SELECT r.status, r.generated_at, r.execution_time, r.full_refresh, "
        "rr.rows_affected "
        "FROM elementary.model_run_results AS r "
        "LEFT JOIN elementary.dbt_run_results AS rr "
        "  ON rr.unique_id = r.unique_id AND rr.invocation_id = r.invocation_id "
        "WHERE r.unique_id = {uid:String} "
        "ORDER BY r.generated_at DESC LIMIT {n:UInt32}"
    )
    try:
        res = run_structured_query(
            ch, sql, database="dbt", parameters={"uid": uid, "n": hist},
            requested_max_rows=hist,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("run_state query failed for %s: %s", name, exc)
        return {"available": False, "reason": "run state unavailable", "name": name}
    runs = [
        {
            "status": r[0], "completed_at": r[1], "execution_time": r[2],
            "full_refresh": r[3], "rows_affected": r[4] if len(r) > 4 else None,
        }
        for r in res.rows
    ]
    return {"available": True, "name": name, "latest": runs[0] if runs else None, "history": runs}


def _catalog_test_results_impl(ch, name: str) -> dict[str, Any]:
    """Latest dbt + elementary test pass/fail/warn for a model from Elementary."""
    if not _model_known(name):
        return {"available": False, "reason": f"model '{name}' not found", "not_found": True, "name": name}
    if not _elementary_available(ch):
        return {"available": False, "reason": "elementary not connected", "name": name}
    uid = _model_unique_id(name)
    # NB: alias must NOT shadow the `detected_at` column, or argMax(..,detected_at)
    # resolves to the max() aggregate → ILLEGAL_AGGREGATION (CH code 184).
    sql = (
        "SELECT test_unique_id, argMax(test_name, detected_at) AS test_name, "
        "argMax(status, detected_at) AS status, max(detected_at) AS last_detected "
        "FROM elementary.elementary_test_results "
        "WHERE model_unique_id = {uid:String} "
        "GROUP BY test_unique_id"
    )
    try:
        res = run_structured_query(
            ch, sql, database="dbt", parameters={"uid": uid}, requested_max_rows=500
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("test_results query failed for %s: %s", name, exc)
        return {"available": False, "reason": "test results unavailable", "name": name}
    tests = [
        {"name": r[1], "status": r[2], "detected_at": r[3]} for r in res.rows
    ]
    counts: dict[str, int] = {}
    for t in tests:
        s = str(t["status"] or "").lower()
        counts[s] = counts.get(s, 0) + 1
    return {"available": True, "name": name, "tests": tests, "counts": counts}


# Latest status per test (dedup by test_unique_id over detected_at) — counting
# raw rows would tally every historical failure, not the current state.
_TEST_HEALTH_SQL = (
    "SELECT countIf(s IN ('fail','error')) AS failing, countIf(s='warn') AS warning, "
    "count() AS total FROM (SELECT test_unique_id, argMax(lower(status), detected_at) AS s "
    "FROM elementary.elementary_test_results GROUP BY test_unique_id)"
)
# Latest run per model (dedup by unique_id over generated_at).
_MODEL_RUN_SQL = (
    "SELECT countIf(s='success') AS ok, countIf(s IN ('fail','error')) AS failed, "
    "countIf(s IN ('skipped','skip')) AS skipped, count() AS total, "
    "toString(max(g)) AS as_of FROM (SELECT unique_id, argMax(lower(status), generated_at) AS s, "
    "max(generated_at) AS g FROM elementary.model_run_results "
    "WHERE unique_id IN (SELECT unique_id FROM elementary.dbt_models) "
    "GROUP BY unique_id)"
)


def _catalog_health_impl(ch) -> dict[str, Any]:
    """Platform test health (current failing/warning/total) from Elementary."""
    if not _elementary_available(ch):
        return {"available": False, "reason": "elementary not connected"}
    try:
        r = run_structured_query(ch, _TEST_HEALTH_SQL, database="dbt", requested_max_rows=1)
        row = r.rows[0] if r.rows else [0, 0, 0]
    except Exception as exc:  # noqa: BLE001
        logger.info("health query failed: %s", exc)
        return {"available": False, "reason": "health unavailable"}
    return {"available": True, "failing_tests": row[0], "warning_tests": row[1], "total_tests": row[2]}


def _catalog_observability_impl(ch) -> dict[str, Any]:
    """Platform observability dashboard from Elementary: model-run health, test
    health, needs-attention (models whose latest run failed), recent runs, and
    the data's as-of timestamp (Elementary upload is only as fresh as the last
    dbt run with the elementary package enabled)."""
    if not _elementary_available(ch):
        return {"available": False, "reason": "elementary not connected"}
    out: dict[str, Any] = {"available": True}
    try:
        m = run_structured_query(ch, _MODEL_RUN_SQL, database="dbt", requested_max_rows=1)
        mr = m.rows[0] if m.rows else [0, 0, 0, 0, ""]
        # failed = hard fail/error only; skipped is tracked separately (it was
        # previously folded into failed, inflating the failure count ~5x).
        out["models"] = {"ok": mr[0], "failed": mr[1], "skipped": mr[2], "total": mr[3]}
        out["as_of"] = mr[4]

        t = run_structured_query(ch, _TEST_HEALTH_SQL, database="dbt", requested_max_rows=1)
        tr = t.rows[0] if t.rows else [0, 0, 0]
        out["tests"] = {"failing": tr[0], "warning": tr[1], "total": tr[2]}

        # Non-success models joined to the current manifest (dbt_models) so ghosts
        # of deleted models drop out, then split by the production tag: real cron
        # failures (needs_attention) vs. downstream skips (skipped_downstream) vs.
        # dev/WIP/non-cron noise (inactive). Replaces a flat LIMIT 25 list that
        # mixed all three and made the header undercount the true total. tags is a
        # String holding a JSON array, so parse with JSONExtract before has().
        na = run_structured_query(
            ch,
            "SELECT r.name, r.s, toString(r.g), "
            "has(JSONExtract(dm.tags, 'Array(String)'), 'production') AS is_prod "
            "FROM (SELECT name, argMax(status, generated_at) AS s, max(generated_at) AS g "
            "      FROM elementary.model_run_results GROUP BY name) AS r "
            "INNER JOIN elementary.dbt_models AS dm ON dm.name = r.name "
            "WHERE r.s != 'success' "
            "ORDER BY multiIf(lower(r.s) IN ('error','fail'), 0, lower(r.s) = 'skipped', 2, 1) ASC, r.g DESC "
            "LIMIT 500",
            database="dbt", requested_max_rows=500,
        )
        errors: list[dict[str, Any]] = []
        skipped_downstream: list[dict[str, Any]] = []
        inactive: list[dict[str, Any]] = []
        for r in na.rows:
            row = {"name": r[0], "status": r[1], "completed_at": r[2]}
            status = str(r[1] or "").lower()
            if not r[3]:
                inactive.append(row)
            elif status in ("skipped", "skip"):
                skipped_downstream.append(row)
            else:
                errors.append(row)
        out["needs_attention"] = errors
        out["skipped_downstream"] = skipped_downstream
        out["inactive"] = inactive
        out["counts"] = {
            "errors": len(errors),
            "skipped": len(skipped_downstream),
            "inactive": len(inactive),
            "total": len(errors) + len(skipped_downstream) + len(inactive),
        }

        # Latest run PER MODEL (argMax), then the 15 most-recent DISTINCT models —
        # otherwise microbatch models log many slices per run and 15 raw rows
        # collapse to ~4 distinct names.
        rr = run_structured_query(
            ch,
            "SELECT name, status, toString(g), et FROM ("
            "  SELECT name, argMax(status, generated_at) AS status, "
            "         max(generated_at) AS g, argMax(execution_time, generated_at) AS et "
            "  FROM elementary.model_run_results GROUP BY name"
            ") ORDER BY g DESC LIMIT 15",
            database="dbt", requested_max_rows=15,
        )
        out["recent_runs"] = [
            {"name": r[0], "status": r[1], "completed_at": r[2], "execution_time": r[3]}
            for r in rr.rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.info("observability query failed: %s", exc)
        return {"available": False, "reason": "observability unavailable"}
    return out


# ---------------------------------------------------------------------------
# Core tool implementations (plain functions — registered as MCP tools and in
# the web-app dispatch registry below).
# ---------------------------------------------------------------------------


def catalog_search(
    query: str = "",
    entity_types: list[str] | None = None,
    module: str = "",
    tier: str = "all",
    tags: list[str] | None = None,
    owner: str = "",
    limit: int = 30,
    include_column_matches: bool = False,
) -> dict[str, Any]:
    """Search the data catalog across models, metrics, and glossary terms.

    Unified BM25 ranking over the semantic registry, with facets so the UI can
    build OpenMetadata-style filter chips. ``facets`` are computed over the full
    query universe (all entity types) so the sidebar shows the complete
    breakdown; ``entity_types`` / ``module`` / ``tier`` / ``tags`` then filter
    the returned ``hits``. An empty query browses everything (tier-then-name
    ordered).

    Args:
        query: Free-text query. Empty = browse.
        entity_types: Subset of ["model", "metric", "glossary"] to return.
        module: Restrict hits to one module (e.g. "execution", "bridges").
        tier: One of "approved"/"candidate"/"docs_only" or "all".
        tags: Restrict hits to those carrying ALL of these tags.
        limit: Max hits returned (facets are always over the full universe).

    Returns:
        ``{query, total, hits[], facets{type,module,tier,tags}, warnings[]}``.
    """
    snap = current_snapshot()
    if snap is None:
        return {
            "query": query,
            "total": 0,
            "hits": [],
            "facets": {"type": {}, "module": {}, "tier": {}, "tags": {}, "owner": {}},
            "suggestions": [],
            "warnings": ["semantic snapshot unavailable"],
        }

    idx = _catalog_index(snap)
    want_types = [t for t in (entity_types or list(ENTITY_TYPES)) if t in ENTITY_TYPES]
    if not want_types:
        want_types = list(ENTITY_TYPES)
    want_tags = [t for t in (tags or []) if t]
    limit = max(1, min(int(limit or 30), 200))

    q = (query or "").strip()
    if q:
        ql = q.lower()
        # BM25 handles full-token relevance; we then fold in prefix/substring
        # matches (so partial typing like "brid" returns hits, not zero) and a
        # name/exact/model boost so canonical entities outrank the *_value metric
        # flood. Combined score drives the final order.
        scored: dict[str, float] = {}
        # Metrics + glossary: local BM25 + token-overlap floor (the floor
        # keeps matches surfacing when the corpus is too small for IDF).
        for hid, sc in idx.bm25.search(q, top_k=len(idx.hits) or 1):
            scored[hid] = float(sc)
        qtok = set(search_tokenize(q))
        if qtok:
            for hid, toks in idx.doc_tokens.items():
                overlap = len(qtok & toks)
                if overlap:
                    scored[hid] = scored.get(hid, 0.0) + 0.5 * overlap
        # Models: the canonical field-weighted index (name/prefix bonuses and
        # fuzzy tolerance are applied INSIDE it — no double-bonusing below).
        column_matches: dict[str, list[dict[str, Any]]] = {}
        for mh in idx.model_search.search(
            q,
            limit=len(idx.model_search) or 1,
            include_column_matches=include_column_matches,
        ):
            scored[f"model:{mh.name}"] = mh.score
            if mh.matched_columns:
                column_matches[f"model:{mh.name}"] = mh.matched_columns
        for hid, hit in idx.hits.items():
            if hit.type == "model":
                continue  # bonused inside ModelSearchIndex
            nl = hit.name.lower()
            tl = hit.title.lower()
            bonus = 0.0
            if nl == ql or tl == ql:
                bonus += 6.0
            elif nl.startswith(ql) or tl.startswith(ql):
                bonus += 3.5
            elif ql in nl or ql in tl:
                bonus += 1.5
            if bonus:
                scored[hid] = scored.get(hid, 0.0) + bonus
        # Type weighting on the relevant set: curated datasets lead; strongly
        # demote auto-generated candidate `*_value` metrics so they don't bury
        # the models users actually search for (DataHub surfaces datasets first).
        for hid in list(scored):
            hit = idx.hits.get(hid)
            if hit is None:
                continue
            if hit.type == "model":
                scored[hid] += 3.0
            elif hit.type == "glossary":
                scored[hid] += 1.0
            elif hit.type == "metric" and hit.tier != "approved" and hit.name.endswith("_value"):
                scored[hid] -= 2.5
        # AND-bias: a multi-word query should NARROW, not broaden. Keep only docs
        # that contain EVERY token (name/title/description/module/tags); fall back
        # to the OR set only when the strict set is empty. Tokens split on
        # whitespace AND underscores so a pasted name like
        # "int_execution_bridges_addresses" ANDs its parts (was OR → whole
        # universe with the exact match buried).
        tokens = [t for t in _re.split(r"[\s_]+", ql) if t]
        if len(tokens) > 1:
            def _words(h: _Hit) -> set[str]:
                text = f"{h.name} {h.title} {getattr(h, 'description', '') or ''} {h.module} {' '.join(h.tags)}".lower()
                return {w for w in _re.split(r"[^a-z0-9]+", text) if w}
            # A token matches a doc only as a whole WORD or word-PREFIX (not a
            # loose substring — else "int" matches "mint"/"print" and the AND
            # gate never narrows). Require every token to match some word.
            strict = set()
            for hid in scored:
                h = idx.hits.get(hid)
                if h is None:
                    continue
                ws = _words(h)
                if all(any(w == tok or w.startswith(tok) for w in ws) for tok in tokens):
                    strict.add(hid)
            if strict:
                scored = {hid: sc for hid, sc in scored.items() if hid in strict}
        # Fuzzy fallback: a typo (no BM25/substring/exact hit) shouldn't dead-end.
        # Only fires when nothing matched — edit-distance against name/title words.
        if not scored and tokens:
            import difflib as _difflib

            for hid, hit in idx.hits.items():
                words = {w for s in (hit.name, hit.title) for w in _re.split(r"[_\s]+", s.lower()) if w}
                best = max(
                    (_difflib.SequenceMatcher(None, tok, w).ratio() for tok in tokens for w in words),
                    default=0.0,
                )
                if best >= 0.8:
                    scored[hid] = best
        # Tiebreak: score desc, then models first, then trusted tier first.
        _type_rank = {"model": 0, "glossary": 1, "metric": 2}
        ordered = sorted(
            scored.items(),
            key=lambda kv: (
                -kv[1],
                _type_rank.get(idx.hits[kv[0]].type, 3),
                -_TIER_ORDINAL.get(idx.hits[kv[0]].tier, 0),
            ),
        )
        universe = []
        for hid, sc in ordered:
            hit = idx.hits.get(hid)
            if hit is None:
                continue
            hit.score = round(sc, 4)
            universe.append(hit)
    else:
        # Browse: everything, most-trusted then alphabetical.
        universe = sorted(
            idx.hits.values(),
            key=lambda h: (-_TIER_ORDINAL.get(h.tier, 0), h.title.lower()),
        )
        for hit in universe:
            hit.score = None

    # DataHub-style faceting: each dimension's counts are computed over the
    # universe filtered by all the OTHER active filters (not its own), so counts
    # reflect the applied context yet you can still pivot within a dimension.
    want_owner = (owner or "").replace("-", "_")

    def _passes(h: _Hit, skip: str) -> bool:
        if skip != "type" and h.type not in want_types:
            return False
        if skip != "module" and module and h.module != module:
            return False
        if skip != "tier" and tier and tier != "all" and h.tier != tier:
            return False
        if skip != "tags" and want_tags and not all(t in h.tags for t in want_tags):
            return False
        if skip != "owner" and want_owner and (h.owner or "").replace("-", "_") != want_owner:
            return False
        return True

    facets: dict[str, dict[str, int]] = {"type": {}, "module": {}, "tier": {}, "tags": {}, "owner": {}}
    for h in universe:
        if _passes(h, "type"):
            facets["type"][h.type] = facets["type"].get(h.type, 0) + 1
        if _passes(h, "module") and h.module:
            facets["module"][h.module] = facets["module"].get(h.module, 0) + 1
        if _passes(h, "tier") and h.tier:
            facets["tier"][h.tier] = facets["tier"].get(h.tier, 0) + 1
        if _passes(h, "owner") and h.owner:
            ow = h.owner.replace("-", "_")
            facets["owner"][ow] = facets["owner"].get(ow, 0) + 1
        if _passes(h, "tags"):
            for t in h.tags:
                facets["tags"][t] = facets["tags"].get(t, 0) + 1

    filtered = [h for h in universe if _passes(h, "")]
    hits = []
    for h in filtered[:limit]:
        d = h.as_dict()
        if include_column_matches:
            cols = column_matches.get(h.id) if q else None
            if cols:
                d["matched_columns"] = cols
        hits.append(d)
    # On a zero-result query, surface the query-relevant universe (which already
    # includes the fuzzy-typo fallback) as "did you mean" suggestions so the
    # empty state isn't a dead end.
    suggestions: list[dict[str, str]] = []
    if q and not filtered:
        suggestions = [{"name": h.name, "title": h.title, "type": h.type} for h in universe[:6]]
    return {
        "query": query,
        "total": len(filtered),
        "hits": hits,
        "facets": facets,
        "limit": limit,
        "suggestions": suggestions,
        "warnings": [],
    }


def get_catalog_entity(name: str, entity_type: str = "model") -> dict[str, Any]:
    """Return a structured profile for one catalog entity.

    For a model: identity (fqn/owner/tier/tags/materialization/module/path),
    the column schema, direct upstream/downstream (names + counts), the metrics
    rooted on it, and the knowledge-graph profiles it participates in. For a
    metric: its measure, root model, allowed dimensions, time grains, synonyms.

    Args:
        name: Entity name (model name, metric name, or glossary profile id).
        entity_type: "model" (default), "metric", or "glossary".

    Returns:
        A structured profile dict, or ``{error, suggestions[]}`` if not found.
    """
    snap = current_snapshot()
    if snap is None:
        return {"error": "semantic snapshot unavailable", "name": name}

    if entity_type == "metric":
        return _metric_profile(snap, name)
    if entity_type == "glossary":
        return _glossary_profile(snap, name)
    return _model_profile(snap, name)


def _model_profile(snap: Any, name: str) -> dict[str, Any]:
    model = (getattr(snap, "models", {}) or {}).get(name)
    if model is None:
        idx = _catalog_index(snap)
        sugg = [
            h.title
            for _id, _ in idx.bm25.search(name, top_k=5)
            for h in [idx.hits.get(_id)]
            if h is not None and h.type == "model"
        ][:5]
        return {"error": f"Model '{name}' not found.", "suggestions": sugg}

    lineage = model.get("lineage") or {}
    upstream = list(lineage.get("upstream", []) or [])
    downstream = list(lineage.get("downstream", []) or [])

    metric_names = list(model.get("metric_names", []) or [])
    metrics_index = getattr(snap, "metrics", {}) or {}
    metrics = []
    for mn in metric_names:
        m = metrics_index.get(mn)
        if m is None:
            metrics.append({"name": mn, "label": mn, "module": "", "tier": ""})
        else:
            metrics.append(
                {
                    "name": mn,
                    "label": m.get("label", mn),
                    "module": m.get("module", ""),
                    "tier": m.get("quality_tier", ""),
                    "description": (m.get("description", "") or "").strip(),
                }
            )

    graph_profiles = [
        {
            "profile": p.profile,
            "module": p.module,
            "description": p.description,
            "source_kind": p.source_kind,
            "target_kind": p.target_kind,
            "directed": p.directed,
            "weight_column": p.weight_column,
            "quality_tier": p.quality_tier,
        }
        for p in (getattr(snap, "graph_profiles", ()) or ())
        if p.model_name == name
    ]

    columns = _columns_list(model)
    profile: dict[str, Any] = {
        "name": name,
        "type": "model",
        "fqn": _fqn_str(model.get("fqn"), fallback=name),
        "description": (model.get("description", "") or "").strip(),
        "owner": model.get("owner", "") or "",
        "tags": list(model.get("tags", []) or []),
        "tier": model.get("semantic_status", "") or "",
        "quality_tier": model.get("quality_tier", "") or "",
        "materialization": model.get("materialized", "") or "",
        "module": model.get("module", "") or "",
        "path": model.get("path", "") or "",
        "relation_name": model.get("relation_name", "") or "",
        "resource_type": model.get("resource_type", "") or "",
        "columns": columns,
        "column_count": len(columns),
        "dimensions": list(model.get("dimensions", []) or []),
        "measures": list(model.get("measures", []) or []),
        "upstream": upstream,
        "downstream": downstream,
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
        "metrics": metrics,
        "metric_count": len(metrics),
        "graph_profiles": graph_profiles,
    }

    # Optional enrichment from the live manifest (tests + raw SQL) — only when
    # loaded. The registry alone drives everything above.
    try:
        if manifest.is_loaded:
            details = manifest.get_model_details(name)
            if details:
                profile["test_count"] = len(details.get("tests", []) or [])
                profile["raw_sql"] = details.get("raw_sql", "") or ""
    except Exception as exc:  # never let manifest state break the profile
        logger.info("data_catalog: manifest enrichment failed for %s: %s", name, exc)

    return profile


def _metric_profile(snap: Any, name: str) -> dict[str, Any]:
    metric = (getattr(snap, "metrics", {}) or {}).get(name)
    if metric is None:
        return {"error": f"Metric '{name}' not found.", "suggestions": []}
    return {
        "name": name,
        "type": "metric",
        "label": metric.get("label", name),
        "fqn": f"metric.{metric.get('module','')}.{name}",
        "description": (metric.get("description", "") or "").strip(),
        "module": metric.get("module", "") or "",
        "tier": metric.get("quality_tier", "") or "",
        "semantic_status": metric.get("semantic_status", "") or "",
        "root_model": metric.get("root_model", "") or "",
        "measure": str(metric.get("measure") or ""),
        "metric_type": metric.get("type", "") or "",
        "allowed_dimensions": list(metric.get("allowed_dimensions", []) or []),
        "supported_time_grains": list(metric.get("supported_time_grains", []) or []),
        "question_synonyms": list(metric.get("question_synonyms", []) or []),
        "default_filters": list(metric.get("default_filters", []) or []),
    }


def _glossary_profile(snap: Any, name: str) -> dict[str, Any]:
    for p in getattr(snap, "graph_profiles", ()) or ():
        if p.profile == name:
            return {
                "name": name,
                "type": "glossary",
                "fqn": f"glossary.{name}",
                "description": (p.description or "").strip(),
                "module": p.module or "",
                "tier": p.quality_tier or "",
                "source_kind": p.source_kind,
                "target_kind": p.target_kind,
                "directed": p.directed,
                "weight_column": p.weight_column,
                "model_name": p.model_name,
                "question_synonyms": list(p.question_synonyms or []),
            }
    return {"error": f"Glossary term '{name}' not found.", "suggestions": []}


_LINEAGE_HEAVY_NODE_FIELDS = ("raw_sql", "compiled_sql", "raw_code", "compiled_code", "columns")


def _slim_lineage_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop heavy per-node fields (full SQL + column lists) the graph renderer
    doesn't need — they are ~75% of the lineage response and the full versions
    are one click away on the node's own profile."""
    return [{k: v for k, v in n.items() if k not in _LINEAGE_HEAVY_NODE_FIELDS} for n in nodes]


def _seed_anchored_subgraph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], seed_id: str | None, cap: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Truncate to the seed + its nearest ``cap`` neighbours by hop (BFS over the
    UNDIRECTED edge set). Unlike an alphabetical slice this never drops the seed
    and never orphans the kept core — it keeps the connected neighbourhood."""
    from collections import deque

    by_id = {n["id"]: n for n in nodes}
    if seed_id not in by_id:
        seed_id = nodes[0]["id"] if nodes else None
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e["target"])
        adj.setdefault(e["target"], []).append(e["source"])
    kept: list[str] = []
    seen: set[str] = set()
    if seed_id is not None:
        seen.add(seed_id)
        kept.append(seed_id)
        dq = deque([seed_id])
        while dq and len(kept) < cap:
            cur = dq.popleft()
            for nb in sorted(adj.get(cur, ())):
                if nb not in seen and nb in by_id:
                    seen.add(nb)
                    kept.append(nb)
                    dq.append(nb)
                    if len(kept) >= cap:
                        break
    keptset = set(kept)
    new_nodes = [by_id[i] for i in kept]
    new_edges = [e for e in edges if e["source"] in keptset and e["target"] in keptset]
    return new_nodes, new_edges


def catalog_lineage(
    seed: str,
    direction: str = "both",
    depth: int = 1,
) -> dict[str, Any]:
    """Bounded model-lineage subgraph for the entity-profile Lineage tab.

    Wraps the dbt manifest's ``get_subgraph`` (rich node metadata + depth
    control) with the same ``MAX_LINEAGE_NODES`` truncation as
    ``get_model_subgraph``. Falls back to the registry's direct 1-hop lineage
    when the manifest is not loaded.

    Returns ``{seed, direction, depth, nodes[], edges[], truncated?, source}``.
    """
    capped_depth = min(max(int(depth or 1), 0), 5)
    direction = direction if direction in ("upstream", "downstream", "both") else "both"

    snap = current_snapshot()
    model = (getattr(snap, "models", {}) or {}).get(seed) if snap else None
    # Sources (resource_type 'source') don't resolve in the manifest model DAG
    # (keyed by short model name), so go straight to the registry lineage —
    # otherwise get_subgraph returns an empty no-error result and the graph
    # silently shows nothing on flagship source entities (e.g. execution.logs).
    is_source = bool(model) and model.get("resource_type") == "source"

    if manifest.is_loaded and not is_source:
        result = manifest.get_subgraph(
            seed=seed, direction=direction, depth=capped_depth
        )
        nodes = result.get("nodes", [])
        if not result.get("error") and nodes:
            # Node ids are dbt unique-ids; `name` is the short name we seeded with.
            seed_id = next((n["id"] for n in nodes if n.get("name") == seed), None)
            edges = result.get("edges", [])
            if len(nodes) > MAX_LINEAGE_NODES:
                nodes, edges = _seed_anchored_subgraph(nodes, edges, seed_id, MAX_LINEAGE_NODES)
                result["truncated"] = True
            result["nodes"] = _slim_lineage_nodes(nodes)
            result["edges"] = edges
            result["seed_id"] = seed_id or seed
            result["node_count"] = len(nodes)
            result["source"] = "manifest"
            return result

    # Fallback: direct lineage from the registry snapshot (1-hop).
    if model is None:
        return {
            "seed": seed,
            "direction": direction,
            "depth": capped_depth,
            "nodes": [],
            "edges": [],
            "error": f"Model '{seed}' not found.",
            "source": "registry",
        }
    lineage = model.get("lineage") or {}
    seed_kind = "source" if is_source else "model"
    nodes = {seed: {"id": seed, "name": seed, "kind": seed_kind}}
    edges: list[dict[str, Any]] = []
    if direction in ("upstream", "both"):
        for up in lineage.get("upstream", []) or []:
            nodes.setdefault(up, {"id": up, "name": up, "kind": "model"})
            edges.append({"id": f"{up}->{seed}", "source": up, "target": seed})
    if direction in ("downstream", "both"):
        for dn in lineage.get("downstream", []) or []:
            nodes.setdefault(dn, {"id": dn, "name": dn, "kind": "model"})
            edges.append({"id": f"{seed}->{dn}", "source": seed, "target": dn})
    return {
        "seed": seed,
        "seed_id": seed,
        "direction": direction,
        "depth": capped_depth,
        "nodes": list(nodes.values()),
        "edges": edges,
        "node_count": len(nodes),
        "source": "registry",
    }


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_data_catalog_tools(mcp, ch=None) -> None:
    """Register the Data Catalog mini app + its structured tools.

    ``ch`` (ClickHouse manager) is accepted for signature parity with the other
    ``register_*_tools`` and future P2 graph tabs; the catalog itself is
    registry-native and needs no DB access.
    """
    from cerebro_mcp.tools.visualization import mini_apps

    mini_apps.register_app(
        DATA_CATALOG_APP_ID, title=DEFAULT_TITLE, resource_uri=DATA_CATALOG_URI
    )

    @mcp.resource(DATA_CATALOG_URI, mime_type="text/html;profile=mcp-app")
    def serve_data_catalog_app() -> str:
        return get_data_catalog_html()

    @mcp.tool()
    def catalog_search_tool(
        query: str = "",
        entity_types: list[str] | None = None,
        module: str = "",
        tier: str = "all",
        tags: list[str] | None = None,
        owner: str = "",
        limit: int = 30,
    ) -> dict[str, Any]:
        """Search the data catalog (models, metrics, glossary) with facets.

        See the catalog mini app for the interactive surface. Returns ranked
        ``hits`` plus ``facets`` for type/module/tier/tags/owner.
        """
        return catalog_search(
            query=query,
            entity_types=entity_types,
            module=module,
            tier=tier,
            tags=tags,
            owner=owner,
            limit=limit,
        )

    @mcp.tool()
    def get_catalog_entity_tool(
        name: str, entity_type: str = "model"
    ) -> dict[str, Any]:
        """Return a structured catalog profile for one entity (model/metric/glossary)."""
        return get_catalog_entity(name=name, entity_type=entity_type)

    # ch-backed tools are CLOSURES capturing `ch` — the web dispatch only
    # forwards browser-supplied arguments, so `ch` can never be a tool param.
    @mcp.tool()
    def catalog_sample(name: str = "", limit: int = 20) -> dict[str, Any]:
        """Return up to `limit` live sample rows for a model's table.

        Privacy-gated: privacy-restricted models return
        ``{available:false, restricted:true}`` without querying. Degrades to a
        warning payload on any error.
        """
        return _catalog_sample_impl(ch, name, limit)

    @mcp.tool()
    def catalog_table_stats(name: str = "") -> dict[str, Any]:
        """Row count + on-disk size for a model's physical table (n/a for views)."""
        return _catalog_table_stats_impl(ch, name)

    @mcp.tool()
    def catalog_run_state(name: str = "", history: int = 10) -> dict[str, Any]:
        """Latest run + recent history for a model (Elementary; feature-flagged)."""
        return _catalog_run_state_impl(ch, name, history)

    @mcp.tool()
    def catalog_test_results(name: str = "") -> dict[str, Any]:
        """Latest test pass/fail/warn for a model (Elementary; feature-flagged)."""
        return _catalog_test_results_impl(ch, name)

    @mcp.tool()
    def catalog_health() -> dict[str, Any]:
        """Platform freshness / failing-tests health (Elementary; feature-flagged)."""
        return _catalog_health_impl(ch)

    @mcp.tool()
    def catalog_observability() -> dict[str, Any]:
        """Platform observability dashboard: model-run + test health, needs-attention,
        recent runs, and the data as-of timestamp (Elementary; feature-flagged)."""
        return _catalog_observability_impl(ch)

    @mcp.tool(
        meta={
            "ui": {"resourceUri": DATA_CATALOG_URI},
            "ui/resourceUri": DATA_CATALOG_URI,
        }
    )
    def open_data_catalog(
        query: str = "", entity: str = "", entity_type: str = "model", etype: str = ""
    ) -> CallToolResult:
        """Open the Data Catalog mini app.

        With no args, opens the Explore/Search landing. With ``entity`` set,
        deep-links straight to that entity's profile page. ``etype`` is the URL
        alias the front-end writes for ``entity_type`` (shared deep links);
        accept it so a shared ``?entity=X&etype=metric`` renders the right type.
        """
        entity_type = etype or entity_type
        if entity:
            payload: dict[str, Any] = {
                "type": "INITIAL_LOAD",
                "app_id": DATA_CATALOG_APP_ID,
                "view": "entity",
                "entity_type": entity_type,
                "entity_name": entity,
                "entity": get_catalog_entity(entity, entity_type),
            }
            summary = f"Data Catalog: {entity}"
        else:
            search = catalog_search(query=query, limit=30)
            payload = {
                "type": "INITIAL_LOAD",
                "app_id": DATA_CATALOG_APP_ID,
                "view": "search",
                "query": query,
                "search": search,
                # Overview + governance are registry-only (CH-free) so the home
                # and Governance section render instantly on open, no round-trip.
                "overview": catalog_overview(),
                "governance": catalog_governance(),
            }
            summary = (
                f"Data Catalog ready: {search['total']} entities match "
                f"'{query}'" if query else "Data Catalog ready"
            )
        return CallToolResult(
            content=[TextContent(type="text", text=summary)],
            structuredContent=payload,
            isError=False,
        )

    web_apps.register_web_app(
        app_id=DATA_CATALOG_APP_ID,
        open_tool="open_data_catalog",
        html_loader=get_data_catalog_html,
        tools={
            "open_data_catalog": open_data_catalog,
            # Plain-dict tools (web dispatch wraps them as structuredContent).
            "catalog_search": catalog_search,
            "get_catalog_entity": get_catalog_entity,
            "catalog_lineage": catalog_lineage,
            "catalog_overview": catalog_overview,
            "catalog_governance": catalog_governance,
            "catalog_run_config": catalog_run_config,
            # ch-backed (closures): live data + Elementary-gated observability.
            "catalog_sample": catalog_sample,
            "catalog_table_stats": catalog_table_stats,
            "catalog_run_state": catalog_run_state,
            "catalog_test_results": catalog_test_results,
            "catalog_health": catalog_health,
            "catalog_observability": catalog_observability,
        },
    )
