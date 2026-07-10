// The loaded-data workspace — one fluid page, nothing hidden behind tabs:
//   KPI cards → chart card (controls + plot) → correlations & stats → data table.
// Multi-metric defaults: Y = first metric (left axis), Y2 = second metric
// (right axis) — explicit and editable, never a silent all-metrics override.

import { useEffect, useMemo, useRef, useState } from "react";
import { CollapsibleSection } from "../shared/CollapsibleSection";
import { SummaryCards } from "../shared/SummaryCards";
import { WarningBanner } from "../shared/WarningBanner";
import type { DatasetDescriptor, SummaryCard } from "../shared/miniAppTypes";
import { AnalysisSection } from "./AnalysisSection";
import { ChartControls } from "./ChartControls";
import { ChartPanel } from "./ChartPanel";
import { DataTableTab } from "./DataTableTab";
import { isNumericColumn } from "./analysis";
import { mergeDualDatasets, type MergedDual } from "./chartOptions";
import type { ChartConfig, ChartSuggestion, MetricLabState } from "./types";
import type { HydratedDataset } from "./useHydratedRows";

interface WorkspaceSectionProps {
  state: MetricLabState;
  summaryCards: SummaryCard[];
  primary: HydratedDataset;
  primaryDescriptor: DatasetDescriptor | undefined;
  secondary: HydratedDataset | null;
  secondaryDescriptor: DatasetDescriptor | undefined;
  config: ChartConfig;
  onConfigChange: (patch: Partial<ChartConfig>) => void;
  datasetEpoch: string;
}

export function WorkspaceSection({
  state,
  summaryCards,
  primary,
  primaryDescriptor,
  secondary,
  secondaryDescriptor,
  config,
  onConfigChange,
  datasetEpoch,
}: WorkspaceSectionProps) {
  const [sortAsc, setSortAsc] = useState(true);
  const [trendline, setTrendline] = useState(true);

  const previewOnly = state.dataset_mode === "preview_only";
  const metricFields = state.metric_fields ?? [];
  const suggestions = state.chart_suggestions ?? [];
  const unvalidated = state.unvalidated_metrics ?? [];

  const numericColumns = useMemo(
    () => primary.columns.filter((_, i) => isNumericColumn(primary.rows, i)),
    [primary.columns, primary.rows],
  );
  const categoricalColumns = useMemo(
    () => primary.columns.filter((_, i) => !isNumericColumn(primary.rows, i)),
    [primary.columns, primary.rows],
  );

  // Multi-metric default: put the second metric on the right axis ONCE per
  // dataset epoch (user edits win afterwards).
  const defaultedEpoch = useRef("");
  useEffect(() => {
    if (defaultedEpoch.current === datasetEpoch) return;
    defaultedEpoch.current = datasetEpoch;
    if (
      metricFields.length >= 2 &&
      !config.y2Field &&
      (config.chartType === "line" || config.chartType === "bar")
    ) {
      onConfigChange({
        yField: metricFields[0],
        y2Field: metricFields[1],
        groupBy: "",
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetEpoch]);

  // Dual api_* table mode: merge on the date columns for the two-axis view.
  const mergedDual: MergedDual | null = useMemo(() => {
    if (!secondary || !secondaryDescriptor || !primaryDescriptor) return null;
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
  if (!primary.hydrating && primary.rows.length === 1) {
    warnings.push(
      "This model returned a single row — it is likely a snapshot/aggregate "
        + "shape rather than a time series (check its columns in Details).",
    );
  }

  const applySuggestion = (s: ChartSuggestion) => {
    onConfigChange({
      chartType: s.chartType,
      xField: s.xField,
      yField: s.yField,
      colorBy: metricFields.find((m) => m !== s.xField && m !== s.yField) ?? "",
    });
  };

  const analysisRows = mergedDual ? mergedDual.rows : primary.rows;
  const analysisColumns = mergedDual ? mergedDual.columns : primary.columns;
  const showAnalysis = !state.analytics_disabled;

  return (
    <section className="mlab-workspace">
      <SummaryCards cards={summaryCards} />
      {warnings.length > 0 && <WarningBanner warnings={warnings} />}

      {suggestions.length > 0 && config.chartType !== "scatter" && (
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

      {/* Chart card: controls + plot as ONE unit */}
      <div className="mlab-chartcard">
        <ChartControls
          columns={primary.columns}
          numericColumns={numericColumns}
          categoricalColumns={categoricalColumns}
          config={config}
          onChange={onConfigChange}
          sortAsc={sortAsc}
          onSortToggle={() => setSortAsc((s) => !s)}
          trendline={trendline}
          onTrendlineToggle={() => setTrendline((t) => !t)}
          previewOnly={previewOnly}
        />
        {config.chartType === "table" ? (
          <DataTableTab
            rows={primary.rows}
            columns={primary.columns}
            totalAvailable={primaryDescriptor?.stats.row_count ?? primary.rows.length}
            hydrating={primary.hydrating}
          />
        ) : (
          <ChartPanel
            rows={primary.rows}
            columns={primary.columns}
            config={config}
            sortAsc={sortAsc}
            trendline={trendline}
            sql={primaryDescriptor?.sql}
            sourceModel={primaryDescriptor?.title}
            mergedDual={mergedDual}
          />
        )}
      </div>

      {/* Correlations + stats: always visible below the chart, no tab wall */}
      {showAnalysis && (
        <AnalysisSection
          rows={analysisRows}
          columns={analysisColumns}
          estimates={state.estimates}
          sampleSourceRows={state.sample_source_rows}
          truncated={primary.truncated}
          unvalidatedMetrics={unvalidated}
        />
      )}

      {config.chartType !== "table" && (
        <CollapsibleSection
          title={`Data table (${primary.rows.length.toLocaleString()} rows loaded)`}
          tone="subtle"
        >
          <DataTableTab
            rows={primary.rows}
            columns={primary.columns}
            totalAvailable={primaryDescriptor?.stats.row_count ?? primary.rows.length}
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
              {primary.rows.length.toLocaleString()}
              {primary.hydrating ? " (loading…)" : ""}
              {primary.truncated ? " (capped at 5,000)" : ""}
            </dd>
          </div>
        </dl>
      </CollapsibleSection>
    </section>
  );
}
