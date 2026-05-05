"""BM25 keyword search over dbt models and their columns.

This module provides keyword-based ranking that complements the token-overlap
scorer in `semantic_index.py`. The two are fused via Reciprocal Rank Fusion
(RRF) so that exact-name matches mathematically rise to the top while still
retaining domain weights (synonym tables, module affinity) from the existing
scorer.

Two indices are exposed:

- `BM25Index` — ranks dbt **models** by relevance to a free-text query.
  Built from each model's `search_index` blob (name + description + tags + owner).

- `ColumnBM25Index` — ranks **columns within a single model** by relevance to a
  query. Used by the SQL compiler to keep prompt context small on wide tables
  (100+ columns) without losing the join keys / date columns the model actually
  needs.

Both indices are pure-Python (rank_bm25), pickle-friendly, and built once per
manifest reload — see `ManifestLoader._build_indexes_internal`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rank_bm25 import BM25Okapi

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + split on non-alphanumerics. Keeps the tokenization
    deterministic across processes (important for the Phase 4 worker pool)."""
    return _TOKEN_RE.findall((text or "").lower())


@dataclass(frozen=True)
class BM25Doc:
    """A model-level document for BM25 ranking.

    `model_name` is the lookup key the caller will receive back; `text` is the
    blob fed to the tokenizer (typically `name + description + tags + owner`).
    """

    model_name: str
    text: str


@dataclass(frozen=True)
class ColumnDoc:
    """A column-level document for BM25 ranking within a model."""

    model_name: str
    column_name: str
    text: str  # column_name + data_type + description


class BM25Index:
    """Model-level BM25 index. Empty corpora are tolerated and return [] from search()."""

    def __init__(self, docs: Iterable[BM25Doc]) -> None:
        self._docs: list[BM25Doc] = list(docs)
        self._corpus: list[list[str]] = [_tokenize(d.text) for d in self._docs]
        # rank_bm25 raises on an empty corpus; guard so an empty manifest
        # (e.g. tests, first boot) doesn't crash the server.
        self._bm25: BM25Okapi | None = (
            BM25Okapi(self._corpus) if self._corpus else None
        )

    def __len__(self) -> int:
        return len(self._docs)

    def search(self, query: str, top_k: int = 50) -> list[tuple[str, float]]:
        """Return top-K `(model_name, score)` ranked descending. Zero-score
        documents are dropped — they didn't match any query token."""
        if self._bm25 is None:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(zip(self._docs, scores), key=lambda x: x[1], reverse=True)
        return [(d.model_name, float(s)) for d, s in ranked[:top_k] if s > 0]

    def ranking(self, query: str, top_k: int = 50) -> list[str]:
        """Convenience wrapper for RRF — returns just the ordered model names."""
        return [name for name, _ in self.search(query, top_k=top_k)]


class ColumnBM25Index:
    """Per-model column index. One physical BM25 model holds all columns
    across all dbt models; we filter to a single model's columns at query
    time. This is much cheaper than building one BM25 per model.
    """

    def __init__(self, columns: Iterable[ColumnDoc]) -> None:
        self._cols: list[ColumnDoc] = list(columns)
        self._by_model: dict[str, list[int]] = {}
        for i, c in enumerate(self._cols):
            self._by_model.setdefault(c.model_name, []).append(i)
        corpus = [_tokenize(c.text) for c in self._cols]
        self._bm25: BM25Okapi | None = BM25Okapi(corpus) if corpus else None

    def __len__(self) -> int:
        return len(self._cols)

    def top_columns_for_model(
        self,
        model_name: str,
        query: str,
        top_k: int = 20,
    ) -> list[str]:
        """Return up to `top_k` column names from `model_name` ranked by
        BM25 against `query`. Returns [] if the model has no indexed columns
        or the query has no usable tokens."""
        idxs = self._by_model.get(model_name, [])
        if not idxs or self._bm25 is None:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        # Sort the model's own column indexes by score, descending.
        ranked = sorted(idxs, key=lambda i: scores[i], reverse=True)
        # Drop zero-score hits — they had no token overlap at all and would
        # just be noise. Keep the order, return names.
        return [self._cols[i].column_name for i in ranked[:top_k] if scores[i] > 0]

    def model_has_columns(self, model_name: str) -> bool:
        return bool(self._by_model.get(model_name))


def build_bm25_indices_from_manifest_data(
    models: dict[str, dict],
    search_blobs: dict[str, str],
) -> tuple[BM25Index, ColumnBM25Index]:
    """Construct both indices from manifest internals.

    `models` is the parsed dbt model node dict (from manifest.json).
    `search_blobs` is the precomputed `name + desc + tags + owner` per model
    (already built by `ManifestLoader._build_indexes_internal`).
    """
    model_docs: list[BM25Doc] = []
    column_docs: list[ColumnDoc] = []
    for model_name, node in models.items():
        blob = search_blobs.get(model_name, model_name.lower())
        model_docs.append(BM25Doc(model_name=model_name, text=blob))
        for col_name, col_meta in (node.get("columns") or {}).items():
            data_type = (col_meta or {}).get("data_type", "") or ""
            description = (col_meta or {}).get("description", "") or ""
            column_docs.append(
                ColumnDoc(
                    model_name=model_name,
                    column_name=col_name,
                    text=f"{col_name} {data_type} {description}",
                )
            )
    return BM25Index(model_docs), ColumnBM25Index(column_docs)
