import { describe, expect, it } from "vitest";

import { MOCK_PAYLOAD, P_WETH, WXDAI } from "../devFixture";
import { seedCall } from "../state/navigation";
import {
  EMPTY_DRAFT, buildEntityArgs, buildGroupArgs, buildSearchArgs, buildSectionToolArgs, draftFromState,
  type PlxFilterDraft,
} from "../state/toolArgs";
import type { PlxUrlState } from "../urlState";

const DRAFT: PlxFilterDraft = {
  query: "weth",
  pool_class: "uniswap_v3",
  pool_family: "cl",
  fee_band: "b3000",
  fee: 3000,
  token: WXDAI.toUpperCase(),
  live_only: true,
  probed_only: true,
  sort_by: "tick_count_desc",
};

describe("load_pools_explorer_section wire shape", () => {
  it("emits the full directory filter set for the pools section, with fee cleared under a fee band", () => {
    const args = buildSectionToolArgs("view-1", "pools", DRAFT, { asOf: "2026-09-01", window: "90d" });
    expect(args).toEqual({
      __tool: "load_pools_explorer_section",
      view_id: "view-1",
      request_id: 0,
      section: "pools",
      query: "weth",
      sort_by: "tick_count_desc",
      pool_class: "uniswap_v3",
      pool_family: "cl",
      fee_band: "b3000",
      fee: 0,
      token: WXDAI,
      live_only: true,
      probed_only: true,
      as_of: "2026-09-01",
      window: "90d",
    });
  });

  it("sends an exact fee only when no band is set", () => {
    const args = buildSectionToolArgs("view-1", "pools", { ...DRAFT, fee_band: "" });
    expect(args.fee).toBe(3000);
    expect(args.fee_band).toBe("");
  });

  it("emits ONLY query + sort_by for tokens and neither for overview / coverage", () => {
    const tokens = buildSectionToolArgs("view-1", "tokens", DRAFT);
    expect(Object.keys(tokens).sort()).toEqual(
      ["__tool", "as_of", "query", "request_id", "section", "sort_by", "view_id", "window"].sort(),
    );
    for (const section of ["overview", "coverage"] as const) {
      const args = buildSectionToolArgs("view-1", section, DRAFT);
      expect(Object.keys(args).sort()).toEqual(["__tool", "as_of", "request_id", "section", "view_id", "window"].sort());
      expect(args.as_of).toBe("");
      expect(args.window).toBe("");
    }
  });

  it("emits force_refresh only when true", () => {
    expect(buildSectionToolArgs("v", "overview", EMPTY_DRAFT, { forceRefresh: false })).not.toHaveProperty("force_refresh");
    expect(buildSectionToolArgs("v", "overview", EMPTY_DRAFT, { forceRefresh: true }).force_refresh).toBe(true);
  });

  it("round-trips the server filters into a draft", () => {
    const state = structuredClone(MOCK_PAYLOAD.view_state!);
    state.filters = { ...DRAFT };
    expect(draftFromState(state)).toEqual(DRAFT);
  });
});

describe("entity / search / group wire shapes", () => {
  it("load_pools_explorer_entity lowercases the identifier and carries as_of + window", () => {
    expect(buildEntityArgs("v", "pool", ` ${P_WETH.toUpperCase()} `, { asOf: "2026-09-01", window: "1y" })).toEqual({
      __tool: "load_pools_explorer_entity",
      view_id: "v",
      request_id: 0,
      entity_type: "pool",
      identifier: P_WETH,
      as_of: "2026-09-01",
      window: "1y",
    });
  });

  it("search_pools_explorer trims the query", () => {
    expect(buildSearchArgs("v", "  weth ")).toEqual({ __tool: "search_pools_explorer", view_id: "v", request_id: 0, query: "weth" });
  });

  it("load_pools_explorer_datasets carries as_of for the profile group and heatmap_window (+force) for the heatmap", () => {
    expect(buildGroupArgs("v", "pool", "profile", "scope-1", { asOf: "2026-08-01" })).toEqual({
      __tool: "load_pools_explorer_datasets", view_id: "v", request_id: 0, section: "pool", group: "profile",
      scope_id: "scope-1", as_of: "2026-08-01",
    });
    expect(buildGroupArgs("v", "pool", "heatmap", "scope-1", { heatmapWindow: "90d", forceRefresh: true })).toEqual({
      __tool: "load_pools_explorer_datasets", view_id: "v", request_id: 0, section: "pool", group: "heatmap",
      scope_id: "scope-1", heatmap_window: "90d", force_refresh: true,
    });
    expect(buildGroupArgs("v", "overview", "trend", "scope-1")).not.toHaveProperty("as_of");
  });
});

describe("seedCall (first load of the deferred driver)", () => {
  const seed = (patch: Partial<PlxUrlState>): PlxUrlState => ({
    section: "", entity: "", id: "", date: "", days: null, q: "", class: "", family: "", fee: "",
    probed: false, live: false, tok: "", sort: "", dir: "", tab: "", zoom: "", axis: "", view: "",
    ...patch,
  });

  it("a URL entity deep link short-circuits to the entity load", () => {
    const state = structuredClone(MOCK_PAYLOAD.view_state!);
    const call = seedCall("v", state, seed({ entity: "token", id: WXDAI, date: "2026-08-01", days: 90 }));
    expect(call).toMatchObject({ __tool: "load_pools_explorer_entity", entity_type: "token", identifier: WXDAI, as_of: "2026-08-01", window: "90d" });
  });

  it("an opener that selected an entity loads that entity", () => {
    const state = structuredClone(MOCK_PAYLOAD.view_state!);
    state.section = "pool";
    state.selected_entity = { entity_type: "pool", identifier: P_WETH, label: "x" };
    expect(seedCall("v", state, null)).toMatchObject({ __tool: "load_pools_explorer_entity", entity_type: "pool", identifier: P_WETH });
  });

  it("a seeded section applies with the seed-derived draft (band vs exact fee)", () => {
    const state = structuredClone(MOCK_PAYLOAD.view_state!);
    const byBand = seedCall("v", state, seed({ section: "pools", fee: "b500", probed: true, tok: WXDAI, days: 0 }));
    expect(byBand).toMatchObject({ section: "pools", fee_band: "b500", fee: 0, probed_only: true, token: WXDAI, window: "all" });
    const byPips = seedCall("v", state, seed({ section: "pools", fee: "3000" }));
    expect(byPips).toMatchObject({ section: "pools", fee_band: "", fee: 3000 });
    // No seed: the opened section with the server's own filters.
    expect(seedCall("v", state, null)).toMatchObject({ __tool: "load_pools_explorer_section", section: "overview", window: "1y" });
  });
});
