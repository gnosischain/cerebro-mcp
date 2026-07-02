import { useEffect, useState } from "react";
import { AvailabilityNotice, StatusDot, testTone } from "./components";
import type { CallTool, CatalogObservability } from "./types";

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

export function ObservabilitySection({ callTool, injected, onOpenEntity }: Props) {
  const [obs, setObs] = useState<CatalogObservability | null>(injected ?? null);
  const [loading, setLoading] = useState(!injected);

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

      <div className="dc-layout dc-layout--obs">
        <div>
          <div className="dc-section-title">Needs attention <span className="dc-results-count">({attention.length})</span></div>
          {attention.length === 0 ? (
            <div className="dc-empty">No failed model runs.</div>
          ) : (
            <div className="dc-list">
              {attention.map((a) =>
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
          )}
        </div>

        <div>
          <div className="dc-section-title">Recent runs <span className="dc-results-count">(latest per model)</span></div>
          <div className="dc-table-wrap">
            <table className="dc-table">
              <thead>
                <tr><th>Status</th><th>Model</th><th>Completed</th><th>Duration</th></tr>
              </thead>
              <tbody>
                {recent.map((r, i) => (
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
        </div>
      </div>
    </div>
  );
}
