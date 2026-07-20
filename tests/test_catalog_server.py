"""Safe local catalog-server lifecycle checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "dev" / "catalog_server.py"
SPEC = importlib.util.spec_from_file_location("catalog_server", SCRIPT)
assert SPEC and SPEC.loader
catalog_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog_server)


def test_pid_file_does_not_prove_ownership_after_pid_reuse(monkeypatch):
    state = {
        "pid": 123,
        "repo": str(catalog_server.REPO_ROOT),
        "command": ["uv", "run", "cerebro-mcp", "--sse"],
    }
    monkeypatch.setattr(catalog_server, "_cwd", lambda _pid: Path("/tmp/other"))
    monkeypatch.setattr(
        catalog_server, "_command", lambda _pid: "python unrelated_server.py"
    )
    assert catalog_server._owned(123, state) is False


def test_repo_cwd_and_server_fingerprint_prove_ownership(monkeypatch):
    monkeypatch.setattr(catalog_server, "_cwd", lambda _pid: catalog_server.REPO_ROOT)
    monkeypatch.setattr(
        catalog_server,
        "_command",
        lambda _pid: "uv run cerebro-mcp --sse",
    )
    assert catalog_server._owned(456, {}) is True


def test_stop_refuses_unknown_listener_without_signalling(monkeypatch, capsys):
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(catalog_server, "_read_state", lambda _port: {})
    monkeypatch.setattr(catalog_server, "_listener_pids", lambda _port: [789])
    monkeypatch.setattr(catalog_server, "_owned", lambda _pid, _state: False)
    monkeypatch.setattr(catalog_server, "_command", lambda _pid: "other")
    monkeypatch.setattr(catalog_server.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    assert catalog_server.stop(8010) == 2
    assert signalled == []
    assert "refusing to stop unknown" in capsys.readouterr().err
