import { describe, expect, it } from "vitest";

import { buildEntityArgs, buildSearchArgs, buildSectionToolArgs, EMPTY_DRAFT } from "../state/toolArgs";

describe("governance section tool args — frozen date-token encoding", () => {
  it("all-history default emits empty start_at AND empty end_at", () => {
    const args = buildSectionToolArgs("v1", 3, "overview", { ...EMPTY_DRAFT, days: 0 });
    expect(args.start_at).toBe("");
    expect(args.end_at).toBe("");
  });

  it("90-day preset encodes as the '90d' token with empty end_at", () => {
    const args = buildSectionToolArgs("v1", 1, "proposals", { ...EMPTY_DRAFT, days: 90 });
    expect(args.start_at).toBe("90d");
    expect(args.end_at).toBe("");
  });

  it("1-year preset encodes as the '1y' token with empty end_at", () => {
    const args = buildSectionToolArgs("v1", 1, "voters", { ...EMPTY_DRAFT, days: 365 });
    expect(args.start_at).toBe("1y");
    expect(args.end_at).toBe("");
  });

  it("custom range emits the ISO pair verbatim", () => {
    const args = buildSectionToolArgs("v1", 1, "forum", {
      ...EMPTY_DRAFT, days: null, start: "2026-05-01T00:00:00Z", end: "2026-06-01T00:00:00Z",
    });
    expect(args.start_at).toBe("2026-05-01T00:00:00Z");
    expect(args.end_at).toBe("2026-06-01T00:00:00Z");
  });

  it("never emits a window_days key", () => {
    for (const draft of [
      { ...EMPTY_DRAFT, days: 0 },
      { ...EMPTY_DRAFT, days: 90 },
      { ...EMPTY_DRAFT, days: null, start: "2026-05-01T00:00:00Z", end: "2026-06-01T00:00:00Z" },
    ]) {
      expect(Object.keys(buildSectionToolArgs("v1", 1, "overview", draft))).not.toContain("window_days");
    }
  });

  it("carries every filter plus view/request identity", () => {
    const args = buildSectionToolArgs("view-9", 7, "proposals", {
      ...EMPTY_DRAFT,
      query: "treasury", proposal_state: "closed", proposal_type: "basic",
      quorum_status: "met", category_id: 21, forum_status: "open", sort_by: "most_votes",
    });
    expect(args).toMatchObject({
      view_id: "view-9", request_id: 7, section: "proposals",
      query: "treasury", proposal_state: "closed", proposal_type: "basic",
      quorum_status: "met", category_id: 21, forum_status: "open", sort_by: "most_votes",
    });
  });

  it("emits force_refresh ONLY when explicitly passed", () => {
    const routine = buildSectionToolArgs("v1", 1, "overview", EMPTY_DRAFT);
    expect(Object.keys(routine)).not.toContain("force_refresh");
    const forced = buildSectionToolArgs("v1", 1, "overview", EMPTY_DRAFT, true);
    expect(forced.force_refresh).toBe(true);
    const explicitFalse = buildSectionToolArgs("v1", 1, "overview", EMPTY_DRAFT, false);
    expect(explicitFalse.force_refresh).toBe(false);
  });
});

describe("governance entity and search args", () => {
  it("builds entity args untouched", () => {
    expect(buildEntityArgs("v1", 4, "forum_topic", "12131")).toEqual({
      view_id: "v1", request_id: 4, entity_type: "forum_topic", identifier: "12131",
    });
  });

  it("builds search args", () => {
    expect(buildSearchArgs("v1", 5, "gip-149")).toEqual({
      view_id: "v1", request_id: 5, query: "gip-149",
    });
  });
});
