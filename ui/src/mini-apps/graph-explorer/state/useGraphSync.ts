// Local-reducer ownership of the Graph Explorer view state + debounced bulk
// sync into the app-only `set_graph_explorer_view` tool (clone of metric-lab's
// useChartsSync):
//   - the reducer is the source of truth while the view is open;
//   - local edits debounce (300ms) into ONE bulk patch of the v2 sync schema
//     {mode, layout, semantic_status_filter, atlas.*, investigate.*};
//   - adoption re-keys on the per-dataset revision map (INITIAL_LOAD from
//     seed/expand/atlas-sample bumps revisions → adopt server wholesale);
//   - inbound PATCHes (agent update_graph_explorer_focus) adopt by structural
//     compare of the synced projection, with our own echoes suppressed;
//   - persistence failures are NOT silent: syncError + Retry, local state
//     stays dirty.
//
// Also exposes the stale-response guard for the load/sample/expand calls the
// app fires: `nextRequestId()` before the call, `isCurrent(id)` before firing
// any FOLLOW-UP local state changes (the payload itself is applied by
// useMiniApp regardless).

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { GraphExplorerViewState } from "../types";
import {
  adoptServerState,
  buildInitialState,
  graphReducer,
  type GraphAction,
  type GraphLocalState,
} from "./graphReducer";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

const SYNC_DEBOUNCE_MS = 300;

/** The server-synced projection of local state — exactly the keys
 * set_graph_explorer_view accepts. Selection is NOT synced here (it goes
 * through update_graph_explorer_focus, which also refreshes evidence). */
export function syncedJson(s: GraphLocalState): string {
  return JSON.stringify({
    mode: s.mode,
    layout: s.layout,
    semantic_status_filter: s.statusFilter,
    atlas: {
      selected_profiles: s.atlasProfiles,
      sample_size: s.atlasSampleSize,
      window_days: s.windowDays,
    },
    investigate: {
      active_profiles: s.investigateProfiles,
      window_days: s.windowDays,
      max_neighbors: s.maxNeighbors,
    },
  });
}

export interface GraphSync {
  state: GraphLocalState;
  dispatch: (action: GraphAction) => void;
  /** Last persistence failure (empty = healthy). Local state stays dirty. */
  syncError: string;
  retrySync: () => void;
  /** Stale-response guard for app-fired load/sample/expand calls. */
  nextRequestId: () => number;
  isCurrent: (id: number) => boolean;
}

export function useGraphSync(
  viewId: string | undefined,
  serverState: GraphExplorerViewState | undefined,
  revisionsKey: string,
  callTool: CallTool,
): GraphSync {
  const [state, rawDispatch] = useReducer(
    graphReducer,
    serverState,
    buildInitialState,
  );
  const [syncError, setSyncError] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);

  const lastServerJson = useRef(syncedJson(buildInitialState(serverState)));
  const suppressSync = useRef(true); // don't echo the initial adoption back
  const stateRef = useRef(state);
  stateRef.current = state;

  // New dataset revision (seed / expand / atlas sample / reopen) → adopt the
  // server state wholesale.
  useEffect(() => {
    suppressSync.current = true;
    lastServerJson.current = syncedJson(
      adoptServerState(serverState, stateRef.current),
    );
    rawDispatch({ type: "ADOPT_SERVER", server: serverState });
    setSyncError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revisionsKey]);

  // Inbound reconciliation (agent PATCH while the view is open). Our own
  // sync echoes match lastServerJson and are skipped; anything else (agent
  // mode/layout/profile changes) adopts wholesale — including selection.
  useEffect(() => {
    if (!serverState) return;
    const incoming = syncedJson(adoptServerState(serverState, stateRef.current));
    if (incoming !== lastServerJson.current) {
      lastServerJson.current = incoming;
      suppressSync.current = true;
      rawDispatch({ type: "ADOPT_SERVER", server: serverState });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverState]);

  // Outbound: persist local edits (debounced, full patch).
  const callToolRef = useRef(callTool);
  callToolRef.current = callTool;
  useEffect(() => {
    if (!viewId) return;
    if (suppressSync.current) {
      suppressSync.current = false;
      return;
    }
    const current = syncedJson(state);
    if (current === lastServerJson.current) return;
    const timer = setTimeout(() => {
      // Mark as sent BEFORE the round trip so the PATCH echo (applied to
      // view_state by callTool) is not misread as an agent edit; restore on
      // failure so Retry re-sends.
      const previous = lastServerJson.current;
      lastServerJson.current = current;
      callToolRef
        .current("set_graph_explorer_view", {
          view_id: viewId,
          patch: JSON.parse(current),
        })
        .then(() => setSyncError(""))
        .catch((err) => {
          lastServerJson.current = previous;
          setSyncError(
            err instanceof Error ? err.message : "Failed to save the view state",
          );
        });
    }, SYNC_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, viewId, retryNonce]);

  const dispatch = useCallback((action: GraphAction) => {
    rawDispatch(action);
  }, []);
  const retrySync = useCallback(() => setRetryNonce((n) => n + 1), []);

  // Stale-response guard: a monotonically increasing id. Every load/sample/
  // expand call takes nextRequestId(); follow-up local dispatches only fire
  // when isCurrent(id) — i.e. no newer request superseded this one.
  const requestIdRef = useRef(0);
  const nextRequestId = useCallback(() => {
    requestIdRef.current += 1;
    return requestIdRef.current;
  }, []);
  const isCurrent = useCallback((id: number) => requestIdRef.current === id, []);

  return { state, dispatch, syncError, retrySync, nextRequestId, isCurrent };
}
