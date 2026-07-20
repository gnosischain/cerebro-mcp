// Pure local-state reducer for the Graph Explorer (no React). The local
// state is the UI's source of truth between server payloads; ADOPT_SERVER
// maps view_state v2 wholesale into it (useGraphSync decides WHEN to adopt,
// keyed on dataset revisions / inbound patches).
//
// Limits come from the server (view_state["limits"]). PLACEHOLDER_LIMITS is
// only the pre-adoption placeholder so clamping has numbers to work with —
// every real value is overwritten on the first ADOPT_SERVER.

import type {
  FlowDirection,
  GraphExplorerViewState,
  GraphLayout,
  GraphMode,
  Limits,
  ProfileSelection,
  ScopedControls,
  StatusFilter,
  TimelineGrain,
} from "../types";

export interface GraphSelection {
  nodeId: string;
  edgeId: string;
}

export interface GraphLocalState {
  mode: GraphMode;
  /** Server-owned mode authority. The local mode/selection are adopted from
   * the server ONLY on the initial load or when this advances (an explicit
   * mode command); a data-load adoption preserves them. */
  modeRevision: number;
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
  /** Timeline settings mirrored from the server namespace (cursor/playing
   * live in useTimelineFilter — client-local, never synced). */
  timelineGrain: TimelineGrain;
  timelineRangeDays: number;
  timelineWindowBuckets: number;
  timelineProfiles: string[];
  /** Flows settings mirrored from the server namespace (seeds/t0/t1 and all
   * counts are server-owned — read them from view_state.flows). */
  flowsDirection: FlowDirection;
  flowsHops: number;
  flowsRangeDays: number;
  flowsMinUsd: number;
  flowsTokens: string[];
  flowsIncludeBridges: boolean;
}

export type GraphAction =
  | { type: "ADOPT_SERVER"; server: Partial<GraphExplorerViewState> | undefined }
  | { type: "RESTORE_DRAFT"; state: GraphLocalState }
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
  | { type: "SET_EXPAND_DEPTH"; depth: number }
  | { type: "SET_TIMELINE_GRAIN"; grain: TimelineGrain }
  | { type: "SET_TIMELINE_RANGE"; days: number }
  | { type: "SET_TIMELINE_WINDOW"; buckets: number }
  | { type: "SET_TIMELINE_PROFILES"; profiles: string[] }
  | { type: "SET_FLOWS_DIRECTION"; direction: FlowDirection }
  | { type: "SET_FLOWS_HOPS"; hops: number }
  | { type: "SET_FLOWS_RANGE"; days: number }
  | { type: "SET_FLOWS_MIN_USD"; minUsd: number }
  | { type: "SET_FLOWS_TOKENS"; tokens: string[] }
  | { type: "SET_FLOWS_BRIDGES"; on: boolean };

/** Pre-adoption placeholder ONLY — the server's limits block replaces this
 * on the first ADOPT_SERVER. Never read these as product constants. */
export const PLACEHOLDER_LIMITS: Limits = {
  max_hops: 1,
  bfs_node_cap: 0,
  default_expand_depth: 1,
  ui_default_window_days: 90,
  ui_default_max_neighbors: 100,
  atlas_sample_size: 150,
  flows_default_hops: 2,
  flows_max_hops: 4,
  flows_default_min_usd: 10,
  flows_default_range_days: 90,
};

function clampFlowHops(hops: number, limits: Limits): number {
  const max = Math.max(1, Number(limits.flows_max_hops) || 4);
  return Math.max(1, Math.min(Math.floor(hops) || 1, max));
}

function clampDepth(depth: number, limits: Limits): number {
  const max = Math.max(1, Number(limits.max_hops) || 1);
  return Math.max(1, Math.min(Math.floor(depth) || 1, max));
}

function toggle(list: string[], item: string): string[] {
  return list.includes(item) ? list.filter((p) => p !== item) : [...list, item];
}

export type ScopedControlsAction<T> =
  | { type: "EDIT_DRAFT"; value: T }
  | { type: "LOAD_STARTED"; requestId: number }
  | { type: "SCOPE_ACCEPTED"; requestId: number; scopeId: string }
  | { type: "LOAD_FAILED"; requestId: number; error: string };

/** Build a control pair without aliasing draft and applied through a mutable
 * caller object. Callers with object values should provide immutable values
 * (the reducer never mutates T). */
export function createScopedControls<T>(
  initialDraft: T,
  applied: T | null = initialDraft,
): ScopedControls<T> {
  return {
    draft: initialDraft,
    applied,
    pending: null,
    scopeId: null,
    error: null,
  };
}

/** Generic draft → pending → applied commit protocol. A stale success OR
 * failure is ignored, preventing an older request from accepting or rolling
 * back a newer snapshot. */
export function reduceScopedControls<T>(
  state: ScopedControls<T>,
  action: ScopedControlsAction<T>,
  normalize: (value: T) => T = (value) => value,
): ScopedControls<T> {
  switch (action.type) {
    case "EDIT_DRAFT":
      return { ...state, draft: normalize(action.value), error: null };
    case "LOAD_STARTED":
      return {
        ...state,
        pending: {
          requestId: action.requestId,
          snapshot: normalize(state.draft),
        },
        error: null,
      };
    case "SCOPE_ACCEPTED": {
      if (state.pending?.requestId !== action.requestId) return state;
      const applied = normalize(state.pending.snapshot);
      return {
        ...state,
        draft: applied,
        applied,
        pending: null,
        scopeId: action.scopeId,
        error: null,
      };
    }
    case "LOAD_FAILED":
      if (state.pending?.requestId !== action.requestId) return state;
      return {
        ...state,
        draft: state.applied === null ? state.draft : normalize(state.applied),
        pending: null,
        error: action.error,
      };
    default:
      return state;
  }
}

