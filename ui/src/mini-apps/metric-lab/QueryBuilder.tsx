// The load builder: selected models as chips, a Mode switch, and an EXPLICIT
// Run button. Editing never fires a query.
//
//   Aggregate — agg(Y) GROUP BY X [, Series top-N] runs IN CLICKHOUSE.
//               The only correct way to chart big per-entity panels
//               (balances per avatar per day etc.): a raw LIMIT sample
//               covers a fraction of one day.
//   Raw rows  — SELECT [cols|*] sample (newest first) for inspection.
//   2-8 models + "Join on date" — ONE server-joined wide table (a value
//               column per model) with per-model Y/agg rows below;
//               exactly 2 models may instead "Raw compare" (legacy dual).

import { AsyncButton } from "../shared/AsyncButton";
import { MaField } from "../shared/MaField";
import { SegmentedControl } from "../shared/SegmentedControl";
import { categoricalColumns, numericColumns, timeColumn } from "./catalogSearch";
import { ColumnMultiSelect } from "./ColumnMultiSelect";
import {
  AGG_FNS,
  GRAINS,
  type AggFn,
  type Grain,
  type LoadMode,
  type MetricCatalogEntry,
  type QuerySpec,
} from "./types";

const WINDOW_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: "All history" },
  { value: 365, label: "Last year" },
  { value: 90, label: "Last 90 days" },
  { value: 30, label: "Last 30 days" },
];

interface QueryBuilderProps {
  spec: QuerySpec;
  onSpecChange: (patch: Partial<QuerySpec>) => void;
  catalogByName: Map<string, MetricCatalogEntry>;
  dirty: boolean;
  loading: boolean;
  error: string;
  onRun: () => Promise<void>;
}

