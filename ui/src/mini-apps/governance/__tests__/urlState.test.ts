// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { readUrl, writeUrl } from "../urlState";

function baseState() {
  return structuredClone(MOCK_PAYLOAD.view_state!);
}

describe("Governance standalone URL state", () => {
  beforeEach(() => window.history.replaceState({}, "", "/app/governance?token=secret&unmanaged=1"));

  it("round-trips managed section/filter/range state and preserves ?token= and unmanaged params", () => {
    const state = baseState();
    state.section = "proposals";
    state.filters = {
      query: "treasury", proposal_state: "closed", proposal_type: "basic",
      quorum_status: "met", category_id: 21, forum_status: "open", sort_by: "most_votes",
    };
    state.date_range = { kind: "absolute", anchor: "explicit", window_days: null, start_at: "2026-05-01T00:00:00Z", end_at: "2026-06-01T00:00:00Z" };
    writeUrl(state);
    const parsed = readUrl();
    expect(parsed).toMatchObject({
      section: "proposals", q: "treasury", pstate: "closed", ptype: "basic",
      quorum: "met", cat: 21, fstatus: "open", sort: "most_votes",
      start: "2026-05-01T00:00:00Z", end: "2026-06-01T00:00:00Z",
    });
    expect(parsed.days).toBeNull();
    const params = new URLSearchParams(window.location.search);
    expect(params.get("token")).toBe("secret");
    expect(params.get("unmanaged")).toBe("1");
  });

  it("round-trips the selected entity", () => {
    const state = baseState();
    state.section = "entity";
    state.selected_entity = { entity_type: "forum_topic", identifier: "12131", label: "GIP-149" };
    writeUrl(state);
    const parsed = readUrl();
    expect(parsed.entity).toBe("forum_topic");
    expect(parsed.id).toBe("12131");
    // entity pseudo-section is never emitted as a section param
    expect(new URLSearchParams(window.location.search).has("section")).toBe(false);
  });

  it("omits defaults: overview section and the all-history date range", () => {
    writeUrl(baseState());
    const params = new URLSearchParams(window.location.search);
    expect(params.has("section")).toBe(false);
    expect(params.has("days")).toBe(false);
    expect(params.has("start")).toBe(false);
    expect(params.has("end")).toBe(false);
    // unmanaged params still intact
    expect(params.get("token")).toBe("secret");
  });

  it("encodes relative presets as days=90/365", () => {
    const state = baseState();
    state.date_range = { kind: "relative", anchor: "now", window_days: 90, start_at: "", end_at: "" };
    writeUrl(state);
    expect(readUrl().days).toBe(90);
    state.date_range.window_days = 365;
    writeUrl(state);
    expect(readUrl().days).toBe(365);
  });

  it("never emits a governance param named token", () => {
    const state = baseState();
    state.filters.query = "anything";
    state.section = "forum";
    state.selected_entity = { entity_type: "voter", identifier: `0x${"aa".repeat(20)}`, label: "" };
    writeUrl(state);
    const params = new URLSearchParams(window.location.search);
    // the only `token` key is the pre-existing unmanaged auth param
    expect(params.get("token")).toBe("secret");
    expect([...params.keys()].filter((key) => key === "token")).toHaveLength(1);
  });

  it("delete-then-set clears stale managed keys", () => {
    window.history.replaceState({}, "", "/app/governance?token=secret&section=forum&days=90&q=old");
    writeUrl(baseState()); // overview + all history + no filters
    const params = new URLSearchParams(window.location.search);
    expect(params.has("section")).toBe(false);
    expect(params.has("days")).toBe(false);
    expect(params.has("q")).toBe(false);
    expect(params.get("token")).toBe("secret");
  });
});
