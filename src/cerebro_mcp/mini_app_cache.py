"""TTL + LRU cache for mini-app loaded datasets.

Mirrors the bounded-size pattern used by the report cache in
``tools/visualization.py`` (TTL + max-entries + threading.Lock), but keyed
by ``sha256(database + mode + sql + parameters)`` so that the same query
under a different sampling mode lives in its own slot.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from cerebro_mcp.mini_app_models import DatasetMode, DatasetStats

_CACHE_TTL = timedelta(minutes=30)
_CACHE_MAX_ENTRIES = 64


@dataclass
class CachedDataset:
    """In-memory dataset payload returned by ``load_bounded_dataset``."""

    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    stats: DatasetStats
    sql: str
    database: str
    parameters: dict[str, Any] | None = None
    expires: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + _CACHE_TTL
    )


def make_cache_key(
    sql: str,
    database: str,
    parameters: dict[str, Any] | None,
    mode: DatasetMode | str,
) -> str:
    """Deterministic cache key for a (sql, database, parameters, mode) tuple."""
    payload = json.dumps(parameters or {}, sort_keys=True, separators=(",", ":"))
    raw = f"{database}\n{mode}\n{sql.strip()}\n{payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MiniAppCache:
    """Thread-safe TTL + LRU cache for ``CachedDataset`` records."""

    def __init__(
        self,
        ttl: timedelta = _CACHE_TTL,
        max_entries: int = _CACHE_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._store: dict[str, CachedDataset] = {}
        self._lock = threading.Lock()

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    def get(self, key: str) -> CachedDataset | None:
        with self._lock:
            self._prune_locked()
            entry = self._store.get(key)
            if entry is None:
                return None
            if datetime.now(timezone.utc) > entry.expires:
                del self._store[key]
                return None
            return entry

    def put(self, key: str, dataset: CachedDataset) -> None:
        with self._lock:
            dataset.expires = datetime.now(timezone.utc) + self._ttl
            self._store[key] = dataset
            self._prune_locked()

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def _prune_locked(self) -> None:
        """Drop expired entries and evict the oldest if over the soft cap."""
        now = datetime.now(timezone.utc)
        expired = [k for k, v in self._store.items() if now > v.expires]
        for k in expired:
            del self._store[k]
        while len(self._store) > self._max_entries:
            oldest = min(self._store, key=lambda k: self._store[k].expires)
            del self._store[oldest]


# Module-level singleton — both Token Explorer and Metric Lab share this.
_cache = MiniAppCache()


def get_cache() -> MiniAppCache:
    return _cache


def reset_cache_for_tests() -> None:
    """Test helper: drop every cached dataset."""
    _cache.clear()


__all__ = [
    "CachedDataset",
    "MiniAppCache",
    "make_cache_key",
    "get_cache",
    "reset_cache_for_tests",
]
