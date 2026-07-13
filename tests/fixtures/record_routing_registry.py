"""Regenerate tests/fixtures/routing_registry.json.gz from the live registry.

Run from the repo root (needs the local semantic artifacts on disk —
SEMANTIC_REGISTRY_PATH in .env):

    uv run python tests/fixtures/record_routing_registry.py

Extended schema shared by the Suite-4 (search/routing) and Suite-5 (semantic)
benchmarks:

    metadata          registry metadata verbatim (manifest_hash, catalog_hash,
                      generated_at, model_count) + recorder-added registry_hash
    metrics           snap.metrics (full dicts — routing scores on these)
    synonym_index     snap.synonym_index
    dimension_index   snap.dimension_index
    models_exec       trimmed executable model shapes (relation_name,
                      dimensions, measures, entities, statuses — what the
                      planner/compiler read; columns omitted for size)
    relationships     snap.relationships (enriched/multi-branch planner paths)
    coverage_summary  registry coverage_summary (Section E baseline)

Re-record ONLY deliberately: the benchmark pins routing/planner/compiler
behavior against a stable registry on purpose. After re-recording, re-run
`python -m benchmarks.run --suite search` and `--suite semantic` and update
any pinned cases that changed in the SAME change set.
"""

from __future__ import annotations

import gzip
import json
import os


_MODEL_FIELDS = (
    "name",
    "module",
    "relation_name",
    "semantic_status",
    "quality_tier",
    "description",
    "tags",
    "dimensions",
    "measures",
    "entities",
)


def main() -> None:
    from cerebro_mcp.config import settings
    from cerebro_mcp.loaders.semantic import semantic_runtime

    snap = semantic_runtime.load()
    if snap is None:
        raise SystemExit("semantic snapshot unavailable — check local artifacts")

    metadata: dict = {}
    coverage_summary: dict = {}
    registry_path = settings.SEMANTIC_REGISTRY_PATH
    if registry_path and os.path.exists(registry_path):
        with open(registry_path, encoding="utf-8") as f:
            raw = json.load(f)
        metadata = dict(raw.get("metadata") or {})
        coverage_summary = dict(raw.get("coverage_summary") or {})
    metadata["registry_hash"] = snap.registry_hash

    models_exec: dict[str, dict] = {}
    for name, m in snap.models.items():
        trimmed = {k: m.get(k) for k in _MODEL_FIELDS if m.get(k) is not None}
        if "description" in trimmed:
            trimmed["description"] = str(trimmed["description"])[:240]
        models_exec[name] = trimmed

    payload = {
        "metadata": metadata,
        "metrics": snap.metrics,
        "synonym_index": snap.synonym_index,
        "dimension_index": snap.dimension_index,
        "models_exec": models_exec,
        "relationships": snap.relationships,
        "coverage_summary": coverage_summary,
    }

    out = os.path.join(os.path.dirname(__file__), "routing_registry.json.gz")
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), default=str)
    print(
        f"{len(models_exec)} models / {len(snap.metrics)} metrics / "
        f"{len(snap.relationships)} relationships -> {out} "
        f"({os.path.getsize(out) // 1024} KB)"
    )


if __name__ == "__main__":
    main()
