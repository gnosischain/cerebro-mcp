import { useEffect, useRef, useState } from "react";
import type { MiniAppPayload, PageRowsResponse } from "./miniAppTypes";

/**
 * Generic mini-app data hook. Mirrors useReportData() but typed against
 * MiniAppPayload<TState> and routing by payload `type`:
 *   - INITIAL_LOAD     → replace the live view
 *   - PATCH_VIEW_STATE → deep-merge `patch` into the existing view_state
 *   - SHOW_WARNING     → append entries to `warnings`
 *
 * Operates in three modes:
 *   1. Dev mode      — uses mockPayload if provided
 *   2. Standalone    — embedded JSON via <script id="mini-app-data">
 *   3. MCP App mode  — connects via @modelcontextprotocol/ext-apps
 *
 * Also exposes `callTool` for app-only hydration tools (`get_mini_app_rows`,
 * `get_mini_app_state`) and a debounced `updateModelContext` helper.
 */

export interface MiniAppHandle<TState> {
  view: MiniAppPayload<TState> | null;
  callTool: <T = unknown>(name: string, args: Record<string, unknown>) => Promise<T | null>;
  fetchRows: (
    viewId: string,
    datasetKey: string,
    pageToken?: string,
  ) => Promise<PageRowsResponse | null>;
  updateModelContext: (lines: Record<string, unknown>) => void;
  sendMessage: (text: string) => Promise<boolean>;
}

interface UseMiniAppOptions<TState> {
  appId: string;
  mockPayload?: MiniAppPayload<TState>;
  modelContextDebounceMs?: number;
}

function deepMerge<T>(base: T, overlay: Partial<T> | Record<string, unknown>): T {
  if (!overlay || typeof overlay !== "object") return base;
  if (typeof base !== "object" || base === null) return overlay as T;
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, value] of Object.entries(overlay as Record<string, unknown>)) {
    const current = out[key];
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      current &&
      typeof current === "object" &&
      !Array.isArray(current)
    ) {
      out[key] = deepMerge(current, value as Record<string, unknown>);
    } else {
      out[key] = value;
    }
  }
  return out as T;
}

function applyPatch<TState>(
  prev: MiniAppPayload<TState>,
  patch: Record<string, unknown> | undefined,
): MiniAppPayload<TState> {
  if (!patch) return prev;
  return {
    ...prev,
    view_state: deepMerge(
      (prev.view_state ?? {}) as TState,
      patch as Partial<TState>,
    ),
  };
}

function pushWarnings<TState>(
  prev: MiniAppPayload<TState>,
  warnings: string[] | undefined,
): MiniAppPayload<TState> {
  if (!warnings || warnings.length === 0) return prev;
  const existing = new Set(prev.warnings ?? []);
  for (const w of warnings) existing.add(w);
  return { ...prev, warnings: Array.from(existing) };
}

function loadEmbedded<TState>(): MiniAppPayload<TState> | null {
  const el = document.getElementById("mini-app-data");
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as MiniAppPayload<TState>;
  } catch (err) {
    console.error("[useMiniApp] failed to parse embedded mini-app-data", err);
    return null;
  }
}

