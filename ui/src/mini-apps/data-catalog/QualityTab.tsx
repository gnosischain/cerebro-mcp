import { useEffect, useState } from "react";
import { AvailabilityNotice, StatusDot, testTone } from "./components";
import type { CallTool, CatalogEntity, TestResults } from "./types";

interface Props {
  entity: CatalogEntity;
  callTool: CallTool;
}

const SEV: Record<string, number> = { bad: 3, warn: 2, ok: 1, muted: 0 };

export function QualityTab({ entity, callTool }: Props) {
  const [results, setResults] = useState<TestResults | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    callTool<TestResults>("catalog_test_results", { name: entity.name })
      .then((r) => alive && setResults(r))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [callTool, entity.name]);

  const definedTests = entity.test_count ?? 0;

  return (
    <div>
      <div className="dc-kpi-grid">
        <div className="dc-kpi"><div className="dc-kpi-label">Defined tests</div><div className="dc-kpi-value">{definedTests}</div></div>
        <div className="dc-kpi"><div className="dc-kpi-label">Columns</div><div className="dc-kpi-value">{entity.column_count}</div></div>
        <div className="dc-kpi">
          <div className="dc-kpi-label">Documented cols</div>
          <div className="dc-kpi-value">
            {entity.columns.filter((c) => c.description).length}/{entity.column_count}
          </div>
        </div>
      </div>

      <div className="dc-group-title" style={{ marginTop: 4 }}>Test results</div>
      {loading ? (
        <div className="dc-empty">Loading test results…</div>
      ) : !results?.available ? (
        <AvailabilityNotice title="Test pass/fail unavailable" reason={results?.reason} />
      ) : (results.tests ?? []).length === 0 ? (
        <div className="dc-empty">No test results recorded for this model.</div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "0 0 10px" }}>
            {(["fail", "error", "warn", "pass"] as const).map((k) => {
              const n = (results.counts ?? {})[k] || 0;
              if (!n) return null;
              const tone = k === "pass" ? "ok" : k === "warn" ? "warn" : "bad";
              return <span key={k} className={`dc-badge dc-badge--${tone}`}>{n} {k}</span>;
            })}
          </div>
          <div className="dc-list">
            {[...(results.tests ?? [])]
              .sort((a, b) => SEV[testTone(b.status)] - SEV[testTone(a.status)])
              .map((t, i) => (
                <div className="dc-row" key={`${t.name}-${i}`}>
                  <StatusDot tone={testTone(t.status)} />
                  <span className="dc-row-name" title={t.name}>{t.name}</span>
                  <span className={`dc-badge dc-badge--${testTone(t.status)}`}>{t.status}</span>
                </div>
              ))}
          </div>
        </>
      )}
    </div>
  );
}
