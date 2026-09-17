// Auto-loader for the chain-state token overlay.
//
// `load_pools_token_metadata` reads symbol / decimals / name over Multicall3
// for the tokens on screen and patches them into `view_state.token_overlay`.
// It is cache-first server-side and cheap, but it is still an RPC round trip:
// this hook fires it ONCE PER SCOPE, not once per render.
//
// The scope key (model/tokenOverlay.ts `overlayScopeKey`) is the load scope
// plus a hash of the visible token set plus the table-page epoch. It must NOT
// be derived from `token_overlay` / `token_overlay_stats`, or applying the
// patch would key a fresh call and loop.

import { useCallback, useEffect, useRef, useState } from "react";

export const TOOL_TOKEN_METADATA = "load_pools_token_metadata";

export type OverlayCallTool = (
  name: string,
  args: Record<string, unknown>,
) => Promise<unknown>;

export interface TokenOverlayLoader {
  loading: boolean;
  /** Client-side failure text (the tool itself never fails for RPC reasons —
   * an unreachable chain comes back as stats.error). */
  error: string;
  /** Warning codes the tool PAYLOAD carried for the latest call. They do not
   * travel in `view_state.warnings`, so they are captured here. */
  warnings: string[];
  /** User-driven retry: bypasses the dedupe and the server's caches. */
  reload: () => void;
}

export function useTokenOverlayLoader(
  callTool: OverlayCallTool,
  viewId: string,
  scopeKey: string,
  enabled: boolean,
): TokenOverlayLoader {
  const requestedRef = useRef("");
  const inFlightRef = useRef(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  // Bumped when a call settles, so a scope that changed WHILE one was in
  // flight gets its own load instead of being silently dropped.
  const [settled, setSettled] = useState(0);

  const run = useCallback(
    (force: boolean): boolean => {
      if (!viewId || inFlightRef.current) return false;
      inFlightRef.current = true;
      setLoading(true);
      setError("");
      void callTool(TOOL_TOKEN_METADATA, {
        view_id: viewId,
        request_id: 0,
        ...(force ? { force_refresh: true } : {}),
      })
        .then((payload) => {
          const codes = (payload as { warnings?: unknown } | null)?.warnings;
          setWarnings(Array.isArray(codes) ? codes.map(String) : []);
        })
        .catch((err: unknown) => {
          // A labelling overlay must never take the panel down with it: the
          // app keeps rendering short addresses and says why.
          console.error(`[${TOOL_TOKEN_METADATA}] failed`, err);
          setError(err instanceof Error ? err.message : "Token metadata could not be read.");
          // The read did not happen at all — the same user-visible state the
          // server reports when the chain is unreachable.
          setWarnings(["token_rpc_unavailable"]);
        })
        .finally(() => {
          inFlightRef.current = false;
          setLoading(false);
          setSettled((n) => n + 1);
        });
      return true;
    },
    [callTool, viewId],
  );

  useEffect(() => {
    if (!enabled || !viewId || !scopeKey) return;
    if (requestedRef.current === scopeKey) return;
    // Claim the scope only once the call actually STARTS. Claiming it while a
    // previous call is still in flight would drop this scope's load entirely —
    // the section would switch and keep the old section's labels.
    if (run(false)) requestedRef.current = scopeKey;
  }, [enabled, viewId, scopeKey, run, settled]);

  const reload = useCallback(() => {
    if (run(true)) requestedRef.current = scopeKey;
  }, [run, scopeKey]);

  return { loading, error, warnings, reload };
}
