from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from typing import Any

from cerebro_mcp.loaders.artifacts import ArtifactLoader, local_artifact_candidates
from cerebro_mcp.loaders.catalog import catalog
from cerebro_mcp.config import settings
from cerebro_mcp.loaders.manifest import INTERNAL_ONLY_TAGS, manifest
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

logger = logging.getLogger(__name__)


def _model_is_internal(model: dict[str, Any]) -> bool:
    """True if a registry model must be hidden from every snapshot consumer.

    Mirrors the manifest's load-bearing privacy control (``INTERNAL_ONLY_TAGS``
    + ``meta.expose_to_mcp is False``). Only ``tags`` reliably survive into the
    published registry, so tags are the primary signal; the ``semantic.meta`` /
    ``meta`` flags are checked defensively when present.
    """
    tags = model.get("tags") or []
    if any(t in INTERNAL_ONLY_TAGS for t in tags):
        return True
    for meta_holder in (model.get("meta"), (model.get("semantic") or {}).get("meta")):
        if isinstance(meta_holder, dict) and meta_holder.get("expose_to_mcp") is False:
            return True
    return False


def _filter_internal_models(registry: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``registry`` with internal-only models — and the
    metrics/relationships that reference them — removed.

    The published semantic registry is not pre-filtered, so without this the
    catalog (and every other snapshot consumer) would surface identity-bridge
    models' existence, FQN, and column schema. Keeping this in the loader makes
    the privacy boundary independent of how the registry was built.
    """
    models = registry.get("models") or {}
    hidden = {name for name, m in models.items() if isinstance(m, dict) and _model_is_internal(m)}
    if not hidden:
        return registry
    logger.info("semantic snapshot: hiding %d internal-only model(s) from the registry", len(hidden))
    out = dict(registry)
    out["models"] = {n: m for n, m in models.items() if n not in hidden}
    metrics = registry.get("metrics") or {}
    out["metrics"] = {
        k: v for k, v in metrics.items()
        if not (isinstance(v, dict) and v.get("root_model") in hidden)
    }
    rels = registry.get("relationships") or []
    out["relationships"] = [
        r for r in rels
        if not (isinstance(r, dict) and (r.get("left_model") in hidden or r.get("right_model") in hidden))
    ]
    return out


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


class SemanticGraphCatalogLoader(ArtifactLoader):
    """Optional published graph-catalog sidecar (WS4).

    Mirrors the docs-index loader. A missing/invalid catalog yields ``None`` and
    the runtime falls back to live profile discovery, so this never blocks.
    """

    def __init__(self):
        super().__init__(
            url=settings.SEMANTIC_GRAPH_CATALOG_URL,
            path=settings.SEMANTIC_GRAPH_CATALOG_PATH,
            label="semantic graph catalog",
            path_resolver=lambda: local_artifact_candidates(
                "semantic_graph_catalog.json",
                settings.SEMANTIC_GRAPH_CATALOG_PATH,
                settings.SEMANTIC_REGISTRY_PATH,
                settings.SEMANTIC_DOCS_INDEX_PATH,
                settings.DBT_MANIFEST_PATH,
                settings.DBT_CATALOG_PATH,
            ),
            validator=lambda body: isinstance(body, dict) and "profiles" in body,
        )


semantic_registry = SemanticRegistryLoader()
semantic_docs = SemanticDocsIndexLoader()
semantic_graph_catalog = SemanticGraphCatalogLoader()


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
        catalog_payload = semantic_graph_catalog.load()
        if registry_payload is None:
            self._last_error = "semantic registry unavailable"
            set_semantic_enabled("unavailable")
            return None
        snapshot = self._build_snapshot(
            registry_payload.body,
            docs_payload.body if docs_payload and docs_payload.body else [],
            catalog_payload.body if catalog_payload and catalog_payload.body else None,
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
        # Reload the dbt manifest + catalog alongside the registry so the
        # execution-availability check (`_execution_state`) sees a consistent
        # set: the registry embeds the manifest/catalog hashes it was built
        # against, and those must be compared against freshly-loaded manifest
        # /catalog bytes. Refreshing only the registry on a force pulls the
        # NEW registry (new embedded manifest_hash) while leaving the stale
        # startup manifest in memory — which re-asserts `manifest_hash_mismatch`
        # instead of clearing it. Move all four together.
        # NOTE: manifest/catalog are reloaded via `reload_if_changed()` on both
        # paths. ManifestLoader has no `force_reload` (it is not an
        # ArtifactLoader), and it doesn't need one here: it re-reads local
        # files unconditionally and uses a conditional GET for the remote, and
        # a real gh-pages deploy always bumps the ETag — so a changed manifest
        # is always picked up. Only the registry/docs use `force_reload` to
        # defeat a stale-ETag re-parse during local authoring loops.
        changed_manifest, _ = manifest.reload_if_changed()
        changed_catalog, _ = catalog.reload_if_changed()
        if force:
            changed_registry, registry_error = semantic_registry.force_reload()
            changed_docs, docs_error = semantic_docs.force_reload()
            changed_graph_catalog, _ = semantic_graph_catalog.force_reload()
        else:
            changed_registry, registry_error = semantic_registry.reload_if_changed()
            changed_docs, docs_error = semantic_docs.reload_if_changed()
            changed_graph_catalog, _ = semantic_graph_catalog.reload_if_changed()
        if not (
            changed_registry
            or changed_docs
            or changed_graph_catalog
            or changed_manifest
            or changed_catalog
        ):
            return False, registry_error or docs_error
        registry_payload = semantic_registry.payload
        docs_payload = semantic_docs.payload
        catalog_payload = semantic_graph_catalog.payload
        if registry_payload is None:
            return False, "semantic registry unavailable"
        snapshot = self._build_snapshot(
            registry_payload.body,
            docs_payload.body if docs_payload and docs_payload.body else [],
            catalog_payload.body if catalog_payload and catalog_payload.body else None,
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

    def _build_snapshot(
        self,
        registry: dict[str, Any],
        docs_index_payload: list[dict[str, Any]],
        catalog_payload: dict[str, Any] | None = None,
    ) -> SemanticSnapshot:
        # Local import breaks the load-time cycle: graph_profiles imports
        # `semantic_runtime` from this module, so it can only be imported once
        # this module has finished defining it.
        from cerebro_mcp.semantic.graph_extraction import (
            profile_from_catalog_row,
            synthesize_search_documents,
        )
        from cerebro_mcp.semantic.graph_profiles import build_kind_index, discover_profiles

        # Privacy boundary: the published registry is NOT pre-filtered, so
        # internal-only / identity-bridge models would otherwise enter the
        # snapshot every snapshot consumer reads (catalog, metrics, graph) and
        # leak their existence + column schema. Mirror the manifest's
        # load-bearing deny list here (the manifest filters at index time;
        # the registry path did not). See loaders/manifest.py:INTERNAL_ONLY_TAGS.
        registry = _filter_internal_models(registry)

        synonym_index, dimension_index, metrics = build_indexes(registry)
        models = registry.get("models", {})
        graph, vertex_ids = build_semantic_graph(
            models,
            registry.get("relationships", []),
        )
        docs_index = {
            entry["uri"]: entry
            for entry in docs_index_payload
            if isinstance(entry, dict) and entry.get("uri")
        }
        # Graph profiles: prefer the published catalog (reconstructed 1:1) when it
        # is present AND consistent with this registry; otherwise fall back to
        # live discovery so a missing/stale/unsupported catalog never breaks the
        # server (WS4 / D-contract edge cases).
        graph_profiles, graph_catalog_hash = self._resolve_graph_profiles(
            registry, models, catalog_payload, discover_profiles, profile_from_catalog_row
        )
        profiles_by_id: dict[str, Any] = {}
        for profile in graph_profiles:
            existing = profiles_by_id.get(profile.profile)
            if existing is not None:
                # Q4: profile IDs must be globally unique. The dbt validator is
                # the real gate (WS2/WS8); here we just keep the runtime
                # deterministic (first wins, profiles are sorted) and surface it.
                logger.warning(
                    "duplicate graph profile id %r (models %s, %s); keeping first",
                    profile.profile,
                    existing.model_name,
                    profile.model_name,
                )
                continue
            profiles_by_id[profile.profile] = profile
        kind_to_profiles = build_kind_index(graph_profiles)
        # Search corpus: prefer the catalog's documents (richer — includes node
        # descriptions/synonyms) when the catalog is in use; else synthesize.
        if (
            graph_catalog_hash
            and isinstance(catalog_payload, dict)
            and isinstance(catalog_payload.get("search_documents"), list)
            and catalog_payload["search_documents"]
        ):
            graph_search_documents = tuple(catalog_payload["search_documents"])
        else:
            graph_search_documents = tuple(synthesize_search_documents(graph_profiles))
        # schema_version reflects the catalog actually in use (dbt stamps it in the
        # catalog metadata, not the registry); defaults to 1 on the live path.
        schema_version = 1
        if graph_catalog_hash and isinstance(catalog_payload, dict):
            sv = (catalog_payload.get("metadata") or {}).get("schema_version", 1)
            if isinstance(sv, int):
                schema_version = sv
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
            models=models,
            relationships=registry.get("relationships", []),
            docs_index=docs_index,
            loaded_at=time.time(),
            graph_profiles=graph_profiles,
            profiles_by_id=profiles_by_id,
            kind_to_profiles=kind_to_profiles,
            graph_search_documents=graph_search_documents,
            graph_catalog_hash=graph_catalog_hash,
            schema_version=schema_version,
        )

    @staticmethod
    def _resolve_graph_profiles(
        registry: dict[str, Any],
        models: dict[str, Any],
        catalog_payload: dict[str, Any] | None,
        discover_profiles,
        profile_from_catalog_row,
    ) -> tuple[tuple[Any, ...], str]:
        """Return (graph_profiles, graph_catalog_hash).

        Uses the published catalog only when it is structurally valid, a
        supported schema_version, and built against THIS registry (manifest hash
        match). Any problem -> live discovery, logged at DEBUG so the transition
        window (catalog 404 while registry present) doesn't spam WARN.
        """
        from cerebro_mcp.semantic.graph_extraction import SUPPORTED_CATALOG_SCHEMA_VERSION

        def _live() -> tuple[tuple[Any, ...], str]:
            return tuple(discover_profiles(models=models)), ""

        if not isinstance(catalog_payload, dict):
            return _live()
        meta = catalog_payload.get("metadata") or {}
        profiles_raw = catalog_payload.get("profiles")
        if not isinstance(profiles_raw, dict) or not profiles_raw:
            logger.debug("graph catalog has no profiles; using live discovery")
            return _live()
        version = meta.get("schema_version", 1)
        if not isinstance(version, int) or version > SUPPORTED_CATALOG_SCHEMA_VERSION:
            logger.debug(
                "graph catalog schema_version %s unsupported (max %s); using live discovery",
                version,
                SUPPORTED_CATALOG_SCHEMA_VERSION,
            )
            return _live()
        registry_manifest = registry.get("metadata", {}).get("manifest_hash", "")
        catalog_manifest = meta.get("registry_manifest_hash", "")
        if registry_manifest and catalog_manifest and registry_manifest != catalog_manifest:
            # A genuine mismatch (catalog built against a different registry) is
            # an operational issue worth surfacing — unlike a simply-absent
            # catalog (logged at DEBUG above), which is the expected transition
            # state. Fall back to live discovery either way.
            logger.warning(
                "graph catalog manifest hash mismatch (registry=%s catalog=%s); using live discovery",
                registry_manifest[:12],
                catalog_manifest[:12],
            )
            return _live()
        try:
            profiles = tuple(
                profile_from_catalog_row(row) for row in profiles_raw.values()
            )
        except Exception as exc:  # malformed row -> never break; fall back
            logger.debug("graph catalog reconstruction failed (%s); using live discovery", exc)
            return _live()
        profiles = tuple(sorted(profiles, key=lambda p: (p.module, p.profile)))
        return profiles, meta.get("graph_catalog_hash", "") or ""

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
