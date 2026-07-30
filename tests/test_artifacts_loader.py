"""ArtifactLoader — the shared remote-fetch path for four critical artifacts.

This module had ZERO coverage, which is how a `NameError` shipped to production
on its hot path: `settings` was referenced without being imported, the broad
`except Exception` in `_fetch_remote_json` turned it into a log warning, and the
semantic registry, catalog, semantic docs index and graph catalog all silently
failed to load while the full suite passed and every health probe stayed green.

The first test below is the direct regression guard: it exercises the real
remote path with only `requests.get` stubbed, so any unresolved module-level
name in that function fails the test instead of degrading production.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from cerebro_mcp.loaders.artifacts import ArtifactLoader, ArtifactPayload


BODY = {"schema_version": 1, "models": {}}


def _resp(status=200, body=None, etag='W/"abc"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT"):
    raw = json.dumps(body if body is not None else BODY).encode()
    resp = MagicMock()
    resp.status_code = status
    resp.content = raw
    resp.json.return_value = json.loads(raw)
    resp.headers = {"ETag": etag, "Last-Modified": last_modified}
    return resp


def _loader(**kw):
    return ArtifactLoader(url="https://example.test/artifact.json", label="test artifact", **kw)


def test_remote_fetch_succeeds_and_populates_payload():
    """Regression guard: the real fetch body must execute without NameError.

    Only requests.get is stubbed — every other name in _fetch_remote_json is
    resolved for real, which is exactly what was broken.
    """
    loader = _loader()
    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp()) as get:
        payload = loader._fetch_remote_json()

    assert get.call_count == 1
    assert isinstance(payload, ArtifactPayload)
    assert payload.body == BODY
    assert payload.content_hash, "content hash must be populated"
    assert payload.etag == 'W/"abc"'


def test_remote_fetch_uses_a_connect_read_timeout_tuple():
    """A scalar timeout is a per-socket-operation deadline, not a total one.

    Asserts the READ component only, so the test survives a change to the
    connect default — same shape as the manifest/docs loader tests.
    """
    loader = _loader()
    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp()) as get:
        loader._fetch_remote_json()

    timeout = get.call_args.kwargs["timeout"]
    assert isinstance(timeout, tuple), f"expected (connect, read) tuple, got {timeout!r}"
    connect, read = timeout
    assert connect > 0
    assert read == 30

    # Conditional GETs use a shorter read budget.
    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp()) as get:
        loader._fetch_remote_json(conditional=True)
    assert get.call_args.kwargs["timeout"][1] == 5


def test_conditional_304_returns_none_without_clobbering_state():
    loader = _loader()
    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp()):
        first = loader._fetch_remote_json()
    assert first is not None

    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp(status=304)):
        assert loader._fetch_remote_json(conditional=True) is None

    # The validators below depend on cached conditional-GET state surviving.
    assert loader._etag == 'W/"abc"'


def test_conditional_request_sends_validators_once_known():
    loader = _loader()
    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp()):
        loader._fetch_remote_json()

    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp(status=304)) as get:
        loader._fetch_remote_json(conditional=True)

    headers = get.call_args.kwargs["headers"]
    assert headers.get("If-None-Match") == 'W/"abc"'


def test_fetch_failure_is_recorded_not_silently_swallowed():
    """A failure must leave a trace a human can find.

    The bug this file exists for was invisible precisely because the failure
    path only wrote a warning; assert the error is at least retained on the
    loader so callers/status surfaces can report it.
    """
    loader = _loader()
    with patch(
        "cerebro_mcp.loaders.artifacts.requests.get",
        side_effect=RuntimeError("connection reset"),
    ):
        assert loader._fetch_remote_json() is None

    assert loader._last_refresh_error, "the failure must be retained, not dropped"
    assert "connection reset" in loader._last_refresh_error


def test_no_url_configured_is_not_an_error():
    loader = ArtifactLoader(url=None, label="test artifact")
    with patch("cerebro_mcp.loaders.artifacts.requests.get") as get:
        assert loader._fetch_remote_json() is None
    get.assert_not_called()


@pytest.mark.parametrize("status", [404, 500])
def test_non_200_returns_none(status):
    loader = _loader()
    with patch("cerebro_mcp.loaders.artifacts.requests.get", return_value=_resp(status=status)):
        assert loader._fetch_remote_json() is None
