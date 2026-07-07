import { useState, useEffect } from "react";
import type { ReportData } from "../types";

/** How long to wait for the host to deliver report data before showing an
 * actionable fallback instead of an endless spinner. Some MCP hosts (e.g.
 * Claude Desktop via an `mcp-remote` bridge) don't complete the ext-apps
 * tool-result handshake, which previously left the panel spinning forever. */
const LOAD_TIMEOUT_MS = 10000;

export interface ReportDataState {
  /** The report payload once available, else null. */
  data: ReportData | null;
  /** True once the load timeout elapsed (or the ext-apps connect failed)
   * without any data arriving — the caller should render a fallback. */
  timedOut: boolean;
}

/**
 * Triple-mode report data hook:
 *   1. Dev mode:        Returns mock data from dev-data.ts
 *   2. Standalone mode: Parses embedded <script id="report-data"> JSON
 *   3. MCP App mode:    Awaits structuredContent from ext-apps SDK
 *
 * In MCP App mode, if no data arrives within LOAD_TIMEOUT_MS (host doesn't
 * forward the tool result), `timedOut` flips true so the UI can offer the
 * "Open Report" link instead of spinning indefinitely.
 */
export function useReportData(): ReportDataState {
  const [data, setData] = useState<ReportData | null>(() => {
    // Check for standalone mode: embedded JSON in the page
    const el = document.getElementById("report-data");
    if (el?.textContent) {
      try {
        return JSON.parse(el.textContent) as ReportData;
      } catch {
        console.error("[useReportData] Failed to parse embedded report-data");
      }
    }
    return null;
  });
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    // In dev mode, load mock data
    if (import.meta.env.DEV && !data) {
      import("../dev-data").then((mod) => {
        const wantResearch =
          typeof window !== "undefined" &&
          new URLSearchParams(window.location.search).get("mode") ===
            "research";
        setData(
          wantResearch ? mod.DEV_RESEARCH_REPORT_DATA : mod.DEV_REPORT_DATA
        );
      });
      return;
    }

    // In production, if no embedded data, connect via MCP ext-apps SDK
    if (!data) {
      let cancelled = false;

      // Guard against a host that never delivers the tool result: after the
      // timeout, surface a fallback instead of an infinite spinner.
      const timer = setTimeout(() => {
        if (!cancelled) setTimedOut(true);
      }, LOAD_TIMEOUT_MS);

      import("@modelcontextprotocol/ext-apps")
        .then((mod) => {
          const app = new mod.App({
            name: "Cerebro Report",
            version: "1.0.0",
          });

          app.ontoolresult = (params) => {
            if (params.structuredContent) {
              setData(params.structuredContent as unknown as ReportData);
            }
          };

          app.onhostcontextchanged = (ctx) => {
            const insets = (ctx as Record<string, unknown>).safeAreaInsets as
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
            const ctx = app.getHostContext();
            if (ctx && app.onhostcontextchanged) {
              app.onhostcontextchanged(ctx);
            }
          });
        })
        .catch((err) => {
          console.error("[useReportData] ext-apps connect failed", err);
          if (!cancelled) setTimedOut(true);
        });

      return () => {
        cancelled = true;
        clearTimeout(timer);
      };
    }
  }, [data]);

  return { data, timedOut };
}
