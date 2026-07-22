// Deferred dataset-group loader for CoW Explorer v2.
//
// The section apply (`load_cow_explorer_section`) only loads a section's
// `core` group; every other group streams in afterwards through the app-only
// `load_cow_explorer_datasets` tool. This hook drives that streaming:
//
//   - at most MAX_IN_FLIGHT group calls run concurrently;
//   - each request carries the scope_id it was queued under, so the server
//     no-ops late arrivals for a superseded scope (`stale_scope`);
//   - a failed group is remembered per scope and NOT retried automatically —
//     `retry()` clears the marker (wired to a retry button);
//   - completion bumps a local tick so the caller's sync effect re-runs even
//     when a failure produced no view-state change.

import { useCallback, useRef, useState } from "react";

const MAX_IN_FLIGHT = 2;

export type GroupCallTool = (
  name: string,
  args: Record<string, unknown>,
) => Promise<unknown>;

export interface GroupLoader {
  /** Enqueue whatever `groups` still need loading for `section`. */
  sync: (viewId: string, section: string, groups: string[], scopeId: string) => void;
  /** Clear a failure marker so the next sync retries the group. */
  retry: (section: string, group: string, scopeId: string) => void;
  /** Clear EVERY failure marker for a scope — the Live poll calls this each
   * cycle so a transient failure never permanently freezes a feed. */
  retryAll: (scopeId: string) => void;
  /** `${section}.${group}` keys that failed under the CURRENT scope. */
  failedGroups: (scopeId: string) => string[];
  /** Monotonic counter bumped on every settle — a cheap effect dependency. */
  tick: number;
}

export function useGroupLoader(callTool: GroupCallTool): GroupLoader {
  const inFlightRef = useRef<Set<string>>(new Set());
  const failedRef = useRef<Set<string>>(new Set());
  const [tick, setTick] = useState(0);

  const sync = useCallback(
    (viewId: string, section: string, groups: string[], scopeId: string) => {
      for (const group of groups) {
        const key = `${scopeId}|${section}.${group}`;
        if (inFlightRef.current.has(key) || failedRef.current.has(key)) continue;
        if (inFlightRef.current.size >= MAX_IN_FLIGHT) return;
        inFlightRef.current.add(key);
        callTool("load_cow_explorer_datasets", {
          view_id: viewId,
          request_id: 0,
          section,
          group,
          scope_id: scopeId,
        })
          .catch((err) => {
            console.error(`[cow_explorer] group ${section}.${group} failed`, err);
            failedRef.current.add(key);
          })
          .finally(() => {
            inFlightRef.current.delete(key);
            setTick((n) => n + 1);
          });
      }
    },
    [callTool],
  );

  const retry = useCallback((section: string, group: string, scopeId: string) => {
    failedRef.current.delete(`${scopeId}|${section}.${group}`);
    setTick((n) => n + 1);
  }, []);

  const retryAll = useCallback((scopeId: string) => {
    let cleared = false;
    for (const key of Array.from(failedRef.current)) {
      if (key.startsWith(`${scopeId}|`)) {
        failedRef.current.delete(key);
        cleared = true;
      }
    }
    if (cleared) setTick((n) => n + 1);
  }, []);

  const failedGroups = useCallback(
    (scopeId: string) =>
      Array.from(failedRef.current)
        .filter((key) => key.startsWith(`${scopeId}|`))
        .map((key) => key.slice(scopeId.length + 1)),
    [],
  );

  return { sync, retry, retryAll, failedGroups, tick };
}
