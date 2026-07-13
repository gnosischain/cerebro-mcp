"""Shared builders over the recorded search corpus + routing registry fixtures.

Used by both the pytest golden suite (``tests/test_search_quality.py``) and the
benchmark harness (``benchmarks/``) so the corpus -> snapshot / manifest
reconstruction logic never forks.

Fixtures on disk (both regenerated deliberately, never gratuitously):

- ``tests/fixtures/search_corpus.json.gz`` — model corpus (descriptions, tags,
  columns). Recorder: ``tests/fixtures/record_search_corpus.py``.
- ``tests/fixtures/routing_registry.json.gz`` — semantic registry snapshot
  (metrics, synonym/dimension indexes, executable model shapes, relationships,
  coverage summary). Recorder: ``tests/fixtures/record_routing_registry.py``.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SEARCH_CORPUS_PATH = FIXTURES_DIR / "search_corpus.json.gz"
ROUTING_REGISTRY_PATH = FIXTURES_DIR / "routing_registry.json.gz"


def load_search_corpus() -> dict[str, dict]:
    with gzip.open(SEARCH_CORPUS_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_routing_registry() -> dict[str, Any]:
    with gzip.open(ROUTING_REGISTRY_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def build_snapshot(corpus: dict[str, dict]):
    """Search-surface snapshot from the model corpus (no metrics/graph)."""
    from cerebro_mcp.models.semantic import SemanticSnapshot

    models = {}
    for name, m in corpus.items():
        models[name] = {
            "name": name,
            "description": m["description"],
            "tags": list(m["tags"]),
            "module": m["module"],
            "owner": m["owner"],
            "path": m["path"],
            "materialized": "table",
            "semantic_status": "approved",
            "quality_tier": "approved",
            "relation_name": f"`dbt`.`{name}`",
            "columns": {c: {"data_type": t} for c, t in m["columns"].items()},
        }
    return SemanticSnapshot(
        registry_hash="golden-corpus-1",
        manifest_hash="",
        catalog_hash="",
        docs_hash="",
        graph={"adjacency": {}},
        vertex_ids={},
        synonym_index={},
        dimension_index={},
        metrics={},
        models=models,
        relationships=[],
        docs_index={},
        loaded_at=0.0,
    )


def build_manifest_nodes(corpus: dict[str, dict]) -> dict[str, dict]:
    nodes = {}
    for name, m in corpus.items():
        uid = f"model.gnosis_dbt.{name}"
        nodes[uid] = {
            "resource_type": "model",
            "unique_id": uid,
            "name": name,
            "description": m["description"],
            "schema": "dbt",
            "alias": name,
            "path": m["path"] or f"{m['module']}/{name}.sql",
            "tags": list(m["tags"]),
            "config": {"materialized": "table", "meta": {"owner": m["owner"]}},
            "columns": {
                c: {"data_type": t, "description": ""}
                for c, t in m["columns"].items()
            },
            "depends_on": {"nodes": []},
        }
    return nodes


def build_manifest_loader(corpus: dict[str, dict]):
    """Standalone ManifestLoader over the corpus (search_models surface)."""
    from cerebro_mcp.loaders.manifest import ManifestLoader

    loader = ManifestLoader()
    indexes = loader._build_indexes_internal(
        {"nodes": build_manifest_nodes(corpus), "sources": {}, "parent_map": {}, "child_map": {}}
    )
    loader._apply_indexes(indexes)
    loader._loaded = True
    return loader


def install_fixture_manifest(corpus: dict[str, dict]) -> None:
    """Point the process-global manifest singleton at the corpus (bench use)."""
    from cerebro_mcp.loaders.manifest import manifest

    indexes = manifest._build_indexes_internal(
        {"nodes": build_manifest_nodes(corpus), "sources": {}, "parent_map": {}, "child_map": {}}
    )
    manifest._apply_indexes(indexes)
    manifest._loaded = True
