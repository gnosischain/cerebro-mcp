// Graph Explorer shell — two modes, one canvas.
//
//   ATLAS        browse the semantic profile catalog; sample profile unions
//                (atlas_nodes/atlas_edges, REPLACE semantics).
//   INVESTIGATE  bounded subgraph around a seed (nodes/edges) with explicit
//                BFS expansion.
//
// Both dataset pairs stay attached to the view; switching modes only flips
// which pair the canvas renders — it never refetches. Local state lives in
// the graphReducer (adopted from view_state v2, bulk-synced back via
// set_graph_explorer_view); selection changes additionally go through
// update_graph_explorer_focus so the server refreshes evidence + roles.

import { useEffect, useMemo, useRef } from "react";
import { GRAPH_EXPLORER_HELP } from "../shared/helpContent";
import { MaHelpButton } from "../shared/HelpDialog";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { useHydratedDatasets } from "../shared/useHydratedDatasets";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { ModeSwitch } from "./ModeSwitch";
import { buildMockPayload } from "./devFixture";
import { AtlasView } from "./modes/AtlasView";
import { InvestigateView, type RefetchOverrides } from "./modes/InvestigateView";
import { useGraphSync } from "./state/useGraphSync";
import type { GraphExplorerViewState, GraphMode } from "./types";
import { readUrl, writeUrl } from "./urlState";

const APP_ID = "graph_explorer";
/** Graph node/edge datasets are row-hungry; hydrate up to 20k rows each
 * (evidence/metrics datasets are tiny and never approach the cap). */
const GRAPH_ROW_CAP = 20_000;

const MOCK_PAYLOAD = buildMockPayload();

