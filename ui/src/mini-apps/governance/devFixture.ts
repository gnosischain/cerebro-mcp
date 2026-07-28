// Dev-only mock payload (used via `import.meta.env.DEV` in GovernanceApp).
// Descriptors are shaped exactly like real ones: database governance_db,
// mode exact_capped, row_cap 10000. Rows exercise the tricky shapes: quorum
// met / missed / unspecified / pending, single + ranked + malformed vote
// choices, the full post-body fallback chain (raw markdown with a [quote]
// block, cooked-only with a benign XSS probe, empty-both), and both
// link_source tiers.

import type { DatasetDescriptor, MiniAppPayload } from "../shared/miniAppTypes";
import type { GovernanceViewState } from "./types";

function descriptor(key: string, columns: string[], rows: unknown[][]): DatasetDescriptor {
  return {
    key, title: key.split("_").join(" "), sql: "-- development fixture", database: "governance_db",
    columns: columns.map((name) => ({ name, type: "Unknown" })),
    stats: { row_count: rows.length, rows_returned: rows.length, mode: "exact_capped", source_rows: rows.length, row_cap: 10000, truncated: false, warnings: [] },
    preview_rows: rows,
    provenance: { coverage: { actual_start: "2020-01-01T00:00:00Z", actual_end: "2026-07-22T00:00:00Z", mode: "exact_capped", warning_codes: [] } },
  };
}

const PROPOSAL_COLUMNS = [
  "id", "title", "state", "type", "author", "created_at", "start_at", "end_at",
  "snapshot_block", "scores_total", "scores_state", "quorum", "quorum_status",
  "quorum_ratio", "votes_count", "leading_choice", "leading_choice_share",
  "choice_shape_flagged", "gip_number", "discussion", "discussion_topic_id",
];

const P1 = `0x${"a1".repeat(32)}`;
const P2 = `0x${"b2".repeat(32)}`;
const P3 = `0x${"c3".repeat(32)}`;
const P4 = `0x${"d4".repeat(32)}`;
const P5 = `0x${"e5".repeat(32)}`;