function ColSelect({
  label,
  title,
  value,
  options,
  onChange,
  allowNone,
  noneLabel = "none",
}: {
  label: string;
  title: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  allowNone?: boolean;
  noneLabel?: string;
}) {
  return (
    <MaField className="mlab-field" title={title}>
      <label className="mlab-field-label">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
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

export function QueryBuilder({
  spec,
  onSpecChange,
  catalogByName,
  dirty,
  loading,
  error,
  onRun,
}: QueryBuilderProps) {
  if (spec.metrics.length === 0) return null;

  const anchor = catalogByName.get(spec.metrics[0]);
  const allCols = (anchor?.columns ?? []).map((c) => c.name);
  const numCols = anchor ? numericColumns(anchor) : [];
  const catCols = anchor ? categoricalColumns(anchor) : [];
  const isCompare = spec.metrics.length > 1;
  const aggregate = spec.mode === "aggregate" && !isCompare;

  // aggYs is the authoritative measure list; legacy aggY seeds it when empty.
  const measureList = spec.aggYs.length
    ? spec.aggYs
    : spec.aggY
      ? [spec.aggY]
      : [];
  const multiY = measureList.length > 1;
  // Grain only makes sense when X is the model's time column.
  const anchorTimeCol = anchor ? timeColumn(anchor) : null;
  const grainEnabled = Boolean(anchorTimeCol && spec.aggX === anchorTimeCol);

  const removeModel = (name: string) =>
    onSpecChange({ metrics: spec.metrics.filter((m) => m !== name) });

  return (
    <section className="mlab-builder" aria-label="Load builder">
      <div className="mlab-builder-row">
        <div className="mlab-basket">
          {spec.metrics.map((name, i) => {
            const entry = catalogByName.get(name);
            return (
              <span
                key={name}
                className={`mlab-basket-chip${i === 0 ? " is-anchor" : ""}`}
                title={entry?.relation_name || name}
              >
                <span className="mlab-basket-label mlab-basket-label--mono">{name}</span>
                {entry?.layer && <span className={`mlab-layer mlab-layer--${entry.layer}`}>{entry.layer}</span>}
                <button
                  type="button"
                  className="mlab-basket-x"
                  aria-label={`Remove ${name}`}
                  onClick={() => removeModel(name)}
                >
                  ×
                </button>
              </span>
            );
          })}
          {spec.metrics.length > 1 && (
            <button type="button" className="mlab-toggle" onClick={() => onSpecChange({ metrics: [] })}>
              clear all
            </button>
          )}
        </div>

        <div className="mlab-builder-actions">
          {dirty && <span className="mlab-dirty">config changed — Run to apply</span>}
          <AsyncButton onClick={onRun} loadingLabel="Running" disabled={loading}>
            Run
          </AsyncButton>
        </div>
      </div>

      <div className="mlab-builder-config">
        {!isCompare && (
          <SegmentedControl<LoadMode>
            ariaLabel="Load mode — Aggregate runs GROUP BY in ClickHouse (correct for big per-entity tables); Raw samples rows"
            size="sm"
            value={spec.mode}
            onChange={(m) => {
              if (m === "aggregate" && anchor && !spec.aggX) {
                // Seed sensible defaults from the anchor's columns.
                onSpecChange({
                  mode: m,
                  aggX: timeColumn(anchor) ?? allCols[0] ?? "",
                  aggY: numCols[0] ?? "",
                });
                return;
              }
              onSpecChange({ mode: m });
            }}
            options={[
              { value: "aggregate", label: "Aggregate" },
              { value: "raw", label: "Raw rows" },
            ]}
          />
        )}
        {isCompare && spec.metrics.length === 2 && (
          <SegmentedControl<LoadMode>
            ariaLabel="Compare mode — Join overlays the models on a date-joined wide table (enables correlations); Raw loads the two tables side by side"
            size="sm"
            value={spec.mode}
            onChange={(m) => onSpecChange({ mode: m })}
            options={[
              { value: "aggregate", label: "Join on date" },
              { value: "raw", label: "Raw compare" },
            ]}
          />
        )}

        {aggregate ? (
          <>
            <ColSelect
              label="X"
              title="Group-by axis (usually the date column)"
              value={spec.aggX}
              options={allCols}
              onChange={(v) =>
                onSpecChange({
                  aggX: v,
                  // Grain is only valid on the time column.
                  ...(anchorTimeCol && v !== anchorTimeCol ? { grain: "" } : {}),
                })
              }
            />
            <ColumnMultiSelect
              label="Y"
              title={
                spec.aggFn === "count"
                  ? "count() needs no measure column"
                  : "Measure column(s) — all aggregated with the same function; multiple Ys and Series are mutually exclusive"
              }
              options={
                spec.aggFn === "uniq" ? allCols : numCols.length ? numCols : allCols
              }
              value={measureList}
              maxSelections={
                spec.aggFn === "count" || spec.aggFn === "uniq" || spec.aggSeries
                  ? 1
                  : undefined
              }
              disabled={spec.aggFn === "count"}
              placeholder={spec.aggFn === "count" ? "—" : "pick column(s)"}
              onChange={(next) =>
                onSpecChange({
                  aggYs: next,
                  aggY: next[0] ?? "",
                  // Multiple measures exclude a series breakdown.
                  ...(next.length > 1 ? { aggSeries: "" } : {}),
                })
              }
            />
            <MaField className="mlab-field" title="Aggregation — uniq counts distinct Y per X (e.g. daily active avatars)">
              <label className="mlab-field-label">Agg</label>
              <select
                value={spec.aggFn}
                onChange={(e) => {
                  const next = e.target.value as AggFn;
                  onSpecChange({
                    aggFn: next,
                    // count/uniq are single-measure aggregations.
                    ...(next === "count"
                      ? { aggYs: [], aggY: "" }
                      : next === "uniq" && measureList.length > 1
                        ? { aggYs: measureList.slice(0, 1), aggY: measureList[0] }
                        : {}),
                  });
                }}
              >
                {AGG_FNS.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </MaField>
            <MaField
              className="mlab-field"
              title={
                grainEnabled
                  ? "Roll X up to day/week/month buckets (runs in ClickHouse)"
                  : "Grain needs X to be the model's time column"
              }
            >
              <label className="mlab-field-label">Grain</label>
              <select
                value={spec.grain}
                disabled={!grainEnabled}
                onChange={(e) => onSpecChange({ grain: e.target.value as Grain })}
              >
                {GRAINS.map((g) => (
                  <option key={g.value} value={g.value}>
                    {g.label}
                  </option>
                ))}
              </select>
            </MaField>
            <ColSelect
              label="Series"
              title={
                multiY
                  ? "Series is unavailable while several Y columns are selected"
                  : "Optional breakdown — output bounded to the top-N series"
              }
              value={multiY ? "" : spec.aggSeries}
              options={multiY ? [] : catCols.filter((c) => c !== spec.aggX)}
              onChange={(v) => onSpecChange({ aggSeries: v })}
              allowNone
              noneLabel={multiY ? "n/a (multi-Y)" : "none"}
            />
            {spec.aggSeries && (
              <MaField className="mlab-field" title="How many series to keep (ranked by the aggregate)">
                <label className="mlab-field-label">Top</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={spec.aggTopN}
                  onChange={(e) => onSpecChange({ aggTopN: Number(e.target.value) || 8 })}
                />
              </MaField>
            )}
            <ColSelect
              label="Filter"
              title="Optional equality filter"
              value={spec.filterCol}
              options={allCols}
              onChange={(v) => onSpecChange({ filterCol: v })}
              allowNone
            />
            {spec.filterCol && (
              <>
                <MaField className="mlab-field" title="Filter operator">
                  <select
                    value={spec.filterOp}
                    onChange={(e) => onSpecChange({ filterOp: e.target.value as "=" | "!=" })}
                  >
                    <option value="=">=</option>
                    <option value="!=">!=</option>
                  </select>
                </MaField>
                <MaField className="mlab-field" title="Filter value (exact match)">
                  <input
                    type="text"
                    placeholder="value"
                    value={spec.filterValue}
                    onChange={(e) => onSpecChange({ filterValue: e.target.value })}
                  />
                </MaField>
              </>
            )}
          </>
        ) : isCompare && spec.mode === "aggregate" ? null : (
          <>
            {!isCompare && (
              <ColumnMultiSelect
                label="Columns"
                title="Project only these columns (empty = all columns)"
                options={allCols}
                value={spec.columns}
                onChange={(next) => onSpecChange({ columns: next })}
                placeholder="all columns"
              />
            )}
            <MaField className="mlab-field" title="Row limit (raw sample, newest first)">
              <label className="mlab-field-label">Limit</label>
              <input
                type="number"
                min={10}
                max={100000}
                value={spec.limit}
                onChange={(e) => onSpecChange({ limit: Number(e.target.value) || 2000 })}
              />
            </MaField>
          </>
        )}

        <MaField className="mlab-field" title="Trailing time window — bounds the scan on heavy views">
          <label className="mlab-field-label">Window</label>
          <select
            value={spec.windowDays}
            onChange={(e) => onSpecChange({ windowDays: Number(e.target.value) || 0 })}
          >
            {WINDOW_OPTIONS.map((w) => (
              <option key={w.value} value={w.value}>
                {w.label}
              </option>
            ))}
          </select>
        </MaField>

        {isCompare && spec.mode === "aggregate" && (
          <MaField
            className="mlab-field"
            title="Date bucket for the join — each model is aggregated to this grain, then joined"
          >
            <label className="mlab-field-label">Grain</label>
            <select
              value={spec.grain || "day"}
              onChange={(e) => onSpecChange({ grain: e.target.value as Grain })}
            >
              {GRAINS.filter((g) => g.value !== "").map((g) => (
                <option key={g.value} value={g.value}>
                  {g.label}
                </option>
              ))}
            </select>
          </MaField>
        )}

        {spec.metrics.length > 8 && (
          <span className="mlab-error">
            At most 8 models per comparison — remove {spec.metrics.length - 8}.
          </span>
        )}
      </div>

      {isCompare && spec.mode === "aggregate" && (
        <div className="mlab-joinspecs" aria-label="Per-model join measures">
          {spec.metrics.map((name) => {
            const entry = catalogByName.get(name);
            const modelNums = entry ? numericColumns(entry) : [];
            const conf = spec.joinSpecs[name] ?? { y: "", agg: "sum" as AggFn };
            const setConf = (patch: Partial<{ y: string; agg: AggFn }>) =>
              onSpecChange({
                joinSpecs: { ...spec.joinSpecs, [name]: { ...conf, ...patch } },
              });
            return (
              <div key={name} className="mlab-joinspec-row">
                <span className="mlab-joinspec-model" title={entry?.relation_name || name}>
                  {name}
                </span>
                <MaField className="mlab-field" title="Measure column (default: first numeric column)">
                  <label className="mlab-field-label">Y</label>
                  <select value={conf.y} onChange={(e) => setConf({ y: e.target.value })}>
                    <option value="">{modelNums[0] ? `${modelNums[0]} (default)` : "auto"}</option>
                    {modelNums.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </MaField>
                <MaField className="mlab-field" title="Aggregation for this model's measure">
                  <label className="mlab-field-label">Agg</label>
                  <select
                    value={conf.agg}
                    onChange={(e) => setConf({ agg: e.target.value as AggFn })}
                  >
                    {AGG_FNS.filter((a) => a !== "count").map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                </MaField>
              </div>
            );
          })}
        </div>
      )}

      {error && <div className="mlab-error mlab-builder-error">{error}</div>}
    </section>
  );
}
