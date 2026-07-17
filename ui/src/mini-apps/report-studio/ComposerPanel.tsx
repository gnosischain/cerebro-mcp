// The composer: ordered sections (markdown blocks | chart blocks), move/
// delete controls, a "New chart" form (SQL → chart record, no agent needed),
// and a Generate action that calls the gated app-only compose_report tool
// (agent quality gates bypassed by construction — chart existence and
// KPI-grid layout rules still apply server-side).

import { useState } from "react";
import { AsyncButton } from "../shared/AsyncButton";
import { ChartPicker } from "./ChartPicker";
import type { ChartRecord, ComposerSection } from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

interface ComposeResult {
  ok: boolean;
  error?: string;
  missing?: string[];
  report_id?: string;
}

interface CreateChartResult {
  ok: boolean;
  error?: string;
  chart_id?: string;
  title?: string;
}

const STUDIO_CHART_TYPES = [
  "line",
  "area",
  "bar",
  "pie",
  "scatter",
  "heatmap",
  "numberDisplay",
];

interface NewChartFormProps {
  callTool: CallTool;
  /** Called with the fresh chart_id after a successful create. */
  onCreated: (chartId: string) => void;
}

function NewChartForm({ callTool, onCreated }: NewChartFormProps) {
  const [sql, setSql] = useState("");
  const [chartType, setChartType] = useState("line");
  const [xField, setXField] = useState("");
  const [yField, setYField] = useState("");
  const [seriesField, setSeriesField] = useState("");
  const [chartTitle, setChartTitle] = useState("");
  const [error, setError] = useState("");
  const [okMsg, setOkMsg] = useState("");

  const create = async () => {
    setError("");
    setOkMsg("");
    if (!sql.trim()) {
      setError("Write a SELECT query first.");
      return;
    }
    try {
      const result = await callTool<CreateChartResult>("create_studio_chart", {
        sql: sql.trim(),
        chart_type: chartType,
        x_field: xField.trim(),
        y_field: yField.trim(),
        series_field: seriesField.trim(),
        title: chartTitle.trim(),
      });
      if (result?.ok && result.chart_id) {
        setOkMsg(`Created ${result.chart_id} — added to this section.`);
        setSql("");
        setChartTitle("");
        onCreated(result.chart_id);
      } else {
        setError(result?.error ?? "Chart creation failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chart creation failed");
    }
  };

  return (
    <div className="rst-newchart">
      <textarea
        className="rst-section-md"
        value={sql}
        rows={Math.min(10, Math.max(3, sql.split("\n").length + 1))}
        placeholder="SELECT date, sum(value) AS total FROM dbt.api_… GROUP BY date ORDER BY date"
        spellCheck={false}
        onChange={(e) => setSql(e.target.value)}
      />
      <div className="rst-newchart-bar">
        <select
          value={chartType}
          title="Chart type"
          onChange={(e) => setChartType(e.target.value)}
        >
          {STUDIO_CHART_TYPES.map((t) => (
            <option key={t} value={t}>
              {t === "numberDisplay" ? "KPI" : t}
            </option>
          ))}
        </select>
        <input
          value={xField}
          placeholder="x (auto)"
          title="X column (defaults to the first column)"
          onChange={(e) => setXField(e.target.value)}
        />
        <input
          value={yField}
          placeholder="y (auto)"
          title="Y column(s), comma-separated (defaults to the second column)"
          onChange={(e) => setYField(e.target.value)}
        />
        <input
          value={seriesField}
          placeholder="series"
          title="Optional series/breakdown column"
          onChange={(e) => setSeriesField(e.target.value)}
        />
        <input
          className="rst-newchart-title"
          value={chartTitle}
          placeholder="chart title"
          maxLength={200}
          onChange={(e) => setChartTitle(e.target.value)}
        />
        <AsyncButton onClick={create} loadingLabel="Running">
          Create chart
        </AsyncButton>
      </div>
      {error && <div className="rst-error">{error}</div>}
      {okMsg && <div className="rst-hint">{okMsg}</div>}
    </div>
  );
}

interface ComposerPanelProps {
  records: ChartRecord[];
  callTool: CallTool;
  onComposed: (reportId: string) => void;
  onAskAgent?: () => void;
}

let nextSectionId = 1;

export function ComposerPanel({
  records: initialRecords,
  callTool,
  onComposed,
  onAskAgent,
}: ComposerPanelProps) {
  const [title, setTitle] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [sections, setSections] = useState<ComposerSection[]>([
    { id: nextSectionId++, markdown: "## Overview\n" },
  ]);
  const [error, setError] = useState("");
  const [records, setRecords] = useState<ChartRecord[]>(initialRecords);
  const [newChartFor, setNewChartFor] = useState<number | null>(null);

  const refreshRecords = async () => {
    try {
      const result = await callTool<{ ok: boolean; charts?: ChartRecord[] }>(
        "list_session_charts",
        {},
      );
      if (result?.ok && result.charts) setRecords(result.charts);
    } catch {
      // best-effort; the stale list still works
    }
  };

  const addMarkdown = () =>
    setSections((prev) => [...prev, { id: nextSectionId++, markdown: "" }]);
  const addCharts = () =>
    setSections((prev) => [...prev, { id: nextSectionId++, charts: [] }]);
  const remove = (id: number) =>
    setSections((prev) => prev.filter((s) => s.id !== id));
  const move = (id: number, dir: -1 | 1) =>
    setSections((prev) => {
      const idx = prev.findIndex((s) => s.id === id);
      const to = idx + dir;
      if (idx < 0 || to < 0 || to >= prev.length) return prev;
      const next = [...prev];
      const [moved] = next.splice(idx, 1);
      next.splice(to, 0, moved);
      return next;
    });

  const generate = async () => {
    setError("");
    const payloadSections = sections
      .map((s) =>
        s.markdown !== undefined
          ? { markdown: s.markdown }
          : { charts: s.charts },
      )
      .filter((s) =>
        "markdown" in s ? Boolean(s.markdown?.trim()) : (s.charts?.length ?? 0) > 0,
      );
    if (!title.trim()) {
      setError("The report needs a title.");
      return;
    }
    if (payloadSections.length === 0) {
      setError("Add at least one non-empty section.");
      return;
    }
    try {
      const result = await callTool<ComposeResult>("compose_report", {
        title: title.trim(),
        subtitle: subtitle.trim(),
        sections: payloadSections,
      });
      if (result?.ok && result.report_id) {
        onComposed(result.report_id);
      } else {
        setError(
          (result?.error ?? "Compose failed") +
            (result?.missing?.length ? ` (missing: ${result.missing.join(", ")})` : ""),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Compose failed");
    }
  };

  return (
    <section className="rst-composer" aria-label="Report composer">
      <div className="rst-composer-head">
        <input
          className="rst-composer-title"
          value={title}
          maxLength={200}
          placeholder="Report title"
          onChange={(e) => setTitle(e.target.value)}
        />
        <input
          className="rst-composer-subtitle"
          value={subtitle}
          maxLength={300}
          placeholder="Optional subtitle"
          onChange={(e) => setSubtitle(e.target.value)}
        />
      </div>

      {sections.map((section, idx) => (
        <div key={section.id} className="rst-section">
          <div className="rst-section-bar">
            <span className="rst-section-kind">
              {section.markdown !== undefined ? "markdown" : "charts"}
            </span>
            <span className="rst-section-actions">
              <button
                type="button"
                className="rst-toggle"
                disabled={idx === 0}
                onClick={() => move(section.id, -1)}
              >
                ↑
              </button>
              <button
                type="button"
                className="rst-toggle"
                disabled={idx === sections.length - 1}
                onClick={() => move(section.id, 1)}
              >
                ↓
              </button>
              <button
                type="button"
                className="rst-toggle"
                onClick={() => remove(section.id)}
              >
                ×
              </button>
            </span>
          </div>
          {section.markdown !== undefined ? (
            <textarea
              className="rst-section-md"
              value={section.markdown}
              rows={Math.min(12, Math.max(3, section.markdown.split("\n").length + 1))}
              placeholder="Markdown — headings, prose, tables…"
              onChange={(e) =>
                setSections((prev) =>
                  prev.map((s) =>
                    s.id === section.id && s.markdown !== undefined
                      ? { ...s, markdown: e.target.value }
                      : s,
                  ),
                )
              }
            />
          ) : (
            <>
              <ChartPicker
                records={records}
                selected={new Set(section.charts)}
                onToggle={(chartId) =>
                  setSections((prev) =>
                    prev.map((s) => {
                      if (s.id !== section.id || s.charts === undefined) return s;
                      return s.charts.includes(chartId)
                        ? { ...s, charts: s.charts.filter((c) => c !== chartId) }
                        : { ...s, charts: [...s.charts, chartId] };
                    }),
                  )
                }
                callTool={callTool}
                onAskAgent={onAskAgent}
              />
              <button
                type="button"
                className="rst-toggle"
                onClick={() =>
                  setNewChartFor((cur) => (cur === section.id ? null : section.id))
                }
              >
                {newChartFor === section.id ? "close new chart" : "+ new chart from SQL"}
              </button>
              {newChartFor === section.id && (
                <NewChartForm
                  callTool={callTool}
                  onCreated={(chartId) => {
                    void refreshRecords();
                    // auto-select the fresh chart into THIS section
                    setSections((prev) =>
                      prev.map((s) =>
                        s.id === section.id && s.charts !== undefined
                          ? { ...s, charts: [...s.charts, chartId] }
                          : s,
                      ),
                    );
                  }}
                />
              )}
            </>
          )}
        </div>
      ))}

      <div className="rst-composer-actions">
        <button type="button" className="rst-toggle" onClick={addMarkdown}>
          + markdown
        </button>
        <button type="button" className="rst-toggle" onClick={addCharts}>
          + charts
        </button>
        <AsyncButton onClick={generate} loadingLabel="Generating">
          Generate report
        </AsyncButton>
      </div>

      {error && <div className="rst-error">{error}</div>}
      <p className="rst-hint">
        KPI counters are automatically grouped into grid rows; chart records
        expire two hours after they were generated.
      </p>
    </section>
  );
}
