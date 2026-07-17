// Client-side catalog filtering + facet math over the embedded catalog page,
// with a helper to decide when a server round-trip (search_metric_catalog)
// is needed because the embedded page is truncated.

import type { ReactNode } from "react";
import { createElement, Fragment } from "react";
import type { CatalogFacets, MetricCatalogEntry } from "./types";

export interface CatalogFilterState {
  query: string;
  sector: string;
  layer?: string;
  tag?: string;
  timeseries?: boolean;
}

const TIME_HINTS = new Set(["date", "day", "week", "month", "ts", "timestamp", "block_date"]);

const TIME_TYPES = ["Date", "Date32", "DateTime", "DateTime64"];

/** Strip Nullable(...) / LowCardinality(...) wrappers — mirror of the
 * backend's `_unwrap_ch_type`. */
export function unwrapChType(t: string): string {
  let out = t ?? "";
  while (/^(Nullable|LowCardinality)\(.*\)$/.test(out)) {
    out = out.slice(out.indexOf("(") + 1, -1);
  }
  return out;
}

export function isTimeType(t?: string): boolean {
  const unwrapped = unwrapChType(t ?? "");
  return TIME_TYPES.some((p) => unwrapped.startsWith(p));
}

/** Client-side twin of the backend's `_entry_is_timeseries`: can this entry
 * plot over time? */
export function entryIsTimeseries(e: MetricCatalogEntry): boolean {
  if (e.supported_time_grains && e.supported_time_grains.length > 0) return true;
  if ((e.allowed_dimensions ?? []).some((d) => TIME_HINTS.has(d.toLowerCase()))) return true;
  if ((e.columns ?? []).some((c) => TIME_HINTS.has(c.name.toLowerCase()) || isTimeType(c.type)))
    return true;
  return false;
}

/** Best time COLUMN of a model — the default X for an aggregate load.
 * Same priority as the backend's `_time_column`: typed AND name-hinted >
 * any time-typed column > untyped name hint. */
export function timeColumn(e: MetricCatalogEntry): string | null {
  const cols = e.columns ?? [];
  const typed = cols.filter((c) => isTimeType(c.type)).map((c) => c.name);
  const hinted = cols
    .filter((c) => TIME_HINTS.has(c.name.toLowerCase()))
    .map((c) => c.name);
  for (const name of hinted) {
    if (typed.includes(name)) return name;
  }
  if (typed.length > 0) return typed[0];
  return hinted[0] ?? null;
}

const NUMERIC_TYPE_HINTS = ["int", "float", "decimal", "double", "uint", "number"];

/** Numeric columns of a model (by registry type) — candidates for Y. */
export function numericColumns(e: MetricCatalogEntry): string[] {
  return (e.columns ?? [])
    .filter((c) => NUMERIC_TYPE_HINTS.some((h) => (c.type || "").toLowerCase().includes(h)))
    .map((c) => c.name);
}

/** Non-numeric, non-time columns — candidates for Series / filters. */
export function categoricalColumns(e: MetricCatalogEntry): string[] {
  const numeric = new Set(numericColumns(e));
  return (e.columns ?? [])
    .map((c) => c.name)
    .filter((n) => !numeric.has(n) && !TIME_HINTS.has(n.toLowerCase()));
}

export function filterCatalog(
  catalog: MetricCatalogEntry[],
  f: CatalogFilterState,
): MetricCatalogEntry[] {
  const q = f.query.trim().toLowerCase();
  return catalog.filter((e) => {
    if (f.sector && (e.sector || "").toLowerCase() !== f.sector.toLowerCase()) return false;
    if (f.layer && (e.layer || "") !== f.layer) return false;
    if (f.tag && !(e.tags ?? []).some((t) => t.toLowerCase() === f.tag!.toLowerCase()))
      return false;
    if (f.timeseries && !entryIsTimeseries(e)) return false;
    if (q) {
      const haystack =
        `${e.name} ${e.label} ${e.description} ${(e.tags ?? []).join(" ")}`.toLowerCase();
      if (!q.split(/\s+/).every((tok) => haystack.includes(tok))) return false;
    }
    return true;
  });
}

/** Facet counts over the query-matched (but not facet-filtered) set —
 * client-side twin of the backend's facet computation. */
export function computeFacets(
  catalog: MetricCatalogEntry[],
  query: string,
): CatalogFacets {
  const matched = filterCatalog(catalog, { query, sector: "" });
  const facets: Required<CatalogFacets> = { sector: {}, layer: {}, tag: {} };
  const tagCounts: Record<string, number> = {};
  for (const e of matched) {
    const sector = e.sector || "";
    const layer = e.layer || "";
    if (sector) facets.sector[sector] = (facets.sector[sector] ?? 0) + 1;
    if (layer) facets.layer[layer] = (facets.layer[layer] ?? 0) + 1;
    for (const t of e.tags ?? []) tagCounts[t] = (tagCounts[t] ?? 0) + 1;
  }
  facets.tag = Object.fromEntries(
    Object.entries(tagCounts)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 24),
  );
  return facets;
}

/** The embedded catalog is one page (default 200 of ~2,100). When it is
 * truncated, client-side filtering misses entries — fall through to the
 * server tool. */
export function needsServerSearch(
  embeddedCount: number,
  catalogTotal: number | undefined,
): boolean {
  if (catalogTotal === undefined) return embeddedCount >= 200;
  return catalogTotal > embeddedCount;
}

/** Highlight query tokens inside text (ported from data-catalog). */
export function Highlight({ text, query }: { text: string; query: string }): ReactNode {
  const q = query.trim();
  if (!q || !text) return text;
  const tokens = q.split(/\s+/).filter((t) => t.length > 1);
  if (tokens.length === 0) return text;
  const escaped = tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const splitRe = new RegExp(`(${escaped.join("|")})`, "ig");
  // Fresh non-global regex for the per-part test — a global regex is
  // stateful (lastIndex advances across .test calls) and would skip parts.
  const matchRe = new RegExp(`^(${escaped.join("|")})$`, "i");
  const parts = text.split(splitRe);
  return createElement(
    Fragment,
    null,
    ...parts.map((part, i) =>
      matchRe.test(part)
        ? createElement("mark", { key: i, className: "mlab-hl" }, part)
        : part,
    ),
  );
}
