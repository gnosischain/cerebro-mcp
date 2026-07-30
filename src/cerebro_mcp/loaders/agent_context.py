"""Loader for the dbt-cerebro agent-context artifact.

The artifact (schema_version 1 or 2) is built in the dbt repo by
scripts/agent_context/build_agent_context.py and carries:
  - lessons: curated mistake-class records (id, title, status, symptom,
    evidence, full body) with a status lifecycle
    observed -> remediated -> enforced;
  - models: per-model resolved engineering contracts (grain, invariants,
    hazards -> lesson ids, rules, validation, reprocess runbook), resolved
    from scope profiles + meta.agent against the manifest;
  - models_hash: digest of the sorted model checksums of the manifest it was
    built from.

Staleness: compare models_hash against the same digest computed from the
live manifest. On mismatch, global lessons remain valid (they describe the
repo, not one manifest) but per-model contract attachments are suppressed
with a warning — a stale contract asserting the wrong grain is worse than
none. Graceful absence: every consumer must behave sensibly when
`is_loaded` is False.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Optional

import requests

from cerebro_mcp.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSIONS = {1, 2}


def normalize_artifact(data: dict) -> dict:
    """Normalize a v1 or v2 artifact to the v2 shape, in place.

    v2 changes consumed here:
      - contract.agents_md: scalar (v1) -> list of guides (v2)
      - downstream_count (v1, direct children) -> downstream_direct_count (v2)
      - downstream_api_models (v1: direct; v2: transitive, capped) gains
        downstream_api_count (v2: total transitive count, may exceed the list)
    Consumers read only the normalized fields.
    """
    for entry in (data.get("models") or {}).values():
        contract = entry.get("contract") or {}
        guides = contract.get("agents_md")
        if isinstance(guides, str):
            contract["agents_md"] = [guides]
        if "downstream_direct_count" not in entry:
            entry["downstream_direct_count"] = entry.get("downstream_count", 0)
        if "downstream_api_count" not in entry:
            entry["downstream_api_count"] = len(entry.get("downstream_api_models") or [])
    return data


def manifest_models_hash(manifest_loader: Any) -> Optional[str]:
    """Recompute the artifact's models_hash from the live ManifestLoader.

    Mirrors build_agent_context.py: sha256 over sorted "name:checksum" lines
    for first-party models. Returns None when checksums are unavailable so
    callers can skip staleness enforcement rather than false-alarm.
    """
    try:
        models = getattr(manifest_loader, "_models", None)
        if not models:
            return None
        lines = []
        for name, node in models.items():
            checksum = (node.get("checksum") or {}).get("checksum", "")
            if not checksum:
                return None
            lines.append(f"{name}:{checksum}")
        return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()
    except Exception:  # pragma: no cover - defensive
        return None


def score_lessons(
    lessons: Any,
    query: str,
    limit: int = 5,
    boost_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Rank lesson records against a query. Shared by every lesson corpus.

    Extracted from AgentContextLoader.search so the repo-local corpus
    (loaders/cerebro_lessons.py) ranks identically rather than growing a second,
    subtly-different ranker — the retrieval evals in
    tests/test_agent_knowledge_eval.py and tests/test_cerebro_lessons.py then
    hold both to the same bar.

    Substring token scoring, weighted by field: an id/title hit outranks a
    symptom hit outranks a scope hit outranks a body hit, with a bonus for the
    whole query appearing as a phrase. `boost_ids` lifts records already known to
    be in the caller's blast radius (a dbt model's hazards, or the hazards of the
    path an agent named). Deterministic tiebreak on id so results never reorder
    between identical calls.
    """
    tokens = [t for t in query.lower().split() if len(t) > 1]
    if not tokens:
        return []
    phrase = query.lower().strip()
    boost = boost_ids or set()
    scored: list[tuple[float, dict]] = []

    for lesson in lessons:
        title = str(lesson.get("title", "")).lower()
        symptom = str(lesson.get("symptom", "")).lower()
        scope = str(lesson.get("scope", "")).lower()
        body = str(lesson.get("body", "")).lower()
        lid = lesson.get("id", "")
        score = 0.0
        for t in tokens:
            if t in lid.lower():
                score += 3
            if t in title:
                score += 3
            if t in symptom:
                score += 2
            if t in scope:
                score += 1.5
            if t in body:
                score += 1
        if phrase and (phrase in title or phrase in body):
            score += 5
        if lid in boost:
            score += 4  # already known to be in the caller's blast radius
        if score > 0:
            scored.append((score, lesson))

    scored.sort(key=lambda x: (-x[0], x[1].get("id", "")))
    return [lesson for _, lesson in scored[:limit]]


