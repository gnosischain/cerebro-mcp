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
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests
from web3 import Web3

from cerebro_mcp.clients.clickhouse import ClickHouseManager
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
