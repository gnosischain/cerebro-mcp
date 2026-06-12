"""Scratch-table DDL builders and the solidity -> ClickHouse type map.

Every scan table is ``ReplacingMergeTree(_scanned_at)`` ordered by the
kind's natural dedup key, so checkpoint-resume overlap is idempotent.
Counts over these tables must use ``uniqExact(<dedup key>)`` or ``FINAL``
— pre-merge ``count()`` overcounts after a resume.

``u256`` is the probed big-integer type: ``UInt256`` where the server
supports it, ``Decimal(76, 0)`` otherwise (ScratchStore.uint256_type).
"""
from __future__ import annotations

from cerebro_mcp.rpc_scan.decoding import EventDecoder

ColumnsDDL = list[str]
ColumnNames = list[str]


def ch_type_for_solidity(sol: str, *, u256: str) -> str:
    t = sol.strip()
    if t == "string_raw":  # dynamic indexed arg stored as raw topic hex
        return "String"
    if t == "address":
        return "String"
    if t == "bool":
        return "UInt8"
    if t.endswith("]"):
        return "Array(String)"
    if t.startswith("uint"):
        bits = int(t[4:] or 256)
        return "UInt64" if bits <= 64 else u256
    if t.startswith("int"):
        bits = int(t[3:] or 256)
        if bits <= 64:
            return "Int64"
        return "Int256" if u256 == "UInt256" else "Decimal(76, 0)"
    return "String"  # bytes/bytesN/string/tuples: hex or JSON


def default_for_ch_type(ch_type: str):
    if ch_type.startswith(("UInt", "Int", "Decimal")):
        return 0
    if ch_type.startswith("Array"):
        return []
    return ""


def _ddl(cols: list[tuple[str, str]]) -> ColumnsDDL:
    return [f"`{name}` {ch_type}" for name, ch_type in cols]


_LOGS_BASE: list[tuple[str, str]] = [
    ("block_number", "UInt64"),
    ("tx_hash", "String"),
    ("tx_index", "UInt32"),
    ("log_index", "UInt32"),
    ("address", "String"),
    ("topic0", "String"),
    ("topic1", "String"),
    ("topic2", "String"),
    ("topic3", "String"),
    ("data", "String"),
    ("event_name", "LowCardinality(String)"),
    ("args_json", "String"),
    ("decode_error", "String"),
]

_SCANNED_AT = ("_scanned_at", "DateTime DEFAULT now()")


def logs_table_ddl(
    decoders: list[EventDecoder], *, u256: str,
) -> tuple[ColumnsDDL, str, ColumnNames]:
    """(columns_ddl, order_by, insert_columns). Typed ``arg_*`` columns are
    promoted only when a single decoder with ``promote=True`` is active."""
    cols = list(_LOGS_BASE)
    if len(decoders) == 1 and decoders[0].promote:
        for _arg, col_name, sol_type in decoders[0].promoted_columns():
            cols.append((col_name, ch_type_for_solidity(sol_type, u256=u256)))
    insert_columns = [name for name, _ in cols]
    cols.append(_SCANNED_AT)
    return _ddl(cols), "ORDER BY (block_number, log_index)", insert_columns


def calls_table_ddl(
    aliases: list[tuple[str, list[str]]], *, u256: str,
) -> tuple[ColumnsDDL, str, ColumnNames]:
    """Wide row per swept address. ``aliases`` is [(alias, output_types)]."""
    cols: list[tuple[str, str]] = [("address", "String"), ("block_number", "UInt64")]
    for alias, output_types in aliases:
        cols.append((f"{alias}_success", "UInt8"))
        for i, out_type in enumerate(output_types):
            cols.append((f"{alias}_out_{i}", ch_type_for_solidity(out_type, u256=u256)))
        if not output_types:
            cols.append((f"{alias}_out_raw", "String"))
        cols.append((f"{alias}_error", "String"))
    insert_columns = [name for name, _ in cols]
    cols.append(_SCANNED_AT)
    return _ddl(cols), "ORDER BY (address)", insert_columns


def storage_table_ddl(*, u256: str) -> tuple[ColumnsDDL, str, ColumnNames]:
    cols: list[tuple[str, str]] = [
        ("address", "String"),
        ("slot", "String"),
        ("block_number", "UInt64"),
        ("value", "String"),
        ("value_uint", u256),
        ("value_address", "String"),
        ("error", "String"),
    ]
    insert_columns = [name for name, _ in cols]
    cols.append(_SCANNED_AT)
    return _ddl(cols), "ORDER BY (address, slot)", insert_columns


def code_table_ddl(*, store_bytecode: bool) -> tuple[ColumnsDDL, str, ColumnNames]:
    cols: list[tuple[str, str]] = [
        ("address", "String"),
        ("block_number", "UInt64"),
        ("has_code", "UInt8"),
        ("code_size", "UInt32"),
        ("code_hash", "String"),
        ("code_prefix_16", "String"),
        ("is_eip1167", "UInt8"),
        ("eip1167_impl", "String"),
        ("eip1967_impl", "String"),
        ("eip1967_admin", "String"),
        ("eip1967_beacon", "String"),
        ("error", "String"),
    ]
    if store_bytecode:
        cols.append(("bytecode", "String"))
    insert_columns = [name for name, _ in cols]
    cols.append(_SCANNED_AT)
    return _ddl(cols), "ORDER BY (address)", insert_columns


def traces_table_ddl(*, u256: str) -> tuple[ColumnsDDL, str, ColumnNames]:
    cols: list[tuple[str, str]] = [
        ("block_number", "UInt64"),
        ("tx_hash", "String"),
        ("trace_address", "String"),
        ("call_type", "LowCardinality(String)"),
        ("from_address", "String"),
        ("to_address", "String"),
        ("value_wei", u256),
        ("gas_used", "UInt64"),
        ("input_selector", "String"),
        ("success", "UInt8"),
        ("error", "String"),
    ]
    insert_columns = [name for name, _ in cols]
    cols.append(_SCANNED_AT)
    return _ddl(cols), "ORDER BY (block_number, tx_hash, trace_address)", insert_columns


def blocks_table_ddl() -> tuple[ColumnsDDL, str, ColumnNames]:
    cols: list[tuple[str, str]] = [
        ("address", "String"),
        ("kind", "LowCardinality(String)"),
        ("found_block", "UInt64"),
        ("value_before", "String"),
        ("value_after", "String"),
        ("error", "String"),
    ]
    insert_columns = [name for name, _ in cols]
    cols.append(_SCANNED_AT)
    return _ddl(cols), "ORDER BY (address, kind)", insert_columns


DEDUP_KEYS: dict[str, str] = {
    "logs": "(block_number, log_index)",
    "calls": "(address)",
    "storage": "(address, slot)",
    "code": "(address)",
    "traces": "(block_number, tx_hash, trace_address)",
    "blocks": "(address, kind)",
}
