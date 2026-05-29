"""Regression test for the post-deploy `manifest_hash_mismatch` self-heal.

The semantic registry embeds the manifest/catalog hashes it was built
against. A force reload that refreshes ONLY the registry pulls the new
embedded `manifest_hash` while leaving the stale in-memory manifest — which
re-asserts the mismatch instead of clearing it. `SemanticRuntime._refresh`
must reload the manifest + catalog alongside the registry so execution
becomes available again in one call, even when the registry payload itself
did not change.
"""

from __future__ import annotations

from types import SimpleNamespace

import cerebro_mcp.loaders.semantic as sem


class _FakeArtifact:
    """Minimal stand-in for the registry / docs ArtifactLoader."""

    def __init__(self, body, content_hash):
        self.payload = SimpleNamespace(body=body)
        self.content_hash = content_hash
        self.force_reload_calls = 0
        self.reload_if_changed_calls = 0

    def force_reload(self):
        self.force_reload_calls += 1
        return False, None  # registry already current; nothing new fetched

    def reload_if_changed(self):
        self.reload_if_changed_calls += 1
        return False, None


class _FakeHashLoader:
    """Stand-in for ManifestLoader/CatalogLoader: reload_if_changed advances
    the in-memory content_hash to the freshly-deployed value."""

    def __init__(self, current_hash, deployed_hash):
        self._current = current_hash
        self._deployed = deployed_hash
        self.is_loaded = True
        self.reload_calls = 0

    @property
    def content_hash(self):
        return self._current

    def reload_if_changed(self):
        self.reload_calls += 1
        changed = self._current != self._deployed
        self._current = self._deployed
        return changed, None


def test_force_reload_clears_manifest_hash_mismatch(monkeypatch):
    new_manifest_hash = "manifest-NEW"
    catalog_hash = "catalog-OK"

    registry_body = {
        "models": {},
        "relationships": [],
        "metadata": {
            "manifest_hash": new_manifest_hash,
            "catalog_hash": catalog_hash,
        },
    }

    monkeypatch.setattr(sem.settings, "SEMANTIC_ENABLED", True)
    monkeypatch.setattr(
        sem, "semantic_registry", _FakeArtifact(registry_body, "registry-hash")
    )
    monkeypatch.setattr(sem, "semantic_docs", _FakeArtifact([], "docs-hash"))
    # Manifest in memory is STALE ("manifest-OLD"); the deploy published
    # "manifest-NEW", which is what the new registry embeds.
    fake_manifest = _FakeHashLoader("manifest-OLD", new_manifest_hash)
    fake_catalog = _FakeHashLoader(catalog_hash, catalog_hash)
    monkeypatch.setattr(sem, "manifest", fake_manifest)
    monkeypatch.setattr(sem, "catalog", fake_catalog)

    runtime = sem.SemanticRuntime()

    # Before: simulate the stuck post-deploy state.
    snapshot = runtime._build_snapshot(registry_body, [])
    runtime._snapshot = snapshot
    available, reason = runtime._execution_state(snapshot)
    assert available is False
    assert reason == "manifest_hash_mismatch"

    # Force reload: even though the registry payload itself didn't change,
    # reloading the manifest advances its hash to match the embedded one.
    changed, error = runtime.force_reload()

    assert error is None
    assert changed is True  # manifest moved, so the snapshot was rebuilt
    assert fake_manifest.reload_calls == 1
    assert fake_catalog.reload_calls == 1
    assert runtime.is_execution_available is True
    assert runtime.stale_reason is None
