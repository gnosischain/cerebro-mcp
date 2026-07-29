// TS mirror of the Governance Explorer backend view state
// (src/cerebro_mcp/tools/visualization/governance_explorer.py `_empty_state`).
// Wire types (MiniAppPayload / DatasetDescriptor) live in
// ../shared/miniAppTypes.ts — never redefined here.

export type GovSection =
  | "overview"
  | "proposals"
  | "voters"
  | "forum"
  | "delegations"
  | "treasury"
  | "entity";

export type GovEntityType =
  | "proposal"
  | "voter"
  | "forum_topic"
  | "forum_user"
  // Treasury entities are identified as `<chain_id>:<address>`: 23 of the 24
  // census wallets exist verbatim on both chains, so a bare address is
  // ambiguous 96% of the time.
  | "treasury_token"
  | "treasury_wallet";

/** Frozen date-range contract: `""` (all history), `"90d"` / `"1y"` relative
 * presets anchored to now() UTC, or an ISO pair (custom). No window_days
 * parameter exists on the wire — `window_days` here is UI bookkeeping only
 * (90 | 365 for presets, 0 for all, null for custom). */
export interface GovDateRange {
  kind: "all" | "relative" | "absolute";
  anchor?: "now" | "explicit";
  window_days: number | null;
  start_at: string;
  end_at: string;
}

export interface GovFilters {
  query: string;
  proposal_state: string;
  proposal_type: string;
  quorum_status: string;
  category_id: number;
  forum_status: string;
  sort_by: string;
  /** Treasury-only. Optional so existing filter literals stay valid; the
   * backend always returns them and defaults them to 0/""/false. */
  chain_id?: number;
  asset?: string;
  exclude_ltd?: boolean;
}

export interface GovSourceFreshness {
  latest_ingested_at: string | null;
  latest_activity_at: string | null;
  stale: boolean;
}

/** Two independent freshness clocks — Snapshot ingestion/activity vs forum. */
export interface GovFreshness {
  snapshot: GovSourceFreshness;
  forum: GovSourceFreshness;
}

export interface GovSearchCandidate {
  entity_type: GovEntityType;
  identifier: string;
  label: string;
  role: string;
  evidence_count: number;
}

export interface GovSelectedEntity {
  entity_type: GovEntityType;
  identifier: string;
  label: string;
}

export interface GovBreadcrumb {
  label: string;
  entity_type: GovEntityType;
  identifier: string;
}

export interface GovCoverage {
  basis?: string;
  actual_start?: string | null;
  actual_end?: string | null;
  returned_rows?: number;
  source_rows?: number | null;
  row_cap?: number | null;
  truncated?: boolean;
  mode?: string;
  error?: string;
  source_kind?: "snapshot" | "forum" | "cross" | "delegation" | "treasury";
  source_label?: string;
  warning_codes?: string[];
}

/** CoinGecko spot prices, patched in by `load_governance_overlays`.
 *
 * Tagged and nested so a HISTORICAL source is a drop-in with no call-site
 * change: `priceFor(src, chain, token, date)` ignores `date` for "spot" (and
 * the caller must then caption the chart as a constant-price revaluation) and
 * resolves it for "historical".
 *
 * A token CoinGecko does not list is ABSENT, never priced 0 — this treasury
 * holds 19 distinct tokens spoofing the symbol `USDC`, and a fabricated $0
 * would make them indistinguishable from the real one. */
export interface GovPriceOverlay {
  kind: "spot" | "historical";
  /** chainId -> lowercase token address -> USD (spot) or {date: USD}. */
  by_chain: Record<string, Record<string, number | Record<string, number>>>;
}

export interface GovernanceViewState {
  section: GovSection;
  title?: string;
  date_range: GovDateRange;
  filters: GovFilters;
  selected_entity: GovSelectedEntity | null;
  breadcrumbs: GovBreadcrumb[];
  search: { query: string; candidates: GovSearchCandidate[] };
  applied_request_id: number;
  scope_id: string;
  coverage: Record<string, GovCoverage>;
  coverage_warnings: string[];
  warnings: string[];
  dataset_revisions: Record<string, number>;
  /** `${section}.${group}` → loaded flag. `false` = not loaded (skeleton),
   * `true` = loaded clean, `"partial"` = loaded with at least one dataset
   * failed (error cards render; retry is user-driven). */
  loaded_groups?: Record<string, boolean | "partial">;
  section_fingerprints?: Record<string, string>;
  section_datasets?: Record<string, string[]>;
  section_lru?: string[];
  freshness: GovFreshness;
  /** chainId -> lowercase token address -> CoinGecko logo URL. OPTIONAL: any
   * required field here breaks the complete-literal builders in
   * `__tests__/askCerebro.test.ts` and `devFixture.ts` at `tsc` time while
   * vitest still passes. */
  icon_overlay?: Record<string, Record<string, string>>;
  price_overlay?: GovPriceOverlay;
  /** ISO instant the quotes were taken. A price without a timestamp is not
   * evidence — every USD figure renders this alongside it. */
  price_overlay_at?: string;
}

