// The chart-panel grid: 1-12 independent panels, each with its own type/
// axes/aggregation config over any attached dataset. Reuses ChartControls
// (collapsible per panel) and ChartPanel/DataTableTab unchanged.

import { useState } from "react";
import type { DatasetDescriptor } from "../shared/miniAppTypes";
import { ChartControls } from "./ChartControls";
import { ChartPanel } from "./ChartPanel";
import { DataTableTab } from "./DataTableTab";
import { isNumericColumn } from "./analysis";
import type { MergedDual } from "./chartOptions";
import { MAX_CHART_PANELS, type ChartPanelConfig } from "./types";
import type { PanelsAction } from "./useChartsSync";
import type { HydratedDataset } from "../shared/useHydratedDatasets";

interface ChartGridProps {
  panels: ChartPanelConfig[];
  dispatch: (action: PanelsAction) => void;
  datasets: Record<string, HydratedDataset>;
  descriptors: Record<string, DatasetDescriptor>;
  /** Merged dual-table shape — only ever offered to panels on `primary`
   * while a legacy raw compare is loaded. */
  mergedDual?: MergedDual | null;
  syncError: string;
  onRetrySync: () => void;
}

/** Repair a panel's fields for a dataset switch: keep what still exists,
 * default the rest from the target dataset's shape. */
export function repairPanelForDataset(
  panel: ChartPanelConfig,
  datasetKey: string,
  dataset: HydratedDataset,
): Partial<ChartPanelConfig> {
  const cols = dataset.columns;
  const numeric = cols.filter((_, i) => isNumericColumn(dataset.rows, i));
  const keep = (field: string | undefined) =>
    field && cols.includes(field) ? field : "";
  const yFields = (panel.yFields ?? []).filter((f) => cols.includes(f));
  return {
    datasetKey,
    xField: keep(panel.xField) || cols[0] || "",
    yFields: yFields.length ? yFields : numeric.slice(0, 1),
    yField: "",
    y2Field: undefined,
    groupBy: keep(panel.groupBy),
    colorBy: keep(panel.colorBy),
  };
}

