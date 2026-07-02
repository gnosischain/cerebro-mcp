"""Graph profile discovery for the Graph Explorer mini-app.

Reads `cerebro.graph` metadata off the compiled semantic registry (the
snapshot exposed by `semantic_loader.semantic_runtime`) and turns each
graph-enabled model into a `GraphProfile`. No per-domain knowledge lives
here — everything comes from the dbt-cerebro semantic authoring layer.
"""

from __future__ import annotations

import logging
from typing import Any

from cerebro_mcp.loaders.semantic import semantic_runtime
from cerebro_mcp.semantic.graph_extraction import (
    _CONTROL_KEYS,
    GraphExtractionError,
    GraphProfile,
    extract_graph_profile,
)

logger = logging.getLogger(__name__)

# Re-exported for backward compatibility — the canonical definitions now live in
# `graph_extraction` (the single, pure place that reads the raw graph block).
__all__ = [
    "GraphProfile",
    "discover_profiles",
    "build_kind_index",
    "profile_by_id",
    "profiles_for_kind",
]


def discover_profiles(models: dict[str, Any] | None = None) -> list[GraphProfile]:
    """Build the list of graph profiles.

    Callers in hot paths pass ``models=None`` and get the list cached on the
    active snapshot (built once in ``SemanticRuntime._build_snapshot``), avoiding
    an O(N_models) rescan per call. The snapshot builder passes ``models`` in
    explicitly to derive the list before the snapshot exists. A snapshot without
    the cached field (e.g. older fakes) transparently falls back to a live scan.
    """
    if models is None:
        snap = semantic_runtime.snapshot
        if snap is None:
            return []
        cached = getattr(snap, "graph_profiles", None)
        if cached:
            return list(cached)
        models = snap.models
    profiles: list[GraphProfile] = []
    for name, model in models.items():
        try:
            profile = extract_graph_profile(name, model)
        except GraphExtractionError as exc:
            # Malformed-but-enabled block: historically skipped silently; now
            # observable (D4). The dbt validator is the authoritative gate.
            logger.warning("skipping graph profile: %s", exc)
            continue
        if profile is not None:
            profiles.append(profile)
    profiles.sort(key=lambda p: (p.module, p.profile))
    return profiles


def current_snapshot():
    """Active semantic snapshot (or None). Single accessor so tools resolve the
    snapshot through this module — which tests patch via `semantic_runtime`."""
    return semantic_runtime.snapshot


def build_kind_index(
    profiles: tuple[GraphProfile, ...] | list[GraphProfile],
) -> dict[str, tuple[GraphProfile, ...]]:
    """Map each node kind to the profiles that touch it (source or target).

    Built once at snapshot time so ``profiles_for_kind`` is an O(1) lookup. Using
    a set over ``{source_kind, target_kind}`` dedups self-referential profiles
    (e.g. circles_avatar -> circles_avatar) so a profile appears once per kind.
    """
    index: dict[str, list[GraphProfile]] = {}
    for profile in profiles:
        for kind in {profile.source_kind, profile.target_kind}:
            if kind:
                index.setdefault(kind, []).append(profile)
    return {kind: tuple(items) for kind, items in index.items()}


def profile_by_id(profile_id: str) -> GraphProfile | None:
    snap = semantic_runtime.snapshot
    cached = getattr(snap, "profiles_by_id", None) if snap is not None else None
    if cached:
        return cached.get(profile_id)
    for profile in discover_profiles():
        if profile.profile == profile_id:
            return profile
    return None


def profiles_for_kind(node_kind: str) -> list[GraphProfile]:
    snap = semantic_runtime.snapshot
    cached = getattr(snap, "kind_to_profiles", None) if snap is not None else None
    if cached:
        return list(cached.get(node_kind, ()))
    return [
        profile
        for profile in discover_profiles()
        if node_kind in (profile.source_kind, profile.target_kind)
    ]


# ---------------------------------------------------------------------------
# SQL assembly
# ---------------------------------------------------------------------------


def _default_filter_clauses(profile: GraphProfile) -> list[str]:
    """Translate a profile's `default_filters` meta into SQL WHERE conditions.

    Authored in dbt-cerebro under `cerebro.graph.default_filters` as a mapping
    of `column -> predicate`. Supported predicate forms:

      * ``"not_null_or_empty"`` -> ``col IS NOT NULL AND toString(col) != ''``
        (drops synthetic/empty endpoints, e.g. a Circles avatar with no inviter
        where ``invited_by`` is NULL/empty for Groups & Orgs).
      * ``"valid_address"`` -> the above PLUS excluding the zero address
        ``0x0000…0000``. Use for address-graph endpoints where the zero
        address is a genesis/migration sentinel that would otherwise collapse
        into one giant artificial hub (e.g. Circles invitation roots).
      * any other scalar -> ``toString(col) = '<value>'`` equality.

    Unknown shapes are skipped rather than raising, so a malformed authoring
    block degrades gracefully instead of breaking the whole graph.
    """
    zero_addr = "0x0000000000000000000000000000000000000000"
    clauses: list[str] = []
    for col, predicate in (profile.default_filters or {}).items():
        if not isinstance(col, str) or not col:
            continue
        # Control/pagination params are never column filters — emitting them as
        # `toString(col) = 'val'` crashes ClickHouse (unknown identifier).
        if col in _CONTROL_KEYS:
            continue
        if predicate == "not_null_or_empty":
            clauses.append(f"({col} IS NOT NULL AND toString({col}) != '')")
        elif predicate == "valid_address":
            clauses.append(
                f"({col} IS NOT NULL AND toString({col}) != '' "
                f"AND lower(toString({col})) != '{zero_addr}')"
            )
        elif isinstance(predicate, (str, int, float)) and predicate != "":
            literal = str(predicate).replace("'", "''")
            clauses.append(f"toString({col}) = '{literal}'")
    return clauses


