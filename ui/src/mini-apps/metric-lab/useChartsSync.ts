// Chart-GRID ownership (replaces the single-config useChartConfigSync):
// local ChartPanelConfig[] is the source of truth while a dataset is loaded;
// edits/add/remove/move debounce into the app-only set_metric_lab_charts bulk
// tool (full array); inbound server changes (agent update_metric_lab_chart)
// adopt wholesale by structural compare. Adoption re-keys on the per-dataset
// revision map — NOT on SQL text, which stays identical across forced reruns.
//
// Persistence failures are NOT silent: syncError surfaces in the UI with a
// Retry, and the local (dirty) panels are kept.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  MAX_CHART_PANELS,
  normalizeChartConfig,
  type ChartConfig,
  type ChartPanelConfig,
} from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

const SYNC_DEBOUNCE_MS = 300;

const FALLBACK_PANEL: ChartPanelConfig = {
  id: "c1",
  datasetKey: "primary",
  xField: "",
  yField: "",
  chartType: "table",
  aggregation: "sum",
  groupBy: "",
};

export function nextPanelId(panels: ChartPanelConfig[]): string {
  let max = 0;
  for (const p of panels) {
    const m = /^c(\d+)$/.exec(p.id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return `c${max + 1}`;
}

/** Wrap a legacy scalar `chart` (or nothing) into a one-panel grid. */
export function panelsFromLegacy(
  chart: ChartConfig | undefined,
): ChartPanelConfig[] {
  return [normalizeChartConfig({ ...FALLBACK_PANEL, ...(chart ?? {}) })];
}

export type PanelsAction =
  | { type: "adopt"; panels: ChartPanelConfig[] }
  | { type: "edit"; id: string; patch: Partial<ChartPanelConfig> }
  | { type: "add"; datasetKey?: string }
  | { type: "duplicate"; id: string }
  | { type: "remove"; id: string }
  | { type: "move"; id: string; dir: -1 | 1 };

/** Pure grid reducer (exported for unit tests). Invariants enforced: >=1
 * panel, <= MAX_CHART_PANELS, unique ids, normalized yFields/mirrors. */
export function panelsReducer(
  state: ChartPanelConfig[],
  action: PanelsAction,
): ChartPanelConfig[] {
  switch (action.type) {
    case "adopt": {
      const panels = action.panels.map((p) => normalizeChartConfig(p));
      return panels.length ? panels : [FALLBACK_PANEL];
    }
    case "edit": {
      return state.map((p) => {
        if (p.id !== action.id) return p;
        const patch = action.patch;
        let next: ChartPanelConfig = { ...p, ...patch, id: p.id };
        if (!("yFields" in patch) && ("yField" in patch || "y2Field" in patch)) {
          // Editing the scalar mirrors via the controls takes MANUAL control
          // of the plotted list — extras beyond [0]/[1] are dropped, which
          // is the predictable reading of "the chart plots what I picked".
          next = {
            ...next,
            yFields: [next.yField, ...(next.y2Field ? [next.y2Field] : [])].filter(
              Boolean,
            ),
          };
        }
        return normalizeChartConfig(next);
      });
    }
    case "add": {
      if (state.length >= MAX_CHART_PANELS) return state;
      const template = state[state.length - 1] ?? FALLBACK_PANEL;
      return [
        ...state,
        {
          ...template,
          id: nextPanelId(state),
          title: undefined,
          ...(action.datasetKey ? { datasetKey: action.datasetKey } : {}),
        },
      ];
    }
    case "duplicate": {
      if (state.length >= MAX_CHART_PANELS) return state;
      const source = state.find((p) => p.id === action.id);
      if (!source) return state;
      const idx = state.indexOf(source);
      const copy = { ...source, id: nextPanelId(state) };
      return [...state.slice(0, idx + 1), copy, ...state.slice(idx + 1)];
    }
    case "remove": {
      if (state.length <= 1) return state; // grid never goes empty
      return state.filter((p) => p.id !== action.id);
    }
    case "move": {
      const idx = state.findIndex((p) => p.id === action.id);
      const to = idx + action.dir;
      if (idx < 0 || to < 0 || to >= state.length) return state;
      const next = [...state];
      const [moved] = next.splice(idx, 1);
      next.splice(to, 0, moved);
      return next;
    }
    default:
      return state;
  }
}

/** The server-synced projection of the grid, as a stable JSON string. */
function syncedJson(panels: ChartPanelConfig[]): string {
  return JSON.stringify(
    panels.map((p) => ({
      id: p.id,
      datasetKey: p.datasetKey,
      xField: p.xField,
      yField: p.yField,
      y2Field: p.y2Field ?? "",
      yFields: p.yFields ?? [],
      chartType: p.chartType,
      aggregation: p.aggregation,
      groupBy: p.groupBy,
      title: p.title ?? "",
      sortDir: p.sortDir ?? "",
      trendline: Boolean(p.trendline),
      colorBy: p.colorBy ?? "",
    })),
  );
}

export interface ChartsSync {
  panels: ChartPanelConfig[];
  dispatch: (action: PanelsAction) => void;
  /** Last persistence failure (empty = healthy). Panels stay locally dirty. */
  syncError: string;
  retrySync: () => void;
}

export function useChartsSync(
  viewId: string | undefined,
  serverPanels: ChartPanelConfig[] | undefined,
  legacyChart: ChartConfig | undefined,
  revisionsKey: string,
  callTool: CallTool,
  enabled: boolean,
): ChartsSync {
  const initial = serverPanels?.length
    ? serverPanels.map((p) => normalizeChartConfig(p))
    : panelsFromLegacy(legacyChart);
  const [panels, rawDispatch] = useReducer(panelsReducer, initial);
  const [syncError, setSyncError] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);

  const lastServerJson = useRef(syncedJson(initial));
  const suppressSync = useRef(true); // don't echo the initial adoption back

  // New dataset revision → adopt the server panels wholesale.
  useEffect(() => {
    const incoming = serverPanels?.length
      ? serverPanels
      : panelsFromLegacy(legacyChart);
    suppressSync.current = true;
    lastServerJson.current = syncedJson(
      incoming.map((p) => normalizeChartConfig(p)),
    );
    rawDispatch({ type: "adopt", panels: incoming });
    setSyncError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revisionsKey]);

  // Inbound reconciliation (agent PATCH while the view is open).
  useEffect(() => {
    if (!serverPanels?.length) return;
    const incoming = syncedJson(serverPanels.map((p) => normalizeChartConfig(p)));
    if (incoming !== lastServerJson.current) {
      lastServerJson.current = incoming;
      suppressSync.current = true;
      rawDispatch({ type: "adopt", panels: serverPanels });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverPanels]);

  // Outbound: persist local edits (debounced, full array).
  const callToolRef = useRef(callTool);
  callToolRef.current = callTool;
  useEffect(() => {
    if (!enabled || !viewId) return;
    if (suppressSync.current) {
      suppressSync.current = false;
      return;
    }
    const current = syncedJson(panels);
    if (current === lastServerJson.current) return;
    const timer = setTimeout(() => {
      // Mark as sent BEFORE the round trip so the PATCH echo (applied to
      // view_state by callTool) is not misread as an agent edit; restore on
      // failure so Retry re-sends.
      const previous = lastServerJson.current;
      lastServerJson.current = current;
      callToolRef
        .current("set_metric_lab_charts", {
          view_id: viewId,
          charts: JSON.parse(current),
        })
        .then(() => setSyncError(""))
        .catch((err) => {
          // NOT silent: the chart already rendered locally, but the user
          // must know the layout did not persist (agent/panel views would
          // disagree). Local state stays dirty; Retry re-sends.
          lastServerJson.current = previous;
          setSyncError(
            err instanceof Error ? err.message : "Failed to save the chart grid",
          );
        });
    }, SYNC_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panels, enabled, viewId, retryNonce]);

  const dispatch = useCallback((action: PanelsAction) => {
    rawDispatch(action);
  }, []);
  const retrySync = useCallback(() => setRetryNonce((n) => n + 1), []);

  return { panels, dispatch, syncError, retrySync };
}
