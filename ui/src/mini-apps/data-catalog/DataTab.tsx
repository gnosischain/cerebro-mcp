import { useEffect, useState } from "react";
import { fmtBytes, fmtInt, SkeletonBlock } from "./components";
import type { CallTool, SampleData, TableStats } from "./types";

interface Props {
  entityName: string;
  callTool: CallTool;
}

function epochToLabel(value: unknown, type: string): string {
  // Date columns arrive as epoch-day ints, DateTime as ISO strings already.
  if (value === null || value === undefined || value === "") return "—";
  if (type === "Date" && typeof value === "number") {
    const d = new Date(value * 86400000);
    return Number.isNaN(d.getTime()) ? String(value) : d.toISOString().slice(0, 10);
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "string") {
    // ClickHouse binary/Nullable columns can arrive as a Python bytes repr
    // (b'\x00...') — show a compact placeholder instead of garbage.
    if (/^b['"]/.test(value) && /\\x[0-9a-f]{2}/i.test(value)) return `binary (${value.length} chars)`;
    // Cap oversized cells (base64 avatars / raw hex run 30K–48K chars) so a
    // single blob column can't flood the DOM or the native title tooltip.
    if (value.length > 200) return `${value.slice(0, 200)}… (${value.length} chars)`;
    return value;
  }
  return String(value);
}

export function DataTab({ entityName, callTool }: Props) {
  const [stats, setStats] = useState<TableStats | null>(null);
  const [sample, setSample] = useState<SampleData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([
      callTool<TableStats>("catalog_table_stats", { name: entityName }),
      callTool<SampleData>("catalog_sample", { name: entityName, limit: 20 }),
    ])
      .then(([s, sm]) => {
        if (!alive) return;
        setStats(s);
        setSample(sm);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [callTool, entityName]);

  if (loading) return <SkeletonBlock height={320} />;

  if (sample && sample.restricted) {
    return <div className="dc-notice"><i className="dc-notice-icon" aria-hidden>⃠</i>
      <div><div className="dc-notice-title">Sample restricted</div>
      <div className="dc-notice-reason">This model is privacy-restricted; row-level data is not exposed in the catalog.</div></div></div>;
  }

  const cols = sample?.columns ?? [];
  const types = sample?.column_types ?? [];
  const rows = sample?.rows ?? [];

  return (
    <div>
      <div className="dc-strip">
        <span className="dc-strip-item">▦ {stats?.row_count != null ? `${fmtInt(stats.row_count)} rows` : stats?.is_view ? "view (computed on read)" : "rows n/a"}</span>
        <span className="dc-strip-item">⛁ {stats?.size_bytes != null ? fmtBytes(stats.size_bytes) : "—"}</span>
        {stats?.materialization && <span className="dc-strip-item">⚙ {stats.materialization}</span>}
        {sample?.available && <span className="dc-strip-item" style={{ marginLeft: "auto" }}>{rows.length} sample rows</span>}
      </div>

      {!sample?.available ? (
        <div className="dc-notice"><i className="dc-notice-icon" aria-hidden>◷</i>
          <div><div className="dc-notice-title">Sample unavailable</div>
          <div className="dc-notice-reason">{sample?.reason || "No rows could be loaded."}</div></div></div>
      ) : rows.length === 0 ? (
        <div className="dc-empty">No rows returned.</div>
      ) : (
        <div className="dc-table-wrap">
          <table className="dc-table is-mono">
            <thead>
              <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {r.map((v, j) => {
                    const label = epochToLabel(v, types[j] ?? "");
                    return <td key={j} title={label}>{label}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sample?.available && (
        <div className="dc-results-count" style={{ marginTop: 8 }}>
          {sample.truncated ? "Showing first " + rows.length + " rows" : rows.length + " rows"} · live from ClickHouse
        </div>
      )}
    </div>
  );
}
