"""Contract Explorer mini-app — Etherscan-style read-only contract page.

Lets the user paste a contract address, auto-resolves the implementation ABI
when the contract is a proxy (via ``dbt.contracts_abi`` first, Blockscout as
fallback), and surfaces every ``view``/``pure`` function as an inline form
the user can fill and call. Sibling mini-app to ``Token Explorer``.

Tool surface (model-callable):

    open_contract_explorer(address?, target?, title?)
        Launch the explorer for a contract address. The natural choice when
        the user says "launch contract explorer", "open the contract page
        for X", "let me poke at contract X", or pastes a bare address and
        asks what it does.

    load_contract_explorer_address(view_id, address, target?)
        Swap to a different contract inside an open explorer view.

    contract_explorer_call_function(view_id, function_name?, ...)
        Call one view/pure function from the explorer.

Wire format
-----------

The frontend at ``ui://cerebro/contract_explorer`` consumes
``CallToolResult.structuredContent`` shaped as ``MiniAppPayload``. The
``view_state`` carried in ``INITIAL_LOAD`` payloads is::

    {
      "address":                "0x420C...3430",   # checksum
      "contract_name":          "GnosisControllerToken",
      "abi_source":             "clickhouse",      # | "blockscout"
      "implementation_address": "0x60cb...635D",   # "" if not a proxy
      "target":                 "auto",            # auto|implementation|proxy
      "read_functions":  [{name, signature, stateMutability,
                           inputs:  [{name,type}],
                           outputs: [{name,type}]}, ...],
      "write_functions": [...],                    # render disabled
      "events":          [{name, signature,
                           inputs: [{name,type,indexed}]}, ...],
      "call_history":    [{function, signature, args, block, called_at,
                           ok, result?, error?, elapsed_seconds}, ...],
      "warnings":        [str, ...],
    }

``contract_explorer_call_function`` emits a ``PATCH_VIEW_STATE`` whose
``patch`` contains the new ``call_history`` slice (capped at
``MAX_CALL_HISTORY``).
"""
from __future__ import annotations

import importlib.resources
import logging
from datetime import datetime, timezone
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.abi_resolver import resolve_abi
from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.models.mini_app import MiniAppPayload, SummaryCard
from cerebro_mcp.tools.visualization import mini_apps
from cerebro_mcp.tools.web3.rpc import call_view_function

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bundled React UI
# ---------------------------------------------------------------------------

_BUNDLED_HTML: str | None = None


def get_contract_explorer_html() -> str:
    """Load the Vite-built single-file React app from the static package.

    Built by ``make build-ui-contract-explorer`` from ``ui/contract-explorer.html``.
    Falls back to a small notice if the bundle has not been built yet, so the
    server still starts and the resource URI still resolves (which is enough
    for ``open_contract_explorer`` to succeed without 404'ing on the host).
    """
    global _BUNDLED_HTML
    if _BUNDLED_HTML is None:
        try:
            _BUNDLED_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/contract_explorer.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            logger.warning(
                "static/contract_explorer.html not found — run "
                "`make build-ui-contract-explorer`. Serving stub.",
            )
            _BUNDLED_HTML = (
                "<!doctype html><html><head>"
                "<meta charset=\"utf-8\">"
                "<title>Contract Explorer</title></head><body>"
                "<div id=\"root\" style=\"font-family:system-ui;padding:2rem\">"
                "<h2>Contract Explorer UI bundle not built</h2>"
                "<p>Run <code>make build-ui-contract-explorer</code> from the "
                "cerebro-mcp repo root, then restart the server.</p>"
                "<p>The MCP tools (<code>open_contract_explorer</code>, "
                "<code>contract_explorer_call_function</code>) work in chat "
                "regardless of the UI bundle.</p>"
                "</div></body></html>"
            )
    return _BUNDLED_HTML


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_ID = "contract_explorer"
APP_TITLE = "Contract Explorer"
RESOURCE_URI = "ui://cerebro/contract_explorer"
APP_META = {"ui": {"resourceUri": RESOURCE_URI}}

#: Cap on the call history retained in ``view_state``. Keep small — the whole
#: history is shipped on every PATCH so big lists bloat the wire payload.
MAX_CALL_HISTORY = 50


# ---------------------------------------------------------------------------
# ABI projection
# ---------------------------------------------------------------------------

def _signature(item: dict[str, Any]) -> str:
    name = item.get("name", "")
    types = ",".join(i.get("type", "") for i in item.get("inputs", []))
    return f"{name}({types})"


