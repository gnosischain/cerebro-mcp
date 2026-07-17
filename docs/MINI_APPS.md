# Cerebro Mini-Apps

Cerebro ships five interactive mini-apps that run inside any MCP-aware host
(Claude Desktop, Claude Code, custom hosts). Each is a React + ECharts UI
built by Vite into a single-file HTML bundle, served by the cerebro-mcp server
over an `ui://cerebro/<app>` resource URI, and driven by MCP tools.

This document is a single-page tour of every app: what it does, how to launch
it, how to develop it, and how it talks to the backend.

---

## Apps at a glance

| App                | Resource URI                       | What it shows                                                | Entry tool                    |
|--------------------|------------------------------------|--------------------------------------------------------------|-------------------------------|
| Report Renderer    | `ui://cerebro/report`              | Interactive analytics reports from `generate_report`         | `generate_report`             |
| Metric Lab         | `ui://cerebro/metric_lab`          | Build a metric from SQL or from the semantic registry        | `open_metric_lab*`            |
| Portfolio (dev-only) | `ui://cerebro/portfolio`         | Address-centric portfolio across Circles / GPay / yields. Registered only when `DEV_MINI_APPS_ENABLED=true`. | `open_portfolio`              |
| Graph Explorer     | `ui://cerebro/graph_explorer`      | Cross-sector semantic graph (Circles trust, Safe, pools, …)  | `open_graph_explorer`         |
| Contract Explorer  | `ui://cerebro/contract_explorer`   | Per-contract: ABI, callable functions, view-call, tx decode  | `open_contract_explorer`      |

All apps share the same plumbing:

1. The tool returns a `MiniAppPayload` with `type: "INITIAL_LOAD"` / `"PATCH_VIEW_STATE"` / `"SHOW_WARNING"`, a set of `datasets`, and app-specific `view_state`.
2. The frontend reads the payload via `useMiniApp` (see [`ui/src/mini-apps/shared/useMiniApp.ts`](../ui/src/mini-apps/shared/useMiniApp.ts)), listens for server-pushed tool results, and can call back into the MCP host with `callServerTool`.
3. Hydration tools (`get_mini_app_rows`, `get_mini_app_state`) are hidden from the model but callable by the frontend for pagination.

---

## Delivery modes & transports

The cerebro-mcp **server only exposes tools** — it never talks to a model
directly. A *host* (Claude Desktop, Claude Code, a custom app) owns an *MCP
client*, and that client speaks the MCP protocol to the server over a
**transport**. The model decides *which* tool to call; the client issues the
call. Mini-apps reach the same tool registry through one of three paths.

### The general picture

```mermaid
flowchart LR
    User([You]) <--> Host[Claude host<br/>e.g. Desktop]
    Host <--> Client[MCP client]
    Client <-->|transport| Server[cerebro-mcp server<br/>tool registry]
    Server <--> CH[(ClickHouse / RPC)]
```

The transport is the only thing that changes between modes — the tool registry
underneath is identical.

### Mode 1 — stdio (Claude Desktop / Claude Code)

Claude Desktop launches the server as a subprocess and talks to it over
stdin/stdout pipes (no `--sse` flag). All MCP tools work, and mini-apps render
**inline inside the host** via the ext-apps bridge. There is **no HTTP
listener**, so the standalone `/app/{id}` browser URLs are *not* served here.

```mermaid
flowchart LR
    subgraph Desktop[Claude Desktop process]
        Model[Model] --> C1[MCP client]
        Webview[Mini-app webview<br/>ext-apps bridge]
    end
    C1 <-->|stdio pipe<br/>MCP JSON-RPC| S1[cerebro-mcp<br/>subprocess]
    Webview <-->|callServerTool<br/>over the bridge| C1
    S1 --> Tools1[(tools)]
```

The mini-app's `useMiniApp.callTool` uses the **ext-apps bridge** (the host
relays the call to its client). No HTTP, no token.

### Mode 2 — standalone web app (`--sse`, plain browser)

