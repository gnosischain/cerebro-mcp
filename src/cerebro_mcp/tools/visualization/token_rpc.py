"""ERC-20 metadata the state indexer never resolved, read live over RPC.

The state indexer resolves ``symbol`` / ``decimals`` / ``name`` only for tokens
inside a census job's own universe. The pool jobs sweep pool *addresses*, not
their assets, so of the 3,400 tokens held by tracked pools it has metadata for
68 — every other one is a token it was never asked about, NOT one that failed.
That is why almost every pool in the explorer showed a raw price and a
truncated address.

This module reads them straight off the chain in batches, so the app can label
a token the indexer has not catalogued yet.

Three things are deliberate:

* **Provenance stays separate.** These values never overwrite a dataset column.
  They travel as an overlay keyed by address, each entry carrying ``source``
  and the block it was read at, so a consumer can always tell an indexer value
  (verified at a pinned finalized block, with a publication record behind it)
  from a live read (current chain state, no publication, no verification).
* **Read at ``latest``, not at the snapshot's anchor block.** ERC-20 metadata is
  immutable in every implementation that matters, and pinning to a historical
  block would demand an archive endpoint for no gain. The block actually read is
  recorded rather than assumed.
* **A token that does not answer is OMITTED**, never given a placeholder. The
  client renders a short address from the absence, exactly as it did before —
  an unreadable token must not become indistinguishable from a nameless one.

Multicall3 ``aggregate3`` with ``allowFailure=true`` does the batching: one
``eth_call`` carries ~600 reads and a non-token address in the set can revert
without taking the batch down. Measured 2026-09-17 on Gnosis: 200 tokens
(600 reads) in 0.8s, all resolved.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from cerebro_mcp.clients.raw_rpc import RpcRouter
from cerebro_mcp.rpc_scan.multicall import (
    MULTICALL3_ADDRESS,
    decode_aggregate3,
    decode_outputs,
    encode_aggregate3,
    selector,
)

logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")

#: Tokens per ``eth_call``. Three reads each, so 200 keeps a batch at the ~600
#: calls Multicall3 handles comfortably in one round trip.
TOKENS_PER_BATCH = 200
#: Ceiling per resolve() call — 3 round trips, ~2.5s measured. A caller asking
#: for more gets the first N and ``truncated``; it must NOT silently drop the
#: rest, and the UI says more are pending.
MAX_TOKENS_PER_CALL = 600
#: How long a token that did not answer stays un-retried. Successes are cached
#: forever: ERC-20 metadata does not change, and a token that answered once will
#: answer the same way.
NEGATIVE_TTL_SECONDS = 15 * 60

_SELECTORS = {
    "symbol": selector("symbol", []),
    "decimals": selector("decimals", []),
    "name": selector("name", []),
}

_LOCK = threading.RLock()
_CACHE: dict[tuple[int, str], "TokenMeta"] = {}
_MISSES: dict[tuple[int, str], float] = {}


@dataclass(frozen=True)
class TokenMeta:
    """One token's on-chain metadata, with where it came from attached.

    ``symbol`` / ``name`` are None when the call reverted or returned something
    undecodable — never "" and never a placeholder, so a consumer cannot mistake
    an unreadable token for an unnamed one.
    """

    address: str
    symbol: str | None
    name: str | None
    decimals: int | None
    block_number: int
    #: "string" (standard ERC-20) or "bytes32" (the older fixed-width variant
    #: several long-lived tokens still use). Recorded because a value decoded
    #: the second way went through a fallback, and that is worth being able to
    #: see rather than having to infer.
    encoding: str

    @property
    def usable(self) -> bool:
        """Whether the read produced anything worth showing."""
        return self.symbol is not None or self.decimals is not None

    def as_overlay(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "decimals": self.decimals,
            "block_number": self.block_number,
            "encoding": self.encoding,
            # The whole point of the overlay: a consumer must be able to tell
            # this apart from an indexer-verified value without guessing.
            "source": "rpc",
        }


@dataclass(frozen=True)
class ResolveStats:
    requested: int
    from_cache: int
    fetched: int
    unreadable: int
    truncated: bool
    block_number: int
    error: str = ""


def normalize(address: str) -> str:
    return str(address or "").strip().lower()


def is_configured(chain_id: int) -> bool:
    """Whether an RPC endpoint exists for this chain.

    A capability that is merely unconfigured must be distinguishable from one
    that is broken, so this is reported in ``system_status`` rather than being
    discovered as an empty overlay.
    """
    try:
        RpcRouter.for_chain(int(chain_id))
    except Exception:  # noqa: BLE001 - any config failure means "not available"
        return False
    return True


#: Longest symbol or name accepted. A token is free to return a kilobyte of
#: text, and a UI that renders it verbatim is a layout break at best and a
#: spoofing surface at worst. Sanitising at the source beats hoping every
#: consumer remembers to.
MAX_TEXT_LENGTH = 64


def _clean_text(value: str) -> str | None:
    """Accept a decoded symbol/name only if it is actually displayable text.

    ``str.isprintable()`` is the load-bearing check, not the replacement-char
    one: bytes 0x00-0x1f are all VALID UTF-8, so a bytes32 field holding raw
    control bytes decodes cleanly and would sail through a mojibake guard into
    the UI as a "symbol" made of control characters. Found by
    test_bytes_that_are_not_text_are_not_passed_off_as_a_symbol.
    """
    text = (value or "").strip()
    if not text or len(text) > MAX_TEXT_LENGTH:
        return None
    if not text.isprintable() or "\ufffd" in text:
        return None
    return text


def _decode_text(ok: bool, ret: bytes) -> tuple[str | None, str]:
    """Decode a ``symbol()`` / ``name()`` return, standard shape first.

    Returns ``(value, encoding)``. The bytes32 branch is not hypothetical: it is
    the original ERC-20 shape and several long-lived tokens never migrated, so a
    string-only decoder reports them as nameless.
    """
    if not ok or not ret:
        return None, "absent"
    decoded = decode_outputs(["string"], ret)
    if decoded and isinstance(decoded[0], str):
        text = _clean_text(decoded[0])
        if text:
            return text, "string"
    if len(ret) == 32:
        text = _clean_text(ret.rstrip(b"\x00").decode("utf-8", "replace"))
        if text:
            return text, "bytes32"
    return None, "absent"


def _decode_decimals(ok: bool, ret: bytes) -> int | None:
    if not ok or not ret:
        return None
    decoded = decode_outputs(["uint8"], ret)
    if not decoded:
        return None
    try:
        value = int(decoded[0])
    except (TypeError, ValueError):
        return None
    # 77 is the largest exponent that keeps 10**d inside a uint256. Anything
    # beyond it cannot be a real scaling factor, and scaling by it would produce
    # a plausible-looking wrong amount.
    return value if 0 <= value <= 77 else None


def _fetch_batch(
    client: Any, chain_id: int, addresses: list[str], block_number: int
) -> dict[str, TokenMeta]:
    """One Multicall3 round trip for up to TOKENS_PER_BATCH tokens."""
    calls: list[tuple[str, bool, bytes]] = []
    for address in addresses:
        for field in ("symbol", "decimals", "name"):
            calls.append((address, True, _SELECTORS[field]))
    raw = client.request(
        "eth_call", [{"to": MULTICALL3_ADDRESS, "data": encode_aggregate3(calls)}, "latest"]
    )
    results = decode_aggregate3(raw)
    if len(results) != len(calls):
        raise ValueError(
            f"multicall returned {len(results)} results for {len(calls)} calls"
        )
    out: dict[str, TokenMeta] = {}
    for index, address in enumerate(addresses):
        symbol_ok, symbol_ret = results[3 * index]
        decimals_ok, decimals_ret = results[3 * index + 1]
        name_ok, name_ret = results[3 * index + 2]
        symbol, encoding = _decode_text(symbol_ok, symbol_ret)
        name, _ = _decode_text(name_ok, name_ret)
        out[address] = TokenMeta(
            address=address,
            symbol=symbol,
            name=name,
            decimals=_decode_decimals(decimals_ok, decimals_ret),
            block_number=block_number,
            encoding=encoding,
        )
    return out


def resolve_tokens(
    chain_id: int,
    addresses: set[str] | list[str],
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, TokenMeta], ResolveStats]:
    """Resolve metadata for a set of token addresses.

    Cache-first and bounded. Only tokens that produced something usable are
    returned; an unreadable one is remembered as a miss for NEGATIVE_TTL_SECONDS
    so a directory page does not re-ask the chain about it on every load.

    Never raises: an RPC failure comes back as an empty result plus an ``error``
    on the stats, because a labelling overlay must never be able to take a panel
    down with it.
    """
    wanted = sorted({normalize(a) for a in addresses if ADDRESS_RE.fullmatch(normalize(a))})
    requested = len(wanted)
    if not wanted:
        return {}, ResolveStats(0, 0, 0, 0, False, 0)

    resolved: dict[str, TokenMeta] = {}
    pending: list[str] = []
    now = time.time()
    with _LOCK:
        for address in wanted:
            key = (int(chain_id), address)
            if not force_refresh:
                cached = _CACHE.get(key)
                if cached is not None:
                    resolved[address] = cached
                    continue
                missed_at = _MISSES.get(key)
                if missed_at is not None and now - missed_at < NEGATIVE_TTL_SECONDS:
                    continue
            pending.append(address)

    from_cache = len(resolved)
    truncated = len(pending) > MAX_TOKENS_PER_CALL
    pending = pending[:MAX_TOKENS_PER_CALL]
    if not pending:
        return resolved, ResolveStats(
            requested, from_cache, 0, 0, truncated, 0
        )

    try:
        router = RpcRouter.for_chain(int(chain_id))
        client = router.standard
        block_number = int(router.latest_block())
    except Exception as exc:  # noqa: BLE001
        logger.warning("token metadata RPC unavailable for chain %s: %s", chain_id, exc)
        return resolved, ResolveStats(
            requested, from_cache, 0, 0, truncated, 0, str(exc)[:200]
        )

    fetched = unreadable = 0
    error = ""
    for start in range(0, len(pending), TOKENS_PER_BATCH):
        chunk = pending[start : start + TOKENS_PER_BATCH]
        try:
            batch = _fetch_batch(client, chain_id, chunk, block_number)
        except Exception as exc:  # noqa: BLE001
            # One failed batch must not discard the batches that worked.
            logger.warning("token metadata batch failed (%d tokens): %s", len(chunk), exc)
            error = error or str(exc)[:200]
            continue
        with _LOCK:
            for address, meta in batch.items():
                if meta.usable:
                    _CACHE[(int(chain_id), address)] = meta
                    _MISSES.pop((int(chain_id), address), None)
                    resolved[address] = meta
                    fetched += 1
                else:
                    _MISSES[(int(chain_id), address)] = time.time()
                    unreadable += 1
    logger.info(
        "token_rpc chain=%s requested=%d cached=%d fetched=%d unreadable=%d block=%d",
        chain_id, requested, from_cache, fetched, unreadable, block_number,
    )
    return resolved, ResolveStats(
        requested, from_cache, fetched, unreadable, truncated, block_number, error
    )


def cache_size() -> int:
    with _LOCK:
        return len(_CACHE)


def reset_cache_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
        _MISSES.clear()


__all__ = [
    "TokenMeta", "ResolveStats", "resolve_tokens", "is_configured",
    "normalize", "cache_size", "reset_cache_for_tests",
    "TOKENS_PER_BATCH", "MAX_TOKENS_PER_CALL", "NEGATIVE_TTL_SECONDS",
]