export function useMiniApp<TState = Record<string, unknown>>(
  options: UseMiniAppOptions<TState>,
): MiniAppHandle<TState> {
  const { appId, mockPayload, modelContextDebounceMs = 350 } = options;

  const [view, setView] = useState<MiniAppPayload<TState> | null>(() => {
    const embedded = loadEmbedded<TState>();
    if (embedded) return embedded;
    return null;
  });

  const appRef = useRef<{
    callServerTool?: (
      params: { name: string; arguments?: Record<string, unknown> },
    ) => Promise<{ structuredContent?: unknown; isError?: boolean; content?: unknown[] }>;
    updateModelContext?: (params: { content: { type: string; text: string }[] }) => Promise<void>;
    sendMessage?: (params: {
      role: "user" | "assistant";
      content: { type: "text"; text: string }[];
    }) => Promise<{ isError?: boolean }>;
  } | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Single payload-handling routine. Used by both `ontoolresult`
  // (notifications from host-initiated tool calls) and `callTool`
  // (results from app-initiated tool calls — these come back inline
  // via the Promise return, NOT via the notification channel, so we
  // must apply them here as well or the view never updates).
  const applyPayload = (payload: MiniAppPayload<TState> | undefined) => {
    if (!payload) return;
    if (payload.type === "INITIAL_LOAD") {
      setView(payload);
    } else if (payload.type === "PATCH_VIEW_STATE") {
      setView((prev) =>
        prev ? applyPatch(prev, payload.patch as Record<string, unknown>) : prev,
      );
    } else if (payload.type === "SHOW_WARNING") {
      setView((prev) => (prev ? pushWarnings(prev, payload.warnings) : prev));
    }
  };

  useEffect(() => {
    // Dev mode: load mock payload if no real data is present
    if (import.meta.env.DEV && !view && mockPayload) {
      setView(mockPayload);
      return;
    }

    if (view) return;

    // MCP App mode: connect to ext-apps SDK
    let cancelled = false;
    import("@modelcontextprotocol/ext-apps")
      .then((mod) => {
        if (cancelled) return;
        const app = new mod.App(
          { name: appId, version: "1.0.0" },
          {}, // app capabilities (none needed to call server tools — host advertises serverTools)
        );
        appRef.current = app as unknown as typeof appRef.current;

        app.ontoolresult = (params: { structuredContent?: unknown }) => {
          applyPayload(params.structuredContent as MiniAppPayload<TState> | undefined);
        };

        app.onhostcontextchanged = (ctx: Record<string, unknown>) => {
          const insets = ctx.safeAreaInsets as
            | { top: number; right: number; bottom: number; left: number }
            | undefined;
          if (insets) {
            document.body.style.paddingTop = `${insets.top}px`;
            document.body.style.paddingRight = `${insets.right}px`;
            document.body.style.paddingBottom = `${insets.bottom}px`;
            document.body.style.paddingLeft = `${insets.left}px`;
          }
        };

        app.connect().then(() => {
          const ctx = app.getHostContext?.();
          if (ctx && app.onhostcontextchanged) {
            app.onhostcontextchanged(ctx);
          }
        });
      })
      .catch((err) => {
        console.error("[useMiniApp] ext-apps load failed", err);
      });

    return () => {
      cancelled = true;
    };
  }, [appId, view, mockPayload]);

  const callTool = async <T,>(
    name: string,
    args: Record<string, unknown>,
  ): Promise<T | null> => {
    const app = appRef.current;
    if (!app?.callServerTool) {
      console.warn(`[useMiniApp] callServerTool('${name}') unavailable (no ext-apps host)`);
      throw new Error(
        "Unable to reach the host. Is the app running inside an MCP-aware client?",
      );
    }
    let result;
    try {
      result = await app.callServerTool({ name, arguments: args });
    } catch (err) {
      console.error(`[useMiniApp] callServerTool('${name}') threw`, err);
      throw new Error(
        err instanceof Error ? err.message : `Tool '${name}' failed`,
      );
    }
    if (result?.isError) {
      // Pull the human-readable error text out of the content blocks so
      // callers can show the actual ClickHouse / validation message instead
      // of a misleading "host unreachable" string.
      const blocks = (result.content ?? []) as Array<{ type: string; text?: string }>;
      const text =
        blocks.find((b) => b?.type === "text" && b.text)?.text ||
        `Tool '${name}' returned an error`;
      console.error(`[useMiniApp] tool '${name}' returned isError`, result);
      throw new Error(text.replace(/^Error:\s*/, ""));
    }
    // App-initiated calls do NOT round-trip through `ontoolresult`,
    // so apply the payload directly here. Skip for hydration tools
    // that return paged rows (not full payloads).
    const sc = result?.structuredContent as
      | MiniAppPayload<TState>
      | undefined;
    if (sc && typeof sc === "object" && "type" in sc) {
      applyPayload(sc);
    }
    return (sc as T) ?? null;
  };

  const sendMessage = async (text: string): Promise<boolean> => {
    const app = appRef.current;
    if (!app?.sendMessage) {
      console.warn("[useMiniApp] sendMessage unavailable (no ext-apps host)");
      return false;
    }
    try {
      const result = await app.sendMessage({
        role: "user",
        content: [{ type: "text", text }],
      });
      return !result?.isError;
    } catch (err) {
      console.error("[useMiniApp] sendMessage failed", err);
      return false;
    }
  };

  const fetchRows = async (
    viewId: string,
    datasetKey: string,
    pageToken: string = "",
  ): Promise<PageRowsResponse | null> => {
    // Hydration is best-effort: failures should not crash the UI, so we
    // swallow errors here (callTool now throws on isError).
    try {
      return await callTool<PageRowsResponse>("get_mini_app_rows", {
        view_id: viewId,
        dataset_key: datasetKey,
        page_token: pageToken,
      });
    } catch (err) {
      console.warn("[useMiniApp] fetchRows failed", err);
      return null;
    }
  };

  const updateModelContext = (lines: Record<string, unknown>) => {
    const app = appRef.current;
    if (!app?.updateModelContext) return;
    if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    debounceTimerRef.current = setTimeout(() => {
      const yamlLines: string[] = ["---", `app: ${appId}`];
      for (const [k, v] of Object.entries(lines)) {
        const value =
          v === null || v === undefined
            ? "n/a"
            : typeof v === "object"
              ? JSON.stringify(v)
              : String(v);
        yamlLines.push(`${k}: ${value}`);
      }
      yamlLines.push("---");
      yamlLines.push("User is interacting with this exact view state.");
      app.updateModelContext?.({
        content: [{ type: "text", text: yamlLines.join("\n") }],
      });
    }, modelContextDebounceMs);
  };

  return {
    view,
    callTool,
    fetchRows,
    updateModelContext,
    sendMessage,
  };
}