Run the server with `--sse` and it starts a real HTTP server (uvicorn +
Starlette). Now `GET /app/{app_id}` serves the single-file bundle with the
initial payload injected, and the frontend POSTs tool calls back to
`/app/{app_id}/api/tool/{tool}`. This path **bypasses the model and the MCP
client entirely** — it's a separate HTTP door into the same tool registry.

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as Starlette routes<br/>(web_apps.py)
    participant T as tool registry
    B->>R: GET /app/portfolio?token=…
    Note over B,R: token in URL — a top-level<br/>navigation can't set headers
    R->>R: _check_auth (Bearer header OR ?token=)
    R->>T: open_portfolio(**query)
    T-->>R: CallToolResult (structuredContent)
    R-->>B: HTML + injected payload<br/>+ window.__MINI_APP_TOKEN__
    B->>R: POST /app/portfolio/api/tool/load_portfolio_address<br/>Authorization: Bearer <token>
    Note over B,R: JS can set headers now —<br/>token no longer in the URL
    R->>T: load_portfolio_address(**args)
    T-->>R: CallToolResult
    R-->>B: { structuredContent, isError, content }
```

**Why the token is in the URL on the first request:** a browser doing a
top-level navigation (address bar or `<a href>` cross-app nav) cannot attach an
`Authorization` header, so `?token=` is the only channel. The server re-injects
it as `window.__MINI_APP_TOKEN__`; every subsequent JS-initiated `POST` sends it
as a `Bearer` header instead, so the token stays out of later URLs. It's the
client echoing a credential it already had — never an escalation. (Serialization
of `structuredContent` mirrors the MCP bridge — Pydantic `mode="json"` — so
`date`/`datetime` values become ISO strings instead of crashing `json.dumps`.)

### Mode 3 — pure-UI dev (Vite, no backend)

`npm run dev` serves every entry HTML with HMR and boots each app into its
`MOCK_PAYLOAD` fixture. No client, no transport, no ClickHouse — `callServerTool`
is unavailable, so "Call"/"Expand"/"Load address" are no-ops. Use it for layout
and styling work only (see [Pure-UI dev loop](#pure-ui-dev-loop-no-mcp-host-no-clickhouse)).

| Mode | Transport | Mini-app delivery | Tool calls | Auth |
|------|-----------|-------------------|------------|------|
| Desktop / Code | stdio pipe | inline webview (ext-apps bridge) | via host's MCP client | none (local subprocess) |
| Standalone web | HTTP/SSE (`--sse`) | `GET /app/{id}` in a browser | `POST /app/{id}/api/tool/{tool}` | `?token=` then `Bearer` header |
| Pure-UI dev | none | Vite dev server | none (mock fixtures) | none |

---

## Running the apps

### Requirements
- Python ≥ 3.11 (see `pyproject.toml`)
- Node ≥ 20 (for building UI bundles)
- An MCP-aware host (Claude Desktop, Claude Code CLI, a custom client)
- `.env` with `CLICKHOUSE_*` credentials for the live backend (not required for UI-only dev)

### One-time install

```bash
# from repo root
make install        # builds ALL UI bundles then pip install -e .
```

Or step-by-step:

```bash
make build-ui       # builds all 5 bundles + copies into src/cerebro_mcp/static/
pip install -e .
```

### Launch inside Claude Desktop

1. Add the server to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

   ```json
   {
     "mcpServers": {
       "cerebro-dev": {
         "command": "/absolute/path/to/python",
         "args": ["-m", "cerebro_mcp.server"],
         "env": { "CLICKHOUSE_URL": "…", "CLICKHOUSE_USER": "…", "CLICKHOUSE_PASSWORD": "…" }
       }
     }
   }
   ```

2. Restart Claude Desktop. Type any of the app entry phrases (e.g. “open portfolio for 0x…”, “show me the graph explorer”). The model calls the corresponding `open_*` tool and the mini-app renders inline.

### Pure-UI dev loop (no MCP host, no ClickHouse)

Each mini-app can be iterated on in isolation with Vite's HMR. The dev server hosts every entry HTML simultaneously — `CEREBRO_UI_ENTRY` only matters at **build** time (it selects which single-file bundle `vite build` emits), not for `npm run dev`.

```bash
cd ui && npm install   # first time only
npm run dev            # Vite on http://localhost:5173/
```

Open any of:

- `http://localhost:5173/`                          — Report Renderer (`index.html`)
- `http://localhost:5173/metric-lab.html`           — Metric Lab
- `http://localhost:5173/portfolio.html`            — Portfolio
- `http://localhost:5173/graph-explorer.html`       — Graph Explorer
- `http://localhost:5173/contract-explorer.html`    — Contract Explorer

