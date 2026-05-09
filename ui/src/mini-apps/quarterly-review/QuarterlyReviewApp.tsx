import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { TabBar, type TabDef } from "../shared/TabBar";
import { DataCard } from "../shared/DataCard";
import { AsyncButton } from "../shared/AsyncButton";
import { SegmentedControl } from "../shared/SegmentedControl";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { CollapsibleSection } from "../shared/CollapsibleSection";
import { PaginatedTable } from "../shared/PaginatedTable";
import type {
  DatasetDescriptor,
  MiniAppPayload,
} from "../shared/miniAppTypes";
import type {
  AnalysisTemplateId,
  CompareMode,
  FamilyId,
  QuarterlyReviewState,
  QuarterlyTab,
} from "./state/types";
import { buildMockPayload } from "./state/mock";

const APP_ID = "quarterly_review";

// Dev-only: surface a mock payload when loaded with ?demo=loaded so the
// app renders without a live MCP host. Production Claude Desktop will
// always send a real INITIAL_LOAD via the ext-apps SDK.
const DEV_MOCK_ENABLED =
  import.meta.env.DEV &&
  typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("demo") === "loaded";

const ANALYSIS_TEMPLATES: {
  id: AnalysisTemplateId;
  label: string;
  desc: string;
}[] = [
  {
    id: "cohort_retention",
    label: "Address retention cohorts",
    desc: "Monthly cohort heatmap of retained active addresses.",
  },
  {
    id: "address_ltv",
    label: "Address LTV distribution",
    desc: "Top 1,000 addresses by fees paid this quarter.",
  },
  {
    id: "churn",
    label: "Churn & reactivation",
    desc: "Retained vs churned vs new addresses Q-over-Q.",
  },
  {
    id: "feature_adoption",
    label: "Feature adoption Q-o-Q",
    desc: "Pool volume changes — which features grew.",
  },
  {
    id: "segmentation",
    label: "Address segmentation",
    desc: "Activity-vs-balance scatter by address.",
  },
];

