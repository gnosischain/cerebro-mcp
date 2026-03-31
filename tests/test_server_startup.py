import importlib
import sys
from pathlib import Path


def test_server_import_does_not_load_manifest_or_docs(monkeypatch):
    sys.modules.pop("cerebro_mcp.server", None)

    from cerebro_mcp.catalog_loader import catalog
    from cerebro_mcp.docs_loader import docs_index
    from cerebro_mcp.manifest_loader import manifest
    from cerebro_mcp.semantic_loader import semantic_runtime

    calls = {"manifest": 0, "catalog": 0, "docs": 0, "semantic": 0}

    def manifest_load():
        calls["manifest"] += 1

    def catalog_load():
        calls["catalog"] += 1

    def docs_load():
        calls["docs"] += 1

    def semantic_load():
        calls["semantic"] += 1

    monkeypatch.setattr(manifest, "load", manifest_load)
    monkeypatch.setattr(catalog, "load", catalog_load)
    monkeypatch.setattr(docs_index, "load", docs_load)
    monkeypatch.setattr(semantic_runtime, "load", semantic_load)

    importlib.import_module("cerebro_mcp.server")

    assert calls == {"manifest": 0, "catalog": 0, "docs": 0, "semantic": 0}


def test_main_loads_manifest_and_docs_before_stdio_run(monkeypatch, tmp_path):
    sys.modules.pop("cerebro_mcp.server", None)
    server = importlib.import_module("cerebro_mcp.server")
    events: list[object] = []
    research_dir = tmp_path / "research"

    monkeypatch.setattr(server, "RESEARCH_DIR", research_dir)
    monkeypatch.setattr(server, "setup_logging", lambda: events.append("logging"))
    monkeypatch.setattr(server.manifest, "load", lambda: events.append("manifest"))
    monkeypatch.setattr(server.catalog, "load", lambda: events.append("catalog"))
    monkeypatch.setattr(server.docs_index, "load", lambda: events.append("docs"))
    monkeypatch.setattr(server.semantic_runtime, "load", lambda: events.append("semantic"))
    monkeypatch.setattr(
        server,
        "ensure_writable_dir",
        lambda path: events.append(("dir", Path(path))),
    )
    monkeypatch.setattr(server.mcp, "run", lambda transport: events.append(("run", transport)))
    monkeypatch.setattr(sys, "argv", ["cerebro-mcp"])

    server.main()

    assert events == [
        "logging",
        ("dir", research_dir),
        "manifest",
        "catalog",
        "docs",
        "semantic",
        ("run", "stdio"),
    ]
