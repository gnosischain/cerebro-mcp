import type { ReactNode } from "react";
import type { EntityType } from "./types";

const TYPE_GLYPH: Record<EntityType, string> = {
  model: "▦",
  metric: "∑",
  glossary: "❖",
};

const TYPE_LABEL: Record<EntityType, string> = {
  model: "Model",
  metric: "Metric",
  glossary: "Glossary",
};

export function TypeIcon({ type }: { type: EntityType }) {
  return (
    <span className="dc-card-icon" aria-hidden title={TYPE_LABEL[type] ?? type}>
      {TYPE_GLYPH[type] ?? "•"}
    </span>
  );
}

export function typeLabel(type: EntityType): string {
  return TYPE_LABEL[type] ?? type;
}

/** Tier badge — class drives the token-based color (approved/candidate/docs_only). */
export function TierBadge({ tier }: { tier: string }) {
  if (!tier) return null;
  return <span className={`dc-badge dc-badge--${tier}`}>{tier.replace(/_/g, " ")}</span>;
}

export function TypeBadge({ type }: { type: EntityType }) {
  return <span className="dc-badge dc-badge--type">{TYPE_LABEL[type] ?? type}</span>;
}

export function Tags({ tags, max = 6 }: { tags: string[]; max?: number }) {
  if (!tags || tags.length === 0) return null;
  const shown = tags.slice(0, max);
  const extra = tags.length - shown.length;
  return (
    <span className="dc-tags">
      {shown.map((t) => (
        <span key={t} className="dc-tag">
          {t}
        </span>
      ))}
      {extra > 0 && <span className="dc-tag">+{extra}</span>}
    </span>
  );
}

/** Small colored status dot (success/warning/danger/neutral). */
export function StatusDot({ tone }: { tone: "ok" | "warn" | "bad" | "muted" }) {
  return <span className={`dc-dot dc-dot--${tone}`} aria-hidden />;
}

/** A first-class "feature not available yet" panel — the PRIMARY state for the
 * Elementary-gated tabs until the ClickHouse grant lands. Never an error. */
export function AvailabilityNotice({ title, reason }: { title: string; reason?: string }) {
  return (
    <div className="dc-notice">
      <i className="dc-notice-icon" aria-hidden>◷</i>
      <div>
        <div className="dc-notice-title">{title}</div>
        <div className="dc-notice-reason">
          {reason || "Lights up automatically when Elementary observability is connected."}
        </div>
      </div>
    </div>
  );
}

/** Content-shaped loading placeholders (replace blank-then-snap text states). */
export function SkeletonRows({ count = 6 }: { count?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="dc-skel dc-skel-row" />
      ))}
    </div>
  );
}

export function SkeletonBlock({ height = 280 }: { height?: number }) {
  return <div className="dc-skel dc-skel-card" style={{ height }} aria-busy="true" aria-label="Loading" />;
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function testTone(status: string): "ok" | "warn" | "bad" | "muted" {
  const s = (status || "").toLowerCase();
  if (s === "pass" || s === "success") return "ok";
  if (s === "warn") return "warn";
  if (s === "fail" || s === "error") return "bad";
  return "muted";
}

const SQL_KEYWORDS = new Set([
  "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "ON", "USING",
  "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET", "AS", "AND", "OR", "NOT", "IN", "IS", "NULL",
  "LIKE", "ILIKE", "BETWEEN", "CASE", "WHEN", "THEN", "ELSE", "END", "UNION", "ALL", "ANY", "DISTINCT",
  "WITH", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "VIEW", "MATERIALIZED",
  "DROP", "ALTER", "PRIMARY", "KEY", "DEFAULT", "CAST", "OVER", "PARTITION", "WINDOW", "ASC", "DESC",
  "EXISTS", "ARRAY", "TUPLE", "MAP", "INTERVAL", "TRUE", "FALSE", "IF", "GLOBAL", "PREWHERE", "FINAL",
  "SETTINGS", "FORMAT", "SAMPLE", "ARRAY", "LATERAL", "QUALIFY", "EXCEPT", "INTERSECT",
]);

/** Dependency-free SQL syntax highlighter — tokenizes comments/strings/numbers/
 * keywords and wraps them in token-colored spans. */
export function highlightSql(sql: string): ReactNode[] {
  const re = /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:''|[^'])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$]*)|(\s+)|([\s\S])/g;
  const out: ReactNode[] = [];
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(sql)) !== null) {
    const [full, comment, str, num, word] = m;
    if (comment) out.push(<span key={i++} className="dc-sql-com">{full}</span>);
    else if (str) out.push(<span key={i++} className="dc-sql-str">{full}</span>);
    else if (num) out.push(<span key={i++} className="dc-sql-num">{full}</span>);
    else if (word && SQL_KEYWORDS.has(word.toUpperCase())) out.push(<span key={i++} className="dc-sql-kw">{full}</span>);
    else out.push(full);
  }
  return out;
}

export function KeyVals({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <div className="dc-props">
      {rows.map(([k, v]) => (
        <div className="dc-prop" key={k}>
          <span className="dc-prop-label">{k}</span>
          <span className="dc-prop-value">{v || "—"}</span>
        </div>
      ))}
    </div>
  );
}