function formatNumber(v: unknown): string {
  if (v == null) return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(2)}k`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function toFinite(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

// ---------------------------------------------------------------------------
// KPI rows extraction — unified (metric, current, prior, delta_pct) shape.
// ---------------------------------------------------------------------------

interface KpiRow {
  family: FamilyId;
  metric: string;
  current: number | null;
  prior: number | null;
  deltaPct: number | null;
}

function kpiRows(
  family: FamilyId,
  dataset: DatasetDescriptor | undefined,
): KpiRow[] {
  if (!dataset || dataset.preview_rows.length === 0) return [];
  return dataset.preview_rows.map((row) => ({
    family,
    metric: String(row[0] ?? "metric"),
    current: toFinite(row[1]),
    prior: toFinite(row[2]),
    deltaPct: toFinite(row[3]),
  }));
}

// ---------------------------------------------------------------------------
// Trend chart option — two-series line indexed by quarter label
// ---------------------------------------------------------------------------

function buildTrendOption(
  dataset: DatasetDescriptor | undefined,
  isDark: boolean,
): Record<string, unknown> | null {
  if (!dataset || dataset.preview_rows.length === 0) return null;
  const cols = dataset.columns.map((c) => c.name);
  const dayIdx = cols.indexOf("day");
  const qIdx = cols.indexOf("quarter");
  const valueCol = cols.find((c) => c !== "day" && c !== "quarter");
  if (dayIdx < 0 || qIdx < 0 || !valueCol) return null;
  const valueIdx = cols.indexOf(valueCol);

  const byQuarter = new Map<string, [string, number][]>();
  for (const row of dataset.preview_rows) {
    const day = String(row[dayIdx] ?? "");
    const q = String(row[qIdx] ?? "");
    const v = toFinite(row[valueIdx]);
    if (v == null || !day || !q) continue;
    if (!byQuarter.has(q)) byQuarter.set(q, []);
    byQuarter.get(q)!.push([day, v]);
  }

  const axisColor = isDark ? "#94a3b8" : "#64748b";
  const gridColor = isDark
    ? "rgba(148, 163, 184, 0.1)"
    : "rgba(15, 23, 42, 0.08)";

  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: {
      data: Array.from(byQuarter.keys()),
      textStyle: { color: axisColor },
      top: 0,
    },
    grid: { left: 60, right: 24, top: 36, bottom: 40 },
    xAxis: {
      type: "time",
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: gridColor } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: axisColor },
      splitLine: { lineStyle: { color: gridColor } },
    },
    series: Array.from(byQuarter.entries()).map(([q, pts]) => ({
      name: q,
      type: "line",
      smooth: true,
      showSymbol: false,
      data: pts,
    })),
  };
}

function buildBreakdownOption(
  dataset: DatasetDescriptor | undefined,
  isDark: boolean,
): Record<string, unknown> | null {
  if (!dataset || dataset.preview_rows.length === 0) return null;
  const axisColor = isDark ? "#94a3b8" : "#64748b";
  const xs: string[] = [];
  const ys: number[] = [];
  for (const row of dataset.preview_rows) {
    xs.push(String(row[0] ?? ""));
    const v = toFinite(row[1]);
    ys.push(v ?? 0);
  }
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 60, right: 24, top: 20, bottom: 70 },
    xAxis: {
      type: "category",
      data: xs,
      axisLabel: { color: axisColor, rotate: 35 },
    },
    yAxis: { type: "value", axisLabel: { color: axisColor } },
    series: [{ type: "bar", data: ys }],
  };
}

// ---------------------------------------------------------------------------
// Tab bodies
// ---------------------------------------------------------------------------

interface TabProps {
  view: MiniAppPayload<QuarterlyReviewState>;
  state: QuarterlyReviewState;
  handle: ReturnType<typeof useMiniApp<QuarterlyReviewState>>;
  isDark: boolean;
}

function OverviewTab({ view, state, handle, isDark }: TabProps) {
  const families = state.kpi_families;
  const allKpis = useMemo(
    () =>
      families.flatMap((fam) =>
        kpiRows(fam, view.datasets?.[`kpi_${fam}_qoq`]),
      ),
    [families, view.datasets],
  );

  return (
    <div className="qr-main">
      <section className="qr-section">
        <h2 className="qr-section__title">Headline KPIs</h2>
        <div className="qr-kpi-grid">
          {allKpis.map((k, i) => (
            <DataCard
              key={`${k.family}-${k.metric}-${i}`}
              label={`${k.family.replace("_", " ")} · ${k.metric}`}
              value={formatNumber(k.current)}
              delta={
                k.deltaPct == null
                  ? undefined
                  : {
                      pct: k.deltaPct,
                      reference: state.compare_quarter,
                    }
              }
              onClick={() =>
                handle.callTool("update_quarterly_review_focus", {
                  view_id: view.view_id,
                  selected_family: k.family,
                  active_tab: "deep_dive",
                })
              }
            />
          ))}
          {allKpis.length === 0 && (
            <div className="qr-loading">No KPI data available.</div>
          )}
        </div>
      </section>

      <section className="qr-section">
        <h2 className="qr-section__title">Trends by family</h2>
        <div className="qr-trend-grid">
          {families.map((fam) => {
            const opt = buildTrendOption(
              view.datasets?.[`trend_${fam}`],
              isDark,
            );
            return (
              <div key={fam} className="qr-mini-chart">
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--text-muted)",
                    marginBottom: 4,
                    paddingLeft: 4,
                  }}
                >
                  {fam.replace("_", " ")}
                </div>
                {opt ? (
                  <ReactECharts
                    option={opt}
                    style={{ height: 200 }}
                    notMerge
                  />
                ) : (
                  <div className="qr-loading">No trend data.</div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function CompareTab({ view, state, handle }: TabProps) {
  const rows = useMemo(
    () =>
      state.kpi_families.flatMap((fam) =>
        kpiRows(fam, view.datasets?.[`kpi_${fam}_qoq`]),
      ),
    [state.kpi_families, view.datasets],
  );

  return (
    <div className="qr-main">
      <section className="qr-section">
        <h2 className="qr-section__title">
          {state.current_quarter} vs {state.compare_quarter} ({state.compare_mode})
        </h2>
        <div className="qr-inline-actions" style={{ marginBottom: 8 }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Compare with:
          </span>
          <SegmentedControl<CompareMode>
            ariaLabel="Compare mode"
            size="sm"
            value={state.compare_mode}
            onChange={(mode) =>
              handle.callTool("update_quarterly_review_focus", {
                view_id: view.view_id,
                compare_mode: mode,
              })
            }
            options={[
              { value: "prior_quarter", label: "Prior Q" },
              { value: "same_quarter_last_year", label: "YoY" },
              { value: "trailing_4q_avg", label: "Trailing 4Q" },
            ]}
          />
        </div>
        <table className="qr-compare-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: 8 }}>Family</th>
              <th style={{ textAlign: "left", padding: 8 }}>Metric</th>
              <th style={{ textAlign: "right", padding: 8 }}>Current</th>
              <th style={{ textAlign: "right", padding: 8 }}>Prior</th>
              <th style={{ textAlign: "right", padding: 8 }}>Δ %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const tone =
                r.deltaPct == null
                  ? "neutral"
                  : r.deltaPct >= 0.05
                  ? "positive"
                  : r.deltaPct <= -0.05
                  ? "negative"
                  : "warning";
              return (
                <tr key={i} style={{ borderTop: "1px solid var(--section-divider)" }}>
                  <td style={{ padding: 8 }}>{r.family.replace("_", " ")}</td>
                  <td style={{ padding: 8 }}>{r.metric}</td>
                  <td style={{ padding: 8, textAlign: "right" }}>
                    {formatNumber(r.current)}
                  </td>
                  <td style={{ padding: 8, textAlign: "right", color: "var(--text-muted)" }}>
                    {formatNumber(r.prior)}
                  </td>
                  <td
                    style={{
                      padding: 8,
                      textAlign: "right",
                      color: `var(--tone-${tone}-fg, inherit)`,
                    }}
                  >
                    {r.deltaPct == null
                      ? "N/A"
                      : `${(r.deltaPct * 100).toFixed(1)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function DeepDiveTab({ view, state, handle, isDark }: TabProps) {
  const fam = state.selected_family;
  const breakdownDs = view.datasets?.[`breakdown_${fam}`];
  const scatterDs = view.datasets?.[`scatter_${fam}`];

  return (
    <div className="qr-main">
      <section className="qr-section">
        <h2 className="qr-section__title">Deep dive — {fam.replace("_", " ")}</h2>
        <div className="qr-inline-actions">
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Family:</span>
          <SegmentedControl<FamilyId>
            ariaLabel="Selected family"
            size="sm"
            value={fam}
            onChange={(next) =>
              handle.callTool("update_quarterly_review_focus", {
                view_id: view.view_id,
                selected_family: next,
              })
            }
            options={state.kpi_families.map((f) => ({
              value: f,
              label: f.replace("_", " "),
            }))}
          />
        </div>

        <div className="qr-trend-grid">
          <div className="qr-mini-chart">
            <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "0 4px 4px" }}>
              breakdown
            </div>
            {breakdownDs ? (
              <ReactECharts
                option={buildBreakdownOption(breakdownDs, isDark) ?? {}}
                style={{ height: 260 }}
                notMerge
              />
            ) : (
              <div className="qr-loading">No breakdown data.</div>
            )}
          </div>
          <div className="qr-mini-chart">
            <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "0 4px 4px" }}>
              scatter (relational)
            </div>
            {scatterDs ? (
              <ReactECharts
                option={
                  buildTrendOption(scatterDs, isDark) ??
                  buildBreakdownOption(scatterDs, isDark) ??
                  {}
                }
                style={{ height: 260 }}
                notMerge
              />
            ) : (
              <div className="qr-loading">No scatter data.</div>
            )}
          </div>
        </div>
      </section>

      <section className="qr-section">
        <h2 className="qr-section__title">Pre-built analyses</h2>
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>
          Drop one onto your canvas. Charts register instantly; you fill in the
          conclusion and save.
        </p>
        <div className="qr-template-grid">
          {ANALYSIS_TEMPLATES.map((tpl) => (
            <button
              key={tpl.id}
              type="button"
              className="qr-template-card"
              onClick={() =>
                handle.callTool("add_quarterly_analysis_template", {
                  view_id: view.view_id,
                  template_id: tpl.id,
                })
              }
            >
              <span className="qr-template-card__name">+ {tpl.label}</span>
              <span className="qr-template-card__desc">{tpl.desc}</span>
            </button>
          ))}
        </div>
      </section>

      <DraftAnalysisPanel view={view} state={state} handle={handle} />

      <section className="qr-section">
        <h2 className="qr-section__title">Detail rows (first family dataset)</h2>
        {breakdownDs ? (
          <PaginatedTable
            dataset={breakdownDs}
            datasetKey={`breakdown_${fam}`}
            viewId={view.view_id}
            fetchRows={handle.fetchRows}
            emptyLabel="No rows in this breakdown."
          />
        ) : (
          <div className="qr-loading">No detail rows available.</div>
        )}
      </section>
    </div>
  );
}

