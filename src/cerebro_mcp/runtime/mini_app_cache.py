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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from cerebro_mcp.models.mini_app import DatasetMode, DatasetStats

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
        # Telemetry import is local so a missing observability module
        # (e.g. during isolated unit-tests) cannot break cache lookups.
        try:
            from cerebro_mcp.runtime.observability import (
                observe_cache_hit,
                observe_cache_miss,
            )
        except Exception:  # pragma: no cover - defensive
            observe_cache_hit = observe_cache_miss = lambda _src: None
        with self._lock:
            self._prune_locked()
            entry = self._store.get(key)
            if entry is None:
                observe_cache_miss("mini_app_dataset")
                return None
            if datetime.now(timezone.utc) > entry.expires:
                del self._store[key]
                observe_cache_miss("mini_app_dataset")
                return None
            observe_cache_hit("mini_app_dataset")
            return entry

    def put(
        self,
        key: str,
        dataset: CachedDataset,
        *,
        ttl: timedelta | None = None,
    ) -> None:
        with self._lock:
            dataset.expires = datetime.now(timezone.utc) + (ttl or self._ttl)
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


class CachedFailure(RuntimeError):
    """A query failure replayed from the negative cache (not re-executed)."""


class FailureCache:
    """Negative-result cache for mini-app dataset queries.

    A dataset whose query failed is remembered for ``ttl_seconds`` so a manual
    Retry within the window returns the cached failure INSTANTLY instead of
    re-running a query known to blow up or time out. Callers doing an explicit
    force-refresh bypass :meth:`get` (a refresh may genuinely retry).

    Each app owns one instance scoped to its database — keys are derived with
    :func:`make_cache_key` under the ``"failure"`` mode.
    """

    def __init__(
        self,
        database: str,
        *,
        ttl_seconds: int = 120,
        max_entries: int = 256,
    ) -> None:
        self._database = database
        self.ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def _key(self, sql: str, parameters: dict[str, Any] | None) -> str:
        return make_cache_key(sql, self._database, parameters, "failure")

    def get(self, sql: str, parameters: dict[str, Any] | None) -> str | None:
        key = self._key(sql, parameters)
        with self._lock:
            hit = self._entries.get(key)
            if hit is None:
                return None
            expires, message = hit
            if time.monotonic() > expires:
                self._entries.pop(key, None)
                return None
            return message

    def put(self, sql: str, parameters: dict[str, Any] | None, message: str) -> None:
        key = self._key(sql, parameters)
        with self._lock:
            if len(self._entries) >= self._max_entries:
                now = time.monotonic()
                for stale_key in [
                    k for k, (exp, _) in self._entries.items() if exp < now
                ]:
                    self._entries.pop(stale_key, None)
                if len(self._entries) >= self._max_entries:
                    self._entries.pop(next(iter(self._entries)), None)
            self._entries[key] = (time.monotonic() + self.ttl_seconds, message)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


# Module-level singleton — both Token Explorer and Metric Lab share this.
_cache = MiniAppCache()


def get_cache() -> MiniAppCache:
    return _cache


def reset_cache_for_tests() -> None:
    """Test helper: drop every cached dataset."""
    _cache.clear()


__all__ = [
    "CachedDataset",
    "CachedFailure",
    "FailureCache",
    "MiniAppCache",
    "make_cache_key",
    "get_cache",
    "reset_cache_for_tests",
]
