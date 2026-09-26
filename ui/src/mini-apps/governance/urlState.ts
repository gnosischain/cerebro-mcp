// Standalone-page URL state. Managed query keys only — writeUrl deletes
// EXACTLY these before re-setting, so unmanaged params (?token=… auth and
// anything else) always survive. No governance key is named `token`.
// Defaults are omitted to keep shared links clean: section=overview, the
// all-history date range and the default treasury view produce no params.
// Always replaceState: a filter change is not a navigation, and pushState
// would make Back step through every toggle.

import { isTreasuryContext } from "./state/overlay";
import {
  TREASURY_URL_KEYS,
  treasuryViewFromParams,
  writeTreasuryParams,
  type TreasuryViewState,
} from "./state/treasuryView";
import type { GovernanceViewState, GovSection } from "./types";

const URL_KEYS = [
  "section", "q", "days", "start", "end",
  "pstate", "ptype", "quorum", "cat", "fstatus", "sort",
  "entity", "id",
  ...TREASURY_URL_KEYS,
];

export interface GovUrlState {
  section: GovSection | "";
  q: string;
  /** 90 | 365 preset, 0 = explicit all-history, null = unset (default). */
  days: number | null;
  start: string;
  end: string;
  pstate: string;
  ptype: string;
  quorum: string;
  cat: number;
  fstatus: string;
  sort: string;
  entity: string;
  id: string;
  /** Treasury view keys PRESENT in the URL (client-only; see
   * state/treasuryView.ts). Any of them implies section=treasury. */
  treasury: Partial<TreasuryViewState>;
}

export function readUrl(): GovUrlState {
  const p = new URLSearchParams(window.location.search);
  const days = p.get("days");
  const treasury = treasuryViewFromParams(p);
  const hasTreasury = Object.keys(treasury).length > 0;
  return {
    // A treasury key implies its host section — they are client-only, so a
    // link like ?ttab=wallets must still land on treasury.
    section: hasTreasury ? "treasury" : ((p.get("section") as GovSection | null) ?? ""),
    q: p.get("q") || "",
    days: days === null || days === "" || !Number.isFinite(Number(days)) ? null : Number(days),
    start: p.get("start") || "",
    end: p.get("end") || "",
    pstate: p.get("pstate") || "",
    ptype: p.get("ptype") || "",
    quorum: p.get("quorum") || "",
    cat: Number(p.get("cat")) || 0,
    fstatus: p.get("fstatus") || "",
    sort: p.get("sort") || "",
    entity: p.get("entity") || "",
    id: p.get("id") || "",
    treasury,
  };
}

export function writeUrl(
  state: GovernanceViewState,
  treasuryView: TreasuryViewState | null = null,
): void {
  const p = new URLSearchParams(window.location.search);
  URL_KEYS.forEach((key) => p.delete(key));
  if (state.section !== "overview" && state.section !== "entity") {
    p.set("section", state.section);
  }
  if (state.filters.query) p.set("q", state.filters.query);
  if (state.date_range.kind === "absolute") {
    p.set("start", state.date_range.start_at);
    p.set("end", state.date_range.end_at);
  } else if (state.date_range.kind === "relative" && state.date_range.window_days) {
    p.set("days", String(state.date_range.window_days));
  }
  // kind === "all" is the default — days omitted entirely.
  if (state.filters.proposal_state) p.set("pstate", state.filters.proposal_state);
  if (state.filters.proposal_type) p.set("ptype", state.filters.proposal_type);
  if (state.filters.quorum_status) p.set("quorum", state.filters.quorum_status);
  if (state.filters.category_id) p.set("cat", String(state.filters.category_id));
  if (state.filters.forum_status) p.set("fstatus", state.filters.forum_status);
  if (state.filters.sort_by) p.set("sort", state.filters.sort_by);
  if (state.selected_entity) {
    p.set("entity", state.selected_entity.entity_type);
    p.set("id", state.selected_entity.identifier);
  }
  // The treasury view travels on the treasury section AND its entity pages
  // (a wallet page honours the hidden-token toggle), never elsewhere.
  if (treasuryView && isTreasuryContext(state)) writeTreasuryParams(p, treasuryView);
  const qs = p.toString();
  const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  window.history.replaceState({}, "", url);
}
