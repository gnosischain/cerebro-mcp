import json
import os
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

    def load(self) -> None:
        """Load manifest from URL or local file and build indexes."""
        data = self._fetch_manifest()
        if data:
            self._build_indexes(data)
            self._loaded = True

    def _fetch_manifest(self) -> Optional[dict]:
        """Fetch manifest from URL with local file fallback."""
        # Try URL first
        if settings.DBT_MANIFEST_URL:
            try:
                resp = requests.get(settings.DBT_MANIFEST_URL, timeout=30)
                if resp.status_code == 200:
                    print(f"Loaded manifest from {settings.DBT_MANIFEST_URL}")
                    return resp.json()
                print(f"Failed to fetch manifest: HTTP {resp.status_code}")
            except Exception as e:
                print(f"Error fetching manifest URL: {e}")

        # Fallback to local file
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

    def _build_indexes(self, data: dict) -> None:
        """Build lookup indexes from manifest data."""
        # Index models
        for key, node in data.get("nodes", {}).items():
            if node.get("resource_type") == "model":
                name = node["name"]
                self._models[name] = node

                # Tags index
                for tag in node.get("tags", []):
                    self._tags_index.setdefault(tag, []).append(name)

                # Module index (first directory in the path)
                path = node.get("path", "")
                if "/" in path:
                    module = path.split("/")[0]
                    self._module_index.setdefault(module, []).append(name)

        # Index sources
        for key, node in data.get("sources", {}).items():
            source_key = f"{node.get('schema', '')}.{node.get('name', '')}"
            self._sources[source_key] = node

        # Lineage maps
        self._parent_map = data.get("parent_map", {})
        self._child_map = data.get("child_map", {})

        print(
            f"Indexed {len(self._models)} models, "
            f"{len(self._sources)} sources, "
            f"{len(self._tags_index)} tags, "
            f"{len(self._module_index)} modules"
        )

    @property
    def is_loaded(self) -> bool:
        return self._loaded

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

        # Filter by module
        if module:
            module_models = set(self._module_index.get(module, []))
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
        names = self._module_index.get(module, [])
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
