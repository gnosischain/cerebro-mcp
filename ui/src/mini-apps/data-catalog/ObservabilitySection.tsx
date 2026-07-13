import { useEffect, useState } from "react";
import { TabBar, type TabDef } from "../shared/TabBar";
import { AvailabilityNotice, StatusDot, testTone } from "./components";
import type { CallTool, CatalogObservability, RunRow } from "./types";

type ObsTabId = "attention" | "runs" | "inactive";

interface Props {
  callTool: CallTool;
  injected?: CatalogObservability | null;
  onOpenEntity?: (name: string, type: "model") => void;
}

function StatCard({ label, value, tone }: { label: string; value: string | number; tone?: "ok" | "warn" | "bad" }) {
  const cls = tone === "ok" ? " is-ok" : tone === "warn" ? " is-warn" : tone === "bad" ? " is-bad" : "";
  return (
    <div className="dc-stat">
      <div className="dc-stat-label">{label}</div>
      <div className={`dc-stat-value${cls}`}>{value}</div>
    </div>
  );
}

function RunList({ rows, onOpenEntity }: { rows: RunRow[]; onOpenEntity?: (name: string, type: "model") => void }) {
  return (
    <div className="dc-list">
      {rows.map((a) =>
        onOpenEntity ? (
          <button className="dc-row" type="button" key={a.name} onClick={() => onOpenEntity(a.name, "model")} title={`Open ${a.name}`}>
            <StatusDot tone={testTone(a.status)} />
            <span className="dc-row-name">{a.name}</span>
            <span className={`dc-badge dc-badge--${testTone(a.status)}`}>{a.status}</span>
            <i className="dc-row-chevron" aria-hidden>›</i>
          </button>
        ) : (
          <div className="dc-row" key={a.name}>
            <StatusDot tone={testTone(a.status)} />
            <span className="dc-row-name">{a.name}</span>
            <span className={`dc-badge dc-badge--${testTone(a.status)}`}>{a.status}</span>
          </div>
        ),
      )}
    </div>
  );
}

type RecentRun = NonNullable<CatalogObservability["recent_runs"]>[number];

// Live-pipeline models run continuously (every few minutes), so in a
// latest-per-model list they always crowd out the daily batch models.
// They are identified by a `live` token in the model name
// (stg_live__*, int_live__*, api_execution_live_*, *_events_live, ...).
const LIVE_NAME_RE = /(^|_)live(_|$)/i;

const RECENT_RUNS_SHOWN = 15;