function DraftAnalysisPanel({
  view,
  state,
  handle,
}: Pick<TabProps, "view" | "state" | "handle">) {
  const [title, setTitle] = useState(state.draft_analysis.title);
  const [conclusion, setConclusion] = useState(state.draft_analysis.conclusion);
  useEffect(() => {
    setTitle(state.draft_analysis.title);
    setConclusion(state.draft_analysis.conclusion);
  }, [state.draft_analysis.title, state.draft_analysis.conclusion]);

  const chartIds = state.draft_analysis.chart_ids;
  const canSave = title.trim().length > 0 && chartIds.length > 0;

  return (
    <section className="qr-section">
      <h2 className="qr-section__title">Draft analysis</h2>
      <div className="qr-draft">
        <input
          placeholder="Analysis title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <textarea
          placeholder="Conclusion — what does the data say?"
          value={conclusion}
          onChange={(e) => setConclusion(e.target.value)}
        />
        <div className="qr-draft__chart-ids">
          {chartIds.length === 0 ? (
            <span>
              No charts pinned yet — click a pre-built analysis above.
            </span>
          ) : (
            chartIds.map((id) => <code key={id}>{id}</code>)
          )}
        </div>
        <div className="qr-inline-actions">
          <AsyncButton
            variant="primary"
            disabled={!canSave}
            loadingLabel="Saving"
            onClick={() =>
              handle.callTool("save_quarterly_analysis", {
                view_id: view.view_id,
                title,
                conclusion,
                chart_ids: chartIds,
              })
            }
          >
            Save analysis
          </AsyncButton>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {state.status_message}
          </span>
        </div>
      </div>
    </section>
  );
}

