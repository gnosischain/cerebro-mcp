"""Graph traversal: THE one bounded BFS + canonical edge identity + merging.

Both traversal consumers run through :func:`bfs_expand`:

- ``explore_neighborhood`` (public data tool): ``kind_partition=False`` — one
  batched query per (profile, whole frontier) per hop, the caller's direction
  passed straight through, a simple ``node_cap`` with a ``truncated`` flag.
  Pinned by characterization goldens; behavior must not drift.
- ``expand_graph_explorer_node`` (UI tool): ``kind_partition=True`` — the
  frontier is partitioned by node kind and each (kind group, compatible
  profile) pair issues ONE batched query (strictly fewer queries than the old
  per-node loop), with per-hop budgets and ``truncated_at_hop`` semantics.

Unknown-kind rule: frontier entries with kind ``""`` query ALL chosen
profiles on that hop; kinds are learned from returned nodes for later hops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.semantic.graph_profiles import GraphProfile

from .fetch import fetch_profile_edges, pick_direction


@dataclass
class TraversalResult:
    """Accumulated result of a bounded BFS walk."""

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)
    profiles_used: set[str] = field(default_factory=set)
    hops_completed: int = 0
    # Simple-cap mode (per_hop_budget=None): the max_nodes cap was hit.
    truncated: bool = False
    # Budget mode: the hop at which the walk hit the cap/budget with frontier
    # left over (None = no genuine truncation).
    truncated_at_hop: int | None = None
    # Frontier ids whose neighborhood fetches actually RAN (a truncated round
    # skips the remaining groups — those ids must stay expandable so the next
    # round can pick them up instead of stranding them forever).
    expanded_frontier: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def _accumulate_edge(
    edges: dict[str, dict[str, Any]], edge: dict[str, Any]
) -> None:
    """Insert an edge under its canonical id; undirected reciprocals sum."""
    eid, src, tgt = canonical_edge_id(
        edge["profile"], edge["source"], edge["target"], edge.get("directed", True)
    )
    existing = edges.get(eid)
    if existing is not None:
        if not edge.get("directed", True):
            incoming_weight = edge.get("weight")
            if existing.get("weight") is None:
                existing["weight"] = incoming_weight
            elif incoming_weight is not None:
                existing["weight"] += incoming_weight
            existing["edge_count"] += edge["edge_count"]
        return
    edges[eid] = {
        "id": eid,
        "source": src,
        "target": tgt,
        "profile": edge["profile"],
        "weight": edge["weight"],
        "edge_count": edge["edge_count"],
        "directed": edge.get("directed", True),
    }


def _merge_node(
    nodes: dict[str, dict[str, Any]], node: dict[str, Any]
) -> bool:
    """Merge one node dict into the accumulator; True when it was NEW."""
    existing = nodes.get(node["id"])
    if existing is None:
        nodes[node["id"]] = node
        return True
    for prof_id in node.get("profiles", []):
        if prof_id not in existing["profiles"]:
            existing["profiles"].append(prof_id)
    return False


def bfs_expand(
    ch: ClickHouseManager,
    *,
    frontier: list[tuple[str, str]],
    chosen_profiles: list[GraphProfile],
    direction: str = "both",
    auto_direction: bool = False,
    kind_partition: bool = False,
    hops: int = 1,
    window_days: int,
    per_query_limit: int,
    node_cap: int,
    per_hop_budget: int | None = None,
    initial_nodes: dict[str, dict[str, Any]] | None = None,
    initial_edges: dict[str, dict[str, Any]] | None = None,
    visited: set[str] | None = None,
    fetch: Callable[..., tuple[list[dict], list[dict], list[str]]] | None = None,
) -> TraversalResult:
    """THE bounded multi-hop BFS (see module docstring for the two modes).

    ``frontier`` entries are ``(node_id, kind)``; ``kind == ""`` means
    unknown — such entries query ALL ``chosen_profiles`` on their hop.
    ``initial_nodes``/``initial_edges`` pre-seed the accumulators (the UI
    expand path seeds them from the datasets already on canvas). ``visited``
    ids are never re-enqueued. ``fetch`` is injectable for tests.
    """
    fetch = fetch or fetch_profile_edges
    result = TraversalResult(
        nodes=dict(initial_nodes or {}),
        edges=dict(initial_edges or {}),
    )
    seen: set[str] = set(visited or ())
    already_expanded: set[str] = set()
    current: list[tuple[str, str]] = list(frontier)
    hops_to_run = max(1, int(hops or 1))

    for hop_round in range(hops_to_run):
        if not current:
            break
        remaining_global = node_cap - len(result.nodes)
        if remaining_global <= 0:
            if per_hop_budget is not None:
                result.truncated_at_hop = hop_round
            result.truncated = True
            break
        round_budget = (
            min(remaining_global, per_hop_budget)
            if per_hop_budget is not None
            else remaining_global
        )
        nodes_at_round_start = len(result.nodes)
        next_frontier: list[tuple[str, str]] = []
        enqueued: set[str] = set()
        round_truncated = False

        # Partition the frontier: by kind when requested (unknown "" is its
        # own group that queries all chosen profiles), else one group.
        groups: dict[str, list[str]] = {}
        for nid, nkind in current:
            if nid in already_expanded:
                continue
            already_expanded.add(nid)
            groups.setdefault(nkind if kind_partition else "", []).append(nid)

        for group_kind, group_ids in groups.items():
            if round_truncated:
                break
            # This group's fetches are about to run — record its ids as
            # consumed. Groups skipped by a mid-round truncation are NOT
            # recorded, so the caller can leave them expandable.
            result.expanded_frontier.update(group_ids)
            if kind_partition and group_kind:
                group_profiles = [
                    p
                    for p in chosen_profiles
                    if p.source_kind == group_kind or p.target_kind == group_kind
                ]
            else:
                group_profiles = chosen_profiles
            for profile in group_profiles:
                eff_dir = (
                    pick_direction(profile, group_kind)
                    if auto_direction and direction == "both"
                    else direction
                )
                try:
                    new_nodes, new_edges, warn = fetch(
                        ch,
                        profile,
                        seed_ids=group_ids,
                        direction=eff_dir,
                        window_days=window_days,
                        limit=per_query_limit,
                    )
                except Exception as exc:  # never let one profile abort the walk
                    result.warnings.append(f"{profile.profile}: {exc}")
                    continue
                result.warnings.extend(warn)
                if new_edges:
                    result.profiles_used.add(profile.profile)
                for node in new_nodes:
                    if node["id"] not in result.nodes and len(result.nodes) >= node_cap:
                        result.truncated = True
                        continue
                    is_new = _merge_node(result.nodes, node)
                    if node["id"] not in seen and node["id"] not in enqueued:
                        enqueued.add(node["id"])
                        next_frontier.append((node["id"], node.get("kind", "")))
                    # Budget applies to NEW nodes added this round.
                    if (
                        per_hop_budget is not None
                        and is_new
                        and len(result.nodes) - nodes_at_round_start >= round_budget
                    ):
                        round_truncated = True
                for edge in new_edges:
                    _accumulate_edge(result.edges, edge)
                if round_truncated:
                    break

        result.hops_completed = hop_round + 1
        seen |= {nid for nid, _ in next_frontier}
        current = next_frontier
        if per_hop_budget is not None:
            # Genuine truncation: budget hit AND frontier left unexpanded.
            if round_truncated and current:
                result.truncated_at_hop = hop_round + 1
                result.truncated = True
                break
        else:
            if len(result.nodes) >= node_cap:
                result.truncated = True
                break

    return result


def canonical_edge_id(
    profile: str, source: str, target: str, directed: bool
) -> tuple[str, str, str]:
    """Stable edge id + ordered endpoints.

    Directed edges keep ``profile:src->tgt``. Undirected edges (directed=False)
    collapse the reciprocal pair onto one canonical id ``profile:min|max`` so a
    B->A row is not stored as a distinct edge from A->B (Q6). Returns
    ``(edge_id, source, target)`` with endpoints reordered for undirected.
    """
    if directed:
        return f"{profile}:{source}->{target}", source, target
    a, b = sorted([str(source), str(target)])
    return f"{profile}:{a}|{b}", a, b


def merge_graph(
    existing_nodes: list[list[Any]],
    existing_edges: list[list[Any]],
    new_nodes: list[dict[str, Any]],
    new_edges: list[dict[str, Any]],
) -> tuple[list[list[Any]], list[list[Any]]]:
    node_index: dict[str, list[Any]] = {}
    for row in existing_nodes:
        if row and row[0]:
            node_index[str(row[0])] = list(row)
    for node in new_nodes:
        node_id = node["id"]
        if node_id in node_index:
            current_profiles = node_index[node_id][3] or []
            merged = list(current_profiles)
            for profile in node["profiles"]:
                if profile not in merged:
                    merged.append(profile)
            node_index[node_id][3] = merged
        else:
            node_index[node_id] = [
                node["id"],
                node["kind"],
                node["label"],
                node["profiles"],
            ]
    edge_rows = list(existing_edges)
    # Index existing edges by their (already-canonical) id so an incoming
    # undirected reciprocal sums into the existing row instead of being dropped.
    edge_pos = {row[0]: i for i, row in enumerate(edge_rows) if row}
    for edge in new_edges:
        eid, src, tgt = canonical_edge_id(
            edge["profile"], edge["source"], edge["target"], edge.get("directed", True)
        )
        if eid in edge_pos:
            if not edge.get("directed", True):
                row = edge_rows[edge_pos[eid]]
                incoming_weight = edge.get("weight")
                if row[4] is None:
                    row[4] = incoming_weight
                elif incoming_weight is not None:
                    row[4] += incoming_weight
                row[5] = (row[5] or 0) + edge["edge_count"]
            continue
        edge_rows.append(
            [
                eid,
                src,
                tgt,
                edge["profile"],
                edge["weight"],
                edge["edge_count"],
                edge["directed"],
            ]
        )
        edge_pos[eid] = len(edge_rows) - 1
    return list(node_index.values()), edge_rows
