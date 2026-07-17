// Deep-linkable URL state (metric-lab pattern): managed params only —
// unmanaged params (?token= etc.) are PRESERVED on every write.
//
// Boot rule: only trigger loads for URL state the server payload LACKS.
// The standalone /app route already maps ?seed= onto open_graph_explorer
// (web_apps `_open_kwargs_from_query`), so a seed the payload already
// reflects must NOT fire a second load.

export interface GraphUrlState {
  mode: string;
  seed: string;
  /** Active-mode profile selection (csv). */
  profiles: string[];
  window: number;
  max: number;
  sel: string;
  esel: string;
  status: string;
  layout: string;
  depth: number;
}

const URL_KEYS = [
  "mode",
  "seed",
  "profiles",
  "window",
  "max",
  "sel",
  "esel",
  "status",
  "layout",
  "depth",
];

export function readUrl(): GraphUrlState {
  const p = new URLSearchParams(window.location.search);
  return {
    mode: p.get("mode") || "",
    seed: p.get("seed") || "",
    profiles: (p.get("profiles") || "").split(",").filter(Boolean),
    window: Number(p.get("window")) || 0,
    max: Number(p.get("max")) || 0,
    sel: p.get("sel") || "",
    esel: p.get("esel") || "",
    status: p.get("status") || "",
    layout: p.get("layout") || "",
    depth: Number(p.get("depth")) || 0,
  };
}

export function writeUrl(s: GraphUrlState, push = false): void {
  const p = new URLSearchParams(window.location.search);
  URL_KEYS.forEach((k) => p.delete(k));
  if (s.mode && s.mode !== "atlas") p.set("mode", s.mode);
  if (s.seed) p.set("seed", s.seed);
  if (s.profiles.length) p.set("profiles", s.profiles.join(","));
  if (s.window) p.set("window", String(s.window));
  if (s.max) p.set("max", String(s.max));
  if (s.sel) p.set("sel", s.sel);
  if (s.esel) p.set("esel", s.esel);
  if (s.status && s.status !== "all") p.set("status", s.status);
  if (s.layout && s.layout !== "force") p.set("layout", s.layout);
  if (s.depth && s.depth !== 1) p.set("depth", String(s.depth));
  const qs = p.toString();
  const url = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
  if (push) window.history.pushState({}, "", url);
  else window.history.replaceState({}, "", url);
}
