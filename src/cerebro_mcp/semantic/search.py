"""Canonical model-search backend for the whole semantic surface.

Every model-search consumer (`catalog_search`, `find.top_models` via
catalog_search, `manifest.search_models`' BM25 leg, the Metric Lab catalog)
ranks through ONE `ModelSearchIndex` so that the same query returns the same
models everywhere. Consumers keep their own response shapes via thin
adapters — this module is a ranking backend only: it records nothing in
session state and never touches routing gates.

Design (per the agreed contract):

* One tokenizer, exported as :func:`tokenize` — lowercase, split on
  non-alphanumerics, SHORT TOKENS KEPT (`tx`, `l1`, `mev` matter here), and a
  light plural-strip stem so `bridges` matches `bridge`. Index and query use
  the same function, so stemming is symmetric.
* Field-weighted scoring: three BM25 legs combined as
  ``3.0 * name + 1.5 * aux (tags+columns+owner) + 1.0 * body (description,
  path)`` plus exact/prefix/substring name bonuses. Models finally get the
  title-vs-body treatment metrics always had.
* Fuzzy fallback (difflib on names) fires whenever the top score is WEAK,
  not only on zero hits — typo tolerance is uniform across tools.
* Column matches are ranked by shared-tokenizer overlap (deterministic at
  any corpus size), capped at 5 per hit, shaped
  ``[{"name": str, "score": float}]``.
* Instances are cached per ``registry_hash`` (single entry, like
  ``_CatalogIndex``).
"""

from __future__ import annotations

import difflib
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

from rank_bm25 import BM25Okapi


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Combined-score weights per field leg (contract 0.1).
_W_NAME = 3.0
_W_AUX = 1.5
_W_BODY = 1.0

# Name-match bonuses (mirrors catalog_search's proven constants).
_BONUS_EXACT = 6.0
_BONUS_PREFIX = 3.5
_BONUS_SUBSTRING = 1.5

# Fuzzy fallback engages when the best combined score is below this floor.
_FUZZY_SCORE_FLOOR = 2.0
_FUZZY_RATIO = 0.8

_MAX_COLUMN_MATCHES = 5