# Defense-in-depth for the SQL builders: column/relation names are interpolated
# verbatim into ClickHouse SQL. The dbt build gate already rejects these tokens
# in authored graph metadata; this is the second line of defence at query time
# (a registry that bypassed the gate, or a hand-edited local one, still can't
# inject). The tools catch the raised error and degrade to a warning.
_DANGEROUS_SQL_TOKENS = (";", "--", "/*", "*/")


def _reject_unsafe_identifiers(*values: str | None) -> None:
    for value in values:
        if value and any(tok in value for tok in _DANGEROUS_SQL_TOKENS):
            raise ValueError(f"unsafe SQL identifier in graph profile: {value!r}")


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
    _reject_unsafe_identifiers(src, tgt, rel, profile.time_column, profile.weight_column)

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

    # Profile-authored guards (e.g. drop NULL/empty inviter endpoints).
    filter_clause = "".join(f" AND {c}" for c in _default_filter_clauses(profile))

    # Project endpoints as String too — keeps downstream node-id assembly uniform
    # across profiles with mixed scalar/string endpoint types.
    sql = f"""
        SELECT
            toString({src}) AS source_id,
            toString({tgt}) AS target_id,
            {weight_expr} AS weight,
            count() AS edge_count
        FROM {rel}
        WHERE ({where_clause}){time_clause}{filter_clause}
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
    _reject_unsafe_identifiers(src, tgt, rel, profile.time_column, profile.weight_column)

    params: dict[str, Any] = {"lim": int(limit)}
    where_bits: list[str] = []
    if profile.time_column:
        where_bits.append(f"{profile.time_column} >= now() - INTERVAL {{win:UInt32}} DAY")
        params["win"] = int(max(1, window_days))
    where_bits.extend(_default_filter_clauses(profile))
    where_clause = f" WHERE {' AND '.join(where_bits)}" if where_bits else ""

    weight_expr = f"sum({profile.weight_column})" if profile.weight_column else "toFloat64(count())"
    sql = f"""
        SELECT
            toString({src}) AS source_id,
            toString({tgt}) AS target_id,
            {weight_expr} AS weight,
            count() AS edge_count
        FROM {rel}{where_clause}
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
    _reject_unsafe_identifiers(src, tgt, model)
    params: dict[str, Any] = {"src": source_id, "tgt": target_id, "lim": int(limit)}
    sql = f"""
        SELECT *
        FROM {model}
        WHERE {src} = {{src:String}} AND {tgt} = {{tgt:String}}
        LIMIT {{lim:UInt32}}
    """
    return sql, params


def build_node_flow_sql(
    profile: GraphProfile,
    *,
    node_ids: list[str],
    window_days: int = 90,
    exclude_self_loops: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Per-NODE inbound/outbound weighted flow for `node_ids` (WS7).

    Returns ``(node_id, outflow, inflow)`` rows. Outflow is the weighted sum of
    edges leaving the node (GROUP BY source); inflow is the weighted sum arriving
    (GROUP BY target). Self-loops (source == target) are excluded by default so a
    node's circular flow is not counted as exiting flow (D5). Falls back to
    ``count()`` weight when the profile has no ``weight_column``.
    """
    src = profile.source_column
    tgt = profile.target_column
    rel = profile.relation_name or profile.model_name
    _reject_unsafe_identifiers(src, tgt, rel, profile.time_column, profile.weight_column)
    params: dict[str, Any] = {"node_ids": [str(n) for n in node_ids]}
    # Wrap the weighted aggregate in toFloat64 so the inflow/outflow legs unify
    # with the toFloat64(0) constant in the UNION (a UInt64 weight_column else
    # collides: ClickHouse NO_COMMON_TYPE for Float64 vs UInt64).
    weight_expr = (
        f"toFloat64(sum({profile.weight_column}))" if profile.weight_column else "toFloat64(count())"
    )

    time_clause = ""
    if profile.time_column:
        time_clause = f" AND {profile.time_column} >= now() - INTERVAL {{win:UInt32}} DAY"
        params["win"] = int(max(1, window_days))
    self_clause = f" AND toString({src}) != toString({tgt})" if exclude_self_loops else ""
    filter_clause = "".join(f" AND {c}" for c in _default_filter_clauses(profile))

    sql = f"""
        SELECT node_id, sum(outflow) AS outflow, sum(inflow) AS inflow
        FROM (
            SELECT toString({src}) AS node_id, {weight_expr} AS outflow, toFloat64(0) AS inflow
            FROM {rel}
            WHERE toString({src}) IN {{node_ids:Array(String)}}{time_clause}{self_clause}{filter_clause}
            GROUP BY node_id
            UNION ALL
            SELECT toString({tgt}) AS node_id, toFloat64(0) AS outflow, {weight_expr} AS inflow
            FROM {rel}
            WHERE toString({tgt}) IN {{node_ids:Array(String)}}{time_clause}{self_clause}{filter_clause}
            GROUP BY node_id
        )
        GROUP BY node_id
    """
    return sql, params


# ---------------------------------------------------------------------------
# Role-based profile inference
# ---------------------------------------------------------------------------


_ROLE_TO_PROFILES: dict[str, tuple[str, ...]] = {
    "is_circles_avatar": (
        "circles_trust",
        "circles_invitation",
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
