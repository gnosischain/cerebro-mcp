"""Spawn a real ``cerebro-mcp --sse`` subprocess for the load suite.

Pure stdlib on purpose: this module must be importable before any
``cerebro_mcp`` env is read, and the server it spawns gets its OWN process
env. The artifact-path env vars (report dir, logs, event store, ...) are
already redirected into the run's scratch dir by ``benchmarks.run`` before
this class is instantiated, so the subprocess simply inherits ``os.environ``
plus the SSE binding and a local-only bearer token.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, IO


class BenchServer:
    """Context manager around a ``uv run cerebro-mcp --sse`` subprocess.

    Usage::

        with BenchServer(port=8091, scratch_dir=ctx.scratch_dir) as server:
            server.wait_healthy()
            ... connect to server.sse_url with server.token ...

    ``__enter__`` only spawns; readiness is a separate explicit
    :meth:`wait_healthy` call so callers can distinguish "failed to spawn"
    from "spawned but ClickHouse unreachable".
    """

    def __init__(
        self,
        port: int,
        scratch_dir: Path,
        *,
        host: str = "127.0.0.1",
        token: str = "bench-local",
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.scratch_dir = Path(scratch_dir)
        self.log_path = self.scratch_dir / "server.log"
        self.proc: subprocess.Popen | None = None
        self._log_handle: IO[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def sse_url(self) -> str:
        return f"{self.base_url}/sse"

    @property
    def metrics_url(self) -> str:
        return f"{self.base_url}/metrics"

    def start(self) -> None:
        if self.proc is not None:
            return
        env = dict(os.environ)
        env.update(
            {
                "FASTMCP_HOST": self.host,
                "FASTMCP_PORT": str(self.port),
                "MCP_AUTH_TOKEN": self.token,
            }
        )
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self._log_handle = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            ["uv", "run", "cerebro-mcp", "--sse"],
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )

    def wait_healthy(self, timeout: float = 90.0) -> dict[str, Any]:
        """Poll ``/health`` until 200, failing FAST on 503.

        Connection-refused means uvicorn hasn't bound yet — keep retrying.
        HTTP 503 means the server IS up but ClickHouse is unreachable; more
        waiting cannot fix that, so raise immediately with the health body.
        Returns the parsed 200 health payload.
        """
        deadline = time.monotonic() + timeout
        url = f"{self.base_url}/health"
        last_err = "no poll attempted"
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(
                    f"server process exited (code {self.proc.returncode}) before "
                    f"becoming healthy — see {self.log_path}"
                )
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
                    last_err = f"HTTP {resp.status}"
            except urllib.error.HTTPError as exc:
                if exc.code == 503:
                    body = ""
                    try:
                        body = exc.read().decode("utf-8", errors="replace")[:300]
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"server up but ClickHouse unreachable (/health 503): {body}"
                    ) from exc
                last_err = f"HTTP {exc.code}"
            except urllib.error.URLError as exc:
                last_err = f"{exc.reason}"  # connection refused during bind — retry
            except Exception as exc:  # noqa: BLE001 — transient socket errors
                last_err = str(exc)
            time.sleep(0.5)
        raise TimeoutError(
            f"server on {url} not healthy after {timeout:.0f}s (last: {last_err}) "
            f"— see {self.log_path}"
        )

    def stop(self) -> None:
        """Terminate, wait up to 10s, then kill. Idempotent."""
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            finally:
                self._log_handle = None

    def __enter__(self) -> "BenchServer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
