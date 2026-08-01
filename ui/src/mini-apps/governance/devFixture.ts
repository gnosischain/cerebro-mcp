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
    // Live-now panel. live_votes is INTENTIONALLY empty: every one of the 253
    // indexed proposals has closed, so the empty state is the normal render and
    // the fixture must exercise it rather than hide it behind sample rows.
    live_votes: descriptor(
      "live_votes",
      ["proposal_id", "title", "gip", "state", "start_at", "end_at", "hours_left",
       "votes_count", "scores_total", "quorum", "quorum_status", "quorum_ratio"],
      [],
    ),
    gip_pipeline: descriptor(
      "gip_pipeline",
      ["topic_id", "title", "gip", "phase", "posts_count", "participant_count",
       "views", "created_at", "last_posted_at", "days_idle", "dormant_hidden",
       "ideas_hidden"],
      // Mirrors the SQL: phase-2 ONLY and days_idle <= 45. A phase-1 row or a
      // four-month-idle row here would be a fixture the query can never return,
      // which is how the panel came to look like it was listing dead threads.
      [
        [12390, "GIP 152 - Should GnosisDAO spin out the Gnosis App into an independent entity?", 152, "phase-2", 51, 24, 4100, "2026-06-30T09:00:00Z", "2026-07-28T23:11:45Z", 1, 4, 2],
        // No GIP number in the title yet — renders without a badge, which is
        // the honest state for a draft.
        [12383, "[REVIEW] GIP-XXX: Transition to an Independent Structural Governance", null, "phase-2", 16, 9, 1200, "2026-07-02T08:30:00Z", "2026-07-17T00:49:11Z", 12, 4, 2],
        // Idle past the render threshold but inside the window — exercises the
        // "idle Nd" marker without pretending a dormant thread is live.
        [12210, "GIP-129: Should GnosisDAO establish Seldon Inc and appoint a Gnosis VPN steward?", 129, "phase-2", 14, 10, 900, "2026-05-02T09:00:00Z", "2026-06-25T14:58:55Z", 35, 4, 2],
      ],
    ),
    graph_nodes: descriptor(
      "graph_nodes",
      ["gip", "label", "topic_id", "proposal_id", "stage", "proposal_state",
       "quorum_status", "author", "posts", "participants", "views", "category_id",
       "votes", "first_seen", "last_activity", "has_topic", "has_proposal"],
      [
        [122, "GIP-122: Should GnosisDAO fund X?", 9901, "0xaa", "voted", "closed", "met", "0xd714", 64, 30, 5200, 21, 210, "2025-01-14 09:00:00", "2026-03-01 00:00:00", 1, 1],
        [98, "GIP-98: Earlier decision this one builds on", 9800, "0xbb", "voted", "closed", "missed", "0xd714", 31, 18, 3100, 21, 180, "2024-06-02 09:00:00", "2025-08-01 00:00:00", 1, 1],
        // Discussed but never voted — the node exists, the proposal link does not.
        [152, "GIP 152 - Should GnosisDAO spin out the Gnosis App?", 12390, "", "phase-2", "", "", "", 51, 24, 4100, 21, 0, "2026-06-30 09:00:00", "2026-07-28 23:11:45", 1, 0],
        // Isolated: in the node set, absent from every edge. Exercises the
        // hide-isolated toggle and the "isolated, not missing" copy.
        [6, "GIP-6: Deploy Gnosis Auction", 1078, "", "unstaged", "", "", "", 10, 6, 700, 21, 0, "2021-03-01 09:00:00", "2021-03-30 15:11:54", 1, 0],
      ],
    ),
    graph_edges: descriptor(
      "graph_edges",
      ["src_gip", "dst_gip", "weight", "topics", "first_mention", "last_mention"],
      [
        [122, 98, 21, 1, "2025-02-13 20:22:43", "2026-01-28 04:18:10"],
        [152, 122, 4, 1, "2026-07-01 10:00:00", "2026-07-20 10:00:00"],
        // Forward citation (older cites newer) — the rare case, drawn on the
        // opposite side in amber so it does not blend in.
        [98, 122, 1, 1, "2025-03-01 10:00:00", "2025-03-01 10:00:00"],
        // Points at a GIP absent from graph_nodes — must be DROPPED, not turned
        // into a phantom node by the chart runtime.
        [122, 9999, 2, 1, "2025-01-01 00:00:00", "2025-01-01 00:00:00"],
      ],
    ),
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
    // vp_share MIRRORS proposal_votes.sql: vp / scores_total, NULL while
    // scores are pending (the last row exercises the em-dash render).
    proposal_votes: descriptor(
      "proposal_votes",
      ["vote_id", "voter_key", "voter", "created_at", "vp", "vp_state", "vp_share", "choice_kind", "choice_index", "choice_indexes", "reason"],
      [
        [`0x${"f1".repeat(32)}`, `0x${"66".repeat(20)}`, `0x${"66".repeat(20)}`, "2026-05-03T10:00:00Z", 51000.5, "final", 0.4104, "single", 1, [], "Strongly in favor"],
        [`0x${"f2".repeat(32)}`, `0x${"77".repeat(20)}`, `0x${"77".repeat(20)}`, "2026-03-12T11:00:00Z", 1200, "final", 0.0097, "ranked", null, [2, 1, 3], ""],
        [`0x${"f3".repeat(32)}`, `0x${"88".repeat(20)}`, `0x${"88".repeat(20)}`, "2026-05-04T12:00:00Z", 10, "pending", null, "unsupported", null, [], ""],
      ],
    ),
    // MIRRORS topic_proposal_links.sql (rows are ALL Snapshot proposals;
    // 'discussion' sorts before 'gip' — the identity-row button targets the
    // first row and folds the rest into its (+N) and tooltip).
    topic_proposal_links: descriptor(
      "topic_proposal_links",
      ["linked_id", "linked_title", "state", "link_source", "votes_count", "created_at"],
      [
        [P1, "GIP-149: Should GnosisDAO fund the thing?", "closed", "discussion", 153, "2026-05-01T12:00:00Z"],
        [P5, "GIP-149: Temperature check (earlier signal)", "closed", "gip", 88, "2026-04-15T10:00:00Z"],
      ],
    ),
    // MIRRORS topic_detail.sql — feeds the Topic KPI panel and the collapsed
    // thread's always-visible brief (GIP / author / phase tag / type tag).
    topic_detail: descriptor(
      "topic_detail",
      [
        "topic_id", "title", "slug", "category_id", "category_name",
        "posts_count", "reply_count", "views", "like_count",
        "participant_count", "tags", "created_at", "last_posted_at",
        "bumped_at", "closed", "archived", "pinned", "status", "gip_number",
        "topic_url",
      ],
      [[
        12131, "GIP-149: Should GnosisDAO fund the thing?",
        "gip-149-should-gnosisdao-fund-the-thing", 6, "GIPs",
        57, 56, 3200, 88, 24, ["phase-2", "funding"],
        "2026-04-28T09:00:00Z", "2026-07-21T22:10:00Z", "2026-07-21T22:10:00Z",
        0, 0, 0, "open", 149,
        "https://forum.gnosis.io/t/gip-149-should-gnosisdao-fund-the-thing/12131",
      ]],
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
    // Forum plane. MIRRORS forum_summary.sql: the like columns follow the
    // eligibility contract (active + mapped + scoped), exclusions are COUNTED
    // (likes_unmapped is nonzero here so the disclosure caption renders in
    // dev), and like_attribution_pct is the live all-history figure the UI
    // caption interpolates — never hard-coded copy.
    forum_summary: descriptor(
      "forum_summary",
      [
        "topic_count", "post_count", "view_count", "like_count",
        "participant_count", "open_count", "closed_count", "archived_count",
        "active_users", "active_categories", "likes_in_range",
        "distinct_likers", "likes_hidden_or_deleted", "likes_unmapped",
        "like_attribution_pct",
      ],
      [[882, 6836, 1204321, 14336, 4180, 640, 200, 42, 310, 14, 10365, 498, 0, 19, 0.723]],
    ),
    // MIRRORS contributor_leaderboard.sql: lifetime counter columns from
    // forum_users beside windowed ATTRIBUTED like columns (the disclosure
    // caption renders on this panel).
    contributor_leaderboard: descriptor(
      "contributor_leaderboard",
      [
        "user_id", "username", "name", "trust_level", "lifetime_posts",
        "lifetime_topics", "likes_received", "likes_given", "days_visited",
        "posts_in_range", "topics_started", "last_post_at",
        "likes_received_in_range", "likes_given_in_range",
      ],
      [
        [301, "gnosis-whale", "Gnosis Whale", 3, 412, 44, 890, 340, 720, 18, 2, "2026-07-21T10:00:00Z", 26, 9],
        [302, "forum-friend", "Forum Friend", 2, 208, 12, 310, 505, 501, 11, 1, "2026-07-19T08:30:00Z", 12, 21],
      ],
    ),
    // MIRRORS poll_summary.sql: poll grain collapsed from the option grain;
    // poll_voter_slots multi-counts a user voting in several polls.
    poll_summary: descriptor(
      "poll_summary",
      [
        "poll_count", "open_polls", "closed_polls", "multiple_choice_polls",
        "hidden_result_polls", "topics_with_polls", "poll_voter_slots",
        "latest_poll_at",
      ],
      [[189, 106, 83, 11, 2, 180, 3584, "2026-07-22T08:56:38Z"]],
    ),
    // MIRRORS forum_polls.sql — one row per poll, ORDER BY created_at DESC.
    // Exercises every leading-option cell branch: a leader, a multiple-choice
    // leader, a positive-score TIE (leading_option null, leading_tied 1), a
    // ZERO-VOTE poll (leading_votes 0, not a tie), a HIDDEN poll (nulls), and
    // a reply-post poll (post_number 3 — polls are not always the opener).
    forum_polls: descriptor(
      "forum_polls",
      [
        "poll_id", "topic_id", "topic_title", "post_id", "post_number",
        "poll_name", "poll_type", "status", "results_visibility", "close_at",
        "created_at", "options_count", "voters", "results_hidden",
        "leading_option", "leading_votes", "leading_tied",
      ],
      [
        [241, 12131, "GIP-149: Should GnosisDAO fund the thing?", 55001, 1, "poll", "regular", "closed", "always", "2026-07-29T08:56:00Z", "2026-07-22T08:56:38Z", 2, 18, 0, "In Favour", 14, 0],
        [240, 12100, "Treasury diversification options", 54900, 1, "poll", "multiple", "open", "always", null, "2026-07-15T14:28:59Z", 3, 64, 0, "Option B", 40, 0],
        [238, 12050, "Which logo should we adopt?", 54800, 3, "poll2", "regular", "open", "always", null, "2026-07-12T16:56:31Z", 2, 10, 0, null, 5, 1],
        [237, 12040, "Signal: park the idea?", 54700, 1, "poll", "regular", "open", "always", null, "2026-06-12T17:07:43Z", 2, 0, 0, null, 0, 0],
        [234, 12000, "Confidential compensation review", 54600, 1, "poll", "regular", "open", "on_vote", "2026-08-15T00:00:00Z", "2026-02-18T09:43:02Z", 3, 28, 1, null, null, 0],
      ],
    ),
    // LONG format mirroring poll_activity.sql; the last bucket is missing its
    // poll_voters row (missing metric -> chart ?? 0).
    poll_activity: descriptor(
      "poll_activity",
      ["bucket", "metric", "metric_value", "bucket_unit"],
      [
        ["2026-05-01", "polls_created", 3, "month"],
        ["2026-05-01", "poll_voters", 96, "month"],
        ["2026-06-01", "polls_created", 1, "month"],
        ["2026-06-01", "poll_voters", 4, "month"],
        ["2026-07-01", "polls_created", 2, "month"],
      ],
    ),
    // MIRRORS likes_by_category.sql: likes per (bucket, category). Three
    // categories so the top-N fold path is exercisable with maxSeries below 3.
    likes_by_category: descriptor(
      "likes_by_category",
      ["bucket", "bucket_unit", "category", "likes"],
      [
        ["2026-05-01", "month", "GIPs", 130],
        ["2026-05-01", "month", "General", 60],
        ["2026-05-01", "month", "Uncategorized", 20],
        ["2026-06-01", "month", "GIPs", 110],
        ["2026-06-01", "month", "General", 54],
        ["2026-07-01", "month", "GIPs", 70],
        ["2026-07-01", "month", "Uncategorized", 26],
      ],
    ),
    // MIRRORS topic_likes_activity.sql: one topic's likes per (bucket, post),
    // adaptive buckets (daily <= 120-day like span, else weekly). This fixture
    // topic spans ~4 months, so the SQL would emit WEEKLY buckets — the rows
    // must agree. post_number 0 is the de-indexed-post residual
    // ("Unknown post").
    topic_likes_activity: descriptor(
      "topic_likes_activity",
      ["bucket", "bucket_unit", "post_number", "username", "likes"],
      [
        ["2026-03-02", "week", 1, "gnosis-whale", 9],
        ["2026-03-02", "week", 3, "forum-friend", 4],
        ["2026-05-04", "week", 1, "gnosis-whale", 6],
        ["2026-05-04", "week", 0, "", 2],
        ["2026-07-13", "week", 3, "forum-friend", 5],
      ],
    ),
    // LONG format mirroring likes_activity.sql (attributed likes only).
    likes_activity: descriptor(
      "likes_activity",
      ["bucket", "metric", "metric_value", "bucket_unit"],
      [
        ["2026-05-01", "likes_given", 210, "month"],
        ["2026-05-01", "distinct_likers", 58, "month"],
        ["2026-06-01", "likes_given", 164, "month"],
        ["2026-06-01", "distinct_likers", 41, "month"],
        ["2026-07-01", "likes_given", 96, "month"],
      ],
    ),
    // MIRRORS most_liked_topics.sql: windowed ATTRIBUTED likes beside the
    // lifetime counter so both truths sit in one row.
    most_liked_topics: descriptor(
      "most_liked_topics",
      [
        "id", "title", "category_id", "likes_in_range", "likers_in_range",
        "lifetime_like_count", "posts_count", "last_like_at", "gip_number",
      ],
      [
        [12131, "GIP-149: Should GnosisDAO fund the thing?", 6, 42, 18, 88, 57, "2026-07-21T22:10:00Z", 149],
        [12100, "Treasury diversification options", 6, 31, 12, 40, 33, "2026-07-20T14:00:00Z", null],
      ],
    ),
    // MIRRORS topic_polls.sql — per-OPTION grain, ORDER BY poll_id,
    // option_votes DESC NULLS LAST. voters repeats per option BY DESIGN
    // (groupPollOptions takes it once, never sums). option_votes is null on
    // the hidden poll (the SQL neutralizes the -1 sentinel — a raw -1 must
    // never reach this contract).
    topic_polls: descriptor(
      "topic_polls",
      [
        "poll_id", "post_id", "post_number", "poll_name", "poll_type",
        "status", "results_visibility", "is_public", "close_at", "voters",
        "option_id", "option_label", "option_votes",
      ],
      [
        [234, 54600, 1, "poll", "regular", "open", "on_vote", 0, "2026-08-15T00:00:00Z", 28, "opt-a", "Approve", null],
        [234, 54600, 1, "poll", "regular", "open", "on_vote", 0, "2026-08-15T00:00:00Z", 28, "opt-b", "Reject", null],
        [234, 54600, 1, "poll", "regular", "open", "on_vote", 0, "2026-08-15T00:00:00Z", 28, "opt-c", "Abstain", null],
        [237, 54700, 1, "poll", "regular", "open", "always", 1, null, 0, "opt-a", "Yes", 0],
        [237, 54700, 1, "poll", "regular", "open", "always", 1, null, 0, "opt-b", "No", 0],
        [238, 54800, 3, "poll2", "regular", "open", "always", 1, null, 10, "opt-a", "Logo A", 5],
        [238, 54800, 3, "poll2", "regular", "open", "always", 1, null, 10, "opt-b", "Logo B", 5],
        [240, 54900, 1, "poll", "multiple", "open", "always", 1, null, 64, "opt-b", "Option B", 40],
        [240, 54900, 1, "poll", "multiple", "open", "always", 1, null, 64, "opt-c", "Option C", 31],
        [240, 54900, 1, "poll", "multiple", "open", "always", 1, null, 64, "opt-a", "Option A", 22],
        [241, 55001, 1, "poll", "regular", "closed", "always", 1, "2026-07-29T08:56:00Z", 18, "opt-a", "In Favour", 14],
        [241, 55001, 1, "poll", "regular", "closed", "always", 1, "2026-07-29T08:56:00Z", 18, "opt-b", "Against", 4],
      ],
    ),
    // Delegation plane (Snapshot DelegateRegistry — Ethereum mainnet AND
    // Gnosis Chain). Registry edges are counts; delegation_power carries
    // realized vp_by_strategy (voted delegates only), and is NULL — never 0 —
    // where no realized figure exists. delegation_activity / _churn are WIDE
    // (parseActivity).
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
        // Never voted: no realized vp_by_strategy exists, so every VP column is
        // NULL and last_vote_at is NULL too. Present in the fixture on purpose —
        // this is the row that catches a renderer printing a fabricated 0.
        ["0xb64fed2aff534d5320bf401d0d5b93ed7abcf13e", 7, null, null, null, null],
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
    // Treasury history. Wide rows keyed by (chain_id, bucket) — chains are
    // separate rows and are never blended onto one axis. Both chains appear so
    // the dev view exercises the two-panel split and the stale-chain path.
    treasury_chain_history: descriptor(
      "treasury_chain_history",
      ["chain_id", "bucket", "anchor_block", "tokens_held", "tokens_named", "wallets_holding", "positions", "gno_units", "gno_units_ex_ltd"],
      [
        [1, "2026-05-01", 25218797, 236, 236, 23, 2473, 784834.32, 424423.32],
        [1, "2026-06-01", 25433938, 240, 240, 23, 2477, 784931.82, 424520.82],
        [1, "2026-07-01", 25627590, 231, 231, 23, 2485, 784931.82, 424520.82],
        [100, "2022-10-01", 25012345, 14, 14, 24, 96, 62192.91, 62192.91],
        [100, "2022-11-01", 25236302, 14, 14, 24, 98, 62192.91, 62192.91],
      ],
    ),
    treasury_token_history: descriptor(
      "treasury_token_history",
      ["chain_id", "bucket", "token_address", "symbol", "decimals", "metadata_status", "balance_units", "balance_total_raw", "wallets_holding"],
      [
        [1, "2026-06-01", "0x6810e776880c02933d47db1b9fc05908e5386b96", "GNO", 18, "resolved", 784931.82, "784931822290089813540215", 5],
        [1, "2026-06-01", "0xdef1ca1fb7fbcdc777520aa7f396b4e015f497ab", "COW", 18, "resolved", 56680422.1, "56680422101900000000000000", 3],
        [1, "2026-07-01", "0x6810e776880c02933d47db1b9fc05908e5386b96", "GNO", 18, "resolved", 784931.82, "784931822290089813540215", 5],
        [1, "2026-07-01", "0xdef1ca1fb7fbcdc777520aa7f396b4e015f497ab", "COW", 18, "resolved", 56680422.1, "56680422101900000000000000", 3],
        [100, "2022-11-01", "0x9c58bacc331c9aa871afd802db6379a98e80cedb", "GNO", 18, "resolved", 62192.91, "62192908894379965000000", 4],
      ],
    ),
    treasury_wallet_history: descriptor(
      "treasury_wallet_history",
      ["chain_id", "bucket", "wallet_address", "is_ltd", "units", "units_raw"],
      [
        [1, "2026-06-01", "0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f", 0, 414931.58, "414931579828128603923740"],
        [1, "2026-06-01", "0x604e4557e9020841f4e8eb98148de3d3cdea350c", 1, 360411, "360410999999999999999999"],
        [1, "2026-06-01", "other", 0, 5851.05, "5851050000000000000000"],
        [1, "2026-07-01", "0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f", 0, 414931.58, "414931579828128603923740"],
        [1, "2026-07-01", "0x604e4557e9020841f4e8eb98148de3d3cdea350c", 1, 360411, "360410999999999999999999"],
        [1, "2026-07-01", "other", 0, 5851.05, "5851050000000000000000"],
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
      // MIRRORS proposal_vote_trend.sql: one row per (bucket, choice), with the
      // cumulative columns already accumulated PER CHOICE and a constant
      // quorum_vp. A fixture at the old one-row-per-bucket shape would make the
      // per-choice chart look correct in dev while the real query returns
      // something it cannot draw.
      ["bucket", "choice", "votes", "vp", "cumulative_votes", "cumulative_vp", "quorum_vp", "bucket_unit"],
      [
        ["2026-05-02T10:00:00", "For", 30, 20000, 30, 20000, 75000, "hour"],
        ["2026-05-02T10:00:00", "Against", 10, 2000, 10, 2000, 75000, "hour"],
        ["2026-05-02T14:00:00", "For", 45, 28000, 75, 48000, 75000, "hour"],
        ["2026-05-02T14:00:00", "Against", 15, 3000, 25, 5000, 75000, "hour"],
        ["2026-05-03T09:00:00", "For", 40, 16000, 115, 64000, 75000, "hour"],
        ["2026-05-03T09:00:00", "Abstain", 15, 2000, 15, 2000, 75000, "hour"],
        // Crosses the quorum line here, so the dev view exercises the markLine.
        ["2026-05-05T12:00:00", "For", 100, 26000, 215, 90000, 75000, "hour"],
        ["2026-05-05T12:00:00", "Against", 20, 4000, 45, 9000, 75000, "hour"],
        ["2026-05-08T20:00:00", "For", 120, 17000, 335, 107000, 75000, "hour"],
        // The unsupported bucket is in the fixture on purpose: it is the one
        // shape a reader is most likely to break, and it must render as its own
        // series with a disclosure rather than disappear.
        ["2026-05-08T20:00:00", "unsupported choice shape", 17, 2000, 17, 2000, 75000, "hour"],
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
      "overview.core": true, "overview.live": true, "overview.insights": false,
      "proposals.core": false, "proposals.charts": false,
      "voters.core": false, "voters.insights": false,
      "forum.core": false, "forum.insights": false, "forum.engagement": false,
      "delegations.core": false, "delegations.insights": false,
      "treasury.core": false, "treasury.insights": false, "treasury.history": false,
      "treasury.token_history": false,
      "graph.core": false,
    },
    section_fingerprints: { overview: "dev" },
    section_datasets: {
      overview: [
        "space_summary", "source_freshness", "governance_activity",
        "live_votes", "gip_pipeline",
      ],
    },
    section_lru: ["overview"],
    freshness: {
      // Dev-only stale variant: snapshot is flagged stale so the STALE badge
      // is visible while developing; forum shows the fresh path.
      snapshot: { latest_ingested_at: "2026-07-22T05:00:00Z", latest_activity_at: "2026-06-25T12:00:00Z", stale: true },
      forum: { latest_ingested_at: "2026-07-22T05:00:00Z", latest_activity_at: "2026-07-22T03:40:00Z", stale: false },
    },
  },
};

/**
 * `MOCK_PAYLOAD` with `?section=<id>` applied, for `npm run dev`.
 *
 * The fixture carries descriptors for EVERY section, but ships every
 * `loaded_groups` flag except overview's as `false` — so without this, a tab
 * click in dev fires a tool call that has no host to answer it and the section
 * renders as a skeleton or a load error. Only the overview was ever reachable
 * from a browser, which is why layout work on the other tabs had to be verified
 * against a built bundle and a live server.
 *
 * Flips the requested section's groups to loaded, which is enough: `useDataset`
 * falls back to each descriptor's `preview_rows` when nothing is hydrated.
 */
export function devPayload(search: string): MiniAppPayload<GovernanceViewState> {
  const section = new URLSearchParams(search).get("section");
  const state = MOCK_PAYLOAD.view_state;
  if (!state || !section || section === state.section) return MOCK_PAYLOAD;
  const groups = state.loaded_groups ?? {};
  // Unknown section id: leave the fixture alone rather than inventing groups.
  if (!Object.keys(groups).some((key) => key.startsWith(`${section}.`))) {
    return MOCK_PAYLOAD;
  }
  return {
    ...MOCK_PAYLOAD,
    view_state: {
      ...state,
      section: section as GovernanceViewState["section"],
      loaded_groups: Object.fromEntries(
        Object.entries(groups).map(([key, loaded]) => [
          key,
          key.startsWith(`${section}.`) ? true : loaded,
        ]),
      ),
    },
  };
}
