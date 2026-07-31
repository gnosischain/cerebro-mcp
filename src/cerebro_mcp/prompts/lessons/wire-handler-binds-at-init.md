---
id: wire-handler-binds-at-init
title: Assigning mcp.list_tools after construction never reaches the wire — FastMCP binds handlers in __init__
status: observed
layer: mcp-tool
scope: any wrapper installed by assigning an attribute on an already-constructed FastMCP
symptom: 'a visibility filter or wrapper passes every test yet changes nothing for real clients; tools/list serves the unfiltered registry'
last_verified: 2026-07-31
evidence:
  - src/cerebro_mcp/tools/visualization/mini_apps.py:1438
  - src/cerebro_mcp/runtime/mcp_server.py:1
  - tests/test_tool_list_wire_surface.py:63
  - "pending deploy: fix exists in the working tree only (CerebroFastMCP subclass + wire tests)"
---
## Symptom

A `list_tools` filter passes every test while every real client receives the
unfiltered registry. Measured: `tools/list` served **187 tools / ~248 KB /
~62k tokens, byte-identical with the filter installed or not** — including
the 27 app-only mini-app hydration tools that were never meant to be
model-facing, and `LEAN_CORE_ENABLED` was a complete no-op on the wire.

## Root cause

`FastMCP.__init__` calls `_setup_handlers()`, which registers the **bound
method** `self.list_tools` into the low-level `tools/list` request handler
(SDK `fastmcp/server.py:239 -> :302-304`). A later
`mcp.list_tools = wrapper` assignment rebinds only the instance attribute;
the handler keeps the original bound method captured at construction. Tests
that call `mcp.list_tools()` exercise the attribute and pass; clients hit
the handler and see everything.

Same shape as [[negated-grep-passes-when-tool-absent]] and the
`settings`-not-imported incident behind `make lint-undefined`: the guard
exists, is tested, and is not in the executed path.

## Forbidden action

Installing any wire-facing behavior by assigning over a FastMCP method
(`mcp.list_tools = ...`, `mcp.call_tool = ...`) after construction.

## Detection

A test that drives `mcp._mcp_server.request_handlers[types.ListToolsRequest]`
— the wire path — and asserts the filtered property. Asserting on
`mcp.list_tools()` detects nothing.

## Safe remediation

Override the methods on a subclass (`CerebroFastMCP`,
`src/cerebro_mcp/runtime/mcp_server.py`) so the methods bound at `__init__`
ARE the filtered ones, and construct that subclass everywhere — including
test fixtures. `install_app_only_filter` now refuses a plain `FastMCP` with
a `TypeError` rather than silently degrading into the dead-wrapper behavior.

## Enforcement

`tests/test_tool_list_wire_surface.py` asserts the app-only drop and the
connector-profile allowlist on the low-level handler, that excluded tools
are rejected at invocation (listing is not a capability boundary), and that
`install_app_only_filter` raises on a plain `FastMCP`.
