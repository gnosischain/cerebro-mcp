"""Forensic semantics for non-actor EVM addresses.

The zero and conventional dead addresses occur in ERC-20 ``Transfer`` logs,
but they are supply endpoints rather than counterparties.  Keeping this rule
in one small module prevents graph walkers, coverage counts, and receipt views
from independently (and inconsistently) deciding whether they are actors.
"""

from __future__ import annotations

from typing import Literal

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEAD_ADDRESS = "0x000000000000000000000000000000000000dead"

STRUCTURAL_TERMINALS = frozenset({ZERO_ADDRESS, DEAD_ADDRESS})

MoneyEventKind = Literal["mint", "burn", "transfer"]


def normalize_evm_address(value: object) -> str:
    """Return the comparison form used by warehouse address columns."""

    return str(value or "").strip().lower()


def is_structural_terminal(value: object) -> bool:
    """Whether *value* is a mint/burn endpoint and never a wallet actor."""

    return normalize_evm_address(value) in STRUCTURAL_TERMINALS


def money_event_kind(source: object, target: object) -> MoneyEventKind:
    """Classify a standard ERC-20 leg without inferring intent.

    A leg emitted from a structural endpoint is a mint; a leg sent to one is
    a burn.  Ordinary address-to-address legs remain transfers.  The source
    check intentionally wins for the degenerate structural-to-structural case.
    """

    if is_structural_terminal(source):
        return "mint"
    if is_structural_terminal(target):
        return "burn"
    return "transfer"


def structural_terminal_label(value: object) -> str:
    """Concise, non-actor label for a structural endpoint."""

    address = normalize_evm_address(value)
    if address == ZERO_ADDRESS:
        return "Zero address"
    if address == DEAD_ADDRESS:
        return "Dead address"
    return "Structural endpoint"
