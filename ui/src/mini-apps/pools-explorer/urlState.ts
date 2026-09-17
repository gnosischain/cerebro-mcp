// Standalone-page URL state. Managed query keys only — writeUrl deletes
// EXACTLY these before re-setting, so unmanaged params (?token=… auth and
// anything else) always survive. NO managed key is named `token`: the token
// filter travels as `tok`. Defaults are omitted to keep shared links clean.
//
// Server state keys: section, entity, id, date (as_of), days (window), q,
// class, family, fee (a band id like `b3000` or exact pips), probed, live,
// tok, sort. Client-only keys (pool detail): tab, zoom, axis, view, dir
// (price orientation, `10` = token0 per token1).

import { isHeatmapWindow } from "./model/profileHeatmap";
import {
  DEFAULT_AXIS, DEFAULT_ZOOM, isAxisMode, isZoomPreset, type AxisMode, type ZoomPreset,
} from "./model/liquidityProfile";
import {
  DEFAULT_POOL_TAB, DEFAULT_PROFILE_VIEW, isEntityType, isListSection, isPoolTab,
  isProfileView, type PoolTabId, type ProfileView,
} from "./model/navGroups";
import { WINDOWS, isPlxWindow, type PlxEntityType, type PlxListSection, type PoolsExplorerViewState } from "./types";

const URL_KEYS = [
  "section", "entity", "id", "date", "days", "q", "class", "family", "fee",
  "probed", "live", "tok", "sort", "dir", "tab", "zoom", "axis", "view",
];

/** Client-only view state threaded next to the server state. */
export interface PlxClientState {
  tab: PoolTabId;
  zoom: ZoomPreset;
  axis: AxisMode;
  view: ProfileView;
  /** Price orientation: false = token1 per token0 (server), true = inverted. */
  inverted: boolean;
}

export const DEFAULT_CLIENT_STATE: PlxClientState = {
  tab: DEFAULT_POOL_TAB,
  zoom: DEFAULT_ZOOM,
  axis: DEFAULT_AXIS,
  view: DEFAULT_PROFILE_VIEW,
  inverted: false,
};

export interface PlxUrlState {
  section: PlxListSection | "";
  entity: PlxEntityType | "";
  id: string;
  /** as_of ISO date or "". */
  date: string;
  /** window: 90 | 365 presets, 0 = all history, null = unset. */
  days: number | null;
  q: string;
  class: string;
  family: string;
  /** Fee band id (`b3000`) or exact pips as text; "" = unset. */
  fee: string;
  probed: boolean;
  live: boolean;
  tok: string;
  sort: string;
  /** Price orientation; "10" = inverted, "" = default. */
  dir: "10" | "";
  tab: PoolTabId | "";
  zoom: ZoomPreset | "";
  axis: AxisMode | "";
  view: ProfileView | "";
}

/** "90d" / "1y" / "all" -> URL days; unknown -> null. */
export function windowToDays(window: string): number | null {
  const entry = WINDOWS.find((w) => w.id === window);
  return entry ? entry.days : null;
}

/** URL days -> "90d" / "1y" / "all"; anything else -> "". */
export function daysToWindow(days: number | null): string {
  if (days === null) return "";
  const entry = WINDOWS.find((w) => w.days === days);
  return entry ? entry.id : "";
}

export function readUrl(): PlxUrlState {
  const p = new URLSearchParams(window.location.search);
  const days = p.get("days");
  const rawSection = p.get("section") || "";
  const rawEntity = p.get("entity") || "";
  const rawTab = p.get("tab") || "";
  const rawZoom = p.get("zoom") || "";
  const rawAxis = p.get("axis") || "";
  const rawView = p.get("view") || "";
  return {
    section: isListSection(rawSection) ? rawSection : "",
    entity: isEntityType(rawEntity) ? rawEntity : "",
    id: p.get("id") || "",
    date: p.get("date") || "",
    days: days === null || days === "" || !Number.isFinite(Number(days)) ? null : Number(days),
    q: p.get("q") || "",
    class: p.get("class") || "",
    family: p.get("family") || "",
    fee: p.get("fee") || "",
    probed: p.get("probed") === "1",
    live: p.get("live") === "1",
    tok: p.get("tok") || "",
    sort: p.get("sort") || "",
    dir: p.get("dir") === "10" ? "10" : "",
    tab: isPoolTab(rawTab) ? rawTab : "",
    zoom: isZoomPreset(rawZoom) ? rawZoom : "",
    axis: isAxisMode(rawAxis) ? rawAxis : "",
    view: isProfileView(rawView) ? rawView : "",
  };
}

export function writeUrl(
  state: PoolsExplorerViewState,
  client: PlxClientState | null = null,
  push = false,
): void {
  const p = new URLSearchParams(window.location.search);
  URL_KEYS.forEach((key) => p.delete(key));
  const isEntity = state.section === "pool" || state.section === "token";
  if (!isEntity && state.section !== "overview") p.set("section", state.section);
  if (isEntity && state.selected_entity) {
    p.set("entity", state.selected_entity.entity_type);
    p.set("id", state.selected_entity.identifier);
  }
  if (state.as_of) p.set("date", state.as_of);
  // "1y" is the server default window — omitted so an ordinary link stays clean.
  if (state.window && state.window !== "1y" && isPlxWindow(state.window)) {
    p.set("days", String(windowToDays(state.window)));
  }
  const filters = state.filters;
  if (filters) {
    if (filters.query) p.set("q", filters.query);
    if (filters.pool_class) p.set("class", filters.pool_class);
    if (filters.pool_family) p.set("family", filters.pool_family);
    if (filters.fee_band) p.set("fee", filters.fee_band);
    else if (filters.fee) p.set("fee", String(filters.fee));
    if (filters.probed_only) p.set("probed", "1");
    if (filters.live_only) p.set("live", "1");
    if (filters.token) p.set("tok", filters.token);
    if (filters.sort_by) p.set("sort", filters.sort_by);
  }
  // Client-only pool-detail state: only while a pool is open, defaults omitted.
  if (client && state.section === "pool") {
    if (client.tab !== DEFAULT_CLIENT_STATE.tab) p.set("tab", client.tab);
    if (client.zoom !== DEFAULT_CLIENT_STATE.zoom) p.set("zoom", client.zoom);
    if (client.axis !== DEFAULT_CLIENT_STATE.axis) p.set("axis", client.axis);
    if (client.view !== DEFAULT_CLIENT_STATE.view) p.set("view", client.view);
    if (client.inverted) p.set("dir", "10");
  }
  const qs = p.toString();
  const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  if (push) window.history.pushState({}, "", url);
  else window.history.replaceState({}, "", url);
}

/** Client state seeded from a URL read (missing keys -> defaults). */
export function clientFromSeed(seed: PlxUrlState | null): PlxClientState {
  if (!seed) return { ...DEFAULT_CLIENT_STATE };
  return {
    tab: seed.tab || DEFAULT_CLIENT_STATE.tab,
    zoom: seed.zoom || DEFAULT_CLIENT_STATE.zoom,
    axis: seed.axis || DEFAULT_CLIENT_STATE.axis,
    view: seed.view || DEFAULT_CLIENT_STATE.view,
    inverted: seed.dir === "10",
  };
}

/** Heatmap window is validated by the server; the URL never carries it. */
export function heatmapWindowFromState(state: PoolsExplorerViewState): "90d" | "1y" | "all" {
  return isHeatmapWindow(state.heatmap_window) ? state.heatmap_window : "1y";
}
