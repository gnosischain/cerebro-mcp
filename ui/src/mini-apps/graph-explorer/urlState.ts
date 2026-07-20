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
  tgrain: string;
  trange: number;
  twin: number;
  /** Flows deep-link state. */
  fseeds: string[];
  fdir: string;
  fhops: number;
  fmin: number;
  frange: number;
  ftok: string[];
  /** Transaction Detail deep-link state. */
  txhashes: string[];
  txseed: string;
  txcounterparties: string[];
  txtokens: string[];
  txrange: number;
  txmax: number;
  txt0: string;
  txt1: string;
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
  "tgrain",
  "trange",
  "twin",
  "fseeds",
  "fdir",
  "fhops",
  "fmin",
  "frange",
  "ftok",
  "txhashes",
  "txseed",
  "txcounterparties",
  "txtokens",
  "txrange",
  "txmax",
  "txt0",
  "txt1",
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
    tgrain: p.get("tgrain") || "",
    trange: Number(p.get("trange")) || 0,
    twin: Number(p.get("twin")) || 0,
    fseeds: (p.get("fseeds") || "").split(",").filter(Boolean),
    fdir: p.get("fdir") || "",
    fhops: Number(p.get("fhops")) || 0,
    fmin: Number(p.get("fmin")) || 0,
    frange: Number(p.get("frange")) || 0,
    ftok: (p.get("ftok") || "").split(",").filter(Boolean),
    txhashes: (p.get("txhashes") || "").split(",").filter(Boolean),
    txseed: p.get("txseed") || "",
    txcounterparties: (p.get("txcounterparties") || "").split(",").filter(Boolean),
    txtokens: (p.get("txtokens") || "").split(",").filter(Boolean),
    txrange: Number(p.get("txrange")) || 0,
    txmax: Number(p.get("txmax")) || 0,
    txt0: p.get("txt0") || "",
    txt1: p.get("txt1") || "",
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
  if (s.tgrain && s.tgrain !== "week") p.set("tgrain", s.tgrain);
  if (s.trange && s.trange !== 365) p.set("trange", String(s.trange));
  if (s.twin && s.twin !== 4) p.set("twin", String(s.twin));
  if (s.fseeds.length) p.set("fseeds", s.fseeds.join(","));
  if (s.fdir && s.fdir !== "out") p.set("fdir", s.fdir);
  if (s.fhops && s.fhops !== 2) p.set("fhops", String(s.fhops));
  if (s.fmin && s.fmin !== 10) p.set("fmin", String(s.fmin));
  if (s.frange && s.frange !== 30) p.set("frange", String(s.frange));
  if (s.ftok.length) p.set("ftok", s.ftok.join(","));
  if (s.txhashes.length) p.set("txhashes", s.txhashes.join(","));
  if (s.txseed) p.set("txseed", s.txseed);
  if (s.txcounterparties.length) {
    p.set("txcounterparties", s.txcounterparties.join(","));
  }
  if (s.txtokens.length) p.set("txtokens", s.txtokens.join(","));
  if (s.txrange && s.txrange !== 30) p.set("txrange", String(s.txrange));
  if (s.txmax && s.txmax !== 25) p.set("txmax", String(s.txmax));
  if (s.txt0) p.set("txt0", s.txt0);
  if (s.txt1) p.set("txt1", s.txt1);
  const qs = p.toString();
  const url = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
  if (push) window.history.pushState({}, "", url);
  else window.history.replaceState({}, "", url);
}
