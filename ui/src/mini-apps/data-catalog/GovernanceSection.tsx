import { useEffect, useState } from "react";
import type { CallTool, CatalogFilters, CatalogGovernance } from "./types";

interface Props {
  callTool: CallTool;
  injected?: CatalogGovernance | null;
  onPickModule?: (module: string) => void;
  onExplore?: (partial: Partial<CatalogFilters>) => void;
  onOpenEntity?: (name: string, type: "model") => void;
}

const TIER_COLOR: Record<string, string> = {
  approved: "var(--success)",
  candidate: "var(--warning)",
  docs_only: "var(--text-muted)",
};

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div style={{ height: 6, background: "var(--surface-2)", borderRadius: 3, marginTop: 3 }}>
      <div style={{ height: 6, width: `${pct}%`, background: color, borderRadius: 3 }} />
    </div>
  );
}

export function GovernanceSection({ callTool, injected, onPickModule, onExplore, onOpenEntity }: Props) {
  const [gov, setGov] = useState<CatalogGovernance | null>(injected ?? null);
  const [loading, setLoading] = useState(!injected);

  useEffect(() => {
    if (injected) return;
    let alive = true;
    callTool<CatalogGovernance>("catalog_governance", {})
      .then((g) => alive && setGov(g))
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [callTool, injected]);

  if (loading) return <div className="dc-empty">Loading governance…</div>;
  if (!gov?.available) return <div className="dc-empty">Governance data unavailable.</div>;

  const n = gov.model_count ?? 0;
  const tiers = gov.tiers ?? {};
  const tierEntries = Object.entries(tiers).sort((a, b) => b[1] - a[1]);
  const owners = gov.ownership ?? [];
  const ownerMax = owners.reduce((m, o) => Math.max(m, o.count), 1);
  const cls = gov.classification ?? { restricted: 0, public: 0 };
  const docs = gov.doc_coverage_by_module ?? [];

  const approved = tiers.approved ?? 0;
  const ownedCount = owners.filter((o) => o.owner !== "(unowned)").reduce((s, o) => s + o.count, 0);
  const ownedPct = n ? Math.round((ownedCount / n) * 100) : 0;
  const unownedCount = gov.unowned_count ?? n - ownedCount;
  const unowned = gov.unowned_sample ?? [];
  // Governance counts are model-basis, so scope the drill to models (otherwise
  // the search result set — which spans metrics/glossary too — won't match the card).
  const drillTier = (t: string) => onExplore?.({ tier: t, entityTypes: ["model"] });

  return (
    <div className="dc-root">
      <div className="dc-stat-grid" style={{ marginBottom: 22 }}>
        <div className="dc-stat"><div className="dc-stat-label">Models</div><div className="dc-stat-value">{n.toLocaleString()}</div></div>
        <div className="dc-stat"><div className="dc-stat-label">Owned</div><div className={`dc-stat-value is-${ownedPct >= 80 ? "ok" : ownedPct >= 60 ? "warn" : "bad"}`}>{ownedPct}%</div></div>
        <button className="dc-stat dc-stat--action" type="button" onClick={() => drillTier("approved")}>
          <div className="dc-stat-label">Approved tier</div><div className="dc-stat-value">{n ? Math.round((approved / n) * 100) : 0}%</div>
        </button>
        <div className="dc-stat"><div className="dc-stat-label">Restricted</div><div className="dc-stat-value is-warn">{cls.restricted}</div></div>
      </div>

      {unowned.length > 0 && (
        <div style={{ marginBottom: 22 }}>
          <div className="dc-section-title">Unowned worklist <span className="dc-results-count">({unownedCount.toLocaleString()} models without an owner)</span></div>
          <div className="dc-list">
            {unowned.map((u) =>
              onOpenEntity ? (
                <button className="dc-row" type="button" key={u.name} onClick={() => onOpenEntity(u.name, "model")} title={`Open ${u.name}`}>
                  <span aria-hidden style={{ color: "var(--text-muted)" }}>○</span>
                  <span className="dc-row-name">{u.name}</span>
                  {u.module && <span className="dc-badge">{u.module}</span>}
                  <i className="dc-row-chevron" aria-hidden>›</i>
                </button>
              ) : (
                <div className="dc-row" key={u.name}>
                  <span className="dc-row-name">{u.name}</span>
                  {u.module && <span className="dc-badge">{u.module}</span>}
                </div>
              ),
            )}
          </div>
        </div>
      )}

      <div className="dc-layout dc-layout--gov">
        <div>
          <div className="dc-section-title">Ownership</div>
          <div className="dc-list">
            {owners.slice(0, 8).map((o) => {
              const drillable = onExplore && o.owner !== "(unowned)";
              const body = (
                <>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
                    <span>{o.owner}</span>
                    <span className="dc-results-count">{o.count}</span>
                  </div>
                  <Bar value={o.count} max={ownerMax} color="var(--accent-text)" />
                </>
              );
              return drillable ? (
                <button key={o.owner} type="button" className="dc-owner-row" onClick={() => onExplore!({ owner: o.owner, entityTypes: ["model"] })} title={`Browse ${o.owner} models`}>
                  {body}
                </button>
              ) : (
                <div key={o.owner}>{body}</div>
              );
            })}
          </div>
        </div>

        <div>
          <div className="dc-section-title">Semantic tier</div>
          <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden" }}>
            {tierEntries.map(([t, c]) => (
              <span key={t} title={`${t}: ${c}`} style={{ flex: c, background: TIER_COLOR[t] ?? "var(--text-muted)" }} />
            ))}
          </div>
          <div className="dc-list" style={{ marginTop: 8 }}>
            {tierEntries.map(([t, c]) => {
              const drillable = onExplore && ["approved", "candidate", "docs_only"].includes(t);
              const inner = (
                <>
                  <span className="dc-dot" style={{ background: TIER_COLOR[t] ?? "var(--text-muted)" }} />
                  <span style={{ flex: 1, textAlign: "left" }}>{t.replace(/_/g, " ")}</span>
                  <span className="dc-results-count">{c}</span>
                </>
              );
              return drillable ? (
                <button key={t} type="button" className="dc-row" onClick={() => drillTier(t)} title={`Browse ${t} models`}>
                  {inner}
                </button>
              ) : (
                <div key={t} className="dc-row">{inner}</div>
              );
            })}
          </div>

          <div className="dc-section-title" style={{ marginTop: 20 }}>Classification</div>
          <div className="dc-list">
            <div className="dc-row"><span className="dc-dot dc-dot--muted" /><span style={{ flex: 1 }}>public</span><span className="dc-results-count">{cls.public}</span></div>
            <div className="dc-row"><span className="dc-dot dc-dot--warn" /><span style={{ flex: 1 }}>privacy-restricted</span><span className="dc-results-count">{cls.restricted}</span></div>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 20 }}>
        <div className="dc-section-title">Doc coverage by domain <span className="dc-results-count">(worst first · {docs.length} domains)</span></div>
        <div className="dc-cov-grid">
          {[...docs].sort((a, b) => a.pct - b.pct).map((d) => (
            <button
              key={d.module}
              type="button"
              className="dc-row"
              style={{ flexDirection: "column", alignItems: "stretch", gap: 4 }}
              onClick={() => onPickModule?.(d.module)}
              title={`Browse ${d.module} (${d.documented}/${d.total} columns documented)`}
            >
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, width: "100%" }}>
                <span>{d.module}</span>
                <span className="dc-results-count">{d.pct}%</span>
              </div>
              <Bar value={d.pct} max={100} color={d.pct >= 80 ? "var(--success)" : d.pct >= 50 ? "var(--warning)" : "var(--error)"} />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
