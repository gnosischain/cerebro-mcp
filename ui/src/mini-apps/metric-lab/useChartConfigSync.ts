// Chart-config ownership: local state is the source of truth while a dataset
// is loaded; outbound changes debounce into update_metric_lab_chart (fire-and
// -forget persistence so the agent/view store see the same config), inbound
// agent-driven PATCH_VIEW_STATE updates reconcile by structural compare.
//
// Only the server-known fields (x/y/type/agg/groupBy) sync; `y2Field` and
// `colorBy` are frontend-local encodings and never round-trip.

import { useEffect, useRef, useState } from "react";
import type { ChartConfig } from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

const SYNC_DEBOUNCE_MS = 300;

const FALLBACK: ChartConfig = {
  xField: "",
  yField: "",
  chartType: "table",
  aggregation: "sum",
  groupBy: "",
  y2Field: "",
  colorBy: "",
};

/** The server-synced subset, as a stable JSON string for comparison. */
function syncedJson(c: ChartConfig): string {
  return JSON.stringify({
    xField: c.xField,
    yField: c.yField,
    chartType: c.chartType,
    aggregation: c.aggregation,
    groupBy: c.groupBy,
  });
}

export function useChartConfigSync(
  viewId: string | undefined,
  serverConfig: ChartConfig | undefined,
  datasetEpoch: string,
  callTool: CallTool,
  enabled: boolean,
): [ChartConfig, (patch: Partial<ChartConfig>) => void] {
  const [config, setConfig] = useState<ChartConfig>({
    ...FALLBACK,
    ...(serverConfig ?? {}),
  });

  const lastServerJson = useRef(syncedJson({ ...FALLBACK, ...(serverConfig ?? {}) }));
  const suppressSync = useRef(true); // don't echo the initial adoption back

  // New dataset epoch → adopt the server defaults wholesale (local-only
  // encodings reset too; the workspace re-defaults them per epoch).
  useEffect(() => {
    if (serverConfig) {
      suppressSync.current = true;
      lastServerJson.current = syncedJson({ ...FALLBACK, ...serverConfig });
      setConfig({ ...FALLBACK, ...serverConfig });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetEpoch]);

  // Inbound reconciliation (agent PATCH while the view is open). Local-only
  // fields survive — the agent can't see them, so it can't clobber them.
  useEffect(() => {
    if (!serverConfig) return;
    const incoming = syncedJson({ ...FALLBACK, ...serverConfig });
    if (incoming !== lastServerJson.current) {
      lastServerJson.current = incoming;
      suppressSync.current = true;
      setConfig((prev) => ({ ...prev, ...serverConfig }));
    }
  }, [serverConfig]);

  // Outbound: persist local edits of the synced subset (debounced).
  const callToolRef = useRef(callTool);
  callToolRef.current = callTool;
  useEffect(() => {
    if (!enabled || !viewId) return;
    if (suppressSync.current) {
      suppressSync.current = false;
      return;
    }
    const current = syncedJson(config);
    if (current === lastServerJson.current) return;
    const timer = setTimeout(() => {
      lastServerJson.current = current;
      callToolRef
        .current("update_metric_lab_chart", {
          view_id: viewId,
          x_field: config.xField,
          y_field: config.yField,
          chart_type: config.chartType,
          aggregation: config.aggregation,
          group_by: config.groupBy,
        })
        .catch(() => {
          /* persistence is best-effort; the chart already rendered locally */
        });
    }, SYNC_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [config, enabled, viewId]);

  const update = (patch: Partial<ChartConfig>) =>
    setConfig((prev) => ({ ...prev, ...patch }));

  return [config, update];
}
