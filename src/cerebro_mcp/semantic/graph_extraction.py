"""Pure graph-metadata extraction + validation (cerebro-mcp side).

This is the *only* module that reads the raw ``model.semantic.meta.graph`` block
and turns it into a ``GraphProfile`` (Rule M1). It is intentionally pure — it
imports no runtime/loader state — so it can be exercised in isolation and so the
catalog-reconstruction path (WS4) and live discovery (``graph_profiles``) share
exactly one extractor, guaranteeing 1:1 fidelity against the published
``semantic_graph_catalog.json`` contract.

The two repos are decoupled: ``dbt-cerebro`` has its own validator aligned to the
same field set, and the committed JSON Schema is the shared source of truth
(contract-only sharing). The roundtrip test in each repo is what keeps them from
drifting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Field contract — kept aligned with dbt-cerebro/scripts/semantic/build_registry.py
# (GRAPH_REQUIRED / GRAPH_OPTIONAL) and the published catalog JSON Schema.
GRAPH_REQUIRED = (
    "enabled",
    "profile",
    "source_column",
    "target_column",
    "source_kind",
    "target_kind",
)
GRAPH_OPTIONAL = (
    "directed",
    "time_column",
    # Temporal extensions (Timeline mode). Forward-compatible: absent in
    # older registries/catalogs -> None; authoring them requires the matching
    # dbt-cerebro validator/publisher update (contract-only sharing).
    "time_end_column",
    "temporal_semantics",
    "sector",
    "weight_unit",
    "freshness_sla",
    "coverage_note",
    "weight_column",
    "node_enrichment_model",
    "node_enrichment_key",
    "evidence_model",
    "evidence_source_column",
    "evidence_target_column",
    "default_filters",
    "notes",
)
GRAPH_ALLOWED = set(GRAPH_REQUIRED) | set(GRAPH_OPTIONAL)

# Control / pagination parameters that may appear in a profile's
# ``default_filters`` authoring block but are NOT column filters. The Graph
# Explorer applies these as query controls (SQL LIMIT, hop depth, time window,
# etc.) — never as ``toString(col) = 'val'`` WHERE predicates. Leaking one of
# these into the WHERE clause crashes ClickHouse with
# ``Code: 47 Unknown identifier '<key>'`` (e.g. a published registry that carries
# ``token_transfers -> default_filters={"limit": 500}``).
_CONTROL_KEYS = frozenset(
    {
        "limit",
        "max_neighbors",
        "hops",
        "window_days",
        "transfer_window_days",
        "direction",
        "relation_types",
        "offset",
        "seed_ids",
    }
)


# Highest published-catalog schema_version this runtime understands. A catalog
# stamped newer than this is treated as unsupported -> fall back to live
# discovery (forward-compat, never raise).
SUPPORTED_CATALOG_SCHEMA_VERSION = 1


class GraphExtractionError(ValueError):
    """Raised when an ``enabled`` graph block is missing required fields.

    Callers in the live-discovery path catch this and skip-with-warning (D4),
    preserving the historical "malformed block is skipped" behaviour while making
    the skip observable instead of silent.
    """


@dataclass(frozen=True)
class GraphProfile:
    profile: str
    model_name: str
    relation_name: str
    source_column: str
    target_column: str
    source_kind: str
    target_kind: str
    directed: bool = True
    time_column: str | None = None
    #: End of a validity interval (e.g. circles_trust ``valid_to``). NULL/open
    #: intervals mean "still active".
    time_end_column: str | None = None
    #: Canonical authored time contract. The legacy flow/state/static aliases
    #: remain readable so an older published catalog cannot change query
    #: semantics during a rolling deployment.
    temporal_semantics: str | None = None
    sector: str = ""
    weight_unit: str = ""
    freshness_sla: str = ""
    coverage_note: str = ""
    weight_column: str | None = None
    evidence_model: str | None = None
    evidence_source_column: str | None = None
    evidence_target_column: str | None = None
    node_enrichment_model: str | None = None
    node_enrichment_key: str | None = None
    default_filters: dict[str, Any] = field(default_factory=dict)
    module: str = ""
    description: str = ""
    semantic_status: str = "docs_only"
    quality_tier: str = ""
    question_synonyms: tuple[str, ...] = ()
    semantic_source_file: str = ""

    @property
    def time_aware(self) -> bool:
        return self.time_column is not None

    @property
    def relationship_time(self) -> str:
        """Canonical relationship-time contract used by every graph query.

        ``event`` is bounded to the requested interval. ``state_at`` starts at
        ``time_column`` and remains applicable at the requested as-of time.
        ``interval`` overlaps the requested interval. ``current_snapshot`` is
        current at retrieval and must never be represented as historical.

        Inference is retained for older catalogs, but new authoring should
        always state the contract explicitly.
        """
        authored = {
            "event": "event",
            "state_at": "state_at",
            "interval": "interval",
            "current_snapshot": "current_snapshot",
            # Legacy aliases.
            "flow": "event",
            "state": "state_at",
            "static": "current_snapshot",
        }.get(self.temporal_semantics or "")
        if authored:
            return authored

        # Compatibility for the currently deployed semantic registry. These
        # profiles/relations publish retrieval-time snapshots but predate the
        # explicit temporal_semantics field. A timestamp on such a model is a
        # freshness/observation timestamp, not a historical valid-from fact.
        # Keep this inference in the application so older deployed manifests
        # remain safe without requiring a dbt metadata deployment.
        legacy_snapshot_profiles = {
            "safe_ownership",
            "gpay_ownership",
            "lending_user_to_reserve",
            "address_labeled_as",
            "circles_trust",
        }
        relation = str(self.model_name or "").lower()
        snapshot_named_relation = (
            relation.endswith("_current")
            or relation.endswith("_latest")
            or "_current_" in relation
        )
        if self.profile in legacy_snapshot_profiles or snapshot_named_relation:
            return "current_snapshot"
        if self.time_column is None:
            return "current_snapshot"
        if self.time_end_column:
            return "interval"
        return "event" if self.weight_column else "state_at"

    @property
    def temporal_shape(self) -> str:
        """Legacy Timeline shape derived from :attr:`relationship_time`."""
        return {
            "event": "flow",
            "state_at": "state",
            "interval": "interval",
            "current_snapshot": "static",
        }[self.relationship_time]


def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def graph_meta(model: dict[str, Any]) -> dict[str, Any] | None:
    """Return the enabled graph block for a registry model, or None."""
    meta = (model.get("semantic", {}) or {}).get("meta") or {}
    graph = meta.get("graph")
    if isinstance(graph, dict) and graph.get("enabled"):
        return graph
    return None


# Back-compat alias (graph_profiles re-exports this under its old private name).
_graph_meta = graph_meta


def extract_graph_profile(name: str, model: dict[str, Any]) -> GraphProfile | None:
    """Extract a single ``GraphProfile`` from a registry model.

    Returns ``None`` when the model has no enabled graph block. Raises
    ``GraphExtractionError`` when the block is enabled but missing required
    fields (D4) — the live-discovery caller catches and logs this.
    """
    graph = graph_meta(model)
    if graph is None:
        return None
    missing = [key for key in GRAPH_REQUIRED if key not in graph]
    if missing:
        raise GraphExtractionError(
            f"{name}: graph block missing required keys {missing}"
        )
    source_column = graph["source_column"]
    target_column = graph["target_column"]
    meta = (model.get("semantic", {}) or {}).get("meta") or {}
    return GraphProfile(
        profile=graph["profile"],
        model_name=name,
        relation_name=model.get("relation_name", "") or name,
        source_column=source_column,
        target_column=target_column,
        source_kind=graph["source_kind"],
        target_kind=graph["target_kind"],
        directed=bool(graph.get("directed", True)),
        time_column=_coerce_str(graph.get("time_column")),
        time_end_column=_coerce_str(graph.get("time_end_column")),
        temporal_semantics=_coerce_str(graph.get("temporal_semantics")),
        sector=_coerce_str(graph.get("sector")) or "",
        weight_unit=_coerce_str(graph.get("weight_unit")) or "",
        freshness_sla=_coerce_str(graph.get("freshness_sla")) or "",
        coverage_note=_coerce_str(graph.get("coverage_note")) or "",
        weight_column=_coerce_str(graph.get("weight_column")),
        evidence_model=_coerce_str(graph.get("evidence_model")),
        evidence_source_column=_coerce_str(graph.get("evidence_source_column"))
        or source_column,
        evidence_target_column=_coerce_str(graph.get("evidence_target_column"))
        or target_column,
        node_enrichment_model=_coerce_str(graph.get("node_enrichment_model")),
        node_enrichment_key=_coerce_str(graph.get("node_enrichment_key")),
        default_filters={
            k: v
            for k, v in (graph.get("default_filters") or {}).items()
            if k not in _CONTROL_KEYS
        },
        module=model.get("module", "") or "",
        description=model.get("description", "") or "",
        semantic_status=model.get("semantic_status", "docs_only") or "docs_only",
        quality_tier=model.get("quality_tier", "") or "",
        question_synonyms=tuple(meta.get("question_synonyms") or ()),
        semantic_source_file=model.get("semantic_source_file", "") or "",
    )


def profile_from_catalog_row(row: dict[str, Any]) -> GraphProfile:
    """Reconstruct a ``GraphProfile`` from a published catalog ``profiles[]`` row.

    The catalog ``profiles`` map is a strict 1:1 with ``GraphProfile`` (the
    committed JSON Schema is the contract). Unknown keys are ignored and missing
    keys fall back to dataclass defaults, so a catalog written by an older/newer
    builder degrades gracefully rather than raising (D4 field-drift handling).
    ``question_synonyms`` is normalised back to a tuple.
    """
    fields = {f for f in GraphProfile.__dataclass_fields__}  # type: ignore[attr-defined]
    data = {k: v for k, v in row.items() if k in fields}
    if "question_synonyms" in data and data["question_synonyms"] is not None:
        data["question_synonyms"] = tuple(data["question_synonyms"])
    if "default_filters" in data and data["default_filters"] is None:
        data["default_filters"] = {}
    return GraphProfile(**data)


def synthesize_search_documents(
    profiles: tuple[GraphProfile, ...] | list[GraphProfile],
) -> list[dict[str, Any]]:
    """Build BM25 search documents from profiles when no catalog is published.

    Mirrors the catalog's ``search_documents`` shape (a subset: profile +
    node-type docs) so ``search_graph_catalog`` works identically whether or not
    the sidecar is present. Deterministic (sorted by id).
    """
    docs: list[dict[str, Any]] = []
    kinds: dict[str, set[str]] = {}
    for prof in profiles:
        body = " ".join(
            part
            for part in [
                prof.profile,
                prof.profile.replace("_", " "),
                prof.description,
                " ".join(prof.question_synonyms),
                prof.model_name,
                prof.source_kind,
                prof.target_kind,
            ]
            if part
        )
        docs.append(
            {
                "id": f"profile:{prof.profile}",
                "type": "edge_type",
                "title": prof.profile,
                "module": prof.module,
                "quality_tier": prof.quality_tier,
                "body": body,
                "payload_ref": prof.profile,
            }
        )
        for kind in (prof.source_kind, prof.target_kind):
            if kind:
                kinds.setdefault(kind, set()).add(prof.profile)
    for kind in sorted(kinds):
        docs.append(
            {
                "id": f"node:{kind}",
                "type": "node_type",
                "title": kind,
                "module": "",
                "quality_tier": "",
                "body": f"{kind} {kind.replace('_', ' ')}",
                "payload_ref": kind,
            }
        )
    docs.sort(key=lambda d: d["id"])
    return docs


def validate_graph_meta(
    model_name: str,
    graph: dict[str, Any],
    all_models: dict[str, Any],
    *,
    allowed_kinds: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Validate one graph block; return a list of error/warning dicts.

    Mirrors the dbt-cerebro build-time validator so the runtime can run the same
    checks if needed. Non-raising: returns structured issues with a ``code`` and
    ``severity`` (``error``/``warning``). When ``allowed_kinds`` is provided, an
    unknown ``source_kind``/``target_kind`` is an ERROR (Q2); otherwise the kind
    check is skipped (the dbt CI gate is the authoritative enforcement point).
    """
    issues: list[dict[str, Any]] = []

    def _err(code: str, message: str, severity: str = "error") -> None:
        issues.append(
            {"code": code, "severity": severity, "model": model_name, "message": message}
        )

    if not isinstance(graph, dict):
        _err("graph_meta_not_mapping", f"{model_name}: config.meta.cerebro.graph must be a mapping")
        return issues

    unknown = set(graph) - GRAPH_ALLOWED
    if unknown:
        _err("graph_meta_unknown_keys", f"{model_name}: unknown cerebro.graph keys: {sorted(unknown)}")

    if not graph.get("enabled"):
        return issues

    missing = [key for key in GRAPH_REQUIRED if key not in graph]
    if missing:
        _err("graph_meta_missing_required", f"{model_name}: cerebro.graph missing required keys {missing}")

    columns = set((all_models.get(model_name, {}) or {}).get("columns", {}).keys())
    for key in ("source_column", "target_column", "time_column", "weight_column"):
        col = graph.get(key)
        if not col:
            continue
        if "(" in col or " " in col:  # expression form — skip existence check
            continue
        if columns and col.strip("`") not in columns:
            _err(
                "graph_meta_unknown_column",
                f"{model_name}: cerebro.graph.{key}='{col}' not in model columns",
            )

    for key in ("node_enrichment_model", "evidence_model"):
        ref = graph.get(key)
        if ref and ref not in all_models:
            _err(
                "graph_meta_unknown_model_ref",
                f"{model_name}: cerebro.graph.{key}='{ref}' not found in registry",
            )

    if allowed_kinds is not None:
        for side in ("source_kind", "target_kind"):
            kind = graph.get(side)
            if kind and kind not in allowed_kinds:
                _err(
                    "graph_meta_unknown_kind",
                    f"{model_name}: cerebro.graph.{side}='{kind}' is not a registered node kind",
                )

    return issues
