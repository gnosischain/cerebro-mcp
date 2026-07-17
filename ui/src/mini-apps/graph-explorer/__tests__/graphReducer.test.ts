// Pure reducer tests: mode/selection semantics, depth clamping against the
// SERVER-published limits, profile toggles, and the ADOPT_SERVER mapping of
// view_state v2. Run with `npm test` (vitest).

import { describe, expect, it } from "vitest";
import {
  buildInitialState,
  graphReducer,
  type GraphLocalState,
} from "../state/graphReducer";
import { syncedJson } from "../state/useGraphSync";
import type { GraphExplorerViewState, Limits } from "../types";

const LIMITS: Limits = {
  max_hops: 7,
  bfs_node_cap: 2000,
  default_expand_depth: 1,
  ui_default_window_days: 90,
  ui_default_max_neighbors: 100,
  atlas_sample_size: 150,
};

function serverState(
  over: Partial<GraphExplorerViewState> = {},
): GraphExplorerViewState {
  return {
    title: "Graph Explorer",
    mode: "investigate",
    catalog: [],
    limits: LIMITS,
    atlas: { selected_profiles: ["lp_in_pool"], sample_size: 200, window_days: 30 },
    investigate: {
      seed: { id: "0xabc", kind: "safe" },
      active_profiles: ["token_transfers", "safe_ownership"],
      window_days: 60,
      max_neighbors: 250,
      hops_used: 3,
    },
    selection: { node_id: "0xsel", edge_id: "" },
    layout: "circular",
    semantic_status_filter: "approved",
    node_roles: {},
    suggested_next_hops: [],
    warnings: [],
    dataset_revisions: { nodes: 2 },
    ...over,
  };
}

function adopted(over: Partial<GraphExplorerViewState> = {}): GraphLocalState {
  return buildInitialState(serverState(over));
}

describe("ADOPT_SERVER mapping", () => {
  it("maps view_state v2 into local state", () => {
    const s = adopted();
    expect(s.mode).toBe("investigate");
    expect(s.selection).toEqual({ nodeId: "0xsel", edgeId: "" });
    expect(s.atlasProfiles).toEqual(["lp_in_pool"]);
    expect(s.investigateProfiles).toEqual(["token_transfers", "safe_ownership"]);
    expect(s.windowDays).toBe(60); // investigate namespace wins in investigate mode
    expect(s.maxNeighbors).toBe(250);
    expect(s.atlasSampleSize).toBe(200);
    expect(s.layout).toBe("circular");
    expect(s.statusFilter).toBe("approved");
    expect(s.limits).toEqual(LIMITS);
    expect(s.expandDepth).toBe(LIMITS.default_expand_depth);
  });

  it("takes the atlas window when mode is atlas", () => {
    const s = adopted({ mode: "atlas" });
    expect(s.windowDays).toBe(30);
  });

  it("falls back to server defaults on an empty payload", () => {
    const s = buildInitialState(undefined);
    expect(s.mode).toBe("atlas");
    expect(s.selection).toEqual({ nodeId: "", edgeId: "" });
    expect(s.atlasProfiles).toEqual([]);
    expect(s.investigateProfiles).toEqual([]);
    expect(s.expandDepth).toBe(1);
  });

  it("preserves the client-only expandDepth across adoption (clamped)", () => {
    let s = adopted();
    s = graphReducer(s, { type: "SET_EXPAND_DEPTH", depth: 5 });
    const next = graphReducer(s, { type: "ADOPT_SERVER", server: serverState() });
    expect(next.expandDepth).toBe(5);
    const tighter = graphReducer(s, {
      type: "ADOPT_SERVER",
      server: serverState({ limits: { ...LIMITS, max_hops: 3 } }),
    });
    expect(tighter.expandDepth).toBe(3);
  });
});

describe("SET_MODE", () => {
  it("clears the selection on mode switch", () => {
    const s = adopted(); // investigate with a selected node
    const next = graphReducer(s, { type: "SET_MODE", mode: "atlas" });
    expect(next.mode).toBe("atlas");
    expect(next.selection).toEqual({ nodeId: "", edgeId: "" });
  });

  it("is a no-op when the mode is unchanged (selection kept)", () => {
    const s = adopted();
    const next = graphReducer(s, { type: "SET_MODE", mode: "investigate" });
    expect(next).toBe(s);
    expect(next.selection.nodeId).toBe("0xsel");
  });
});

