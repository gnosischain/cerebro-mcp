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
      "chain_id":               100,
      "chain_name":             "Gnosis",
      "chain_options":  [{chain_id, name, native_symbol, icon_url,
                          explorer: {...}}, ...],  # configured chains only
      "explorer":       {provider, brand, base_url, ...},
      "contract_name":          "GnosisControllerToken",
      "abi_source":             "clickhouse",      # | "blockscout" | "sourcify"
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
      "history":         [{signature, range_label, from_block, to_block,
                           decimals, points: [...], warnings}, ...],
      "warnings":        [str, ...],
    }

``contract_explorer_call_function`` emits a ``PATCH_VIEW_STATE`` whose
``patch`` contains the new ``call_history`` slice (capped at
``MAX_CALL_HISTORY``); ``contract_explorer_read_history`` does the same for
``history`` (capped at ``MAX_HISTORY_SERIES``).

Both are LISTS, not maps keyed by signature: ``patch_view_state`` deep-merges
dicts, so a map would accumulate every sweep forever with no way to evict —
lists are replaced wholesale, which is what a capped ring needs.
"""
from __future__ import annotations

import importlib.resources
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.chains import (
    GNOSIS_CHAIN_ID,
    NATIVE_ICON_URLS,
    ChainInfo,
    configured_chains,
    get_chain,
    has_rpc,
    resolve_chain,
    rpc_env_hint,
)
from cerebro_mcp.clients.abi_resolver import resolve_abi
from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.clients.contract_history import read_function_history
from cerebro_mcp.models.mini_app import MiniAppPayload, SummaryCard
from cerebro_mcp.tools.visualization import mini_apps, web_apps
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

#: Cap on retained history series. Each carries up to CONTRACT_HISTORY_MAX_POINTS
#: points, so this is the bigger payload risk of the two.
MAX_HISTORY_SERIES = 5


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


def _chain_dict(chain: ChainInfo) -> dict[str, Any]:
    return {
        "chain_id": chain.chain_id,
        "name": chain.name,
        "native_symbol": chain.native_symbol,
        "environment": chain.environment,
        "explorer": asdict(chain.explorer),
        "icon_url": NATIVE_ICON_URLS.get(chain.chain_id, ""),
    }


def _chain_options() -> list[dict[str, Any]]:
    """Only chains with an RPC configured — the selector must not offer a
    chain that would fail the moment the user picks it."""
    return [_chain_dict(c) for c in configured_chains()]


def _empty_view_state(chain_id: int = GNOSIS_CHAIN_ID) -> dict[str, Any]:
    chain = get_chain(chain_id)
    return {
        "address": "",
        "chain_id": chain.chain_id,
        "chain_name": chain.name,
        "chain_options": _chain_options(),
        "explorer": asdict(chain.explorer),
        "contract_name": "",
        "abi_source": "",
        "implementation_address": "",
        "target": "auto",
        "read_functions": [],
        "write_functions": [],
        "events": [],
        "call_history": [],
        "history": [],
        "warnings": [],
    }


def _build_view_state(
    *,
    record_address: str,
    record,  # AbiRecord
    target: str,
    projected: dict[str, list[dict[str, Any]]],
    chain_id: int = GNOSIS_CHAIN_ID,
) -> dict[str, Any]:
    chain = get_chain(chain_id)
    return {
        "address": record_address,
        "chain_id": chain.chain_id,
        "chain_name": chain.name,
        "chain_options": _chain_options(),
        "explorer": asdict(chain.explorer),
        "contract_name": record.contract_name,
        "abi_source": record.source,
        "implementation_address": record.implementation_address,
        "target": target,
        "read_functions": projected["read_functions"],
        "write_functions": projected["write_functions"],
        "events": projected["events"],
        "call_history": [],
        "history": [],
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

    def _resolve_chain_or_error(chain: str | int):
        """Resolve a chain and confirm it is usable. Returns (chain, error)."""
        try:
            chain_info = resolve_chain(chain)
        except ValueError as exc:
            return None, str(exc)
        if not has_rpc(chain_info.chain_id):
            return None, (
                f"{chain_info.name} (chain {chain_info.chain_id}) has no RPC "
                f"endpoint configured. Set {rpc_env_hint(chain_info.chain_id)}."
            )
        return chain_info, None

    def _resolve_and_build(address: str, target: str, chain_id: int):
        """Resolve ABI + project it. Returns ``(view_state, error_message)``."""
        try:
            from web3 import Web3
            checksum = Web3.to_checksum_address(address)
        except Exception as exc:  # noqa: BLE001
            return None, f"Bad address: {exc}"
        try:
            record = resolve_abi(ch, checksum, target=target, chain_id=chain_id)
        except Exception as exc:  # noqa: BLE001
            return None, f"{exc}"
        projected = _project_abi(record.abi)
        view_state = _build_view_state(
            record_address=checksum,
            record=record,
            target=target,
            projected=projected,
            chain_id=chain_id,
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
        chain: str = "",
    ) -> CallToolResult:
        """Launch the Contract Explorer — an Etherscan-style read-only contract page.

        Use this when the user says any of:
          - "launch contract explorer", "open contract explorer", "open contract page for X"
          - "let me poke at / inspect / explore contract X"
          - "show me what functions contract X has"
          - pastes a bare contract address and asks what it does
          - wants to repeatedly query a single contract through a UI

        Works across every chain with an ``RPC_URL_*`` configured — pass
        ``chain`` as a name, key, or id ("mainnet", "base", 42161); defaults to
        Gnosis. The in-app selector lists the configured chains.

        The mini-app resolves the ABI from ``dbt.contracts_abi`` (Gnosis only),
        then that chain's Blockscout, then Sourcify, automatically following
        proxies (target="auto" returns the implementation ABI by default —
        pass target="proxy" only when the user explicitly wants the proxy's own
        ABI). Lists every view/pure function as a card with input forms; users
        click "Call" to read it, or "History" to plot it over a block range.

        With an empty ``address``, opens an empty explorer that prompts the
        user to paste a contract address.

        For ABI-level read/write inspection of arbitrary EVM contracts.
        """
        title = title.strip() or APP_TITLE

        chain_info, chain_err = _resolve_chain_or_error(chain)
        if chain_err is not None:
            payload = MiniAppPayload(
                type="SHOW_WARNING", view_id="", app_id=APP_ID, title=title,
                status="error", warnings=[chain_err],
            )
            return mini_apps.payload_to_call_tool_result(payload, summary_text=chain_err)

        if not address.strip():
            view_id = mini_apps.create_view(APP_ID, title)
            view_state = _empty_view_state(chain_info.chain_id)
            payload = _initial_load_payload(view_id, title, view_state)
            return mini_apps.payload_to_call_tool_result(
                payload,
                summary_text="Contract Explorer ready — paste a contract address.",
            )

        view_state, err = _resolve_and_build(address, target, chain_info.chain_id)
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
                f"({_short_address(view_state['address'])}) on "
                f"{chain_info.name} — "
                f"{len(view_state['read_functions'])} read function(s)."
            ),
        )

    @mcp.tool(meta=APP_META)
    def load_contract_explorer_address(
        view_id: str,
        address: str,
        target: str = "auto",
        chain: str = "",
    ) -> CallToolResult:
        """Swap to a different contract (or chain) inside an open Contract Explorer view.

        Use when the user wants to look at a different contract without losing
        the current explorer tab. Reuses the existing ``view_id``; replaces
        ``view_state`` and emits a fresh ``INITIAL_LOAD`` payload so the
        frontend can do a clean swap. Omitting ``chain`` keeps the view's
        current chain.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        # Empty chain means "keep the current one", not "reset to Gnosis".
        chain_info, chain_err = _resolve_chain_or_error(
            chain or record.view_state.get("chain_id") or GNOSIS_CHAIN_ID
        )
        if chain_err is not None:
            return mini_apps.error_call_tool_result(chain_err)

        view_state, err = _resolve_and_build(address, target, chain_info.chain_id)
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
                f"({_short_address(view_state['address'])}) on {chain_info.name}."
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
        chain_id = int(record.view_state.get("chain_id") or GNOSIS_CHAIN_ID)

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
                chain_id=chain_id,
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

    @mcp.tool(meta=APP_META)
    def contract_explorer_read_history(
        view_id: str,
        function_name: str = "",
        function_signature: str = "",
        args: list[Any] | None = None,
        since: str = "30d",
        until: str = "",
        from_block: int | str = "",
        to_block: int | str = "",
        points: int = 0,
        output_index: int = 0,
        decimals: int = 0,
    ) -> CallToolResult:
        """Plot one view function's value across a block range in the open explorer.

        Use when the user, looking at a Contract Explorer view, asks how a
        value changed over time ("chart totalSupply over the last 90 days",
        "graph this balance", "when did it start dropping"). The resulting
        series is appended to the view's ``history`` and emitted as a
        ``PATCH_VIEW_STATE`` so the mini-app draws the line chart inline.

        For a chat-only answer with no open view, use ``contract_read_history``.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        address = record.view_state.get("address") or ""
        if not address:
            return mini_apps.error_call_tool_result(
                "Explorer view has no address yet — open with an address first."
            )
        chain_id = int(record.view_state.get("chain_id") or GNOSIS_CHAIN_ID)

        try:
            outcome = read_function_history(
                ch,
                address,
                chain_id=chain_id,
                function_name=function_name,
                function_signature=function_signature,
                args=args,
                from_block=from_block or None,
                to_block=to_block or None,
                since=since,
                until=until,
                points=points,
                target=record.view_state.get("target") or "auto",
                output_index=output_index,
                decimals=decimals or None,
            )
        except Exception as exc:  # noqa: BLE001
            return mini_apps.error_call_tool_result(f"History sweep failed: {exc}")

        series = {
            "signature": outcome["signature"],
            "range_label": since or f"{outcome['from_block']}–{outcome['to_block']}",
            "from_block": outcome["from_block"],
            "to_block": outcome["to_block"],
            "output_index": outcome["output_index"],
            "decimals": outcome["decimals"],
            "output_types": outcome["output_types"],
            "points": outcome["points"],
            "ok_count": outcome["ok_count"],
            "truncated": outcome["truncated"],
            "warnings": outcome["warnings"],
            "swept_at": datetime.now(timezone.utc).isoformat(),
        }

        # Replace any prior sweep of the same signature, then cap — the whole
        # view_state ships on INITIAL_LOAD and each series carries up to
        # CONTRACT_HISTORY_MAX_POINTS entries.
        prior = [
            s for s in (record.view_state.get("history") or [])
            if s.get("signature") != series["signature"]
        ]
        patch: dict[str, Any] = {"history": [series, *prior][:MAX_HISTORY_SERIES]}
        mini_apps.patch_view_state(view_id, patch)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=APP_ID,
            title=record.title,
            patch=patch,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Swept {series['signature']} over blocks "
                f"{series['from_block']:,}–{series['to_block']:,} — "
                f"{series['ok_count']}/{len(series['points'])} samples ok."
            ),
        )

    web_apps.register_web_app(
        app_id=APP_ID,
        open_tool="open_contract_explorer",
        html_loader=get_contract_explorer_html,
        title="Contract Explorer",
        description=(
        "Inspect any contract: resolved ABI (proxy-aware), read functions live over RPC, and decode transaction inputs and receipt logs."
        ),
        icon="◎",
        tools={
            "open_contract_explorer": open_contract_explorer,
            "load_contract_explorer_address": load_contract_explorer_address,
            "contract_explorer_call_function": contract_explorer_call_function,
            "contract_explorer_read_history": contract_explorer_read_history,
        },
    )


__all__ = [
    "APP_ID",
    "APP_TITLE",
    "RESOURCE_URI",
    "MAX_CALL_HISTORY",
    "MAX_HISTORY_SERIES",
    "register_contract_explorer_tools",
]