def _stem(token: str) -> str:
    """Light plural-strip: `bridges`→`bridge`, `balances`→`balance`,
    `address` stays (double-s). Deliberately no heavier stemming — model
    names are technical identifiers, not prose."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """THE shared tokenizer: lowercase, split on non-alphanumerics, keep
    short tokens, plural-strip stem. Used symmetrically for corpus and
    query by every search surface."""
    return [_stem(t) for t in _TOKEN_RE.findall((text or "").lower())]


@dataclass(frozen=True)
class FieldDoc:
    """Per-model field texts. Adapters build this from whichever source
    they have (semantic snapshot models or manifest nodes)."""

    name: str
    name_text: str  # model name + name split into parts
    aux_text: str  # tags + column names + owner + module/layer
    body_text: str  # description + path tokens + inference notes


@dataclass
class ModelHit:
    name: str
    score: float
    matched_fields: list[str] = field(default_factory=list)
    matched_columns: list[dict[str, Any]] = field(default_factory=list)


class _FieldBM25:
    """One BM25 leg over a single field of every document.

    BM25 alone zeroes out in tiny corpora (rank_bm25's IDF is exactly 0 when
    a term appears in half of a 2-doc corpus, and negative→epsilon for
    all-doc terms), so each leg blends a plain token-overlap floor:
    ``bm25 + 0.5 * |query ∩ doc|``. In large corpora BM25 dominates; in
    small ones matches still surface.
    """

    def __init__(self, texts: list[str]) -> None:
        corpus = [tokenize(t) for t in texts]
        self._token_sets = [set(tokens) for tokens in corpus]
        self._nonempty = any(corpus)
        self._bm25 = BM25Okapi(corpus) if self._nonempty and corpus else None

    def scores(self, query_tokens: list[str]) -> list[float]:
        if not query_tokens or not self._token_sets:
            return []
        bm25 = (
            [float(s) for s in self._bm25.get_scores(query_tokens)]
            if self._bm25 is not None
            else [0.0] * len(self._token_sets)
        )
        qset = set(query_tokens)
        return [
            s + 0.5 * len(qset & toks)
            for s, toks in zip(bm25, self._token_sets)
        ]


class ModelSearchIndex:
    """Field-weighted BM25 index over models. Build once per registry hash."""

    def __init__(
        self,
        docs: Iterable[FieldDoc],
        model_columns: dict[str, list[str]] | None = None,
    ) -> None:
        self._docs: list[FieldDoc] = list(docs)
        self._names = [d.name for d in self._docs]
        self._names_lower = [n.lower() for n in self._names]
        # name -> row index, so a candidate set (module/tag-filtered search)
        # maps to the rows to score without a full-corpus scan.
        self._cand_index = {name: i for i, name in enumerate(self._names)}
        self._name_leg = _FieldBM25([d.name_text for d in self._docs])
        self._aux_leg = _FieldBM25([d.aux_text for d in self._docs])
        self._body_leg = _FieldBM25([d.body_text for d in self._docs])
        # Column matching uses direct token overlap with the shared
        # tokenizer, NOT ColumnBM25Index — rank_bm25's IDF zeroes out in
        # small corpora and its zero-score drop would eat legitimate
        # matches. Overlap is deterministic at any corpus size.
        self._model_columns: dict[str, list[tuple[str, set[str]]]] = {}
        for model_name, cols in (model_columns or {}).items():
            self._model_columns[model_name] = [
                (c, set(tokenize(c))) for c in cols
            ]

    def __len__(self) -> int:
        return len(self._docs)

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
        include_column_matches: bool = False,
        candidates: set[str] | None = None,
    ) -> list[ModelHit]:
        """Rank models for ``query``.

        ``candidates`` (a set of model names) scopes scoring — and the fuzzy
        fallback — to just those rows, so a module/tag-filtered search skips
        the full-corpus loop instead of ranking everything then discarding
        non-matches. ``None`` scores the whole corpus (the fast default path).
        """
        q = (query or "").strip()
        if not q or not self._docs:
            return []
        q_lower = q.lower()
        q_tokens = tokenize(q)

        name_scores = self._name_leg.scores(q_tokens)
        aux_scores = self._aux_leg.scores(q_tokens)
        body_scores = self._body_leg.scores(q_tokens)

        if candidates is None:
            indices: Iterable[int] = range(len(self._names))
        else:
            indices = sorted(
                self._cand_index[n] for n in candidates if n in self._cand_index
            )

        hits: list[ModelHit] = []
        for i in indices:
            name = self._names[i]
            n = name_scores[i] if name_scores else 0.0
            a = aux_scores[i] if aux_scores else 0.0
            b = body_scores[i] if body_scores else 0.0
            score = _W_NAME * n + _W_AUX * a + _W_BODY * b

            nl = self._names_lower[i]
            if nl == q_lower:
                score += _BONUS_EXACT
            elif nl.startswith(q_lower):
                score += _BONUS_PREFIX
            elif q_lower in nl:
                score += _BONUS_SUBSTRING

            if score <= 0:
                continue
            matched = []
            if n > 0 or q_lower in nl:
                matched.append("name")
            if a > 0:
                matched.append("aux")
            if b > 0:
                matched.append("body")
            hits.append(ModelHit(name=name, score=score, matched_fields=matched))

        hits.sort(key=lambda h: (-h.score, h.name))

        # Fuzzy fallback: uniform typo tolerance when lexical matching is weak.
        if not hits or hits[0].score < _FUZZY_SCORE_FLOOR:
            if candidates is None:
                fuzzy_pool = self._names_lower
            else:
                fuzzy_pool = [self._names_lower[i] for i in indices]
            close = difflib.get_close_matches(
                q_lower, fuzzy_pool, n=limit, cutoff=_FUZZY_RATIO
            )
            existing = {h.name for h in hits}
            for match in close:
                name = self._names[self._names_lower.index(match)]
                if name in existing:
                    continue
                if candidates is not None and name not in candidates:
                    continue
                ratio = difflib.SequenceMatcher(None, q_lower, match).ratio()
                hits.append(
                    ModelHit(name=name, score=ratio, matched_fields=["fuzzy"])
                )
            hits.sort(key=lambda h: (-h.score, h.name))

        hits = hits[: max(1, limit)]

        if include_column_matches and self._model_columns:
            qset = set(q_tokens)
            for hit in hits:
                scored = [
                    (len(qset & toks), name)
                    for name, toks in self._model_columns.get(hit.name, [])
                    if qset & toks
                ]
                scored.sort(key=lambda pair: (-pair[0], pair[1]))
                hit.matched_columns = [
                    {"name": name, "score": float(overlap)}
                    for overlap, name in scored[:_MAX_COLUMN_MATCHES]
                ]
        return hits

    # ------------------------------------------------------------------
    # Constructors + cache
    # ------------------------------------------------------------------

    @classmethod
    def from_field_docs(
        cls,
        docs: Iterable[FieldDoc],
        model_columns: dict[str, list[str]] | None = None,
    ) -> "ModelSearchIndex":
        return cls(docs, model_columns=model_columns)

    @classmethod
    def for_snapshot(cls, snapshot) -> "ModelSearchIndex":
        """Build (or fetch cached) index over a semantic snapshot's models."""
        registry_hash = getattr(snapshot, "registry_hash", "") or ""
        with _cache_lock:
            cached = _snapshot_cache.get(registry_hash)
            if cached is not None:
                return cached

        docs: list[FieldDoc] = []
        model_columns: dict[str, list[str]] = {}
        for name, model in (snapshot.models or {}).items():
            cols_field = model.get("columns") or {}
            if isinstance(cols_field, dict):
                col_items = [
                    (c, (meta or {}).get("data_type", ""), (meta or {}).get("description", "") or "")
                    for c, meta in cols_field.items()
                ]
            else:
                col_items = [
                    (
                        c.get("name", ""),
                        c.get("data_type", ""),
                        c.get("description", "") or "",
                    )
                    for c in cols_field
                    if isinstance(c, dict)
                ]
            col_names = [c for c, _, _ in col_items]
            tags = " ".join(model.get("tags") or [])
            docs.append(
                FieldDoc(
                    name=name,
                    name_text=f"{name} {name.replace('_', ' ').replace('.', ' ')}",
                    aux_text=f"{tags} {' '.join(col_names)} {model.get('owner') or ''} {model.get('module') or ''}",
                    body_text=f"{model.get('description') or ''} {model.get('path') or ''}",
                )
            )
            model_columns[name] = col_names

        index = cls(docs, model_columns=model_columns)
        with _cache_lock:
            _snapshot_cache.clear()  # single-entry cache
            _snapshot_cache[registry_hash] = index
        return index


_cache_lock = threading.Lock()
_snapshot_cache: dict[str, ModelSearchIndex] = {}


def reset_search_cache_for_tests() -> None:
    with _cache_lock:
        _snapshot_cache.clear()
