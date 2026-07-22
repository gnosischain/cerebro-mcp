from __future__ import annotations

from pathlib import Path

from cerebro_mcp.tools.semantic import graph_explorer


def _reset_bundle_cache() -> None:
    graph_explorer._BUNDLED_HTML = None
    graph_explorer._BUNDLED_HTML_SIGNATURE = None
    graph_explorer._BUNDLED_HTML_SHA256 = None
    graph_explorer._BUNDLED_HTML_MTIME = None
    graph_explorer._WEB_BUNDLED_HTML = None
    graph_explorer._WEB_BUNDLED_HTML_SIGNATURE = None
    graph_explorer._WEB_BUNDLED_HTML_SHA256 = None
    graph_explorer._WEB_BUNDLED_HTML_MTIME = None


def test_web_bundle_cache_invalidates_on_file_change(monkeypatch, tmp_path: Path):
    shell = tmp_path / "graph_explorer_web.html"
    shell.write_text("<html>first</html>")
    monkeypatch.setattr(graph_explorer, "_web_bundle_resource", lambda: shell)
    _reset_bundle_cache()

    first = graph_explorer.get_graph_explorer_web_html()
    shell.write_text("<html>second-build</html>")
    second = graph_explorer.get_graph_explorer_web_html()

    assert first == "<html>first</html>"
    assert second == "<html>second-build</html>"
    assert graph_explorer._WEB_BUNDLED_HTML_SIGNATURE is not None
    assert graph_explorer._WEB_BUNDLED_HTML_SHA256 is not None


def test_missing_web_bundle_falls_back_to_inline(monkeypatch, tmp_path: Path):
    missing = tmp_path / "missing.html"
    monkeypatch.setattr(graph_explorer, "_web_bundle_resource", lambda: missing)
    monkeypatch.setattr(
        graph_explorer,
        "get_graph_explorer_html",
        lambda: "<html>inline</html>",
    )
    graph_explorer._BUNDLED_HTML_MTIME = "2026-07-21T00:00:00Z"
    graph_explorer._WEB_BUNDLED_HTML = None
    graph_explorer._WEB_BUNDLED_HTML_SIGNATURE = None

    assert graph_explorer.get_graph_explorer_web_html() == "<html>inline</html>"
    assert graph_explorer._WEB_BUNDLED_HTML_SIGNATURE is None
