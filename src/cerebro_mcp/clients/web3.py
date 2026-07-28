"""Lazy per-chain Web3 RPC manager (read-only).

Chain identity, explorer URLs, and endpoint resolution live in
``cerebro_mcp.chains``; this module only holds the live connections.
Connections are constructed on first use — importing this module performs no
network I/O so the MCP server boots offline-clean.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from web3 import Web3

from cerebro_mcp.chains import GNOSIS_CHAIN_ID, chain_rpc_urls, get_chain, rpc_env_hint
from cerebro_mcp.config import settings


LATEST_BLOCK_TAGS = {"latest", "pending", "safe", "finalized", "earliest"}


class ChainRpcManager:
    """Lazy holder for one Web3 client per (chain, standard|archive) endpoint.

    Clients are cached and shared across threads. web3's HTTP provider keeps a
    session per (thread, endpoint), so a shared client is safe to call
    concurrently — but *construction* is not (it fires a chain-id probe), hence
    the lock. The history sweep is the first concurrent caller.
    """

    def __init__(self) -> None:
        self._clients: dict[tuple[int, str], Web3] = {}
        self._lock = threading.Lock()

    # -- construction ----------------------------------------------------

    def _make(self, url: str, chain_id: int) -> Web3:
        """Build a Web3 client and verify it really is the chain we asked for.

        The guard catches an endpoint pointed at the wrong network, which would
        otherwise return confidently wrong state. The URL is deliberately kept
        out of the error — endpoint URLs routinely embed API keys.
        """
        w3 = Web3(Web3.HTTPProvider(
            url,
            request_kwargs={"timeout": settings.RPC_TIMEOUT_SECONDS},
        ))
        actual = int(w3.eth.chain_id)
        if actual != int(chain_id):
            chain = get_chain(chain_id)
            raise ValueError(
                f"RPC endpoint configured for {chain.name} (chain {chain_id}) "
                f"reports chain_id {actual}. Check {rpc_env_hint(chain_id)}."
            )
        return w3

    def _client(self, chain_id: int, kind: str) -> Web3:
        chain = get_chain(chain_id)
        cache_key = (chain.chain_id, kind)

        cached = self._clients.get(cache_key)
        if cached is not None:
            return cached

        standard_url, archive_url = chain_rpc_urls(chain.chain_id)
        url = archive_url if kind == "archive" else standard_url
        if not url:
            hint = rpc_env_hint(chain.chain_id)
            if chain.chain_id == GNOSIS_CHAIN_ID:
                hint = f"{hint} or GNOSIS_RPC_URL"
            raise ValueError(
                f"No RPC endpoint configured for {chain.name} "
                f"(chain {chain.chain_id}). Set {hint}."
            )

        with self._lock:
            # Re-check: another thread may have built it while we waited.
            cached = self._clients.get(cache_key)
            if cached is None:
                cached = self._make(url, chain.chain_id)
                self._clients[cache_key] = cached
            return cached

    # -- accessors -------------------------------------------------------

    def standard(self, chain_id: int = GNOSIS_CHAIN_ID) -> Web3:
        return self._client(chain_id, "standard")

    def archive(self, chain_id: int = GNOSIS_CHAIN_ID) -> Web3:
        """Client for historical state.

        The configured endpoints are archive nodes, so this is usually the same
        client as :meth:`standard`; chains with a separate pruned/archive split
        get the ``RPC_URL_<CHAIN>_ARCHIVE`` override.
        """
        return self._client(chain_id, "archive")

    def for_block(
        self,
        block_identifier: int | str | None,
        chain_id: int = GNOSIS_CHAIN_ID,
    ) -> Web3:
        """Route to archive when the caller asks for a specific historical block."""
        if block_identifier is None:
            return self.standard(chain_id)
        if isinstance(block_identifier, str) and block_identifier in LATEST_BLOCK_TAGS:
            return self.standard(chain_id)
        return self.archive(chain_id)

    def retry(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run an RPC callable with exponential backoff, returning the RAW result.

        Do NOT normalize here. web3.py returns AttributeDict (a Mapping, not a
        ``dict``) for transactions/receipts; normalize_value() would miss the
        dict branch and fall through to ``str(value)``, stringifying the whole
        object and breaking ``receipt.get("logs")`` / ``process_receipt(receipt)``
        in the decode tools. JSON-normalization is applied at each tool's output
        boundary instead (e.g. ``normalize_value(result)`` on the call path).
        """
        last: Exception | None = None
        for attempt in range(max(1, settings.RPC_MAX_RETRIES)):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < settings.RPC_MAX_RETRIES - 1:
                    time.sleep(0.25 * (2 ** attempt))
        raise last or RuntimeError("RPC call failed")


rpc_manager = ChainRpcManager()
