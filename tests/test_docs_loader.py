import json
from unittest.mock import MagicMock, patch

from cerebro_mcp.loaders.docs import (
    DocsLoader,
    gnosis_chain_path_to_page_path,
    location_to_markdown_path,
    markdown_url_to_location,
    parse_gnosis_chain_llms,
    parse_llms_index,
)


def test_missing_docs_index_logs_without_stdout(caplog, capsys):
    loader = DocsLoader()

    with patch("cerebro_mcp.loaders.docs.settings") as mock_settings:
        mock_settings.DOCS_BASE_URL = ""
        mock_settings.DOCS_SEARCH_INDEX_URL = None
        mock_settings.DOCS_SEARCH_INDEX_PATH = ""
        mock_settings.GNOSIS_CHAIN_DOCS_LLM_URL = None
        with caplog.at_level("WARNING"):
            loader.load()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No docs index loaded" in caplog.text


def test_docs_index_load_success_uses_logging_not_stdout(caplog, capsys):
    loader = DocsLoader()
    data = {"docs": []}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data
    mock_resp.content = json.dumps(data).encode()
    mock_resp.headers = {}

    with patch(
        "cerebro_mcp.loaders.docs.requests.get",
        return_value=mock_resp,
    ):
        with patch("cerebro_mcp.loaders.docs.settings") as mock_settings:
            mock_settings.DOCS_BASE_URL = ""
            mock_settings.DOCS_SEARCH_INDEX_URL = "http://test.com/search_index.json"
            mock_settings.DOCS_SEARCH_INDEX_PATH = ""
            mock_settings.GNOSIS_CHAIN_DOCS_LLM_URL = None
            with caplog.at_level("INFO"):
                loader.load()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Loaded docs index from http://test.com/search_index.json" in caplog.text


def test_parse_llms_index_maps_markdown_urls_back_to_locations():
    text = """
# Gnosis Analytics

## Docs

- [Dispatcher](https://docs.analytics.gnosis.io/mcp/dispatcher/index.html.md): Top-level routing
""".strip()

    entries = parse_llms_index(text, "https://docs.analytics.gnosis.io/")

    assert entries["mcp/dispatcher/"]["section"] == "Docs"
    assert entries["mcp/dispatcher/"]["description"] == "Top-level routing"
    assert markdown_url_to_location(
        "https://docs.analytics.gnosis.io/",
        "https://docs.analytics.gnosis.io/mcp/dispatcher/index.html.md",
    ) == "mcp/dispatcher/"
    assert location_to_markdown_path("mcp/dispatcher/") == "mcp/dispatcher/index.html.md"


def test_get_chunk_prefers_markdown_mirror_over_search_index_text():
    loader = DocsLoader()
    loader._loaded = True
    loader._docs = [
        {
            "location": "mcp/dispatcher/",
            "title": "Dispatcher",
            "text": "search index fallback",
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "# Dispatcher\n\nMirror copy"

    with patch("cerebro_mcp.loaders.docs.requests.get", return_value=mock_resp):
        with patch("cerebro_mcp.loaders.docs.settings") as mock_settings:
            mock_settings.DOCS_BASE_URL = "https://docs.analytics.gnosis.io/"
            result = loader.get_chunk("mcp/dispatcher/")

    assert "Mirror copy" in result
    assert "search index fallback" not in result


def test_get_chunk_falls_back_to_search_index_when_mirror_unavailable():
    loader = DocsLoader()
    loader._loaded = True
    loader._docs = [
        {
            "location": "mcp/dispatcher/",
            "title": "Dispatcher",
            "text": "search index fallback",
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("cerebro_mcp.loaders.docs.requests.get", return_value=mock_resp):
        with patch("cerebro_mcp.loaders.docs.settings") as mock_settings:
            mock_settings.DOCS_BASE_URL = "https://docs.analytics.gnosis.io/"
            result = loader.get_chunk("mcp/dispatcher/")

    assert result == "search index fallback"


def test_get_docs_context_uses_full_artifact_when_requested():
    loader = DocsLoader()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "full context"

    with patch("cerebro_mcp.loaders.docs.requests.get", return_value=mock_resp) as mock_get:
        with patch("cerebro_mcp.loaders.docs.settings") as mock_settings:
            mock_settings.DOCS_BASE_URL = "https://docs.analytics.gnosis.io/"
            result = loader.get_context(full=True)

    assert result == "full context"
    mock_get.assert_called_once_with(
        "https://docs.analytics.gnosis.io/llms-ctx-full.txt",
        timeout=30,
    )


def test_parse_gnosis_chain_llms_splits_file_chunks_and_urls():
    text = (
        "// File: about/README\n"
        "# Introducing Gnosis Chain\n\n"
        "A resilient EVM chain.\n"
        "--- // File: about/networks/mainnet\n"
        "# Gnosis (Mainnet)\n\n"
        "Mainnet details.\n"
    )

    entries = parse_gnosis_chain_llms(text, "https://docs.gnosischain.com/")

    assert len(entries) == 2
    assert entries[0]["file_path"] == "about/README"
    assert entries[0]["title"] == "Introducing Gnosis Chain"
    assert entries[0]["description"] == "A resilient EVM chain."
    assert entries[0]["page_url"] == "https://docs.gnosischain.com/about/"
    assert gnosis_chain_path_to_page_path("about/README") == "about/"


def test_search_includes_gnosis_chain_docs_hits():
    loader = DocsLoader()
    loader._loaded = False
    loader._gnosis_chain_docs = [
        {
            "file_path": "about/networks/mainnet",
            "title": "Gnosis (Mainnet)",
            "description": "Mainnet details.",
            "text": "Chain ID 100 and xDAI.",
            "page_url": "https://docs.gnosischain.com/about/networks/mainnet",
        }
    ]

    result = loader.search("xDAI", limit=1)[0]

    assert result["source"] == "gnosis_chain"
    assert result["title"] == "Gnosis (Mainnet)"


def test_get_gnosis_chain_doc_chunk_matches_file_path():
    loader = DocsLoader()
    loader._gnosis_chain_docs = [
        {
            "file_path": "about/networks/mainnet",
            "title": "Gnosis (Mainnet)",
            "description": "Mainnet details.",
            "text": "Chain ID 100 and xDAI.",
            "page_url": "https://docs.gnosischain.com/about/networks/mainnet",
        }
    ]

    assert loader.get_gnosis_chain_chunk("about/networks/mainnet") == "Chain ID 100 and xDAI."
