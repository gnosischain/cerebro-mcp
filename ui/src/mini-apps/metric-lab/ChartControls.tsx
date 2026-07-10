// Chart configuration row. Controls are honest — the chart plots exactly
// what is selected here:
//   line/bar : X · Y (left axis) · Y2 (right axis) · Series (breakdown)
//   scatter  : X · Y · Color (third value: numeric → gradient, category → hues)
//   heatmap  : X · Value (Y) · Series (rows)

import { SegmentedControl } from "../shared/SegmentedControl";
import { MaField } from "../shared/MaField";
import {
  AGGREGATIONS,
  CHART_TYPES,
  type Aggregation,
  type ChartConfig,
  type ChartType,
} from "./types";

interface ChartControlsProps {
  columns: string[];
  numericColumns: string[];
  categoricalColumns: string[];
  config: ChartConfig;
  onChange: (patch: Partial<ChartConfig>) => void;
  sortAsc: boolean;
  onSortToggle: () => void;
  trendline: boolean;
  onTrendlineToggle: () => void;
  previewOnly: boolean;
}

const TYPE_LABEL: Record<ChartType, string> = {
  table: "Table",
  line: "Line",
  bar: "Bar",
  scatter: "Scatter",
  heatmap: "Heatmap",
  pie: "Pie",
  numberDisplay: "KPI",
};

function FieldSelect({
  label,
  title,
  value,
  options,
  onChange,
  disabled,
  allowNone,
  noneLabel = "none",
}: {
  label: string;
  title: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  disabled?: boolean;
  allowNone?: boolean;
  noneLabel?: string;
}) {
  return (
    <MaField className="mlab-field" title={title}>
      <label className="mlab-field-label">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {allowNone && <option value="">{noneLabel}</option>}
        {options.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </MaField>
  );
}

export function ChartControls({
  columns,
  numericColumns,
  categoricalColumns,
  config,
  onChange,
  sortAsc,
  onSortToggle,
  trendline,
  onTrendlineToggle,
  previewOnly,
}: ChartControlsProps) {
  const t = config.chartType;
  const heatmapPossible = categoricalColumns.some((c) => c !== config.xField);
  const seriesOptions = categoricalColumns.filter((c) => c !== config.xField);

  return (
    <section className="mlab-controls">
      <SegmentedControl<ChartType>
        ariaLabel="Chart type"
        size="sm"
        value={t}
        onChange={(next) => {
          if (previewOnly && next !== "table") return;
          if (next === "heatmap" && !heatmapPossible) return;
          if (next === "heatmap" && !config.groupBy) {
            const candidate = seriesOptions[0] ?? "";
            onChange({ chartType: next, groupBy: candidate });
            return;
          }
          if (next === "scatter") {
            // Scatter wants numeric X — steer to the first numeric column
            // that isn't Y so the user lands on a working chart.
            const xi = columns.indexOf(config.xField);
            const xNumeric = xi >= 0 && numericColumns.includes(config.xField);
            if (!xNumeric) {
              const nx = numericColumns.find((c) => c !== config.yField) ?? config.xField;
              onChange({ chartType: next, xField: nx });
              return;
            }
          }
          onChange({ chartType: next });
        }}
        options={CHART_TYPES.map((ct) => ({
          value: ct,
          label: TYPE_LABEL[ct],
          ariaLabel:
            previewOnly && ct !== "table"
              ? `${TYPE_LABEL[ct]} (preview-only dataset: table only)`
              : ct === "heatmap" && !heatmapPossible
                ? "Heatmap (needs a categorical series column)"
                : TYPE_LABEL[ct],
        }))}
      />

      <div className="mlab-controls-fields">
        <FieldSelect
          label="X"
          title={t === "scatter" ? "X axis (numeric)" : "X axis"}
          value={config.xField}
          options={t === "scatter" ? numericColumns : columns}
          onChange={(v) => onChange({ xField: v })}
          disabled={previewOnly}
          allowNone
          noneLabel="—"
        />

        <button
          type="button"
          className="mlab-swap"
          title="Swap X and Y"
          onClick={() => onChange({ xField: config.yField, yField: config.xField })}
          disabled={previewOnly || !config.xField || !config.yField}
        >
          ⇄
        </button>

        <FieldSelect
          label={t === "heatmap" ? "Value" : "Y"}
          title={t === "heatmap" ? "Cell value" : "Y axis (left)"}
          value={config.yField}
          options={t === "scatter" ? numericColumns : columns}
          onChange={(v) => onChange({ yField: v })}
          disabled={previewOnly}
          allowNone
          noneLabel="—"
        />

        {(t === "line" || t === "bar") && (
          <FieldSelect
            label="Y2"
            title="Secondary axis (right) — for a metric on a different scale"
            value={config.y2Field ?? ""}
            options={numericColumns.filter((c) => c !== config.yField && c !== config.xField)}
            onChange={(v) => onChange({ y2Field: v })}
            disabled={previewOnly}
            allowNone
            noneLabel="none"
          />
        )}

        {t === "scatter" ? (
          <FieldSelect
            label="Color"
            title="Color the points by a third value — numeric gets a gradient, categories get hues"
            value={config.colorBy ?? ""}
            options={columns.filter((c) => c !== config.xField && c !== config.yField)}
            onChange={(v) => onChange({ colorBy: v })}
            disabled={previewOnly}
            allowNone
          />
        ) : (
          t !== "pie" &&
          t !== "numberDisplay" && (
            <FieldSelect
              label="Series"
              title={t === "heatmap" ? "Heatmap rows" : "Break the chart into one series per value"}
              value={config.groupBy}
              options={seriesOptions}
              onChange={(v) => onChange({ groupBy: v })}
              disabled={previewOnly || Boolean(config.y2Field && (t === "line" || t === "bar"))}
              allowNone
            />
          )
        )}

        <FieldSelect
          label="Agg"
          title="Aggregation applied per bucket"
          value={config.aggregation}
          options={AGGREGATIONS}
          onChange={(v) => onChange({ aggregation: v as Aggregation })}
          disabled={previewOnly || t === "scatter"}
        />

        <button
          type="button"
          className={`mlab-toggle ${sortAsc ? "" : "is-on"}`}
          onClick={onSortToggle}
          title="Toggle sort direction"
        >
          {sortAsc ? "↑ asc" : "↓ desc"}
        </button>

        {t === "scatter" && (
          <button
            type="button"
            className={`mlab-toggle ${trendline ? "is-on" : ""}`}
            onClick={onTrendlineToggle}
            title="Toggle OLS trendline"
          >
            fit
          </button>
        )}
      </div>
    </section>
  );
}
