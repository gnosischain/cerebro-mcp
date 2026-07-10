// URL-synced navigation state — mirrors the data-catalog pattern so views
// are deep-linkable and browser Back/Forward work. Unmanaged params
// (e.g. ?token=) are preserved on every write.

import type { ChartType, WorkspaceTab } from "./types";

export interface UrlState {
  q: string;
  sector: string;
  /** dbt layer filter: api|fct|int|stg|source ("" = all). */
  layer: string;
  tag: string;
  /** Trailing window in days for loads (0 = all). */
  window: number;
  /** Time-series scope — ON by default; URL stores ts=0 only when off. */
  ts: boolean;
  metrics: string[];
  dims: string[];
  limit: number;
  order: string; // "field:dir" or ""
  tab: WorkspaceTab;
  chart: ChartType | "";
  /** Aggregate-mode load config (x/y/agg/series reused for it). */
  mode: string;
  x: string;
  y: string;
  agg: string;
  series: string;
  topn: number;
  fcol: string;
  fop: string;
  fval: string;
  detail: string;
}

const URL_KEYS = [
  "q",
  "sector",
  "layer",
  "tag",
  "window",
  "ts",
  "metrics",
  "dims",
  "limit",
  "order",
  "tab",
  "chart",
  "mode",
  "x",
  "y",
  "agg",
  "series",
  "topn",
  "fcol",
  "fop",
  "fval",
  "detail",
];

export const DEFAULT_URL_STATE: UrlState = {
  q: "",
  sector: "",
  layer: "",
  tag: "",
  window: 0,
  ts: true,
  metrics: [],
  dims: [],
  limit: 2000,
  order: "",
  tab: "chart",
  chart: "",
  mode: "",
  x: "",
  y: "",
  agg: "sum",
  series: "",
  topn: 8,
  fcol: "",
  fop: "=",
  fval: "",
  detail: "",
};

export function readUrl(): UrlState {
  const p = new URLSearchParams(window.location.search);
  return {
    q: p.get("q") || "",
    sector: p.get("sector") || "",
    layer: p.get("layer") || "",
    tag: p.get("tag") || "",
    window: Number(p.get("window")) || 0,
    ts: p.get("ts") !== "0",
    metrics: (p.get("metrics") || "").split(",").filter(Boolean),
    dims: (p.get("dims") || "").split(",").filter(Boolean),
    limit: Number(p.get("limit")) || 2000,
    order: p.get("order") || "",
    tab: (p.get("tab") as WorkspaceTab) || "chart",
    chart: (p.get("chart") as ChartType) || "",
    mode: p.get("mode") || "",
    x: p.get("x") || "",
    y: p.get("y") || "",
    agg: p.get("agg") || "sum",
    series: p.get("series") || "",
    topn: Number(p.get("topn")) || 8,
    fcol: p.get("fcol") || "",
    fop: p.get("fop") || "=",
    fval: p.get("fval") || "",
    detail: p.get("detail") || "",
  };
}

export function writeUrl(s: UrlState, push: boolean): void {
  const p = new URLSearchParams(window.location.search);
  URL_KEYS.forEach((k) => p.delete(k));
  if (s.q.trim()) p.set("q", s.q.trim());
  if (s.sector) p.set("sector", s.sector);
  if (s.layer) p.set("layer", s.layer);
  if (s.tag) p.set("tag", s.tag);
  if (s.window) p.set("window", String(s.window));
  if (!s.ts) p.set("ts", "0");
  if (s.metrics.length) p.set("metrics", s.metrics.join(","));
  if (s.dims.length) p.set("dims", s.dims.join(","));
  if (s.limit && s.limit !== 2000) p.set("limit", String(s.limit));
  if (s.order) p.set("order", s.order);
  if (s.tab && s.tab !== "chart") p.set("tab", s.tab);
  if (s.chart) p.set("chart", s.chart);
  if (s.mode) p.set("mode", s.mode);
  if (s.x) p.set("x", s.x);
  if (s.y) p.set("y", s.y);
  if (s.agg && s.agg !== "sum") p.set("agg", s.agg);
  if (s.series) p.set("series", s.series);
  if (s.topn && s.topn !== 8) p.set("topn", String(s.topn));
  if (s.fcol) p.set("fcol", s.fcol);
  if (s.fop && s.fop !== "=") p.set("fop", s.fop);
  if (s.fval) p.set("fval", s.fval);
  if (s.detail) p.set("detail", s.detail);
  const qs = p.toString();
  const url = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
  if (push) window.history.pushState({}, "", url);
  else window.history.replaceState({}, "", url);
}