// ---------------------------------------------------------------------------
// Typed rows for the main datasets (parsed via model/parseRows.ts).
// ---------------------------------------------------------------------------

export interface SpaceSummaryRow {
  proposal_count: number;
  vote_count: number;
  voter_count: number;
  follower_count: number;
  topic_count: number;
  post_count: number;
  forum_user_count: number;
}

export interface ActivityRow {
  bucket: string;
  bucket_unit: "hour" | "day" | "week" | "month";
  proposals_created?: number;
  votes_cast?: number;
  topics_created?: number;
  posts_created?: number;
  [metric: string]: unknown;
}

export interface ProposalRow {
  id: string;
  title: string;
  state: string;
  type: string;
  author: string;
  created_at: string;
  start_at: string;
  end_at: string;
  snapshot_block: number | null;
  scores_total: number | null;
  scores_state: string;
  quorum: number | null;
  quorum_status: "met" | "missed" | "unspecified";
  quorum_ratio: number | null;
  votes_count: number | null;
  leading_choice: string;
  leading_choice_share: number | null;
  choice_shape_flagged?: boolean | number;
  gip_number: number | null;
  discussion: string;
  discussion_topic_id: number | null;
}

export interface VoteRow {
  vote_id: string;
  proposal_id?: string;
  proposal_title?: string;
  proposal_state?: string;
  voter?: string;
  voter_key?: string;
  vp: number | null;
  vp_state?: string;
  created_at: string;
  choice_kind: "single" | "ranked" | "unsupported";
  choice_index?: number | null;
  choice_indexes?: number[];
  choice_label?: string;
  reason?: string;
}

export interface VoterLeaderboardRow {
  voter: string;
  vote_count: number;
  total_vp: number | null;
  avg_vp: number | null;
  first_vote_at?: string;
  last_vote_at?: string;
}

export interface ConcentrationRow {
  tier: number;
  metric: "vp" | "votes";
  share: number | null;
}

export interface ForumCategoryRow {
  id: number;
  name: string;
  topic_count: number | null;
  post_count?: number | null;
}

export interface TopicRow {
  id: number;
  title: string;
  category_id: number | null;
  category_name?: string;
  posts_count: number | null;
  reply_count: number | null;
  views: number | null;
  like_count: number | null;
  participant_count?: number | null;
  created_at: string;
  last_posted_at: string;
  status?: string;
  gip_number: number | null;
}

export interface PostRow {
  id: number;
  topic_id: number;
  post_number: number;
  username: string;
  created_at: string;
  reply_to_post_number: number | null;
  like_count: number | null;
  reads: number | null;
  raw_markdown?: string;
  cooked_html?: string;
  plain_text?: string;
}

// ---------------------------------------------------------------------------
// Delegation plane (Snapshot DelegateRegistry, rpc_log_indexer) — Ethereum
// mainnet (chain 1) AND Gnosis Chain (chain 100); the gnosis.eth space
// delegates on both. Edge counts come from the registry view; "delegated
// voting power" is Snapshot's realized vp_by_strategy delegation share, with
// the delegation slots resolved per proposal (the space has rewritten its
// strategy list three times). Voted delegates only — NULL, never 0, where no
// realized figure exists.
// ---------------------------------------------------------------------------

export interface DelegationSummaryRow {
  active_delegators: number;
  active_delegates: number;
  total_events: number;
  set_events: number;
  clear_events: number;
  re_delegations: number;
  clear_rate: number | null;
}

export interface TopDelegateRow {
  delegate: string;
  delegator_count: number;
  first_delegation_at?: string;
  last_delegation_at?: string;
}

export interface DelegationPowerRow {
  delegate: string;
  delegator_count: number;
  last_vote_at?: string;
  delegated_vp_gnosischain: number | null;
  delegated_vp_mainnet: number | null;
  delegated_vp_total: number | null;
}

/** One cross-link row. `proposal_forum_links` rows carry `linked_type`,
 * `activity_count`, `activity_at`; `topic_proposal_links` rows are always
 * proposals and carry `state`, `votes_count`, `created_at`. */
export interface LinkRow {
  linked_type: "forum_topic" | "proposal";
  linked_id: string;
  linked_title?: string;
  link_source: "discussion" | "gip";
  activity_count?: number | null;
  activity_at?: string;
  state?: string;
  votes_count?: number | null;
  created_at?: string;
}
