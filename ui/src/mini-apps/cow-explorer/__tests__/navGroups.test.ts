import { describe, expect, it } from "vitest";

import {
  FACET_IDS,
  FACET_VIEWS,
  NAV_GROUPS,
  SECTION_TO_GROUP,
  isCowFacet,
  resolveDestination,
} from "../model/navGroups";
import type { CowFacet, CowSection } from "../types";

/** Every non-entity server section (the server's flat 9-section model). */
const NON_ENTITY_SECTIONS: Array<Exclude<CowSection, "entity">> = [
  "live", "overview", "markets", "trades", "orders", "auctions", "solvers",
  "traders", "patterns",
];

describe("CoW grouped navigation model", () => {
  it("places every non-entity CowSection in exactly one nav group", () => {
    for (const section of NON_ENTITY_SECTIONS) {
      const owners = NAV_GROUPS.filter(
        (group) => group.destinations.some((entry) => entry.id === section),
      );
      expect(owners.length, `section ${section}`).toBe(1);
      expect(SECTION_TO_GROUP[section]).toBe(owners[0].id);
    }
  });

  it("places every facet in exactly one nav group", () => {
    for (const facet of FACET_IDS) {
      const owners = NAV_GROUPS.filter(
        (group) => group.destinations.some((entry) => entry.id === facet),
      );
      expect(owners.length, `facet ${facet}`).toBe(1);
      expect(SECTION_TO_GROUP[facet]).toBe(owners[0].id);
    }
  });

  it("contains no destinations beyond sections and facets, and no duplicates", () => {
    const all = NAV_GROUPS.flatMap((group) => group.destinations.map((entry) => entry.id));
    expect(new Set(all).size).toBe(all.length);
    for (const dest of all) {
      const known = (NON_ENTITY_SECTIONS as string[]).includes(dest) || isCowFacet(dest);
      expect(known, `destination ${dest}`).toBe(true);
    }
    // "entity" is a detail view, never a nav destination.
    expect(all).not.toContain("entity");
  });

  it("resolves every facet to a real host section with non-empty groups", () => {
    for (const facet of FACET_IDS) {
      const view = FACET_VIEWS[facet];
      expect(NON_ENTITY_SECTIONS, `facet ${facet} host`).toContain(view.section);
      expect(view.groups.length, `facet ${facet} groups`).toBeGreaterThan(0);
      const resolved = resolveDestination(facet);
      expect(resolved.section).toBe(view.section);
      expect(resolved.facet).toBe(facet);
      expect(resolved.groups).toEqual(view.groups);
    }
  });

  it("resolves plain sections to themselves with no facet", () => {
    for (const section of NON_ENTITY_SECTIONS) {
      expect(resolveDestination(section)).toEqual({ section, facet: null, groups: [] });
    }
  });

  it("keeps each facet in the same nav group as its host section", () => {
    for (const facet of FACET_IDS) {
      expect(SECTION_TO_GROUP[facet], `facet ${facet}`)
        .toBe(SECTION_TO_GROUP[FACET_VIEWS[facet].section]);
    }
  });

  it("isCowFacet accepts only the facet ids", () => {
    for (const facet of FACET_IDS) expect(isCowFacet(facet)).toBe(true);
    for (const section of NON_ENTITY_SECTIONS) expect(isCowFacet(section)).toBe(false);
    expect(isCowFacet("")).toBe(false);
    expect(isCowFacet("bogus")).toBe(false);
  });

  it("pins the approved facet -> (section, groups) mapping", () => {
    expect(FACET_VIEWS).toEqual({
      order_types: { section: "orders", groups: ["types", "programmatic", "class_quality"] },
      solver_directory: { section: "solvers", groups: ["directory"] },
      trader_dynamics: { section: "traders", groups: ["dynamics", "retention"] },
    } satisfies Record<CowFacet, unknown>);
  });
});
