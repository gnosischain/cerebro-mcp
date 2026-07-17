// The loaded-data workspace — one fluid page, nothing hidden behind tabs:
//   KPI cards → chart-panel GRID (1-12 independent panels) → correlations &
//   stats (per selected dataset) → data table.
// Multi-metric loads arrive with server defaults already on the first panel
// (yFields = every value column); the user reshapes panels from there.

import { useMemo, useState } from "react";
import { CollapsibleSection } from "../shared/CollapsibleSection";
import { SummaryCards } from "../shared/SummaryCards";
import { WarningBanner } from "../shared/WarningBanner";
import type { DatasetDescriptor, SummaryCard } from "../shared/miniAppTypes";
import { AnalysisSection } from "./AnalysisSection";
import { ChartGrid } from "./ChartGrid";
import { DataTableTab } from "./DataTableTab";
import { SqlSection } from "./SqlSection";
import { mergeDualDatasets, type MergedDual } from "./chartOptions";
import type { ChartPanelConfig, ChartSuggestion, MetricLabState } from "./types";
import type { PanelsAction } from "./useChartsSync";
import type { HydratedDataset } from "../shared/useHydratedDatasets";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

interface WorkspaceSectionProps {
  state: MetricLabState;
  summaryCards: SummaryCard[];
  datasets: Record<string, HydratedDataset>;
  descriptors: Record<string, DatasetDescriptor>;
  panels: ChartPanelConfig[];
  dispatch: (action: PanelsAction) => void;
  syncError: string;
  onRetrySync: () => void;
  viewId: string | undefined;
  callTool: CallTool;
}

