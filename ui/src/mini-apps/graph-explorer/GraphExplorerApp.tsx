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
import { ToastStack } from "../shared/ToastStack";
import { TASK_OF_MODE, TaskSwitch, type GraphTask } from "./TaskSwitch";
import { downloadCaseExport } from "./caseExport";
import { buildMockPayload } from "./devFixture";
import { AtlasView } from "./modes/AtlasView";
import { FlowsView, type FlowsSettings } from "./modes/FlowsView";
import { TransactionsView, type TxSettings } from "./modes/TransactionsView";
import { InvestigateView, type RefetchOverrides } from "./modes/InvestigateView";
import { TimelineView, type TimelineSettings } from "./modes/TimelineView";
import { useGraphSync } from "./state/useGraphSync";
import { useSerializedLoader } from "./state/useSerializedLoader";
import { buildTransactionRequest } from "./transactionRequest";
import type {
  EvidenceExpectation,
  FlowDirection,
  GraphExplorerViewState,
  GraphMode,
} from "./types";
import { readUrl, writeUrl } from "./urlState";

const APP_ID = "graph_explorer";
const GRAPH_TOPOLOGY_DATASETS = new Set([
  "atlas_nodes",
  "atlas_edges",
  "atlas_preview_nodes",
  "atlas_preview_edges",
  "nodes",
  "edges",
  "flow_nodes",
  "flow_edges",
  "timeline_nodes",
  "timeline_edges",
]);

/** Hydration is evidence-class aware. A receipt cannot legitimately approach
 * the topology cap, while large graph datasets must not be silently clipped
 * at the shared hook's conservative default. Kept outside the component so
 * its identity remains stable across every render. */
function graphDatasetRowCap(key: string): number {
  if (GRAPH_TOPOLOGY_DATASETS.has(key)) return 120_000;
  if (key === "tx_legs") return 5_000;
  if (key === "tx_list") return 20_000;
  if (key === "timeline_narrative") return 20_000;
  if (key.endsWith("_evidence")) return 5_000;
  return 20_000;
}

const MOCK_PAYLOAD = import.meta.env.DEV ? buildMockPayload() : undefined;

