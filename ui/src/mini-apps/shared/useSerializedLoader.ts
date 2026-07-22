// Shared serialized "latest snapshot wins" loader.  App-fired mini-app tool
// results are applied immediately by useMiniApp, so scope-changing loads must
// never race.  At most one request is in flight; a newer queued snapshot
// replaces any older pending snapshot.

import { useCallback, useEffect, useRef, useState } from "react";

export type SerializedRequest<TArgs extends object> = Omit<TArgs, "request_id"> & {
  request_id: number;
};

export interface SerializedLoader<TArgs extends object> {
  enqueue: (snapshot: Omit<TArgs, "request_id">) => number;
  loading: boolean;
  desiredRequestId: number;
  activeRequestId: number | null;
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
      request_id: pending.requestId,
    } as SerializedRequest<TArgs>;
    sendRef.current(request).catch((err) => {
      setError(errorMessage(err));
      onErrorRef.current?.(err);
    }).finally(() => {
      inFlightRef.current = false;
      setActiveRequestId(null);
      const next = pendingRef.current;
      pendingRef.current = null;
      if (next) run(next);
      else setLoading(false);
    });
  }, []);

  const enqueue = useCallback((snapshot: Omit<TArgs, "request_id">) => {
    const requestId = ++seqRef.current;
    setDesiredRequestId(requestId);
    const pending = { snapshot, requestId };
    if (inFlightRef.current) pendingRef.current = pending;
    else run(pending);
    return requestId;
  }, [run]);

  return { enqueue, loading, desiredRequestId, activeRequestId, error };
}
