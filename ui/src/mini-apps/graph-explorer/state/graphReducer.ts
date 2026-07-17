// Pure local-state reducer for the Graph Explorer (no React). The local
// state is the UI's source of truth between server payloads; ADOPT_SERVER
// maps view_state v2 wholesale into it (useGraphSync decides WHEN to adopt,
// keyed on dataset revisions / inbound patches).
//
// Limits come from the server (view_state["limits"]). PLACEHOLDER_LIMITS is
// only the pre-adoption placeholder so clamping has numbers to work with —
// every real value is overwritten on the first ADOPT_SERVER.

import type {
  GraphExplorerViewState,
  GraphLayout,
  GraphMode,
  Limits,
  StatusFilter,
} from "../types";

export interface GraphSelection {
  nodeId: string;
  edgeId: string;
}

export interface GraphLocalState {
  mode: GraphMode;
  selection: GraphSelection;
  atlasProfiles: string[];
  investigateProfiles: string[];
  windowDays: number;
  maxNeighbors: number;
  atlasSampleSize: number;
  /** BFS depth for explicit expand actions (button / double-click). */
  expandDepth: number;
  layout: GraphLayout;
  statusFilter: StatusFilter;
  limits: Limits;
}

export type GraphAction =
  | { type: "ADOPT_SERVER"; server: Partial<GraphExplorerViewState> | undefined }
  | { type: "SET_MODE"; mode: GraphMode }
  | { type: "SELECT_NODE"; id: string }
  | { type: "SELECT_EDGE"; id: string }
  | { type: "TOGGLE_ATLAS_PROFILE"; profile: string }
  | { type: "SET_ATLAS_PROFILES"; profiles: string[] }
  | { type: "TOGGLE_INVESTIGATE_PROFILE"; profile: string }
  | { type: "SET_WINDOW"; days: number }
  | { type: "SET_MAX_NEIGHBORS"; value: number }
  | { type: "SET_STATUS_FILTER"; filter: StatusFilter }
  | { type: "SET_LAYOUT"; layout: GraphLayout }
  | { type: "SET_EXPAND_DEPTH"; depth: number };

/** Pre-adoption placeholder ONLY — the server's limits block replaces this
 * on the first ADOPT_SERVER. Never read these as product constants. */
export const PLACEHOLDER_LIMITS: Limits = {
  max_hops: 1,
  bfs_node_cap: 0,
  default_expand_depth: 1,
  ui_default_window_days: 90,
  ui_default_max_neighbors: 100,
  atlas_sample_size: 150,
};

function clampDepth(depth: number, limits: Limits): number {
  const max = Math.max(1, Number(limits.max_hops) || 1);
  return Math.max(1, Math.min(Math.floor(depth) || 1, max));
}

function toggle(list: string[], item: string): string[] {
  return list.includes(item) ? list.filter((p) => p !== item) : [...list, item];
}

/** Map view_state v2 → local state. `previous` preserves client-only fields
 * (expandDepth) across adoptions. Exported for Vitest. */
export function adoptServerState(
  server: Partial<GraphExplorerViewState> | undefined,
  previous?: GraphLocalState,
): GraphLocalState {
  const limits: Limits = {
    ...PLACEHOLDER_LIMITS,
    ...(previous?.limits ?? {}),
    ...(server?.limits ?? {}),
  };
  const mode: GraphMode = server?.mode === "investigate" ? "investigate" : "atlas";
  const atlas = server?.atlas;
  const investigate = server?.investigate;
  const windowDays =
    (mode === "atlas" ? atlas?.window_days : investigate?.window_days) ||
    investigate?.window_days ||
    atlas?.window_days ||
    limits.ui_default_window_days;
  const expandDepth = clampDepth(
    previous?.expandDepth ?? limits.default_expand_depth,
    limits,
  );
  return {
    mode,
    selection: {
      nodeId: server?.selection?.node_id ?? "",
      edgeId: server?.selection?.edge_id ?? "",
    },
    atlasProfiles: [...(atlas?.selected_profiles ?? [])],
    investigateProfiles: [...(investigate?.active_profiles ?? [])],
    windowDays: Math.max(1, Number(windowDays) || limits.ui_default_window_days),
    maxNeighbors: Math.max(
      1,
      Number(investigate?.max_neighbors) || limits.ui_default_max_neighbors,
    ),
    atlasSampleSize: Math.max(
      1,
      Number(atlas?.sample_size) || limits.atlas_sample_size,
    ),
    expandDepth,
    layout: server?.layout === "circular" ? "circular" : "force",
    statusFilter:
      server?.semantic_status_filter === "approved" ||
      server?.semantic_status_filter === "candidate"
        ? server.semantic_status_filter
        : "all",
    limits,
  };
}

/** Initial-state builder (exported for Vitest). */
export function buildInitialState(
  server?: Partial<GraphExplorerViewState>,
): GraphLocalState {
  return adoptServerState(server);
}

export function graphReducer(
  state: GraphLocalState,
  action: GraphAction,
): GraphLocalState {
  switch (action.type) {
    case "ADOPT_SERVER":
      return adoptServerState(action.server, state);
    case "SET_MODE": {
      if (action.mode === state.mode) return state;
      // Mode switch clears selection (matches the server contract).
      return { ...state, mode: action.mode, selection: { nodeId: "", edgeId: "" } };
    }
    case "SELECT_NODE":
      return { ...state, selection: { nodeId: action.id, edgeId: "" } };
    case "SELECT_EDGE":
      return { ...state, selection: { nodeId: "", edgeId: action.id } };
    case "TOGGLE_ATLAS_PROFILE":
      return { ...state, atlasProfiles: toggle(state.atlasProfiles, action.profile) };
    case "SET_ATLAS_PROFILES":
      return { ...state, atlasProfiles: [...action.profiles] };
    case "TOGGLE_INVESTIGATE_PROFILE":
      return {
        ...state,
        investigateProfiles: toggle(state.investigateProfiles, action.profile),
      };
    case "SET_WINDOW":
      return { ...state, windowDays: Math.max(1, Math.floor(action.days) || 1) };
    case "SET_MAX_NEIGHBORS":
      return { ...state, maxNeighbors: Math.max(1, Math.floor(action.value) || 1) };
    case "SET_STATUS_FILTER":
      return { ...state, statusFilter: action.filter };
    case "SET_LAYOUT":
      return { ...state, layout: action.layout };
    case "SET_EXPAND_DEPTH":
      return { ...state, expandDepth: clampDepth(action.depth, state.limits) };
    default:
      return state;
  }
}
