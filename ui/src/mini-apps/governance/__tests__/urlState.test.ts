// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { DEFAULT_TREASURY_VIEW, type TreasuryViewState } from "../state/treasuryView";
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

describe("treasury view keys (ttab tchain tltd thidden tstack tmeasure trange tassets)", () => {
  beforeEach(() => window.history.replaceState({}, "", "/app/governance?token=secret"));

  const view: TreasuryViewState = {
    tab: "history", chain: 100, exLtd: true, showHidden: true,
    stackBy: "wallet", measure: "gno", range: "1y", assetFilter: "hidden",
  };

  it("round-trips every key on the treasury section and keeps ?token=", () => {
    const state = baseState();
    state.section = "treasury";
    writeUrl(state, view);
    const params = new URLSearchParams(window.location.search);
    expect(params.get("ttab")).toBe("history");
    expect(params.get("tchain")).toBe("100");
    expect(params.get("tltd")).toBe("1");
    expect(params.get("thidden")).toBe("1");
    expect(params.get("tstack")).toBe("wallet");
    expect(params.get("tmeasure")).toBe("gno");
    expect(params.get("trange")).toBe("1y");
    expect(params.get("tassets")).toBe("hidden");
    expect(params.get("token")).toBe("secret");
    const parsed = readUrl();
    expect(parsed.treasury).toEqual(view);
    // Treasury keys are client-only, so a shared link must land on treasury.
    expect(parsed.section).toBe("treasury");
  });

  it("omits every default so an ordinary treasury link stays clean", () => {
    const state = baseState();
    state.section = "treasury";
    writeUrl(state, DEFAULT_TREASURY_VIEW);
    expect(window.location.search).toBe("?token=secret&section=treasury");
    expect(readUrl().treasury).toEqual({});
  });

  it("travels with a treasury entity page too (the wallet page honours the hidden toggle)", () => {
    const state = baseState();
    state.section = "entity";
    state.selected_entity = { entity_type: "treasury_wallet", identifier: "1:0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f", label: "" };
    writeUrl(state, { ...DEFAULT_TREASURY_VIEW, showHidden: true });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("thidden")).toBe("1");
    expect(params.get("entity")).toBe("treasury_wallet");
    expect(params.get("id")).toBe("1:0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f");
  });

  it("never emits treasury keys off the treasury", () => {
    const state = baseState();
    state.section = "proposals";
    writeUrl(state, view);
    const params = new URLSearchParams(window.location.search);
    for (const key of ["ttab", "tchain", "tltd", "thidden", "tstack", "tmeasure", "trange", "tassets"]) {
      expect(params.has(key), key).toBe(false);
    }
  });

  it("clears stale treasury keys when the view returns to defaults", () => {
    window.history.replaceState({}, "", "/app/governance?token=secret&section=treasury&tchain=100&tltd=1");
    const state = baseState();
    state.section = "treasury";
    writeUrl(state, DEFAULT_TREASURY_VIEW);
    const params = new URLSearchParams(window.location.search);
    expect(params.has("tchain")).toBe(false);
    expect(params.has("tltd")).toBe(false);
  });

  it("aliases the old tab ids and ignores junk", () => {
    window.history.replaceState({}, "", "/app/governance?ttab=portfolio");
    expect(readUrl().treasury.tab).toBe("overview");
    window.history.replaceState({}, "", "/app/governance?ttab=tokens");
    expect(readUrl().treasury.tab).toBe("assets");
    window.history.replaceState({}, "", "/app/governance?ttab=nope");
    const parsed = readUrl();
    expect(parsed.treasury).toEqual({});
    expect(parsed.section).toBe("");
  });

  it("uses replaceState only — a filter change is not a navigation", () => {
    const before = window.history.length;
    const state = baseState();
    state.section = "treasury";
    writeUrl(state, view);
    writeUrl(state, DEFAULT_TREASURY_VIEW);
    expect(window.history.length).toBe(before);
  });
});
