# Mini-Apps UX Pass — WS10 Implementation & QA Log

> Status: **implemented + live-verified, uncommitted.** Nothing was committed or pushed.
> Date: 2026-06-02 · Server: local SSE on port 8000 · Verified in Claude-in-Chrome (light + dark).

This document compiles the full WS10 thread: the user's reported issues, the
decisions taken, the code changes made across backend and frontend, the build /
restart steps, and the live in-browser QA results.

---

## 1. Origin — user request

Live QA on the mini-apps surfaced six issues (paraphrased from the user, with screenshot):

1. **Model Lineage:** double-clicking a *source* node errors with
   "Heads up — Model 'dune_bridge_flows' not found". "Source is the start of all."
2. **Graph Explorer:** no visibility into a model's **schema or SQL**.
3. **Graph Explorer:** canvas is **cut off on the right**, worse in the desktop app
   (needs better panning).
4. **Graph Explorer:** the network **sim is slow / inconsistent and stops**;
   evolution "is just not good."
5. **Graph Explorer:** remove the **"Ask" button**.
6. **Graph Explorer:** **hop navigation is unintuitive** — hard to go over more hops.

The user also asked to "deploy agents to explore all these via actual visual
inspection and navigation."

---

## 2. Root causes (confirmed in code)

| # | Issue | Root cause |
|---|-------|-----------|
| 1 | Source double-click error | `expand_model_lineage_node` did `seed_name = node_id.split(".")[-1]` then `get_subgraph` → `_resolve_unique_id` only searches model names, never `_sources` (keyed `"schema.name"`), so sources returned `{"error": "Model '…' not found"}`. |
| 2 | No schema/SQL | `NODES_COLUMNS` omitted columns/SQL; `manifest.get_model_details` already exposes `raw_code`/`compiled_code`/columns — just not surfaced. |
| 3 | Canvas cutoff | Cosmos sizes to its container but only re-measures on explicit `render()`/`fitView()`; **no `ResizeObserver`**, so toggling the `.ge-body` grid (`1fr 320px` ↔ `1fr 0`) or resizing kept the stale WebGL width. |
| 4 | Sim stops | A hard `window.setTimeout(() => graph.pause(), 9000)` froze the sim mid-evolution (alpha still high). |
| 5 | Ask button | `FilterBar` Ask `<button>` + `onAskAssistant` prop + `GraphExplorerApp` handler. |
| 6 | Hop nav | Hop pill auto-expanded the seed after a silent 600ms debounce; double-click and neighbor "+" hardcoded `hops=1` against arbitrary nodes. |

---

## 3. User decisions (resolved via AskUserQuestion)

- **Sources:** *Details only, no expand* — a source double-click selects + shows
  metadata; suppress the error.
- **Sim:** *Settle + play/pause control* — snappy natural settle by default, plus an
  explicit play/pause to re-energize and watch it evolve.
- **Hops:** *Both combined* — hop count + expand button + double-click + neighbor "+"
  all operate on the **selected node** (fallback to seed); **remove** the 600ms silent
  auto-expand; require an explicit click.

---

## 4. Changes implemented

### A — Lineage source double-click → details only
- `src/cerebro_mcp/tools/analytics/model_lineage_app.py` — `expand_model_lineage_node`
  guards `node_id.startswith("source.")` **before** seed-name computation: patches
  `selected_node_id`, clears warnings, returns the unchanged graph as a selection.
- `ui/src/mini-apps/model-lineage/LineageGraph.tsx` — `onNodeDoubleClick` routes a
  `kind === "source"` node to `onSelectNode` instead of `onExpandNode`.

### B — Schema + SQL in lineage details
- `src/cerebro_mcp/loaders/manifest.py` — `_subgraph_node` now attaches `columns`
  (`name`/`data_type`/`description`), `raw_sql` (`raw_code`), `compiled_sql`
  (`compiled_code`); sources get schema/description/columns from `self._sources`.
- `model_lineage_app.py` — `NODES_COLUMNS` + `_node_to_row` + `_semantic_node` extended
  with `columns`, `raw_sql`, `compiled_sql`.
- `ui/src/mini-apps/model-lineage/types.ts` — `ColumnSchema` + `columns`/`rawSql`/`compiledSql`
  on `ModelNodeData`; `parseNodeRow` reads the new columns.
- `ui/src/mini-apps/model-lineage/DetailsPanel.tsx` — collapsible **Schema** table
  (name · type · description) and **SQL** block with Raw/Compiled toggle (monospace,
  horizontal scroll); sources show "No SQL — source table." Hides "Expand neighbours"
  for sources.
- `ui/src/mini-apps/model-lineage/model-lineage.css` — token-based section/table/SQL styling.

### C — Graph Explorer panning / resize
- `ui/src/mini-apps/graph-explorer/CosmosGraph.tsx` — `ResizeObserver` on the Cosmos
  container's parent; rAF-debounced → `graph.render(); graph.fitView(300); updateLabels()`;
  cleaned up on unmount.

### D — Sim snappy settle + play/pause
- `CosmosGraph.tsx` — retuned forces (`simulationFriction:0.9`, `gravity:0.08`,
  `repulsion:1.6`, `repulsionTheta:1.15`, `linkSpring:0.5`, `linkDistance:60`,
  `decay:4000`); **removed** the 9000ms hard `pause()`; natural settle ends via
  `onSimulationEnd` (final `fitView` + label retrack). Added `simRunning` state driven by
  `onSimulationStart/Pause/Unpause/End`, a `toggleSim` handler, and a **play/pause** button.