describe("selection", () => {
  it("SELECT_NODE clears any edge selection and vice versa", () => {
    let s = adopted();
    s = graphReducer(s, { type: "SELECT_EDGE", id: "e9" });
    expect(s.selection).toEqual({ nodeId: "", edgeId: "e9" });
    s = graphReducer(s, { type: "SELECT_NODE", id: "0xnew" });
    expect(s.selection).toEqual({ nodeId: "0xnew", edgeId: "" });
  });
});

describe("expand-depth clamping vs limits", () => {
  it("clamps into 1..limits.max_hops", () => {
    const s = adopted();
    expect(graphReducer(s, { type: "SET_EXPAND_DEPTH", depth: 99 }).expandDepth).toBe(7);
    expect(graphReducer(s, { type: "SET_EXPAND_DEPTH", depth: 0 }).expandDepth).toBe(1);
    expect(graphReducer(s, { type: "SET_EXPAND_DEPTH", depth: -3 }).expandDepth).toBe(1);
    expect(graphReducer(s, { type: "SET_EXPAND_DEPTH", depth: 4 }).expandDepth).toBe(4);
  });
});

describe("profile toggles", () => {
  it("TOGGLE_ATLAS_PROFILE adds then removes", () => {
    let s = adopted();
    s = graphReducer(s, { type: "TOGGLE_ATLAS_PROFILE", profile: "circles_trust" });
    expect(s.atlasProfiles).toEqual(["lp_in_pool", "circles_trust"]);
    s = graphReducer(s, { type: "TOGGLE_ATLAS_PROFILE", profile: "lp_in_pool" });
    expect(s.atlasProfiles).toEqual(["circles_trust"]);
  });

  it("SET_ATLAS_PROFILES replaces wholesale", () => {
    const s = graphReducer(adopted(), {
      type: "SET_ATLAS_PROFILES",
      profiles: ["a", "b"],
    });
    expect(s.atlasProfiles).toEqual(["a", "b"]);
  });

  it("TOGGLE_INVESTIGATE_PROFILE only touches the investigate list", () => {
    const s = graphReducer(adopted(), {
      type: "TOGGLE_INVESTIGATE_PROFILE",
      profile: "safe_ownership",
    });
    expect(s.investigateProfiles).toEqual(["token_transfers"]);
    expect(s.atlasProfiles).toEqual(["lp_in_pool"]);
  });
});

describe("sync projection stability", () => {
  it("adopting the same server state twice yields an identical projection (no sync loop)", () => {
    const first = buildInitialState(serverState());
    const second = graphReducer(first, {
      type: "ADOPT_SERVER",
      server: serverState(),
    });
    expect(syncedJson(second)).toBe(syncedJson(first));
  });

  it("projection carries exactly the set_graph_explorer_view schema keys", () => {
    const proj = JSON.parse(syncedJson(adopted()));
    expect(Object.keys(proj).sort()).toEqual([
      "atlas",
      "investigate",
      "layout",
      "mode",
      "semantic_status_filter",
    ]);
    expect(Object.keys(proj.atlas).sort()).toEqual([
      "sample_size",
      "selected_profiles",
      "window_days",
    ]);
    expect(Object.keys(proj.investigate).sort()).toEqual([
      "active_profiles",
      "max_neighbors",
      "window_days",
    ]);
  });
});

describe("numeric guards", () => {
  it("window and max-neighbors floor at 1", () => {
    const s = adopted();
    expect(graphReducer(s, { type: "SET_WINDOW", days: 0 }).windowDays).toBe(1);
    expect(graphReducer(s, { type: "SET_MAX_NEIGHBORS", value: -5 }).maxNeighbors).toBe(1);
    expect(graphReducer(s, { type: "SET_WINDOW", days: 365 }).windowDays).toBe(365);
  });
});
