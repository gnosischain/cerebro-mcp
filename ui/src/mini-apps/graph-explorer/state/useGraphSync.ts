// Local-reducer ownership of the Graph Explorer view state + debounced
// persistence of SAFE visual preferences into the app-only
// `set_graph_explorer_view` tool:
//   - the reducer is the source of truth while the view is open;
//   - layout/status edits debounce (300ms) into one visual-only patch;
//   - mode and data-backed controls are deliberately excluded. Their loaders
//     own scope acceptance, so a draft can never relabel already-applied data;
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
type SyncedPreferences = Pick<GraphLocalState, "layout" | "statusFilter">;

/** The only state this generic persistence channel may write. Mode and all
 * server-backed controls are intentionally absent: mode/selection go through
 * update_graph_explorer_focus, and each data loader commits its own complete
 * scoped snapshot. Keeping this projection narrow also prevents a harmless
 * visual edit from clearing selection via a mode-bearing bulk patch. */
export function syncedJson(s: GraphLocalState): string {
  return JSON.stringify({
    layout: s.layout,
    semantic_status_filter: s.statusFilter,
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
  const failedDraftRef = useRef<SyncedPreferences | null>(null);

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

  // Inbound visual reconciliation (agent PATCH while the view is open). Data
  // namespace adoption is driven by revisionsKey above, after the matching
  // loader has published a dataset/scope revision.
  useEffect(() => {
    if (!serverState) return;
    const adopted = adoptServerState(serverState, stateRef.current);
    const incoming = syncedJson(adopted);
    if (incoming !== lastServerJson.current) {
      lastServerJson.current = incoming;
      suppressSync.current = true;
      rawDispatch({
        type: "RESTORE_DRAFT",
        state: {
          ...stateRef.current,
          layout: adopted.layout,
          statusFilter: adopted.statusFilter,
        },
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverState]);

  // Outbound: persist visual edits only. The effect still observes the whole
  // reducer state, but a mode/profile/window/flow/timeline draft produces the
  // same projection and therefore no network call.
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
      // view_state by callTool) is not misread as an agent edit. On failure
      // retain this visual draft for Retry and visibly roll it back.
      lastServerJson.current = current;
      callToolRef
        .current("set_graph_explorer_view", {
          view_id: viewId,
          patch: JSON.parse(current),
        })
        .then(() => setSyncError(""))
        .catch((err) => {
          failedDraftRef.current = {
            layout: state.layout,
            statusFilter: state.statusFilter,
          };
          suppressSync.current = true;
          const applied = adoptServerState(serverState, state);
          lastServerJson.current = syncedJson(applied);
          rawDispatch({
            type: "RESTORE_DRAFT",
            state: {
              ...state,
              layout: applied.layout,
              statusFilter: applied.statusFilter,
            },
          });
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
  const retrySync = useCallback(() => {
    const failedDraft = failedDraftRef.current;
    if (failedDraft) {
      failedDraftRef.current = null;
      rawDispatch({
        type: "RESTORE_DRAFT",
        state: { ...stateRef.current, ...failedDraft },
      });
    }
    setSyncError("");
    setRetryNonce((n) => n + 1);
  }, []);

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
