"""Lazy Web3 RPC manager for Gnosis Chain (read-only)."""
from __future__ import annotations

import time
from typing import Any, Callable

from web3 import Web3

from cerebro_mcp.config import settings


LATEST_BLOCK_TAGS = {"latest", "pending", "safe", "finalized", "earliest"}
GNOSIS_CHAIN_ID = 100


class GnosisRpcManager:
    """Lazy holder for standard + archive Web3 clients.

    Connections are constructed on first property access — importing this
    module performs no network I/O so the MCP server boots offline-clean.
    """

    def __init__(self) -> None:
        self._standard: Web3 | None = None
        self._archive: Web3 | None = None

    def _make(self, url: str) -> Web3:
        w3 = Web3(Web3.HTTPProvider(
            url,
            request_kwargs={"timeout": settings.RPC_TIMEOUT_SECONDS},
        ))
        if int(w3.eth.chain_id) != GNOSIS_CHAIN_ID:
            raise ValueError(
                f"RPC endpoint at {url} is not Gnosis Chain (chain_id != {GNOSIS_CHAIN_ID})"
            )
        return w3

    @property
    def standard(self) -> Web3:
        if self._standard is None:
            self._standard = self._make(settings.GNOSIS_RPC_URL)
        return self._standard

    @property
    def archive(self) -> Web3:
        if not settings.GNOSIS_ARCHIVE_RPC_URL:
            raise ValueError(
                "Historical state calls require GNOSIS_ARCHIVE_RPC_URL"
            )
        if self._archive is None:
            self._archive = self._make(settings.GNOSIS_ARCHIVE_RPC_URL)
        return self._archive

    def for_block(self, block_identifier: int | str | None) -> Web3:
        """Route to archive when caller asks for a specific historical block."""
        if block_identifier is None:
            return self.standard
        if isinstance(block_identifier, str) and block_identifier in LATEST_BLOCK_TAGS:
            return self.standard
        return self.archive

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


rpc_manager = GnosisRpcManager()