export function WorkspaceSection({
  state,
  summaryCards,
  datasets,
  descriptors,
  panels,
  dispatch,
  syncError,
  onRetrySync,
  viewId,
  callTool,
}: WorkspaceSectionProps) {
  const primary = datasets.primary;
  const primaryDescriptor = descriptors.primary;
  const secondary = datasets.secondary ?? null;
  const secondaryDescriptor = descriptors.secondary;

  const metricFields = state.metric_fields ?? [];
  const suggestions = state.chart_suggestions ?? [];
  const unvalidated = state.unvalidated_metrics ?? [];

  // Legacy dual raw compare: merge the two tables on their date columns so
  // primary-panel line/bar charts can overlay them on two axes.
  const mergedDual: MergedDual | null = useMemo(() => {
    if (!secondary || !secondaryDescriptor || !primary || !primaryDescriptor) {
      return null;
    }
    return mergeDualDatasets(
      { rows: primary.rows, columns: primary.columns, title: primaryDescriptor.title },
      { rows: secondary.rows, columns: secondary.columns, title: secondaryDescriptor.title },
    );
  }, [primary, secondary, primaryDescriptor, secondaryDescriptor]);

  const warnings: string[] = [];
  if (unvalidated.length > 0) {
    warnings.push(
      `Unvalidated metric${unvalidated.length > 1 ? "s" : ""} (candidate tier): ${unvalidated.join(", ")} — definitions are not yet approved, treat results as estimates.`,
    );
  }
  if (state.estimates) {
    warnings.push(
      "Dataset is a deterministic random sample — values are estimates of the full data.",
    );
  }
  if (primary && !primary.hydrating && primary.rows.length === 1) {
    warnings.push(
      "This model returned a single row — it is likely a snapshot/aggregate "
        + "shape rather than a time series (check its columns in Details).",
    );
  }

  const firstPanelId = panels[0]?.id;
  const applySuggestion = (s: ChartSuggestion) => {
    if (!firstPanelId) return;
    dispatch({
      type: "edit",
      id: firstPanelId,
      patch: {
        chartType: s.chartType,
        xField: s.xField,
        yField: s.yField,
        yFields: [s.yField],
        y2Field: undefined,
        colorBy: metricFields.find((m) => m !== s.xField && m !== s.yField) ?? "",
      },
    });
  };

  // Correlations & stats run over ONE selected dataset (default primary; the
  // merged dual table when a raw compare is loaded).
  const datasetKeys = Object.keys(datasets);
  const [analysisKey, setAnalysisKey] = useState("primary");
  const analysisDataset = datasets[analysisKey] ?? primary;
  const useMerged = analysisKey === "primary" && mergedDual;
  const analysisRows = useMerged ? mergedDual.rows : analysisDataset?.rows ?? [];
  const analysisColumns = useMerged
    ? mergedDual.columns
    : analysisDataset?.columns ?? [];
  const showAnalysis = !state.analytics_disabled;
  const firstPanelType = panels[0]?.chartType ?? "table";

  return (
    <section className="mlab-workspace">
      <SummaryCards cards={summaryCards} />
      {warnings.length > 0 && <WarningBanner warnings={warnings} />}

      {suggestions.length > 0 && firstPanelType !== "scatter" && (
        <div className="mlab-suggestions">
          {suggestions.map((s, i) => (
            <button
              key={i}
              type="button"
              className="mlab-suggestion-chip"
              onClick={() => applySuggestion(s)}
              title={`Scatter ${s.yField} against ${s.xField} with a fitted line${metricFields.length > 2 ? ", colored by the third metric" : ""}`}
            >
              Correlate {s.xField} × {s.yField} →
            </button>
          ))}
        </div>
      )}

      {/* SQL tab ON TOP: generated SQL always visible, editable + re-runnable */}
      <SqlSection
        viewId={viewId}
        descriptors={descriptors}
        revisions={(state.dataset_revisions ?? {}) as Record<string, number>}
        allowedDatabases={state.allowed_databases ?? []}
        callTool={callTool}
      />

      <ChartGrid
        panels={panels}
        dispatch={dispatch}
        datasets={datasets}
        descriptors={descriptors}
        mergedDual={mergedDual}
        syncError={syncError}
        onRetrySync={onRetrySync}
      />

      {/* Correlations + stats: always visible below the grid, no tab wall */}
      {showAnalysis && analysisDataset && (
        <>
          {datasetKeys.length > 1 && (
            <div className="mlab-analysis-picker">
              <label className="mlab-field-label">Analyze</label>
              <select
                value={analysisKey}
                onChange={(e) => setAnalysisKey(e.target.value)}
              >
                {datasetKeys.map((k) => (
                  <option key={k} value={k}>
                    {descriptors[k]?.title || k}
                  </option>
                ))}
              </select>
            </div>
          )}
          <AnalysisSection
            rows={analysisRows}
            columns={analysisColumns}
            estimates={state.estimates}
            sampleSourceRows={state.sample_source_rows}
            truncated={analysisDataset.truncated}
            unvalidatedMetrics={unvalidated}
          />
        </>
      )}

      {primary && (
        <CollapsibleSection
          title={`Data table (${primary.rows.length.toLocaleString()} rows loaded)`}
          tone="subtle"
        >
          <DataTableTab
            rows={primary.rows}
            columns={primary.columns}
            totalAvailable={primaryDescriptor?.stats?.row_count ?? primary.rows.length}
            hydrating={primary.hydrating}
          />
        </CollapsibleSection>
      )}

      <CollapsibleSection title="Query context" tone="subtle">
        <dl className="mlab-context">
          <div>
            <dt>Metrics</dt>
            <dd>{(state.selected_metrics ?? [state.selected_metric]).filter(Boolean).join(", ") || "—"}</dd>
          </div>
          <div>
            <dt>Dimensions</dt>
            <dd>{state.selected_dimensions.join(", ") || "—"}</dd>
          </div>
          <div>
            <dt>Dataset mode</dt>
            <dd>{state.dataset_mode ?? "—"}</dd>
          </div>
          <div>
            <dt>Rows loaded</dt>
            <dd>
              {(primary?.rows.length ?? 0).toLocaleString()}
              {primary?.hydrating ? " (loading…)" : ""}
              {primary?.truncated ? " (capped at 5,000)" : ""}
            </dd>
          </div>
        </dl>
      </CollapsibleSection>
    </section>
  );
}
