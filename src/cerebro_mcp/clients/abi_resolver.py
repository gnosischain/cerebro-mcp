"""Resolve contract ABIs, per chain.

Order of precedence:

1. In-memory TTL cache (keyed by chain + address + target).
2. ``dbt.contracts_abi`` (the dbt-cerebro seed; mirrors the Blockscout API).
   **Gnosis only** — that table is keyed on address alone and holds Gnosis
   data, but addresses collide across chains (CREATE2 factories, Multicall3,
   Safe singletons, Permit2), so consulting it off-Gnosis returns a
   confidently wrong ABI.
3. Live Blockscout fetch against that chain's instance (read-only fallback for
   contracts not yet seeded).
4. Sourcify, which is chain-id-keyed and uniform — the only ABI source for
   chains whose explorer is not Blockscout (BNB, Avalanche, Plasma).

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
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests
from web3 import Web3

from cerebro_mcp.chains import GNOSIS_CHAIN_ID, get_chain
from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.web3 import rpc_manager
from cerebro_mcp.config import settings


@dataclass
class AbiRecord:
    contract_address: str
    implementation_address: str
    contract_name: str
    abi: list[dict[str, Any]]
    source: str  # "clickhouse" | "blockscout" | "sourcify" | dbt source string
    chain_id: int = GNOSIS_CHAIN_ID


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, AbiRecord]] = {}

#: Failed lookups, cached briefly. Without this an unverified contract re-pays
#: the full miss path (Blockscout timeout + Sourcify timeout + on-chain probe)
#: on every single call — which a history sweep does repeatedly.
_negative_cache: dict[str, float] = {}
NEGATIVE_CACHE_TTL_SECONDS = 120

#: Guards BOTH dicts above. Tool bodies run on worker threads (see
#: ``runtime/offload.py::install_tool_offload``), so two resolutions can now
#: interleave here. The read-then-pop and len-then-pop sequences below are not
#: atomic, so without this a concurrent eviction raises StopIteration from
#: ``next(iter(...))`` on an emptied dict, or resurrects a just-expired entry.
_cache_lock = threading.Lock()


def _cache_key(chain_id: int, address: str, target: str) -> str:
    """Chain + address + target.

    ``chain_id`` is load-bearing: the same address is a different contract on
    every chain (Multicall3, USDC proxies, any CREATE2 deployment), so a
    chain-blind key would serve one chain's ABI for all of them.

    ``target`` matters because proxy / impl / auto resolutions for the same
    address must not collide — without it a target=proxy load poisons later
    target=auto / target=implementation calls.
    """
    return f"{chain_id}|{address.lower()}|{target}"


def _cache_get(chain_id: int, address: str, target: str) -> AbiRecord | None:
    key = _cache_key(chain_id, address, target)
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        ts, rec = hit
        if time.time() - ts > settings.ABI_CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return rec


def _cache_set(chain_id: int, address: str, target: str, record: AbiRecord) -> None:
    with _cache_lock:
        if len(_cache) >= settings.ABI_CACHE_MAX_ENTRIES:
            # FIFO eviction — cheap; resolver is not on a hot path.
            _cache.pop(next(iter(_cache)))
        _cache[_cache_key(chain_id, address, target)] = (time.time(), record)


def _negative_cached(chain_id: int, address: str, target: str) -> bool:
    key = _cache_key(chain_id, address, target)
    with _cache_lock:
        ts = _negative_cache.get(key)
        if ts is None:
            return False
        if time.time() - ts > NEGATIVE_CACHE_TTL_SECONDS:
            _negative_cache.pop(key, None)
            return False
        return True


def _negative_cache_set(chain_id: int, address: str, target: str) -> None:
    with _cache_lock:
        if len(_negative_cache) >= settings.ABI_CACHE_MAX_ENTRIES:
            _negative_cache.pop(next(iter(_negative_cache)))
        _negative_cache[_cache_key(chain_id, address, target)] = time.time()


def clear_caches() -> None:
    """Drop both the hit and miss caches.

    Call after re-seeding ``dbt.contracts_abi`` or verifying a contract that
    previously 404'd — otherwise the miss is remembered for
    ``NEGATIVE_CACHE_TTL_SECONDS``. Tests use it for isolation.
    """
    with _cache_lock:
        _cache.clear()
        _negative_cache.clear()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_abi(
    ch: ClickHouseManager,
    address: str,
    target: str = "auto",
    chain_id: int = GNOSIS_CHAIN_ID,
) -> AbiRecord:
    """Return an :class:`AbiRecord` for ``address`` on ``chain_id``.

    Args:
        ch: ClickHouse manager (used to query ``dbt.contracts_abi``).
        address: Hex address (any case).
        target: ``"auto"`` (default) or ``"implementation"`` returns the
            implementation ABI when the contract is a proxy. ``"proxy"``
            forces the proxy's own ABI.
        chain_id: Chain to resolve against. Defaults to Gnosis, preserving the
            pre-multi-chain behavior for callers that do not pass one.

    Raises:
        ValueError: If no source yields a usable ABI. The message names every
            source that was tried — across a dozen chains, a bare "not found"
            is not actionable.
    """
    chain = get_chain(chain_id)
    checksum = Web3.to_checksum_address(address)
    if (cached := _cache_get(chain.chain_id, checksum, target)) is not None:
        return cached
    if _negative_cached(chain.chain_id, checksum, target):
        raise ValueError(
            f"ABI not found for {checksum} on {chain.name} (cached miss)"
        )

    tried: list[str] = []
    record: AbiRecord | None = None

    # dbt seed is Gnosis-only data keyed on address alone — see module docstring.
    if chain.chain_id == GNOSIS_CHAIN_ID:
        record = _resolve_from_clickhouse(ch, checksum, target)
        tried.append("dbt.contracts_abi")

    if record is None:
        record, note = _resolve_from_explorer(checksum, target, chain.chain_id)
        tried.append(note)

    # Neither source knew this was a proxy, yet the ABI has no callable
    # surface (constructor/fallback only) — a bare delegating proxy whose
    # implementation pointer neither source tracks (Safe masterCopy in
    # slot 0, unverified EIP-1967/1167 shells). Probe the chain and resolve
    # the implementation's ABI instead; calls still target the proxy.
    # No `record.abi` requirement here: an UNVERIFIED bare proxy resolves to no
    # ABI at all, and that is exactly the case where the on-chain probe is the
    # only thing that can find the implementation.
    if (
        target in ("auto", "implementation")
        and (record is None or not record.implementation_address)
        and not _abi_has_functions(record.abi if record else [])
    ):
        base = record or AbiRecord(
            contract_address=checksum,
            implementation_address="",
            contract_name="",
            abi=[],
            source="",
            chain_id=chain.chain_id,
        )
        probed = _resolve_proxy_implementation(ch, checksum, base, chain.chain_id)
        if probed is not None:
            record = probed
            tried.append("on-chain proxy probe")

    if record is None or not record.abi:
        _negative_cache_set(chain.chain_id, checksum, target)
        raise ValueError(
            f"ABI not found for {checksum} on {chain.name} "
            f"(chain {chain.chain_id}). Tried: {', '.join(tried) or 'nothing'}."
        )

    _cache_set(chain.chain_id, checksum, target, record)
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

_HTTP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "cerebro-mcp/contract-explorer",
}

#: Per-source timeout. Deliberately below RPC_TIMEOUT_SECONDS: a full miss
#: walks Blockscout then Sourcify, and these tools are synchronous.
_EXPLORER_TIMEOUT_SECONDS = 8


def _blockscout_api_base(chain_id: int) -> str:
    """API root for a chain's Blockscout instance, or "" if it has none.

    Chain 100 honors the legacy ``BLOCKSCOUT_API_BASE_URL`` override so
    existing deployments keep pointing wherever they were pointed.
    """
    if chain_id == GNOSIS_CHAIN_ID and settings.BLOCKSCOUT_API_BASE_URL:
        return settings.BLOCKSCOUT_API_BASE_URL.rstrip("/")
    return get_chain(chain_id).explorer.api_base_url


def _fetch_blockscout(address: str, chain_id: int) -> dict[str, Any]:
    base = _blockscout_api_base(chain_id)
    if not base:
        raise ValueError("no Blockscout instance for this chain")
    resp = requests.get(
        f"{base}/smart-contracts/{address.lower()}",
        timeout=_EXPLORER_TIMEOUT_SECONDS,
        headers=_HTTP_HEADERS,
    )
    resp.raise_for_status()
    return resp.json()


def _resolve_from_blockscout(
    address: str,
    target: str,
    chain_id: int = GNOSIS_CHAIN_ID,
) -> AbiRecord:
    body = _fetch_blockscout(address, chain_id)
    implementations = body.get("implementations") or []

    if target in ("auto", "implementation") and implementations:
        impl_addr = implementations[0].get("address_hash")
        if impl_addr:
            impl = _fetch_blockscout(impl_addr, chain_id)
            return AbiRecord(
                contract_address=address,
                implementation_address=Web3.to_checksum_address(impl_addr),
                contract_name=impl.get("name") or body.get("name") or "",
                abi=impl.get("abi") or [],
                source="blockscout",
                chain_id=chain_id,
            )

    return AbiRecord(
        contract_address=address,
        implementation_address="",
        contract_name=body.get("name") or "",
        abi=body.get("abi") or [],
        source="blockscout",
        chain_id=chain_id,
    )


# ---------------------------------------------------------------------------
# Sourcify path (chain-id keyed; the only source for non-Blockscout chains)
# ---------------------------------------------------------------------------

SOURCIFY_API_BASE = "https://sourcify.dev/server"


def _resolve_from_sourcify(
    address: str,
    chain_id: int,
) -> AbiRecord | None:
    """Fetch a verified ABI from Sourcify.

    Sourcify indexes by ``(chain_id, address)``, so it works uniformly on every
    chain — including BNB, Avalanche, and Plasma, whose explorers expose no ABI
    API at all. It does NOT resolve proxies; ``_detect_implementation_onchain``
    covers that on those chains.
    """
    try:
        resp = requests.get(
            f"{SOURCIFY_API_BASE}/v2/contract/{int(chain_id)}/{address}",
            params={"fields": "abi"},
            timeout=_EXPLORER_TIMEOUT_SECONDS,
            headers=_HTTP_HEADERS,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
    except Exception:  # noqa: BLE001 — Sourcify is a best-effort fallback
        return None

    abi = body.get("abi")
    if not isinstance(abi, list) or not abi:
        return None

    return AbiRecord(
        contract_address=address,
        implementation_address="",
        contract_name=(body.get("compilation") or {}).get("name") or "",
        abi=abi,
        source="sourcify",
        chain_id=chain_id,
    )


def _resolve_from_explorer(
    address: str,
    target: str,
    chain_id: int,
) -> tuple[AbiRecord | None, str]:
    """Blockscout first where it exists, then Sourcify. Returns (record, note).

    ``note`` names the sources actually attempted, for the not-found message.
    """
    attempted: list[str] = []

    if _blockscout_api_base(chain_id):
        attempted.append("blockscout")
        try:
            record = _resolve_from_blockscout(address, target, chain_id)
            if record.abi:
                return record, "blockscout"
        except Exception:  # noqa: BLE001 — unverified / 404 / instance down
            pass

    attempted.append("sourcify")
    record = _resolve_from_sourcify(address, chain_id)
    if record is not None:
        return record, "sourcify"

    return None, "+".join(attempted)


# ---------------------------------------------------------------------------
# On-chain proxy probe (last-resort implementation detection)
# ---------------------------------------------------------------------------

#: EIP-1967: keccak256("eip1967.proxy.implementation") - 1
_EIP1967_IMPL_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)

#: EIP-1967: keccak256("eip1967.proxy.beacon") - 1
_EIP1967_BEACON_SLOT = (
    "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
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


#: ``implementation()`` — the EIP-1967 beacon interface.
_IMPLEMENTATION_SELECTOR = "0x5c60da1b"

#: Cap on bytecode-immutable candidates probed. A delegating proxy is tiny and
#: embeds one or two addresses; the cap stops a pathological contract from
#: turning detection into an eth_getCode storm.
_MAX_IMMUTABLE_CANDIDATES = 8


def _has_code(w3, address: str) -> bool:
    return bool(bytes(rpc_manager.retry(w3.eth.get_code, address)))


def _addresses_in_code(code: bytes) -> list[str]:
    """Addresses embedded as PUSH20/PUSH32 operands (Solidity immutables).

    Beacon proxies of the Nomad/Optics family keep the beacon in an
    ``immutable``, which the compiler inlines into the runtime bytecode — no
    storage slot holds it, so slot reads alone can never find it. Parsing the
    push opcodes (rather than sliding a 20-byte window) keeps the candidate
    list to the handful of real constants.
    """
    out: list[str] = []
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        if op == 0x73:            # PUSH20 — a bare address literal
            word, i = code[i + 1:i + 21], i + 21
        elif op == 0x7F:          # PUSH32 — an address right-aligned in a word
            word, i = code[i + 1:i + 33], i + 33
        elif 0x60 <= op <= 0x7F:  # any other PUSHn — skip its operand
            i += 1 + (op - 0x5F)
            continue
        else:
            i += 1
            continue
        candidate = _address_from_word(word)
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def _implementation_behind_beacon(w3, beacon: str) -> str:
    """Ask a beacon for the implementation it points at.

    Two dialects: the EIP-1967 ``implementation()`` selector, and the Nomad
    ``UpgradeBeacon``, whose fallback answers ANY calldata with the address.
    Returns "" unless the answer is a deployed contract.
    """
    for data in (_IMPLEMENTATION_SELECTOR, "0x"):
        try:
            raw = bytes(rpc_manager.retry(w3.eth.call, {"to": beacon, "data": data}))
        except Exception:  # noqa: BLE001 — not a beacon, or reverts on this shape
            continue
        if len(raw) < 20:
            continue
        impl = _address_from_word(raw[-32:] if len(raw) >= 32 else raw)
        if impl and impl.lower() != beacon.lower() and _has_code(w3, impl):
            return impl
    return ""


def _detect_implementation_onchain(
    address: str,
    chain_id: int = GNOSIS_CHAIN_ID,
) -> str:
    """Find a delegating proxy's implementation the way block explorers do.

    Order, standards before heuristics:

    1. EIP-1167 minimal-proxy bytecode.
    2. EIP-1967 implementation slot.
    3. EIP-1967 beacon slot — the slot holds the BEACON, so the implementation
       needs a second hop through it.
    4. Storage slot 0 (the Safe ``masterCopy`` convention — Blockscout labels
       these "Custom" with ``implementations: []``).
    5. Addresses inlined in the bytecode as immutables, each tried as a beacon
       first and accepted directly otherwise. This is what finds the
       Nomad/Optics ``UpgradeBeaconProxy``, which keeps its beacon in an
       immutable and leaves every storage slot empty.

    Slot and bytecode values must point at a deployed contract to count, so
    ordinary storage that merely looks like an address is rejected. Returns ""
    when nothing qualifies or the RPC is unavailable; only ever called for
    ABIs with no callable functions, so it can never shadow a real contract's
    own ABI.

    This carries more weight off-Gnosis: Sourcify does not resolve proxies, so
    on chains without a Blockscout instance this is the ONLY proxy detection
    there is.
    """
    try:
        w3 = rpc_manager.standard(chain_id)
        code = bytes(rpc_manager.retry(w3.eth.get_code, address))

        i = code.find(_EIP1167_PREFIX)
        if i != -1:
            start = i + len(_EIP1167_PREFIX)
            end = start + 20
            if code[end:end + len(_EIP1167_SUFFIX)] == _EIP1167_SUFFIX:
                impl = _address_from_word(code[start:end])
                if impl:
                    return impl

        for slot, via_beacon in (
            (_EIP1967_IMPL_SLOT, False),
            (_EIP1967_BEACON_SLOT, True),
            (0, False),
        ):
            raw = rpc_manager.retry(w3.eth.get_storage_at, address, slot)
            found = _address_from_word(raw)
            if not found or found.lower() == address.lower():
                continue
            if not _has_code(w3, found):
                continue
            if via_beacon:
                impl = _implementation_behind_beacon(w3, found)
                if impl:
                    return impl
                continue
            return found

        # Immutables: the beacon/implementation never touched storage.
        candidates = [
            c for c in _addresses_in_code(code) if c.lower() != address.lower()
        ][:_MAX_IMMUTABLE_CANDIDATES]
        for candidate in candidates:
            if not _has_code(w3, candidate):
                continue
            impl = _implementation_behind_beacon(w3, candidate)
            if impl:
                return impl
            return candidate
    except Exception:  # noqa: BLE001 — RPC down/misconfigured: keep the proxy ABI
        return ""
    return ""


def _resolve_proxy_implementation(
    ch: ClickHouseManager,
    proxy: str,
    proxy_record: AbiRecord,
    chain_id: int = GNOSIS_CHAIN_ID,
) -> AbiRecord | None:
    """Resolve the ABI of an on-chain-detected implementation for ``proxy``.

    The returned record keeps the PROXY as ``contract_address`` — view calls
    must hit the proxy so the delegatecalled storage is read.
    """
    impl = _detect_implementation_onchain(proxy, chain_id)
    if not impl:
        return None
    # target="proxy" fetches the implementation's OWN ABI without another hop.
    impl_record = (
        _resolve_from_clickhouse(ch, impl, "proxy")
        if chain_id == GNOSIS_CHAIN_ID
        else None
    )
    # dbt seeds are often decoding stubs (events + a write fn or two, e.g.
    # the Safe singletons). A contract page needs the read surface — upgrade
    # to the live explorer when the seeded ABI has no view/pure functions,
    # keeping the stub only if the explorer can't do better.
    if impl_record is None or not _abi_has_read_functions(impl_record.abi):
        explorer_record, _ = _resolve_from_explorer(impl, "proxy", chain_id)
        if explorer_record is not None and _abi_has_functions(explorer_record.abi):
            impl_record = explorer_record
    if impl_record is None or not _abi_has_functions(impl_record.abi):
        return None
    return AbiRecord(
        contract_address=proxy,
        implementation_address=impl,
        contract_name=impl_record.contract_name or proxy_record.contract_name,
        abi=impl_record.abi,
        source=impl_record.source,
        chain_id=chain_id,
    )
