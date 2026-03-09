import hashlib
import json
import os
import time
from typing import Any, Optional

import requests

from cerebro_mcp.config import settings


class ManifestLoader:
    """Loads and indexes dbt manifest.json for efficient lookups."""

    def __init__(self):
        self._models: dict[str, dict] = {}
        self._sources: dict[str, dict] = {}
        self._parent_map: dict[str, list[str]] = {}
        self._child_map: dict[str, list[str]] = {}
        self._tags_index: dict[str, list[str]] = {}
        self._module_index: dict[str, list[str]] = {}
        self._loaded = False

        # Conditional GET state
        self._etag: str | None = None
        self._last_modified_header: str | None = None
        self._content_hash: str | None = None
        self._last_load_time: float = 0.0
        self._last_refresh_error: str | None = None

    def load(self) -> None:
        """Load manifest from URL or local file and build indexes."""
        data = self._fetch_manifest()
        if data:
            indexes = self._build_indexes_internal(data)
            self._apply_indexes(indexes)
            self._content_hash = self._hash_bytes(
                json.dumps(data, sort_keys=True).encode()
            )
            self._last_load_time = time.time()
            self._loaded = True

    def _fetch_manifest(self, conditional: bool = False) -> Optional[dict]:
        """Fetch manifest from URL with local file fallback.

        Args:
            conditional: If True, use conditional GET (If-None-Match/If-Modified-Since)
                        with a short timeout. Only applies to URL source.
        """
        # Try URL first
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
                    if not conditional:
                        print(f"Loaded manifest from {settings.DBT_MANIFEST_URL}")
                    return resp.json()

                error_msg = f"Failed to fetch manifest: HTTP {resp.status_code}"
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                print(error_msg)
            except Exception as e:
                error_msg = f"Error fetching manifest URL: {e}"
                if conditional:
                    self._last_refresh_error = error_msg
                    return None
                print(error_msg)

        if conditional:
            # Don't fall back to local file during refresh
            return None

        # Fallback to local file (initial load only, for dev convenience)
        if settings.DBT_MANIFEST_PATH and os.path.exists(settings.DBT_MANIFEST_PATH):
            try:
                with open(settings.DBT_MANIFEST_PATH, "r") as f:
                    data = json.load(f)
                print(f"Loaded manifest from {settings.DBT_MANIFEST_PATH}")
                return data
            except Exception as e:
                print(f"Error loading local manifest: {e}")

        print("Warning: No manifest loaded. dbt context tools will be unavailable.")
        return None

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        """Compute MD5 hash of bytes for content dedup."""
        return hashlib.md5(data).hexdigest()

    def reload_if_changed(self) -> tuple[bool, str | None]:
        """Check if manifest has changed and reload if so.

        Returns:
            Tuple of (changed, error). changed is True if indexes were updated.
        """
        if not settings.DBT_MANIFEST_URL:
            return False, None

        data = self._fetch_manifest(conditional=True)
        if data is None:
            return False, self._last_refresh_error

        new_hash = self._hash_bytes(json.dumps(data, sort_keys=True).encode())
        if new_hash == self._content_hash:
            return False, None

        # Build new indexes atomically
        indexes = self._build_indexes_internal(data)
        self._apply_indexes(indexes)
        self._content_hash = new_hash
        self._last_load_time = time.time()
        self._last_refresh_error = None
        return True, None

    def _build_indexes_internal(self, data: dict) -> dict:
        """Build lookup indexes from manifest data without mutating self.

        Returns a dict of all index data for atomic swap.
        """
        models: dict[str, dict] = {}
        sources: dict[str, dict] = {}
        tags_index: dict[str, list[str]] = {}
        module_index: dict[str, list[str]] = {}

        for key, node in data.get("nodes", {}).items():
            if node.get("resource_type") == "model":
                name = node["name"]
                models[name] = node

                for tag in node.get("tags", []):
                    tags_index.setdefault(tag, []).append(name)

                path = node.get("path", "")
                if "/" in path:
                    module = path.split("/")[0].lower()
                    module_index.setdefault(module, []).append(name)

        for key, node in data.get("sources", {}).items():
            source_key = f"{node.get('schema', '')}.{node.get('name', '')}"
            sources[source_key] = node

        parent_map = data.get("parent_map", {})
        child_map = data.get("child_map", {})

        print(
            f"Indexed {len(models)} models, "
            f"{len(sources)} sources, "
            f"{len(tags_index)} tags, "
            f"{len(module_index)} modules"
        )

        return {
            "models": models,
            "sources": sources,
            "parent_map": parent_map,
            "child_map": child_map,
            "tags_index": tags_index,
            "module_index": module_index,
        }

    def _apply_indexes(self, indexes: dict) -> None:
        """Atomically swap all index references."""
        self._models = indexes["models"]
        self._sources = indexes["sources"]
        self._parent_map = indexes["parent_map"]
        self._child_map = indexes["child_map"]
        self._tags_index = indexes["tags_index"]
        self._module_index = indexes["module_index"]

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

    def search_models(
        self,
        query: str = "",
        tags: Optional[list[str]] = None,
        module: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search models by name, description, or tags."""
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

        # Filter by query string (name or description match)
        results = []
        query_lower = query.lower()
        for name in sorted(candidates):
            node = self._models[name]
            if query:
                name_match = query_lower in name.lower()
                desc_match = query_lower in node.get("description", "").lower()
                if not name_match and not desc_match:
                    continue
            results.append({
                "name": name,
                "description": node.get("description", ""),
                "materialized": node.get("config", {}).get("materialized", ""),
                "tags": node.get("tags", []),
                "schema": node.get("schema", ""),
                "path": node.get("path", ""),
            })
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

        return {
            "name": model_name,
            "unique_id": unique_id,
            "description": node.get("description", ""),
            "table_name": f"{schema}.{alias}",
            "materialized": node.get("config", {}).get("materialized", ""),
            "tags": node.get("tags", []),
            "path": node.get("path", ""),
            "columns": columns,
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
                results.append({
                    "name": node.get("name", ""),
                    "identifier": node.get("identifier", node.get("name", "")),
                    "description": node.get("description", ""),
                    "columns": columns,
                })
        return results


# Singleton instance
manifest = ManifestLoader()