function RunTable({ rows, onOpenEntity }: { rows: RecentRun[]; onOpenEntity?: Props["onOpenEntity"] }) {
  return (
    <div className="dc-table-wrap">
      <table className="dc-table">
        <thead>
          <tr><th>Status</th><th>Model</th><th>Completed</th><th>Duration</th></tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><span className="dc-status-cell"><StatusDot tone={testTone(r.status)} /> {r.status}</span></td>
              <td style={{ fontFamily: "var(--font-mono, monospace)" }}>
                {onOpenEntity ? (
                  <button
                    type="button"
                    onClick={() => onOpenEntity(r.name, "model")}
                    style={{ background: "none", border: "none", padding: 0, font: "inherit", color: "var(--accent-text)", cursor: "pointer" }}
                  >
                    {r.name}
                  </button>
                ) : (
                  r.name
                )}
              </td>
              <td>{r.completed_at}</td>
              <td>{r.execution_time != null ? `${Number(r.execution_time).toFixed(2)}s` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ObservabilitySection({ callTool, injected, onOpenEntity }: Props) {
  const [obs, setObs] = useState<CatalogObservability | null>(injected ?? null);
  const [loading, setLoading] = useState(!injected);
  const [tab, setTab] = useState<ObsTabId>("attention");

  useEffect(() => {
    if (injected) return;
    let alive = true;
    callTool<CatalogObservability>("catalog_observability", {})
      .then((o) => alive && setObs(o))
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [callTool, injected]);

  if (loading) return <div className="dc-empty">Loading observability…</div>;
  if (!obs?.available) {
    return (
      <AvailabilityNotice
        title="Observability not connected"
        reason={obs?.reason || "Grant SELECT on elementary.* to mcp_reader to enable run state and tests."}
      />
    );
  }

  const m = obs.models ?? { ok: 0, failed: 0, total: 0 };
  const t = obs.tests ?? { failing: 0, warning: 0, total: 0 };
  const attention = obs.needs_attention ?? [];
  const skipped = obs.skipped_downstream ?? [];
  const inactive = obs.inactive ?? [];
  const attentionCount = obs.counts?.errors ?? attention.length;
  // Microbatch models log many slices per run — collapse to latest-per-model so
  // "Recent runs" shows distinct models, not duplicate slices.
  const recent = (() => {
    const seen = new Set<string>();
    const out: typeof obs.recent_runs = [];
    for (const r of obs.recent_runs ?? []) {
      if (seen.has(r.name)) continue;
      seen.add(r.name);
      out!.push(r);
    }
    return out ?? [];
  })();
  const liveRuns = recent.filter((r) => LIVE_NAME_RE.test(r.name));
  const batchRuns = recent.filter((r) => !LIVE_NAME_RE.test(r.name));

  const ageDays = obs.as_of ? Math.floor((Date.now() - Date.parse(obs.as_of.replace(" ", "T") + "Z")) / 86400000) : 0;
  const stale = ageDays >= 2;

  return (
    <div className="dc-root">
      {stale ? (
        <div className="dc-stale-banner">
          <i aria-hidden>⚠</i>
          <span>
            Observability data is <strong>{ageDays} days old</strong> — last refreshed {obs.as_of}.
            The counts below reflect that snapshot; re-enable the elementary dbt package for live run data.
          </span>
        </div>
      ) : (
        <div className="dc-strip">
          <span className="dc-strip-item"><StatusDot tone="ok" /> Elementary data as of {obs.as_of || "—"}</span>
        </div>
      )}

      <div className="dc-stat-grid" style={{ marginBottom: 22 }}>
        <StatCard label="Models run" value={m.total.toLocaleString()} />
        <StatCard label="Succeeded" value={m.ok.toLocaleString()} tone="ok" />
        <StatCard label="Failed runs" value={m.failed} tone={m.failed > 0 ? "bad" : "ok"} />
        {m.skipped != null && <StatCard label="Skipped" value={m.skipped} tone={m.skipped > 0 ? "warn" : "ok"} />}
        <StatCard label="Failing tests" value={t.failing} tone={t.failing > 0 ? "bad" : "ok"} />
        <StatCard label="Warnings" value={t.warning} tone={t.warning > 0 ? "warn" : "ok"} />
      </div>

      <TabBar<ObsTabId>
        tabs={[
          { id: "attention", label: "Needs attention", badge: attentionCount || undefined },
          { id: "runs", label: "Recent runs", badge: recent.length || undefined },
          { id: "inactive", label: "Inactive / not in prod cron", badge: inactive.length || undefined },
        ] as TabDef<ObsTabId>[]}
        active={tab}
        onChange={setTab}
        scrollOnChange={false}
        ariaLabel="Observability views"
      />
      <div className="dc-tab-body">
        {tab === "attention" && (
          <>
            {attention.length === 0 ? (
              <div className="dc-empty">No production model failures.</div>
            ) : (
              <RunList rows={attention} onOpenEntity={onOpenEntity} />
            )}
            {skipped.length > 0 && (
              <details className="dc-collapse" style={{ marginTop: 20 }}>
                <summary className="dc-section-title" style={{ cursor: "pointer", opacity: 0.8 }}>
                  Skipped — downstream of failures <span className="dc-results-count">({skipped.length})</span>
                </summary>
                <div style={{ fontSize: 12, opacity: 0.6, margin: "6px 0 10px" }}>
                  Not broken themselves — dbt skipped these because an upstream model errored. They clear once the failures above are fixed.
                </div>
                <RunList rows={skipped} onOpenEntity={onOpenEntity} />
              </details>
            )}
          </>
        )}

        {tab === "runs" && (
          <>
            <div className="dc-section-title" style={{ marginBottom: 8 }}>
              Batch models <span className="dc-results-count">(latest run per model)</span>
            </div>
            {batchRuns.length === 0 ? (
              <div className="dc-empty">No batch model runs recorded.</div>
            ) : (
              <>
                <RunTable rows={batchRuns.slice(0, RECENT_RUNS_SHOWN)} onOpenEntity={onOpenEntity} />
                {batchRuns.length > RECENT_RUNS_SHOWN && (
                  <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
                    Showing the latest {RECENT_RUNS_SHOWN} of {batchRuns.length} models.
                  </div>
                )}
              </>
            )}

            {liveRuns.length > 0 && (
              <details className="dc-collapse" style={{ marginTop: 20 }}>
                <summary className="dc-section-title" style={{ cursor: "pointer", opacity: 0.8 }}>
                  Live pipeline <span className="dc-results-count">({liveRuns.length} models, run continuously)</span>
                </summary>
                <div style={{ fontSize: 12, opacity: 0.6, margin: "6px 0 10px" }}>
                  Streaming/near-real-time models that rerun every few minutes. Split out so they
                  do not crowd the daily batch models above.
                </div>
                <RunTable rows={liveRuns.slice(0, RECENT_RUNS_SHOWN)} onOpenEntity={onOpenEntity} />
                {liveRuns.length > RECENT_RUNS_SHOWN && (
                  <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
                    Showing the latest {RECENT_RUNS_SHOWN} of {liveRuns.length} models.
                  </div>
                )}
              </details>
            )}
          </>
        )}

        {tab === "inactive" && (
          <>
            <div style={{ fontSize: 12, opacity: 0.6, margin: "0 0 10px" }}>
              Dev/WIP or non-production models. Their status reflects the last time they ran, which may be old — they are not part of the daily production build.
            </div>
            {inactive.length === 0 ? (
              <div className="dc-empty">No inactive models.</div>
            ) : (
              <RunList rows={inactive} onOpenEntity={onOpenEntity} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
