// Serialized "latest snapshot wins" loader for app-fired dataset loads
// (timeline / flows). Guarantees:
//  - at most ONE request in flight (useMiniApp.callTool applies every
//    returned payload, so never-concurrent => payloads apply in request
//    order by construction);
//  - a request enqueued while one is in flight replaces any previously
//    pending one — only the NEWEST fires afterwards;
//  - the pending slot stores the COMPLETE argument snapshot built at
//    enqueue time. No partial-merge, no re-entry through stale closures
//    (the bug this replaces: a pending retry re-invoked the original
//    closure and could pair a new range with an old grain).

import { useCallback, useEffect, useRef, useState } from "react";

export type SerializedRequest<TArgs extends object> = Omit<TArgs, "request_id"> & {
  request_id: number;
};

export interface SerializedLoader<TArgs extends object> {
  /** Fire (or queue) a fully-resolved request snapshot and return the id that
   * was allocated NOW, before it can wait behind an in-flight request. */
  enqueue: (snapshot: Omit<TArgs, "request_id">) => number;
  /** True from first enqueue until the queue fully drains. */
  loading: boolean;
  /** Newest user intent, including a request still waiting in the queue. */
  desiredRequestId: number;
  /** Request currently on the wire; null while idle. */
  activeRequestId: number | null;
  /** Last request failure. Cleared when the next queued request starts. */
  error: string | null;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err || "Request failed");
}

export function useSerializedLoader<TArgs extends object>(
  send: (snapshot: SerializedRequest<TArgs>) => Promise<unknown>,
  onError?: (err: unknown) => void,
  initialRequestId = 0,
): SerializedLoader<TArgs> {
  const sendRef = useRef(send);
  sendRef.current = send;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const seqRef = useRef(Math.max(0, Math.floor(initialRequestId) || 0));
  const inFlightRef = useRef(false);
  const pendingRef = useRef<{
    snapshot: Omit<TArgs, "request_id">;
    requestId: number;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [desiredRequestId, setDesiredRequestId] = useState(seqRef.current);
  const [activeRequestId, setActiveRequestId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The view payload commonly arrives after this hook's first render. Adopt a
  // newer server revision while idle so a remounted client never reuses an old
  // focus id and gets rejected by the server's stale-write guard.
  useEffect(() => {
    const serverId = Math.max(0, Math.floor(initialRequestId) || 0);
    if (!inFlightRef.current && serverId > seqRef.current) {
      seqRef.current = serverId;
      setDesiredRequestId(serverId);
    }
  }, [initialRequestId]);

  const run = useCallback((pending: {
    snapshot: Omit<TArgs, "request_id">;
    requestId: number;
  }) => {
    inFlightRef.current = true;
    setLoading(true);
    setActiveRequestId(pending.requestId);
    setError(null);
    const request = {
      ...pending.snapshot,
      // Always overwrite a caller-supplied index-signature value. The loader
      // alone owns attribution.
      request_id: pending.requestId,
    } as SerializedRequest<TArgs>;
    sendRef
      .current(request)
      .catch((err) => {
        setError(errorMessage(err));
        onErrorRef.current?.(err);
      })
      .finally(() => {
        inFlightRef.current = false;
        setActiveRequestId(null);
        const next = pendingRef.current;
        pendingRef.current = null;
        if (next) run(next);
        else setLoading(false);
      });
  }, []);

  const enqueue = useCallback(
    (snapshot: Omit<TArgs, "request_id">) => {
      const requestId = ++seqRef.current;
      setDesiredRequestId(requestId);
      const pending = { snapshot, requestId };
      if (inFlightRef.current) {
        // Replace outright — snapshots are complete, newest wins.
        pendingRef.current = pending;
        return requestId;
      }
      run(pending);
      return requestId;
    },
    [run],
  );

  return {
    enqueue,
    loading,
    desiredRequestId,
    activeRequestId,
    error,
  };
}
