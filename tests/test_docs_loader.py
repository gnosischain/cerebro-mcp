import json
from unittest.mock import MagicMock, patch

from cerebro_mcp.docs_loader import DocsLoader


def test_missing_docs_index_logs_without_stdout(caplog, capsys):
    loader = DocsLoader()

    with patch("cerebro_mcp.docs_loader.settings") as mock_settings:
        mock_settings.DOCS_SEARCH_INDEX_URL = None
        mock_settings.DOCS_SEARCH_INDEX_PATH = ""
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
        "cerebro_mcp.docs_loader.requests.get",
        return_value=mock_resp,
    ):
        with patch("cerebro_mcp.docs_loader.settings") as mock_settings:
            mock_settings.DOCS_SEARCH_INDEX_URL = "http://test.com/search_index.json"
            mock_settings.DOCS_SEARCH_INDEX_PATH = ""
            with caplog.at_level("INFO"):
                loader.load()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Loaded docs index from http://test.com/search_index.json" in caplog.text
