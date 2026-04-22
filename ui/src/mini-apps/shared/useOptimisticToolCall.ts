import { useCallback, useRef, useState } from "react";
import type { MiniAppHandle } from "./useMiniApp";

/**
 * Optimistic tool-call hook. Lifts Graph Explorer's "update local state
 * instantly, fire server confirmation" pattern into a reusable primitive
 * and adds proper revert-on-failure (the original .catch(() => {}) at
 * GraphExplorerApp.tsx:177-201 silently swallows rejections).
 *
 * TLocal defaults to `Partial<TState>` so callers pass only the concrete
 * state type: `useOptimisticToolCall<QuarterlyReviewState>(handle, "...")`.
 *
 * Usage:
 *   const { optimistic, apply } = useOptimisticToolCall<MyState>(handle, "update_foo");
 *   const onPick = (v: string) => apply(
 *     { selected: v },                        // local patch, applied immediately
 *     { view_id: view.view_id, selected: v }, // server args
 *   );
 *   const merged = { ...view.view_state, ...optimistic };
 */
export function useOptimisticToolCall<
  TState extends object,
  TLocal extends Partial<TState> = Partial<TState>,
>(handle: MiniAppHandle<TState>, toolName: string) {
  const [optimistic, setOptimistic] = useState<Partial<TLocal>>({});
  const reqId = useRef(0);
  const [pending, setPending] = useState(false);

  const apply = useCallback(
    async (localPatch: Partial<TLocal>, toolArgs: Record<string, unknown>) => {
      const id = ++reqId.current;
      const prev = optimistic;
      setOptimistic((cur) => ({ ...cur, ...localPatch }));
      setPending(true);
      try {
        await handle.callTool(toolName, toolArgs);
        // On success, the server payload replaces view.view_state via useMiniApp's
        // applyPayload, so we can drop the optimistic overlay (but only if this
        // is still the latest request — stale successes must not clobber newer
        // pending optimistic state).
        if (id === reqId.current) setOptimistic({});
      } catch (err) {
        if (id === reqId.current) {
          console.error(
            `[useOptimisticToolCall] '${toolName}' failed; reverting`,
            err,
          );
          setOptimistic(prev);
        }
      } finally {
        if (id === reqId.current) setPending(false);
      }
    },
    [handle, toolName, optimistic],
  );

  const clear = useCallback(() => setOptimistic({}), []);

  return { optimistic, apply, pending, clear } as const;
}