def _project_abi(abi: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Convert a raw ABI into frontend-friendly read/write/event lists."""
    read: list[dict[str, Any]] = []
    write: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in abi or []:
        kind = item.get("type")
        if kind == "function":
            entry = {
                "name": item.get("name", ""),
                "signature": _signature(item),
                "stateMutability": item.get("stateMutability", ""),
                "inputs": [
                    {
                        "name": i.get("name") or f"arg{n}",
                        "type": i.get("type", ""),
                    }
                    for n, i in enumerate(item.get("inputs", []) or [])
                ],
                "outputs": [
                    {
                        "name": o.get("name", ""),
                        "type": o.get("type", ""),
                    }
                    for o in item.get("outputs", []) or []
                ],
            }
            if entry["stateMutability"] in ("view", "pure"):
                read.append(entry)
            else:
                write.append(entry)
        elif kind == "event":
            events.append({
                "name": item.get("name", ""),
                "signature": _signature(item),
                "inputs": [
                    {
                        "name": i.get("name", ""),
                        "type": i.get("type", ""),
                        "indexed": bool(i.get("indexed")),
                    }
                    for i in item.get("inputs", []) or []
                ],
            })
    read.sort(key=lambda e: (e["name"].lower(), e["signature"]))
    write.sort(key=lambda e: (e["name"].lower(), e["signature"]))
    events.sort(key=lambda e: e["name"].lower())
    return {"read_functions": read, "write_functions": write, "events": events}


# ---------------------------------------------------------------------------
# View-state builders
# ---------------------------------------------------------------------------

def _short_address(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) >= 12 else addr


def _empty_view_state() -> dict[str, Any]:
    return {
        "address": "",
        "contract_name": "",
        "abi_source": "",
        "implementation_address": "",
        "target": "auto",
        "read_functions": [],
        "write_functions": [],
        "events": [],
        "call_history": [],
        "warnings": [],
    }


def _build_view_state(
    *,
    record_address: str,
    record,  # AbiRecord
    target: str,
    projected: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "address": record_address,
        "contract_name": record.contract_name,
        "abi_source": record.source,
        "implementation_address": record.implementation_address,
        "target": target,
        "read_functions": projected["read_functions"],
        "write_functions": projected["write_functions"],
        "events": projected["events"],
        "call_history": [],
        "warnings": [],
    }


def _summary_cards(view_state: dict[str, Any]) -> list[SummaryCard]:
    cards = [
        SummaryCard(
            label="Contract",
            value=view_state["contract_name"] or "unknown",
            tone="neutral",
        ),
        SummaryCard(
            label="ABI source",
            value=view_state["abi_source"] or "—",
            tone="neutral",
        ),
        SummaryCard(
            label="Read functions",
            value=str(len(view_state["read_functions"])),
            tone="neutral",
        ),
    ]
    if view_state["implementation_address"]:
        cards.append(SummaryCard(
            label="Implementation",
            value=_short_address(view_state["implementation_address"]),
            tone="positive",
        ))
    return cards


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_contract_explorer_tools(mcp, ch: ClickHouseManager) -> None:
    """Register the Contract Explorer mini-app tools."""

    mini_apps.register_app(
        APP_ID,
        title=APP_TITLE,
        resource_uri=RESOURCE_URI,
    )

    @mcp.resource(
        RESOURCE_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_contract_explorer_app() -> str:
        """Serve the bundled Contract Explorer single-file app."""
        return get_contract_explorer_html()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _initial_load_payload(
        view_id: str,
        title: str,
        view_state: dict[str, Any],
    ) -> MiniAppPayload:
        return MiniAppPayload(
            type="INITIAL_LOAD",
            view_id=view_id,
            app_id=APP_ID,
            title=title,
            status="ready",
            summary_cards=_summary_cards(view_state),
            view_state=view_state,
        )

    def _resolve_and_build(address: str, target: str):
        """Resolve ABI + project it. Returns ``(view_state, error_message)``."""
        try:
            from web3 import Web3
            checksum = Web3.to_checksum_address(address)
        except Exception as exc:  # noqa: BLE001
            return None, f"Bad address: {exc}"
        try:
            record = resolve_abi(ch, checksum, target=target)
        except Exception as exc:  # noqa: BLE001
            return None, f"ABI not found for {checksum}: {exc}"
        projected = _project_abi(record.abi)
        view_state = _build_view_state(
            record_address=checksum,
            record=record,
            target=target,
            projected=projected,
        )
        return view_state, None

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @mcp.tool(meta=APP_META)
    def open_contract_explorer(
        address: str = "",
        target: str = "auto",
        title: str = "",
    ) -> CallToolResult:
        """Launch the Contract Explorer — an Etherscan-style read-only contract page.

        Use this when the user says any of:
          - "launch contract explorer", "open contract explorer", "open contract page for X"
          - "let me poke at / inspect / explore contract X"
          - "show me what functions contract X has"
          - pastes a bare contract address and asks what it does
          - wants to repeatedly query a single contract through a UI

        The mini-app resolves the ABI from ``dbt.contracts_abi`` first, then
        Blockscout, automatically following proxies (target="auto" returns
        the implementation ABI by default — pass target="proxy" only when the
        user explicitly wants the proxy's own ABI). Lists every view/pure
        function as a card with input forms; users click "Call" to query.

        With an empty ``address``, opens an empty explorer that prompts the
        user to paste a contract address.

        For ABI-level read/write inspection of arbitrary EVM contracts.
        """
        title = title.strip() or APP_TITLE

        if not address.strip():
            view_id = mini_apps.create_view(APP_ID, title)
            view_state = _empty_view_state()
            payload = _initial_load_payload(view_id, title, view_state)
            return mini_apps.payload_to_call_tool_result(
                payload,
                summary_text="Contract Explorer ready — paste a contract address.",
            )

        view_state, err = _resolve_and_build(address, target)
        if err is not None:
            payload = MiniAppPayload(
                type="SHOW_WARNING",
                view_id="",
                app_id=APP_ID,
                title=title,
                status="error",
                warnings=[err],
            )
            return mini_apps.payload_to_call_tool_result(
                payload,
                summary_text=err,
            )

        view_id = mini_apps.create_view(APP_ID, title)
        # Persist the view_state so subsequent contract_explorer_call_function
        # reads the correct address/target from it.
        mini_apps.patch_view_state(view_id, view_state)

        payload = _initial_load_payload(view_id, title, view_state)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Contract Explorer ready for "
                f"{view_state['contract_name'] or 'unknown'} "
                f"({_short_address(view_state['address'])}) — "
                f"{len(view_state['read_functions'])} read function(s)."
            ),
        )

    @mcp.tool(meta=APP_META)
    def load_contract_explorer_address(
        view_id: str,
        address: str,
        target: str = "auto",
    ) -> CallToolResult:
        """Swap to a different contract inside an open Contract Explorer view.

        Use when the user wants to look at a different contract without losing
        the current explorer tab. Reuses the existing ``view_id``; replaces
        ``view_state`` and emits a fresh ``INITIAL_LOAD`` payload so the
        frontend can do a clean swap.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        view_state, err = _resolve_and_build(address, target)
        if err is not None:
            return mini_apps.error_call_tool_result(err)

        # Replace, don't merge — _deep_merge would keep stale read_functions
        # entries from the prior contract.
        record.view_state = view_state

        payload = _initial_load_payload(view_id, record.title, view_state)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Contract Explorer swapped to "
                f"{view_state['contract_name'] or 'unknown'} "
                f"({_short_address(view_state['address'])})."
            ),
        )

    @mcp.tool(meta=APP_META)
    def contract_explorer_call_function(
        view_id: str,
        function_name: str = "",
        function_signature: str = "",
        args: list[Any] | None = None,
        block_identifier: int | str = "latest",
        from_address: str = "",
    ) -> CallToolResult:
        """Call one view/pure function on the Contract Explorer's current contract.

        Use when the user, while looking at an open Contract Explorer view, asks
        to call a specific function (e.g. "click balanceOf with 0x…",
        "call symbol", "what does totalSupply return"). The result (or
        validation error) is appended to the view's ``call_history`` and
        emitted as a ``PATCH_VIEW_STATE`` payload so the frontend can update
        the function's card inline.

        For one-off chat queries that don't need a UI, prefer
        ``contract_call_function`` instead — same underlying call path, plain
        text output, no view bookkeeping.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        address = record.view_state.get("address") or ""
        if not address:
            return mini_apps.error_call_tool_result(
                "Inspector view has no address yet — open with an address first."
            )
        target = record.view_state.get("target") or "auto"

        try:
            outcome = call_view_function(
                ch,
                address,
                function_name=function_name,
                function_signature=function_signature,
                args=args,
                block_identifier=block_identifier,
                target=target,
                from_address=from_address,
            )
        except Exception as exc:  # noqa: BLE001
            outcome = {
                "ok": False,
                "address": address,
                "function": function_name,
                "signature": function_signature or function_name,
                "mutability": "",
                "block": block_identifier,
                "args": list(args or []),
                "elapsed_seconds": 0.0,
                "error": str(exc),
            }

        entry = {
            "function": outcome.get("function") or function_name,
            "signature": outcome.get("signature", ""),
            "args": outcome.get("args", list(args or [])),
            "block": outcome.get("block", block_identifier),
            "called_at": datetime.now(timezone.utc).isoformat(),
            "ok": bool(outcome.get("ok")),
            "elapsed_seconds": outcome.get("elapsed_seconds", 0.0),
        }
        if outcome.get("ok"):
            entry["result"] = outcome.get("result")
        else:
            entry["error"] = outcome.get("error", "unknown error")

        prior = list(record.view_state.get("call_history") or [])
        new_history = [entry, *prior][:MAX_CALL_HISTORY]
        patch: dict[str, Any] = {"call_history": new_history}
        # patch_view_state deep-merges dicts but replaces lists outright — exactly
        # what we want for call_history.
        mini_apps.patch_view_state(view_id, patch)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=APP_ID,
            title=record.title,
            patch=patch,
        )
        if entry["ok"]:
            summary = (
                f"Called {entry['function']} → "
                f"{str(entry['result'])[:80]}"
            )
        else:
            summary = f"Call to {entry['function']} failed: {entry['error']}"
        return mini_apps.payload_to_call_tool_result(payload, summary_text=summary)


__all__ = [
    "APP_ID",
    "APP_TITLE",
    "RESOURCE_URI",
    "MAX_CALL_HISTORY",
    "register_contract_explorer_tools",
]
