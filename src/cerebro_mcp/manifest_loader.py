import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Optional

import networkx as nx
import requests

from cerebro_mcp.artifact_loader import local_artifact_candidates
from cerebro_mcp.config import settings
from cerebro_mcp.semantic_bm25 import (
    BM25Index,
    ColumnBM25Index,
    build_bm25_indices_from_manifest_data,
)
from cerebro_mcp.semantic_index import rrf_fuse

logger = logging.getLogger(__name__)


class ManifestLoader:
    """Loads and indexes dbt manifest.json for efficient lookups."""

    def __init__(self):
        self._models: dict[str, dict] = {}
        self._sources: dict[str, dict] = {}
        self._parent_map: dict[str, list[str]] = {}
        self._child_map: dict[str, list[str]] = {}
        self._tags_index: dict[str, list[str]] = {}
        self._module_index: dict[str, list[str]] = {}
        self._search_index: dict[str, str] = {}
        self._tests_by_model: dict[str, list[dict]] = {}
        self._owner_index: dict[str, list[str]] = {}
        # Phase 1: BM25 search + networkx lineage. Built once per manifest
        # reload by `_build_indexes_internal` and swapped atomically with
        # the rest of the indexes.
        self._bm25_models: BM25Index = BM25Index([])
        self._bm25_columns: ColumnBM25Index = ColumnBM25Index([])
        self._lineage_graph: nx.DiGraph = nx.DiGraph()
        self._unique_id_by_name: dict[str, str] = {}
        self._loaded = False

        # Conditional GET state
        self._etag: str | None = None
        self._last_modified_header: str | None = None
        self._content_hash: str | None = None
        self._last_load_time: float = 0.0
        self._last_refresh_error: str | None = None

    def load(self) -> None:
        """Load manifest from URL or local file and build indexes."""
        result = self._fetch_manifest()
        if result:
            data, content_hash = result
            indexes = self._build_indexes_internal(data)
            self._apply_indexes(indexes)
            self._content_hash = content_hash
            self._last_load_time = time.time()
            self._loaded = True

    def _fetch_manifest(
        self, conditional: bool = False
    ) -> Optional[tuple[dict, str]]:
        """Fetch manifest from local file or URL.

        Args:
            conditional: If True, use a local hash check or conditional GET.

        Returns:
            Tuple of (parsed_data, content_hash) or None if unchanged/unavailable.
        """
        local_result = self._load_local_manifest()
        if local_result is not None:
            data, content_hash, source = local_result
            self._last_refresh_error = None
            if not conditional:
                logger.info("Loaded manifest from %s", source)
            return data, content_hash

        if settings.DBT_MANIFEST_URL:
            try:
                headers = {}
                timeout = 30
                if conditional:
                    timeout = 1
                    if self._etag:
                        headers["If-None-Match"] = self._etag
                    if self._last_modified_header:
                        headers["If-Modified-Since"] = self._last_modified_header

                resp = requests.get(
                    settings.DBT_MANIFEST_URL, timeout=timeout, headers=headers
                )

                if resp.status_code == 304:
                    return None  # Not modified

                if resp.status_code == 200:
                    self._etag = resp.headers.get("ETag")
                    self._last_modified_header = resp.headers.get("Last-Modified")
                    self._last_refresh_error = None
                    content_hash = self._hash_bytes(resp.content)
                    if not conditional:
                        logger.info(
                            "Loaded manifest from %s",
                            settings.DBT_MANIFEST_URL,
                        )
                    return resp.json(), content_hash

                error_msg = f"Failed to fetch manifest: HTTP {resp.status_code}"
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                logger.warning(error_msg)
            except Exception as e:
                error_msg = f"Error fetching manifest URL: {e}"
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                logger.warning(error_msg)

        logger.warning(
            "No manifest loaded. dbt context tools will be unavailable."
        )
        return None

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        """Compute SHA-256 hash of bytes for content matching/dedup."""
        return hashlib.sha256(data).hexdigest()

    def reload_if_changed(self) -> tuple[bool, str | None]:
        """Check if manifest has changed and reload if so.

        Returns:
            Tuple of (changed, error). changed is True if indexes were updated.
        """
        result = self._fetch_manifest(conditional=True)
        if result is None:
            if not settings.DBT_MANIFEST_URL and self._load_local_manifest() is None:
                return False, None
            return False, self._last_refresh_error

        data, new_hash = result
        if new_hash == self._content_hash:
            return False, None

        # Build new indexes atomically
        indexes = self._build_indexes_internal(data)
        self._apply_indexes(indexes)
        self._content_hash = new_hash
        self._last_load_time = time.time()
        self._last_refresh_error = None
        return True, None

    @staticmethod
    def _looks_like_manifest(data: Any) -> bool:
        return (
            isinstance(data, dict)
            and isinstance(data.get("nodes"), dict)
            and isinstance(data.get("parent_map"), dict)
            and isinstance(data.get("child_map"), dict)
        )

    def _local_manifest_candidates(self) -> list[str]:
        return local_artifact_candidates(
            "manifest.json",
            settings.DBT_MANIFEST_PATH,
            settings.DBT_CATALOG_PATH,
            settings.SEMANTIC_REGISTRY_PATH,
            settings.SEMANTIC_DOCS_INDEX_PATH,
        )

    def _load_local_manifest(self) -> Optional[tuple[dict, str, str]]:
        for candidate in self._local_manifest_candidates():
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "rb") as f:
                    raw = f.read()
                data = json.loads(raw)
            except Exception as exc:
                logger.warning("Error loading local manifest from %s: %s", candidate, exc)
                continue
            if not self._looks_like_manifest(data):
                logger.warning(
                    "Ignoring local manifest candidate %s because it does not look like a dbt manifest.",
                    candidate,
                )
                continue
            return data, self._hash_bytes(raw), candidate
        return None

    def _build_indexes_internal(self, data: dict) -> dict:
        """Build lookup indexes from manifest data without mutating self.

        Returns a dict of all index data for atomic swap.
        """
        models: dict[str, dict] = {}
        sources: dict[str, dict] = {}
        tags_index: dict[str, list[str]] = {}
        module_index: dict[str, list[str]] = {}
        search_index: dict[str, str] = {}
        tests_by_model: dict[str, list[dict]] = {}
        owner_index: dict[str, list[str]] = {}

        for key, node in data.get("nodes", {}).items():
            resource_type = node.get("resource_type")

            if resource_type == "model":
                name = node["name"]
                models[name] = node

                for tag in node.get("tags", []):
                    tags_index.setdefault(tag, []).append(name)

                path = node.get("path", "")
                if "/" in path:
                    module = path.split("/")[0].lower()
                    module_index.setdefault(module, []).append(name)

                # Index by owner from meta
                meta = node.get("config", {}).get("meta", {})
                if not meta:
                    meta = node.get("meta", {})
                owner = meta.get("owner", "") if meta else ""
                if owner:
                    owner_index.setdefault(owner, []).append(name)

                # Include owner in searchable text.
                #
                # Phase 1.5: enrich with column metadata and path tokens.
                # Column names + descriptions are the most direct query→data
                # signal in the manifest. Path tokens (e.g. "execution dex
                # marts") add category context that's missing from generic
                # descriptions like "Daily aggregate". Both are appended at
                # the end so BM25's IDF still gives the most weight to
                # distinctive tokens in the model name and description.
                #
                # `meta.inference_notes` is included when present — analysts
                # author it specifically for retrieval.
                desc = node.get("description", "")
                tags_str = " ".join(node.get("tags", []))
                column_text = " ".join(
                    f"{col_name} {(col_meta or {}).get('description', '')}"
                    for col_name, col_meta in (node.get("columns") or {}).items()
                )
                path_tokens = path.replace("/", " ").replace(".sql", "")
                inference_notes = ""
                if meta and isinstance(meta, dict):
                    inference_notes = str(meta.get("inference_notes", "") or "")
                search_index[name] = (
                    f"{name.lower()} {desc.lower()} {tags_str.lower()}"
                    f" {owner.lower()} {column_text.lower()}"
                    f" {path_tokens.lower()} {inference_notes.lower()}"
                )

            elif resource_type == "test":
                # Index tests by the model they reference
                test_meta = node.get("test_metadata", {})
                test_name = test_meta.get("name", "") if test_meta else ""
                depends = node.get("depends_on", {}).get("nodes", [])
                for dep in depends:
                    # dep format: "model.gnosis_dbt.model_name"
                    parts = dep.split(".")
                    if parts[0] == "model" and len(parts) >= 3:
                        model_name = parts[-1]
                        test_info = {
                            "test_name": test_name,
                            "test_type": test_meta.get("namespace", "")
                            if test_meta
                            else "",
                            "severity": node.get("config", {}).get(
                                "severity", "warn"
                            ),
                            "tags": node.get("tags", []),
                            "column_name": node.get("column_name", ""),
                        }
                        # Add Elementary-specific config
                        if test_meta and test_meta.get("namespace") == "elementary":
                            kwargs = test_meta.get("kwargs", {})
                            if kwargs.get("timestamp_column"):
                                test_info["timestamp_column"] = kwargs[
                                    "timestamp_column"
                                ]
                            if kwargs.get("time_bucket"):
                                test_info["time_bucket"] = kwargs["time_bucket"]
                            if kwargs.get("anomaly_sensitivity"):
                                test_info["anomaly_sensitivity"] = kwargs[
                                    "anomaly_sensitivity"
                                ]
                        tests_by_model.setdefault(model_name, []).append(
                            test_info
                        )

        for key, node in data.get("sources", {}).items():
            source_key = f"{node.get('schema', '')}.{node.get('name', '')}"
            sources[source_key] = node

        parent_map = data.get("parent_map", {})
        child_map = data.get("child_map", {})

        test_count = sum(len(v) for v in tests_by_model.values())
        logger.info(
            "Indexed %s models, %s sources, %s tests, %s tags, %s modules",
            len(models),
            len(sources),
            test_count,
            len(tags_index),
            len(module_index),
        )

        # --- Phase 1: BM25 indices over models + columns --------------------
        # Pure data — picklable, safe to rebuild in a worker process if Phase 4
        # later offloads manifest parsing.
        bm25_models, bm25_columns = build_bm25_indices_from_manifest_data(
            models, search_index
        )

        # --- Phase 1: networkx lineage DAG ---------------------------------
        # Built from the dbt-emitted parent_map / child_map. Nodes are the
        # full unique_ids ("model.<project>.<name>"), edges go parent -> child.
        # We additionally hydrate model name -> unique_id for cheap lookups
        # from public methods that take a model name.
        lineage_graph = nx.DiGraph()
        unique_id_by_name: dict[str, str] = {}
        for model_name, node in models.items():
            uid = node.get("unique_id", "")
            if uid:
                unique_id_by_name[model_name] = uid
                lineage_graph.add_node(
                    uid, kind="model", model_name=model_name
                )
        # Source nodes are referenced by parent_map entries; add them as
        # placeholder nodes so ancestor walks don't drop edges silently.
        for src_key, src_node in sources.items():
            uid = src_node.get("unique_id", "")
            if uid:
                lineage_graph.add_node(
                    uid, kind="source", source_key=src_key
                )
        # Edges: parent_map["model.x.b"] = ["model.x.a"] means a -> b.
        for child_uid, parents in parent_map.items():
            if child_uid not in lineage_graph:
                lineage_graph.add_node(child_uid, kind="unknown")
            for parent_uid in parents:
                if parent_uid not in lineage_graph:
                    lineage_graph.add_node(parent_uid, kind="unknown")
                lineage_graph.add_edge(parent_uid, child_uid)

        return {
            "models": models,
            "sources": sources,
            "parent_map": parent_map,
            "child_map": child_map,
            "tags_index": tags_index,
            "module_index": module_index,
            "search_index": search_index,
            "tests_by_model": tests_by_model,
            "owner_index": owner_index,
            "bm25_models": bm25_models,
            "bm25_columns": bm25_columns,
            "lineage_graph": lineage_graph,
            "unique_id_by_name": unique_id_by_name,
        }

    def _apply_indexes(self, indexes: dict) -> None:
        """Atomically swap all index references."""
        self._models = indexes["models"]
        self._sources = indexes["sources"]
        self._parent_map = indexes["parent_map"]
        self._child_map = indexes["child_map"]
        self._tags_index = indexes["tags_index"]
        self._module_index = indexes["module_index"]
        self._search_index = indexes["search_index"]
        self._tests_by_model = indexes["tests_by_model"]
        self._owner_index = indexes["owner_index"]
        # Phase 1 additions — fall back to empty indices for older callers
        # that may have hand-built an indexes dict (none in tree today, but
        # `.get` keeps tests forward-compatible).
        self._bm25_models = indexes.get("bm25_models", BM25Index([]))
        self._bm25_columns = indexes.get("bm25_columns", ColumnBM25Index([]))
        self._lineage_graph = indexes.get("lineage_graph", nx.DiGraph())
        self._unique_id_by_name = indexes.get("unique_id_by_name", {})

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model_count(self) -> int:
        return len(self._models)

    @property
    def last_load_time(self) -> float:
        return self._last_load_time

    @property
    def content_hash(self) -> str | None:
        return self._content_hash

    @property
    def last_refresh_error(self) -> str | None:
        return self._last_refresh_error

    def get_model(self, name: str) -> Optional[dict]:
        return self._models.get(name)

    def get_all_model_names(self) -> list[str]:
        return list(self._models.keys())

    def _model_summary(self, node: dict) -> dict[str, Any]:
        """Build a summary dict for a model node."""
        meta = node.get("config", {}).get("meta", {})
        if not meta:
            meta = node.get("meta", {})

        tests = self._tests_by_model.get(node["name"], [])
        test_count = len(tests)
        elementary_tests = [t for t in tests if t.get("test_type") == "elementary"]

        return {
            "name": node["name"],
            "description": node.get("description", ""),
            "materialized": node.get("config", {}).get("materialized", ""),
            "tags": node.get("tags", []),
            "schema": node.get("schema", ""),
            "path": node.get("path", ""),
            "owner": meta.get("owner", "") if meta else "",
            "test_count": test_count,
            "elementary_test_count": len(elementary_tests),
        }

    def search_models(
        self,
        query: str = "",
        tags: Optional[list[str]] = None,
        module: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search models by name, description, or tags.

        Multi-word queries are tokenized and matched independently (OR logic).
        Results are ranked by number of matching tokens (most relevant first).
        """
        candidates = set(self._models.keys())

        # Filter by module (case-insensitive)
        if module:
            module_models = set(self._module_index.get(module.lower(), []))
            candidates &= module_models

        # Filter by tags
        if tags:
            for tag in tags:
                tag_models = set(self._tags_index.get(tag, []))
                candidates &= tag_models

        if not query:
            # No query — return all candidates sorted alphabetically
            results = []
            for name in sorted(candidates):
                results.append(self._model_summary(self._models[name]))
                if len(results) >= limit:
                    break
            return results

        # ---- Phase 1: hybrid search (token-overlap + BM25 fused via RRF) ----
        #
        # The legacy token-overlap scorer is kept because it carries hand-tuned
        # behavior (substring match, short-token fallback, stable alphabetical
        # tie-break). BM25 adds proper IDF weighting so that distinctive tokens
        # (e.g. "trades", "tvl") dominate noise tokens (e.g. "daily", "by").
        # We fuse the two ranked lists with RRF; items present in both rise.
        tokens = re.split(r"[\s_]+", query.lower())
        tokens = [t for t in tokens if len(t) >= 3]
        if not tokens:
            tokens = [query.lower()]

        token_scored: list[tuple[int, str]] = []
        for name in candidates:
            searchable = self._search_index.get(name, "")
            hits = sum(1 for t in tokens if t in searchable)
            if hits > 0:
                token_scored.append((hits, name))
        token_scored.sort(key=lambda x: (-x[0], x[1]))
        token_ranking = [name for _, name in token_scored]

        # BM25 ranks the entire model corpus; restrict to the candidates
        # surviving the tag/module filters before fusion.
        bm25_ranking_full = self._bm25_models.ranking(query, top_k=200)
        bm25_ranking = [n for n in bm25_ranking_full if n in candidates]

        if not token_ranking and not bm25_ranking:
            return []

        fused = rrf_fuse([token_ranking, bm25_ranking], top_k=limit)
        results: list[dict[str, Any]] = []
        for name, _score in fused:
            node = self._models.get(name)
            if node is None:
                continue
            results.append(self._model_summary(node))
            if len(results) >= limit:
                break
        return results

    def get_model_details(self, model_name: str) -> Optional[dict[str, Any]]:
        """Get comprehensive details about a dbt model."""
        node = self._models.get(model_name)
        if not node:
            return None

        unique_id = node.get("unique_id", "")
        parents = self._parent_map.get(unique_id, [])
        children = self._child_map.get(unique_id, [])

        # Build column info
        columns = {}
        for col_name, col_meta in node.get("columns", {}).items():
            columns[col_name] = {
                "data_type": col_meta.get("data_type", ""),
                "description": col_meta.get("description", ""),
            }

        schema = node.get("schema", "dbt")
        alias = node.get("alias", model_name)

        # Extract model meta (owner, authoritative, full_refresh)
        meta = node.get("config", {}).get("meta", {})
        if not meta:
            meta = node.get("meta", {})
        model_meta = {}
        if meta:
            for key in ("owner", "authoritative", "full_refresh", "inference_notes"):
                if key in meta:
                    model_meta[key] = meta[key]

        # Collect tests for this model
        tests = self._tests_by_model.get(model_name, [])

        return {
            "name": model_name,
            "unique_id": unique_id,
            "description": node.get("description", ""),
            "table_name": f"{schema}.{alias}",
            "materialized": node.get("config", {}).get("materialized", ""),
            "tags": node.get("tags", []),
            "path": node.get("path", ""),
            "meta": model_meta,
            "columns": columns,
            "tests": tests,
            "raw_sql": node.get("raw_code", ""),
            "compiled_sql": node.get("compiled_code", ""),
            "upstream": parents,
            "downstream": children,
        }

    def get_lineage(
        self,
        model_name: str,
        direction: str = "both",
        depth: int = 2,
    ) -> dict[str, Any]:
        """Trace lineage for a model."""
        node = self._models.get(model_name)
        if not node:
            return {"error": f"Model '{model_name}' not found"}

        unique_id = node["unique_id"]
        result: dict[str, Any] = {"model": model_name, "unique_id": unique_id}

        if direction in ("upstream", "both"):
            result["upstream"] = self._traverse(unique_id, self._parent_map, depth)

        if direction in ("downstream", "both"):
            result["downstream"] = self._traverse(unique_id, self._child_map, depth)

        return result

    # ------------------------------------------------------------------
    # Phase 1: networkx-backed lineage
    #
    # `get_lineage` (above) is kept for API compatibility — it does a bounded
    # BFS via the dbt-emitted parent_map/child_map. The methods below operate
    # on the same data via `networkx`, which gives transitive closure
    # (`ancestors` / `descendants`) cheaply and lets analysts/reviewers ask
    # "everything upstream" without picking a depth.
    # ------------------------------------------------------------------

    def _resolve_unique_id(self, model_name: str) -> Optional[str]:
        """Resolve a dbt model short name to its full unique_id."""
        return self._unique_id_by_name.get(model_name)

    def upstream(self, model_name: str) -> list[str]:
        """Return all transitive ancestor model/source unique_ids for `model_name`.

        Uses networkx ancestors over the lineage DAG. Returns [] if the model
        is unknown. Order is unspecified — callers that need a sorted list
        should sort by `unique_id` (path-based) themselves.
        """
        uid = self._resolve_unique_id(model_name)
        if uid is None or uid not in self._lineage_graph:
            return []
        return list(nx.ancestors(self._lineage_graph, uid))

    def downstream(self, model_name: str) -> list[str]:
        """Return all transitive descendant model unique_ids for `model_name`."""
        uid = self._resolve_unique_id(model_name)
        if uid is None or uid not in self._lineage_graph:
            return []
        return list(nx.descendants(self._lineage_graph, uid))

    def upstream_named(self, model_name: str) -> list[str]:
        """Same as `upstream` but returns short model names (not unique_ids).

        Sources are skipped because they don't have a corresponding dbt model
        name in `self._models`. Use `upstream` if you need source coverage.
        """
        out = []
        for uid in self.upstream(model_name):
            data = self._lineage_graph.nodes[uid]
            if data.get("kind") == "model" and "model_name" in data:
                out.append(data["model_name"])
        return out

    def downstream_named(self, model_name: str) -> list[str]:
        """Same as `downstream` but returns short model names."""
        out = []
        for uid in self.downstream(model_name):
            data = self._lineage_graph.nodes[uid]
            if data.get("kind") == "model" and "model_name" in data:
                out.append(data["model_name"])
        return out

    # ------------------------------------------------------------------
    # Phase 1: column-scoped BM25 (used by the SQL compiler to keep prompt
    # context small on wide tables — see `semantic_sql_compiler.py`).
    # ------------------------------------------------------------------

    def top_columns_for_model(
        self, model_name: str, query: str, top_k: int = 20
    ) -> list[str]:
        """Rank columns of `model_name` by BM25 relevance to `query`."""
        return self._bm25_columns.top_columns_for_model(
            model_name, query, top_k=top_k
        )

    def _traverse(
        self, start_id: str, graph: dict[str, list[str]], max_depth: int
    ) -> list[dict]:
        """BFS traversal of lineage graph."""
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(start_id, 0)]
        nodes: list[dict] = []

        while queue:
            node_id, depth = queue.pop(0)
            if node_id in visited or depth > max_depth:
                continue
            visited.add(node_id)

            if node_id != start_id:
                # Extract readable name from unique_id
                parts = node_id.split(".")
                node_type = parts[0] if parts else "unknown"
                node_name = parts[-1] if parts else node_id
                nodes.append({
                    "id": node_id,
                    "name": node_name,
                    "type": node_type,
                    "depth": depth,
                })

            if depth < max_depth:
                for neighbor in graph.get(node_id, []):
                    if neighbor not in visited:
                        queue.append((neighbor, depth + 1))

        return nodes

    def get_modules(self) -> dict[str, int]:
        """Return modules and their model counts."""
        return {mod: len(models) for mod, models in self._module_index.items()}

    def get_module_models(self, module: str) -> list[dict[str, str]]:
        """Return models for a specific module."""
        names = self._module_index.get(module.lower(), [])
        results = []
        for name in sorted(names):
            node = self._models.get(name, {})
            results.append({
                "name": name,
                "description": node.get("description", "")[:200],
                "materialized": node.get("config", {}).get("materialized", ""),
                "path": node.get("path", ""),
            })
        return results

    def get_sources_for_database(self, database: str) -> list[dict]:
        """Return source table definitions for a given database/schema."""
        results = []
        for key, node in self._sources.items():
            if node.get("schema", "") == database:
                columns = {}
                for col_name, col_meta in node.get("columns", {}).items():
                    columns[col_name] = {
                        "data_type": col_meta.get("data_type", ""),
                        "description": col_meta.get("description", ""),
                    }
                source_info: dict[str, Any] = {
                    "name": node.get("name", ""),
                    "identifier": node.get("identifier", node.get("name", "")),
                    "description": node.get("description", ""),
                    "columns": columns,
                }
                # Include freshness config if present
                freshness = node.get("freshness", {})
                if freshness:
                    source_info["freshness"] = freshness
                loaded_at = node.get("loaded_at_field", "")
                if loaded_at:
                    source_info["loaded_at_field"] = loaded_at
                # Include source meta
                meta = node.get("source_meta", {})
                if meta:
                    source_info["meta"] = {
                        k: v
                        for k, v in meta.items()
                        if k in ("owner", "authoritative")
                    }
                results.append(source_info)
        return results

    def get_tests_for_model(self, model_name: str) -> list[dict]:
        """Return all tests associated with a model."""
        return self._tests_by_model.get(model_name, [])

    def get_observability_summary(self) -> dict[str, Any]:
        """Return a summary of observability coverage across all models."""
        total = len(self._models)
        models_with_tests = sum(
            1 for name in self._models if name in self._tests_by_model
        )
        elementary_models = sum(
            1
            for tests in self._tests_by_model.values()
            if any(t.get("test_type") == "elementary" for t in tests)
        )

        # Count by Elementary test type
        elem_type_counts: dict[str, int] = {}
        for tests in self._tests_by_model.values():
            for t in tests:
                if t.get("test_type") == "elementary":
                    tname = t.get("test_name", "unknown")
                    elem_type_counts[tname] = elem_type_counts.get(tname, 0) + 1

        # Count by owner
        owner_counts = {
            owner: len(names) for owner, names in self._owner_index.items()
        }

        return {
            "total_models": total,
            "models_with_tests": models_with_tests,
            "models_with_elementary": elementary_models,
            "elementary_test_types": elem_type_counts,
            "owner_distribution": owner_counts,
        }


# Singleton instance
manifest = ManifestLoader()