### E — Remove the Ask button
- `FilterBar.tsx` — deleted the Ask `<button>` and the `onAskAssistant` prop.
- `GraphExplorerApp.tsx` — removed the `onAskAssistant` handler and its pass-through;
  dropped now-unused `updateModelContext`/`sendMessage`.

### F — Hop navigation: selected-node-centric, explicit
- `GraphExplorerApp.tsx` — `onExpand(nodeId, hops?)` clamps to `MAX_HOPS`;
  `onExpandTarget` expands `selected_node_id ?? seed_node.id`; `onBfsHopsChange` only
  updates the count (no debounce/auto-expand).
- `FilterBar.tsx` — `expandTarget`/`canExpand` derivations; the `+` icon became a
  labeled **"+ Expand selected node / seed"** button (disabled when no target).
- `graph-explorer.css` — `.ge-btn[disabled]` + `.ge-expand-btn` styling.

---

## 5. Build & restart

```
pytest tests/test_graph_explorer.py tests/test_lineage_graph.py tests/test_manifest_loader.py
  → 66 passed, 4 skipped

make build-ui-graph-explorer   # → src/cerebro_mcp/static/graph_explorer.html
make build-ui-model-lineage    # → src/cerebro_mcp/static/model_lineage.html

# restart (server caches the bundle at startup)
ALLOW_INSECURE_REMOTE_TRANSPORT=true python -m cerebro_mcp.server --sse   # port 8000
```

> Note: the `mcp__cerebro-dev__*` tools in the session connect to a *separate, stale*
> MCP server instance. All live QA below was done against the freshly-restarted
> port-8000 SSE server that serves `/app/{id}`.

---

## 6. Live QA results (port 8000, light + dark)

| Item | Result |
|------|--------|
| **A** — Source double-click | Double-clicked the `dune_bridge_flows` source → **no error toast**; details show KIND=source, SCHEMA=crawlers_data, "No SQL — source table." Console clean. |
| **B** — Schema/SQL | `int_bridges_flows_daily` shows a 10-col **Schema** table (date·Date, bridge·String, …, txs·UInt64) and the **SQL** block (`{{ config(...) }}` raw); **Compiled** correctly disabled when absent. |
| **C** — Panning/cutoff | Toggling the details panel flips the grid to full-width and the `ResizeObserver` re-fits the graph centered — **no right-edge cutoff**, both directions. |
| **D** — Sim + play/pause | Sim settles naturally (no mid-motion freeze); **Pause ↔ Play** toggles re-energize/pause correctly. |
| **E** — Ask button | **Gone** from the controls row (Fit · Recenter · Focus · Play · LABELS). |
| **F** — Hops | Selecting a node relabels "Expand seed" → "Expand selected node"; selection alone does **not** auto-expand; double-clicking the seed deepened **251 → 500 nodes, hop 1/50 → 4/50** (`MAX_HOPS=50`); the "BFS reached the 15000-node cap after hop 3" notice is the raised WS9 cap working. |

Both lineage and graph-explorer were legible in **light and dark** themes; console
clean on both apps.

---

## 7. Files touched (WS10)

**Backend**
- `src/cerebro_mcp/loaders/manifest.py`
- `src/cerebro_mcp/tools/analytics/model_lineage_app.py`

**Frontend — model-lineage**
- `ui/src/mini-apps/model-lineage/types.ts`
- `ui/src/mini-apps/model-lineage/DetailsPanel.tsx`
- `ui/src/mini-apps/model-lineage/LineageGraph.tsx`
- `ui/src/mini-apps/model-lineage/model-lineage.css`

**Frontend — graph-explorer**
- `ui/src/mini-apps/graph-explorer/CosmosGraph.tsx`
- `ui/src/mini-apps/graph-explorer/FilterBar.tsx`
- `ui/src/mini-apps/graph-explorer/GraphExplorerApp.tsx`
- `ui/src/mini-apps/graph-explorer/graph-explorer.css`

**Built bundles**
- `src/cerebro_mcp/static/graph_explorer.html`
- `src/cerebro_mcp/static/model_lineage.html`

---

## 8. Prior context (superseded workstreams)

WS10 sits on top of earlier passes, all previously shipped/verified:

- **WS1** Help dialog wired into chrome.
- **WS2** Light-mode token foundation + per-app conversion.
- **WS3** Portfolio width-safe tables + overlay DB fix (`database="dbt"`).
- **WS4** Lineage node dragging (`useNodesState`/`useEdgesState`).
- **WS5** Graph Explorer compact strip, `MAX_HOPS`, refetch depth preservation.
- **WS6** Shared `Ma*` primitives + migrate portfolio / contract-explorer / metric-lab.
- **WS7** Graph Explorer rebuilt on **Cosmos GL**; backend BFS cap fix; `circles_invitation` profile.
- **WS8** Cosmos live-QA fixes (stale-closure interactions, sizing, layout, labels, arrows, seed UX).
- **WS9** token_transfers control-key crash fix, sample-mode menu, hover tooltips,
  layout/vertex restyle, raised node/hop limits (`MAX_HOPS=50`, cap 15000), edge-type chip clarity.

---

## 9. Binding constraint

**NEVER PUSH.** All WS10 changes are left uncommitted on the working tree (last commit
`33d8168 feat: lineage mini-app` unchanged) for the user to test before any commit/push.
