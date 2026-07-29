"""Shared CoinGecko access for mini-apps: token icons today, spot prices next.

Extracted from ``cow_explorer`` when a second mini-app (the governance Treasury
tab) needed the same address-keyed lookups. The behaviour is unchanged — CoW
keeps its original public names as thin aliases.

Two invariants make this safe to call from a request path:

* **Never blocks.** Every ``*_nowait`` function returns whatever is cached and
  submits a miss to a background executor, reporting ``pending=True`` so the
  caller can schedule one retry. Data-loading paths never wait on CoinGecko.
* **Never fabricates.** A token absent from CoinGecko is simply absent from the
  overlay — there is no placeholder URL and no zero price. The client renders a
  monogram / an em-dash from that absence, which is the honest reading. A failed
  fetch caches the empty result for a full TTL rather than hot-looping.

Address keys are lowercase everywhere: the indexer stores lowercase, CoinGecko
returns lowercase, and a case mismatch would silently miss every lookup.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import requests

from cerebro_mcp.runtime.mini_app_cache import CachedDataset

logger = logging.getLogger(__name__)

#: CoinGecko asset-platform ids per EVM chain. A chain absent from this map is
#: never fetched (no platform => no list => no icons), which is why unsupported
#: testnets cost nothing.
PLATFORM_IDS = {
    1: "ethereum",
    56: "binance-smart-chain",
    100: "xdai",
    137: "polygon-pos",
    8453: "base",
    9745: "plasma",
    42161: "arbitrum-one",
    43114: "avalanche",
    57073: "ink",
    59144: "linea",
}
TOKEN_LIST_URL = "https://tokens.coingecko.com/{platform}/all.json"
ICON_CACHE_TTL_SECONDS = 30 * 60
#: Only these hosts may appear in an overlay URL. The mini-app hosts enforce a
#: CSP allowlist naming exactly these two, so a URL from anywhere else could not
#: load anyway — rejecting it here keeps the overlay honest rather than shipping
#: a link the browser will refuse.
_IMAGE_HOSTS = {"assets.coingecko.com", "coin-images.coingecko.com"}

_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")

_ICON_CACHE: dict[int, tuple[float, dict[str, str]]] = {}
_LOCK = threading.RLock()
#: Background fetcher. Nothing ever waits on it: callers read the cache as-is
#: and the frontend patches results in afterwards.
_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cg-fetch")
_ICON_PENDING: set[int] = set()


def normalize_hex(value: str) -> str:
    return value.strip().lower()


def _safe_logo_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _IMAGE_HOSTS:
        return ""
    return url


def fetch_icon_map(chain_id: int) -> dict[str, str]:
    platform = PLATFORM_IDS.get(chain_id)
    if not platform:
        return {}
    response = requests.get(
        TOKEN_LIST_URL.format(platform=platform),
        timeout=(2, 8),
        headers={"Accept": "application/json", "User-Agent": "cerebro-mini-apps/1"},
    )
    response.raise_for_status()
    payload = response.json()
    tokens = payload.get("tokens", []) if isinstance(payload, dict) else []
    icons: dict[str, str] = {}
    for item in tokens:
        if not isinstance(item, dict):
            continue
        address = normalize_hex(str(item.get("address") or ""))
        logo_url = _safe_logo_url(item.get("logoURI"))
        if _ADDRESS_RE.fullmatch(address) and logo_url:
            icons[address] = logo_url
    return icons


def icon_map_nowait(chain_id: int) -> tuple[dict[str, str], bool]:
    """Return ``(cached icon map, pending)`` without ever blocking.

    On a cache miss the fetch is submitted to the background executor and
    ``pending=True`` signals the caller that a retry will find more icons.
    """
    now = time.monotonic()
    with _LOCK:
        cached = _ICON_CACHE.get(chain_id)
        if cached and now - cached[0] < ICON_CACHE_TTL_SECONDS:
            return cached[1], False
        if chain_id not in PLATFORM_IDS:
            return {}, False
        if chain_id in _ICON_PENDING:
            return (cached[1] if cached else {}), True
        _ICON_PENDING.add(chain_id)

    def fetch() -> None:
        icons: dict[str, str] = {}
        try:
            icons = fetch_icon_map(chain_id)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning(
                "CoinGecko token icons unavailable for chain %s: %s", chain_id, exc
            )
        finally:
            # Cache the empty result too: a failure must not hot-loop the API.
            with _LOCK:
                _ICON_CACHE[chain_id] = (time.monotonic(), icons)
                _ICON_PENDING.discard(chain_id)

    try:
        _EXECUTOR.submit(fetch)
    except RuntimeError:  # interpreter shutdown
        with _LOCK:
            _ICON_PENDING.discard(chain_id)
        return (cached[1] if cached else {}), False
    return (cached[1] if cached else {}), True


def dataset_token_addresses(
    datasets: dict[str, CachedDataset],
    *,
    token_columns: re.Pattern[str],
    native_token: str | None = None,
    cap_per_chain: int = 500,
) -> dict[int, set[str]]:
    """Collect distinct token addresses per chain from attached datasets.

    ``token_columns`` is the consuming app's own column-name pattern — the
    schema belongs to the app, not to CoinGecko.
    """
    per_chain: dict[int, set[str]] = {}
    for dataset in datasets.values():
        token_indexes = [
            index for index, name in enumerate(dataset.columns)
            if token_columns.fullmatch(name)
        ]
        if not token_indexes:
            continue
        chain_index = (
            dataset.columns.index("chain_id") if "chain_id" in dataset.columns else -1
        )
        fallback_chain = int(dataset.parameters.get("chain_id") or 0) if dataset.parameters else 0
        for row in dataset.rows:
            chain_id = fallback_chain
            if 0 <= chain_index < len(row) and row[chain_index] is not None:
                try:
                    chain_id = int(row[chain_index])
                except (TypeError, ValueError):
                    chain_id = fallback_chain
            if chain_id <= 0:
                continue
            bucket = per_chain.setdefault(chain_id, set())
            if len(bucket) >= cap_per_chain:
                continue
            for index in token_indexes:
                if index < len(row):
                    value = normalize_hex(str(row[index] or ""))
                    if value == native_token or _ADDRESS_RE.fullmatch(value):
                        bucket.add(value)
    return per_chain


def build_icon_overlay(
    datasets: dict[str, CachedDataset],
    *,
    token_columns: re.Pattern[str],
    native_token: str | None = None,
    native_icon_urls: dict[int, str] | None = None,
) -> tuple[dict[str, dict[str, str]], bool]:
    """Resolve icon URLs for every token visible in the attached datasets.

    Returns ``(overlay, pending)`` where overlay is ``{chain_id: {token: url}}``
    and ``pending`` means at least one chain's CoinGecko list is still being
    fetched in the background (the frontend retries once shortly after).

    A token with no known icon is OMITTED, never mapped to a placeholder — the
    client renders a monogram from the absence.
    """
    overlay: dict[str, dict[str, str]] = {}
    any_pending = False
    natives = native_icon_urls or {}
    addresses = dataset_token_addresses(
        datasets, token_columns=token_columns, native_token=native_token
    )
    for chain_id, tokens in addresses.items():
        icon_map, pending = icon_map_nowait(chain_id)
        any_pending = any_pending or pending
        chain_icons: dict[str, str] = {}
        for token in tokens:
            if native_token is not None and token == native_token:
                url = natives.get(chain_id, "")
            else:
                url = icon_map.get(token, "")
            if url:
                chain_icons[token] = url
        if chain_icons:
            overlay[str(chain_id)] = chain_icons
    return overlay, any_pending


# ---------------------------------------------------------------------------
# Spot prices
#
# NOT via ``simple/token_price``: the free tier now caps that endpoint at ONE
# contract address per call (HTTP 400, error_code 10012), which is unusable for
# a few hundred holdings. The workable path is two batched endpoints —
# ``coins/list?include_platform=true`` maps contract -> coin id, then
# ``simple/price?ids=`` prices many coin ids at once.
#
# Measured free-tier behaviour: 4 rapid calls returned 429; 3 calls spaced 20s
# apart succeeded. Negative caching is therefore mandatory, not defensive.
# ---------------------------------------------------------------------------

COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
#: The contract -> coin-id index is ~2.8 MB and new listings appear over days,
#: so a long TTL costs nothing and spares the rate limit.
COIN_INDEX_TTL_SECONDS = 6 * 60 * 60
#: Spot quotes: short enough that a treasury figure is never hours stale, long
#: enough that reopening the tab never re-hits the API.
PRICE_CACHE_TTL_SECONDS = 5 * 60
#: After ANY failure (429 included) the empty result is cached this long so a
#: per-view tool call cannot turn into a hot loop against the endpoint.
NEGATIVE_CACHE_TTL_SECONDS = 10 * 60
#: Coin ids average ~14 chars, so 100 ids is ~1.5 KB of query string.
PRICE_BATCH_IDS = 100

#: platform -> {contract: coin_id}
_COIN_INDEX: tuple[float, dict[str, dict[str, str]]] | None = None
_COIN_INDEX_PENDING = False
#: coin_id -> (usd, fetched_at monotonic). A NaN value is the negative cache:
#: CoinGecko knows the coin but returned no quote. It is never surfaced as a
#: price — omission is the truthful reading — and it stops the id being
#: re-requested on every view.
_PRICE_CACHE: dict[str, tuple[float, float]] = {}
#: One resolve-then-quote pass at a time, so concurrent views cannot stampede
#: the rate-limited endpoint.
_PRICE_PASS_PENDING = False


def fetch_coin_index() -> dict[str, dict[str, str]]:
    """``{platform: {contract_address: coin_id}}`` from the full coin list."""
    response = requests.get(
        COINS_LIST_URL,
        timeout=(3, 20),
        headers={"Accept": "application/json", "User-Agent": "cerebro-mini-apps/1"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return {}
    index: dict[str, dict[str, str]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        coin_id = str(item.get("id") or "").strip()
        platforms = item.get("platforms")
        if not coin_id or not isinstance(platforms, dict):
            continue
        for platform, address in platforms.items():
            contract = normalize_hex(str(address or ""))
            if not _ADDRESS_RE.fullmatch(contract):
                continue
            index.setdefault(str(platform), {})[contract] = coin_id
    return index


def coin_index_nowait() -> tuple[dict[str, dict[str, str]], bool]:
    """``(cached contract->coin-id index, pending)``. Never blocks."""
    global _COIN_INDEX_PENDING
    now = time.monotonic()
    with _LOCK:
        cached = _COIN_INDEX
        if cached and now - cached[0] < COIN_INDEX_TTL_SECONDS:
            return cached[1], False
        if _COIN_INDEX_PENDING:
            return (cached[1] if cached else {}), True
        _COIN_INDEX_PENDING = True

    def fetch() -> None:
        global _COIN_INDEX, _COIN_INDEX_PENDING
        index: dict[str, dict[str, str]] = {}
        try:
            index = fetch_coin_index()
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("CoinGecko coin index unavailable: %s", exc)
        finally:
            with _LOCK:
                # An empty index is cached under the SHORT negative TTL so a
                # transient 429 does not blind pricing for six hours.
                stamp = time.monotonic()
                if not index:
                    stamp -= COIN_INDEX_TTL_SECONDS - NEGATIVE_CACHE_TTL_SECONDS
                _COIN_INDEX = (stamp, index)
                _COIN_INDEX_PENDING = False

    try:
        _EXECUTOR.submit(fetch)
    except RuntimeError:  # interpreter shutdown
        with _LOCK:
            _COIN_INDEX_PENDING = False
        return (cached[1] if cached else {}), False
    return (cached[1] if cached else {}), True


def fetch_prices(coin_ids: list[str]) -> dict[str, float]:
    """USD spot for a batch of coin ids."""
    if not coin_ids:
        return {}
    response = requests.get(
        SIMPLE_PRICE_URL,
        params={"ids": ",".join(coin_ids), "vs_currencies": "usd"},
        timeout=(3, 15),
        headers={"Accept": "application/json", "User-Agent": "cerebro-mini-apps/1"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return {}
    prices: dict[str, float] = {}
    for coin_id, quote in payload.items():
        if not isinstance(quote, dict):
            continue
        usd = quote.get("usd")
        # 0.0 is a legitimate quote for a worthless token and must survive as
        # 0.0 — it is NOT the same as "no quote", which stays absent.
        if isinstance(usd, (int, float)) and not isinstance(usd, bool):
            prices[str(coin_id)] = float(usd)
    return prices


def _resolve_contracts(
    index: dict[str, dict[str, str]], wanted: dict[int, set[str]]
) -> dict[int, dict[str, str]]:
    """``{chain_id: {contract: coin_id}}`` for chains CoinGecko covers."""
    per_chain: dict[int, dict[str, str]] = {}
    for chain_id, contracts in wanted.items():
        platform = PLATFORM_IDS.get(chain_id)
        if not platform:
            continue
        platform_map = index.get(platform) or {}
        resolved = {c: platform_map[c] for c in contracts if c in platform_map}
        if resolved:
            per_chain[chain_id] = resolved
    return per_chain


def price_map_nowait(
    wanted: dict[int, set[str]],
) -> tuple[dict[int, dict[str, float]], bool]:
    """``({chain_id: {contract: usd}}, pending)`` for the requested tokens.

    Never blocks. Pricing needs two hops — contract -> coin id, then coin id ->
    quote — and BOTH happen inside a single background pass, so a caller sees
    the usual two-round behaviour (first call kicks it off, one retry reads the
    result) rather than needing three. Chaining them here instead of returning
    after the index hop is the difference between the frontend's single retry
    finding prices and finding nothing.
    """
    global _PRICE_PASS_PENDING
    now = time.monotonic()
    with _LOCK:
        index = _COIN_INDEX[1] if _COIN_INDEX else {}
        index_fresh = bool(_COIN_INDEX) and now - _COIN_INDEX[0] < COIN_INDEX_TTL_SECONDS
        resolved = _resolve_contracts(index, wanted)
        out: dict[int, dict[str, float]] = {}
        unpriced = False
        for chain_id, contracts in resolved.items():
            chain_prices: dict[str, float] = {}
            for contract, coin_id in contracts.items():
                cached = _PRICE_CACHE.get(coin_id)
                if not cached or now - cached[1] >= PRICE_CACHE_TTL_SECONDS:
                    unpriced = True
                elif cached[0] == cached[0]:  # not NaN
                    chain_prices[contract] = cached[0]
                # A NaN entry is the negative cache: CoinGecko has the coin but
                # returned no quote. Omit it (never 0) and do NOT re-request it
                # until its shorter TTL lapses.
            if chain_prices:
                out[chain_id] = chain_prices
        if _PRICE_PASS_PENDING:
            return out, True
        # Nothing to do: the index is current and every resolvable contract is
        # freshly quoted. Contracts CoinGecko does not list are simply absent
        # and must NOT keep the pass running forever.
        if index_fresh and not unpriced:
            return out, False
        _PRICE_PASS_PENDING = True
        snapshot = {chain: set(tokens) for chain, tokens in wanted.items()}

    def pass_() -> None:
        global _COIN_INDEX, _PRICE_PASS_PENDING
        try:
            with _LOCK:
                have_index = bool(_COIN_INDEX) and (
                    time.monotonic() - _COIN_INDEX[0] < COIN_INDEX_TTL_SECONDS
                )
                current = _COIN_INDEX[1] if _COIN_INDEX else {}
            if not have_index:
                fresh: dict[str, dict[str, str]] = {}
                try:
                    fresh = fetch_coin_index()
                except (requests.RequestException, ValueError, TypeError) as exc:
                    logger.warning("CoinGecko coin index unavailable: %s", exc)
                with _LOCK:
                    stamp = time.monotonic()
                    if not fresh:
                        # Cache the failure under the SHORT negative TTL so a
                        # transient 429 does not blind pricing for six hours.
                        stamp -= COIN_INDEX_TTL_SECONDS - NEGATIVE_CACHE_TTL_SECONDS
                    _COIN_INDEX = (stamp, fresh or current)
                    current = _COIN_INDEX[1]

            with _LOCK:
                now_ = time.monotonic()
                needed = sorted({
                    coin_id
                    for contracts in _resolve_contracts(current, snapshot).values()
                    for coin_id in contracts.values()
                    if not (
                        (entry := _PRICE_CACHE.get(coin_id))
                        and now_ - entry[1] < PRICE_CACHE_TTL_SECONDS
                    )
                })
            for start in range(0, len(needed), PRICE_BATCH_IDS):
                batch = needed[start:start + PRICE_BATCH_IDS]
                prices: dict[str, float] = {}
                try:
                    prices = fetch_prices(batch)
                except (requests.RequestException, ValueError, TypeError) as exc:
                    logger.warning(
                        "CoinGecko prices unavailable for %d id(s): %s", len(batch), exc
                    )
                with _LOCK:
                    stamp = time.monotonic()
                    for coin_id in batch:
                        if coin_id in prices:
                            _PRICE_CACHE[coin_id] = (prices[coin_id], stamp)
                        else:
                            # Unquoted: park a negative entry so an unlisted
                            # coin is not re-requested on every view.
                            _PRICE_CACHE[coin_id] = (
                                float("nan"),
                                stamp - PRICE_CACHE_TTL_SECONDS + NEGATIVE_CACHE_TTL_SECONDS,
                            )
        finally:
            with _LOCK:
                _PRICE_PASS_PENDING = False

    try:
        _EXECUTOR.submit(pass_)
    except RuntimeError:  # interpreter shutdown
        with _LOCK:
            _PRICE_PASS_PENDING = False
        return out, False
    return out, True


def build_price_overlay(
    datasets: dict[str, CachedDataset],
    *,
    token_columns: re.Pattern[str],
) -> tuple[dict[str, Any], bool]:
    """Spot USD for every token visible in the attached datasets.

    Shape is deliberately tagged and nested so a HISTORICAL source is a drop-in
    replacement for this function with no client change::

        {"kind": "spot",       "by_chain": {chain: {token: usd}}}
        {"kind": "historical", "by_chain": {chain: {token: {date: usd}}}}

    A token CoinGecko does not list is omitted, never priced at 0 — the client
    renders "unpriced", which is the truthful reading.
    """
    wanted = dataset_token_addresses(datasets, token_columns=token_columns)
    prices, pending = price_map_nowait(wanted)
    by_chain = {
        str(chain_id): dict(chain_prices)
        for chain_id, chain_prices in prices.items()
        if chain_prices
    }
    return {"kind": "spot", "by_chain": by_chain}, pending


def reset_caches_for_tests() -> None:
    """Drop every cached map. Tests only."""
    global _COIN_INDEX, _COIN_INDEX_PENDING, _PRICE_PASS_PENDING
    with _LOCK:
        _ICON_CACHE.clear()
        _ICON_PENDING.clear()
        _PRICE_CACHE.clear()
        _COIN_INDEX = None
        _COIN_INDEX_PENDING = False
        _PRICE_PASS_PENDING = False