export function ChartGrid({
  panels,
  dispatch,
  datasets,
  descriptors,
  mergedDual,
  syncError,
  onRetrySync,
}: ChartGridProps) {
  const datasetKeys = Object.keys(datasets);
  const [collapsedControls, setCollapsedControls] = useState<Record<string, boolean>>({});

  return (
    <section className="mlab-grid-wrap" aria-label="Chart panels">
      {syncError && (
        <div className="mlab-error mlab-sync-error" role="alert">
          Chart layout not saved: {syncError}{" "}
          <button type="button" className="mlab-toggle" onClick={onRetrySync}>
            Retry
          </button>
        </div>
      )}

      <div className={`mlab-grid${panels.length > 1 ? " is-multi" : ""}`}>
        {panels.map((panel, idx) => {
          const data = datasets[panel.datasetKey];
          const descriptor = descriptors[panel.datasetKey];
          const numericColumns = data
            ? data.columns.filter((_, i) => isNumericColumn(data.rows, i))
            : [];
          const categoricalColumns = data
            ? data.columns.filter((_, i) => !isNumericColumn(data.rows, i))
            : [];
          const previewOnly = descriptor?.stats?.mode === "preview_only";
          const controlsCollapsed = collapsedControls[panel.id] ?? false;

          return (
            <div key={panel.id} className="mlab-panel" data-panel-id={panel.id}>
              <header className="mlab-panel-head">
                <input
                  className="mlab-panel-title"
                  value={panel.title ?? ""}
                  placeholder={`Chart ${idx + 1}`}
                  maxLength={200}
                  onChange={(e) =>
                    dispatch({
                      type: "edit",
                      id: panel.id,
                      patch: { title: e.target.value || undefined },
                    })
                  }
                />
                {datasetKeys.length > 1 && (
                  <select
                    className="mlab-panel-dataset"
                    title="Dataset this panel plots"
                    value={panel.datasetKey}
                    onChange={(e) => {
                      const key = e.target.value;
                      const target = datasets[key];
                      if (!target) return;
                      dispatch({
                        type: "edit",
                        id: panel.id,
                        patch: repairPanelForDataset(panel, key, target),
                      });
                    }}
                  >
                    {datasetKeys.map((k) => (
                      <option key={k} value={k}>
                        {descriptors[k]?.title || k}
                      </option>
                    ))}
                  </select>
                )}
                <div className="mlab-panel-actions">
                  <button
                    type="button"
                    className="mlab-toggle"
                    title="Show/hide chart controls"
                    onClick={() =>
                      setCollapsedControls((prev) => ({
                        ...prev,
                        [panel.id]: !controlsCollapsed,
                      }))
                    }
                  >
                    {controlsCollapsed ? "controls" : "hide"}
                  </button>
                  <button
                    type="button"
                    className="mlab-toggle"
                    title="Move left"
                    disabled={idx === 0}
                    onClick={() => dispatch({ type: "move", id: panel.id, dir: -1 })}
                  >
                    ◀
                  </button>
                  <button
                    type="button"
                    className="mlab-toggle"
                    title="Move right"
                    disabled={idx === panels.length - 1}
                    onClick={() => dispatch({ type: "move", id: panel.id, dir: 1 })}
                  >
                    ▶
                  </button>
                  <button
                    type="button"
                    className="mlab-toggle"
                    title="Duplicate this panel"
                    disabled={panels.length >= MAX_CHART_PANELS}
                    onClick={() => dispatch({ type: "duplicate", id: panel.id })}
                  >
                    ⧉
                  </button>
                  <button
                    type="button"
                    className="mlab-toggle"
                    title={panels.length <= 1 ? "The last panel cannot be removed" : "Remove this panel"}
                    disabled={panels.length <= 1}
                    onClick={() => dispatch({ type: "remove", id: panel.id })}
                  >
                    ×
                  </button>
                </div>
              </header>

              {!controlsCollapsed && data && (
                <ChartControls
                  columns={data.columns}
                  numericColumns={numericColumns}
                  categoricalColumns={categoricalColumns}
                  config={panel}
                  onChange={(patch) => dispatch({ type: "edit", id: panel.id, patch })}
                  sortAsc={panel.sortDir !== "desc"}
                  onSortToggle={() =>
                    dispatch({
                      type: "edit",
                      id: panel.id,
                      patch: { sortDir: panel.sortDir === "desc" ? "asc" : "desc" },
                    })
                  }
                  trendline={panel.trendline ?? true}
                  onTrendlineToggle={() =>
                    dispatch({
                      type: "edit",
                      id: panel.id,
                      patch: { trendline: !(panel.trendline ?? true) },
                    })
                  }
                  previewOnly={previewOnly}
                />
              )}

              {!data ? (
                <div className="mlab-chart-empty">
                  Dataset "{panel.datasetKey}" is not loaded.
                </div>
              ) : panel.chartType === "table" ? (
                <DataTableTab
                  rows={data.rows}
                  columns={data.columns}
                  totalAvailable={descriptor?.stats?.row_count ?? data.rows.length}
                  hydrating={data.hydrating}
                />
              ) : (
                <ChartPanel
                  rows={data.rows}
                  columns={data.columns}
                  config={panel}
                  sortAsc={panel.sortDir !== "desc"}
                  trendline={panel.trendline ?? true}
                  sql={descriptor?.sql}
                  sourceModel={descriptor?.title}
                  mergedDual={panel.datasetKey === "primary" ? mergedDual : null}
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="mlab-grid-actions">
        <button
          type="button"
          className="mlab-toggle"
          disabled={panels.length >= MAX_CHART_PANELS}
          title={
            panels.length >= MAX_CHART_PANELS
              ? `Panel cap reached (${MAX_CHART_PANELS})`
              : "Add another chart panel"
          }
          onClick={() => dispatch({ type: "add" })}
        >
          + Add chart
        </button>
      </div>
    </section>
  );
}