export const MOCK_PAYLOAD: MiniAppPayload<GovernanceViewState> = {
  type: "INITIAL_LOAD", view_id: "governance-dev", app_id: "governance", title: "Governance Explorer", status: "ready",
  datasets: {
    space_summary: descriptor(
      "space_summary",
      ["proposal_count", "vote_count", "voter_count", "follower_count", "topic_count", "post_count", "forum_user_count"],
      [[253, 48136, 6341, 12229, 882, 6836, 2665]],
    ),
    source_freshness: descriptor(
      "source_freshness",
      ["source", "latest_ingested_at", "latest_activity_at"],
      [
        // Snapshot activity lags ingestion — two independent clocks.
        ["snapshot", "2026-07-22T05:00:00Z", "2026-06-25T12:00:00Z"],
        ["forum", "2026-07-22T05:00:00Z", "2026-07-22T03:40:00Z"],
      ],
    ),
    // LONG format — one row per (bucket, metric), mirroring the backend
    // UNION ALL SQL. parseActivity pivots to wide. The last bucket has no
    // proposals_created / votes_cast rows (missing metric -> chart ?? 0).
    governance_activity: descriptor(
      "governance_activity",
      ["bucket", "metric", "metric_value", "bucket_unit"],
      [
        ["2026-06-29", "proposals_created", 2, "week"],
        ["2026-06-29", "votes_cast", 1450, "week"],
        ["2026-06-29", "topics_created", 6, "week"],
        ["2026-06-29", "posts_created", 84, "week"],
        ["2026-07-06", "proposals_created", 1, "week"],
        ["2026-07-06", "votes_cast", 610, "week"],
        ["2026-07-06", "topics_created", 4, "week"],
        ["2026-07-06", "posts_created", 51, "week"],
        ["2026-07-13", "topics_created", 7, "week"],
        ["2026-07-13", "posts_created", 66, "week"],
      ],
    ),
    proposals: descriptor("proposals", PROPOSAL_COLUMNS, [
      // basic, quorum met, discussion-linked
      [P1, "GIP-149: Should GnosisDAO fund the thing?", "closed", "basic", `0x${"11".repeat(20)}`,
        "2026-05-01T09:00:00Z", "2026-05-02T09:00:00Z", "2026-05-09T09:00:00Z",
        34567890, 120000, "final", 75000, "met", 1.6, 412, "For", 0.83, 0, 149,
        "https://forum.gnosis.io/t/gip-149-should-gnosisdao-fund-the-thing/12131", 12131],
      // single-choice, quorum missed, "GIP 152 -" title variant
      [P2, "GIP 152 - Treasury diversification", "closed", "single-choice", `0x${"22".repeat(20)}`,
        "2026-05-20T10:00:00Z", "2026-05-21T10:00:00Z", "2026-05-28T10:00:00Z",
        34600001, 42000, "final", 75000, "missed", 0.56, 88, "Option A", 0.61, 0, 152, "", null],
      // THE ranked-choice proposal
      [P3, "GIP-128: Ranked signal on validator set", "closed", "ranked-choice", `0x${"33".repeat(20)}`,
        "2026-03-10T08:00:00Z", "2026-03-11T08:00:00Z", "2026-03-18T08:00:00Z",
        34100500, 98000, "final", 75000, "met", 1.31, 240, "Candidate B", 0.44, 0, 128, "", null],
      // zero-quorum announcement -> unspecified
      [P4, "Community call schedule (announcement)", "closed", "basic", `0x${"44".repeat(20)}`,
        "2026-04-02T12:00:00Z", "2026-04-03T12:00:00Z", "2026-04-05T12:00:00Z",
        34300000, 5100, "final", 0, "unspecified", null, 33, "Acknowledge", 0.97, 0, null, "", null],
      // pending scores_state — no leading choice yet
      [P5, "GIP-153: Pending scores example", "closed", "basic", `0x${"55".repeat(20)}`,
        "2026-07-10T09:00:00Z", "2026-07-11T09:00:00Z", "2026-07-18T09:00:00Z",
        34990000, 0, "pending", 75000, "missed", 0, 12, "", null, 0, 153, "", null],
    ]),
    proposal_votes: descriptor(
      "proposal_votes",
      ["vote_id", "voter_key", "voter", "created_at", "vp", "vp_state", "choice_kind", "choice_index", "choice_indexes", "reason"],
      [
        [`0x${"f1".repeat(32)}`, `0x${"66".repeat(20)}`, `0x${"66".repeat(20)}`, "2026-05-03T10:00:00Z", 51000.5, "final", "single", 1, [], "Strongly in favor"],
        [`0x${"f2".repeat(32)}`, `0x${"77".repeat(20)}`, `0x${"77".repeat(20)}`, "2026-03-12T11:00:00Z", 1200, "final", "ranked", null, [2, 1, 3], ""],
        [`0x${"f3".repeat(32)}`, `0x${"88".repeat(20)}`, `0x${"88".repeat(20)}`, "2026-05-04T12:00:00Z", 10, "final", "unsupported", null, [], ""],
      ],
    ),
    topic_posts: descriptor(
      "topic_posts",
      ["id", "topic_id", "post_number", "username", "created_at", "reply_to_post_number", "like_count", "reads", "raw_markdown", "cooked_html", "plain_text"],
      [
        // raw markdown with a Discourse [quote] block
        [9001, 12131, 1, "alice", "2026-04-28T09:00:00Z", null, 14, 320,
          "[quote=\"bob, post:2, topic:12131\"]We should fund it.[/quote]\n\nI **agree** — see GIP-149.",
          "<p>quoted + agreement</p>", "We should fund it. I agree — see GIP-149."],
        // cooked-only (empty raw) with a benign XSS probe for eyeballing the sanitizer
        [9002, 12131, 2, "mallory", "2026-04-28T10:00:00Z", 1, 0, 250,
          "",
          "<p>Hello <script>alert(1)</script><a href=\"javascript:alert(2)\">bad link</a> <a href=\"https://forum.gnosis.io/t/x/1\">good link</a> <img src=\"https://forum.gnosis.io/uploads/pic.png\"></p>",
          "Hello bad link good link"],
        // empty raw AND cooked — plain_text last resort
        [9003, 12131, 3, "carol", "2026-04-29T11:00:00Z", null, 2, 190,
          "", "", "Plain text only body."],
      ],
    ),
    proposal_forum_links: descriptor(
      "proposal_forum_links",
      ["linked_type", "linked_id", "linked_title", "link_source", "activity_count", "activity_at"],
      [
        ["forum_topic", "12131", "GIP-149: Should GnosisDAO fund the thing?", "discussion", 34, "2026-05-10T09:00:00Z"],
        ["forum_topic", "11987", "Pre-GIP-149 temperature check", "gip", 12, "2026-04-20T00:00:00Z"],
        // A sibling proposal sharing the same GIP number — exercises the
        // 'proposal' linked_type click route.
        ["proposal", P5, "GIP-149: Temperature check (earlier signal)", "gip", 12, "2026-04-15T10:00:00Z"],
      ],
    ),
    // Delegation plane (Snapshot DelegateRegistry, mainnet). Registry edges
    // are counts; delegation_power carries realized vp_by_strategy (voted
    // delegates only). delegation_activity / _churn are WIDE (parseActivity).
    delegation_summary: descriptor(
      "delegation_summary",
      ["active_delegators", "active_delegates", "total_events", "set_events", "clear_events", "re_delegations", "clear_rate"],
      [[1820, 143, 5210, 3990, 1220, 210, 0.3058]],
    ),
    top_delegates: descriptor(
      "top_delegates",
      ["delegate", "delegator_count", "first_delegation_at", "last_delegation_at"],
      [
        ["0x0da0c3e52c977ed3cbc641ff02dd271c3ed55afe", 214, "2021-11-16T11:49:47Z", "2026-06-02T08:14:00Z"],
        ["0x4fde2196d2d4fcb7a23cf333be20849ce498db2c", 156, "2022-03-30T05:46:30Z", "2026-05-20T19:03:00Z"],
        ["0x6d9aba400a2a487a5fb76c6d56518835553cd284", 98, "2021-11-17T15:14:53Z", "2026-02-02T02:06:47Z"],
        ["0xa333100ca865cd8b504e99faf0a578b199ec49fe", 42, "2022-08-12T03:48:16Z", "2025-12-11T10:20:00Z"],
      ],
    ),
    delegation_activity: descriptor(
      "delegation_activity",
      ["bucket", "set_events", "clear_events", "net_change", "cumulative_net", "bucket_unit"],
      [
        ["2026-05", 120, 30, 90, 1600, "month"],
        ["2026-06", 90, 45, 45, 1645, "month"],
        ["2026-07", 60, 20, 40, 1685, "month"],
      ],
    ),
    delegation_power: descriptor(
      "delegation_power",
      ["delegate", "delegator_count", "last_vote_at", "delegated_vp_gnosischain", "delegated_vp_mainnet", "delegated_vp_total"],
      [
        ["0x0da0c3e52c977ed3cbc641ff02dd271c3ed55afe", 214, "2026-06-25T12:00:00Z", 12000, 8000, 20000],
        ["0x4fde2196d2d4fcb7a23cf333be20849ce498db2c", 156, "2026-06-20T09:00:00Z", 9000, 3000, 12000],
        ["0x6d9aba400a2a487a5fb76c6d56518835553cd284", 98, "2026-02-02T02:06:47Z", 0, 5400, 5400],
        ["0xa333100ca865cd8b504e99faf0a578b199ec49fe", 42, "2025-12-11T10:20:00Z", 2100, 0, 2100],
      ],
    ),
    delegation_concentration: descriptor(
      "delegation_concentration",
      ["tier", "tier_value", "total_value", "share"],
      [
        [5, 620, 1820, 0.3407],
        [10, 940, 1820, 0.5165],
        [20, 1300, 1820, 0.7143],
      ],
    ),
    delegation_churn: descriptor(
      "delegation_churn",
      ["bucket", "new_delegators", "repointed", "cleared", "bucket_unit"],
      [
        ["2026-05", 80, 40, 30, "month"],
        ["2026-06", 55, 35, 45, "month"],
        ["2026-07", 40, 20, 20, "month"],
      ],
    ),
    // Treasury: verified balances at a pinned finalized block. Rows exercise
    // the states that matter — a fully resolved token (GNO), a 6-decimal one
    // (USDC), an unresolved token whose decimals were never observed (must
    // render "not scalable" + the exact integer, never a scaled guess), and a
    // legitimate 0-decimals token (must NOT be confused with unknown).
    treasury_summary: descriptor(
      "treasury_summary",
      ["chain_id", "as_of", "anchor_block", "anchor_hash", "tokens_held", "wallets_tracked", "positions", "tokens_named", "gno_units", "gno_units_ex_ltd", "metadata_known_share", "nav_usd"],
      [[1, "2026-07-27", 25627590, `0x${"13".repeat(32)}`, 231, 23, 2485, 27, 784931.82, 424520.82, 0.1169, null]],
    ),
    treasury_holdings: descriptor(
      "treasury_holdings",
      ["chain_id", "token_address", "symbol", "decimals", "metadata_status", "metadata_known", "wallets_holding", "balance_total_raw", "balance_units", "supply_share", "value_usd"],
      [
        [1, "0x1a5f9352af8af974bfc03399e3767df6370d82e4", "OWL", 18, "resolved", 1, 2, "1087933347759016548062892", 1087933.35, 0.2486, null],
        [1, "0x6810e776880c02933d47db1b9fc05908e5386b96", "GNO", 18, "resolved", 1, 5, "784931822290089813540215", 784931.82, 0.0785, null],
        [1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6, "resolved", 1, 3, "621494537595", 621494.54, 0.0000124, null],
        [1, "0x2ec109a0cefec70661a242a8b54cae8f45630397", null, null, "failed", 0, 23, "10000000000000000000", null, null, null],
        [1, "0x78a25f62c52973abdb2f467116df97a1f57fe211", "PTS", 0, "resolved", 1, 23, "5700", 5700, null, null],
      ],
    ),
    treasury_by_wallet: descriptor(
      "treasury_by_wallet",
      ["chain_id", "wallet_address", "is_ltd", "tokens_held", "unnamed_positions", "gno_units", "value_usd"],
      [
        [1, "0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f", 0, 102, 101, 414931.58, null],
        [1, "0x604e4557e9020841f4e8eb98148de3d3cdea350c", 1, 100, 99, 360411, null],
        [1, "0x689d4bd36bc1938af5ca2673c3c753235e3b4d2b", 0, 98, 97, 5000, null],
        [1, "0x4971dd016127f390a3ef6b956ff944d0e2e1e462", 0, 152, 131, 3738.19, null],
        [1, "0x7eea4286e9e82ba332f49400d037609bb1cf00da", 0, 100, 98, 851.05, null],
      ],
    ),
    treasury_coverage: descriptor(
      "treasury_coverage",
      ["dimension", "known", "unknown", "pct_known"],
      [
        ["decimals", 27, 204, 0.1169],
        ["metadata", 27, 204, 0.1169],
        ["symbol", 27, 204, 0.1169],
        ["usd_price", 0, 231, 0],
      ],
    ),
    // Proposal entity datasets (for dev-rendering ProposalDetail incl. the
    // vote-trend chart). proposal_votes / proposal_forum_links are above.
    proposal_detail: descriptor(
      "proposal_detail",
      ["id", "space_id", "title", "state", "type", "author", "discussion", "created_at", "start_at", "end_at", "snapshot_block", "scores_total", "quorum", "votes_count", "scores_state", "quorum_ratio", "quorum_status", "gip_number", "discussion_topic_id", "body_markdown", "choices_json", "scores_json", "snapshot_url"],
      [[P1, "gnosis.eth", "GIP-149: Should GnosisDAO fund the thing?", "closed", "basic", `0x${"11".repeat(20)}`,
        "https://forum.gnosis.io/t/gip-149-should-gnosisdao-fund-the-thing/12131",
        "2026-05-01T09:00:00Z", "2026-05-02T09:00:00Z", "2026-05-09T09:00:00Z", 34567890, 120000, 75000, 412, "final",
        1.6, "met", 149, 12131, "## Summary\nThis proposal funds the thing. See GIP-149 for context.",
        "[\"For\",\"Against\"]", "[100000,20000]", `https://snapshot.org/#/gnosis.eth/proposal/${P1}`]],
    ),
    proposal_vote_trend: descriptor(
      "proposal_vote_trend",
      ["bucket", "votes", "vp", "cumulative_votes", "cumulative_vp", "bucket_unit"],
      [
        ["2026-05-02T10:00:00", 40, 22000, 40, 22000, "hour"],
        ["2026-05-02T14:00:00", 60, 31000, 100, 53000, "hour"],
        ["2026-05-03T09:00:00", 55, 18000, 155, 71000, "hour"],
        ["2026-05-05T12:00:00", 120, 30000, 275, 101000, "hour"],
        ["2026-05-08T20:00:00", 137, 19000, 412, 120000, "hour"],
      ],
    ),
  },
  view_state: {
    section: "overview",
    title: "Governance Explorer",
    date_range: { kind: "all", anchor: "now", window_days: 0, start_at: "", end_at: "" },
    filters: { query: "", proposal_state: "", proposal_type: "", quorum_status: "", category_id: 0, forum_status: "", sort_by: "" },
    selected_entity: null,
    breadcrumbs: [],
    search: { query: "", candidates: [] },
    applied_request_id: 0,
    scope_id: "overview:all::::::0:::",
    coverage: {},
    coverage_warnings: [],
    warnings: [],
    dataset_revisions: {
      space_summary: 1, source_freshness: 1, governance_activity: 1,
      proposals: 1, proposal_votes: 1, topic_posts: 1, proposal_forum_links: 1,
    },
    // Every frozen `section.group` key appears exactly once (test-enforced).
    loaded_groups: {
      "overview.core": true, "overview.insights": false,
      "proposals.core": false, "proposals.charts": false,
      "voters.core": false, "voters.insights": false,
      "forum.core": false, "forum.insights": false,
      "delegations.core": false, "delegations.insights": false,
      "treasury.core": false, "treasury.insights": false,
    },
    section_fingerprints: { overview: "dev" },
    section_datasets: { overview: ["space_summary", "source_freshness", "governance_activity"] },
    section_lru: ["overview"],
    freshness: {
      // Dev-only stale variant: snapshot is flagged stale so the STALE badge
      // is visible while developing; forum shows the fresh path.
      snapshot: { latest_ingested_at: "2026-07-22T05:00:00Z", latest_activity_at: "2026-06-25T12:00:00Z", stale: true },
      forum: { latest_ingested_at: "2026-07-22T05:00:00Z", latest_activity_at: "2026-07-22T03:40:00Z", stale: false },
    },
  },
};
