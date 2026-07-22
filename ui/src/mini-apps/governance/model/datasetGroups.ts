// Frontend mirror of the backend SECTION_GROUPS / ENTITY_BUNDLES maps
// (governance_explorer.py). FROZEN CONTRACT — every dataset key lives in
// exactly one group, globally unique across sections; entity bundle keys are
// never in a section group. A unit test enforces this byte-for-byte with the
// devFixture's loaded_groups.

export const SECTION_GROUPS: Record<string, Record<string, readonly string[]>> = {
  overview: {
    core: ["space_summary", "source_freshness", "governance_activity"],
    insights: [
      "proposal_types",
      "quorum_distribution",
      "voter_power_concentration",
      "latest_activity",
      "forum_category_activity",
    ],
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

/** Entity drill-down bundles — loaded under the `"entity"` pseudo-section by
 * `load_governance_entity`, NEVER part of SECTION_GROUPS. */
export const ENTITY_DATASETS: Record<string, readonly string[]> = {
  proposal: ["proposal_detail", "proposal_choices", "proposal_votes", "proposal_forum_links"],
  voter: ["voter_profile", "voter_votes", "voter_participation"],
  forum_topic: ["topic_detail", "topic_posts", "topic_proposal_links"],
  forum_user: ["contributor_profile", "contributor_posts", "contributor_activity"],
};

/** dataset key -> owning `{section, group}` (section datasets only). */
export const DATASET_GROUP: Record<string, { section: string; group: string }> = {};
for (const [section, groups] of Object.entries(SECTION_GROUPS)) {
  for (const [group, keys] of Object.entries(groups)) {
    for (const key of keys) DATASET_GROUP[key] = { section, group };
  }
}
