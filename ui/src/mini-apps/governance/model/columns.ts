// Per-table presentation config for PaginatedTable renders: display labels
// and hidden columns. Cell renderers live beside the tables (sections/detail)
// because they need click-through callbacks.

export const COLUMN_LABELS: Record<string, string> = {
  title: "Title",
  state: "State",
  type: "Type",
  author: "Author",
  start_at: "Voting starts",
  end_at: "Voting ends",
  created_at: "Created",
  scores_total: "Total VP cast",
  scores_state: "Scores",
  quorum_status: "Quorum",
  quorum_ratio: "Quorum ratio",
  votes_count: "Votes",
  leading_choice: "Leading choice",
  leading_choice_share: "Leading share",
  gip_number: "GIP",
  voter: "Voter",
  vote_count: "Votes",
  total_vp: "Total VP",
  avg_vp: "Avg VP",
  first_vote_at: "First vote",
  last_vote_at: "Latest vote",
  choice_label: "Choice",
  proposal_state: "State",
  vp: "VP",
  vp_state: "VP state",
  vp_share: "VP share",
  choice_kind: "Choice",
  reason: "Reason",
  proposal_title: "Proposal",
  category_name: "Category",
  posts_count: "Posts",
  reply_count: "Replies",
  participant_count: "Participants",
  views: "Views",
  like_count: "Likes",
  likes_given: "Likes given",
  likes_received: "Likes received",
  last_posted_at: "Last activity",
  status: "Status",
  username: "Username",
  user_id: "User ID",
  trust_level: "Trust level",
  days_visited: "Days visited",
  post_count: "Posts",
  topic_count: "Topics",
  posts_in_range: "Posts (range)",
  topics_started: "Topics started",
  last_post_at: "Last post",
  post_number: "#",
  reads: "Reads",
  topic_title: "Topic",
  excerpt: "Excerpt",
  link_source: "Link tier",
  token_address: "Token",
  symbol: "Symbol",
  decimals: "Decimals",
  metadata_status: "Metadata",
  wallets_holding: "Wallets",
  balance_units: "Balance",
  balance_total_raw: "Balance (raw)",
  supply_share: "Share of supply",
  // Share of the TREASURY's own position, not of the token's supply — the two
  // read alike at a glance and answer completely different questions.
  treasury_share: "Share of treasury position",
  symbol_collisions: "Others claiming symbol",
  value_usd: "USD value",
  wallet_address: "Wallet",
  tokens_held: "Tokens",
  unnamed_positions: "Unnamed",
  gno_units: "GNO",
  is_ltd: "Gnosis Ltd.",
  chain_id: "Chain",
  bucket: "Month",
  anchor_block: "Anchor block",
  positions: "Positions",
  tokens_named: "Named",
  gno_units_ex_ltd: "GNO (ex-Ltd.)",
  units: "Units",
  units_raw: "Units (raw)",
  dimension: "Field",
  known: "Known",
  unknown: "Unknown",
  pct_known: "Coverage",
  hours_left: "Ends in (h)",
  phase: "Phase",
  has_proposal: "Reached a vote",
  topic_id: "Topic",
  proposal_id: "Proposal",
  delegate: "Delegate",
  delegator_count: "Delegators",
  first_delegation_at: "First delegation",
  last_delegation_at: "Latest delegation",
  delegated_vp_total: "Delegated VP",
  delegated_vp_mainnet: "Delegated VP (Ethereum)",
  delegated_vp_gnosischain: "Delegated VP (Gnosis Chain)",
  poll_name: "Poll",
  poll_type: "Type",
  results_visibility: "Results",
  voters: "Voters",
  options_count: "Options",
  leading_option: "Leading option",
  leading_votes: "Leading votes",
  close_at: "Closes",
  likes_in_range: "Likes (range)",
  likers_in_range: "Likers (range)",
  likes_received_in_range: "Likes received (range)",
  likes_given_in_range: "Likes given (range)",
  lifetime_like_count: "Likes (lifetime)",
  last_like_at: "Last like",
  poll_voter_slots: "Poll voters",
};

/** Columns kept in the row payload (for click-through identifiers and cell
 * renderers) but not shown as table columns. */
export const HIDDEN_COLUMNS: Record<string, string[]> = {
  proposals: ["id", "discussion", "discussion_topic_id", "choice_shape_flagged", "created_at", "snapshot_block", "quorum", "choices", "scores", "len_ok"],
  // choice_kind is VISIBLE (rendered as the "Choice" cell from the row's
  // choice_index/choice_indexes/choice_label payload columns, which stay hidden).
  proposal_votes: ["vote_id", "voter_key", "proposal_id", "choice_index", "choice_indexes"],
  voter_votes: ["vote_id", "voter_key", "proposal_id", "choice_index", "choice_indexes", "choice_label"],
  voter_leaderboard: ["voter_key"],
  forum_topics: ["id", "category_id", "created_at", "slug", "bumped_at", "closed", "archived", "pinned"],
  // user_id is deliberately VISIBLE post-de-identification (WL-039): the
  // leaderboard payload carries no username, so the id column IS the identity
  // column and the click-through target.
  contributor_leaderboard: ["id"],
  // results_hidden / leading_tied stay in the payload: the leading-option
  // cell renders Hidden / Tie / No votes from them. poll_name stays VISIBLE
  // as the disambiguator for topics carrying two polls (its value is usually
  // the literal "poll" — the topic title leads).
  forum_polls: [
    "poll_id", "topic_id", "post_id", "post_number", "results_hidden",
    "leading_tied", "results_visibility",
  ],
  most_liked_topics: ["id", "category_id", "last_like_at"],
  contributor_posts: ["id", "topic_id", "user_id", "post_id"],
  // balance_total_raw stays VISIBLE: it is the authoritative exact integer, and
  // it is the only balance shown at all for a token whose decimals were never
  // observed. metadata_known is a sort key, not a column.
  treasury_holdings: ["metadata_known", "decimals"],
  treasury_by_wallet: ["is_ltd"],
  treasury_wallet_positions: ["chain_id", "decimals", "value_usd"],
};

export function hiddenColumnsFor(datasetKey: string): string[] {
  return HIDDEN_COLUMNS[datasetKey] ?? [];
}
