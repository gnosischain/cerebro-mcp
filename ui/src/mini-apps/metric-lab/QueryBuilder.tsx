// The load builder: selected models as chips (max 2 — compare is raw-only),
// a Mode switch, and an EXPLICIT Run button. Editing never fires a query.
//
//   Aggregate — agg(Y) GROUP BY X [, Series top-N] runs IN CLICKHOUSE.
//               The only correct way to chart big per-entity panels
//               (balances per avatar per day etc.): a raw LIMIT sample
//               covers a fraction of one day.
//   Raw rows  — SELECT * sample (newest first) for inspection.

import { AsyncButton } from "../shared/AsyncButton";
import { MaField } from "../shared/MaField";
import { SegmentedControl } from "../shared/SegmentedControl";
import { categoricalColumns, numericColumns, timeColumn } from "./catalogSearch";
import { AGG_FNS, type AggFn, type LoadMode, type MetricCatalogEntry, type QuerySpec } from "./types";

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

        {aggregate ? (
          <>
            <ColSelect
              label="X"
              title="Group-by axis (usually the date column)"
              value={spec.aggX}
              options={allCols}
              onChange={(v) => onSpecChange({ aggX: v })}
            />
            <ColSelect
              label="Y"
              title="Measured column (any column for uniq/count)"
              value={spec.aggY}
              options={spec.aggFn === "uniq" ? allCols : numCols.length ? numCols : allCols}
              onChange={(v) => onSpecChange({ aggY: v })}
            />
            <MaField className="mlab-field" title="Aggregation — uniq counts distinct Y per X (e.g. daily active avatars)">
              <label className="mlab-field-label">Agg</label>
              <select
                value={spec.aggFn}
                onChange={(e) => onSpecChange({ aggFn: e.target.value as AggFn })}
              >
                {AGG_FNS.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </MaField>
            <ColSelect
              label="Series"
              title="Optional breakdown — output bounded to the top-N series"
              value={spec.aggSeries}
              options={catCols.filter((c) => c !== spec.aggX)}
              onChange={(v) => onSpecChange({ aggSeries: v })}
              allowNone
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
        ) : (
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

        {spec.metrics.length > 2 && (
          <span className="mlab-error">
            At most 2 models can be compared side by side — remove {spec.metrics.length - 2}.
          </span>
        )}
      </div>

      {error && <div className="mlab-error mlab-builder-error">{error}</div>}
    </section>
  );
}
