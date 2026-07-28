"""Shared EVM chain registry.

Pure data plus URL resolution — no clients, no caches, no I/O. Anything that
holds a live connection (``clients/web3.py``, ``clients/raw_rpc.py``) builds on
top of this; keeping this module inert is what lets those modules be reloaded
in tests without leaking state.

The registry originated inside the CoW Explorer mini-app, which still owns its
own *subset* of chains (the ones CoW Protocol is deployed on) — see
``COW_CHAIN_IDS`` in ``tools/visualization/cow_explorer.py``. This module is the
superset and the single source of truth for chain identity, explorer URLs, and
RPC endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cerebro_mcp.config import settings


ExplorerProvider = Literal["blockscout", "bscscan", "avalanche", "plasmascan"]


@dataclass(frozen=True)
class ExplorerInfo:
    provider: ExplorerProvider
    brand: str
    base_url: str
    transaction_url_template: str
    address_url_template: str
    token_url_template: str
    #: REST API root for programmatic ABI lookup. Empty when the explorer has
    #: no API we can use — ``base_url`` is the *human* site and is NOT an API
    #: base (Blockscout serves its API under ``/api/v2``; BscScan, the
    #: Avalanche subnet explorer, and Plasmascan expose nothing compatible).
    api_base_url: str = ""


@dataclass(frozen=True)
class ChainInfo:
    chain_id: int
    name: str
    native_symbol: str
    environment: Literal["production", "testnet"]
    explorer: ExplorerInfo
    #: Suffix of the ``RPC_URL_<KEY>`` setting holding this chain's endpoint.
    rpc_env_key: str = ""


def _explorer(
    provider: ExplorerProvider,
    brand: str,
    base: str,
    *,
    token_as_address: bool = False,
) -> ExplorerInfo:
    base = base.rstrip("/")
    return ExplorerInfo(
        provider=provider,
        brand=brand,
        base_url=base,
        transaction_url_template=f"{base}/tx/{{hash}}",
        address_url_template=f"{base}/address/{{address}}",
        token_url_template=(
            f"{base}/address/{{address}}" if token_as_address else f"{base}/token/{{address}}"
        ),
        # Only Blockscout exposes an ABI-serving REST API at a predictable path.
        api_base_url=(f"{base}/api/v2" if provider == "blockscout" else ""),
    )


#: Every chain cerebro knows about. Entries 1..11155111 are byte-identical to
#: the original CoW registry — do not reorder them; ``COW_CHAINS`` derives its
#: iteration order from an explicit id tuple, but keeping this stable avoids
#: surprises for anything else that iterates.
CHAINS: dict[int, ChainInfo] = {
    1: ChainInfo(1, "Ethereum", "ETH", "production", _explorer("blockscout", "Blockscout", "https://eth.blockscout.com"), "MAINNET"),
    100: ChainInfo(100, "Gnosis", "xDAI", "production", _explorer("blockscout", "Blockscout", "https://gnosis.blockscout.com"), "GNOSIS"),
    42161: ChainInfo(42161, "Arbitrum One", "ETH", "production", _explorer("blockscout", "Blockscout", "https://arbitrum.blockscout.com"), "ARBITRUM"),
    8453: ChainInfo(8453, "Base", "ETH", "production", _explorer("blockscout", "Blockscout", "https://base.blockscout.com"), "BASE"),
    56: ChainInfo(56, "BNB Smart Chain", "BNB", "production", _explorer("bscscan", "BscScan", "https://bscscan.com"), "BNB"),
    137: ChainInfo(137, "Polygon PoS", "POL", "production", _explorer("blockscout", "Blockscout", "https://polygon.blockscout.com"), "POLYGON"),
    43114: ChainInfo(43114, "Avalanche C-Chain", "AVAX", "production", _explorer("avalanche", "Avalanche Explorer", "https://subnets.avax.network/c-chain", token_as_address=True), "AVALANCHE"),
    59144: ChainInfo(59144, "Linea", "ETH", "production", _explorer("blockscout", "Blockscout", "https://explorer.linea.build"), "LINEA"),
    57073: ChainInfo(57073, "Ink", "ETH", "production", _explorer("blockscout", "Blockscout", "https://explorer.inkonchain.com"), "INK"),
    9745: ChainInfo(9745, "Plasma", "XPL", "production", _explorer("plasmascan", "Plasmascan", "https://plasmascan.to"), "PLASMA"),
    11155111: ChainInfo(11155111, "Ethereum Sepolia", "ETH", "testnet", _explorer("blockscout", "Blockscout", "https://eth-sepolia.blockscout.com"), "SEPOLIA"),
    42220: ChainInfo(42220, "Celo", "CELO", "production", _explorer("blockscout", "Blockscout", "https://celo.blockscout.com"), "CELO"),
}

GNOSIS_CHAIN_ID = 100

#: CoinGecko asset-platform images, keyed by chain id. Mirrored client-side in
#: ``ui/src/mini-apps/shared/chainIcons.ts``; chains without one (Sepolia, Celo)
#: fall back to a monogram badge.
NATIVE_ICON_URLS: dict[int, str] = {
    1: "https://coin-images.coingecko.com/asset_platforms/images/279/thumb/ethereum.png?1706606803",
    56: "https://coin-images.coingecko.com/asset_platforms/images/1/thumb/bnb_smart_chain.png?1706606721",
    100: "https://coin-images.coingecko.com/asset_platforms/images/11062/thumb/Aatar_green_white.png?1706606458",
    137: "https://coin-images.coingecko.com/asset_platforms/images/15/thumb/polygon_pos.png?1706606645",
    8453: "https://coin-images.coingecko.com/asset_platforms/images/131/thumb/base.png?1759905869",
    9745: "https://coin-images.coingecko.com/asset_platforms/images/32256/thumb/plasma.jpg?1758000963",
    42161: "https://coin-images.coingecko.com/asset_platforms/images/33/thumb/AO_logomark.png?1706606717",
    43114: "https://coin-images.coingecko.com/asset_platforms/images/12/thumb/avalanche.png?1706606775",
    57073: "https://coin-images.coingecko.com/asset_platforms/images/22194/thumb/ink.jpg?1737600222",
    59144: "https://coin-images.coingecko.com/asset_platforms/images/135/thumb/linea.jpeg?1706606705",
}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def get_chain(chain_id: int) -> ChainInfo:
    """Return the :class:`ChainInfo` for ``chain_id`` or raise ``ValueError``."""
    chain = CHAINS.get(int(chain_id))
    if chain is None:
        known = ", ".join(str(c) for c in sorted(CHAINS))
        raise ValueError(f"Unknown chain_id {chain_id}. Known chains: {known}")
    return chain


def resolve_chain(value: int | str | None) -> ChainInfo:
    """Resolve a chain id, env key (``"gnosis"``), or name (``"Arbitrum One"``).

    ``None`` / empty resolves to Gnosis, preserving the pre-multi-chain default
    for every caller that does not pass a chain.
    """
    if value is None or value == "":
        return get_chain(GNOSIS_CHAIN_ID)
    if isinstance(value, int):
        return get_chain(value)

    text = value.strip()
    if text.isdigit():
        return get_chain(int(text))

    folded = text.casefold().replace(" ", "").replace("-", "").replace("_", "")
    for chain in CHAINS.values():
        candidates = {
            chain.rpc_env_key.casefold(),
            chain.name.casefold().replace(" ", ""),
        }
        if folded in candidates:
            return chain
    # Prefix match is the forgiving tail: "arbitrum" -> "Arbitrum One".
    for chain in CHAINS.values():
        if chain.name.casefold().replace(" ", "").startswith(folded):
            return chain

    known = ", ".join(sorted(c.rpc_env_key.lower() for c in CHAINS.values()))
    raise ValueError(f"Unknown chain {value!r}. Known chains: {known}")


# ---------------------------------------------------------------------------
# RPC endpoint resolution
# ---------------------------------------------------------------------------

def chain_rpc_urls(chain_id: int) -> tuple[str, str]:
    """Return ``(standard_url, archive_url)`` for ``chain_id``.

    Precedence, per chain:

    * ``RPC_URL_<KEY>`` is the endpoint. The nodes cerebro is pointed at are
      archive nodes, so it serves as BOTH standard and archive unless an
      explicit ``RPC_URL_<KEY>_ARCHIVE`` override is set.
    * Gnosis additionally honors the legacy ``GNOSIS_RPC_URL`` /
      ``GNOSIS_ARCHIVE_RPC_URL`` pair, which wins so existing deployments keep
      their exact current behavior.

    Either element may be ``""`` — callers decide whether that is fatal. This
    never raises and never performs I/O, so it is safe at import time.
    """
    chain = get_chain(chain_id)
    key = chain.rpc_env_key
    standard = str(getattr(settings, f"RPC_URL_{key}", "") or "").strip()
    archive = str(getattr(settings, f"RPC_URL_{key}_ARCHIVE", "") or "").strip()

    if chain.chain_id == GNOSIS_CHAIN_ID:
        standard = str(settings.GNOSIS_RPC_URL or "").strip() or standard
        archive = str(settings.GNOSIS_ARCHIVE_RPC_URL or "").strip() or archive

    return standard, (archive or standard)


def has_rpc(chain_id: int) -> bool:
    """True when ``chain_id`` has a usable standard endpoint configured."""
    try:
        standard, _ = chain_rpc_urls(chain_id)
    except ValueError:
        return False
    return bool(standard)


def configured_chains() -> list[ChainInfo]:
    """Every chain with an RPC endpoint configured, in registry order.

    This is what the Contract Explorer's chain selector is built from, so the
    available chains are driven purely by which ``RPC_URL_*`` env vars are set.
    """
    return [chain for chain in CHAINS.values() if has_rpc(chain.chain_id)]


def rpc_env_hint(chain_id: int) -> str:
    """The env var a user should set to enable this chain.

    Used in error messages instead of the URL itself — endpoint URLs routinely
    embed API keys and must never reach tool output or logs.
    """
    return f"RPC_URL_{get_chain(chain_id).rpc_env_key}"


__all__ = [
    "CHAINS",
    "GNOSIS_CHAIN_ID",
    "NATIVE_ICON_URLS",
    "ChainInfo",
    "ExplorerInfo",
    "ExplorerProvider",
    "chain_rpc_urls",
    "configured_chains",
    "get_chain",
    "has_rpc",
    "resolve_chain",
    "rpc_env_hint",
]
