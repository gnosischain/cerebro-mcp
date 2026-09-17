// Frontend mirror of the backend SECTION_GROUPS map (pools_explorer.py).
// FROZEN CONTRACT — every dataset key lives in exactly one group, globally
// unique across sections. Unlike Governance there is NO entity pseudo-section:
// `pool` and `token` are real sections whose groups stream through the same
// `load_pools_explorer_datasets` tool once `load_pools_explorer_entity` has
// loaded their core. A unit test pins this byte-for-byte against the
// devFixture's loaded_groups; the backend suite pins its own side.

export const SECTION_GROUPS: Record<string, Record<string, readonly string[]>> = {
  overview: {
    core: ["pools_summary", "source_freshness"],
    mix: ["pools_by_class_fee", "probe_coverage_split"],
    // Sole member: the one whole-history all-pool scan.
    trend: ["live_pool_trend"],
    concentration: ["concentration_summary", "range_width_distribution"],
  },
  pools: {
    core: ["pool_directory"],
  },
  tokens: {
    core: ["token_directory"],
  },
  coverage: {
    core: ["coverage_summary", "publication_calendar"],
    gaps: ["missing_days", "metadata_gap"],
  },
  pool: {
    core: ["pool_detail", "pool_publication_facts"],
    // CL only — absent for reserves_only pools (the group load skips absent
    // keys, CoW `execution_flow` precedent).
    profile: ["pool_profile_at", "pool_profile_concentration", "pool_ticks_at"],
    // State history CL only; reserves history for every pool.
    history: ["pool_state_history", "pool_reserves_history"],
    // CL only.
    fees: ["pool_fee_growth"],
    // CL only, ON DEMAND — loads when the "Over time" view is opened.
    heatmap: ["pool_profile_heatmap"],
  },
  token: {
    core: ["token_detail", "token_pools"],
  },
};

/** Groups that must NOT be background-streamed (`${section}.${group}`). */
export const ON_DEMAND_GROUPS: ReadonlySet<string> = new Set(["pool.heatmap"]);

/** dataset key -> owning `{section, group}`. */
export const DATASET_GROUP: Record<string, { section: string; group: string }> = {};
for (const [section, groups] of Object.entries(SECTION_GROUPS)) {
  for (const [group, keys] of Object.entries(groups)) {
    for (const key of keys) DATASET_GROUP[key] = { section, group };
  }
}

/** Every dataset key, in section/group order. */
export const ALL_DATASET_KEYS: readonly string[] = Object.keys(DATASET_GROUP);