function SavedAnalysesTab({ state }: Pick<TabProps, "state">) {
  if (state.saved_analyses.length === 0) {
    return (
      <div className="qr-main">
        <section className="qr-section">
          <div className="qr-loading">
            No analyses saved yet. Head to Deep Dive and add one.
          </div>
        </section>
      </div>
    );
  }
  return (
    <div className="qr-main">
      {state.saved_analyses.map((a) => (
        <article key={a.finding_id} className="qr-saved-item">
          <strong>{a.title}</strong>
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
            {a.conclusion}
          </p>
          <div className="qr-draft__chart-ids">
            {a.chart_ids.map((id) => (
              <code key={id}>{id}</code>
            ))}
            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
              {a.quarter || state.current_quarter}
            </span>
          </div>
        </article>
      ))}
    </div>
  );
}

function PublishTab({ view, state, handle }: TabProps) {
  const [title, setTitle] = useState(
    `Quarterly Review — ${state.current_quarter}`,
  );
  const [publishedUri, setPublishedUri] = useState<string | null>(null);

  const preview = useMemo(
    () =>
      [
        `# ${title}`,
        `_Comparison: **${state.compare_quarter}** (${state.compare_mode})_`,
        "",
        "## Key takeaways",
        "",
        "| Takeaway | Evidence | Why it matters |",
        "|---|---|---|",
        ...state.saved_analyses.map(
          (a) =>
            `| ${a.title} | ${a.chart_ids.join(", ") || "—"} | ${a.conclusion.slice(0, 140)} |`,
        ),
        "",
        state.priorities.length
          ? "## Next-quarter priorities\n" +
            state.priorities.map((p) => `- ${p.statement}`).join("\n")
          : "",
        state.action_items.length
          ? "## Action items\n" +
            state.action_items.map((a) => `- ${a.statement}`).join("\n")
          : "",
      ]
        .filter(Boolean)
        .join("\n"),
    [title, state],
  );

  return (
    <div className="qr-main qr-publish">
      <section className="qr-section">
        <h2 className="qr-section__title">Publish</h2>
        <p style={{ margin: 0, fontSize: 13 }}>
          {state.saved_analyses.length} analyses · {state.priorities.length}{" "}
          priorities · {state.action_items.length} actions
        </p>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Report title"
        />
        <CollapsibleSection title="Markdown preview" defaultOpen tone="subtle">
          <pre>{preview}</pre>
        </CollapsibleSection>
        <div className="qr-publish__actions">
          <AsyncButton
            variant="primary"
            loadingLabel="Publishing"
            disabled={state.saved_analyses.length === 0}
            onClick={async () => {
              await handle.callTool("publish_quarterly_review", {
                view_id: view.view_id,
                title,
              });
              // The server response lands in view.provenance.file_uri via
              // payload_to_call_tool_result; read it from the latest view.
              const uri =
                (handle.view?.provenance?.file_uri as string | undefined) ??
                null;
              setPublishedUri(uri);
            }}
          >
            Publish report
          </AsyncButton>
          {publishedUri && (
            <a
              href={publishedUri}
              target="_blank"
              rel="noopener"
              style={{ fontSize: 13 }}
            >
              Open report →
            </a>
          )}
        </div>
      </section>
    </div>
  );
}