function isGraphMode(value: string): value is GraphMode {
  return (
    value === "atlas" ||
    value === "investigate" ||
    value === "timeline" ||
    value === "flows" ||
    value === "transactions"
  );
}

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
    graphDatasetRowCap,
    // Geometric publication: a 90+-page hydration publishes ~8 times with
    // growing spacing instead of once per 500-row page — each publish
    // rebuilds the graph model, so per-page publishing froze the sim in a
    // 2-second stutter rhythm on large graphs.
    "geometric",
  );

  // Adoption / re-hydration key: per-dataset revisions (NOT SQL text).
  const revisionsKey = `${view?.view_id ?? ""}|${Object.entries(revisions)
    .map(([k, v]) => `${k}:${v}`)
    .sort()
    .join(",")}`;
  const { state, dispatch, syncError, retrySync, nextRequestId, isCurrent } =
    useGraphSync(view?.view_id, server, revisionsKey, callTool);

  const viewId = view?.view_id ?? "";
  const timelineRequestRevision = Number(
    server?.timeline?.forensic_scope?.request_id ?? 0,
  );
  const moneyRequestRevision = Number(server?.flows?.scope?.request_id ?? 0);
  const transactionRequestRevision = Math.max(
    Number(server?.transactions?.scope?.request_id ?? 0),
    Number(server?.transactions?.discovery_scope?.request_id ?? 0),
    Number(server?.transactions?.receipt_scope?.request_id ?? 0),
    Number(server?.transactions?.last_attempt?.request_id ?? 0),
  );
  const focusLoader = useSerializedLoader<Record<string, unknown>>(
    (snapshot) => callTool("update_graph_explorer_focus", snapshot),
    (err) => console.warn("[graph_explorer] focus sync failed", err),
    Number(server?.selection?.request_id ?? 0),
  );
  const focusCall = (args: Record<string, unknown>) => {
    if (!viewId) return 0;
    return focusLoader.enqueue({ view_id: viewId, ...args });
  };

  // Selection belongs to the task surface, not to the tab switch. Keep one
  // local focus per task so inspecting a transaction does not discard the
  // relationship or money edge the analyst was following.
  const selectionByTask = useRef<
    Record<GraphTask, { nodeId: string; edgeId: string }>
  >({
    relationships: { nodeId: "", edgeId: "" },
    money: { nodeId: "", edgeId: "" },
    tx: { nodeId: "", edgeId: "" },
  });

  // Atlas sampling, Investigate reseeding/refetch, and BFS expansion all
  // replace or extend the same relationship datasets. Serialize them as one
  // channel so an older payload can never apply after newer local intent.
  const relationshipLoader = useSerializedLoader<Record<string, unknown>>(
    async (snapshot) => {
      const {
        __tool,
        __enter_investigate,
        __intent_id,
        ...args
      } = snapshot;
      await callTool(String(__tool), args);
      if (
        __enter_investigate &&
        typeof __intent_id === "number" &&
        isCurrent(__intent_id)
      ) {
        dispatch({ type: "SET_MODE", mode: "investigate" });
      }
    },
    (err) => {
      console.error("[graph_explorer] relationship load failed", err);
      // Roll draft controls back to the last server-echoed scope. Mode and
      // selection remain locally owned unless mode_revision advanced.
      dispatch({ type: "ADOPT_SERVER", server });
    },
    Math.max(
      Number(server?.atlas?.scope?.request_id ?? 0),
      Number(server?.investigate?.scope?.request_id ?? 0),
    ),
  );

  // Catalog previews are inspect-only and therefore deliberately do not
  // share the applied Atlas/Investigate queue. A preview response can replace
  // only atlas_preview_* datasets; applying a profile still goes through the
  // relationship loader below.
  const atlasPreviewLoader = useSerializedLoader<Record<string, unknown>>(
    (snapshot) => callTool("load_graph_atlas_preview", snapshot),
    (err) => console.error("[graph_explorer] atlas preview failed", err),
    Number(server?.atlas_preview?.scope?.request_id ?? 0),
  );

  // Selection flow: local dispatch (instant) + focus tool (server refreshes
  // evidence/roles; the PATCH echo applies via useMiniApp).
  const onSelectNode = (id: string) => {
    selectionByTask.current[TASK_OF_MODE[state.mode]] = {
      nodeId: id,
      edgeId: "",
    };
    dispatch({ type: "SELECT_NODE", id });
    focusCall({ selected_node_id: id, selected_edge_id: "" });
  };
  const onSelectEdge = (id: string) => {
    selectionByTask.current[TASK_OF_MODE[state.mode]] = {
      nodeId: "",
      edgeId: id,
    };
    dispatch({ type: "SELECT_EDGE", id });
    focusCall({ selected_node_id: "", selected_edge_id: id });
  };
  const onClearSelection = () => {
    selectionByTask.current[TASK_OF_MODE[state.mode]] = {
      nodeId: "",
      edgeId: "",
    };
    dispatch({ type: "SELECT_NODE", id: "" });
    focusCall({ selected_node_id: "", selected_edge_id: "" });
  };

  const evidenceExpectation = useMemo<EvidenceExpectation | null>(() => {
    if (state.selection.edgeId) {
      return {
        subjectKind: "edge",
        subjectId: state.selection.edgeId,
        requestId: focusLoader.desiredRequestId,
      };
    }
    if (state.selection.nodeId) {
      return {
        subjectKind: "node",
        subjectId: state.selection.nodeId,
        requestId: focusLoader.desiredRequestId,
      };
    }
    return null;
  }, [
    state.selection.edgeId,
    state.selection.nodeId,
    focusLoader.desiredRequestId,
  ]);

  useEffect(() => {
    selectionByTask.current[TASK_OF_MODE[state.mode]] = { ...state.selection };
  }, [state.mode, state.selection]);

  const onModeChange = (
    mode: GraphMode,
    options: {
      /** Boot-only, fully resolved URL snapshot. Supplying it prevents the
       * mode switch from reading pre-dispatch reducer defaults. */
      timelineRequest?: Partial<TimelineSettings>;
      forceTimelineRequest?: boolean;
    } = {},
  ) => {
    const currentTask = TASK_OF_MODE[state.mode];
    const nextTask = TASK_OF_MODE[mode];
    selectionByTask.current[currentTask] = { ...state.selection };
    const preservedSelection = selectionByTask.current[nextTask];
    dispatch({ type: "SET_MODE", mode });
    if (preservedSelection.edgeId) {
      dispatch({ type: "SELECT_EDGE", id: preservedSelection.edgeId });
    } else if (preservedSelection.nodeId) {
      dispatch({ type: "SELECT_NODE", id: preservedSelection.nodeId });
    }
    // Supersede any in-flight seedInvestigate whose post-resolve SET_MODE
    // (guarded by isCurrent) would otherwise clobber this explicit tab click.
    nextRequestId();
    // Mode revision, restored task selection and its evidence are one server
    // mutation. The former two-call sequence could adopt the intermediate
    // clear and then ignore the reselect because both responses shared the
    // same mode revision.
    focusCall({
      mode,
      selected_node_id: preservedSelection.nodeId,
      selected_edge_id: preservedSelection.edgeId,
    });
    // First switch into Timeline (or a scope change since the last load):
    // auto-load via the serialized loader.
    if (mode === "timeline") {
      const tl = server?.timeline;
      const invSeed = server?.investigate?.seed?.id ?? "";
      const stale = !tl?.range_start || (tl?.anchor?.id ?? "") !== invSeed;
      if (options.forceTimelineRequest || stale) {
        requestTimeline(options.timelineRequest ?? {});
      }
    }
  };

  // ---- Serialized timeline loader (full-snapshot queue; newest wins). ------
  // The snapshot is built COMPLETELY at enqueue time from the caller's render
  // context — a queued follow-up can never pair a new range with a stale
  // grain (the old pending-retry re-entered a stale closure).
  const timelineLoader = useSerializedLoader<Record<string, unknown>>(
    (snapshot) => callTool("load_graph_timeline", snapshot),
    (err) => console.error("[graph_explorer] timeline load failed", err),
    timelineRequestRevision,
  );
  const timelineLoading = timelineLoader.loading;
  const requestTimeline = (settings: Partial<TimelineSettings>) => {
    if (!viewId) return;
    const args: Record<string, unknown> = { view_id: viewId };
    // Explicitly-edited profiles are ALWAYS sent (even when the state list is
    // the source) — omitting them let the server fall back to defaults and
    // resurrect untoggled edge types.
    const profiles =
      settings.profiles ??
      (state.timelineProfiles.length ? state.timelineProfiles : undefined);
    if (profiles !== undefined) args.profiles = profiles;
    if (settings.seeds?.length) args.seed_node_ids = settings.seeds;
    if (settings.direction) args.direction = settings.direction;
    if (settings.tokens !== undefined) args.tokens = settings.tokens;
    if (settings.minUsd !== undefined) args.min_usd = settings.minUsd;
    args.grain = settings.grain ?? state.timelineGrain;
    args.range_days = settings.rangeDays ?? state.timelineRangeDays;
    timelineLoader.enqueue(args);
  };

  // ---- Serialized flows loader (same full-snapshot contract). --------------
  const flowsLoader = useSerializedLoader<Record<string, unknown>>(
    (snapshot) => callTool("load_graph_flows", snapshot),
    (err) => console.error("[graph_explorer] flows load failed", err),
    moneyRequestRevision,
  );
  const flowsLoading = flowsLoader.loading;
  /** Full re-trace (REPLACE semantics) — the complete snapshot is built at
   * enqueue time so a queued follow-up can never mix old and new filters. */
  const requestFlows = (settings: Partial<FlowsSettings>) => {
    if (!viewId) return;
    const seeds = settings.seeds ?? server?.flows?.seeds ?? [];
    if (!seeds.length) return;
    flowsLoader.enqueue({
      view_id: viewId,
      seed_node_ids: seeds,
      direction: settings.direction ?? state.flowsDirection,
      hops: settings.hops ?? state.flowsHops,
      range_days: settings.rangeDays ?? state.flowsRangeDays,
      min_usd: settings.minUsd ?? state.flowsMinUsd,
      tokens: settings.tokens ?? state.flowsTokens,
      include_bridges: settings.includeBridges ?? state.flowsIncludeBridges,
    });
  };
  // ---- Serialized transactions loader (same full-snapshot contract). ------
  const txLoader = useSerializedLoader<Record<string, unknown>>(
    (snapshot) => callTool("load_graph_transactions", snapshot),
    (err) => console.error("[graph_explorer] transactions load failed", err),
    transactionRequestRevision,
  );
  const txLoading = txLoader.loading;
  /** Open transactions. Either explicit hashes ("what did this tx do?") or an
   * address + window ("what has this address been doing?"). The snapshot is
   * built at enqueue time so a queued follow-up cannot mix old and new
   * filters. */
  const requestTransactions = (settings: Partial<TxSettings>) => {
    const snapshot = buildTransactionRequest(
      viewId,
      server?.transactions,
      settings,
    );
    if (snapshot) txLoader.enqueue(snapshot);
  };

  // Failed requests retire their draft controls. The graph remains labelled
  // with (and the controls return to) the last server-echoed applied scope.
  useEffect(() => {
    if (!flowsLoader.error || !server?.flows) return;
    dispatch({ type: "SET_FLOWS_DIRECTION", direction: server.flows.direction });
    dispatch({ type: "SET_FLOWS_HOPS", hops: server.flows.hops });
    dispatch({ type: "SET_FLOWS_RANGE", days: server.flows.range_days });
    dispatch({ type: "SET_FLOWS_MIN_USD", minUsd: server.flows.min_usd });
    dispatch({ type: "SET_FLOWS_TOKENS", tokens: server.flows.tokens });
    dispatch({ type: "SET_FLOWS_BRIDGES", on: server.flows.include_bridges });
  }, [flowsLoader.error, server?.flows, dispatch]);

  useEffect(() => {
    if (!timelineLoader.error || !server?.timeline) return;
    dispatch({ type: "SET_TIMELINE_GRAIN", grain: server.timeline.grain });
    dispatch({ type: "SET_TIMELINE_RANGE", days: server.timeline.range_days });
    dispatch({
      type: "SET_TIMELINE_WINDOW",
      buckets: server.timeline.window_buckets,
    });
    dispatch({ type: "SET_TIMELINE_PROFILES", profiles: server.timeline.profiles });
  }, [timelineLoader.error, server?.timeline, dispatch]);

  /** Per-node Trace (MERGE): extends the graph 1 hop from an on-graph node
   * using the VIEW's filters (the server ignores+warns on conflicts). Also
   * how an analyst pushes through a terminal DEX/Bridge/Privacy node. */
  const traceFlow = (nodeId: string, direction: FlowDirection) => {
    if (!viewId || !nodeId) return;
    flowsLoader.enqueue({
      view_id: viewId,
      seed_node_ids: [nodeId],
      direction,
      hops: 1,
      merge: true,
    });
  };

  /** Seed a NEW investigate subgraph (empty relation_types → the server
   * auto-detects applicable profiles from the address roles). */
  const seedInvestigate = (nodeId: string, enterInvestigate = true) => {
    if (!viewId || !nodeId) return;
    const intentId = nextRequestId();
    relationshipLoader.enqueue({
      __tool: "load_graph_explorer_seed",
      __enter_investigate: enterInvestigate,
      __intent_id: intentId,
      view_id: viewId,
      seed_node_id: nodeId,
      seed_model: "",
      relation_types: [],
      hops: 1,
      transfer_window_days: state.windowDays,
      max_neighbors: state.maxNeighbors,
    });
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
    relationshipLoader.enqueue({
      __tool: "load_graph_explorer_seed",
      view_id: viewId,
      seed_node_id: seedId,
      seed_model: "",
      relation_types: overrides.profiles ?? state.investigateProfiles,
      hops,
      transfer_window_days: overrides.windowDays ?? state.windowDays,
      max_neighbors: overrides.maxNeighbors ?? state.maxNeighbors,
    });
  };

  /** Explicit BFS expansion — exactly the stepper depth, never silent. */
  const expandNode = (nodeId: string) => {
    if (!viewId || !nodeId) return;
    nextRequestId();
    relationshipLoader.enqueue({
      __tool: "expand_graph_explorer_node",
      view_id: viewId,
      node_id: nodeId,
      relation_types: state.investigateProfiles,
      direction: "both",
      hops: state.expandDepth,
    });
  };

  /** Atlas sample (REPLACE semantics; empty profile list clears the atlas). */
  const loadSample = (profiles: string[]) => {
    if (!viewId) return;
    nextRequestId();
    relationshipLoader.enqueue({
      __tool: "load_graph_atlas_sample",
      view_id: viewId,
      profiles,
      sample_size: state.atlasSampleSize,
      window_days: state.windowDays,
    });
  };

  /** Load one real relationship sample for inspection. This does not touch
   * atlas.selected_profiles; AtlasView applies only through loadSample after
   * an explicit "Add to graph" action. */
  const loadAtlasPreview = (profile: string) => {
    if (!viewId || !profile) return;
    atlasPreviewLoader.enqueue({
      view_id: viewId,
      profile,
      sample_size: Math.min(25, Math.max(1, state.atlasSampleSize)),
      window_days: state.windowDays,
    });
  };

  // ---- Deep links (boot: apply URL state the payload LACKS; then keep the
  // URL in sync via replaceState — unmanaged params like ?token= preserved).
  const urlBootDone = useRef(false);
  const timelineBootSeeds = useRef<string[]>([]);
  // The URL-writer effect runs in the same commit as this boot effect. Skip
  // that first stale-state write so it cannot erase tgrain/trange/twin before
  // the reducer dispatches below are visible on the next render.
  const skipFirstUrlSync = useRef(false);
  useEffect(() => {
    if (urlBootDone.current || !view || !server) return;
    urlBootDone.current = true;
    skipFirstUrlSync.current = true;
    const u = readUrl();
    timelineBootSeeds.current = u.mode === "timeline" ? u.fseeds : [];
    const timelineGrain =
      u.tgrain === "day" || u.tgrain === "week" || u.tgrain === "month"
        ? u.tgrain
        : state.timelineGrain;
    const timelineRangeDays = u.trange || u.frange || state.timelineRangeDays;
    const hasTimelineRequest =
      u.mode === "timeline" &&
      Boolean(
        u.tgrain ||
          u.trange ||
          u.frange ||
          u.fseeds.length ||
          u.ftok.length ||
          u.fdir,
      );
    const timelineBootRequest: Partial<TimelineSettings> = {
      grain: timelineGrain,
      rangeDays: timelineRangeDays,
      seeds: u.fseeds,
      direction:
        u.fdir === "in" || u.fdir === "both" ? u.fdir : "out",
      tokens: u.ftok,
      minUsd: u.fmin || state.flowsMinUsd,
    };
    // `mode` is a legacy public route and remains authoritative at boot. The
    // old code only used it to qualify Atlas profile loads, which meant direct
    // Timeline/Transactions links silently opened Atlas and rewrote the URL.
    if (isGraphMode(u.mode) && u.mode !== state.mode) {
      onModeChange(
        u.mode,
        u.mode === "timeline"
          ? {
              timelineRequest: timelineBootRequest,
              forceTimelineRequest: hasTimelineRequest,
            }
          : undefined,
      );
    } else if (u.mode === "timeline" && hasTimelineRequest) {
      // A reopened Timeline view may already own the mode while its URL asks
      // for a different scope. Enqueue exactly that URL snapshot once.
      requestTimeline(timelineBootRequest);
    }
    if (u.depth) dispatch({ type: "SET_EXPAND_DEPTH", depth: u.depth });
    if (u.layout === "force" || u.layout === "circular") {
      dispatch({ type: "SET_LAYOUT", layout: u.layout });
    }
    if (u.status === "all" || u.status === "approved" || u.status === "candidate") {
      dispatch({ type: "SET_STATUS_FILTER", filter: u.status });
    }
    if (u.window) dispatch({ type: "SET_WINDOW", days: u.window });
    if (u.max) dispatch({ type: "SET_MAX_NEIGHBORS", value: u.max });
    if (u.tgrain === "day" || u.tgrain === "week" || u.tgrain === "month") {
      dispatch({ type: "SET_TIMELINE_GRAIN", grain: u.tgrain });
    }
    if (u.trange) dispatch({ type: "SET_TIMELINE_RANGE", days: u.trange });
    if (u.twin) dispatch({ type: "SET_TIMELINE_WINDOW", buckets: u.twin });
    if (u.fdir === "out" || u.fdir === "in" || u.fdir === "both") {
      dispatch({ type: "SET_FLOWS_DIRECTION", direction: u.fdir });
    }
    if (u.fhops) dispatch({ type: "SET_FLOWS_HOPS", hops: u.fhops });
    if (u.fmin) dispatch({ type: "SET_FLOWS_MIN_USD", minUsd: u.fmin });
    if (u.frange) dispatch({ type: "SET_FLOWS_RANGE", days: u.frange });
    if (u.ftok.length) dispatch({ type: "SET_FLOWS_TOKENS", tokens: u.ftok });
    if (
      u.mode !== "timeline" &&
      u.fseeds.length &&
      !(server.flows?.seeds ?? []).length
    ) {
      // Deep-linked trace the payload does NOT already reflect. The flows load
      // no longer sets mode, so switch the tab explicitly: SET_MODE flips the
      // view now, focusCall persists it server-side (bumps mode_revision) so a
      // later adoption keeps flows.
      dispatch({ type: "SET_MODE", mode: "flows" });
      focusCall({ mode: "flows" });
      flowsLoader.enqueue({
        view_id: view.view_id,
        seed_node_ids: u.fseeds,
        direction: u.fdir || "out",
        hops: u.fhops || undefined,
        range_days: u.frange || undefined,
        min_usd: u.fmin || undefined,
        tokens: u.ftok,
      });
    }
    const txState = server.transactions ?? {};
    const urlTxSubject = u.txhashes.length ? u.txhashes : u.txseed ? [u.txseed] : [];
    const serverTxSubject = txState.query
      ? txState.query.kind === "hash"
        ? txState.query.hashes
        : txState.query.address
          ? [txState.query.address]
          : []
      : (txState.tx_hashes ?? []).length &&
          txState.scope?.window?.source === "ignored_for_explicit_hash"
        ? (txState.tx_hashes ?? [])
        : txState.seed
          ? [txState.seed]
          : [];
    if (
      urlTxSubject.length &&
      urlTxSubject.join(",").toLowerCase() !==
        serverTxSubject.join(",").toLowerCase()
    ) {
      dispatch({ type: "SET_MODE", mode: "transactions" });
      focusCall({ mode: "transactions", selected_node_id: "", selected_edge_id: "" });
      txLoader.enqueue({
        view_id: view.view_id,
        operation: u.txhashes.length ? "receipt" : "discover",
        tx_hashes: u.txhashes,
        seed_node_id: u.txhashes.length ? "" : u.txseed,
        counterparty_ids: u.txcounterparties,
        tokens: u.txtokens,
        t0: u.txt0,
        t1: u.txt1,
        // Legacy txrange remains readable for URL compatibility, but address
        // discovery is all stored history unless the analyst supplied exact
        // UTC bounds. Never synthesize an arbitrary lookback here.
        range_days: 0,
        max_txs: u.txmax || 25,
        page_size: u.txmax || 25,
        cursor: "",
        activity_kinds: ["direct", "erc20"],
        min_usd: 0,
        expand_node_id: "",
        after_block: 0,
        after_index: -1,
        merge: false,
      });
    }
    const serverSeed = server.investigate?.seed?.id ?? "";
    if (u.seed && u.seed !== serverSeed) {
      // Standalone /app?seed=… is mapped onto open_graph_explorer by the
      // route — only load when the payload does NOT already reflect it. An
      // explicit legacy mode remains authoritative: Atlas may pre-load that
      // seed's Investigate dataset, but the loader must not switch the visible
      // Relationships subview after it resolves.
      seedInvestigate(
        u.seed,
        !isGraphMode(u.mode) || u.mode === "investigate",
      );
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
    if (skipFirstUrlSync.current) {
      skipFirstUrlSync.current = false;
      return;
    }
    const transactionState = server?.transactions;
    const transactionQuery = transactionState?.query;
    const legacyExplicitHash =
      transactionState?.scope?.window?.source === "ignored_for_explicit_hash";
    const urlTransactionHashes = transactionQuery
      ? transactionQuery.kind === "hash"
        ? transactionQuery.hashes
        : []
      : legacyExplicitHash
        ? transactionState?.tx_hashes ?? []
        : [];
    const urlTransactionSeed = transactionQuery
      ? transactionQuery.address ?? ""
      : legacyExplicitHash
        ? ""
        : transactionState?.seed ?? "";
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
      tgrain: state.timelineGrain,
      trange: state.timelineRangeDays,
      twin: state.timelineWindowBuckets,
      fseeds:
        state.mode === "timeline"
          ? server?.timeline?.seed_ids?.length
            ? server.timeline.seed_ids
            : timelineBootSeeds.current.length
              ? timelineBootSeeds.current
              : server?.flows?.seeds ?? []
          : server?.flows?.seeds ?? [],
      fdir: state.flowsDirection,
      fhops: state.flowsHops,
      fmin: state.flowsMinUsd,
      frange: state.flowsRangeDays,
      ftok: state.flowsTokens,
      // Serialize the analyst's query, never address-discovery result hashes.
      txhashes: urlTransactionHashes,
      txseed: urlTransactionSeed,
      txcounterparties:
        transactionQuery?.counterparties ?? transactionState?.counterparties ?? [],
      txtokens: transactionQuery?.tokens ?? transactionState?.tokens ?? [],
      txrange: transactionState?.range_days ?? 30,
      txmax: transactionState?.max_txs ?? 25,
      txt0: transactionQuery?.window?.t0 ?? transactionState?.t0 ?? "",
      txt1: transactionQuery?.window?.t1 ?? transactionState?.t1 ?? "",
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
    state.timelineGrain,
    state.timelineRangeDays,
    state.timelineWindowBuckets,
    state.flowsDirection,
    state.flowsHops,
    state.flowsMinUsd,
    state.flowsRangeDays,
    state.flowsTokens,
    server?.investigate?.seed?.id,
    server?.flows?.seeds,
    server?.transactions?.tx_hashes,
    server?.transactions?.seed,
    server?.transactions?.query?.kind,
    server?.transactions?.query?.address,
    server?.transactions?.query?.hashes,
    server?.transactions?.counterparties,
    server?.transactions?.tokens,
    server?.transactions?.range_days,
    server?.transactions?.max_txs,
    server?.transactions?.query,
    server?.transactions?.scope?.window?.source,
  ]);

  // ---- Model context (what the agent sees about this view) ----
  const activePair =
    state.mode === "atlas"
      ? ["atlas_nodes", "atlas_edges"]
      : state.mode === "timeline"
        ? ["timeline_nodes", "timeline_edges"]
        : state.mode === "flows"
          ? ["flow_nodes", "flow_edges"]
          : state.mode === "transactions"
            ? ["tx_nodes", "tx_legs"]
          : ["nodes", "edges"];
  const nodeCount = datasets[activePair[0]]?.rows.length ?? 0;
  const edgeCount = datasets[activePair[1]]?.rows.length ?? 0;
  const activeProfiles = useMemo(
    () => (state.mode === "atlas" ? state.atlasProfiles : state.investigateProfiles),
    [state.mode, state.atlasProfiles, state.investigateProfiles],
  );
  useEffect(() => {
    if (!view) return;
    updateModelContext({
      task: TASK_OF_MODE[state.mode],
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

  // Dataset warnings already live with the forensic scope that explains
  // their impact. Repeating the same text as floating toasts covers the
  // graph/table (especially at 600-700px) and detaches the warning from the
  // evidence it qualifies. Keep toasts only for unmatched shell-level
  // warnings; scope warnings remain available in their task disclosure.
  const scopedWarningSet = new Set(
    [
      server.atlas?.scope,
      server.atlas_preview?.scope,
      server.investigate?.scope,
      server.flows?.scope,
      server.timeline?.forensic_scope,
      server.transactions?.scope,
      server.focus_scope,
    ].flatMap((scope) => scope?.warnings ?? []),
  );
  const shellWarnings = (view.warnings ?? []).filter(
    (warning) => !scopedWarningSet.has(warning),
  );

  return (
    <MiniAppChrome
      activeTabId="graph"
      bodyClassName="ma-body--flush"
      rightSlot={<MaHelpButton content={GRAPH_EXPLORER_HELP} />}
    >
      <div className="ge-shell">
        <ToastStack warnings={shellWarnings} />
        {syncError ? (
          <div className="ge-sync-error" role="alert">
            <span>View sync failed: {syncError}</span>
            <button type="button" className="ge-btn" onClick={retrySync}>
              Retry
            </button>
          </div>
        ) : null}
        <TaskSwitch
          mode={state.mode}
          onChange={onModeChange}
          onExportCase={() => {
            const analyst = window.prompt("Analyst name for this case export", "") ?? "";
            downloadCaseExport({
              viewId,
              server,
              datasets,
              descriptors: view.datasets,
              analyst,
            });
          }}
        />
        {state.mode === "atlas" ? (
          <AtlasView
            server={server}
            local={state}
            dispatch={dispatch}
            atlasNodes={datasets.atlas_nodes}
            atlasEdges={datasets.atlas_edges}
            atlasPreviewNodes={datasets.atlas_preview_nodes}
            atlasPreviewEdges={datasets.atlas_preview_edges}
            atlasPreviewNodeDescriptor={view.datasets?.atlas_preview_nodes}
            atlasPreviewEdgeDescriptor={view.datasets?.atlas_preview_edges}
            loadSample={loadSample}
            loading={relationshipLoader.loading}
            loadError={relationshipLoader.error}
            loadPreview={loadAtlasPreview}
            previewLoading={atlasPreviewLoader.loading}
            previewError={atlasPreviewLoader.error}
            desiredPreviewRequestId={atlasPreviewLoader.desiredRequestId}
            seedInvestigate={seedInvestigate}
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onClearSelection={onClearSelection}
          />
        ) : state.mode === "flows" ? (
          <FlowsView
            server={server}
            local={state}
            dispatch={dispatch}
            flowNodes={datasets.flow_nodes}
            flowEdges={datasets.flow_edges}
            nodeEvidence={datasets.node_evidence}
            edgeEvidence={datasets.edge_evidence}
            evidenceExpectation={evidenceExpectation}
            requestFlows={requestFlows}
            traceFlow={traceFlow}
            loading={flowsLoading}
            loadError={flowsLoader.error}
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onClearSelection={onClearSelection}
            onBrowseInvestigate={() => onModeChange("investigate")}
            onOpenTransactions={(edge, appliedWindow) => {
              requestTransactions({
                operation: "discover",
                txHashes: [],
                seed: edge.source,
                counterparties: [edge.target],
                tokens: edge.tokenAddress ? [edge.tokenAddress] : [],
                rangeDays: appliedWindow.rangeDays,
                t0: appliedWindow.t0,
                t1: appliedWindow.t1,
              });
              onModeChange("transactions");
            }}
          />
        ) : state.mode === "transactions" ? (
          <TransactionsView
            viewId={viewId}
            server={server}
            local={state}
            txNodes={datasets.tx_nodes}
            txLegs={datasets.tx_legs}
            txList={datasets.tx_list}
            txContext={datasets.tx_context}
            nodeEvidence={datasets.node_evidence}
            edgeEvidence={datasets.edge_evidence}
            evidenceExpectation={evidenceExpectation}
            requestTransactions={requestTransactions}
            loading={txLoading}
            loadError={txLoader.error}
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onClearSelection={onClearSelection}
          />
        ) : state.mode === "timeline" ? (
          <TimelineView
            server={server}
            local={state}
            dispatch={dispatch}
            timelineNodes={datasets.timeline_nodes}
            timelineEdges={datasets.timeline_edges}
            timelineNarrative={datasets.timeline_narrative}
            nodeEvidence={datasets.node_evidence}
            edgeEvidence={datasets.edge_evidence}
            evidenceExpectation={evidenceExpectation}
            requestTimeline={requestTimeline}
            loading={timelineLoading}
            loadError={timelineLoader.error}
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onClearSelection={onClearSelection}
            onBrowseMoneyTrail={() => onModeChange("flows")}
          />
        ) : (
          <InvestigateView
            server={server}
            local={state}
            dispatch={dispatch}
            nodes={datasets.nodes}
            edges={datasets.edges}
            nodeEvidence={datasets.node_evidence}
            edgeEvidence={datasets.edge_evidence}
            evidenceExpectation={evidenceExpectation}
            refetchSeed={refetchSeed}
            seedInvestigate={seedInvestigate}
            expandNode={expandNode}
            loading={relationshipLoader.loading}
            loadError={relationshipLoader.error}
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
