// Pure reducer tests: mode/selection semantics, depth clamping against the
// SERVER-published limits, profile toggles, and the ADOPT_SERVER mapping of
// view_state v2. Run with `npm test` (vitest).

import { describe, expect, it } from "vitest";
import {
  buildInitialState,
  createProfileSelection,
  createScopedControls,
  graphReducer,
  normalizedStringSetsEqual,
  normalizeStringSet,
  reduceScopedControls,
  scopedControlsAreStale,
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
  // Flows defaults ride in view_state["limits"] in production; include them so
  // the adopted limits (PLACEHOLDER_LIMITS merged) match exactly.
  flows_default_hops: 2,
  flows_max_hops: 4,
  flows_default_min_usd: 10,
  flows_default_range_days: 30,
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

  // Relationships is one section, so there is no longer an "atlas mode" whose
  // window competes with investigate's. Atlas's window survives as a FALLBACK:
  // a payload carrying only a catalog window still seeds the control.
  it("falls back to the atlas window when investigate declares none", () => {
    // window_days deliberately absent — that is the case under test.
    const s = adopted({
      investigate: {
        seed: { id: "0xabc", kind: "safe" },
        active_profiles: [],
        max_neighbors: 250,
        hops_used: 3,
      },
    } as unknown as Partial<GraphExplorerViewState>);
    expect(s.windowDays).toBe(30);
  });

  it("prefers the investigate window when both are present", () => {
    expect(adopted().windowDays).toBe(60);
  });

  // "atlas" is still a valid wire value, but it is not a reachable UI mode.
  it("resolves an atlas payload to investigate", () => {
    expect(adopted({ mode: "atlas" }).mode).toBe("investigate");
  });

  it("falls back to server defaults on an empty payload", () => {
    const s = buildInitialState(undefined);
    expect(s.mode).toBe("investigate");
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
    const next = graphReducer(s, { type: "SET_MODE", mode: "flows" });
    expect(next.mode).toBe("flows");
    expect(next.selection).toEqual({ nodeId: "", edgeId: "" });
  });

  // A legacy deep link, a restored draft or a stale server command can still
  // name "atlas"; it must resolve to Relationships, not to a dead mode with no
  // view mounted.
  it("normalizes an atlas command to investigate", () => {
    const s = adopted();
    const next = graphReducer(s, { type: "SET_MODE", mode: "atlas" });
    expect(next.mode).toBe("investigate");
    // Already in investigate, so this is a no-op rather than a mode switch —
    // the selection must survive.
    expect(next).toBe(s);
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

describe("profile selection authority", () => {
  it("distinguishes not-yet-adopted from an authoritative empty list", () => {
    expect(createProfileSelection(undefined)).toEqual({
      phase: "unresolved",
      draft: [],
      applied: [],
      scopeId: null,
    });
    expect(createProfileSelection([], "scope-empty")).toEqual({
      phase: "applied",
      draft: [],
      applied: [],
      scopeId: "scope-empty",
    });
  });
});

describe("scoped draft/pending/applied controls", () => {
  const normalize = (value: { profiles: string[]; range: number }) => ({
    profiles: normalizeStringSet(value.profiles),
    range: Math.max(1, Math.floor(value.range)),
  });

  it("labels only the matching pending snapshot as applied", () => {
    let controls = createScopedControls(
      { profiles: ["b", "a"], range: 90 },
      { profiles: ["a"], range: 30 },
    );
    controls = reduceScopedControls(
      controls,
      { type: "EDIT_DRAFT", value: { profiles: ["b", "a", "a"], range: 90 } },
      normalize,
    );
    controls = reduceScopedControls(
      controls,
      { type: "LOAD_STARTED", requestId: 7 },
      normalize,
    );
    expect(controls.pending).toEqual({
      requestId: 7,
      snapshot: { profiles: ["a", "b"], range: 90 },
    });
    expect(scopedControlsAreStale(controls)).toBe(true);

    const staleAcceptance = reduceScopedControls(controls, {
      type: "SCOPE_ACCEPTED",
      requestId: 6,
      scopeId: "old",
    });
    expect(staleAcceptance).toBe(controls);

    controls = reduceScopedControls(
      controls,
      { type: "SCOPE_ACCEPTED", requestId: 7, scopeId: "scope-7" },
      normalize,
    );
    expect(controls.applied).toEqual({ profiles: ["a", "b"], range: 90 });
    expect(controls.draft).toEqual(controls.applied);
    expect(controls.scopeId).toBe("scope-7");
    expect(controls.pending).toBeNull();
  });

  it("rolls a failed current draft back but ignores stale failures", () => {
    let controls = createScopedControls(
      { profiles: ["a"], range: 30 },
      { profiles: ["a"], range: 30 },
    );
    controls = reduceScopedControls(controls, {
      type: "EDIT_DRAFT",
      value: { profiles: ["b"], range: 365 },
    });
    controls = reduceScopedControls(controls, { type: "LOAD_STARTED", requestId: 9 });
    expect(
      reduceScopedControls(controls, {
        type: "LOAD_FAILED",
        requestId: 8,
        error: "old failure",
      }),
    ).toBe(controls);

    controls = reduceScopedControls(controls, {
      type: "LOAD_FAILED",
      requestId: 9,
      error: "timeout",
    });
    expect(controls.draft).toEqual({ profiles: ["a"], range: 30 });
    expect(controls.applied).toEqual({ profiles: ["a"], range: 30 });
    expect(controls.pending).toBeNull();
    expect(controls.error).toBe("timeout");
  });

  it("compares normalized sets without JSON serialization order", () => {
    expect(normalizedStringSetsEqual(["b", "a", "a"], ["a", "b"])).toBe(true);
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

  it("generic persistence projects only safe visual preferences", () => {
    const proj = JSON.parse(syncedJson(adopted()));
    expect(Object.keys(proj).sort()).toEqual([
      "layout",
      "semantic_status_filter",
    ]);
    expect(proj).toEqual({
      layout: "circular",
      semantic_status_filter: "approved",
    });
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

describe("flows namespace", () => {
  const FLOW_LIMITS: Limits = {
    ...LIMITS,
    flows_default_hops: 2,
    flows_max_hops: 4,
    flows_default_min_usd: 10,
    flows_default_range_days: 30,
  };
  function flowServer(over: Partial<GraphExplorerViewState> = {}) {
    return serverState({
      limits: FLOW_LIMITS,
      mode: "flows",
      flows: {
        seeds: ["0xseed"],
        direction: "both",
        hops: 3,
        range_days: 90,
        t0: "2026-06-01 00:00:00",
        t1: "2026-07-01 00:00:00",
        min_usd: 100,
        tokens: ["0xtok"],
        include_bridges: false,
        node_count: 5,
        edge_count: 4,
        truncated: false,
        truncated_hops: [],
        expanded: {},
        token_catalog: [],
      },
      ...over,
    });
  }

  it("adopts the flows namespace and recognizes the flows mode", () => {
    const s = buildInitialState(flowServer());
    expect(s.mode).toBe("flows");
    expect(s.flowsDirection).toBe("both");
    expect(s.flowsHops).toBe(3);
    expect(s.flowsRangeDays).toBe(90);
    expect(s.flowsMinUsd).toBe(100);
    expect(s.flowsTokens).toEqual(["0xtok"]);
    expect(s.flowsIncludeBridges).toBe(false);
  });

  it("clamps flows hops into 1..flows_max_hops", () => {
    const s = buildInitialState(flowServer());
    expect(graphReducer(s, { type: "SET_FLOWS_HOPS", hops: 99 }).flowsHops).toBe(4);
    expect(graphReducer(s, { type: "SET_FLOWS_HOPS", hops: 0 }).flowsHops).toBe(1);
  });

  it("min-usd floors at 0; direction/tokens/bridges are set verbatim", () => {
    let s = buildInitialState(flowServer());
    s = graphReducer(s, { type: "SET_FLOWS_MIN_USD", minUsd: -50 });
    expect(s.flowsMinUsd).toBe(0);
    s = graphReducer(s, { type: "SET_FLOWS_DIRECTION", direction: "in" });
    expect(s.flowsDirection).toBe("in");
    s = graphReducer(s, { type: "SET_FLOWS_TOKENS", tokens: ["0xa", "0xb"] });
    expect(s.flowsTokens).toEqual(["0xa", "0xb"]);
    s = graphReducer(s, { type: "SET_FLOWS_BRIDGES", on: true });
    expect(s.flowsIncludeBridges).toBe(true);
  });

  it("re-adopting the same flows server state is projection-stable", () => {
    const first = buildInitialState(flowServer());
    const second = graphReducer(first, {
      type: "ADOPT_SERVER",
      server: flowServer(),
    });
    expect(syncedJson(second)).toBe(syncedJson(first));
  });
});

describe("mode authority (mode_revision gate)", () => {
  it("initial build adopts the server mode + revision", () => {
    const s = adopted({ mode: "timeline", mode_revision: 4 });
    expect(s.mode).toBe("timeline");
    expect(s.modeRevision).toBe(4);
  });

  it("a data-load adoption (revision unchanged) PRESERVES local mode + selection but still adopts the namespace", () => {
    // User is in timeline at revision 4.
    const start = adopted({ mode: "timeline", mode_revision: 4 });
    // A slow flows trace lands: server now says mode=flows BUT mode_revision is
    // unchanged (loaders no longer bump it), and it carries fresh flows data.
    const straggler = serverState({
      mode: "flows",
      mode_revision: 4,
      selection: { node_id: "0xLATE", edge_id: "" },
      flows: {
        seeds: ["0xseed"], direction: "in", hops: 2, range_days: 30,
        t0: "", t1: "", min_usd: 10, tokens: [], include_bridges: true,
        node_count: 0, edge_count: 0, truncated: false, truncated_hops: [],
        expanded: {}, token_catalog: [],
      },
    });
    const next = graphReducer(start, { type: "ADOPT_SERVER", server: straggler });
    // Mode + selection held (the exact "Timeline jumps to Flows" bug)…
    expect(next.mode).toBe("timeline");
    expect(next.selection).toEqual(start.selection);
    // …but the flows NAMESPACE was still adopted (only mode/selection gated).
    expect(next.flowsDirection).toBe("in");
  });

  it("an advanced mode_revision (explicit mode command) adopts the new mode + selection", () => {
    const start = adopted({ mode: "timeline", mode_revision: 4 });
    const deliberate = serverState({
      mode: "flows",
      mode_revision: 5, // bumped by update_graph_explorer_focus
      selection: { node_id: "", edge_id: "" },
    });
    const next = graphReducer(start, { type: "ADOPT_SERVER", server: deliberate });
    expect(next.mode).toBe("flows");
    expect(next.modeRevision).toBe(5);
  });

  it("SET_MODE flips locally without touching modeRevision", () => {
    const start = adopted({ mode: "atlas", mode_revision: 2 });
    const next = graphReducer(start, { type: "SET_MODE", mode: "flows" });
    expect(next.mode).toBe("flows");
    expect(next.modeRevision).toBe(2);
    expect(next.selection).toEqual({ nodeId: "", edgeId: "" });
  });
});
