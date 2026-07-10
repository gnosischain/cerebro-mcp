"""Regenerate tests/fixtures/search_corpus.json.gz from the live registry.

Run from the repo root (needs the local semantic artifacts on disk):

    uv run python tests/fixtures/record_search_corpus.py

Re-record ONLY when the golden queries in tests/test_search_quality.py need
models that the current fixture lacks — the suite pins ranking behavior
against a stable corpus on purpose, so gratuitous re-records weaken it.
After re-recording, re-run the suite and fix any pairs that now miss.
"""

from __future__ import annotations

import gzip
import json
import os


def main() -> None:
    from cerebro_mcp.loaders.semantic import semantic_runtime

    snap = semantic_runtime.load()
    if snap is None:
        raise SystemExit("semantic snapshot unavailable — check local artifacts")

    corpus: dict[str, dict] = {}
    for name, m in snap.models.items():
        cols_field = m.get("columns") or {}
        if isinstance(cols_field, dict):
            cols = {c: (meta or {}).get("data_type", "") for c, meta in cols_field.items()}
        else:
            cols = {
                c.get("name", ""): c.get("data_type", "")
                for c in cols_field
                if isinstance(c, dict)
            }
        corpus[name] = {
            "description": (m.get("description") or "")[:240],
            "tags": list(m.get("tags") or []),
            "module": m.get("module") or "",
            "owner": m.get("owner") or "",
            "path": m.get("path") or "",
            "columns": cols,
        }

    out = os.path.join(os.path.dirname(__file__), "search_corpus.json.gz")
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(corpus, f, separators=(",", ":"))
    print(f"{len(corpus)} models -> {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
