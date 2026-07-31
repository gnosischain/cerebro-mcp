#!/usr/bin/env python3
"""Safely manage the local Cerebro mini-app server.

The controller never kills a process merely because it owns the requested
port. A listener is manageable only when it was recorded by this repository or
its current working directory and command identify this checkout's server.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    try:
        value = int(os.environ.get("FASTMCP_PORT", "8010"))
    except ValueError as exc:
        raise SystemExit("FASTMCP_PORT must be an integer") from exc
    if not 1 <= value <= 65535:
        raise SystemExit("FASTMCP_PORT must be between 1 and 65535")
    return value


def _state_path(port: int) -> Path:
    key = hashlib.sha256(str(REPO_ROOT).encode()).hexdigest()[:16]
    root = Path(tempfile.gettempdir()) / "cerebro-mcp-catalog" / key
    root.mkdir(parents=True, exist_ok=True)
    return root / f"catalog-{port}.json"


def _read_state(port: int) -> dict[str, Any]:
    path = _state_path(port)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(port: int, pid: int, command: list[str]) -> None:
    path = _state_path(port)
    payload = {
        "pid": pid,
        "port": port,
        "repo": str(REPO_ROOT),
        "command": command,
        "started_at": time.time(),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _remove_state(port: int) -> None:
    try:
        _state_path(port).unlink()
    except FileNotFoundError:
        pass


def _listener_pids(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(
        {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}
    )


def _command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unavailable"
    return result.stdout.strip()


def _cwd(pid: int) -> Path | None:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            try:
                return Path(line[1:]).resolve()
            except OSError:
                return None
    return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _owned(pid: int, state: dict[str, Any]) -> bool:
    recorded = int(state.get("pid", 0) or 0) == pid and state.get("repo") == str(
        REPO_ROOT
    )
    command = _command(pid)
    cwd = _cwd(pid)
    fingerprint = "cerebro-mcp" in command and "--sse" in command
    # A PID file alone is not ownership proof: operating systems reuse PIDs.
    # A matching listener must also carry this repository's cwd + command
    # fingerprint before stop/restart may signal it.
    if recorded:
        recorded_command = " ".join(str(part) for part in state.get("command") or [])
        state_fingerprint = (
            "cerebro-mcp" in recorded_command and "--sse" in recorded_command
        )
        return bool(cwd == REPO_ROOT and fingerprint and state_fingerprint)
    return bool(cwd == REPO_ROOT and fingerprint)


def _health(port: int, token: str) -> dict[str, Any] | None:
    url = f"http://127.0.0.1:{port}/app/graph_explorer/health?token={token}"
    try:
        with urlopen(url, timeout=2) as response:  # noqa: S310 - fixed localhost
            value = json.loads(response.read().decode())
            return value if isinstance(value, dict) else None
    except (OSError, URLError, json.JSONDecodeError):
        return None


def status(port: int, token: str) -> int:
    state = _read_state(port)
    listeners = _listener_pids(port)
    if not listeners:
        print(f"catalog server is not listening on port {port}")
        return 0
    for pid in listeners:
        ownership = "managed" if _owned(pid, state) else "unknown"
        print(f"port {port}: pid {pid} ({ownership}) {_command(pid)}")
    health = _health(port, token)
    if health:
        print(json.dumps(health, indent=2, sort_keys=True))
    return 0


def stop(port: int) -> int:
    state = _read_state(port)
    recorded_pid = int(state.get("pid", 0) or 0)
    targets = set(_listener_pids(port))
    if recorded_pid and _alive(recorded_pid):
        targets.add(recorded_pid)
    if not targets:
        _remove_state(port)
        print(f"catalog server is already stopped on port {port}")
        return 0
    unknown = [pid for pid in targets if not _owned(pid, state)]
    if unknown:
        details = "; ".join(f"{pid}: {_command(pid)}" for pid in unknown)
        print(f"refusing to stop unknown listener/process: {details}", file=sys.stderr)
        return 2
    for pid in sorted(targets):
        if _alive(pid):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in targets):
            _remove_state(port)
            print(f"stopped catalog server on port {port}")
            return 0
        time.sleep(0.1)
    print("catalog server did not stop within 10 seconds", file=sys.stderr)
    return 1


def serve(port: int, token: str) -> int:
    listeners = _listener_pids(port)
    state = _read_state(port)
    if listeners:
        if all(_owned(pid, state) for pid in listeners):
            print(f"catalog server already running on port {port}")
            return status(port, token)
        details = "; ".join(f"{pid}: {_command(pid)}" for pid in listeners)
        print(f"port {port} is owned by an unknown process: {details}", file=sys.stderr)
        return 2

    command = ["uv", "run", "cerebro-mcp", "--sse"]
    environment = os.environ.copy()
    environment["MCP_AUTH_TOKEN"] = token
    environment["FASTMCP_PORT"] = str(port)
    # Remote transports must NAME a surface profile (fail-closed boot), so
    # the connector profile can never be silently absent. This dev server
    # wants today's full surface: say so explicitly rather than inheriting
    # it from an empty default. Overridable for testing another profile.
    environment.setdefault("MCP_SURFACE_PROFILE", "internal_full")
    # The mini-app browser plane is what this server exists to serve, so
    # opt it back in (it is default-OFF because serve_app echoes the
    # caller credential into the page and its POST dispatch runs tools on
    # it — acceptable for a local single-user dev server, not for a shared
    # deployment). See docs/deploy/connector_breaking_changes.md.
    environment.setdefault("MINI_APP_BROWSER_ENABLED", "true")
    child = subprocess.Popen(command, cwd=REPO_ROOT, env=environment)
    _write_state(port, child.pid, command)

    def forward(signum, _frame):
        if child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    deadline = time.monotonic() + 45
    health = None
    while time.monotonic() < deadline and child.poll() is None:
        health = _health(port, token)
        if health:
            break
        time.sleep(0.25)
    if health:
        print(
            f"open http://localhost:{port}/app/graph_explorer?token={token}\n"
            f"bundle {health.get('bundle_sha256', 'unknown')}"
        )
    else:
        print("server did not become healthy within 45 seconds", file=sys.stderr)
    try:
        return child.wait()
    finally:
        _remove_state(port)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "status", "stop", "restart"))
    args = parser.parse_args()
    port = _port()
    token = os.environ.get("MCP_AUTH_TOKEN", "dev")
    if args.action == "status":
        return status(port, token)
    if args.action == "stop":
        return stop(port)
    if args.action == "restart":
        code = stop(port)
        return code if code else serve(port, token)
    return serve(port, token)


if __name__ == "__main__":
    raise SystemExit(main())
