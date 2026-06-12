"""Multicall3 aggregate3 encoding/decoding (pure, no I/O).

One ``eth_call`` to Multicall3 reads ~600 targets; ``allowFailure=true`` per
call means one reverting target never aborts the batch. Mirrors the pattern
proven in gp_rpc_forensics/lib/multicall.py.
"""
from __future__ import annotations

from typing import Any

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import keccak

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
SEL_AGGREGATE3 = bytes.fromhex("82ad56cb")

# Classic state-mutating names: calling these via eth_call returns a
# meaningless result, so reject with a teaching error at the tool layer.
KNOWN_MUTATOR_NAMES = {
    "transfer", "transferFrom", "approve", "mint", "burn",
    "setApprovalForAll", "execTransaction", "swap", "swapExactTokensForTokens",
}


def parse_signature(sig: str) -> tuple[str, list[str], list[str]]:
    """'balanceOf(address)(uint256)' -> ('balanceOf', ['address'], ['uint256']).

    Return types are optional: 'getOwners()' -> ('getOwners', [], []).
    Tuples are not supported in signature form.
    """
    sig = sig.strip()
    if "(" not in sig or not sig.endswith(")"):
        raise ValueError(f"Malformed function signature: {sig!r}")
    name, rest = sig.split("(", 1)
    name = name.strip()
    if not name:
        raise ValueError(f"Malformed function signature: {sig!r}")
    groups: list[str] = []
    depth = 0
    current = ""
    for char in "(" + rest:
        if char == "(":
            depth += 1
            if depth == 1:
                current = ""
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                groups.append(current)
                continue
        if depth >= 1:
            current += char
    if depth != 0 or not groups or len(groups) > 2:
        raise ValueError(f"Malformed function signature: {sig!r}")
    if any("(" in g for g in groups):
        raise ValueError(
            "Tuple-typed arguments are not supported in signature form; "
            "use contract_call_function for one-off tuple calls."
        )

    def split(group: str) -> list[str]:
        return [t.strip() for t in group.split(",") if t.strip()]

    inputs = split(groups[0])
    outputs = split(groups[1]) if len(groups) == 2 else []
    return name, inputs, outputs


def selector(name: str, input_types: list[str]) -> bytes:
    return keccak(text=f"{name}({','.join(input_types)})")[:4]


def coerce_arg(sol_type: str, value: Any) -> Any:
    """Coerce JSON-shaped tool args into what eth_abi expects."""
    t = sol_type.strip()
    if t == "address":
        if not isinstance(value, str) or not value.lower().startswith("0x"):
            raise ValueError(f"address arg must be a 0x string, got {value!r}")
        return value
    if t.startswith(("uint", "int")) and not t.endswith("]"):
        if isinstance(value, bool):
            raise ValueError(f"bool given for {t}")
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    if t == "bool":
        return bool(value)
    if t.startswith("bytes"):
        if isinstance(value, str):
            return bytes.fromhex(value.removeprefix("0x"))
        return bytes(value)
    return value


def encode_call(sel: bytes, input_types: list[str], args: list[Any]) -> bytes:
    if len(args) != len(input_types):
        raise ValueError(
            f"argument count mismatch: signature has {len(input_types)} "
            f"input(s), got {len(args)} arg(s)"
        )
    coerced = [coerce_arg(t, a) for t, a in zip(input_types, args)]
    return sel + (abi_encode(input_types, coerced) if input_types else b"")


def encode_aggregate3(calls: list[tuple[str, bool, bytes]]) -> str:
    """calls: [(target_address, allow_failure, calldata)] -> 0x… eth_call data."""
    encoded = abi_encode(["(address,bool,bytes)[]"], [calls])
    return "0x" + (SEL_AGGREGATE3 + encoded).hex()


def decode_aggregate3(raw: str) -> list[tuple[bool, bytes]]:
    data = bytes.fromhex(raw.removeprefix("0x"))
    (out,) = abi_decode(["(bool,bytes)[]"], data)
    return [(bool(ok), bytes(ret)) for ok, ret in out]


def decode_outputs(output_types: list[str], ret: bytes) -> list[Any] | None:
    """Decode return data against declared types. None on failure (caller
    falls back to raw hex) — never raises."""
    if not output_types:
        return None
    if not ret:
        return None
    try:
        return list(abi_decode(output_types, ret))
    except Exception:  # noqa: BLE001
        return None
