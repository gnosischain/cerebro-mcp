// Frozen-contract tests: the client SECTION_GROUPS / ENTITY_DATASETS mirrors
// must match the backend byte-for-byte (the backend suite pins its own side).

import { describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { DATASET_GROUP, ENTITY_DATASETS, SECTION_GROUPS } from "../model/datasetGroups";

const FROZEN_SECTION_GROUPS: Record<string, Record<string, string[]>> = {
  overview: {
    core: ["space_summary", "source_freshness", "governance_activity"],
    insights: ["proposal_types", "quorum_distribution", "voter_power_concentration", "latest_activity", "forum_category_activity"],
  },
  proposals: {
    core: ["proposal_summary", "proposals"],
    charts: ["proposal_activity"],
  },
  voters: {
    core: ["voter_summary", "voter_leaderboard"],
    insights: ["voter_concentration", "voter_activity"],
  },
  forum: {
    core: ["forum_summary", "forum_categories", "forum_topics"],
    insights: ["forum_activity", "contributor_leaderboard"],
  },
};

const FROZEN_ENTITY_DATASETS: Record<string, string[]> = {
  proposal: ["proposal_detail", "proposal_choices", "proposal_votes", "proposal_forum_links"],
  voter: ["voter_profile", "voter_votes", "voter_participation"],
  forum_topic: ["topic_detail", "topic_posts", "topic_proposal_links"],
  forum_user: ["contributor_profile", "contributor_posts", "contributor_activity"],
};

describe("frozen SECTION_GROUPS contract", () => {
  it("matches the frozen map exactly", () => {
    expect(SECTION_GROUPS).toEqual(FROZEN_SECTION_GROUPS);
  });

  it("every dataset key is globally unique across all sections and groups", () => {
    const seen = new Map<string, string>();
    for (const [section, groups] of Object.entries(SECTION_GROUPS)) {
      for (const [group, keys] of Object.entries(groups)) {
        for (const key of keys) {
          const owner = `${section}.${group}`;
          expect(seen.get(key), `${key} owned by both ${seen.get(key)} and ${owner}`).toBeUndefined();
          seen.set(key, owner);
        }
      }
    }
  });

  it("every section has a core group", () => {
    for (const groups of Object.values(SECTION_GROUPS)) {
      expect(Object.keys(groups)).toContain("core");
    }
  });

  it("DATASET_GROUP reverse index resolves every key to its owning group", () => {
    expect(DATASET_GROUP.space_summary).toEqual({ section: "overview", group: "core" });
    expect(DATASET_GROUP.forum_category_activity).toEqual({ section: "overview", group: "insights" });
    expect(DATASET_GROUP.contributor_leaderboard).toEqual({ section: "forum", group: "insights" });
    const totalKeys = Object.values(SECTION_GROUPS)
      .flatMap((groups) => Object.values(groups))
      .flat().length;
    expect(Object.keys(DATASET_GROUP)).toHaveLength(totalKeys);
  });
});

describe("frozen ENTITY_DATASETS contract", () => {
  it("matches the frozen bundles exactly", () => {
    expect(ENTITY_DATASETS).toEqual(FROZEN_ENTITY_DATASETS);
  });

  it("entity bundle keys never appear in any section group", () => {
    for (const keys of Object.values(ENTITY_DATASETS)) {
      for (const key of keys) {
        expect(DATASET_GROUP[key], `${key} must not be in SECTION_GROUPS`).toBeUndefined();
      }
    }
  });

  it("entity keys are unique across bundles", () => {
    const all = Object.values(ENTITY_DATASETS).flat();
    expect(new Set(all).size).toBe(all.length);
  });
});

describe("devFixture consistency", () => {
  it("loaded_groups covers every frozen section.group key exactly", () => {
    const expected = Object.entries(SECTION_GROUPS)
      .flatMap(([section, groups]) => Object.keys(groups).map((group) => `${section}.${group}`))
      .sort();
    const actual = Object.keys(MOCK_PAYLOAD.view_state!.loaded_groups!).sort();
    expect(actual).toEqual(expected);
  });

  it("fixture descriptors are shaped like real ones", () => {
    for (const descriptor of Object.values(MOCK_PAYLOAD.datasets!)) {
      expect(descriptor.database).toBe("governance_db");
      expect(descriptor.stats.mode).toBe("exact_capped");
      expect(descriptor.stats.row_cap).toBe(10000);
    }
  });
});