/** Stable set normalization for profile/token/address arrays. Supply a
 * transform such as `(v) => v.trim().toLowerCase()` for addresses. */
export function normalizeStringSet(
  values: readonly string[],
  transform: (value: string) => string = (value) => value.trim(),
): string[] {
  return [...new Set(values.map(transform).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b),
  );
}

export function normalizedStringSetsEqual(
  a: readonly string[],
  b: readonly string[],
): boolean {
  const aa = normalizeStringSet(a);
  const bb = normalizeStringSet(b);
  return aa.length === bb.length && aa.every((value, index) => value === bb[index]);
}

export function scopedControlsAreStale<T>(
  state: ScopedControls<T>,
  equals: (a: T, b: T) => boolean = Object.is,
): boolean {
  return Boolean(
    state.pending || state.applied === null || !equals(state.draft, state.applied),
  );
}

/** `undefined` means the client has not adopted server authority. An echoed
 * list, including [], is authoritative and therefore applied. */
export function createProfileSelection(
  profiles: string[] | undefined,
  scopeId: string | null = null,
): ProfileSelection {
  const normalized = normalizeStringSet(profiles ?? []);
  return {
    phase: profiles === undefined ? "unresolved" : "applied",
    draft: normalized,
    applied: normalized,
    scopeId,
  };
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
  // Mode + selection are adopted from the server ONLY on the initial build
  // (no previous) or when the server's mode_revision advances (an explicit
  // mode command). A data-load adoption (revision unchanged) PRESERVES the
  // local mode + selection — so a slow cross-mode trace can't flip the tab.
  const serverModeRevision = Number(server?.mode_revision ?? 0);
  const adoptMode =
    previous === undefined ||
    serverModeRevision > Number(previous.modeRevision ?? -1);
  const validServerMode: GraphMode =
    server?.mode === "investigate" ||
    server?.mode === "timeline" ||
    server?.mode === "flows" ||
    server?.mode === "transactions"
      ? server.mode
      : "atlas";
  const mode: GraphMode = adoptMode ? validServerMode : previous.mode;
  const selection: GraphSelection = adoptMode
    ? {
        nodeId: server?.selection?.node_id ?? "",
        edgeId: server?.selection?.edge_id ?? "",
      }
    : previous.selection;
  const modeRevision = Number(
    server?.mode_revision ?? previous?.modeRevision ?? 0,
  );
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
  const timeline = server?.timeline;
  const flows = server?.flows;
  return {
    mode,
    modeRevision,
    selection,
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
    timelineGrain:
      timeline?.grain === "day" || timeline?.grain === "month"
        ? timeline.grain
        : "week",
    timelineRangeDays: Math.max(
      1,
      Number(timeline?.range_days) || previous?.timelineRangeDays || 365,
    ),
    timelineWindowBuckets: Math.max(
      1,
      Number(timeline?.window_buckets) || previous?.timelineWindowBuckets || 4,
    ),
    timelineProfiles: [...(timeline?.profiles ?? previous?.timelineProfiles ?? [])],
    flowsDirection:
      flows?.direction === "in" || flows?.direction === "both"
        ? flows.direction
        : flows?.direction === "out"
          ? "out"
          : (previous?.flowsDirection ?? "out"),
    flowsHops: clampFlowHops(
      Number(flows?.hops) ||
        previous?.flowsHops ||
        Number(limits.flows_default_hops) ||
        2,
      limits,
    ),
    flowsRangeDays: Math.max(
      1,
      Number(flows?.range_days) ||
        previous?.flowsRangeDays ||
        Number(limits.flows_default_range_days) ||
        30,
    ),
    flowsMinUsd:
      flows?.min_usd !== undefined && Number.isFinite(Number(flows.min_usd))
        ? Math.max(0, Number(flows.min_usd))
        : (previous?.flowsMinUsd ?? Number(limits.flows_default_min_usd) ?? 10),
    flowsTokens: [...(flows?.tokens ?? previous?.flowsTokens ?? [])],
    flowsIncludeBridges:
      flows?.include_bridges !== undefined
        ? Boolean(flows.include_bridges)
        : (previous?.flowsIncludeBridges ?? true),
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
    case "RESTORE_DRAFT":
      return action.state;
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
    case "SET_TIMELINE_GRAIN":
      return { ...state, timelineGrain: action.grain };
    case "SET_TIMELINE_RANGE":
      return {
        ...state,
        timelineRangeDays: Math.max(1, Math.floor(action.days) || 1),
      };
    case "SET_TIMELINE_WINDOW":
      return {
        ...state,
        timelineWindowBuckets: Math.max(1, Math.floor(action.buckets) || 1),
      };
    case "SET_TIMELINE_PROFILES":
      return { ...state, timelineProfiles: [...action.profiles] };
    case "SET_FLOWS_DIRECTION":
      return { ...state, flowsDirection: action.direction };
    case "SET_FLOWS_HOPS":
      return { ...state, flowsHops: clampFlowHops(action.hops, state.limits) };
    case "SET_FLOWS_RANGE":
      return { ...state, flowsRangeDays: Math.max(1, Math.floor(action.days) || 1) };
    case "SET_FLOWS_MIN_USD":
      return {
        ...state,
        flowsMinUsd: Math.max(0, Number.isFinite(action.minUsd) ? action.minUsd : 0),
      };
    case "SET_FLOWS_TOKENS":
      return { ...state, flowsTokens: [...action.tokens] };
    case "SET_FLOWS_BRIDGES":
      return { ...state, flowsIncludeBridges: action.on };
    default:
      return state;
  }
}
