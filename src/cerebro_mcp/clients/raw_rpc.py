"""Raw JSON-RPC client for bulk Gnosis Chain scans.

The existing ``GnosisRpcManager`` (clients/web3.py) stays the path for
single-call contract tools. The scan engine needs things web3.py's
HTTPProvider does not give us: hand-built ``eth_getLogs`` windows without
middleware coercion, ``trace_filter`` / ``debug_traceTransaction``, and
Multicall3 ``eth_call`` payloads — at 10^5+ calls the per-call object
conversion is also measurable. Sessions are thread-local so 30-worker
sweeps never share a mutable ``requests.Session``.
"""
from __future__ import annotations

import itertools
import threading
import time
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter

from cerebro_mcp.config import settings


# JSON-RPC errors that no amount of retrying will fix.
_NON_RETRYABLE_CODES = {-32601, -32602}  # method not found / invalid params


class RpcError(RuntimeError):
    """A JSON-RPC level error (the HTTP request itself succeeded)."""

    def __init__(self, method: str, code: int | None, message: str):
        super().__init__(f"{method}: [{code}] {message}")
        self.method = method
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.code not in _NON_RETRYABLE_CODES

    @property
    def method_unsupported(self) -> bool:
        return self.code == -32601


class RawRpcClient:
    """Thread-safe raw JSON-RPC over one HTTP endpoint.

    One ``requests.Session`` per thread (``threading.local``); request ids
    come from ``itertools.count`` which is atomic under the GIL.
    """

    def __init__(self, url: str, timeout: float | None = None):
        self._url = url
        self._timeout = timeout or settings.RPC_SCAN_RPC_TIMEOUT_SECONDS
        self._ids = itertools.count(1)
        self._local = threading.local()

    @property
    def url(self) -> str:
        return self._url

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            self._local.session = session
        return session

    def request(self, method: str, params: list[Any]) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        resp = self._session().post(self._url, json=body, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload and payload["error"]:
            err = payload["error"]
            raise RpcError(method, err.get("code"), str(err.get("message", "unknown")))
        return payload.get("result")


TEACHING_TRACE_UNSUPPORTED = (
    "trace_filter is not available on this node (JSON-RPC -32601). Gnosis "
    "archive nodes running Erigon expose trace_filter/trace_block; Nethermind "
    "needs the Trace module enabled. Configure GNOSIS_ARCHIVE_RPC_URL to a "
    "trace-capable endpoint."
)
TEACHING_DEBUG_UNSUPPORTED = (
    "debug_traceTransaction is not available on this node (JSON-RPC -32601). "
    "Enable the debug API (Erigon: --http.api includes 'debug'; Nethermind: "
    "Debug module) or point GNOSIS_ARCHIVE_RPC_URL at a debug-capable endpoint."
)


class RpcRouter:
    """Standard vs archive raw endpoints.

    Mirrors ``GnosisRpcManager`` semantics: lazy construction, archive is
    optional and raises a clear error when missing, no I/O at import time.
    """

    def __init__(self, standard_url: str, archive_url: str = "",
                 timeout: float | None = None):
        self._standard_url = standard_url
        self._archive_url = archive_url
        self._timeout = timeout
        self._standard: RawRpcClient | None = None
        self._archive: RawRpcClient | None = None
        self._lock = threading.Lock()
        self._capabilities: dict[str, bool] = {}

    @classmethod
    def from_settings(cls) -> "RpcRouter":
        return cls(settings.GNOSIS_RPC_URL, settings.GNOSIS_ARCHIVE_RPC_URL)

    @property
    def standard(self) -> RawRpcClient:
        with self._lock:
            if self._standard is None:
                self._standard = RawRpcClient(self._standard_url, self._timeout)
            return self._standard

    @property
    def archive(self) -> RawRpcClient:
        if not self._archive_url:
            raise ValueError(
                "This operation requires historical state: set GNOSIS_ARCHIVE_RPC_URL."
            )
        with self._lock:
            if self._archive is None:
                self._archive = RawRpcClient(self._archive_url, self._timeout)
            return self._archive

    def has_archive(self) -> bool:
        return bool(self._archive_url)

    def for_capability(self, *, needs_archive: bool) -> RawRpcClient:
        if needs_archive:
            return self.archive
        if self._archive_url and settings.RPC_SCAN_PREFER_ARCHIVE_FOR_LOGS:
            return self.archive
        return self.standard

    def supports(self, method: str) -> bool:
        """Cached capability probe for optional node APIs.

        -32601 means the method does not exist on the node — definitively
        unsupported. Any other outcome (including transient errors) is
        treated as supported; the real call will surface real errors.
        """
        if method not in self._capabilities:
            try:
                self._probe(method)
                self._capabilities[method] = True
            except RpcError as exc:
                if exc.method_unsupported:
                    self._capabilities[method] = False
                else:
                    self._capabilities[method] = True
            except Exception:
                # Network blip during the probe: don't cache a verdict.
                return True
        return self._capabilities[method]

    def _probe(self, method: str) -> None:
        client = self.archive if self.has_archive() else self.standard
        if method == "trace_filter":
            head = self.latest_block()
            client.request(
                "trace_filter",
                [{"fromBlock": hex(head), "toBlock": hex(head)}],
            )
        elif method == "debug_traceTransaction":
            # A bogus-but-well-formed hash: unsupported nodes answer -32601
            # before they ever look the tx up.
            client.request(
                "debug_traceTransaction",
                ["0x" + "00" * 32, {"tracer": "callTracer"}],
            )
        else:
            client.request(method, [])

    def retry(self, fn: Callable[[], Any], *, tries: int | None = None,
              base_sleep: float = 0.25) -> Any:
        """Run ``fn`` with exponential backoff; non-retryable RpcErrors re-raise immediately."""
        tries = tries or settings.RPC_MAX_RETRIES
        last: Exception | None = None
        for attempt in range(max(1, tries)):
            try:
                return fn()
            except RpcError as exc:
                if not exc.retryable:
                    raise
                last = exc
            except requests.RequestException as exc:
                last = exc
            if attempt < tries - 1:
                time.sleep(base_sleep * (2 ** attempt))
        raise last or RuntimeError("RPC call failed")

    def latest_block(self) -> int:
        return int(self.standard.request("eth_blockNumber", []), 16)
