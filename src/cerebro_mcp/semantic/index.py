from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def token_overlap(query: str, blob: str) -> int:
    query_tokens = set(normalize(query).split())
    blob_tokens = set(normalize(blob).split())
    return len(query_tokens & blob_tokens)


# ──────────────────────────────────────────────────────────────────────
# Token IDF weighting (used by score_metric)
# ──────────────────────────────────────────────────────────────────────
# Without weighting, score_metric treats every matched token as +5
# regardless of how informative it is — so a generic query like "weekly"
# scores all weekly-grain metrics the same, and a rare-token match (e.g.
# "passkey") scores no higher than a common one (e.g. "users"). We fix
# this with an idf-style weight: rarer tokens contribute more.
#
# Cap is per-token: even a singleton-token bump can't outscore the
# exact-name (100) or exact-synonym (90) shortcut paths.
_TOKEN_BONUS_CAP = 15
# Floor: a token that appears in every metric still gives a small bump
# (so "weekly" matched against a weekly-tagged blob still beats no
# match at all). The min floor here matches the previous flat +5 weight.
_TOKEN_BONUS_FLOOR = 5

_TOKEN_RE = re.compile(r"\w+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(normalize(text)))


def build_token_idf(
    metrics: Iterable[Mapping[str, Any]],
) -> dict[str, float]:
    """Compute a document-frequency-based idf weight per token across all
    metric `search_blob` fields.

    Returns ``{token: weight}`` where ``weight`` is bounded by
    ``_TOKEN_BONUS_FLOOR`` and ``_TOKEN_BONUS_CAP``. Tokens absent from
    the table fall back to the floor — the cost of not seeing them is
    just that they can't earn the cap.

    Callers should compute this once per snapshot and pass it to
    ``score_metric`` for every candidate metric. Computing per-call is
    O(N_metrics × |blob|) — fine for snapshots of a few hundred metrics
    but worth caching for larger projects.
    """
    df: dict[str, int] = defaultdict(int)
    total = 0
    for metric in metrics:
        total += 1
        for token in _tokens(metric.get("search_blob", "") or ""):
            df[token] += 1
    if total == 0:
        return {}
    # idf = log(N / df). Multiply by floor so the smallest non-zero
    # weight matches the legacy flat-5 score (preserves existing
    # rankings for tokens that appear everywhere) and the rarest tokens
    # approach the cap.
    weights: dict[str, float] = {}
    for token, freq in df.items():
        idf = math.log((total + 1) / (freq + 1)) + 1.0
        weight = _TOKEN_BONUS_FLOOR * idf
        weights[token] = max(_TOKEN_BONUS_FLOOR, min(_TOKEN_BONUS_CAP, weight))
    return weights


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


def score_metric(
    query: str,
    metric: dict[str, Any],
    token_idf: Mapping[str, float] | None = None,
) -> int:
    """Rank `metric` for an analyst free-text `query`.

    Scoring stack:
        100  Query equals the metric `name`.
         90  Query equals one of the metric's normalized synonyms.
         50  Metric name starts with the query.
         25  Query is a substring of the metric's search blob.
          0  None of the above (still eligible for the token bonus).

    Plus, additively:
        token bonus           Per-token idf weight from `token_idf` (or a
                              flat +5 each if no idf table is provided,
                              matching legacy behaviour).
        +20 approved          Promotes vetted metrics over candidates.
        +15 module match      When the query mentions an inferred module
                              and the metric belongs to it.

    Passing a `token_idf` produced by `build_token_idf(snapshot.metrics)`
    differentiates rare-token matches (e.g. "passkey") from common ones
    (e.g. "weekly"). When omitted, scoring is exactly the same as the
    pre-PR-6 implementation — safe to call from legacy paths that
    haven't been updated to pass the idf table.
    """
    q = normalize(query)
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

    matched_tokens = _tokens(q) & _tokens(metric.get("search_blob", "") or "")
    if score == 0 and not matched_tokens:
        return 0

    # Field-weighted token bonus. Tokens that match the metric's curated
    # TOPICAL fields (name / label / question-synonyms) earn full idf weight;
    # tokens that match ONLY in the free-text description are heavily
    # discounted. Without this, a verbose description makes an off-topic metric
    # rank for generic query tokens — e.g. `bridge_netflow_weekly_by_bridge`,
    # whose description happens to mention "active users", scoring 76 for
    # "gnosis app active users" purely on description prose. Standard IR
    # title-vs-body weighting: the name/label/synonyms are the signal, the
    # description is context.
    topical_tokens = _tokens(metric.get("name", "")) | _tokens(metric.get("label", "") or "")
    for _syn in metric.get("all_synonyms", []) or []:
        topical_tokens |= _tokens(_syn)
    for token in matched_tokens:
        weight = (
            token_idf.get(token, _TOKEN_BONUS_FLOOR)
            if token_idf is not None
            else _TOKEN_BONUS_FLOOR
        )
        if token in topical_tokens:
            score += int(weight)
        else:
            # description-only match: keep a weak recall signal, but never
            # enough to lift an off-topic metric over a topical one.
            score += int(min(2, weight * 0.25))

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
