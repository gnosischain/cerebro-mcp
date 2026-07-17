"""Resolve contract ABIs.

Order of precedence:

1. In-memory TTL cache.
2. ``dbt.contracts_abi`` (the dbt-cerebro seed; mirrors the Blockscout API).
3. Live Blockscout fetch (read-only fallback for contracts not yet seeded).

Proxy handling matches the dbt-cerebro convention seen in
``macros/decoding/fetch_and_insert_abi.sql``: for a proxy, BOTH the proxy ABI
and the implementation ABI are stored under the proxy's ``contract_address``,
distinguished by ``implementation_address`` (empty for the proxy row, set to
the implementation's address for the impl row). The implementation is *not*
keyed under its own ``contract_address``.

When neither source knows the implementation but the resolved ABI has no
callable functions (the signature of a bare delegating proxy — e.g.
``GnosisSafeProxy``, whose masterCopy lives in storage slot 0 where
Blockscout reports ``implementations: []``), the resolver probes the chain
directly: EIP-1967 implementation slot, EIP-1167 minimal-proxy bytecode,
then slot 0.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests
from web3 import Web3

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.web3 import rpc_manager
from cerebro_mcp.config import settings


@dataclass
class AbiRecord:
    contract_address: str
    implementation_address: str
    contract_name: str
    abi: list[dict[str, Any]]
    source: str  # "clickhouse" | "blockscout" | dbt-stored source string


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, AbiRecord]] = {}


def _cache_key(address: str, target: str) -> str:
    """Include target in the key — proxy / impl / auto resolutions for the
    same address must NOT collide. Without this, a target=proxy load poisons
    later target=auto / target=implementation calls for the same address."""
    return f"{address.lower()}|{target}"


def _cache_get(address: str, target: str) -> AbiRecord | None:
    hit = _cache.get(_cache_key(address, target))
    if not hit:
        return None
    ts, rec = hit
    if time.time() - ts > settings.ABI_CACHE_TTL_SECONDS:
        _cache.pop(_cache_key(address, target), None)
        return None
    return rec


def _cache_set(address: str, target: str, record: AbiRecord) -> None:
    if len(_cache) >= settings.ABI_CACHE_MAX_ENTRIES:
        # FIFO eviction — cheap; resolver is not on a hot path.
        _cache.pop(next(iter(_cache)))
    _cache[_cache_key(address, target)] = (time.time(), record)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_abi(
    ch: ClickHouseManager,
    address: str,
    target: str = "auto",
) -> AbiRecord:
    """Return an :class:`AbiRecord` for ``address``.

    Args:
        ch: ClickHouse manager (used to query ``dbt.contracts_abi``).
        address: Hex address (any case).
        target: ``"auto"`` (default) or ``"implementation"`` returns the
            implementation ABI when the contract is a proxy. ``"proxy"``
            forces the proxy's own ABI.

    Raises:
        ValueError: If neither ClickHouse nor Blockscout yields a usable ABI.
    """
    checksum = Web3.to_checksum_address(address)
    if (cached := _cache_get(checksum, target)) is not None:
        return cached

    record = _resolve_from_clickhouse(ch, checksum, target)
    if record is None:
        record = _resolve_from_blockscout(checksum, target)

    # Neither source knew this was a proxy, yet the ABI has no callable
    # surface (constructor/fallback only) — a bare delegating proxy whose
    # implementation pointer neither source tracks (Safe masterCopy in
    # slot 0, unverified EIP-1967/1167 shells). Probe the chain and resolve
    # the implementation's ABI instead; calls still target the proxy.
    if (
        target in ("auto", "implementation")
        and not record.implementation_address
        and record.abi
        and not _abi_has_functions(record.abi)
    ):
        record = _resolve_proxy_implementation(ch, checksum, record) or record

    if not record.abi:
        raise ValueError(f"ABI not found for {checksum}")

    _cache_set(checksum, target, record)
    return record


# ---------------------------------------------------------------------------
# ClickHouse path
# ---------------------------------------------------------------------------

_CH_SELECT = (
    "SELECT contract_address, implementation_address, abi_json, "
    "       contract_name, source "
    "FROM dbt.contracts_abi "
    "WHERE lower(contract_address) = lower({addr:String}) "
    # impl row first when present so target=auto picks it directly.
    "ORDER BY implementation_address != '' DESC "
    "LIMIT 5"
)


def _parse_abi(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _pick_row(rows: list[list[Any]], target: str) -> list[Any] | None:
    """Pick the right row for ``target`` from the (1 or 2) ClickHouse rows."""
    if not rows:
        return None
    has_impl = [r for r in rows if r[1]]
    no_impl = [r for r in rows if not r[1]]
    if target in ("auto", "implementation"):
        return has_impl[0] if has_impl else (no_impl[0] if no_impl else rows[0])
    if target == "proxy":
        return no_impl[0] if no_impl else (has_impl[0] if has_impl else rows[0])
    return rows[0]


def _resolve_from_clickhouse(
    ch: ClickHouseManager,
    address: str,
    target: str,
) -> AbiRecord | None:
    try:
        result = ch.execute_raw_cached(
            _CH_SELECT,
            "dbt",
            f"abi:{address.lower()}:{target}",
            parameters={"addr": address},
        )
    except Exception:
        return None

    row = _pick_row(result.get("rows") or [], target)
    if row is None:
        return None

    abi = _parse_abi(row[2])
    if not abi:
        # Row exists but ABI is empty/garbage — let Blockscout try.
        return None

    return AbiRecord(
        contract_address=row[0],
        implementation_address=row[1] or "",
        contract_name=row[3] or "",
        abi=abi,
        source=row[4] or "clickhouse",
    )


# ---------------------------------------------------------------------------
# Blockscout path
# ---------------------------------------------------------------------------

def _fetch_blockscout(address: str) -> dict[str, Any]:
    url = f"{settings.BLOCKSCOUT_API_BASE_URL}/smart-contracts/{address.lower()}"
    resp = requests.get(
        url,
        timeout=settings.RPC_TIMEOUT_SECONDS,
        headers={
            "Accept": "application/json",
            "User-Agent": "cerebro-mcp/contract-explorer",
        },
    )
    resp.raise_for_status()
    return resp.json()


def _resolve_from_blockscout(address: str, target: str) -> AbiRecord:
    body = _fetch_blockscout(address)
    implementations = body.get("implementations") or []

    if target in ("auto", "implementation") and implementations:
        impl_addr = implementations[0].get("address_hash")
        if impl_addr:
            impl = _fetch_blockscout(impl_addr)
            return AbiRecord(
                contract_address=address,
                implementation_address=Web3.to_checksum_address(impl_addr),
                contract_name=impl.get("name") or body.get("name") or "",
                abi=impl.get("abi") or [],
                source="blockscout",
            )

    return AbiRecord(
        contract_address=address,
        implementation_address="",
        contract_name=body.get("name") or "",
        abi=body.get("abi") or [],
        source="blockscout",
    )


# ---------------------------------------------------------------------------
# On-chain proxy probe (last-resort implementation detection)
# ---------------------------------------------------------------------------

#: EIP-1967: keccak256("eip1967.proxy.implementation") - 1
_EIP1967_IMPL_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)

#: EIP-1167 minimal-proxy runtime bytecode, split around the embedded address.
_EIP1167_PREFIX = bytes.fromhex("363d3d373d3d3d363d73")
_EIP1167_SUFFIX = bytes.fromhex("5af43d82803e903d91602b57fd5bf3")


def _abi_has_functions(abi: list[dict[str, Any]] | None) -> bool:
    return any(item.get("type") == "function" for item in abi or [])


def _abi_has_read_functions(abi: list[dict[str, Any]] | None) -> bool:
    return any(
        item.get("type") == "function"
        and item.get("stateMutability") in ("view", "pure")
        for item in abi or []
    )


def _address_from_word(raw: bytes) -> str:
    """Checksum address from the last 20 bytes of a storage word ('' if zero)."""
    word = bytes(raw).rjust(32, b"\x00")[-20:]
    if word == b"\x00" * 20:
        return ""
    return Web3.to_checksum_address(word)


def _detect_implementation_onchain(address: str) -> str:
    """Find a delegating proxy's implementation the way block explorers do.

    Order: EIP-1167 bytecode, EIP-1967 implementation slot, then storage
    slot 0 (the Safe ``masterCopy`` convention — Blockscout labels these
    "Custom" with ``implementations: []``). Slot values must point at a
    deployed contract to count, so ordinary storage that merely looks like
    an address is rejected. Returns "" when nothing qualifies or the RPC is
    unavailable; only ever called for function-less ABIs, so it can never
    shadow a real contract's own ABI.
    """
    try:
        w3 = rpc_manager.standard
        code = bytes(rpc_manager.retry(w3.eth.get_code, address))
        i = code.find(_EIP1167_PREFIX)
        if i != -1:
            start = i + len(_EIP1167_PREFIX)
            end = start + 20
            if code[end:end + len(_EIP1167_SUFFIX)] == _EIP1167_SUFFIX:
                impl = _address_from_word(code[start:end])
                if impl:
                    return impl
        for slot in (_EIP1967_IMPL_SLOT, 0):
            raw = rpc_manager.retry(w3.eth.get_storage_at, address, slot)
            impl = _address_from_word(raw)
            if (
                impl
                and impl.lower() != address.lower()
                and bytes(rpc_manager.retry(w3.eth.get_code, impl))
            ):
                return impl
    except Exception:  # noqa: BLE001 — RPC down/misconfigured: keep the proxy ABI
        return ""
    return ""


def _resolve_proxy_implementation(
    ch: ClickHouseManager,
    proxy: str,
    proxy_record: AbiRecord,
) -> AbiRecord | None:
    """Resolve the ABI of an on-chain-detected implementation for ``proxy``.

    The returned record keeps the PROXY as ``contract_address`` — view calls
    must hit the proxy so the delegatecalled storage is read.
    """
    impl = _detect_implementation_onchain(proxy)
    if not impl:
        return None
    # target="proxy" fetches the implementation's OWN ABI without another hop.
    impl_record = _resolve_from_clickhouse(ch, impl, "proxy")
    # dbt seeds are often decoding stubs (events + a write fn or two, e.g.
    # the Safe singletons). A contract page needs the read surface — upgrade
    # to Blockscout when the seeded ABI has no view/pure functions, keeping
    # the stub only if Blockscout can't do better.
    if impl_record is None or not _abi_has_read_functions(impl_record.abi):
        try:
            bs_record = _resolve_from_blockscout(impl, "proxy")
        except Exception:  # noqa: BLE001 — unverified impl
            bs_record = None
        if bs_record is not None and _abi_has_functions(bs_record.abi):
            impl_record = bs_record
    if impl_record is None or not _abi_has_functions(impl_record.abi):
        return None
    return AbiRecord(
        contract_address=proxy,
        implementation_address=impl,
        contract_name=impl_record.contract_name or proxy_record.contract_name,
        abi=impl_record.abi,
        source=impl_record.source,
    )
