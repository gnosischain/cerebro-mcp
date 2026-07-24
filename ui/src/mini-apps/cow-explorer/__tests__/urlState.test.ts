// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { readUrl, writeUrl } from "../urlState";

describe("CoW standalone URL state", () => {
  beforeEach(() => window.history.replaceState({}, "", "/app/cow_explorer?token=secret&unmanaged=1"));

  it("round-trips managed entity state and preserves unmanaged auth/query parameters", () => {
    const state = structuredClone(MOCK_PAYLOAD.view_state!);
    state.environment_scope = "testnet";
    state.chain_id = 11155111;
    state.section = "entity";
    state.interval = "1d";
    state.selected_entity = { entity_type: "transaction", identifier: `0x${"ab".repeat(32)}`, chain_id: 11155111, chain_name: "Ethereum Sepolia" };
    state.date_range = { kind: "absolute", anchor: "explicit", window_days: null, start_at: "2026-07-01T00:00:00Z", end_at: "2026-07-02T00:00:00Z" };
    writeUrl(state);
    const parsed = readUrl();
    expect(parsed).toMatchObject({ scope: "testnet", chain: 11155111, interval: "1d", entity: "transaction", id: state.selected_entity.identifier });
    expect(parsed.start).toBe("2026-07-01T00:00:00Z");
    const params = new URLSearchParams(window.location.search);
    expect(params.get("token")).toBe("secret");
    expect(params.get("unmanaged")).toBe("1");
  });

  it("omits production overview defaults", () => {
    writeUrl(structuredClone(MOCK_PAYLOAD.view_state!));
    const params = new URLSearchParams(window.location.search);
    expect(params.has("scope")).toBe(false);
    expect(params.has("section")).toBe(false);
    expect(params.has("interval")).toBe(false);
    expect(params.has("facet")).toBe(false);
  });

  it("round-trips the facet param alongside its host section", () => {
    const state = structuredClone(MOCK_PAYLOAD.view_state!);
    state.section = "orders";
    writeUrl(state, "order_types");
    const params = new URLSearchParams(window.location.search);
    expect(params.get("facet")).toBe("order_types");
    expect(params.get("section")).toBe("orders");
    const parsed = readUrl();
    expect(parsed.facet).toBe("order_types");
    expect(parsed.section).toBe("orders");
  });

  it("a valid facet implies its host section on read", () => {
    // No ?section= at all: the facet alone must land on its host section.
    window.history.replaceState({}, "", "/app/cow_explorer?facet=trader_dynamics");
    expect(readUrl()).toMatchObject({ facet: "trader_dynamics", section: "traders" });
    // Mismatched ?section= loses to the facet's host.
    window.history.replaceState({}, "", "/app/cow_explorer?facet=solver_directory&section=trades");
    expect(readUrl()).toMatchObject({ facet: "solver_directory", section: "solvers" });
  });

  it("drops invalid facets and never writes a facet whose host is inactive", () => {
    window.history.replaceState({}, "", "/app/cow_explorer?facet=bogus&section=trades");
    expect(readUrl()).toMatchObject({ facet: "", section: "trades" });
    // Facet host (orders) does not match the applied section -> omitted.
    const state = structuredClone(MOCK_PAYLOAD.view_state!);
    state.section = "markets";
    writeUrl(state, "order_types");
    expect(new URLSearchParams(window.location.search).has("facet")).toBe(false);
  });
});
