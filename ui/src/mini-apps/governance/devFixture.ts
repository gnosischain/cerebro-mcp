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
