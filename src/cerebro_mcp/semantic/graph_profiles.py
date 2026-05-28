"""Graph profile discovery for the Graph Explorer mini-app.

Reads `cerebro.graph` metadata off the compiled semantic registry (the
snapshot exposed by `semantic_loader.semantic_runtime`) and turns each
graph-enabled model into a `GraphProfile`. No per-domain knowledge lives
here — everything comes from the dbt-cerebro semantic authoring layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cerebro_mcp.loaders.semantic import semantic_runtime


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


def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _graph_meta(model: dict[str, Any]) -> dict[str, Any] | None:
    meta = (model.get("semantic", {}) or {}).get("meta") or {}
    graph = meta.get("graph")
    if isinstance(graph, dict) and graph.get("enabled"):
        return graph
    return None


def discover_profiles() -> list[GraphProfile]:
    snap = semantic_runtime.snapshot
    if snap is None:
        return []
    profiles: list[GraphProfile] = []
    for name, model in snap.models.items():
        graph = _graph_meta(model)
        if graph is None:
            continue
        try:
            source_column = graph["source_column"]
            target_column = graph["target_column"]
            source_kind = graph["source_kind"]
            target_kind = graph["target_kind"]
            profile_id = graph["profile"]
        except KeyError:
            continue
        meta = (model.get("semantic", {}) or {}).get("meta") or {}
        profiles.append(
            GraphProfile(
                profile=profile_id,
                model_name=name,
                relation_name=model.get("relation_name", "") or name,
                source_column=source_column,
                target_column=target_column,
                source_kind=source_kind,
                target_kind=target_kind,
                directed=bool(graph.get("directed", True)),
                time_column=_coerce_str(graph.get("time_column")),
                weight_column=_coerce_str(graph.get("weight_column")),
                evidence_model=_coerce_str(graph.get("evidence_model")),
                evidence_source_column=_coerce_str(graph.get("evidence_source_column"))
                or source_column,
                evidence_target_column=_coerce_str(graph.get("evidence_target_column"))
                or target_column,
                node_enrichment_model=_coerce_str(graph.get("node_enrichment_model")),
                node_enrichment_key=_coerce_str(graph.get("node_enrichment_key")),
                default_filters=dict(graph.get("default_filters") or {}),
                module=model.get("module", "") or "",
                description=model.get("description", "") or "",
                semantic_status=model.get("semantic_status", "docs_only") or "docs_only",
                quality_tier=model.get("quality_tier", "") or "",
                question_synonyms=tuple(meta.get("question_synonyms") or ()),
                semantic_source_file=model.get("semantic_source_file", "") or "",
            )
        )
    profiles.sort(key=lambda p: (p.module, p.profile))
    return profiles


def profile_by_id(profile_id: str) -> GraphProfile | None:
    for profile in discover_profiles():
        if profile.profile == profile_id:
            return profile
    return None


def profiles_for_kind(node_kind: str) -> list[GraphProfile]:
    return [
        profile
        for profile in discover_profiles()
        if node_kind in (profile.source_kind, profile.target_kind)
    ]


# ---------------------------------------------------------------------------
# SQL assembly
# ---------------------------------------------------------------------------


def build_neighbors_sql(
    profile: GraphProfile,
    *,
    seed_ids: list[str],
    direction: str = "both",
    window_days: int = 90,
    limit: int = 25,
) -> tuple[str, dict[str, Any]]:
    """Build a ClickHouse SQL that fetches neighbors of `seed_ids` for a profile."""
    src = profile.source_column
    tgt = profile.target_column
    rel = profile.relation_name or profile.model_name

    params: dict[str, Any] = {"seed_ids": [str(s) for s in seed_ids], "lim": int(limit)}
    where_bits: list[str] = []
    # Wrap endpoint columns in toString(...) so the comparison is type-safe
    # regardless of the underlying column type (UInt32 validator_index,
    # UInt64, etc.). The seed ids are always strings coming from the UI.
    if direction in ("out", "both"):
        where_bits.append(f"toString({src}) IN {{seed_ids:Array(String)}}")
    if direction in ("in", "both"):
        where_bits.append(f"toString({tgt}) IN {{seed_ids:Array(String)}}")
    if not where_bits:
        where_bits.append("1 = 0")
    where_clause = " OR ".join(where_bits)

    time_clause = ""
    if profile.time_column:
        time_clause = f" AND {profile.time_column} >= now() - INTERVAL {{win:UInt32}} DAY"
        params["win"] = int(max(1, window_days))

    weight_expr = f"sum({profile.weight_column})" if profile.weight_column else "toFloat64(count())"

    # Project endpoints as String too — keeps downstream node-id assembly uniform
    # across profiles with mixed scalar/string endpoint types.
    sql = f"""
        SELECT
            toString({src}) AS source_id,
            toString({tgt}) AS target_id,
            {weight_expr} AS weight,
            count() AS edge_count
        FROM {rel}
        WHERE ({where_clause}){time_clause}
        GROUP BY source_id, target_id
        ORDER BY weight DESC
        LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_sample_sql(
    profile: GraphProfile,
    *,
    window_days: int = 90,
    limit: int = 25,
) -> tuple[str, dict[str, Any]]:
    """Top-weight edges of a profile with NO seed filter.

    Used when the user clicks a profile row in the catalog without pasting a
    seed address — we preview the graph so they can click any node to promote
    it into a real seed.
    """
    src = profile.source_column
    tgt = profile.target_column
    rel = profile.relation_name or profile.model_name

    params: dict[str, Any] = {"lim": int(limit)}
    time_clause = ""
    if profile.time_column:
        time_clause = f" WHERE {profile.time_column} >= now() - INTERVAL {{win:UInt32}} DAY"
        params["win"] = int(max(1, window_days))

    weight_expr = f"sum({profile.weight_column})" if profile.weight_column else "toFloat64(count())"
    sql = f"""
        SELECT
            toString({src}) AS source_id,
            toString({tgt}) AS target_id,
            {weight_expr} AS weight,
            count() AS edge_count
        FROM {rel}{time_clause}
        GROUP BY source_id, target_id
        ORDER BY weight DESC
        LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_evidence_sql(
    profile: GraphProfile,
    *,
    source_id: str,
    target_id: str,
    limit: int = 25,
) -> tuple[str, dict[str, Any]]:
    model = profile.evidence_model or profile.model_name
    src = profile.evidence_source_column or profile.source_column
    tgt = profile.evidence_target_column or profile.target_column
    params: dict[str, Any] = {"src": source_id, "tgt": target_id, "lim": int(limit)}
    sql = f"""
        SELECT *
        FROM {model}
        WHERE {src} = {{src:String}} AND {tgt} = {{tgt:String}}
        LIMIT {{lim:UInt32}}
    """
    return sql, params


# ---------------------------------------------------------------------------
# Role-based profile inference
# ---------------------------------------------------------------------------


_ROLE_TO_PROFILES: dict[str, tuple[str, ...]] = {
    "is_circles_avatar": (
        "circles_trust",
        "circles_avatar_balances",
        "circles_trust_history",
    ),
    "is_gpay_wallet": ("gpay_ownership",),
    "is_ga_user": ("gpay_ownership",),
    "is_safe": ("safe_ownership", "token_transfers"),
    "is_safe_owner": ("safe_ownership", "token_transfers"),
    "is_lp_provider": ("lp_in_pool",),
    "is_pool": ("pool_contains_token",),
    "is_lending_user": ("lending_user_to_reserve",),
    "is_validator_depositor": ("deposit_to_validator", "validator_controlled_by"),
    "has_dune_label": ("address_labeled_as",),
}


def profiles_for_address_roles(roles: dict[str, Any]) -> list[str]:
    """Map role flags from address_roles_current → graph profile ids."""
    if not roles:
        return []
    seen: list[str] = []
    for flag, profiles in _ROLE_TO_PROFILES.items():
        if roles.get(flag):
            for profile_id in profiles:
                if profile_id not in seen:
                    seen.append(profile_id)
    # A bare address with no known role still gets the universal transfer view.
    if not seen:
        seen.append("token_transfers")
    return seen


# ---------------------------------------------------------------------------
# Cross-sector hop suggestions (from semantic relationships)
# ---------------------------------------------------------------------------


def suggested_next_hops(node_kind: str, available_profiles: list[GraphProfile]) -> list[dict[str, str]]:
    """Cross-sector pivot suggestions for a node of the given kind.

    Two passes:
      1. Approved — any profile linked to this kind via a semantic
         relationship whose `via_entity` matches; stamped `approved`.
      2. Candidate — any profile whose `source_kind` or `target_kind`
         touches this kind and whose OTHER endpoint is a different kind
         (so it's a real cross-sector pivot, not a self-loop).

    Both passes skip profiles where both endpoints equal `node_kind`
    (same-kind; that's an in-sector expand, not a pivot).
    """
    snap = semantic_runtime.snapshot
    if snap is None or not node_kind:
        return []
    seen: set[str] = set()
    suggestions: list[dict[str, str]] = []

    def _label(profile_id: str) -> str:
        return profile_id.replace("_", " ")

    # Pass 1 — approved cross-sector hops via semantic.relationships.
    for rel in snap.relationships or []:
        if rel.get("via_entity") != node_kind:
            continue
        for profile in available_profiles:
            if profile.profile in seen:
                continue
            if node_kind not in (profile.source_kind, profile.target_kind):
                continue
            other = (
                profile.target_kind if profile.source_kind == node_kind else profile.source_kind
            )
            if other == node_kind:
                continue
            seen.add(profile.profile)
            suggestions.append(
                {
                    "profile": profile.profile,
                    "label": _label(profile.profile),
                    "rationale": rel.get("name") or "approved relationship",
                    "quality_tier": rel.get("quality_tier", "approved"),
                    "target_kind": other,
                }
            )

    # Pass 2 — candidate pivots. Any profile that bridges node_kind
    # to a different kind, regardless of explicit semantic relationship.
    for profile in available_profiles:
        if profile.profile in seen:
            continue
        if node_kind not in (profile.source_kind, profile.target_kind):
            continue
        other = (
            profile.target_kind if profile.source_kind == node_kind else profile.source_kind
        )
        if other == node_kind:
            continue
        seen.add(profile.profile)
        suggestions.append(
            {
                "profile": profile.profile,
                "label": _label(profile.profile),
                "rationale": f"{profile.source_kind} \u2192 {profile.target_kind}",
                "quality_tier": profile.quality_tier or "candidate",
                "target_kind": other,
            }
        )

    return suggestions
