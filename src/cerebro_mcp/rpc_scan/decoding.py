"""Event-signature parsing and log decoding for the scan engine (pure, no I/O).

Two ways to describe an event:

* **Full form** — explicit ``indexed`` markers and argument names:
  ``"Transfer(address indexed from, address indexed to, uint256 value)"``.
  The layout is unambiguous, so decoded args can be promoted to typed
  scratch-table columns.
* **Short form** — types only: ``"Transfer(address,address,uint256)"``.
  The indexed layout is ambiguous (ERC-20 and ERC-721 share this exact
  signature with different layouts), so short form is only accepted for
  events in ``WELL_KNOWN_EVENTS``, whose entries carry every known layout
  variant. The decoder picks the variant per log line by topic count.

Dynamic indexed values (string/bytes/arrays/tuples) are keccak hashes on
chain — they are stored raw (the topic hex) and never abi-decoded.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from eth_abi import decode as abi_decode
from eth_utils import keccak


@dataclass
class EventInput:
    type: str
    indexed: bool
    name: str


@dataclass
class ParsedEvent:
    name: str
    variants: list[list[EventInput]]  # >=1; matched by indexed-count at decode time
    topic0: str
    canonical: str


class AmbiguousEventError(ValueError):
    """Short-form signature whose indexed layout cannot be inferred."""


def canonical_signature(name: str, types: list[str]) -> str:
    return f"{name}({','.join(types)})"


def event_topic0(canonical: str) -> str:
    return "0x" + keccak(text=canonical).hex()


def pad_address_topic(address: str) -> str:
    """Left-pad an address to the 32-byte topic form used in getLogs filters."""
    bare = address.lower().removeprefix("0x")
    return "0x" + bare.rjust(64, "0")


def is_dynamic_type(sol_type: str) -> bool:
    t = sol_type.strip()
    if t.endswith("]") or t.startswith("("):
        return True
    if t == "string":
        return True
    return t == "bytes"  # bytesN (fixed) fits in a topic; bare bytes does not


def _inp(sol_type: str, indexed: bool, name: str) -> EventInput:
    return EventInput(type=sol_type, indexed=indexed, name=name)


# Short-form signatures with every known indexed layout. Variants are listed
# most-common first; filters built from a short form use variant[0]'s layout.
WELL_KNOWN_EVENTS: dict[str, list[list[EventInput]]] = {
    "Transfer(address,address,uint256)": [
        # ERC-20: from/to indexed, value in data (3 topics)
        [_inp("address", True, "from"), _inp("address", True, "to"),
         _inp("uint256", False, "value")],
        # ERC-721: from/to/tokenId all indexed (4 topics)
        [_inp("address", True, "from"), _inp("address", True, "to"),
         _inp("uint256", True, "tokenId")],
    ],
    "Approval(address,address,uint256)": [
        # ERC-20: owner/spender indexed, value in data
        [_inp("address", True, "owner"), _inp("address", True, "spender"),
         _inp("uint256", False, "value")],
        # ERC-721: owner/approved/tokenId all indexed
        [_inp("address", True, "owner"), _inp("address", True, "approved"),
         _inp("uint256", True, "tokenId")],
    ],
    "ApprovalForAll(address,address,bool)": [
        [_inp("address", True, "owner"), _inp("address", True, "operator"),
         _inp("bool", False, "approved")],
    ],
    "TransferSingle(address,address,address,uint256,uint256)": [
        # ERC-1155: operator/from/to indexed, id+value in data
        [_inp("address", True, "operator"), _inp("address", True, "from"),
         _inp("address", True, "to"), _inp("uint256", False, "id"),
         _inp("uint256", False, "value")],
    ],
    "Deposit(address,uint256)": [
        # WETH-style wrap
        [_inp("address", True, "dst"), _inp("uint256", False, "wad")],
    ],
    "Withdrawal(address,uint256)": [
        [_inp("address", True, "src"), _inp("uint256", False, "wad")],
    ],
}


def parse_event_signature(sig: str) -> ParsedEvent:
    """Parse a full- or short-form event signature.

    Raises ``AmbiguousEventError`` for short forms not in the well-known
    registry, and ``ValueError`` for malformed signatures.
    """
    sig = sig.strip()
    if "(" not in sig or not sig.endswith(")"):
        raise ValueError(f"Malformed event signature: {sig!r}")
    name, rest = sig.split("(", 1)
    name = name.strip()
    body = rest[:-1].strip()
    if "(" in body:
        raise ValueError(
            "Tuple-typed event args are not supported in signature form; "
            "pass decode_abi_address instead."
        )
    parts = [p.strip() for p in body.split(",") if p.strip()] if body else []

    tokenized = [p.split() for p in parts]
    short_form = all(len(toks) == 1 for toks in tokenized)

    if short_form:
        canonical = canonical_signature(name, [t[0] for t in tokenized])
        variants = WELL_KNOWN_EVENTS.get(canonical)
        if parts and variants is None:
            raise AmbiguousEventError(
                f'Event "{canonical}" is not in the well-known registry, so the '
                "indexed layout is ambiguous. Either mark indexed args explicitly "
                '(e.g. "Transfer(address indexed from, address indexed to, '
                'uint256 value)") or pass decode_abi_address to resolve the ABI.'
            )
        if variants is None:
            variants = [[]]
        return ParsedEvent(
            name=name,
            variants=[list(v) for v in variants],
            topic0=event_topic0(canonical),
            canonical=canonical,
        )

    inputs: list[EventInput] = []
    for i, toks in enumerate(tokenized):
        sol_type = toks[0]
        indexed = "indexed" in toks[1:]
        named = [t for t in toks[1:] if t != "indexed"]
        arg_name = named[-1] if named else f"arg{i}"
        inputs.append(EventInput(type=sol_type, indexed=indexed, name=arg_name))
    canonical = canonical_signature(name, [i.type for i in inputs])
    return ParsedEvent(
        name=name,
        variants=[inputs],
        topic0=event_topic0(canonical),
        canonical=canonical,
    )


def parsed_event_from_abi(event_abi: dict[str, Any]) -> ParsedEvent:
    inputs = [
        EventInput(
            type=str(i.get("type", "")),
            indexed=bool(i.get("indexed", False)),
            name=str(i.get("name") or f"arg{n}"),
        )
        for n, i in enumerate(event_abi.get("inputs") or [])
    ]
    name = str(event_abi.get("name", ""))
    canonical = canonical_signature(name, [i.type for i in inputs])
    return ParsedEvent(
        name=name, variants=[inputs],
        topic0=event_topic0(canonical), canonical=canonical,
    )


def _decode_topic_value(sol_type: str, topic: str) -> Any:
    if is_dynamic_type(sol_type):
        return topic  # keccak hash of the value — keep raw
    if sol_type == "address":
        return "0x" + topic[-40:].lower()
    if sol_type.startswith(("uint", "int")):
        value = int(topic, 16)
        # Topics are 32 bytes; signed values arrive sign-extended to 256 bits.
        if sol_type.startswith("int") and value >= 1 << 255:
            value -= 1 << 256
        return value
    if sol_type == "bool":
        return int(topic, 16) != 0
    return topic  # bytesN and anything else: raw topic hex


def _normalize_decoded(sol_type: str, value: Any) -> Any:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if sol_type == "address" and isinstance(value, str):
        return value.lower()
    if isinstance(value, (list, tuple)):
        return [_normalize_decoded("", v) for v in value]
    return value


@dataclass
class EventDecoder:
    """Decodes one event's logs; never raises from ``decode``."""

    event: ParsedEvent
    promote: bool = True  # typed arg_* columns (single-event mode only)
    _by_topic_count: dict[int, list[EventInput]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for variant in self.event.variants:
            n_indexed = sum(1 for i in variant if i.indexed)
            self._by_topic_count.setdefault(1 + n_indexed, variant)

    @property
    def event_name(self) -> str:
        return self.event.name

    @property
    def topic0(self) -> str:
        return self.event.topic0

    def promoted_columns(self) -> list[tuple[str, str, str]]:
        """Union over variants in declaration order: (arg_name, column_name, sol_type).

        Dynamic indexed args get a ``_hash`` column suffix — the stored value
        is the keccak topic, not the value itself.
        """
        seen: set[str] = set()
        cols: list[tuple[str, str, str]] = []
        for variant in self.event.variants:
            for inp in variant:
                if inp.name in seen:
                    continue
                seen.add(inp.name)
                suffix = "_hash" if (inp.indexed and is_dynamic_type(inp.type)) else ""
                col_type = "string_raw" if suffix else inp.type
                cols.append((inp.name, f"arg_{_sanitize(inp.name)}{suffix}", col_type))
        return cols

    def filter_layout(self, arg_name: str) -> tuple[bool, int | None]:
        """(exists, topic_position) for ``arg_name`` using variant[0]'s layout.

        ``topic_position`` is the 1-based topics[] index when the arg is
        indexed, else ``None`` (engine-side filtering required).
        """
        variant = self.event.variants[0]
        position = 1
        for inp in variant:
            if inp.name == arg_name:
                return True, (position if inp.indexed else None)
            if inp.indexed:
                position += 1
        return False, None

    def decode(self, topics: list[str], data: str) -> tuple[dict[str, Any], str]:
        variant = self._by_topic_count.get(len(topics))
        if variant is None:
            return {}, f"no layout variant matches {len(topics)} topics"
        args: dict[str, Any] = {}
        try:
            indexed = [i for i in variant if i.indexed]
            for inp, topic in zip(indexed, topics[1:]):
                args[inp.name] = _decode_topic_value(inp.type, topic)
            unindexed = [i for i in variant if not i.indexed]
            if unindexed:
                raw = bytes.fromhex(data[2:]) if data and data not in ("0x", "") else b""
                values = abi_decode([i.type for i in unindexed], raw)
                for inp, value in zip(unindexed, values):
                    args[inp.name] = _normalize_decoded(inp.type, value)
            return args, ""
        except Exception as exc:  # noqa: BLE001
            return args, f"decode_error: {exc}"


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def args_to_json(args: dict[str, Any]) -> str:
    return json.dumps(args, default=str, separators=(",", ":"))
