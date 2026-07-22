// Breadcrumb-trail + deep-link navigation helpers (pure — no rendering).

import { describe, expect, it } from "vitest";

import {
  BREADCRUMB_CAP,
  crumbCall,
  draftFromSeed,
  seedCall,
  sectionFromSeed,
  sectionReturnCall,
  trailForDisplay,
} from "../state/navigation";
import { EMPTY_DRAFT } from "../state/toolArgs";
import type { GovBreadcrumb } from "../types";
import type { GovUrlState } from "../urlState";

function crumb(entityType: GovBreadcrumb["entity_type"], identifier: string, label = ""): GovBreadcrumb {
  return { entity_type: entityType, identifier, label: label || identifier };
}

function seed(overrides: Partial<GovUrlState> = {}): GovUrlState {
  return {
    section: "", q: "", days: null, start: "", end: "",
    pstate: "", ptype: "", quorum: "", cat: 0, fstatus: "", sort: "",
    entity: "", id: "",
    ...overrides,
  };
}

describe("trailForDisplay — construction / dedupe / truncation", () => {
  it("keeps an already-clean trail in order", () => {
    const trail = [crumb("proposal", "0xaa"), crumb("forum_topic", "12131")];
    expect(trailForDisplay(trail)).toEqual(trail);
  });

  it("dedupes a revisited entity keeping its NEWEST position", () => {
    const trail = [
      crumb("proposal", "0xaa"),
      crumb("forum_topic", "12131"),
      crumb("proposal", "0xaa"),
    ];
    expect(trailForDisplay(trail)).toEqual([
      crumb("forum_topic", "12131"),
      crumb("proposal", "0xaa"),
    ]);
  });

  it("same identifier under DIFFERENT entity types is not deduped", () => {
    const trail = [crumb("forum_topic", "42"), crumb("forum_user", "42")];
    expect(trailForDisplay(trail)).toHaveLength(2);
  });

  it("truncates to the newest CAP chips", () => {
    const trail = Array.from({ length: BREADCRUMB_CAP + 3 }, (_, i) => crumb("voter", `0x${i}`));
    const shown = trailForDisplay(trail);
    expect(shown).toHaveLength(BREADCRUMB_CAP);
    expect(shown[0].identifier).toBe("0x3"); // oldest three dropped
    expect(shown[shown.length - 1].identifier).toBe(`0x${BREADCRUMB_CAP + 2}`);
  });
});

describe("chip -> load_governance_entity args", () => {
  it("a breadcrumb chip resolves to the entity tool with its identity", () => {
    const call = crumbCall("v1", crumb("forum_topic", "12131", "GIP-149 discussion"));
    expect(call.__tool).toBe("load_governance_entity");
    expect(call).toMatchObject({
      view_id: "v1",
      entity_type: "forum_topic",
      identifier: "12131",
    });
  });
});

describe("leading chip -> section return", () => {
  it("returns to the drilled-from section carrying the current draft", () => {
    const call = sectionReturnCall("v1", "voters", { ...EMPTY_DRAFT, days: 90, quorum_status: "met" });
    expect(call.__tool).toBe("load_governance_section");
    expect(call).toMatchObject({
      view_id: "v1",
      section: "voters",
      start_at: "90d",
      end_at: "",
      quorum_status: "met",
    });
    expect(call.force_refresh).toBeUndefined();
  });
});

describe("entity+id URL deep-link seeding", () => {
  it("entity + id short-circuits to the entity load", () => {
    const call = seedCall("v1", seed({ entity: "proposal", id: "0xabc", section: "forum" }), "overview", EMPTY_DRAFT);
    expect(call.__tool).toBe("load_governance_entity");
    expect(call).toMatchObject({ entity_type: "proposal", identifier: "0xabc" });
  });

  it("an unknown entity type falls back to the seeded section apply", () => {
    const call = seedCall("v1", seed({ entity: "wallet", id: "0xabc", section: "voters" }), "overview", EMPTY_DRAFT);
    expect(call.__tool).toBe("load_governance_section");
    expect(call.section).toBe("voters");
  });

  it("no seed -> fallback section with the live draft", () => {
    const call = seedCall("v1", null, "proposals", { ...EMPTY_DRAFT, proposal_state: "closed" });
    expect(call.__tool).toBe("load_governance_section");
    expect(call).toMatchObject({ section: "proposals", proposal_state: "closed" });
  });

  it("seeded filters map onto the frozen date-token encoding", () => {
    const call = seedCall(
      "v1",
      seed({ section: "proposals", days: 90, pstate: "closed", quorum: "met", q: "gip" }),
      "overview",
      EMPTY_DRAFT,
    );
    expect(call).toMatchObject({
      section: "proposals",
      start_at: "90d",
      end_at: "",
      proposal_state: "closed",
      quorum_status: "met",
      query: "gip",
    });
    expect(Object.keys(call)).not.toContain("window_days");
  });

  it("custom ISO pair seeds an absolute range; a half pair degrades to all-history", () => {
    const full = draftFromSeed(seed({ start: "2026-05-01T00:00:00Z", end: "2026-06-01T00:00:00Z" }));
    expect(full.days).toBeNull();
    expect(full.start).toBe("2026-05-01T00:00:00Z");
    expect(full.end).toBe("2026-06-01T00:00:00Z");

    const half = draftFromSeed(seed({ start: "2026-05-01T00:00:00Z" }));
    expect(half.days).toBe(0);
    expect(half.start).toBe("");
    expect(half.end).toBe("");
  });

  it("sectionFromSeed rejects unknown/empty/entity sections", () => {
    expect(sectionFromSeed(seed({ section: "forum" }), "overview")).toBe("forum");
    expect(sectionFromSeed(seed(), "overview")).toBe("overview");
    expect(sectionFromSeed(seed({ section: "entity" as GovUrlState["section"] }), "voters")).toBe("voters");
  });
});
