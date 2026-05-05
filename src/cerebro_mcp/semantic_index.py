from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Sequence


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def token_overlap(query: str, blob: str) -> int:
    query_tokens = set(normalize(query).split())
    blob_tokens = set(normalize(blob).split())
    return len(query_tokens & blob_tokens)


def infer_module_from_query(query: str) -> str:
    tokens = set(normalize(query).split())
    for module in (
        "execution",
        "consensus",
        "bridges",
        "p2p",
        "contracts",
        "esg",
        "probelab",
        "crawlers_data",
    ):
        if module in tokens:
            return module
    return ""


def build_indexes(registry: dict[str, Any]) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    synonym_index: dict[str, str] = {}
    dimension_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metrics: dict[str, dict[str, Any]] = {}

    for metric_name, metric in registry.get("metrics", {}).items():
        synonyms = [metric_name, metric.get("label", ""), *metric.get("question_synonyms", [])]
        search_blob = " ".join(
            filter(
                None,
                [
                    metric_name,
                    metric.get("label", ""),
                    metric.get("description", ""),
                    metric.get("module", ""),
                    *metric.get("question_synonyms", []),
                ],
            )
        )
        metrics[metric_name] = {
            **metric,
            "all_synonyms": [normalize(value) for value in synonyms if value],
            "search_blob": normalize(search_blob),
        }
        for synonym in metrics[metric_name]["all_synonyms"]:
            synonym_index[synonym] = metric_name

    for model_name, model in registry.get("models", {}).items():
        for dimension in model.get("dimensions", []):
            dimension_index[dimension["name"]].append(
                {
                    "provider_model": model_name,
                    "module": model.get("module", ""),
                    "dimension": dimension,
                    "semantic_status": model.get("semantic_status", ""),
                }
            )

    return synonym_index, dict(dimension_index), metrics


def score_metric(query: str, metric: dict[str, Any]) -> int:
    q = normalize(query)
    overlap = token_overlap(q, metric["search_blob"])
    if q == metric["name"]:
        score = 100
    elif q in metric["all_synonyms"]:
        score = 90
    elif metric["name"].startswith(q):
        score = 50
    elif q in metric["search_blob"]:
        score = 25
    else:
        score = 0
    if score == 0 and overlap == 0:
        return 0
    score += 5 * overlap
    if metric.get("quality_tier") == "approved":
        score += 20
    if metric.get("module") == infer_module_from_query(q):
        score += 15
    return score


def rrf_fuse(
    rankings: Sequence[Iterable[str]],
    k: int = 60,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    Each input is an ordered iterable of item identifiers (e.g. model names),
    best-first. The output is a single fused ranking sorted by descending RRF
    score. The constant `k=60` is the value from the original Cormack/Clarke/
    Buettcher (2009) paper; it dampens the contribution of low-rank items so
    that an item appearing at rank 1 in any list dominates one appearing at
    rank 30 in two lists, which is what we want when mixing keyword + token
    overlap rankings of dbt models.

    Items missing from a ranking simply contribute 0 from that list — there
    is no penalty for absence. Duplicates within a single ranking are
    collapsed (only the first occurrence's rank counts).
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, name in enumerate(ranking, start=1):
            if name in seen:
                continue
            seen.add(name)
            scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if top_k is not None:
        fused = fused[:top_k]
    return fused
