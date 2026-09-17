// Tool-argument builders for the Pool Liquidity Explorer. FROZEN wire shapes:
//
//   load_pools_explorer_section(view_id, request_id, section, query, pool_class,
//       pool_family, fee_band, fee, token, live_only, probed_only, sort_by,
//       as_of, window, force_refresh)
//   load_pools_explorer_datasets(view_id, request_id, section, group, scope_id,
//       as_of, heatmap_window, force_refresh)
//   search_pools_explorer(view_id, request_id, query)
//   load_pools_explorer_entity(view_id, request_id, entity_type, identifier,
//       as_of, window, force_refresh)
//
// Directory filters are emitted ONLY for the `pools` section (the backend
// rejects filters on the wrong section by design); `query` and `sort_by`
// travel with `pools` and `tokens`. `fee` is forced to 0 whenever `fee_band`
// is set — the two are mutually exclusive on the wire. `force_refresh` is
// emitted only when true. `request_id` is a placeholder the serialized loader
// overwrites; group streaming sends 0 (the CoW convention).

import type { PlxEntityType, PlxListSection, PlxSection, PoolsExplorerViewState } from "../types";

export const TOOL_SECTION = "load_pools_explorer_section";
export const TOOL_DATASETS = "load_pools_explorer_datasets";
export const TOOL_SEARCH = "search_pools_explorer";
export const TOOL_ENTITY = "load_pools_explorer_entity";

export interface PlxFilterDraft {
  query: string;
  pool_class: string;
  pool_family: string;
  fee_band: string;
  fee: number;
  token: string;
  live_only: boolean;
  probed_only: boolean;
  sort_by: string;
}

export const EMPTY_DRAFT: PlxFilterDraft = {
  query: "",
  pool_class: "",
  pool_family: "",
  fee_band: "",
  fee: 0,
  token: "",
  live_only: false,
  probed_only: false,
  sort_by: "",
};

/** Whitelisted directory sorts (`<field>_<dir>`); "" = server default
 * (liquidity DESC, pool_address). Mirror of the backend DIRECTORY_SORTS. */
export const POOL_SORTS: ReadonlyArray<{ id: string; label: string }> = [
  { id: "", label: "Liquidity (default)" },
  { id: "liquidity_asc", label: "Liquidity ↑" },
  { id: "tick_count_desc", label: "Tick count ↓" },
  { id: "days_published_desc", label: "Days published ↓" },
  { id: "first_published_asc", label: "First published ↑" },
  { id: "first_published_desc", label: "First published ↓" },
  { id: "fee_asc", label: "Fee ↑" },
  { id: "fee_desc", label: "Fee ↓" },
  { id: "pool_name_asc", label: "Name A→Z" },
];

/** Mirror of the backend TOKEN_SORTS. */
export const TOKEN_SORTS: ReadonlyArray<{ id: string; label: string }> = [
  { id: "", label: "Pools (default)" },
  { id: "live_pools_desc", label: "Live pools ↓" },
  { id: "probed_pools_desc", label: "Probed pools ↓" },
  { id: "symbol_asc", label: "Symbol A→Z" },
];

export function draftFromState(state: PoolsExplorerViewState): PlxFilterDraft {
  const filters = state.filters ?? EMPTY_DRAFT;
  return {
    query: filters.query ?? "",
    pool_class: filters.pool_class ?? "",
    pool_family: filters.pool_family ?? "",
    fee_band: filters.fee_band ?? "",
    fee: Number(filters.fee ?? 0) || 0,
    token: filters.token ?? "",
    live_only: Boolean(filters.live_only),
    probed_only: Boolean(filters.probed_only),
    sort_by: filters.sort_by ?? "",
  };
}

export interface SectionArgOptions {
  asOf?: string;
  window?: string;
  forceRefresh?: boolean;
}

export function buildSectionToolArgs(
  viewId: string,
  section: PlxListSection,
  draft: PlxFilterDraft,
  opts: SectionArgOptions = {},
): Record<string, unknown> {
  const args: Record<string, unknown> = {
    __tool: TOOL_SECTION,
    view_id: viewId,
    request_id: 0,
    section,
  };
  if (section === "pools" || section === "tokens") {
    args.query = draft.query.trim();
    args.sort_by = draft.sort_by;
  }
  if (section === "pools") {
    args.pool_class = draft.pool_class;
    args.pool_family = draft.pool_family;
    args.fee_band = draft.fee_band;
    // Mutually exclusive on the wire: a band wins over an exact fee.
    args.fee = draft.fee_band ? 0 : Math.max(0, Math.floor(Number(draft.fee) || 0));
    args.token = draft.token.trim().toLowerCase();
    args.live_only = Boolean(draft.live_only);
    args.probed_only = Boolean(draft.probed_only);
  }
  args.as_of = opts.asOf ?? "";
  args.window = opts.window ?? "";
  if (opts.forceRefresh) args.force_refresh = true;
  return args;
}

export function buildEntityArgs(
  viewId: string,
  entityType: PlxEntityType,
  identifier: string,
  opts: SectionArgOptions = {},
): Record<string, unknown> {
  const args: Record<string, unknown> = {
    __tool: TOOL_ENTITY,
    view_id: viewId,
    request_id: 0,
    entity_type: entityType,
    identifier: identifier.trim().toLowerCase(),
    as_of: opts.asOf ?? "",
    window: opts.window ?? "",
  };
  if (opts.forceRefresh) args.force_refresh = true;
  return args;
}

export function buildSearchArgs(viewId: string, query: string): Record<string, unknown> {
  return { __tool: TOOL_SEARCH, view_id: viewId, request_id: 0, query: query.trim() };
}

export interface GroupArgOptions {
  /** Profile group only: re-run the profile at this date ("" = latest). */
  asOf?: string;
  /** Heatmap group only. */
  heatmapWindow?: string;
  /** Retry-after-failure: bypass the server's negative failure cache. */
  forceRefresh?: boolean;
}

/** Additive group load routed through the serialized loader (as opposed to
 * the background group streamer, which builds its own args with request_id
 * 0 and no extras). */
export function buildGroupArgs(
  viewId: string,
  section: PlxSection,
  group: string,
  scopeId: string,
  opts: GroupArgOptions = {},
): Record<string, unknown> {
  const args: Record<string, unknown> = {
    __tool: TOOL_DATASETS,
    view_id: viewId,
    request_id: 0,
    section,
    group,
    scope_id: scopeId,
  };
  if (opts.asOf !== undefined) args.as_of = opts.asOf;
  if (opts.heatmapWindow !== undefined) args.heatmap_window = opts.heatmapWindow;
  if (opts.forceRefresh) args.force_refresh = true;
  return args;
}
