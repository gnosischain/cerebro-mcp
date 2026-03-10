import { useState, useEffect } from "react";
import type { ReportData } from "../types";

/**
 * Triple-mode report data hook:
 *   1. Dev mode:        Returns mock data from dev-data.ts
 *   2. Standalone mode: Parses embedded <script id="report-data"> JSON
 *   3. MCP App mode:    Awaits structuredContent from ext-apps SDK
 */
export function useReportData(): ReportData | null {
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

  useEffect(() => {
    // In dev mode, load mock data
    if (import.meta.env.DEV && !data) {
      import("../dev-data").then((mod) => {
        setData(mod.DEV_REPORT_DATA);
      });
      return;
    }

    // In production, if no embedded data, connect via MCP ext-apps SDK
    if (!data) {
      import("@modelcontextprotocol/ext-apps").then((mod) => {
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
      });
    }
  }, [data]);

  return data;
}