class AgentContextLoader:
    """Loads and serves the agent-context artifact (local path wins over URL)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._last_load_time = 0.0
        self._last_error: Optional[str] = None
        self._etag: Optional[str] = None
        # Serializes refresh so concurrent tool bodies (now on worker threads,
        # see runtime/offload.py) cannot each fetch the artifact at once.
        self._lock = threading.RLock()
        # Last refresh ATTEMPT. `_last_load_time` advances only on SUCCESS, so
        # gating the TTL on it means a failing or 304 endpoint is re-fetched on
        # every single call for the life of the process.
        self._last_refresh_attempt = 0.0
        # Observability counters (surfaced via stats()).
        self.searches = 0
        self.search_hits = 0
        self.change_packets = 0
        self.stale_serves = 0

    # -- loading -----------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def load(self) -> None:
        data = self._fetch()
        if data is None:
            return
        version = data.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            self._last_error = (
                f"unsupported agent-context schema_version {version} "
                f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
            )
            logger.warning(self._last_error)
            return
        self._data = normalize_artifact(data)
        self._loaded = True
        self._last_load_time = time.time()
        self._last_error = None
        logger.info(
            "agent-context loaded: %d models, %d lessons",
            len(data.get("models") or {}),
            len(data.get("lessons") or {}),
        )

    def maybe_refresh(self) -> None:
        ttl = settings.AGENT_CONTEXT_REFRESH_INTERVAL_SECONDS
        with self._lock:
            attempted = self._last_refresh_attempt or self._last_load_time
            if self._loaded and (time.time() - attempted) <= ttl:
                return
            # Stamp BEFORE the fetch: on failure this is what stops the retry
            # from firing again on the very next tool call.
            self._last_refresh_attempt = time.time()
            try:
                self.load()
            except Exception as exc:  # pragma: no cover - network trouble
                self._last_error = str(exc)
                logger.warning("agent-context refresh failed: %s", exc)

    def _fetch(self) -> Optional[dict]:
        path = settings.AGENT_CONTEXT_PATH
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception as exc:
                self._last_error = f"local agent-context unreadable: {exc}"
                logger.warning(self._last_error)
                return None
        url = settings.AGENT_CONTEXT_URL
        if not url:
            self._last_error = "no agent-context source configured"
            return None
        headers = {"If-None-Match": self._etag} if self._etag else {}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 304:
                self._last_load_time = time.time()
                return None
            resp.raise_for_status()
            self._etag = resp.headers.get("ETag")
            return resp.json()
        except Exception as exc:
            self._last_error = f"agent-context fetch failed: {exc}"
            logger.warning(self._last_error)
            return None

    # -- staleness ---------------------------------------------------------

    def is_stale_for(self, manifest_loader: Any) -> Optional[bool]:
        """True when the live manifest differs from the artifact's build input.

        None = undeterminable (missing checksums) — treat as fresh but note it.
        """
        if not self._loaded:
            return None
        live = manifest_models_hash(manifest_loader)
        if live is None:
            return None
        return live != self._data.get("models_hash")

    # -- access ------------------------------------------------------------

    @property
    def lessons(self) -> dict[str, dict]:
        return self._data.get("lessons") or {}

    def get_model(self, name: str) -> Optional[dict]:
        return (self._data.get("models") or {}).get(name)

    def search(
        self, query: str, model_name: Optional[str] = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Keyword search over lessons (and model contracts when model_name given).

        Same token-scoring family as DocsLoader.search: substring token hits,
        weighted by field (title/symptom > scope > body), exact-phrase boost.
        """
        self.searches += 1
        model_hazards: set[str] = set()
        if model_name:
            entry = self.get_model(model_name)
            if entry:
                model_hazards = {
                    h.get("id", "") for h in (entry.get("contract") or {}).get("hazards", [])
                }
        results = score_lessons(
            self.lessons.values(), query, limit=limit, boost_ids=model_hazards
        )
        if results:
            self.search_hits += 1
        return results

    def stats(self) -> dict[str, Any]:
        return {
            "loaded": self._loaded,
            "lessons": len(self.lessons),
            "models": len(self._data.get("models") or {}),
            "searches": self.searches,
            "search_hits": self.search_hits,
            "change_packets": self.change_packets,
            "stale_serves": self.stale_serves,
            "last_error": self._last_error,
        }


agent_context = AgentContextLoader()
