from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any

from cerebro_mcp.loaders.artifacts import ArtifactLoader, local_artifact_candidates
from cerebro_mcp.loaders.catalog import catalog
from cerebro_mcp.config import settings
from cerebro_mcp.loaders.manifest import manifest
from cerebro_mcp.runtime.observability import (
    observe_semantic_snapshot_reload,
    observe_semantic_snapshot_stale,
    set_semantic_enabled,
    set_semantic_registry_totals,
    set_semantic_snapshot_age,
)
from cerebro_mcp.semantic.graph import build_semantic_graph
from cerebro_mcp.semantic.index import build_indexes
from cerebro_mcp.models.semantic import SemanticSnapshot


class SemanticRegistryLoader(ArtifactLoader):
    def __init__(self):
        super().__init__(
            url=settings.SEMANTIC_REGISTRY_URL,
            path=settings.SEMANTIC_REGISTRY_PATH,
            label="semantic registry",
            path_resolver=lambda: local_artifact_candidates(
                "semantic_registry.json",
                settings.SEMANTIC_REGISTRY_PATH,
                settings.SEMANTIC_DOCS_INDEX_PATH,
                settings.DBT_MANIFEST_PATH,
                settings.DBT_CATALOG_PATH,
            ),
            validator=lambda body: isinstance(body, dict) and "models" in body,
        )


class SemanticDocsIndexLoader(ArtifactLoader):
    def __init__(self):
        super().__init__(
            url=settings.SEMANTIC_DOCS_INDEX_URL,
            path=settings.SEMANTIC_DOCS_INDEX_PATH,
            label="semantic docs index",
            path_resolver=lambda: local_artifact_candidates(
                "semantic_docs_index.json",
                settings.SEMANTIC_DOCS_INDEX_PATH,
                settings.SEMANTIC_REGISTRY_PATH,
                settings.DBT_MANIFEST_PATH,
                settings.DBT_CATALOG_PATH,
            ),
            validator=lambda body: isinstance(body, list),
        )


semantic_registry = SemanticRegistryLoader()
semantic_docs = SemanticDocsIndexLoader()


class SemanticRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._snapshot: SemanticSnapshot | None = None
        self._last_error: str | None = None
        self._execution_available = False
        self._stale_reason: str | None = None

    def load(self) -> SemanticSnapshot | None:
        if not settings.SEMANTIC_ENABLED:
            set_semantic_enabled("disabled")
            return None
        started = time.perf_counter()
        registry_payload = semantic_registry.load()
        docs_payload = semantic_docs.load()
        if registry_payload is None:
            self._last_error = "semantic registry unavailable"
            set_semantic_enabled("unavailable")
            return None
        snapshot = self._build_snapshot(
            registry_payload.body,
            docs_payload.body if docs_payload and docs_payload.body else [],
        )
        with self._lock:
            self._snapshot = snapshot
            self._execution_available, self._stale_reason = self._execution_state(snapshot)
        self._emit_snapshot_gauges(snapshot)
        set_semantic_enabled("execution_available" if self._execution_available else "docs_only")
        observe_semantic_snapshot_reload(
            status="success" if self._execution_available else "docs_only",
            elapsed_seconds=time.perf_counter() - started,
        )
        return snapshot

    def refresh_if_changed(self) -> tuple[bool, str | None]:
        if not settings.SEMANTIC_ENABLED:
            return False, None
        return self._refresh(force=False)

    def force_reload(self) -> tuple[bool, str | None]:
        """Unconditional refresh — bypasses the ETag-based polling.

        Backs the ``reload_semantic_registry`` admin tool. Useful during
        semantic-layer authoring loops when you've just rebuilt the
        registry and don't want to wait for the 5-minute TTL or fight
        a stale upstream ETag. Returns ``(changed, error)`` where
        ``changed`` reports whether the new payload actually differed
        from the cached one.
        """
        if not settings.SEMANTIC_ENABLED:
            return False, None
        return self._refresh(force=True)

    def _refresh(self, *, force: bool) -> tuple[bool, str | None]:
        started = time.perf_counter()
        if force:
            changed_registry, registry_error = semantic_registry.force_reload()
            changed_docs, docs_error = semantic_docs.force_reload()
        else:
            changed_registry, registry_error = semantic_registry.reload_if_changed()
            changed_docs, docs_error = semantic_docs.reload_if_changed()
        if not changed_registry and not changed_docs:
            return False, registry_error or docs_error
        registry_payload = semantic_registry.payload
        docs_payload = semantic_docs.payload
        if registry_payload is None:
            return False, "semantic registry unavailable"
        snapshot = self._build_snapshot(
            registry_payload.body,
            docs_payload.body if docs_payload and docs_payload.body else [],
        )
        with self._lock:
            self._snapshot = snapshot
            self._execution_available, self._stale_reason = self._execution_state(snapshot)
        self._emit_snapshot_gauges(snapshot)
        set_semantic_enabled("execution_available" if self._execution_available else "docs_only")
        observe_semantic_snapshot_reload(
            status="success" if self._execution_available else "docs_only",
            elapsed_seconds=time.perf_counter() - started,
        )
        return True, None

    def _build_snapshot(self, registry: dict[str, Any], docs_index_payload: list[dict[str, Any]]) -> SemanticSnapshot:
        synonym_index, dimension_index, metrics = build_indexes(registry)
        graph, vertex_ids = build_semantic_graph(
            registry.get("models", {}),
            registry.get("relationships", []),
        )
        docs_index = {
            entry["uri"]: entry
            for entry in docs_index_payload
            if isinstance(entry, dict) and entry.get("uri")
        }
        return SemanticSnapshot(
            registry_hash=semantic_registry.content_hash or "",
            manifest_hash=registry.get("metadata", {}).get("manifest_hash", ""),
            catalog_hash=registry.get("metadata", {}).get("catalog_hash", ""),
            docs_hash=semantic_docs.content_hash or "",
            graph=graph,
            vertex_ids=vertex_ids,
            synonym_index=synonym_index,
            dimension_index=dimension_index,
            metrics=metrics,
            models=registry.get("models", {}),
            relationships=registry.get("relationships", []),
            docs_index=docs_index,
            loaded_at=time.time(),
        )

    def _execution_state(self, snapshot: SemanticSnapshot) -> tuple[bool, str | None]:
        if not manifest.is_loaded:
            observe_semantic_snapshot_stale(reason="manifest_unavailable")
            return False, "manifest_unavailable"
        if not catalog.is_loaded:
            observe_semantic_snapshot_stale(reason="catalog_unavailable")
            return False, "catalog_unavailable"
        if snapshot.manifest_hash != (manifest.content_hash or ""):
            observe_semantic_snapshot_stale(reason="manifest_hash_mismatch")
            return False, "manifest_hash_mismatch"
        if snapshot.catalog_hash != (catalog.content_hash or ""):
            observe_semantic_snapshot_stale(reason="catalog_hash_mismatch")
            return False, "catalog_hash_mismatch"
        return True, None

    def _emit_snapshot_gauges(self, snapshot: SemanticSnapshot) -> None:
        set_semantic_snapshot_age(max(0.0, time.time() - snapshot.loaded_at))
        model_counts = Counter(
            model.get("semantic_status", "docs_only")
            for model in snapshot.models.values()
        )
        metric_counts = Counter(
            metric.get("quality_tier", "candidate") or "candidate"
            for metric in snapshot.metrics.values()
        )
        relationship_counts = Counter(
            relationship.get("quality_tier", "candidate") or "candidate"
            for relationship in snapshot.relationships
        )
        set_semantic_registry_totals(
            model_status_counts=dict(model_counts),
            metric_quality_counts=dict(metric_counts),
            relationship_quality_counts=dict(relationship_counts),
        )

    @property
    def snapshot(self) -> SemanticSnapshot | None:
        with self._lock:
            return self._snapshot

    @property
    def is_execution_available(self) -> bool:
        with self._lock:
            return self._execution_available

    @property
    def stale_reason(self) -> str | None:
        with self._lock:
            return self._stale_reason

    @property
    def last_error(self) -> str | None:
        return self._last_error


semantic_runtime = SemanticRuntime()
