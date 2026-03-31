from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from cerebro_mcp.semantic_models import ig


MAX_JOIN_HOPS = 3


class PlanningError(ValueError):
    pass


@dataclass(frozen=True)
class PathResult:
    models: tuple[str, ...]
    edges: tuple[dict[str, Any], ...]
    cost: float


_PATH_CACHE: dict[tuple[str, str, str, int], PathResult] = {}


def _invert_cardinality(cardinality: str) -> str:
    mapping = {
        "many_to_one": "one_to_many",
        "one_to_many": "many_to_one",
        "one_to_one": "one_to_one",
    }
    return mapping.get(cardinality, cardinality)


def _base_cost(cardinality: str) -> float:
    return {
        "many_to_one": 1.0,
        "one_to_one": 1.2,
        "one_to_many": 5.0,
    }.get(cardinality, 5.0)


def _edge_cost(relationship: dict[str, Any], *, reverse: bool) -> float:
    cardinality = _invert_cardinality(relationship.get("cardinality", "")) if reverse else relationship.get("cardinality", "")
    cost = _base_cost(cardinality)
    if relationship.get("preferred_bridge"):
        cost = min(cost, 0.5)
    return cost


def build_semantic_graph(
    models: dict[str, dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> tuple[Any, dict[str, int]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    vertices = sorted(models.keys())
    vertex_ids = {name: idx for idx, name in enumerate(vertices)}

    for relationship in relationships:
        if relationship.get("quality_tier") != "approved":
            continue
        left_model = relationship.get("left_model", "")
        right_model = relationship.get("right_model", "")
        if left_model not in models or right_model not in models:
            continue
        if models[left_model].get("semantic_status") != "approved":
            continue
        if models[right_model].get("semantic_status") != "approved":
            continue

        forward = {
            "relationship": relationship,
            "name": relationship.get("name", ""),
            "source": left_model,
            "target": right_model,
            "left_keys": relationship.get("left_keys", []),
            "right_keys": relationship.get("right_keys", []),
            "cardinality": relationship.get("cardinality", ""),
            "cost": _edge_cost(relationship, reverse=False)
            + (
                0.5
                if models[left_model].get("module") != models[right_model].get("module")
                else 0.0
            ),
        }
        reverse = {
            "relationship": relationship,
            "name": relationship.get("name", ""),
            "source": right_model,
            "target": left_model,
            "left_keys": relationship.get("right_keys", []),
            "right_keys": relationship.get("left_keys", []),
            "cardinality": _invert_cardinality(relationship.get("cardinality", "")),
            "cost": _edge_cost(relationship, reverse=True)
            + (
                0.5
                if models[left_model].get("module") != models[right_model].get("module")
                else 0.0
            ),
        }
        adjacency[left_model].append(forward)
        adjacency[right_model].append(reverse)

    graph_bundle: dict[str, Any] = {"adjacency": dict(adjacency)}
    if ig is not None:
        graph = ig.Graph(directed=True)
        graph.add_vertices(vertices)
        graph.add_edges(
            [
                (edge["source"], edge["target"])
                for edges in adjacency.values()
                for edge in edges
            ]
        )
        graph.es["cost"] = [
            edge["cost"]
            for edges in adjacency.values()
            for edge in edges
        ]
        graph_bundle["igraph"] = graph
    return graph_bundle, vertex_ids


def _search_paths(
    adjacency: dict[str, list[dict[str, Any]]],
    source_model: str,
    target_model: str,
    *,
    max_hops: int,
) -> list[PathResult]:
    heap: list[tuple[float, str, tuple[str, ...], tuple[dict[str, Any], ...]]] = [
        (0.0, source_model, (source_model,), ())
    ]
    best_cost: float | None = None
    found: list[PathResult] = []

    while heap:
        cost, node, models, edges = heapq.heappop(heap)
        hop_count = len(models) - 1
        if best_cost is not None and cost > best_cost:
            break
        if hop_count > max_hops:
            continue
        if node == target_model and hop_count > 0:
            best_cost = cost if best_cost is None else best_cost
            found.append(PathResult(models=models, edges=edges, cost=cost))
            continue

        for edge in adjacency.get(node, []):
            target = edge["target"]
            if target in models:
                continue
            heapq.heappush(
                heap,
                (
                    cost + float(edge["cost"]),
                    target,
                    models + (target,),
                    edges + (edge,),
                ),
            )
    return found


def find_safest_path(
    registry_hash: str,
    graph: Any,
    source_model: str,
    target_model: str,
    *,
    max_hops: int = MAX_JOIN_HOPS,
) -> PathResult:
    cache_key = (registry_hash, source_model, target_model, max_hops)
    if cache_key in _PATH_CACHE:
        return _PATH_CACHE[cache_key]

    adjacency = graph.get("adjacency", {})
    found = _search_paths(adjacency, source_model, target_model, max_hops=max_hops)
    if not found:
        raise PlanningError(f"No semantic path found from {source_model} to {target_model}")

    best = found[0]
    materially_different = [
        candidate
        for candidate in found[1:]
        if candidate.cost == best.cost and candidate.models != best.models
    ]
    if materially_different:
        raise PlanningError(
            f"Ambiguous semantic path found from {source_model} to {target_model}"
        )

    _PATH_CACHE[cache_key] = best
    return best
