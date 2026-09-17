// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import { MOCK_PAYLOAD, P_WETH } from "../devFixture";
import { DEFAULT_CLIENT_STATE, clientFromSeed, readUrl, writeUrl } from "../urlState";

function baseState() {
  return structuredClone(MOCK_PAYLOAD.view_state!);
}

describe("Pools standalone URL state", () => {
  beforeEach(() => window.history.replaceState({}, "", "/app/pools_explorer?token=secret&unmanaged=1"));

  it("round-trips managed section/filter/date state and preserves ?token= and unmanaged params", () => {
    const state = baseState();
    state.section = "pools";
    state.as_of = "2026-09-01";
    state.window = "90d";
    state.filters = {
      query: "weth", pool_class: "uniswap_v3", pool_family: "cl", fee_band: "b3000", fee: 0,
      token: `0x${"ab".repeat(20)}`, live_only: true, probed_only: true, sort_by: "tick_count_desc",
    };
    writeUrl(state);
    const parsed = readUrl();
    expect(parsed).toMatchObject({
      section: "pools", date: "2026-09-01", days: 90, q: "weth", class: "uniswap_v3", family: "cl",
      fee: "b3000", probed: true, live: true, tok: `0x${"ab".repeat(20)}`, sort: "tick_count_desc",
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("token")).toBe("secret");
    expect(params.get("unmanaged")).toBe("1");
  });

  it("never emits a managed param named token — the token filter travels as tok", () => {
    const state = baseState();
    state.section = "pools";
    state.filters.token = `0x${"cd".repeat(20)}`;
    writeUrl(state);
    const params = new URLSearchParams(window.location.search);
    expect(params.get("token")).toBe("secret");
    expect([...params.keys()].filter((key) => key === "token")).toHaveLength(1);
    expect(params.get("tok")).toBe(`0x${"cd".repeat(20)}`);
  });

  it("encodes an exact fee as pips and a band as its id", () => {
    const state = baseState();
    state.section = "pools";
    state.filters.fee = 3000;
    writeUrl(state);
    expect(readUrl().fee).toBe("3000");
    state.filters.fee_band = "b500";
    writeUrl(state);
    expect(readUrl().fee).toBe("b500");
  });

  it("round-trips the selected entity and client-only pool keys, defaults omitted", () => {
    const state = baseState();
    state.section = "pool";
    state.selected_entity = { entity_type: "pool", identifier: P_WETH, label: "uniswap_v3 · 0x0cf4…42ae" };
    writeUrl(state, { tab: "ticks", zoom: "all", axis: "pct", view: "over_time", inverted: true });
    const parsed = readUrl();
    expect(parsed).toMatchObject({ entity: "pool", id: P_WETH, tab: "ticks", zoom: "all", axis: "pct", view: "over_time", dir: "10" });
    expect(new URLSearchParams(window.location.search).has("section")).toBe(false);
    expect(clientFromSeed(parsed)).toEqual({ tab: "ticks", zoom: "all", axis: "pct", view: "over_time", inverted: true });
    // Defaults are omitted entirely.
    writeUrl(state, { ...DEFAULT_CLIENT_STATE });
    const params = new URLSearchParams(window.location.search);
    for (const key of ["tab", "zoom", "axis", "view", "dir"]) expect(params.has(key)).toBe(false);
    expect(clientFromSeed(readUrl())).toEqual(DEFAULT_CLIENT_STATE);
  });

  it("never emits client keys off the pool section", () => {
    const state = baseState();
    state.section = "tokens";
    writeUrl(state, { tab: "ticks", zoom: "all", axis: "pct", view: "over_time", inverted: true });
    const params = new URLSearchParams(window.location.search);
    for (const key of ["tab", "zoom", "axis", "view", "dir", "entity", "id"]) expect(params.has(key)).toBe(false);
    expect(params.get("section")).toBe("tokens");
  });

  it("omits defaults: overview, the 1y window, no as_of, empty filters", () => {
    writeUrl(baseState());
    const params = new URLSearchParams(window.location.search);
    expect([...params.keys()].sort()).toEqual(["token", "unmanaged"]);
  });

  it("delete-then-set clears stale managed keys", () => {
    window.history.replaceState({}, "", "/app/pools_explorer?token=secret&section=coverage&days=90&q=old&tab=fees");
    writeUrl(baseState());
    const params = new URLSearchParams(window.location.search);
    expect(params.has("section")).toBe(false);
    expect(params.has("days")).toBe(false);
    expect(params.has("q")).toBe(false);
    expect(params.has("tab")).toBe(false);
    expect(params.get("token")).toBe("secret");
  });

  it("reads unknown enum values as absent rather than rendering a broken view", () => {
    window.history.replaceState({}, "", "/app/pools_explorer?section=nope&entity=pair&tab=x&zoom=y&axis=z&view=w&dir=5&days=abc");
    expect(readUrl()).toMatchObject({ section: "", entity: "", tab: "", zoom: "", axis: "", view: "", dir: "", days: null });
  });
});