Or from the repo root: `make dev`.

**What you get:** every app boots into its `MOCK_PAYLOAD` fixture (defined at the top of each `*App.tsx` — e.g. `ContractExplorerApp.tsx` carries a hardcoded `GnosisControllerToken` view). The layout, styling, navigation, and all client-side view-state work fully.

**What you don't get:** live data. With no MCP host attached, `useMiniApp`'s `callServerTool` is unavailable, so clicking "Call" / "Expand" / "Load address" is a no-op (you'll see `[useMiniApp] callServerTool(...) unavailable (no ext-apps host)` in the browser devtools console). Use this loop for UI work; switch to the Claude Desktop flow above for end-to-end testing against real ClickHouse + RPC.

For Graph Explorer, set `sessionStorage.ge_force_empty = '1'` in the browser console to see the catalog/empty state instead of the seeded mock.

### Rebuilding a single app

```bash
make build-ui-<app>
# e.g.
make build-ui-graph-explorer
make build-ui-portfolio
```

Each per-app target is self-contained: it builds the bundle AND copies it into `src/cerebro_mcp/static/`. After the copy, restart the MCP server (`Ctrl+C` then rerun, or restart Claude Desktop) so the server re-reads the HTML from disk.

> **Gotcha** — the server caches the HTML in-memory on first read (`_BUNDLED_*_HTML`), and the MCP host may cache the resource fetch. If you still see the old UI, kill the host's mini-app tab and reopen it.

---

## Report Renderer

**Resource**: `ui://cerebro/report`  
**Source**: [`ui/src/mini-apps/report/`](../ui/src/mini-apps/report/)  
**Tool entry**: `generate_report` — produces a report doc with `{{chart:ID}}` placeholders.

The report renderer is the "heavy hitter" — it renders a full document with
chart cards, SQL-inspect drawers (`</>`), tables, and verbatim markdown prose.
Charts are rendered via ECharts from specs stored by `generate_chart` /
`generate_charts`.

Related tools: `list_reports`, `open_report`, `export_report`, `generate_chart`, `generate_charts`.

**Workflow** (see [CLAUDE.md](../CLAUDE.md)):

1. `generate_charts` (batch, minimum 3 charts, at least one dimensional breakdown, one correlation-type, one statistical query).
2. `generate_report` assembles the report with placeholders.
3. The returned `file://` URL opens the report in the mini-app.

---

## Metric Lab

**Resource**: `ui://cerebro/metric_lab`  
**Source**: [`ui/src/mini-apps/metric-lab/`](../ui/src/mini-apps/metric-lab/)  
**Tools**: `open_metric_lab`, `open_metric_lab_from_sql`, `open_metric_lab_from_metrics`, `load_metric_lab_metric`, `update_metric_lab_chart`

A two-pane workspace:
- **Left** — metric source: either a raw SQL query, a semantic-registry metric lookup, or a list of ClickHouse models.
- **Right** — live chart + table of the result, with chart spec editable inline.

Used to draft metrics that can then be saved via `save_query` or promoted into a scheduled `generate_chart`.

---

## Portfolio

> **Dev-only.** Portfolio (and Model Lineage) register only when
> `DEV_MINI_APPS_ENABLED=true` — in the default deployment their tools are
> absent, `/app/portfolio` / `/app/model_lineage` 404, and the cross-app
> tabs hide (the chrome filters on the injected `__MINI_APP_APPS__` list).

**Resource**: `ui://cerebro/portfolio`  
**Source**: [`ui/src/mini-apps/portfolio/`](../ui/src/mini-apps/portfolio/)  
**Tools**: `open_portfolio`, `load_portfolio_address`, `load_portfolio_section`, `update_portfolio_focus`, `navigate_portfolio_relation`

Address-centric view that unifies:
- **Overview** — summary card with presence across Circles, GPay, Safe, DeFi
- **Relationships** — graph-like neighborhood (Safe owners, GPay owner events)
- **Yields** — the address's active LP and lending positions
- **GPay** — GPay Safe events, modules, and spender history
- **Circles** — avatar profile, trust relations, CRC balances, wrappers

Internally uses the same address-roles resolver that Graph Explorer does (see `int_execution_address_roles_current` in dbt-cerebro). Navigation between sections is `PATCH_VIEW_STATE`-only; datasets load on demand.

---

## Contract Explorer

**Resource**: `ui://cerebro/contract_explorer`  
**Source**: [`ui/src/mini-apps/contract-explorer/`](../ui/src/mini-apps/contract-explorer/)  
**Tools**: `open_contract_explorer`, `load_contract_explorer_address`, `contract_explorer_call_function`

Per-contract inspection backed by direct JSON-RPC reads (not dbt). Workflow:

1. Resolve ABI for the address via the local catalog → Sourcify → 4byte selector fallback. Proxies (EIP-1967, transparent, minimal) are followed automatically to the implementation.
2. Render the callable function list (view/pure only — state-changing functions are rejected at the tool layer).
3. Click a function, fill its args, fire `contract_explorer_call_function`. Result is the decoded return value(s) at `block_identifier="latest"` by default.

Use this for **single-address current state**: `balanceOf`, `totalSupply`, `owner`, `paused`, `allowance`, etc. For multi-address sweeps, historical balances, USD valuation, or aggregations, use the dbt path (`execute_query` over `fct_*_balances` etc.).

Standalone (non-mini-app) RPC tools — `contract_explore`, `contract_call_function`, `contract_decode_transaction_input`, `contract_decode_receipt_logs` — live in [`tools/rpc.py`](../src/cerebro_mcp/tools/rpc.py) and use the same `call_view_function` engine. Archive reads (non-`latest` block) require `GNOSIS_ARCHIVE_RPC_URL` in `.env`.

---

## Graph Explorer

**Resource**: `ui://cerebro/graph_explorer`  
**Source**: [`ui/src/mini-apps/graph-explorer/`](../ui/src/mini-apps/graph-explorer/) (frontend) · [`src/cerebro_mcp/tools/semantic/graph_explorer/`](../src/cerebro_mcp/tools/semantic/graph_explorer/) (backend package)  
**Agent tools**: `open_graph_explorer`, `load_graph_explorer_seed`, `expand_graph_explorer_node`, `update_graph_explorer_focus`  
**App-only tools** (hidden from the model): `load_graph_atlas_sample`, `set_graph_explorer_view`

Cross-sector graph rendered with **cosmos.gl** (WebGL, `@cosmos.gl/graph`). Every
graph-capable semantic model in `dbt-cerebro` contributes a **profile** (see
`cerebro.graph` meta). Nodes come from the semantic registry; edges are fetched
at query time.

### Two MODES, one canvas

A segmented switch in the header flips between two modes; each keeps its own
dataset pair (`nodes`/`edges` vs `atlas_nodes`/`atlas_edges`) so switching is
instant and lossless.

1. **Atlas** (default) — browse the profile catalog grouped by sector in a
   left rail. Checking profiles loads top-weight *sample* subgraphs
   (REPLACE semantics — deselecting a profile removes its edges; sample size
   is per profile). Clicking a sampled node offers **"Investigate → "** which
   promotes it into a real seed. An address input at the top seeds directly.
2. **Investigate** — seed an address/entity → bounded 1-hop subgraph with
   role auto-detection via `int_execution_address_roles_current` → expand
   hop-by-hop (explicit action only; default depth 1, stepper up to the
   session cap) → inspect role badges, edge evidence, and suggested
   cross-sector next hops in the details panel.

### view_state v2 (server is the source of truth)

```
{ mode: "atlas"|"investigate", catalog, limits: {...},
  atlas: { selected_profiles, sample_size, window_days },
  investigate: { seed, active_profiles, window_days, max_neighbors, hops_used },
  selection: { node_id, edge_id },     # cleared on mode switch
  layout, semantic_status_filter, node_roles, suggested_next_hops,
  warnings, dataset_revisions }
```

`limits` publishes the server constants (`max_hops`, `bfs_node_cap`,
`default_expand_depth`, `ui_default_window_days`, `ui_default_max_neighbors`,
`atlas_sample_size`) — the frontend derives every control from it and keeps
no compile-time mirrors.

### What the controls do

| Control                     | Effect                                                                                       |
|-----------------------------|----------------------------------------------------------------------------------------------|
| `Atlas / Investigate`       | Mode switch — swaps which dataset pair the canvas renders; clears selection; never refetches. |
| Atlas profile checkbox      | Debounced `load_graph_atlas_sample` with the full selection (REPLACE semantics).             |
| Window / max pills          | Debounced refetch of the investigate subgraph.                                               |
| `Force / Circle` segmented  | Client-side layout change. No backend call.                                                  |
| `All / ● / ●` status        | Filters profiles by quality tier.                                                            |
| Chip click (profile strip)  | **Adds**: refetch with the profile mixed in. **Removes**: client-side filter only.           |
| Depth stepper + `+ Expand`  | Expands the selected node (else the seed) by the stepped depth (default **1**).              |
| Node click                  | Selects; server refreshes role badges + evidence + suggested next hops.                      |
| Node double-click           | Expands that node by the stepped depth.                                                      |
| Play/Pause · Fit · Recenter · Focus · Labels · Legend | Canvas-local controls (ephemeral — reset on reload).               |
| Details → Expand / Recenter / Copy | Same as before (Recenter reseeds from the selected node).                             |

There is **no "Ask" button** — it was removed at user request (WS10). The app
publishes passive model context (`mode`, seed, selection, profile set, node/
edge counts, hops) via `updateModelContext` so the agent can see the view.

### Profiles (as of initial release)

| Profile                        | Model                                                      | Source → Target          | Quality   |
|--------------------------------|------------------------------------------------------------|--------------------------|-----------|
| `circles_trust`                | `api_execution_circles_v2_trust_relations_current`         | circles_avatar → circles_avatar | approved |
| `circles_trust_history`        | `api_execution_circles_v2_avatar_trust_relations`          | circles_avatar → circles_avatar | candidate |
| `circles_avatar_balances`      | `fct_execution_circles_v2_avatar_balances_latest`          | circles_avatar → token   | approved |
| `safe_ownership`               | `int_execution_safes_current_owners`                       | address → safe           | candidate |
| `gpay_ownership`               | `int_execution_gpay_wallet_owners`                         | address → gpay_wallet    | candidate |
| `token_transfers`              | `int_execution_transfers_whitelisted_daily`                | address → address        | candidate |
| `lp_in_pool`                   | `int_execution_pools_dex_liquidity_events`                 | address → pool           | approved |
| `pool_contains_token`          | `int_execution_pools_dex_liquidity_events`                 | pool → token             | approved |
| `lending_user_to_reserve`      | `fct_execution_yields_user_lending_positions_latest`       | address → token          | approved |
| `validator_controlled_by`      | `int_consensus_validators_withdrawal_addresses`            | address → validator      | approved |
| `deposit_to_validator`         | `int_GBCDeposit_deposists_daily`                           | address → validator      | approved |
| `bridge_user_flows`            | `int_execution_bridges_address_flows_daily`                | address → bridge         | candidate |
| `address_labeled_as`           | `stg_crawlers_data__dune_labels`                           | address → project_label  | approved |

### Supporting dbt models

- `int_execution_address_roles_current` — one row per address with role flags (is_safe, is_gpay_wallet, is_circles_avatar, …). Powers auto-detection on seed and the role badges in the details panel.
- `int_consensus_validators_withdrawal_addresses` — derives the controlling EVM address from `withdrawal_credentials` (trailing 20 bytes of 0x01-type credentials).
- `int_execution_bridges_address_flows_daily` — address-grain bridge flows (joins whitelisted transfers with dune bridge labels).

### Cross-sector relationships

Authored in `semantic/relationships/execution_graph.yml` (dbt-cerebro). These drive the "suggested next hops" chips in the details panel — when a node is selected, the registry's relationship graph proposes which cross-sector profile to overlay next.

Key cross-sector edges:
- `gpay_wallet_is_safe` — GPay Safes are Safes (type-tag).
- `ga_user_controls_gpay` — **Canonical** EOA → GP Safe binding via Delay Module. Do NOT use `safes_current_owners.owner` (returns sentinel `0x…0002`).
- `circles_avatar_is_address` — avatars are first-class addresses.
- `deposit_to_validator_identity` — `withdrawal_credentials` joins GBCDeposit and consensus.
- `validator_address_is_safe` — the 20-byte withdrawal address is often a Safe.
- `address_labeled_as_project` — universal enrichment via Dune labels.

### Knobs

All in [`src/cerebro_mcp/tools/semantic/graph_explorer/constants.py`](../src/cerebro_mcp/tools/semantic/graph_explorer/constants.py) and published to the UI via `view_state["limits"]`:

- `MAX_HOPS = 50` — session hop cap (deliberately raised in WS9, live-QA'd in WS10).
- `DEFAULT_EXPAND_DEPTH = 1` — UI default hops per expand action.
- `UI_DEFAULT_WINDOW_DAYS = 90` / `UI_DEFAULT_MAX_NEIGHBORS = 100` — UI load defaults.
- `DEFAULT_ATLAS_SAMPLE = 150` — Atlas sample size per profile.
- `DEFAULT_WINDOW_DAYS = 365` / `DEFAULT_MAX_NEIGHBORS = 250` — the PUBLIC
  `explore_neighborhood`/`calculate_flow_efficiency` tool defaults (a pinned
  contract; distinct from the UI defaults on purpose).
- BFS caps `GRAPH_EXPLORER_BFS_NODE_CAP` (15000) / `GRAPH_EXPLORER_BFS_PER_HOP_BUDGET` (3000) — env-tunable settings.

---

## Adding a new mini-app

A mini-app has five files on the backend and about six on the frontend. Use `graph_explorer` or `portfolio` as the canonical template.

### Backend

1. `src/cerebro_mcp/tools/<app>.py`
   - Define `APP_ID`, `URI`, `DEFAULT_TITLE`, and a `register_<app>_tools(mcp, ch)` function.
   - In `register_*_tools`:
     - `mini_apps.register_app(APP_ID, title=..., resource_uri=URI)`
     - `@mcp.resource(URI, mime_type="text/html;profile=mcp-app")` → return the bundled HTML via `importlib.resources`.
     - `@mcp.tool(meta={"ui": {"resourceUri": URI}})` for each interaction. `open_*` returns `INITIAL_LOAD`; `update_*_focus` returns `PATCH_VIEW_STATE`.
   - Use `mini_apps.run_structured_query` or `mini_apps.load_bounded_dataset` for ClickHouse queries — they handle caching and dataset-mode rules.
2. `src/cerebro_mcp/server.py` — call `register_<app>_tools(mcp, ch)`.
3. `src/cerebro_mcp/security.py` — classify every new tool as `_RO` (read-only).
4. `src/cerebro_mcp/tools/metadata.py` — add the tools to the manual tool table.
5. `tests/test_mini_app_visibility.py` — extend the visibility assertions.

### Frontend

6. `ui/<app>.html` + `ui/src/<app>-main.tsx` — Vite entry points.
7. `ui/src/mini-apps/<app>/<App>App.tsx` — root component using `useMiniApp`.
8. Support components (`FilterBar`, `DetailsPanel`, etc.) and a scoped CSS file.
9. `ui/vite.config.ts` — add to `ENTRY_MAP`.
10. `Makefile` — add `build-ui-<app>` target (build + copy, following the pattern).

### Visibility + tests

```bash
pytest tests/test_mini_app_visibility.py tests/test_<app>.py -v
```

---

## Further reading

- [`CLAUDE.md`](../CLAUDE.md) — project-level agent instructions.
- [`docs/security.md`](security.md) — tool classification rules and SQL safety.
- [`docs/observability.md`](observability.md) — metrics and logging for the MCP server.
- [`dbt-cerebro`](https://github.com/gnosis-org/dbt-cerebro) — where the semantic registry and graph metadata live.
