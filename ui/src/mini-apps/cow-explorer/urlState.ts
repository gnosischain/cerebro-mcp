import type { CowExplorerViewState, CowSection, EnvironmentScope } from "./types";

// Managed query keys. writeUrl deletes ONLY these before re-setting, so
// unmanaged params (?token=… auth) always survive. Defaults are omitted to
// keep shared links clean.
const URL_KEYS = [
  "scope", "chain", "section", "base", "quote", "interval", "start", "end",
  "entity", "id", "days", "owner", "token_f", "status", "solver",
];

//: Mirror of the server's SECTION_DEFAULT_DAYS — default windows are omitted
//: from shared links.
const SECTION_DEFAULT_DAYS: Partial<Record<CowSection, number>> = {
  overview: 30,
  markets: 30,
  trades: 7,
  orders: 30,
  auctions: 30,
  solvers: 30,
  live: 1,
};

export interface CowUrlState {
  scope: EnvironmentScope | "";
  chain: number;
  section: CowSection | "";
  base: string;
  quote: string;
  interval: string;
  start: string;
  end: string;
  entity: string;
  id: string;
  /** window_days; -1 = unset (section default), 0 = all indexed history. */
  days: number;
  owner: string;
  /** token filter — `token_f` in the URL to avoid clashing with ?token= auth. */
  token: string;
  status: string;
  solver: string;
}

export function readUrl(): CowUrlState {
  const p = new URLSearchParams(window.location.search);
  const days = p.get("days");
  return {
    scope: (p.get("scope") as EnvironmentScope | null) ?? "",
    chain: Number(p.get("chain")) || 0,
    section: (p.get("section") as CowSection | null) ?? "",
    base: p.get("base") || "",
    quote: p.get("quote") || "",
    interval: p.get("interval") || "",
    start: p.get("start") || "",
    end: p.get("end") || "",
    entity: p.get("entity") || "",
    id: p.get("id") || "",
    days: days === null || days === "" || !Number.isFinite(Number(days)) ? -1 : Number(days),
    owner: p.get("owner") || "",
    token: p.get("token_f") || "",
    status: p.get("status") || "",
    solver: p.get("solver") || "",
  };
}

export function writeUrl(state: CowExplorerViewState, push = false): void {
  const p = new URLSearchParams(window.location.search);
  URL_KEYS.forEach((key) => p.delete(key));
  if (state.environment_scope !== "production") p.set("scope", state.environment_scope);
  if (state.chain_id) p.set("chain", String(state.chain_id));
  if (state.section !== "overview" && state.section !== "entity") p.set("section", state.section);
  if (state.pair.base) p.set("base", state.pair.base);
  if (state.pair.quote) p.set("quote", state.pair.quote);
  if (state.interval && state.interval !== "1h") p.set("interval", state.interval);
  if (state.date_range.kind === "absolute") {
    p.set("start", state.date_range.start_at);
    p.set("end", state.date_range.end_at);
  } else if (state.date_range.kind === "all") {
    p.set("days", "0");
  } else if (
    state.date_range.window_days !== null
    && state.date_range.window_days !== undefined
    && state.date_range.window_days !== SECTION_DEFAULT_DAYS[state.section]
  ) {
    p.set("days", String(state.date_range.window_days));
  }
  if (state.filters.owner) p.set("owner", state.filters.owner);
  if (state.filters.token) p.set("token_f", state.filters.token);
  if (state.filters.status) p.set("status", state.filters.status);
  if (state.filters.solver) p.set("solver", state.filters.solver);
  if (state.selected_entity) {
    p.set("entity", state.selected_entity.entity_type);
    p.set("id", state.selected_entity.identifier);
  }
  const qs = p.toString();
  const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  if (push) window.history.pushState({}, "", url);
  else window.history.replaceState({}, "", url);
}