export default function GraphExplorerApp() {
  const { view, callTool, fetchRows, updateModelContext } =
    useMiniApp<GraphExplorerViewState>({ appId: APP_ID, mockPayload: MOCK_PAYLOAD });

  const server = view?.view_state;
  const revisions = (server?.dataset_revisions ?? {}) as Record<string, number>;
  const datasets = useHydratedDatasets(
    view?.view_id,
    view?.datasets,
    revisions,
    fetchRows,
    GRAPH_ROW_CAP,
  );

  // Adoption / re-hydration key: per-dataset revisions (NOT SQL text).
  const revisionsKey = `${view?.view_id ?? ""}|${Object.entries(revisions)
    .map(([k, v]) => `${k}:${v}`)
    .sort()
    .join(",")}`;
  const { state, dispatch, syncError, retrySync, nextRequestId, isCurrent } =
    useGraphSync(view?.view_id, server, revisionsKey, callTool);

  const viewId = view?.view_id ?? "";
  const focusCall = (args: Record<string, unknown>) => {
    if (!viewId) return;
    callTool("update_graph_explorer_focus", { view_id: viewId, ...args }).catch(
      (err) => console.warn("[graph_explorer] focus sync failed", err),
    );
  };

  // Selection flow: local dispatch (instant) + focus tool (server refreshes
  // evidence/roles; the PATCH echo applies via useMiniApp).
  const onSelectNode = (id: string) => {
    dispatch({ type: "SELECT_NODE", id });
    if (id) focusCall({ selected_node_id: id });
  };
  const onSelectEdge = (id: string) => {
    dispatch({ type: "SELECT_EDGE", id });
    if (id) focusCall({ selected_edge_id: id });
  };
  const onClearSelection = () => dispatch({ type: "SELECT_NODE", id: "" });

  const onModeChange = (mode: GraphMode) => {
    dispatch({ type: "SET_MODE", mode });
    focusCall({ mode });
  };

  /** Seed a NEW investigate subgraph (empty relation_types → the server
   * auto-detects applicable profiles from the address roles). */
  const seedInvestigate = (nodeId: string) => {
    if (!viewId || !nodeId) return;
    const rid = nextRequestId();
    callTool("load_graph_explorer_seed", {
      view_id: viewId,
      seed_node_id: nodeId,
      seed_model: "",
      relation_types: [],
      hops: 1,
      transfer_window_days: state.windowDays,
      max_neighbors: state.maxNeighbors,
    })
      .then(() => {
        if (isCurrent(rid)) dispatch({ type: "SET_MODE", mode: "investigate" });
      })
      .catch((err) => console.error("[graph_explorer] seed failed", err));
  };

  /** Re-query the CURRENT seed with fresh filters (window/max/profile add).
   * Preserves the echoed hop depth so the counter never collapses to 1. */
  const refetchSeed = (overrides: RefetchOverrides) => {
    const seedId = server?.investigate?.seed?.id;
    if (!viewId || !seedId) return;
    const hops = Math.max(
      1,
      Math.min(
        Number(server?.investigate?.hops_used) || 1,
        Math.max(1, state.limits.max_hops),
      ),
    );
    nextRequestId(); // supersede any pending follow-up from older loads
    callTool("load_graph_explorer_seed", {
      view_id: viewId,
      seed_node_id: seedId,
      seed_model: "",
      relation_types: overrides.profiles ?? state.investigateProfiles,
      hops,
      transfer_window_days: overrides.windowDays ?? state.windowDays,
      max_neighbors: overrides.maxNeighbors ?? state.maxNeighbors,
    }).catch((err) => console.error("[graph_explorer] refetch failed", err));
  };

  /** Explicit BFS expansion — exactly the stepper depth, never silent. */
  const expandNode = (nodeId: string) => {
    if (!viewId || !nodeId) return;
    nextRequestId();
    callTool("expand_graph_explorer_node", {
      view_id: viewId,
      node_id: nodeId,
      relation_types: state.investigateProfiles,
      direction: "both",
      hops: state.expandDepth,
    }).catch((err) => console.error("[graph_explorer] expand failed", err));
  };

  /** Atlas sample (REPLACE semantics; empty profile list clears the atlas). */
  const loadSample = (profiles: string[]) => {
    if (!viewId) return;
    nextRequestId();
    callTool("load_graph_atlas_sample", {
      view_id: viewId,
      profiles,
      sample_size: state.atlasSampleSize,
      window_days: state.windowDays,
    }).catch((err) => console.error("[graph_explorer] atlas sample failed", err));
  };

  // ---- Deep links (boot: apply URL state the payload LACKS; then keep the
  // URL in sync via replaceState — unmanaged params like ?token= preserved).
  const urlBootDone = useRef(false);
  useEffect(() => {
    if (urlBootDone.current || !view || !server) return;
    urlBootDone.current = true;
    const u = readUrl();
    if (u.depth) dispatch({ type: "SET_EXPAND_DEPTH", depth: u.depth });
    if (u.layout === "force" || u.layout === "circular") {
      dispatch({ type: "SET_LAYOUT", layout: u.layout });
    }
    if (u.status === "all" || u.status === "approved" || u.status === "candidate") {
      dispatch({ type: "SET_STATUS_FILTER", filter: u.status });
    }
    if (u.window) dispatch({ type: "SET_WINDOW", days: u.window });
    if (u.max) dispatch({ type: "SET_MAX_NEIGHBORS", value: u.max });
    const serverSeed = server.investigate?.seed?.id ?? "";
    if (u.seed && u.seed !== serverSeed) {
      // Standalone /app?seed=… is mapped onto open_graph_explorer by the
      // route — only load when the payload does NOT already reflect it.
      seedInvestigate(u.seed);
    } else if (
      u.profiles.length &&
      (u.mode === "atlas" || !u.mode) &&
      !(server.atlas?.selected_profiles ?? []).length
    ) {
      dispatch({ type: "SET_ATLAS_PROFILES", profiles: u.profiles });
      loadSample(u.profiles);
    }
    if (u.sel) onSelectNode(u.sel);
    else if (u.esel) onSelectEdge(u.esel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, server]);

  useEffect(() => {
    if (!urlBootDone.current || typeof window === "undefined") return;
    writeUrl({
      mode: state.mode,
      seed: server?.investigate?.seed?.id ?? "",
      profiles:
        state.mode === "atlas" ? state.atlasProfiles : state.investigateProfiles,
      window: state.windowDays,
      max: state.maxNeighbors,
      sel: state.selection.nodeId,
      esel: state.selection.edgeId,
      status: state.statusFilter,
      layout: state.layout,
      depth: state.expandDepth,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    state.mode,
    state.atlasProfiles,
    state.investigateProfiles,
    state.windowDays,
    state.maxNeighbors,
    state.selection,
    state.statusFilter,
    state.layout,
    state.expandDepth,
    server?.investigate?.seed?.id,
  ]);

  // ---- Model context (what the agent sees about this view) ----
  const activePair = state.mode === "atlas" ? ["atlas_nodes", "atlas_edges"] : ["nodes", "edges"];
  const nodeCount = datasets[activePair[0]]?.rows.length ?? 0;
  const edgeCount = datasets[activePair[1]]?.rows.length ?? 0;
  const activeProfiles = useMemo(
    () => (state.mode === "atlas" ? state.atlasProfiles : state.investigateProfiles),
    [state.mode, state.atlasProfiles, state.investigateProfiles],
  );
  useEffect(() => {
    if (!view) return;
    updateModelContext({
      mode: state.mode,
      seed: server?.investigate?.seed?.id || "none",
      selected_node_id: state.selection.nodeId || "none",
      selected_edge_id: state.selection.edgeId || "none",
      active_profiles: activeProfiles.join(",") || "none",
      node_count: nodeCount,
      edge_count: edgeCount,
      hops_used: server?.investigate?.hops_used ?? 0,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    state.mode,
    state.selection.nodeId,
    state.selection.edgeId,
    activeProfiles,
    nodeCount,
    edgeCount,
    server?.investigate?.seed?.id,
    server?.investigate?.hops_used,
  ]);

  if (!view || !server) {
    return (
      <MiniAppChrome activeTabId="graph" rightSlot={<MaHelpButton content={GRAPH_EXPLORER_HELP} />}>
        <div className="ma-empty">Loading Graph Explorer…</div>
      </MiniAppChrome>
    );
  }

  return (
    <MiniAppChrome
      activeTabId="graph"
      bodyClassName="ma-body--flush"
      rightSlot={<MaHelpButton content={GRAPH_EXPLORER_HELP} />}
    >
      <div className="ge-shell">
        <WarningBanner warnings={view.warnings ?? []} />
        {syncError ? (
          <div className="ge-sync-error" role="alert">
            <span>View sync failed: {syncError}</span>
            <button type="button" className="ge-btn" onClick={retrySync}>
              Retry
            </button>
          </div>
        ) : null}
        {state.mode === "atlas" || !server.investigate?.seed?.id ? (
          // Investigate-with-seed folds the mode switch into the seed row
          // instead — one less full-width header bar above the canvas.
          <div className="ge-modebar">
            <span className="ge-title">Graph Explorer</span>
            <ModeSwitch mode={state.mode} onChange={onModeChange} />
          </div>
        ) : null}
        {state.mode === "atlas" ? (
          <AtlasView
            server={server}
            local={state}
            dispatch={dispatch}
            atlasNodes={datasets.atlas_nodes}
            atlasEdges={datasets.atlas_edges}
            loadSample={loadSample}
            seedInvestigate={seedInvestigate}
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onClearSelection={onClearSelection}
          />
        ) : (
          <InvestigateView
            server={server}
            local={state}
            dispatch={dispatch}
            modeSwitch={<ModeSwitch mode={state.mode} onChange={onModeChange} />}
            nodes={datasets.nodes}
            edges={datasets.edges}
            nodeEvidence={datasets.node_evidence}
            edgeEvidence={datasets.edge_evidence}
            graphMetrics={datasets.graph_metrics}
            refetchSeed={refetchSeed}
            seedInvestigate={seedInvestigate}
            expandNode={expandNode}
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onClearSelection={onClearSelection}
            onBrowseAtlas={() => onModeChange("atlas")}
          />
        )}
      </div>
    </MiniAppChrome>
  );
}