function NotesPanel({ view, state, handle }: TabProps) {
  const [draft, setDraft] = useState("");
  const [kind, setKind] = useState<"observation" | "priority" | "action">(
    "observation",
  );
  return (
    <aside className="qr-aside">
      <section className="qr-section">
        <h2 className="qr-section__title">Living guide</h2>
        <SegmentedControl<"observation" | "priority" | "action">
          ariaLabel="Note kind"
          size="sm"
          value={kind}
          onChange={setKind}
          options={[
            { value: "observation", label: "Note" },
            { value: "priority", label: "Priority" },
            { value: "action", label: "Action" },
          ]}
        />
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            kind === "action"
              ? "What needs doing?"
              : kind === "priority"
              ? "Priority for next quarter"
              : "Observation, assumption, caveat…"
          }
        />
        <AsyncButton
          variant="secondary"
          disabled={!draft.trim()}
          onClick={async () => {
            await handle.callTool("record_quarterly_note", {
              view_id: view.view_id,
              kind,
              statement: draft,
            });
            setDraft("");
          }}
        >
          Add
        </AsyncButton>
        <ul className="qr-notes-list">
          {state.notes.length === 0 && state.priorities.length === 0 && state.action_items.length === 0 && (
            <li>
              <small>—</small>
              No notes yet. Use this panel as the running analyst log.
            </li>
          )}
          {state.notes.map((n) => (
            <li key={n.id}>
              <small>{n.kind}</small>
              {n.statement}
            </li>
          ))}
          {state.priorities.map((p) => (
            <li key={p.id}>
              <small>priority</small>
              {p.statement}
            </li>
          ))}
          {state.action_items.map((a) => (
            <li key={a.id}>
              <small>action</small>
              {a.statement}
            </li>
          ))}
        </ul>
      </section>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export function QuarterlyReviewApp() {
  const handle = useMiniApp<QuarterlyReviewState>({
    appId: APP_ID,
    mockPayload: DEV_MOCK_ENABLED ? buildMockPayload() : undefined,
    // Heavy app with many rapid filter/tab changes — bump debounce so the
    // host LLM only sees settled view state (plan refinement §11b item 3).
    modelContextDebounceMs: 500,
  });
  const { view } = handle;
  const [isDark, setIsDark] = useState(
    () => document.documentElement.dataset.theme !== "light",
  );

  useEffect(() => {
    const obs = new MutationObserver(() =>
      setIsDark(document.documentElement.dataset.theme !== "light"),
    );
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!view?.view_state) return;
    const s = view.view_state;
    handle.updateModelContext({
      quarter: s.current_quarter,
      compare: s.compare_quarter,
      compare_mode: s.compare_mode,
      tab: s.active_tab,
      family: s.selected_family,
      saved_count: s.saved_analyses.length,
    });
  }, [
    view?.view_state?.current_quarter,
    view?.view_state?.compare_quarter,
    view?.view_state?.active_tab,
    view?.view_state?.selected_family,
    view?.view_state?.saved_analyses.length,
    handle,
    view?.view_state,
  ]);

  if (!view || !view.view_state) {
    return (
      <MiniAppChrome activeTabId="quarterly">
      <div className="qr-loading">
        Open the Quarterly Review from a chat prompt (or append <code>?demo=loaded</code>
        in dev).
      </div>
      </MiniAppChrome>
    );
  }

  const state = view.view_state;
  const patch = (delta: Record<string, unknown>) =>
    handle.callTool("update_quarterly_review_focus", {
      view_id: view.view_id,
      ...delta,
    });

  const tabs: TabDef<QuarterlyTab>[] = [
    { id: "overview", label: "Overview" },
    { id: "compare", label: "Compare" },
    { id: "deep_dive", label: "Deep Dive" },
    {
      id: "saved",
      label: "Saved",
      badge: state.saved_analyses.length || undefined,
    },
    {
      id: "publish",
      label: "Publish",
      disabled: state.saved_analyses.length === 0,
    },
  ];

  return (
    <MiniAppChrome activeTabId="quarterly">
    <div className="qr-canvas">
      <header className="qr-header">
        <div>
          <h1>{view.title}</h1>
          <div className="qr-subtitle">
            view_id: {view.view_id.slice(0, 8)} · project:{" "}
            {state.project_id.slice(0, 10)}
          </div>
        </div>
        <div className="qr-header__controls">
          <label className="qr-picker">
            <span>Quarter</span>
            <select
              value={state.current_quarter}
              onChange={(e) => void patch({ quarter: e.target.value })}
            >
              {state.available_quarters.map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
            </select>
          </label>
          <label className="qr-picker">
            <span>vs</span>
            <select
              value={state.compare_mode}
              onChange={(e) => void patch({ compare_mode: e.target.value })}
            >
              <option value="prior_quarter">Prior quarter</option>
              <option value="same_quarter_last_year">Same Q last year</option>
              <option value="trailing_4q_avg">Trailing 4Q avg</option>
            </select>
          </label>
        </div>
      </header>

      <WarningBanner warnings={view.warnings ?? []} />

      <TabBar<QuarterlyTab>
        ariaLabel="Quarterly review section"
        active={state.active_tab}
        onChange={(t) => void patch({ active_tab: t })}
        tabs={tabs}
      />

      <div className="qr-body">
        <main>
          {state.active_tab === "overview" && (
            <OverviewTab view={view} state={state} handle={handle} isDark={isDark} />
          )}
          {state.active_tab === "compare" && (
            <CompareTab view={view} state={state} handle={handle} isDark={isDark} />
          )}
          {state.active_tab === "deep_dive" && (
            <DeepDiveTab view={view} state={state} handle={handle} isDark={isDark} />
          )}
          {state.active_tab === "saved" && <SavedAnalysesTab state={state} />}
          {state.active_tab === "publish" && (
            <PublishTab view={view} state={state} handle={handle} isDark={isDark} />
          )}
        </main>
        <NotesPanel view={view} state={state} handle={handle} isDark={isDark} />
      </div>
    </div>
    </MiniAppChrome>
  );
}
