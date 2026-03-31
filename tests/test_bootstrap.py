import sys
import types
from pathlib import Path

import pytest

from cerebro_mcp import bootstrap


def test_validate_remote_transport_auth_requires_token_when_secure(monkeypatch):
    monkeypatch.setattr(
        bootstrap.settings,
        "ALLOW_INSECURE_REMOTE_TRANSPORT",
        False,
    )

    with pytest.raises(RuntimeError):
        bootstrap.validate_remote_transport_auth(None)


def test_validate_remote_transport_auth_allows_insecure_override(monkeypatch):
    monkeypatch.setattr(
        bootstrap.settings,
        "ALLOW_INSECURE_REMOTE_TRANSPORT",
        True,
    )

    bootstrap.validate_remote_transport_auth(None)


def test_init_ssl_trust_handles_missing_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)
    result = bootstrap.init_ssl_trust()
    assert result is False


def test_init_ssl_trust_returns_true_when_injection_succeeds(monkeypatch):
    module = types.SimpleNamespace(inject_into_ssl=lambda: None)
    monkeypatch.setitem(sys.modules, "truststore", module)
    assert bootstrap.init_ssl_trust() is True


def test_ensure_writable_dir_creates_and_cleans_probe(tmp_path):
    target = Path(tmp_path) / "research"
    bootstrap.ensure_writable_dir(target)

    assert target.exists()
    assert not (target / ".write_test").exists()


def test_ensure_writable_dir_expands_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = Path("~/research")

    bootstrap.ensure_writable_dir(target)

    assert (tmp_path / "research").exists()


def test_ensure_writable_dir_raises_clear_error(monkeypatch, tmp_path):
    def fail_write(*args, **kwargs):
        raise PermissionError("read only")

    monkeypatch.setattr(Path, "write_text", fail_write)

    with pytest.raises(RuntimeError) as exc_info:
        bootstrap.ensure_writable_dir(tmp_path / "research")

    assert "CEREBRO_RESEARCH_DIR" in str(exc_info.value)
